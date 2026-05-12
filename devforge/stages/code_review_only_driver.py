"""Driver for the ``code_review_only`` workflow.

Runs reviewer + judge over a pre-existing diff without invoking an
implementer or creating a candidate worktree. Useful for reviewing
uncommitted work, staged changes, a commit range, or a patch file
without spending tokens/CLI calls on regenerating code that already
exists.

Diff sources (selected via ``run_ctx.metadata["diff_spec"]``):

- ``"working"`` (default)                  → ``git diff``
- ``"staged"``                             → ``git diff --cached``
- ``"ref:<base>..<head>"`` (incl. ``...``) → ``git diff <base>..<head>``
- ``"file:<path>"``                         → read the patch verbatim

The driver records the run in :class:`StateStore` (and therefore the
SQLite index from DEVF-080), so the same dashboard / report surfaces
work for review-only runs.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devforge.core.config_loader import DevforgeConfig
from devforge.core.role_router import RoleRouter
from devforge.core.run_context import RunContext
from devforge.core.state_store import StateStore
from devforge.evaluators.command_policy_checker import check_command_policy
from devforge.evaluators.file_policy_checker import check_file_policy
from devforge.evaluators.judge import decide
from devforge.evaluators.score_calculator import EvaluationBundle, calculate_score
from devforge.evaluators.secret_scanner import scan_diff_and_logs
from devforge.evaluators.test_mutation_checker import check_test_mutation
from devforge.evaluators.validation_runner import ValidationReport
from devforge.providers.registry import ProviderRegistry
from devforge.stages.reviewer_stage import run_reviewer_stage

_STAGE_IDS = ["collect_diff", "review", "judge", "final_report"]
_CANDIDATE_ID = "review_target"


class CodeReviewOnlyError(Exception):
    """Raised when no diff can be assembled from the configured source."""


@dataclass
class DiffSource:
    """Resolved diff source — what the driver actually reads from."""

    kind: str             # "working" | "staged" | "ref" | "file"
    spec: str             # the original spec string
    detail: str = ""      # human-readable description for the report

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "spec": self.spec, "detail": self.detail}


def parse_diff_spec(spec: str | None) -> DiffSource:
    """Map a ``--diff-from`` argument onto a :class:`DiffSource`.

    Accepts:

    - ``None`` / ``""`` / ``"working"`` → ``git diff``
    - ``"staged"``                       → ``git diff --cached``
    - ``"ref:<rev_range>"``              → ``git diff <rev_range>``
    - ``"file:<path>"``                  → read patch verbatim
    """
    if not spec or spec == "working":
        return DiffSource(kind="working", spec="working", detail="git diff (unstaged)")
    if spec == "staged":
        return DiffSource(kind="staged", spec="staged", detail="git diff --cached")
    if spec.startswith("ref:"):
        rev_range = spec[len("ref:"):]
        return DiffSource(kind="ref", spec=spec, detail=f"git diff {rev_range}")
    if spec.startswith("file:"):
        return DiffSource(
            kind="file", spec=spec, detail=f"patch file: {spec[len('file:'):]}"
        )
    # Fall back to "ref" when the spec looks like a revision range
    # (``main..HEAD``, ``abc..def``).
    if ".." in spec:
        return DiffSource(kind="ref", spec=f"ref:{spec}", detail=f"git diff {spec}")
    raise CodeReviewOnlyError(
        f"unrecognised diff spec '{spec}'. "
        f"Expected working|staged|ref:<a..b>|file:<path>"
    )


def collect_diff_text(
    project_root: Path, source: DiffSource
) -> str:
    """Materialise the diff into a string the reviewer prompt can carry."""
    if source.kind == "file":
        path = Path(source.spec[len("file:"):])
        if not path.is_absolute():
            path = project_root / path
        if not path.exists():
            raise CodeReviewOnlyError(f"diff file not found: {path}")
        return path.read_text(encoding="utf-8")

    if source.kind == "working":
        # Tracked changes (modified files) + a synthetic diff per untracked
        # file so brand-new files still show up in the review.
        tracked = _git(project_root, ["git", "diff"])
        untracked_listing = _git(
            project_root, ["git", "ls-files", "--others", "--exclude-standard"]
        )
        parts = [tracked]
        for path in untracked_listing.splitlines():
            path = path.strip()
            if not path:
                continue
            # devforge's own runtime state (run dirs, sqlite index) is
            # untracked but is not source code under review.
            if path.startswith(".orchestrator/") or path == ".orchestrator":
                continue
            full = (project_root / path).resolve()
            if not full.exists() or not full.is_file():
                continue
            # `git diff --no-index` exits 1 when the files differ — that's
            # the normal case here, so we don't treat it as failure.
            proc = subprocess.run(
                ["git", "diff", "--no-index", "/dev/null", str(full)],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.stdout:
                parts.append(proc.stdout)
        return "\n".join(p for p in parts if p.strip())

    if source.kind == "staged":
        args = ["git", "diff", "--cached"]
    elif source.kind == "ref":
        rev = source.spec[len("ref:"):]
        args = ["git", "diff", rev]
    else:
        raise CodeReviewOnlyError(f"unknown diff source kind: {source.kind}")
    return _git(project_root, args)


def _git(project_root: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            args, cwd=str(project_root), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise CodeReviewOnlyError(f"git not on PATH: {exc}") from exc
    if proc.returncode != 0:
        raise CodeReviewOnlyError(
            f"`{' '.join(args)}` failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def changed_files_from_diff(diff_text: str) -> list[str]:
    """Extract ``b/<path>`` filenames from a unified diff."""
    out: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            ref = line[4:].strip()
            if ref.startswith("b/"):
                ref = ref[2:]
            if ref and ref != "/dev/null" and ref not in out:
                out.append(ref)
    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def run_code_review_only_workflow(
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    implementer_override: str | None = None,  # accepted, unused — review-only
    reviewer_override: str | None = None,
    *,
    state_store: StateStore | None = None,
    definition: Any = None,  # devforge.core.workflow_engine.WorkflowDefinition
) -> None:
    """Run review + judge over a pre-existing diff."""
    _ = implementer_override  # explicitly unused — kept for engine signature parity
    _ = definition

    if state_store is None:
        state_store = StateStore(run_ctx.root)
        if not state_store.is_initialized():
            state_store.init_run(
                workflow=run_ctx.workflow,
                input_ref=str(run_ctx.input_path) if run_ctx.input_path else None,
                stages=list(_STAGE_IDS),
            )

    registry = ProviderRegistry.from_config(cfg)
    state_store.snapshot_provider_registry(registry)
    router = RoleRouter(cfg, registry)

    project_root = Path(cfg.project.root).resolve()
    diff_spec = run_ctx.metadata.get("diff_spec") if run_ctx.metadata else None

    # Stage 1: collect_diff -----------------------------------------------
    state_store.save_step("collect_diff", "running")
    try:
        source = parse_diff_spec(diff_spec)
        diff_text = collect_diff_text(project_root, source)
    except CodeReviewOnlyError as exc:
        state_store.save_step("collect_diff", "failed", note=str(exc))
        _write_failure(run_ctx, "diff collection failed", {"reason": str(exc)})
        return
    if not diff_text.strip():
        state_store.save_step(
            "collect_diff", "skipped", note=f"empty diff from {source.detail}"
        )
        _write_failure(
            run_ctx,
            "empty diff",
            {"reason": "nothing to review", "source": source.to_dict()},
        )
        return

    candidate_dir = run_ctx.candidate_dir(_CANDIDATE_ID)
    (candidate_dir / "diff.patch").write_text(diff_text, encoding="utf-8")
    (candidate_dir / "diff_source.json").write_text(
        json.dumps(source.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    changed = changed_files_from_diff(diff_text)
    (candidate_dir / "changed_files.txt").write_text(
        "\n".join(changed) + ("\n" if changed else ""),
        encoding="utf-8",
    )
    state_store.save_step(
        "collect_diff", "completed", artifact_ref=f"candidates/{_CANDIDATE_ID}/diff.patch"
    )

    # Stage 2: reviewer ---------------------------------------------------
    state_store.save_step("review", "running")
    rv_decision = router.select("reviewer", override=reviewer_override)
    if not rv_decision.selected:
        state_store.save_step("review", "failed", note="no reviewer provider available")
        _write_failure(
            run_ctx, "no reviewer available", {"excluded": rv_decision.excluded}
        )
        return

    task_text = _read_task(run_ctx.input_path)
    review = run_reviewer_stage(
        cfg=cfg,
        run_ctx=run_ctx,
        registry=registry,
        reviewer_provider_id=rv_decision.selected[0],
        candidate_dir=candidate_dir,
        task_text=task_text,
        acceptance_criteria=_format_acceptance(task_text),
    )
    state_store.save_step(
        "review",
        "completed",
        artifact_ref=f"candidates/{_CANDIDATE_ID}/review.json",
    )

    # Stage 3: judge ------------------------------------------------------
    state_store.save_step("judge", "running")
    # Build a synthetic evaluation bundle — no validation, no implementer.
    file_policy = check_file_policy(changed, cfg.file_policy)
    cmd_policy = check_command_policy(
        [
            (review.agent_result.stdout if review.agent_result else ""),
            (review.agent_result.stderr if review.agent_result else ""),
        ],
        cfg.command_policy,
    )
    secret = scan_diff_and_logs(
        diff_text=diff_text,
        stdout=(review.agent_result.stdout if review.agent_result else ""),
        stderr=(review.agent_result.stderr if review.agent_result else ""),
        changed_files=changed,
    )
    mutation = check_test_mutation(diff_text, changed)
    bundle = EvaluationBundle(
        validation=ValidationReport(cwd=str(project_root), results={}),
        file_policy=file_policy,
        command_policy=cmd_policy,
        secret_scan=secret,
        test_mutation=mutation,
        reviewer_verdict=review.verdict,  # type: ignore[arg-type]
        acceptance_coverage=0.0,
        diff_size_lines=diff_text.count("\n"),
        previous_best_score=0.0,
        critical_review_issues=review.critical_count,
    )
    score = calculate_score(bundle, cfg.scoring)
    (candidate_dir / "score.json").write_text(
        json.dumps(score.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    decision = decide(bundle, score, cfg.stop_conditions)
    (candidate_dir / "decision.json").write_text(
        json.dumps(decision.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_ctx.root / "decision.json").write_text(
        json.dumps(
            {
                "run_id": run_ctx.run_id,
                "chosen_candidate": _CANDIDATE_ID,
                "decision": decision.to_dict(),
                "diff_source": source.to_dict(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_store.save_candidate(
        candidate_id=_CANDIDATE_ID,
        provider_id=rv_decision.selected[0],
        decision=decision.verdict,
        score=float(score.score),
        decision_ref=f"candidates/{_CANDIDATE_ID}/decision.json",
    )
    state_store.save_step(
        "judge",
        "completed",
        artifact_ref=f"candidates/{_CANDIDATE_ID}/decision.json",
    )

    # Stage 4: final_report ----------------------------------------------
    state_store.save_step("final_report", "running")
    _write_final_report(run_ctx, source, review, decision, score, changed)
    state_store.save_final_decision(
        decision_ref="decision.json", chosen_candidate=_CANDIDATE_ID
    )
    state_store.save_step("final_report", "completed", artifact_ref="final_report.md")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_task(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _format_acceptance(task_text: str) -> str:
    """code_review_only does not normalise a task; surface raw acceptance lines."""
    lines: list[str] = []
    for line in task_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            lines.append(f"- {stripped[2:].strip()}")
    return "\n".join(lines) or "(no explicit acceptance criteria — review for general quality + safety)"


def _write_failure(run_ctx: RunContext, message: str, details: dict) -> None:
    (run_ctx.root / "failure.json").write_text(
        json.dumps({"message": message, "details": details}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_ctx.root / "final_report.md").write_text(
        f"# Final Report — run {run_ctx.run_id}\n\nworkflow aborted: **{message}**\n"
        f"\n```\n{json.dumps(details, indent=2, ensure_ascii=False)}\n```\n",
        encoding="utf-8",
    )


def _write_final_report(
    run_ctx: RunContext,
    source: DiffSource,
    review,  # ReviewResult
    decision,  # Decision
    score,  # ScoreBreakdown
    changed_files: list[str],
) -> None:
    lines: list[str] = [f"# Code review — run {run_ctx.run_id}", ""]
    lines.append(f"- Diff source: **{source.detail}** (`{source.spec}`)")
    lines.append(f"- Reviewer verdict: **{review.verdict}**")
    lines.append(f"- Critical issues: **{review.critical_count}**")
    lines.append(f"- Score: **{score.score:.1f}**")
    lines.append(f"- Decision: **{decision.verdict}** ({decision.reason})")
    if changed_files:
        lines.append("")
        lines.append("## Changed files")
        lines.append("")
        for path in changed_files:
            lines.append(f"- `{path}`")
    lines.append("")
    lines.append("## Reviewer payload")
    lines.append("")
    if review.raw_json:
        lines.append("```json")
        lines.append(json.dumps(review.raw_json, indent=2, ensure_ascii=False))
        lines.append("```")
    else:
        lines.append("_(no structured review JSON returned)_")
    lines.append("")
    (run_ctx.root / "final_report.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )
