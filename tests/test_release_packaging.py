"""Unit tests for the release packaging stage (DEVF-071)."""
from __future__ import annotations

from pathlib import Path

from devforge.core.run_context import create_run_context
from devforge.stages.acceptance_coverage import (
    AcceptanceCoverage,
    FrCoverage,
    PriorityRollup,
)
from devforge.stages.architecture_generator import Architecture, Entity
from devforge.stages.backlog_generator import Backlog, BacklogItem
from devforge.stages.backlog_implementer import (
    BacklogProgress,
    BacklogProgressItem,
)
from devforge.stages.mvp_scope import freeze_mvp_scope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.release_packaging import (
    ReleasePackage,
    package_release,
    save_release_package_manifest,
)
from devforge.stages.requirements_schema import (
    FunctionalRequirement,
    Requirements,
)
from devforge.stages.scaffold_generator import ScaffoldFile, ScaffoldManifest
from devforge.stages.ux_flow import UxInventory
from devforge.stages.vertical_slice_implementer import (
    VerticalSliceImplementerResult,
)
from devforge.stages.vertical_slice_planner import VerticalSlicePlan

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _intake() -> PrdIntake:
    return PrdIntake(
        product_summary="A tiny todo service for solo developers.",
        target_users=["devs"],
        out_of_scope=["Multi-user auth"],
    )


def _fr(idx: int, *, priority: str = "must", acs: int = 1) -> FunctionalRequirement:
    return FunctionalRequirement(
        id=f"FR-{idx:03d}",
        title=f"Req {idx}",
        description=f"req {idx}",
        priority=priority,
        acceptance_criteria=[f"ac{idx}.{j}" for j in range(1, acs + 1)],
        test_strategy="integration",
    )


def _reqs() -> Requirements:
    return Requirements(functional=[_fr(1, acs=2), _fr(2, priority="should")])


def _scope(reqs: Requirements):
    return freeze_mvp_scope(reqs, _intake())


def _inventory() -> UxInventory:
    return UxInventory()


def _arch(*, supported: bool = True) -> Architecture:
    return Architecture(
        stack="python-fastapi-only",
        supported_stack=supported,
        runtime="Python 3.11+",
        framework="FastAPI",
        test_command="pytest -q",
        entities=[Entity(name="Task", fields={"id": "integer"}, sourced_from=["FR-001"])],
        persistence="In-memory store",
        notes=["MVP uses in-memory storage; swap before scaling."],
        project_name="todo",
    )


def _manifest(
    *, supported: bool = True, smoke: bool = True
) -> ScaffoldManifest:
    return ScaffoldManifest(
        stack="python-fastapi-only",
        supported=supported,
        scaffold_root="scaffold",
        files=[
            ScaffoldFile(path="app/__init__.py", bytes=0, sha256="aaa"),
            ScaffoldFile(path="app/main.py", bytes=64, sha256="bbb"),
            ScaffoldFile(path="tests/__init__.py", bytes=0, sha256="ccc"),
        ],
        import_smoke_passed=smoke,
        test_command="pytest -q",
        project_name="todo",
        entities=["Task"],
    )


def _slice_plan() -> VerticalSlicePlan:
    return VerticalSlicePlan(
        vertical_slice_name="Add + list tasks",
        user_journey=["POST /tasks", "GET /tasks"],
        requirement_ids=["FR-001"],
        acceptance_criteria=["x"],
    )


def _slice_accept() -> VerticalSliceImplementerResult:
    return VerticalSliceImplementerResult(
        decision="accept",
        candidate_id="mock_impl",
        provider_id="mock_impl",
        score=80.0,
    )


def _backlog() -> Backlog:
    return Backlog(items=[
        BacklogItem(
            id="TASK-001",
            title="Add task",
            requirement_ids=["FR-001"],
            acceptance_criteria=["ac1"],
            priority="P0",
            estimated_complexity="S",
        ),
        BacklogItem(
            id="TASK-002",
            title="List tasks",
            requirement_ids=["FR-002"],
            acceptance_criteria=["ac2"],
            priority="P1",
            estimated_complexity="S",
        ),
    ])


def _backlog_progress() -> BacklogProgress:
    return BacklogProgress(
        decision="completed",
        accepted_count=2,
        total_count=2,
        acceptance_coverage=1.0,
        items=[
            BacklogProgressItem(task_id="TASK-001", status="already_in_slice"),
            BacklogProgressItem(task_id="TASK-002", status="accept"),
        ],
        notes=["Validation is limited to compileall."],
    )


def _coverage_full() -> AcceptanceCoverage:
    return AcceptanceCoverage(
        overall_total=3,
        overall_passed=3,
        overall_coverage=1.0,
        by_requirement=[
            FrCoverage(
                requirement_id="FR-001",
                title="Req 1",
                priority="must",
                total=2,
                passed=2,
                coverage=1.0,
                covered_by="slice",
            ),
            FrCoverage(
                requirement_id="FR-002",
                title="Req 2",
                priority="should",
                total=1,
                passed=1,
                coverage=1.0,
                covered_by="backlog",
                source_task_ids=["TASK-002"],
            ),
        ],
        by_priority=[
            PriorityRollup(
                priority="must", total=2, passed=2, coverage=1.0, fr_count=1
            ),
            PriorityRollup(
                priority="should", total=1, passed=1, coverage=1.0, fr_count=1
            ),
        ],
    )


def _coverage_partial() -> AcceptanceCoverage:
    return AcceptanceCoverage(
        overall_total=3,
        overall_passed=1,
        overall_coverage=1 / 3,
        by_requirement=[
            FrCoverage(
                requirement_id="FR-001",
                title="Req 1",
                priority="must",
                total=2,
                passed=1,
                coverage=0.5,
                covered_by="backlog",
                source_task_ids=["TASK-001"],
            ),
            FrCoverage(
                requirement_id="FR-002",
                title="Req 2",
                priority="should",
                total=1,
                passed=0,
                coverage=0.0,
                covered_by="none",
            ),
        ],
        by_priority=[
            PriorityRollup(
                priority="must", total=2, passed=1, coverage=0.5, fr_count=1
            ),
            PriorityRollup(
                priority="should", total=1, passed=0, coverage=0.0, fr_count=1
            ),
        ],
    )


def _setup_run(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    return create_run_context(
        project_root, workflow="app_from_prd", input_path=None
    )


# ---------------------------------------------------------------------------
# Skip-gate
# ---------------------------------------------------------------------------

def test_unsupported_scaffold_skips(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package = package_release(
        run_ctx,
        intake=_intake(),
        reqs=reqs,
        scope=_scope(reqs),
        inventory=_inventory(),
        arch=_arch(supported=False),
        scaffold_manifest=_manifest(supported=False),
        slice_plan=None,
        slice_result=None,
        backlog=None,
        backlog_progress=None,
        coverage=None,
    )
    assert package.decision == "skipped"
    assert "no profile" in package.reason
    assert not (run_ctx.root / "release").exists()


# ---------------------------------------------------------------------------
# Happy path — every file is written with the spec'd name
# ---------------------------------------------------------------------------

def test_writes_all_five_release_files(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package = package_release(
        run_ctx,
        intake=_intake(),
        reqs=reqs,
        scope=_scope(reqs),
        inventory=_inventory(),
        arch=_arch(),
        scaffold_manifest=_manifest(),
        slice_plan=_slice_plan(),
        slice_result=_slice_accept(),
        backlog=_backlog(),
        backlog_progress=_backlog_progress(),
        coverage=_coverage_full(),
    )
    assert package.decision == "completed"
    release_root = run_ctx.root / "release"
    for name in (
        "README.md",
        "deployment.md",
        "release_notes.md",
        "qa_report.md",
        "final_report.md",
    ):
        assert (release_root / name).exists()
        assert name in package.files


# ---------------------------------------------------------------------------
# Content sanity checks per file
# ---------------------------------------------------------------------------

def test_readme_contains_install_and_run_blocks(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package_release(
        run_ctx,
        intake=_intake(), reqs=reqs, scope=_scope(reqs),
        inventory=_inventory(), arch=_arch(),
        scaffold_manifest=_manifest(),
        slice_plan=_slice_plan(), slice_result=_slice_accept(),
        backlog=_backlog(), backlog_progress=_backlog_progress(),
        coverage=_coverage_full(),
    )
    body = (run_ctx.root / "release" / "README.md").read_text(encoding="utf-8")
    assert "## Install" in body
    assert "uvicorn app.main:app" in body
    assert "pytest -q" in body
    # Reflects delivery status.
    assert "100%" in body or "Acceptance coverage" in body


def test_deployment_lists_pre_deploy_checklist(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package_release(
        run_ctx,
        intake=_intake(), reqs=reqs, scope=_scope(reqs),
        inventory=_inventory(), arch=_arch(),
        scaffold_manifest=_manifest(),
        slice_plan=None, slice_result=None,
        backlog=None, backlog_progress=None, coverage=None,
    )
    body = (run_ctx.root / "release" / "deployment.md").read_text(encoding="utf-8")
    assert "Pre-deploy checklist" in body
    assert "in-memory" in body.lower()
    assert "secrets" in body.lower() or "env" in body.lower()


def test_release_notes_show_slice_and_backlog(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package_release(
        run_ctx,
        intake=_intake(), reqs=reqs, scope=_scope(reqs),
        inventory=_inventory(), arch=_arch(),
        scaffold_manifest=_manifest(),
        slice_plan=_slice_plan(), slice_result=_slice_accept(),
        backlog=_backlog(), backlog_progress=_backlog_progress(),
        coverage=_coverage_full(),
    )
    body = (run_ctx.root / "release" / "release_notes.md").read_text(encoding="utf-8")
    assert "Vertical slice" in body
    assert "Add + list tasks" in body
    assert "TASK-002" in body  # accepted backlog task
    assert "Multi-user auth" in body  # PRD-declared out of scope


def test_qa_report_includes_per_fr_table(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package_release(
        run_ctx,
        intake=_intake(), reqs=reqs, scope=_scope(reqs),
        inventory=_inventory(), arch=_arch(),
        scaffold_manifest=_manifest(),
        slice_plan=_slice_plan(), slice_result=_slice_accept(),
        backlog=_backlog(), backlog_progress=_backlog_progress(),
        coverage=_coverage_partial(),
    )
    body = (run_ctx.root / "release" / "qa_report.md").read_text(encoding="utf-8")
    assert "Per-requirement" in body
    assert "FR-001" in body and "FR-002" in body
    # Partial coverage surfaces an outstanding section.
    assert "Outstanding" in body


def test_final_report_links_to_other_docs(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package_release(
        run_ctx,
        intake=_intake(), reqs=reqs, scope=_scope(reqs),
        inventory=_inventory(), arch=_arch(),
        scaffold_manifest=_manifest(),
        slice_plan=_slice_plan(), slice_result=_slice_accept(),
        backlog=_backlog(), backlog_progress=_backlog_progress(),
        coverage=_coverage_full(),
    )
    body = (run_ctx.root / "release" / "final_report.md").read_text(
        encoding="utf-8"
    )
    assert "deployment.md" in body
    assert "release_notes.md" in body
    assert "qa_report.md" in body


# ---------------------------------------------------------------------------
# Deployable signal
# ---------------------------------------------------------------------------

def test_deployable_when_must_coverage_complete(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package = package_release(
        run_ctx,
        intake=_intake(), reqs=reqs, scope=_scope(reqs),
        inventory=_inventory(), arch=_arch(),
        scaffold_manifest=_manifest(),
        slice_plan=_slice_plan(), slice_result=_slice_accept(),
        backlog=_backlog(), backlog_progress=_backlog_progress(),
        coverage=_coverage_full(),
    )
    assert package.deployable is True


def test_not_deployable_when_must_coverage_partial(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package = package_release(
        run_ctx,
        intake=_intake(), reqs=reqs, scope=_scope(reqs),
        inventory=_inventory(), arch=_arch(),
        scaffold_manifest=_manifest(),
        slice_plan=_slice_plan(), slice_result=_slice_accept(),
        backlog=_backlog(), backlog_progress=_backlog_progress(),
        coverage=_coverage_partial(),
    )
    assert package.deployable is False


def test_not_deployable_when_smoke_failed(tmp_path: Path) -> None:
    run_ctx = _setup_run(tmp_path)
    reqs = _reqs()
    package = package_release(
        run_ctx,
        intake=_intake(), reqs=reqs, scope=_scope(reqs),
        inventory=_inventory(), arch=_arch(),
        scaffold_manifest=_manifest(smoke=False),
        slice_plan=_slice_plan(), slice_result=_slice_accept(),
        backlog=_backlog(), backlog_progress=_backlog_progress(),
        coverage=_coverage_full(),
    )
    assert package.deployable is False


# ---------------------------------------------------------------------------
# Manifest serialization
# ---------------------------------------------------------------------------

def test_save_release_package_manifest_writes_valid_json(tmp_path: Path) -> None:
    package = ReleasePackage(
        decision="completed",
        files=["README.md"],
        overall_coverage=1.0,
        deployable=True,
    )
    out = tmp_path / "package.json"
    save_release_package_manifest(package, out)
    body = out.read_text(encoding="utf-8")
    assert "completed" in body and "README.md" in body
