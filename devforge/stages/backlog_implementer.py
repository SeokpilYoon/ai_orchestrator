"""Backlog implementation loop (DEVF-069).

Iterates the backlog (DEVF-068) on top of the scaffold (DEVF-065) and the
vertical slice (DEVF-067). Each backlog item gets its own implementer
worktree, runs through the candidate loop
(:mod:`devforge.stages.candidate_loop`), and — on ``accept`` — has its
diff synced + committed to ``<run_root>/scaffold/``. The scaffold's git
history accumulates one commit per accepted task, giving users an audit
trail and letting later items branch off earlier accepted work.

Output: ``backlog_progress.json`` with per-task status, the run-level
``acceptance_coverage`` (fraction of acceptance criteria covered by
accepted items), and human-readable notes.

Skip rules:

- Whole stage skips cleanly (no candidates created) when the scaffold is
  unsupported, the scaffold's compileall smoke failed earlier, no
  implementer provider is healthy, or the backlog has no items.
- A single item skips with ``already_in_slice`` when every one of its
  ``requirement_ids`` is also in the accepted vertical slice plan.
- A single item skips with ``dependency_failed`` when one of its
  ``dependencies`` did not produce an ``accept`` verdict.

Validation is the same scaffold-friendly compileall used by DEVF-067 —
no network installs, deterministic in CI.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.core.config_loader import DevforgeConfig
from devforge.core.role_router import RoleRouter
from devforge.core.run_context import RunContext
from devforge.git.worktree_manager import WorktreeError, WorktreeManager
from devforge.providers.registry import ProviderRegistry
from devforge.stages import candidate_loop
from devforge.stages.architecture_generator import Architecture
from devforge.stages.backlog_generator import Backlog, BacklogItem
from devforge.stages.implementer_stage import run_implementer_stage
from devforge.stages.scaffold_generator import ScaffoldManifest
from devforge.stages.vertical_slice_implementer import (
    VerticalSliceImplementerError,
    VerticalSliceImplementerResult,
    build_scaffold_cfg,
    build_slice_repo_context,
    commit_scaffold_progress,
    init_scaffold_git_repo,
    sync_worktree_to_scaffold,
)
from devforge.stages.vertical_slice_implementer import (
    skip_reason as _scaffold_skip_reason,
)
from devforge.stages.vertical_slice_planner import VerticalSlicePlan

_SCAFFOLD_DIR_NAME = "scaffold"
_SCAFFOLD_WORKTREES_DIR_NAME = "scaffold_worktrees"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class BacklogProgressItem:
    task_id: str
    requirement_ids: list[str] = field(default_factory=list)
    status: str = "pending"  # accept | revise | discard | human_review |
                             # skipped | failed | already_in_slice |
                             # dependency_failed | pending
    reason: str = ""
    candidate_id: str | None = None
    provider_id: str | None = None
    reviewer_verdict: str | None = None
    score: float | None = None
    iterations: int = 0
    changed_files: list[str] = field(default_factory=list)
    synced_to_scaffold: bool = False
    candidate_artifacts: str | None = None
    acceptance_criteria_count: int = 0
    accepted_acceptance_criteria_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class BacklogProgress:
    items: list[BacklogProgressItem] = field(default_factory=list)
    decision: str = "completed"            # completed | skipped | failed
    reason: str = ""
    accepted_count: int = 0
    total_count: int = 0
    acceptance_coverage: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "accepted_count": self.accepted_count,
            "total_count": self.total_count,
            "acceptance_coverage": round(self.acceptance_coverage, 4),
            "items": [it.to_dict() for it in self.items],
            "notes": list(self.notes),
        }


class BacklogImplementerError(Exception):
    """Raised when the backlog loop cannot proceed safely."""


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def run_backlog_implementer(
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    *,
    backlog: Backlog,
    slice_plan: VerticalSlicePlan | None,
    slice_result: VerticalSliceImplementerResult | None,
    arch: Architecture,
    scaffold_manifest: ScaffoldManifest,
    implementer_override: str | None = None,
    reviewer_override: str | None = None,
) -> BacklogProgress:
    """Loop each backlog item through the candidate pipeline on the scaffold."""
    progress = BacklogProgress(
        items=[],
        total_count=len(backlog.items),
    )

    # Whole-stage skip gates ------------------------------------------------
    scaffold_root = (run_ctx.root / _SCAFFOLD_DIR_NAME).resolve()
    if not scaffold_root.exists():
        progress.decision = "skipped"
        progress.reason = f"scaffold directory not found: {scaffold_root}"
        progress.items = [
            _skipped_item(item, status="skipped", reason=progress.reason)
            for item in backlog.items
        ]
        return progress

    # Reuse the slice implementer's manifest gates so behavior matches.
    scaffold_skip = _scaffold_skip_reason(
        scaffold_manifest,
        # Synthesise a non-empty plan so skip_reason doesn't trip on
        # acceptance_criteria — backlog items carry their own AC.
        _BACKLOG_GATE_STUB,
    )
    if scaffold_skip is not None:
        progress.decision = "skipped"
        progress.reason = scaffold_skip
        progress.items = [
            _skipped_item(item, status="skipped", reason=scaffold_skip)
            for item in backlog.items
        ]
        return progress

    if not backlog.items:
        progress.decision = "skipped"
        progress.reason = "backlog is empty"
        return progress

    scaffold_cfg = build_scaffold_cfg(cfg, scaffold_root, run_ctx.root)

    try:
        init_scaffold_git_repo(scaffold_root)
    except (VerticalSliceImplementerError, WorktreeError) as exc:
        progress.decision = "failed"
        progress.reason = f"scaffold git init failed: {exc}"
        progress.items = [
            _skipped_item(item, status="failed", reason=progress.reason)
            for item in backlog.items
        ]
        return progress

    registry = ProviderRegistry.from_config(scaffold_cfg)
    router = RoleRouter(scaffold_cfg, registry)

    impl_decision = router.select("implementer", override=implementer_override)
    if not impl_decision.selected:
        progress.decision = "skipped"
        progress.reason = "no implementer provider available for the backlog"
        progress.items = [
            _skipped_item(item, status="skipped", reason=progress.reason)
            for item in backlog.items
        ]
        progress.notes.append(
            "Configure a provider for the `implementer` role in devforge.yaml "
            "or pass --implementer to `devforge create-app`."
        )
        return progress

    impl_provider_id = impl_decision.selected[0]

    worktree_root = (run_ctx.root / _SCAFFOLD_WORKTREES_DIR_NAME).resolve()
    worktree_mgr = WorktreeManager(
        repo_root=scaffold_root, worktree_root=worktree_root
    )

    # Per-item loop ---------------------------------------------------------
    accepted_slice_frs = _accepted_slice_requirement_ids(slice_plan, slice_result)
    repo_context_md = build_slice_repo_context(arch, scaffold_manifest)
    status_by_task: dict[str, str] = {}

    ordered = _items_in_dependency_order(backlog.items)
    for item in ordered:
        progress_item = _new_progress_item(item)

        # 1) Already covered by the accepted slice → skip.
        if accepted_slice_frs and set(item.requirement_ids).issubset(
            accepted_slice_frs
        ):
            progress_item.status = "already_in_slice"
            progress_item.reason = (
                "all requirement_ids are covered by the accepted vertical slice"
            )
            status_by_task[item.id] = progress_item.status
            progress.items.append(progress_item)
            continue

        # 2) Any dependency that did not accept → cascade skip.
        blocked_by = [
            dep
            for dep in item.dependencies
            if status_by_task.get(dep, "pending") not in {"accept", "already_in_slice"}
        ]
        if blocked_by:
            progress_item.status = "dependency_failed"
            progress_item.reason = (
                f"blocked by upstream task(s): {', '.join(blocked_by)}"
            )
            status_by_task[item.id] = progress_item.status
            progress.items.append(progress_item)
            continue

        # 3) Run the candidate loop for this task.
        summary = _run_one_backlog_task(
            cfg=scaffold_cfg,
            run_ctx=run_ctx,
            registry=registry,
            router=router,
            worktree_mgr=worktree_mgr,
            provider_id=impl_provider_id,
            item=item,
            repo_context_md=repo_context_md,
            reviewer_override=reviewer_override,
        )
        if summary is None:
            progress_item.status = "failed"
            progress_item.reason = "implementer did not produce a candidate"
            status_by_task[item.id] = progress_item.status
            progress.items.append(progress_item)
            continue

        progress_item.candidate_id = summary.candidate_id
        progress_item.provider_id = summary.provider_id
        progress_item.reviewer_verdict = summary.review_verdict
        progress_item.score = float(summary.score)
        progress_item.changed_files = list(summary.changed_files)
        progress_item.candidate_artifacts = f"candidates/{summary.candidate_id}/"
        progress_item.status = summary.decision
        progress_item.reason = summary.reason or ""

        if summary.decision == "accept":
            worktree_dir = _worktree_path_for(run_ctx, summary.candidate_id)
            if worktree_dir is not None and worktree_dir.exists():
                try:
                    synced = sync_worktree_to_scaffold(
                        worktree_dir, scaffold_root, summary.changed_files
                    )
                    progress_item.synced_to_scaffold = bool(synced)
                    if progress_item.synced_to_scaffold:
                        commit_scaffold_progress(
                            scaffold_root,
                            f"backlog: accept {item.id} ({item.title})",
                        )
                        progress_item.accepted_acceptance_criteria_count = (
                            progress_item.acceptance_criteria_count
                        )
                except VerticalSliceImplementerError as exc:
                    progress_item.synced_to_scaffold = False
                    progress_item.reason = (
                        f"sync failed: {exc}"
                        if not progress_item.reason
                        else f"{progress_item.reason}; sync failed: {exc}"
                    )

        status_by_task[item.id] = progress_item.status
        progress.items.append(progress_item)

    # Coverage summary ------------------------------------------------------
    total_ac = sum(it.acceptance_criteria_count for it in progress.items)
    accepted_ac = sum(
        it.accepted_acceptance_criteria_count for it in progress.items
    )
    progress.accepted_count = sum(
        1 for it in progress.items if it.status == "accept"
    )
    progress.acceptance_coverage = (
        (accepted_ac / total_ac) if total_ac else 0.0
    )
    progress.notes.append(
        "Validation is limited to `python -m compileall -q app tests`. "
        "Extend `cfg.validation` in devforge.yaml for stronger gates."
    )
    return progress


def save_backlog_progress(progress: BacklogProgress, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(progress.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Stub plan used solely to exercise the scaffold-level skip gate from the
# slice implementer (which insists on at least one acceptance criterion).
class _BacklogGateStub:
    acceptance_criteria = ["__backlog_gate__"]


_BACKLOG_GATE_STUB = _BacklogGateStub()


def _new_progress_item(item: BacklogItem) -> BacklogProgressItem:
    return BacklogProgressItem(
        task_id=item.id,
        requirement_ids=list(item.requirement_ids),
        acceptance_criteria_count=len(item.acceptance_criteria),
    )


def _skipped_item(item: BacklogItem, *, status: str, reason: str) -> BacklogProgressItem:
    out = _new_progress_item(item)
    out.status = status
    out.reason = reason
    return out


def _accepted_slice_requirement_ids(
    slice_plan: VerticalSlicePlan | None,
    slice_result: VerticalSliceImplementerResult | None,
) -> set[str]:
    if slice_plan is None or slice_result is None:
        return set()
    if slice_result.decision != "accept":
        return set()
    return set(slice_plan.requirement_ids)


def _items_in_dependency_order(items: list[BacklogItem]) -> list[BacklogItem]:
    """Stable topological sort: items appear after their dependencies.

    Backlog dependencies form a DAG by construction (DEVF-068 only points
    later items at earlier producers). We still defensively fall back to
    input order on any cycle to avoid raising mid-loop.
    """
    by_id = {item.id: item for item in items}
    visited: set[str] = set()
    visiting: set[str] = set()
    ordered: list[BacklogItem] = []

    def _visit(item: BacklogItem) -> None:
        if item.id in visited:
            return
        if item.id in visiting:
            # Cycle — give up gracefully; the caller still processes the item.
            return
        visiting.add(item.id)
        for dep_id in item.dependencies:
            dep = by_id.get(dep_id)
            if dep is not None:
                _visit(dep)
        visiting.remove(item.id)
        visited.add(item.id)
        ordered.append(item)

    for item in items:
        _visit(item)
    return ordered


def _worktree_path_for(run_ctx: RunContext, candidate_id: str) -> Path | None:
    candidate = (
        run_ctx.root
        / _SCAFFOLD_WORKTREES_DIR_NAME
        / f"{run_ctx.run_id}-{candidate_id}"
    )
    return candidate if candidate.exists() else None


def _build_task_text(item: BacklogItem) -> str:
    lines: list[str] = [
        f"# Backlog task: {item.id} — {item.title}",
        "",
        "Implement exactly this backlog item on top of the current scaffold "
        "state. The scaffold may already contain code from a previously "
        "accepted vertical slice or earlier backlog items — build on it, "
        "do not overwrite unrelated files.",
        "",
        "## Requirement trace",
    ]
    for rid in item.requirement_ids:
        lines.append(f"- {rid}")
    lines.append("")
    lines.append("## Acceptance criteria")
    for ac in item.acceptance_criteria:
        lines.append(f"- {ac}")
    lines.append("")
    lines.append("## Constraints")
    lines.append("- Modify only files inside `app/` and `tests/`.")
    lines.append(
        "- Do not edit `pyproject.toml`, `README.md`, or anything under "
        "`.git/`."
    )
    lines.append(
        "- Add or extend at least one test under `tests/` covering this "
        "task's acceptance criteria."
    )
    lines.append(
        "- Keep imports working — the scaffold must still pass "
        "`python -m compileall -q app tests` when you are done."
    )
    lines.append(f"- Estimated complexity: **{item.estimated_complexity}**")
    lines.append(f"- Priority: **{item.priority}**")
    return "\n".join(lines) + "\n"


def _run_one_backlog_task(
    *,
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    registry: ProviderRegistry,
    router: RoleRouter,
    worktree_mgr: WorktreeManager,
    provider_id: str,
    item: BacklogItem,
    repo_context_md: str,
    reviewer_override: str | None,
):
    """Run implementer → revision loop for one backlog item.

    Returns the :class:`CandidateSummary` (from candidate_loop) or ``None``
    when the implementer itself failed before producing any candidate.
    """
    task_text = _build_task_text(item)
    acceptance = "\n".join(f"- {ac}" for ac in item.acceptance_criteria)

    initial = run_implementer_stage(
        cfg=cfg,
        run_ctx=run_ctx,
        registry=registry,
        worktree_mgr=worktree_mgr,
        provider_ids=[provider_id],
        candidate_ids=[item.id],          # use TASK-NNN as candidate_id
        task_text=task_text,
        repo_context=repo_context_md,
        acceptance_criteria=acceptance,
    )
    if not initial:
        return None
    cand = initial[0]
    if not cand.agent_result.success:
        # Surface an explicit failure summary so the caller can record it.
        return candidate_loop.failure_summary(cand)

    return candidate_loop.run_revision_loop(
        cand=cand,
        cfg=cfg,
        run_ctx=run_ctx,
        registry=registry,
        router=router,
        task_text=task_text,
        acceptance=acceptance,
        reviewer_override=reviewer_override,
    )
