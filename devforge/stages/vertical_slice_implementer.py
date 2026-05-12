"""Vertical slice implementer (DEVF-067).

Run a real implementer → validation → reviewer → judge → revision loop
on top of the generated scaffold (DEVF-065), guided by the deterministic
vertical slice plan (DEVF-066). The end of a ``devforge create-app`` run
should leave a runnable, slice-implemented app under ``<run_root>/scaffold/``.

Design summary (see ``docs/plan/03 §DEVF-067`` and the project plan file):

1. **Scaffold becomes a tiny isolated git repo**: ``init_scaffold_git_repo``
   does ``git init`` with a local user identity and an initial commit on
   ``main``. The ``.git/`` lives inside ``<run_root>/scaffold/.git/`` —
   fully contained in the run directory.
2. **Worktree root is pinned to the run dir**:
   ``WorktreeManager(repo_root=<run_root>/scaffold,
   worktree_root=<run_root>/scaffold_worktrees)``. No git state escapes
   ``<run_root>``.
3. **A scaffold-scoped DevforgeConfig copy** is built on the fly. The
   user's providers / scoring / stop_conditions are preserved; only
   ``project.root / default_branch / worktree_root``, ``file_policy``,
   and ``validation`` are swapped.
4. **The full feature-pipeline evaluation engine is reused** via
   :mod:`devforge.stages.candidate_loop` — no behavior fork.
5. **Accepted candidate is synced back to ``scaffold/`` by file copy**.
   The scaffold's git history intentionally stays at the initial commit;
   it is a run-local artifact, not a source-control surface.

Validation is intentionally lightweight (``python -m compileall``) so the
stage works in CI without installing scaffold dependencies. Users who
want stronger gates can extend ``cfg.validation`` in their devforge.yaml.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.core.config_loader import (
    DevforgeConfig,
    FilePolicy,
    ProjectConfig,
    ValidationCommands,
    ValidationConfig,
)
from devforge.core.role_router import RoleRouter
from devforge.core.run_context import RunContext
from devforge.git.worktree_manager import WorktreeError, WorktreeManager
from devforge.providers.registry import ProviderRegistry
from devforge.stages import candidate_loop
from devforge.stages.architecture_generator import Architecture
from devforge.stages.scaffold_generator import ScaffoldManifest
from devforge.stages.vertical_slice_planner import VerticalSlicePlan

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

_SCAFFOLD_DIR_NAME = "scaffold"
_SCAFFOLD_WORKTREES_DIR_NAME = "scaffold_worktrees"
_DEFAULT_BRANCH = "main"


@dataclass
class VerticalSliceImplementerResult:
    """Compact result artifact written as ``vertical_slice_result.json``."""

    decision: str = "skipped"          # accept | revise | discard |
                                       # human_review | skipped | failed
    reason: str = ""
    candidate_id: str | None = None
    provider_id: str | None = None
    reviewer_provider_id: str | None = None
    score: float | None = None
    iterations: int = 0
    reviewer_verdict: str | None = None
    validation: dict[str, bool] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    synced_to_scaffold: bool = False
    candidate_artifacts: str | None = None
    scaffold_root: str = _SCAFFOLD_DIR_NAME
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class VerticalSliceImplementerError(Exception):
    """Raised when the slice implementer cannot proceed safely."""


# ---------------------------------------------------------------------------
# Skip-gate decision
# ---------------------------------------------------------------------------

def skip_reason(
    manifest: ScaffoldManifest, slice_plan: VerticalSlicePlan
) -> str | None:
    """Return a human-readable reason to skip the stage, or ``None`` to proceed."""
    if not manifest.supported:
        return f"scaffold stack '{manifest.stack}' is not supported"
    if not manifest.import_smoke_passed:
        return (
            "scaffold py_compile smoke failed; refusing to implement on a "
            "broken base"
        )
    if not slice_plan.acceptance_criteria:
        return "vertical slice has no acceptance criteria"
    return None


# ---------------------------------------------------------------------------
# Scaffold git bootstrap
# ---------------------------------------------------------------------------

def init_scaffold_git_repo(scaffold_root: Path) -> str:
    """Initialise ``scaffold_root`` as a git repo on branch ``main``.

    Idempotent: if the repo already has at least one commit on
    ``_DEFAULT_BRANCH`` the function is a no-op. Local user identity is
    configured so commits succeed without relying on global git config.

    Returns the default branch name.
    """
    scaffold_root = scaffold_root.resolve()
    if not scaffold_root.exists():
        raise VerticalSliceImplementerError(
            f"scaffold root does not exist: {scaffold_root}"
        )
    if not scaffold_root.is_dir():
        raise VerticalSliceImplementerError(
            f"scaffold root is not a directory: {scaffold_root}"
        )

    git_dir = scaffold_root / ".git"
    if git_dir.exists():
        # Verify the existing repo has the expected branch + at least one
        # commit. If not, do not try to "fix" it — let the caller see the
        # error.
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{_DEFAULT_BRANCH}"],
            cwd=str(scaffold_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return _DEFAULT_BRANCH
        # .git exists but no main branch — unusual; raise rather than silently
        # repair.
        raise VerticalSliceImplementerError(
            f"scaffold already has .git/ but no '{_DEFAULT_BRANCH}' branch "
            f"({proc.stderr.strip()})"
        )

    _git(scaffold_root, ["init", "-b", _DEFAULT_BRANCH], "git init")
    _git(
        scaffold_root,
        ["config", "user.email", "devforge@local.invalid"],
        "git config user.email",
    )
    _git(scaffold_root, ["config", "user.name", "devforge"], "git config user.name")
    _git(
        scaffold_root,
        ["config", "commit.gpgsign", "false"],
        "git config commit.gpgsign",
    )
    _git(scaffold_root, ["add", "-A"], "git add")
    _git(
        scaffold_root,
        ["commit", "-m", "scaffold: initial commit (devforge DEVF-065)"],
        "git commit",
    )
    return _DEFAULT_BRANCH


# ---------------------------------------------------------------------------
# Config + policy + validation overrides
# ---------------------------------------------------------------------------

def scaffold_file_policy() -> FilePolicy:
    """File policy for slice implementation: only ``app/`` and ``tests/`` editable."""
    return FilePolicy(
        allowed_paths=["app/**", "tests/**"],
        blocked_paths=[
            "pyproject.toml",
            "README.md",
            ".git/**",
            "app/__pycache__/**",
        ],
        require_human_review_if_modified=[],
    )


def scaffold_validation_config() -> ValidationConfig:
    """Lightweight, dependency-free validation for the scaffold.

    ``python -m compileall`` works without installing fastapi / uvicorn /
    pytest. Stronger gates (real ``pytest -q`` inside the scaffold) require
    user-configured commands and are intentionally out of scope here.
    """
    return ValidationConfig(
        commands=ValidationCommands(
            import_smoke="python -m compileall -q app tests"
        ),
        default_timeout_sec=30,
    )


def build_scaffold_cfg(cfg: DevforgeConfig, scaffold_root: Path, run_root: Path) -> DevforgeConfig:
    """Return a copy of ``cfg`` re-targeted at the scaffold directory.

    Preserves the user's providers / roles / scoring / stop_conditions /
    command_policy. Overrides only project.root / default_branch /
    worktree_root, file_policy, and validation.
    """
    new_cfg = cfg.model_copy(deep=True)
    new_cfg.project = ProjectConfig(
        name=cfg.project.name or "scaffold",
        root=str(scaffold_root.resolve()),
        default_branch=_DEFAULT_BRANCH,
        worktree_root=str((run_root / _SCAFFOLD_WORKTREES_DIR_NAME).resolve()),
        profile=cfg.project.profile,
    )
    new_cfg.file_policy = scaffold_file_policy()
    new_cfg.validation = scaffold_validation_config()
    return new_cfg


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def build_slice_task_text(
    plan: VerticalSlicePlan, arch: Architecture, manifest: ScaffoldManifest
) -> str:
    """Render the implementer ``task`` variable for the slice."""
    lines: list[str] = []
    lines.append(f"# Implement vertical slice: {plan.vertical_slice_name}")
    lines.append("")
    lines.append(
        "Implement exactly **one** end-to-end user journey on the scaffold "
        "below. Do not implement the entire app — only the slice described "
        "here. The scaffold already contains a runnable FastAPI skeleton; "
        "fill in the slice's behavior under ``app/`` and add focused tests "
        "under ``tests/``."
    )
    lines.append("")

    lines.append("## User journey")
    if plan.user_journey:
        for i, step in enumerate(plan.user_journey, 1):
            lines.append(f"{i}. {step}")
    else:
        lines.append("_(no journey supplied)_")
    lines.append("")

    lines.append("## Acceptance criteria")
    for ac in plan.acceptance_criteria:
        lines.append(f"- {ac}")
    lines.append("")

    if plan.api_endpoints:
        lines.append("## API endpoints in this slice")
        for ep in plan.api_endpoints:
            lines.append(f"- {ep}")
        lines.append("")

    if plan.data_entities:
        lines.append("## Data entities in this slice")
        for ent in plan.data_entities:
            lines.append(f"- {ent}")
        lines.append("")

    if plan.screens:
        lines.append("## Screens / surfaces")
        for sid in plan.screens:
            lines.append(f"- {sid}")
        lines.append("")

    lines.append("## Scaffold layout")
    lines.append(
        f"- stack: `{manifest.stack}` "
        f"(supported={'yes' if manifest.supported else 'no'})"
    )
    lines.append(
        f"- generated files ({len(manifest.files)}): see scaffold_manifest.json"
    )
    head = [f.path for f in manifest.files[:12]]
    for path in head:
        lines.append(f"  - `{path}`")
    if len(manifest.files) > 12:
        lines.append(f"  - ... and {len(manifest.files) - 12} more")
    lines.append("")

    lines.append("## Constraints")
    lines.append("- Modify only files inside `app/` and `tests/`.")
    lines.append(
        "- Do not edit `pyproject.toml`, `README.md`, or anything under "
        "`.git/`."
    )
    lines.append(
        "- Keep imports working — the scaffold must still pass "
        "`python -m compileall -q app tests` when you are done."
    )
    lines.append(
        "- Add at least one test under `tests/` that exercises the slice's "
        "acceptance criteria."
    )
    return "\n".join(lines) + "\n"


def build_slice_repo_context(
    arch: Architecture, manifest: ScaffoldManifest
) -> str:
    """Render the implementer ``repo_context`` variable from in-memory artifacts."""
    lines: list[str] = ["# Scaffold repo context", ""]
    lines.append(f"- Project: `{manifest.project_name}`")
    lines.append(f"- Stack: `{manifest.stack}`")
    lines.append(f"- Runtime: {arch.runtime}")
    lines.append(f"- Framework: {arch.framework}")
    lines.append(f"- Persistence: {arch.persistence}")
    lines.append(f"- Test command: `{arch.test_command}`")
    lines.append("")

    if arch.entities:
        lines.append("## Entities")
        for entity in arch.entities:
            fields_str = ", ".join(
                f"{name}: {ftype}" for name, ftype in entity.fields.items()
            ) or "(no declared fields)"
            lines.append(f"- **{entity.name}** — {fields_str}")
        lines.append("")

    if arch.operations:
        lines.append("## API operations")
        for op in arch.operations:
            lines.append(f"- `{op.method.upper()} {op.path}` — {op.summary}")
        lines.append("")

    lines.append("## Generated files")
    for f in manifest.files[:40]:
        lines.append(f"- `{f.path}` ({f.bytes} bytes)")
    if len(manifest.files) > 40:
        lines.append(f"- ... and {len(manifest.files) - 40} more")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sync accepted candidate back to scaffold/
# ---------------------------------------------------------------------------

def sync_worktree_to_scaffold(
    worktree_path: Path, scaffold_root: Path, changed_files: list[str]
) -> list[str]:
    """Copy each ``changed_file`` from ``worktree_path`` into ``scaffold_root``.

    Files that exist in the worktree are copied (created or overwritten);
    files that no longer exist are removed from the scaffold (honouring
    deletions). All paths are validated to remain inside the scaffold root.
    Returns the list of paths actually synced.
    """
    scaffold_root = scaffold_root.resolve()
    worktree_path = worktree_path.resolve()
    synced: list[str] = []
    for rel in changed_files:
        rel_clean = rel.strip()
        if not rel_clean:
            continue
        src = (worktree_path / rel_clean).resolve()
        dst = (scaffold_root / rel_clean).resolve()
        if not _is_inside(dst, scaffold_root):
            raise VerticalSliceImplementerError(
                f"refused to sync outside scaffold root: {rel_clean}"
            )
        if src.exists() and src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            synced.append(rel_clean)
        elif dst.exists() and dst.is_file():
            # File was deleted in the worktree — mirror the deletion.
            dst.unlink()
            synced.append(rel_clean)
        # else: nothing to do (e.g. transient file)
    return synced


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def run_vertical_slice_implementer(
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    *,
    slice_plan: VerticalSlicePlan,
    arch: Architecture,
    scaffold_manifest: ScaffoldManifest,
    implementer_override: str | None = None,
    reviewer_override: str | None = None,
) -> VerticalSliceImplementerResult:
    """Run one slice end-to-end on top of the scaffold.

    Returns a :class:`VerticalSliceImplementerResult` describing the outcome.
    On ``accept``/``revise``/``discard``/``human_review`` the candidate's
    artifacts live under ``<run_root>/candidates/<provider_id>/``. On
    ``accept`` only, the changed files are also copied back into
    ``<run_root>/scaffold/`` so the run directory holds a runnable
    slice-implemented app.
    """
    scaffold_root = (run_ctx.root / _SCAFFOLD_DIR_NAME).resolve()
    if not scaffold_root.exists():
        return VerticalSliceImplementerResult(
            decision="skipped",
            reason=f"scaffold directory not found: {scaffold_root}",
        )

    # Defence-in-depth: keep all git state inside the run dir.
    worktree_root = (run_ctx.root / _SCAFFOLD_WORKTREES_DIR_NAME).resolve()
    if not _is_inside(worktree_root, run_ctx.root.resolve()):
        raise VerticalSliceImplementerError(
            "worktree_root must live inside run_root"
        )

    scaffold_cfg = build_scaffold_cfg(cfg, scaffold_root, run_ctx.root)

    try:
        init_scaffold_git_repo(scaffold_root)
    except (VerticalSliceImplementerError, WorktreeError) as exc:
        return VerticalSliceImplementerResult(
            decision="failed",
            reason=f"scaffold git init failed: {exc}",
        )

    registry = ProviderRegistry.from_config(scaffold_cfg)
    # Best-effort: mirror the scaffold-scoped provider health into the
    # project-level SQLite index. The state store guards against errors.
    from devforge.core.state_store import StateStore as _StateStore  # noqa: PLC0415
    _StateStore(run_ctx.root).snapshot_provider_registry(registry)
    router = RoleRouter(scaffold_cfg, registry)

    impl_decision = router.select("implementer", override=implementer_override)
    if not impl_decision.selected:
        return VerticalSliceImplementerResult(
            decision="skipped",
            reason="no implementer provider available for the slice",
            notes=[
                "Configure a provider for the `implementer` role in "
                "devforge.yaml or pass --implementer to `devforge create-app`.",
            ],
        )

    worktree_mgr = WorktreeManager(
        repo_root=scaffold_root,
        worktree_root=worktree_root,
    )

    task_text = build_slice_task_text(slice_plan, arch, scaffold_manifest)
    repo_context_md = build_slice_repo_context(arch, scaffold_manifest)
    acceptance = "\n".join(f"- {ac}" for ac in slice_plan.acceptance_criteria)

    if impl_decision.mode == "tournament":
        # Tournament mode: pick the best summary across providers. We mirror
        # the feature driver's tournament behaviour: each provider runs
        # independently through the candidate loop, no inter-provider fallback.
        summaries: list = []
        for pid in impl_decision.selected:
            summary = candidate_loop.execute_candidate(
                pid,
                cfg=scaffold_cfg,
                run_ctx=run_ctx,
                registry=registry,
                router=router,
                worktree_mgr=worktree_mgr,
                task_text=task_text,
                repo_context_md=repo_context_md,
                acceptance=acceptance,
                reviewer_override=reviewer_override,
            )
            if summary is not None:
                summaries.append(summary)
        chosen = max(summaries, key=lambda s: s.score) if summaries else None
    else:
        summary, _attempts = candidate_loop.execute_with_fallback(
            impl_decision.selected,
            cfg=scaffold_cfg,
            run_ctx=run_ctx,
            registry=registry,
            router=router,
            worktree_mgr=worktree_mgr,
            task_text=task_text,
            repo_context_md=repo_context_md,
            acceptance=acceptance,
            reviewer_override=reviewer_override,
        )
        chosen = summary

    if chosen is None:
        return VerticalSliceImplementerResult(
            decision="failed",
            reason="no candidate produced",
        )

    # Read the reviewer provider id from the candidate's review.json if it exists.
    reviewer_provider_id = _read_reviewer_id(run_ctx, chosen.candidate_id)

    result = VerticalSliceImplementerResult(
        decision=chosen.decision,
        reason=chosen.reason or "",
        candidate_id=chosen.candidate_id,
        provider_id=chosen.provider_id,
        reviewer_provider_id=reviewer_provider_id,
        score=float(chosen.score),
        reviewer_verdict=chosen.review_verdict,
        validation=dict(chosen.validation_pass),
        changed_files=list(chosen.changed_files),
        candidate_artifacts=f"candidates/{chosen.candidate_id}/",
    )

    # Sync only on accept. Other verdicts leave the scaffold untouched so the
    # user can inspect what the candidate produced under candidates/.
    if chosen.decision == "accept":
        worktree_dir = _worktree_path_for(run_ctx, chosen.candidate_id)
        if worktree_dir is not None and worktree_dir.exists():
            try:
                synced = sync_worktree_to_scaffold(
                    worktree_dir, scaffold_root, chosen.changed_files
                )
                result.synced_to_scaffold = bool(synced)
                if synced != list(chosen.changed_files):
                    result.notes.append(
                        f"synced {len(synced)} of {len(chosen.changed_files)} "
                        f"changed file(s) — some entries may have been transient"
                    )
                # Commit so DEVF-069 worktrees branch off the slice's tree.
                if result.synced_to_scaffold:
                    commit_scaffold_progress(
                        scaffold_root,
                        f"slice: accept {chosen.candidate_id} "
                        f"({slice_plan.vertical_slice_name})",
                    )
            except VerticalSliceImplementerError as exc:
                result.notes.append(f"sync_worktree_to_scaffold failed: {exc}")
                result.synced_to_scaffold = False

    result.notes.append(
        "Validation is limited to `python -m compileall -q app tests` so "
        "the stage runs without installing scaffold dependencies. Extend "
        "`cfg.validation` in devforge.yaml for stronger gates."
    )
    return result


def save_vertical_slice_result(
    result: VerticalSliceImplementerResult, path: Path
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _git(cwd: Path, args: list[str], error_prefix: str) -> None:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise VerticalSliceImplementerError(
            f"{error_prefix} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )


def _read_reviewer_id(run_ctx: RunContext, candidate_id: str) -> str | None:
    review_path = run_ctx.candidates_dir / candidate_id / "review.json"
    if not review_path.exists():
        return None
    try:
        payload = json.loads(review_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    pid = payload.get("provider_id")
    return pid if isinstance(pid, str) else None


def commit_scaffold_progress(scaffold_root: Path, message: str) -> bool:
    """Stage + commit any uncommitted scaffold changes on the ``main`` branch.

    Returns ``True`` when a new commit was created, ``False`` when there was
    nothing to commit. Idempotent — safe to call after every accepted
    candidate or backlog item.
    """
    scaffold_root = scaffold_root.resolve()
    if not (scaffold_root / ".git").exists():
        raise VerticalSliceImplementerError(
            f"scaffold has no git repo: {scaffold_root}"
        )
    proc = subprocess.run(
        ["git", "add", "-A"],
        cwd=str(scaffold_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise VerticalSliceImplementerError(
            f"git add failed: {proc.stderr.strip()}"
        )
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(scaffold_root),
        check=False,
    )
    if staged.returncode == 0:
        return False  # nothing to commit
    proc = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(scaffold_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise VerticalSliceImplementerError(
            f"git commit failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return True


def _worktree_path_for(run_ctx: RunContext, candidate_id: str) -> Path | None:
    """Resolve the worktree directory for a candidate from the naming convention.

    :class:`WorktreeManager` creates worktrees at
    ``<worktree_root>/<run_id>-<candidate_id>`` (see ``worktree_manager.py:68``).
    The scaffold worktree_root is ``<run_root>/scaffold_worktrees/``.
    """
    candidate = (
        run_ctx.root
        / _SCAFFOLD_WORKTREES_DIR_NAME
        / f"{run_ctx.run_id}-{candidate_id}"
    )
    return candidate if candidate.exists() else None
