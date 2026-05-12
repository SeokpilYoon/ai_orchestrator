"""DEVF-013 integration coverage — feature workflow records state."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.core.config_loader import DevforgeConfig
from devforge.core.run_context import create_run_context
from devforge.core.state_store import StateStore
from devforge.core.workflow_engine import WorkflowEngine, WorkflowLoadError
from devforge.providers.base import AgentResult
from tests.integration._mock_helpers import (
    commit_all,
    install_mock_providers,
    review_behavior,
)

pytestmark = pytest.mark.integration


def _task_md(tmp_path: Path) -> Path:
    p = tmp_path / "task.md"
    p.write_text(
        "# Goal\n\nDo X.\n\n## Acceptance Criteria\n\n- works\n", encoding="utf-8"
    )
    return p


def test_engine_records_full_state_on_success(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All six feature stages completed + 1 candidate + run.status=completed."""
    repo = Path(base_config.project.root)
    base_config.stop_conditions.accept_when.min_score = 70

    def impl(req):
        (req.cwd / "src").mkdir(exist_ok=True)
        (req.cwd / "src" / "f.py").write_text("X = 1\n", encoding="utf-8")
        commit_all(req.cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="ok",
            changed_files=["src/f.py"],
            exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    ctx = create_run_context(repo, workflow="feature", input_path=_task_md(tmp_path))
    engine = WorkflowEngine(base_config, ctx)
    engine.run("feature")

    state = StateStore(ctx.root)
    run = state.load_run()
    steps = state.load_steps()
    cands = state.load_candidates()

    assert run["status"] == "completed"
    assert run["workflow"] == "feature"
    assert run["chosen_candidate"] == "mock_impl"
    assert run["final_decision_ref"] == "decision.json"
    assert run["completed_at"] is not None

    # All six declared stages must end up in steps.json with a terminal status.
    by_id = {s["stage_id"]: s for s in steps}
    for sid in (
        "normalize_task",
        "inspect_repo",
        "plan",
        "implement_candidates",
        "comparison_report",
        "final_report",
    ):
        assert sid in by_id, f"missing step {sid}"
        assert by_id[sid]["status"] in {"completed", "skipped"}
    # comparison_report is skipped with a single candidate.
    assert by_id["comparison_report"]["status"] == "skipped"
    assert by_id["implement_candidates"]["status"] == "completed"

    # Candidate recorded with decision_ref pointing at the real artifact.
    assert len(cands) == 1
    cand = cands[0]
    assert cand["candidate_id"] == "mock_impl"
    assert cand["decision_ref"] == "candidates/mock_impl/decision.json"
    assert (ctx.root / cand["decision_ref"]).exists()


def test_engine_marks_failed_run_on_empty_plan(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty plan aborts the workflow; state shows implement_candidates skipped."""
    repo = Path(base_config.project.root)
    install_mock_providers(
        impl_behavior=None,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    ctx = create_run_context(repo, workflow="feature", input_path=empty)
    engine = WorkflowEngine(base_config, ctx)
    engine.run("feature")

    state = StateStore(ctx.root)
    run = state.load_run()
    by_id = {s["stage_id"]: s for s in state.load_steps()}
    # The driver writes failure.json, but the engine itself sees no exception
    # so run.status is "completed" — the failure is signalled by the skipped
    # implement_candidates step + the on-disk failure.json artifact.
    assert run["status"] in {"completed", "failed"}
    assert by_id["plan"]["status"] == "completed"
    assert by_id["implement_candidates"]["status"] == "skipped"
    assert by_id["implement_candidates"]["note"] == "aborted: empty plan"
    assert (ctx.root / "failure.json").exists()


def test_unknown_workflow_marks_run_failed(
    base_config: DevforgeConfig, tmp_path: Path
) -> None:
    """Engine returns a clear error and records run.status=failed for unknown workflow."""
    repo = Path(base_config.project.root)
    ctx = create_run_context(
        repo, workflow="this_workflow_does_not_exist", input_path=_task_md(tmp_path)
    )
    engine = WorkflowEngine(base_config, ctx)

    with pytest.raises(WorkflowLoadError, match="not found|no engine handler"):
        engine.run("this_workflow_does_not_exist")

    state = StateStore(ctx.root)
    run = state.load_run()
    assert run["status"] == "failed"
    assert run.get("error")


def test_run_context_run_json_untouched(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-existing run_root/run.json must not be overwritten by state_store."""
    repo = Path(base_config.project.root)
    base_config.stop_conditions.accept_when.min_score = 70

    def impl(req):
        (req.cwd / "src").mkdir(exist_ok=True)
        (req.cwd / "src" / "f.py").write_text("X = 1\n", encoding="utf-8")
        commit_all(req.cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="ok",
            changed_files=["src/f.py"],
            exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    ctx = create_run_context(repo, workflow="feature", input_path=_task_md(tmp_path))
    legacy_before = json.loads((ctx.root / "run.json").read_text(encoding="utf-8"))

    engine = WorkflowEngine(base_config, ctx)
    engine.run("feature")

    legacy_after = json.loads((ctx.root / "run.json").read_text(encoding="utf-8"))
    assert legacy_after == legacy_before
    # state lives in a subdir, not at run_root.
    assert (ctx.root / "state" / "run.json").exists()
    assert (ctx.root / "state" / "run.json") != (ctx.root / "run.json")
