"""Integration coverage for the ``bugfix`` and ``refactor`` workflow variants.

Both variants share the feature driver — only the implementer prompt
framing differs. These tests verify:

1. ``WorkflowEngine.run("bugfix")`` / ``"refactor"`` no longer raise the
   "no engine handler" error.
2. The variant-specific guidance text is prepended to the implementer's
   rendered ``prompt.md``.
3. The state store records the workflow id (so SQLite reports the
   variant accurately).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devforge.core.config_loader import DevforgeConfig
from devforge.core.run_context import create_run_context
from devforge.core.state_store import StateStore
from devforge.core.workflow_engine import WorkflowEngine
from devforge.providers.base import AgentResult
from devforge.providers.mock import MockProvider
from devforge.providers.registry import ProviderRegistry

pytestmark = pytest.mark.integration


def _commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identical pattern to test_feature_workflow's mock installer."""

    review_payload = (
        '{"verdict": "pass", "requirement_coverage": 1.0, '
        '"critical_issues": [], "major_issues": [], "minor_issues": [], '
        '"test_concerns": [], "security_concerns": [], '
        '"recommended_revision_prompt": ""}'
    )

    def impl_behavior(request):
        (request.cwd / "src").mkdir(exist_ok=True)
        (request.cwd / "src" / "f.py").write_text("VALUE = 1\n", encoding="utf-8")
        _commit_all(request.cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="implemented",
            changed_files=["src/f.py"],
            exit_code=0,
        )

    def review_behavior(request):  # noqa: ARG001
        return AgentResult(
            provider_id="mock_review",
            role="reviewer",
            success=True,
            stdout=review_payload,
            exit_code=0,
        )

    def patched(_cfg: DevforgeConfig) -> ProviderRegistry:
        reg = ProviderRegistry()
        reg.register(MockProvider("mock_impl", behavior=impl_behavior))
        reg.register(MockProvider("mock_review", behavior=review_behavior))
        from devforge.core.config_loader import ProviderConfig
        from devforge.providers.local_rule_based import LocalRuleBasedProvider
        reg.register(
            LocalRuleBasedProvider(
                "local_rule_based", ProviderConfig(type="local_rule_based")
            )
        )
        return reg

    monkeypatch.setattr(ProviderRegistry, "from_config", staticmethod(patched))


def _run(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workflow_id: str,
) -> tuple[Path, dict]:
    _install_mock_providers(base_config, monkeypatch)

    task = tmp_path / "task.md"
    task.write_text(
        "## Goal\n\nFix or refactor the thing.\n\n"
        "## Acceptance Criteria\n\n- works\n",
        encoding="utf-8",
    )
    repo = Path(base_config.project.root)
    ctx = create_run_context(repo, workflow=workflow_id, input_path=task)
    engine = WorkflowEngine(base_config, ctx)
    engine.run(workflow_id)

    state = StateStore(ctx.root)
    return ctx.root, state.load_run()


# ---------------------------------------------------------------------------
# bugfix
# ---------------------------------------------------------------------------

def test_bugfix_workflow_runs_end_to_end(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root, run_doc = _run(base_config, tmp_path, monkeypatch, "bugfix")
    assert run_doc["workflow"] == "bugfix"
    # The implementer's rendered prompt should carry the bugfix guidance.
    prompt = (run_root / "candidates" / "mock_impl" / "prompt.md").read_text(
        encoding="utf-8"
    )
    assert "Workflow variant: bugfix" in prompt
    assert "failing test first" in prompt


def test_bugfix_records_workflow_id_in_sqlite_index(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root, _ = _run(base_config, tmp_path, monkeypatch, "bugfix")
    from devforge.core.sqlite_index import SqliteIndex
    idx = SqliteIndex(Path(base_config.project.root) / ".orchestrator" / "state.db")
    runs = idx.list_runs(workflow="bugfix")
    assert any(r["run_id"] == run_root.name for r in runs)


# ---------------------------------------------------------------------------
# refactor
# ---------------------------------------------------------------------------

def test_refactor_workflow_runs_end_to_end(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root, run_doc = _run(base_config, tmp_path, monkeypatch, "refactor")
    assert run_doc["workflow"] == "refactor"
    prompt = (run_root / "candidates" / "mock_impl" / "prompt.md").read_text(
        encoding="utf-8"
    )
    assert "Workflow variant: refactor" in prompt
    assert "Preserve all observable behavior" in prompt


def test_refactor_records_workflow_id_in_sqlite_index(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root, _ = _run(base_config, tmp_path, monkeypatch, "refactor")
    from devforge.core.sqlite_index import SqliteIndex
    idx = SqliteIndex(Path(base_config.project.root) / ".orchestrator" / "state.db")
    runs = idx.list_runs(workflow="refactor")
    assert any(r["run_id"] == run_root.name for r in runs)


# ---------------------------------------------------------------------------
# feature default unchanged
# ---------------------------------------------------------------------------

def test_feature_prompt_has_no_variant_guidance(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root, _ = _run(base_config, tmp_path, monkeypatch, "feature")
    prompt = (run_root / "candidates" / "mock_impl" / "prompt.md").read_text(
        encoding="utf-8"
    )
    assert "Workflow variant:" not in prompt


# ---------------------------------------------------------------------------
# Unknown workflow still raises
# ---------------------------------------------------------------------------

def test_unknown_workflow_still_raises(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_mock_providers(base_config, monkeypatch)
    task = tmp_path / "task.md"
    task.write_text("# x\n", encoding="utf-8")
    repo = Path(base_config.project.root)
    ctx = create_run_context(repo, workflow="research_optimize", input_path=task)
    from devforge.core.workflow_engine import WorkflowLoadError
    engine = WorkflowEngine(base_config, ctx)
    with pytest.raises(WorkflowLoadError):
        engine.run("research_optimize")
