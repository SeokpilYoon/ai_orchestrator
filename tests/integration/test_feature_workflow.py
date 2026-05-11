"""Integration smoke for the feature workflow using mock providers."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devforge.core.config_loader import DevforgeConfig
from devforge.core.run_context import create_run_context
from devforge.providers.mock import MockProvider, write_files_behavior
from devforge.providers.registry import ProviderRegistry
from devforge.stages.feature_driver import run_feature_workflow

pytestmark = pytest.mark.integration


def _commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    # Skip silently if there's nothing to commit (e.g. revision loop re-runs
    # the same mock behavior in an already-up-to-date worktree).
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo, check=False
    )
    if staged.returncode == 0:
        return
    subprocess.run(
        ["git", "commit", "-m", "step"], cwd=repo, check=True, capture_output=True
    )


def _install_mock_providers(
    base_config: DevforgeConfig,
    *,
    impl_behavior=None,
    review_behavior=None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monkey-patch ProviderRegistry.from_config to inject mock providers."""

    def patched(cfg: DevforgeConfig) -> ProviderRegistry:  # noqa: ARG001
        reg = ProviderRegistry()
        reg.register(MockProvider("mock_impl", behavior=impl_behavior))
        reg.register(MockProvider("mock_review", behavior=review_behavior))
        from devforge.core.config_loader import ProviderConfig
        from devforge.providers.local_rule_based import LocalRuleBasedProvider

        reg.register(
            LocalRuleBasedProvider("local_rule_based", ProviderConfig(type="local_rule_based"))
        )
        return reg

    monkeypatch.setattr(ProviderRegistry, "from_config", staticmethod(patched))


def _make_review_behavior(verdict: str):
    from devforge.providers.base import AgentResult

    payload = (
        '{"verdict": "' + verdict + '", "requirement_coverage": 1.0, '
        '"critical_issues": [], "major_issues": [], "minor_issues": [], '
        '"test_concerns": [], "security_concerns": [], "recommended_revision_prompt": ""}'
    )

    def _behave(request):  # noqa: ARG001
        return AgentResult(
            provider_id="mock_review",
            role="reviewer",
            success=True,
            stdout=payload,
            exit_code=0,
        )

    return _behave


def test_accept_on_clean_patch(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)

    def impl_behavior(request):
        from devforge.providers.base import AgentResult

        (request.cwd / "src").mkdir(exist_ok=True)
        (request.cwd / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        _commit_all(request.cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="implemented",
            changed_files=["src/feature.py"],
            exit_code=0,
        )

    _install_mock_providers(
        base_config,
        impl_behavior=impl_behavior,
        review_behavior=_make_review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    task = tmp_path / "task.md"
    task.write_text("add feature", encoding="utf-8")

    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, implementer_override=None, reviewer_override=None)

    final = (ctx.root / "final_report.md").read_text(encoding="utf-8")
    decision = (ctx.root / "decision.json").read_text(encoding="utf-8")
    assert "mock_impl" in final
    assert "accept" in decision


def test_discard_on_blocked_file(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)

    def impl_behavior(request):
        from devforge.providers.base import AgentResult

        # Create .env inside the worktree — this is in blocked_paths.
        (request.cwd / ".env").write_text("OPENAI_API_KEY=placeholder\n", encoding="utf-8")
        _commit_all(request.cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="oops",
            changed_files=[".env"],
            exit_code=0,
        )

    _install_mock_providers(
        base_config,
        impl_behavior=impl_behavior,
        review_behavior=_make_review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    task = tmp_path / "task.md"
    task.write_text("bad task", encoding="utf-8")

    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    cand_decision = (ctx.root / "candidates" / "mock_impl" / "decision.json").read_text()
    assert "discard" in cand_decision


def test_artifact_stages_persist(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEVF-040/041/042: normalized_task.json + repo_context.md + implementation_plan.json."""
    repo = Path(base_config.project.root)

    def impl_behavior(request):
        from devforge.providers.base import AgentResult

        (request.cwd / "src").mkdir(exist_ok=True)
        (request.cwd / "src" / "f.py").write_text("X = 1\n", encoding="utf-8")
        _commit_all(request.cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="impl",
            changed_files=["src/f.py"],
            exit_code=0,
        )

    _install_mock_providers(
        base_config,
        impl_behavior=impl_behavior,
        review_behavior=_make_review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    task = tmp_path / "task.md"
    task.write_text(
        "# Goal\n\nAdd feature F.\n\n## Acceptance Criteria\n\n- F returns 1\n",
        encoding="utf-8",
    )

    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    nt = ctx.root / "normalized_task.json"
    rc = ctx.root / "repo_context.md"
    ip = ctx.root / "implementation_plan.json"
    assert nt.exists() and rc.exists() and ip.exists()

    import json as _json

    norm = _json.loads(nt.read_text(encoding="utf-8"))
    assert "Add feature F" in norm["goal"]
    assert any("F returns 1" in ac for ac in norm["acceptance_criteria"])

    plan = _json.loads(ip.read_text(encoding="utf-8"))
    assert plan["steps"], "plan steps must not be empty"


def test_empty_plan_aborts_workflow(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If task has no goal and no acceptance criteria, the workflow aborts."""
    repo = Path(base_config.project.root)
    _install_mock_providers(
        base_config,
        impl_behavior=None,  # should never run
        review_behavior=_make_review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    task = tmp_path / "empty.md"
    task.write_text("", encoding="utf-8")
    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    assert (ctx.root / "failure.json").exists()
    # implementer worktree should NOT have been created
    assert not list((ctx.root / "candidates").iterdir())


def test_revise_on_test_fail(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)
    base_config.validation.commands.test = "false"

    impl_behavior = write_files_behavior({"src/x.py": "X = 1\n"})

    def impl_with_commit(request):
        res = impl_behavior(request)
        _commit_all(request.cwd)
        return res

    _install_mock_providers(
        base_config,
        impl_behavior=impl_with_commit,
        review_behavior=_make_review_behavior("pass"),
        monkeypatch=monkeypatch,
    )

    task = tmp_path / "task.md"
    task.write_text("task", encoding="utf-8")

    ctx = create_run_context(repo, workflow="feature", input_path=task)
    run_feature_workflow(base_config, ctx, None, None)

    cand_decision = (ctx.root / "candidates" / "mock_impl" / "decision.json").read_text()
    assert "revise" in cand_decision or "tests_failed" in cand_decision
