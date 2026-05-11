"""Integration test for the revision loop (DEVF-045)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.core.config_loader import DevforgeConfig
from devforge.core.run_context import create_run_context
from devforge.providers.base import AgentResult
from devforge.stages.feature_driver import run_feature_workflow
from tests.integration._mock_helpers import (
    commit_all,
    install_mock_providers,
    review_behavior,
)

pytestmark = pytest.mark.integration


def test_revision_loop_iterates_when_judge_says_revise(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests failing → revise → re-run; final iteration is at top level + revision_00 snapshot exists."""
    repo = Path(base_config.project.root)
    # Force tests to fail so the judge returns "revise".
    base_config.validation.commands.test = "false"
    base_config.mode.max_iterations_per_task = 2  # limit for the test

    call_count = {"n": 0}

    def impl_behavior(request):
        call_count["n"] += 1
        (request.cwd / "src").mkdir(exist_ok=True)
        (request.cwd / "src" / f"f{call_count['n']}.py").write_text(
            f"X = {call_count['n']}\n", encoding="utf-8"
        )
        commit_all(request.cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout=f"iteration {call_count['n']}",
            changed_files=[f"src/f{call_count['n']}.py"],
            exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl_behavior,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    task = tmp_path / "task.md"
    task.write_text(
        "# Goal\n\nDo X.\n\n## Acceptance Criteria\n\n- It works\n", encoding="utf-8"
    )
    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    # implementer ran at least twice (initial + 1 revision)
    assert call_count["n"] >= 2

    cand_dir = ctx.root / "candidates" / "mock_impl"
    assert (cand_dir / "revision_00").is_dir()
    # revision_00 captures the first iteration's decision (revise) for forensics
    snap_decision = json.loads(
        (cand_dir / "revision_00" / "decision.json").read_text(encoding="utf-8")
    )
    assert snap_decision["verdict"] in {"revise", "discard"}


def test_revision_loop_terminates_on_accept(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept on first iteration → only revision_00 snapshot, no re-run."""
    repo = Path(base_config.project.root)
    # Adjust threshold so the default 80-point score qualifies as accept.
    base_config.stop_conditions.accept_when.min_score = 70

    call_count = {"n": 0}

    def impl_behavior(request):
        call_count["n"] += 1
        (request.cwd / "src").mkdir(exist_ok=True)
        (request.cwd / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        commit_all(request.cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="impl",
            changed_files=["src/feature.py"],
            exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl_behavior,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    task = tmp_path / "task.md"
    task.write_text("# Goal\n\nDo X.\n\n## Acceptance Criteria\n\n- works\n",
                    encoding="utf-8")
    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    cand_dir = ctx.root / "candidates" / "mock_impl"
    assert (cand_dir / "revision_00").is_dir()
    assert not (cand_dir / "revision_01").exists()
    decision = json.loads((cand_dir / "decision.json").read_text(encoding="utf-8"))
    assert decision["verdict"] == "accept"
    assert call_count["n"] == 1  # implementer ran exactly once
