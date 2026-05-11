"""Integration tests for fallback (DEVF-052) and tournament mode (DEVF-053)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.core.config_loader import DevforgeConfig, ProviderConfig, RoleConfig
from devforge.core.run_context import create_run_context
from devforge.providers.base import (
    FAILURE_POLICY_VIOLATION,
    FAILURE_USAGE_LIMIT,
    AgentResult,
)
from devforge.stages.feature_driver import run_feature_workflow
from tests.integration._mock_helpers import (
    commit_all,
    install_mock_providers,
    review_behavior,
)

pytestmark = pytest.mark.integration


def _configure_two_implementers(cfg: DevforgeConfig) -> None:
    cfg.providers["mock_impl_b"] = ProviderConfig(type="mock", enabled=True)
    cfg.roles["implementer"] = RoleConfig(
        provider_order=["mock_impl", "mock_impl_b"], tournament=False
    )
    cfg.stop_conditions.accept_when.min_score = 70


def test_fallback_on_recoverable_failure(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First provider returns usage_limit → second provider succeeds."""
    repo = Path(base_config.project.root)
    _configure_two_implementers(base_config)

    def primary_fails(request):
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=False,
            stdout="",
            stderr="usage limit reached",
            exit_code=1,
            failure_class=FAILURE_USAGE_LIMIT,
            error="usage limit reached",
        )

    def backup_succeeds(request):
        (request.cwd / "src").mkdir(exist_ok=True)
        (request.cwd / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
        commit_all(request.cwd)
        return AgentResult(
            provider_id="mock_impl_b",
            role="implementer",
            success=True,
            stdout="b ok",
            changed_files=["src/x.py"],
            exit_code=0,
        )

    install_mock_providers(
        impl_behavior=primary_fails,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
        extra_impl={"mock_impl_b": backup_succeeds},
    )

    task = tmp_path / "t.md"
    task.write_text("# Goal\n\nDo X.\n\n## Acceptance Criteria\n\n- works\n",
                    encoding="utf-8")
    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    # fallback_history.json should capture the primary failure
    history_path = ctx.root / "fallback_history.json"
    assert history_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))["history"]
    assert any(h["provider"] == "mock_impl" for h in history)
    assert any(h["failure_class"] == FAILURE_USAGE_LIMIT for h in history)

    # mock_impl_b's candidate dir should exist with a decision
    assert (ctx.root / "candidates" / "mock_impl_b" / "decision.json").exists()


def test_fallback_short_circuits_on_fatal(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Policy violation is not recoverable — second provider must NOT run."""
    repo = Path(base_config.project.root)
    _configure_two_implementers(base_config)

    backup_called = {"n": 0}

    def primary_fails(request):
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=False,
            stderr="refused by policy",
            exit_code=1,
            failure_class=FAILURE_POLICY_VIOLATION,
            error="refused",
        )

    def backup(request):
        backup_called["n"] += 1
        return AgentResult(
            provider_id="mock_impl_b", role="implementer", success=True, exit_code=0
        )

    install_mock_providers(
        impl_behavior=primary_fails,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
        extra_impl={"mock_impl_b": backup},
    )

    task = tmp_path / "t.md"
    task.write_text("# Goal\n\nDo X.\n\n## Acceptance Criteria\n\n- works\n",
                    encoding="utf-8")
    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    assert backup_called["n"] == 0
    history = json.loads(
        (ctx.root / "fallback_history.json").read_text(encoding="utf-8")
    )["history"]
    assert len(history) == 1
    assert history[0]["failure_class"] == FAILURE_POLICY_VIOLATION


def test_tournament_runs_all_providers(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tournament mode creates one candidate per provider."""
    repo = Path(base_config.project.root)
    _configure_two_implementers(base_config)
    base_config.roles["implementer"].tournament = True

    def make_behavior(name: str):
        def _behave(request):
            (request.cwd / "src").mkdir(exist_ok=True)
            (request.cwd / "src" / f"{name}.py").write_text(f"# {name}\n", encoding="utf-8")
            commit_all(request.cwd)
            return AgentResult(
                provider_id=name,
                role="implementer",
                success=True,
                stdout=f"{name} ok",
                changed_files=[f"src/{name}.py"],
                exit_code=0,
            )

        return _behave

    install_mock_providers(
        impl_behavior=make_behavior("mock_impl"),
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
        extra_impl={"mock_impl_b": make_behavior("mock_impl_b")},
    )

    task = tmp_path / "t.md"
    task.write_text("# Goal\n\nDo X.\n\n## Acceptance Criteria\n\n- works\n",
                    encoding="utf-8")
    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    cand_a = ctx.root / "candidates" / "mock_impl" / "decision.json"
    cand_b = ctx.root / "candidates" / "mock_impl_b" / "decision.json"
    assert cand_a.exists() and cand_b.exists()
    # Tournament should not produce a fallback history.
    assert not (ctx.root / "fallback_history.json").exists()
    # DEVF-054 — comparison report present whenever there are ≥2 candidates.
    comparison = ctx.root / "comparison.md"
    assert comparison.exists()
    txt = comparison.read_text(encoding="utf-8")
    assert "mock_impl" in txt and "mock_impl_b" in txt
