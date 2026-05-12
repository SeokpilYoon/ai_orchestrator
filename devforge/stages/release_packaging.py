"""Release packaging stage (DEVF-071).

Final stage of the app_from_prd workflow. Bundles the run's artifacts
into a handoff-ready ``release/`` directory inside the run root:

- ``release/README.md``         — install + run instructions for the
                                  generated scaffold, plus the delivery
                                  status so a new user knows what works
- ``release/deployment.md``     — production handoff: runtime, env vars,
                                  persistence caveats, pre-deploy
                                  checklist, known limitations
- ``release/release_notes.md``  — what was delivered this run: slice +
                                  backlog outcomes, per-FR delivery,
                                  out-of-scope list
- ``release/qa_report.md``      — acceptance coverage breakdown, per-FR
                                  table, validation summary, follow-up
                                  list
- ``release/final_report.md``   — one-page exec summary with pointers
                                  to the other release docs

Deterministic — no LLM. Skips cleanly when the scaffold isn't supported
(no profile shipped for that stack, so there's nothing meaningful to
package). The top-level ``<run_root>/final_report.md`` is unchanged —
that remains the run's machine-readable summary consumed by
``devforge report``. The release-package ``final_report.md`` is a
human-readable companion living alongside the other deliverables.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.core.run_context import RunContext
from devforge.stages.acceptance_coverage import AcceptanceCoverage
from devforge.stages.architecture_generator import Architecture
from devforge.stages.backlog_generator import Backlog
from devforge.stages.backlog_implementer import BacklogProgress
from devforge.stages.mvp_scope import MvpScope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import Requirements
from devforge.stages.scaffold_generator import ScaffoldManifest
from devforge.stages.ux_flow import UxInventory
from devforge.stages.vertical_slice_implementer import (
    VerticalSliceImplementerResult,
)
from devforge.stages.vertical_slice_planner import VerticalSlicePlan

_RELEASE_DIR_NAME = "release"
_RELEASE_FILES = (
    "README.md",
    "deployment.md",
    "release_notes.md",
    "qa_report.md",
    "final_report.md",
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class ReleasePackage:
    decision: str = "completed"           # completed | skipped | failed
    reason: str = ""
    release_root: str = _RELEASE_DIR_NAME
    files: list[str] = field(default_factory=list)
    overall_coverage: float = 0.0
    deployable: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ReleasePackagingError(Exception):
    """Raised when packaging cannot proceed."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def package_release(
    run_ctx: RunContext,
    *,
    intake: PrdIntake,
    reqs: Requirements,
    scope: MvpScope,
    inventory: UxInventory,
    arch: Architecture,
    scaffold_manifest: ScaffoldManifest,
    slice_plan: VerticalSlicePlan | None,
    slice_result: VerticalSliceImplementerResult | None,
    backlog: Backlog | None,
    backlog_progress: BacklogProgress | None,
    coverage: AcceptanceCoverage | None,
) -> ReleasePackage:
    """Materialise the release directory next to the scaffold."""
    package = ReleasePackage()

    if not scaffold_manifest.supported:
        package.decision = "skipped"
        package.reason = (
            f"scaffold stack '{scaffold_manifest.stack}' has no profile; "
            f"nothing to package"
        )
        return package

    release_root = (run_ctx.root / _RELEASE_DIR_NAME).resolve()
    if not _is_inside(release_root, run_ctx.root.resolve()):
        raise ReleasePackagingError(
            "release_root must live inside run_root"
        )
    release_root.mkdir(parents=True, exist_ok=True)

    package.overall_coverage = coverage.overall_coverage if coverage else 0.0
    package.deployable = _is_deployable(scaffold_manifest, coverage)

    files_written: list[str] = []
    files_written.append(
        _write(release_root, "README.md", _render_readme(
            run_ctx, intake, scaffold_manifest, slice_result, backlog_progress,
            coverage,
        ))
    )
    files_written.append(
        _write(release_root, "deployment.md", _render_deployment(
            arch, scaffold_manifest,
        ))
    )
    files_written.append(
        _write(release_root, "release_notes.md", _render_release_notes(
            run_ctx, intake, scope, slice_plan, slice_result, backlog,
            backlog_progress, coverage,
        ))
    )
    files_written.append(
        _write(release_root, "qa_report.md", _render_qa_report(
            reqs, scope, slice_plan, slice_result, backlog, backlog_progress,
            coverage,
        ))
    )
    files_written.append(
        _write(release_root, "final_report.md", _render_release_final_report(
            run_ctx, intake, scaffold_manifest, slice_result, backlog_progress,
            coverage, package.deployable,
        ))
    )

    package.files = files_written
    package.notes.append(
        "Validation in this release was limited to `python -m compileall -q "
        "app tests`. Run the scaffold's full `pytest -q` after "
        "`pip install -e .[dev]` for a stronger gate before deploying."
    )
    return package


def save_release_package_manifest(
    package: ReleasePackage, path: Path
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(package.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Internal — renderers
# ---------------------------------------------------------------------------

def _render_readme(
    run_ctx: RunContext,
    intake: PrdIntake,
    manifest: ScaffoldManifest,
    slice_result: VerticalSliceImplementerResult | None,
    backlog_progress: BacklogProgress | None,
    coverage: AcceptanceCoverage | None,
) -> str:
    project = manifest.project_name or "app"
    summary = (intake.product_summary or "").strip() or (
        "Auto-generated scaffold from devforge."
    )

    lines: list[str] = [f"# {project}", "", summary, ""]
    lines.append("## Status")
    lines.append("")
    if coverage is not None:
        lines.append(
            f"- Acceptance coverage: **{coverage.overall_coverage:.0%}** "
            f"({coverage.overall_passed} of {coverage.overall_total} "
            f"acceptance criteria)"
        )
    if slice_result is not None:
        lines.append(
            f"- Vertical slice: **{slice_result.decision}**"
            + (
                f" — {slice_result.candidate_id}"
                if slice_result.candidate_id else ""
            )
        )
    if backlog_progress is not None:
        lines.append(
            f"- Backlog: **{backlog_progress.accepted_count}** of "
            f"**{backlog_progress.total_count}** items accepted "
            f"(decision: {backlog_progress.decision})"
        )
    lines.append("")

    lines.append("## Install")
    lines.append("")
    lines.append("```bash")
    lines.append("python -m venv .venv")
    lines.append("source .venv/bin/activate")
    lines.append('pip install -e ".[dev]"')
    lines.append("```")
    lines.append("")

    lines.append("## Run")
    lines.append("")
    lines.append("```bash")
    lines.append("uvicorn app.main:app --reload")
    lines.append("```")
    lines.append("")
    lines.append(
        "The health probe lives at `GET /health`. Each generated resource "
        "exposes the usual CRUD verbs under `/<resource>s`."
    )
    lines.append("")

    lines.append("## Test")
    lines.append("")
    lines.append("```bash")
    lines.append(manifest.test_command or "pytest -q")
    lines.append("```")
    lines.append("")

    lines.append("## Layout")
    lines.append("")
    for f in manifest.files[:20]:
        lines.append(f"- `{f.path}`")
    if len(manifest.files) > 20:
        lines.append(f"- ... and {len(manifest.files) - 20} more")
    lines.append("")

    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- Generated by `devforge create-app` (run `{run_ctx.run_id}`).")
    lines.append("- Stack: `" + manifest.stack + "`.")
    lines.append(
        "- See `release_notes.md` for what shipped this run and "
        "`qa_report.md` for the acceptance coverage breakdown."
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_deployment(
    arch: Architecture, manifest: ScaffoldManifest
) -> str:
    lines: list[str] = ["# Deployment notes", ""]
    lines.append("## Runtime")
    lines.append("")
    lines.append(f"- Runtime: {arch.runtime}")
    lines.append(f"- Framework: {arch.framework}")
    lines.append(f"- Test command: `{arch.test_command}`")
    lines.append("")

    lines.append("## Persistence")
    lines.append("")
    lines.append(f"- {arch.persistence}")
    lines.append(
        "- The scaffold ships with an in-memory store by default. Swap "
        "`app/store.py` for a real database before any multi-process "
        "deployment — otherwise each worker holds an independent copy "
        "of the data."
    )
    lines.append("")

    lines.append("## Environment variables")
    lines.append("")
    lines.append(
        "- No environment variables are required for the MVP scaffold. "
        "Add them as you wire real persistence, auth, or external APIs."
    )
    lines.append(
        "- Never commit secrets — devforge's policy blocks `.env*` files "
        "by default."
    )
    lines.append("")

    lines.append("## Pre-deploy checklist")
    lines.append("")
    lines.append("- [ ] Replace in-memory `app/store.py` with a real database")
    lines.append("- [ ] Add authentication / authorisation (none ships in MVP)")
    lines.append("- [ ] Add rate limiting in front of public endpoints")
    lines.append("- [ ] Run `pytest -q` after `pip install -e .[dev]`")
    lines.append("- [ ] Confirm CORS / TLS settings for the target environment")
    lines.append("")

    lines.append("## Known limitations")
    lines.append("")
    for note in arch.notes or []:
        lines.append(f"- {note}")
    for note in manifest.notes or []:
        lines.append(f"- {note}")
    if not (arch.notes or manifest.notes):
        lines.append("- _(none recorded by upstream stages)_")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_release_notes(
    run_ctx: RunContext,
    intake: PrdIntake,
    scope: MvpScope,
    slice_plan: VerticalSlicePlan | None,
    slice_result: VerticalSliceImplementerResult | None,
    backlog: Backlog | None,
    backlog_progress: BacklogProgress | None,
    coverage: AcceptanceCoverage | None,
) -> str:
    lines: list[str] = ["# Release notes", ""]
    lines.append(f"- Run id: `{run_ctx.run_id}`")
    lines.append(f"- Created at: {run_ctx.created_at}")
    if intake.product_summary:
        lines.append("")
        lines.append("## Product summary")
        lines.append("")
        lines.append(intake.product_summary.strip())
    lines.append("")

    lines.append("## Vertical slice")
    lines.append("")
    if slice_plan is not None:
        lines.append(f"- Slice: **{slice_plan.vertical_slice_name}**")
        lines.append(
            f"- Requirements in slice: "
            f"{', '.join(slice_plan.requirement_ids) or '(none)'}"
        )
    if slice_result is not None:
        lines.append(f"- Decision: **{slice_result.decision}**")
        if slice_result.candidate_id:
            lines.append(
                f"- Candidate: `{slice_result.candidate_id}` "
                f"(provider `{slice_result.provider_id}`)"
            )
        if slice_result.reason:
            lines.append(f"- Reason: {slice_result.reason}")
    lines.append("")

    lines.append("## Backlog delivery")
    lines.append("")
    if backlog_progress is not None and backlog is not None:
        lines.append(
            f"- **{backlog_progress.accepted_count}** of "
            f"**{backlog_progress.total_count}** items accepted"
        )
        lines.append(f"- Decision: **{backlog_progress.decision}**")
        if backlog_progress.acceptance_coverage:
            lines.append(
                f"- Backlog acceptance coverage: "
                f"{backlog_progress.acceptance_coverage:.0%}"
            )
        accepted = [it.task_id for it in backlog_progress.items if it.status == "accept"]
        if accepted:
            lines.append(f"- Accepted tasks: {', '.join(accepted)}")
    else:
        lines.append("- (backlog implementation not run)")
    lines.append("")

    if coverage is not None:
        lines.append("## Acceptance coverage")
        lines.append("")
        lines.append(
            f"- Overall: **{coverage.overall_passed}/{coverage.overall_total}** "
            f"({coverage.overall_coverage:.0%})"
        )
        for pr in coverage.by_priority:
            lines.append(
                f"- {pr.priority}: {pr.passed}/{pr.total} "
                f"({pr.coverage:.0%})"
            )
        lines.append("")

    lines.append("## Out of scope")
    lines.append("")
    out_of_scope = list(intake.out_of_scope) + list(scope.out_of_scope)
    seen: set[str] = set()
    deduped = []
    for item in out_of_scope:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    if deduped:
        for item in deduped:
            lines.append(f"- {item}")
    else:
        lines.append("- _(nothing explicitly out of scope)_")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_qa_report(
    reqs: Requirements,
    scope: MvpScope,
    slice_plan: VerticalSlicePlan | None,
    slice_result: VerticalSliceImplementerResult | None,
    backlog: Backlog | None,
    backlog_progress: BacklogProgress | None,
    coverage: AcceptanceCoverage | None,
) -> str:
    lines: list[str] = ["# QA report", ""]

    if coverage is None:
        lines.append("_(no coverage artifact available)_\n")
        return "\n".join(lines)

    lines.append(
        f"Overall acceptance coverage: "
        f"**{coverage.overall_passed}/{coverage.overall_total}** "
        f"acceptance criteria ({coverage.overall_coverage:.1%}).")
    lines.append("")

    lines.append("## Per-priority")
    lines.append("")
    lines.append("| Priority | Passed | Total | Coverage |")
    lines.append("|---|---|---|---|")
    for pr in coverage.by_priority:
        lines.append(
            f"| {pr.priority} | {pr.passed} | {pr.total} | "
            f"{pr.coverage:.0%} |"
        )
    lines.append("")

    lines.append("## Per-requirement")
    lines.append("")
    lines.append(
        "| Requirement | Priority | Title | Passed / Total | Source | "
        "Tasks |"
    )
    lines.append("|---|---|---|---|---|---|")
    for fr in coverage.by_requirement:
        tasks = ", ".join(fr.source_task_ids) if fr.source_task_ids else "—"
        lines.append(
            f"| `{fr.requirement_id}` | {fr.priority} | {fr.title} | "
            f"{fr.passed}/{fr.total} | {fr.covered_by} | {tasks} |"
        )
    lines.append("")

    uncovered = [fr for fr in coverage.by_requirement if fr.covered_by == "none"]
    if uncovered:
        lines.append("## Outstanding")
        lines.append("")
        for fr in uncovered:
            lines.append(
                f"- `{fr.requirement_id}` ({fr.priority}) — {fr.title}"
            )
        lines.append("")

    if backlog_progress is not None and backlog_progress.notes:
        lines.append("## Validation notes")
        lines.append("")
        for n in backlog_progress.notes:
            lines.append(f"- {n}")
        lines.append("")

    # Hint at follow-ups the new user should run themselves.
    lines.append("## Recommended follow-ups")
    lines.append("")
    lines.append(
        "- Run the scaffold's full test suite with `pytest -q` after "
        "`pip install -e .[dev]` — devforge only runs `python -m "
        "compileall` to stay dependency-free in CI."
    )
    if uncovered:
        lines.append(
            "- Address the outstanding requirements above before treating "
            "the slice as production-ready."
        )
    lines.append(
        "- Re-run `devforge create-app` with `--implementer` / `--reviewer` "
        "pointing at a real provider to drive coverage higher."
    )

    # Silence linter for variables we accepted but did not deeply use:
    _ = (reqs, scope, slice_plan, slice_result, backlog)
    return "\n".join(lines).rstrip() + "\n"


def _render_release_final_report(
    run_ctx: RunContext,
    intake: PrdIntake,
    manifest: ScaffoldManifest,
    slice_result: VerticalSliceImplementerResult | None,
    backlog_progress: BacklogProgress | None,
    coverage: AcceptanceCoverage | None,
    deployable: bool,
) -> str:
    project = manifest.project_name or "app"
    lines: list[str] = [f"# Final report — {project}", ""]
    lines.append(f"- Run id: `{run_ctx.run_id}`")
    lines.append(f"- Stack: `{manifest.stack}`")
    lines.append(
        f"- Deployable as-is: **{'yes' if deployable else 'no'}** "
        f"(see `deployment.md` for the pre-deploy checklist)"
    )
    if coverage is not None:
        lines.append(
            f"- Acceptance coverage: "
            f"**{coverage.overall_coverage:.0%}** "
            f"({coverage.overall_passed}/{coverage.overall_total})"
        )
    if slice_result is not None:
        lines.append(f"- Vertical slice: **{slice_result.decision}**")
    if backlog_progress is not None:
        lines.append(
            f"- Backlog: **{backlog_progress.accepted_count}/"
            f"{backlog_progress.total_count}** accepted"
        )
    lines.append("")

    if intake.product_summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(intake.product_summary.strip())
        lines.append("")

    lines.append("## Where to look next")
    lines.append("")
    lines.append("- `README.md` — install + run instructions for the scaffold")
    lines.append("- `deployment.md` — production handoff notes and checklist")
    lines.append("- `release_notes.md` — what shipped this run, in detail")
    lines.append("- `qa_report.md` — per-FR acceptance coverage table")
    lines.append(
        "- `../scaffold/` — the generated source tree (git history records "
        "each accepted slice / backlog commit)"
    )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Internal — helpers
# ---------------------------------------------------------------------------

def _is_deployable(
    manifest: ScaffoldManifest, coverage: AcceptanceCoverage | None
) -> bool:
    """A run is deployable when the scaffold is supported, its smoke
    passed, and every must-have requirement is covered."""
    if not manifest.supported or not manifest.import_smoke_passed:
        return False
    if coverage is None:
        return False
    for pr in coverage.by_priority:
        if pr.priority == "must" and pr.total and pr.passed < pr.total:
            return False
    return True


def _write(release_root: Path, rel: str, content: str) -> str:
    target = (release_root / rel).resolve()
    if not _is_inside(target, release_root):
        raise ReleasePackagingError(
            f"refused to write outside release root: {rel}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return rel


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


_ = _RELEASE_FILES  # exported for tests that want the canonical list
