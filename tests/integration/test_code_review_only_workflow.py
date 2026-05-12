"""Integration coverage for the code_review_only workflow.

Mocks the reviewer provider so the test stays deterministic without
hitting any LLM API. The driver builds the diff with real ``git`` from
the test repo, so most assertions exercise the actual flow end-to-end.
"""
from __future__ import annotations

import json
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
from devforge.stages.code_review_only_driver import (
    CodeReviewOnlyError,
    changed_files_from_diff,
    parse_diff_spec,
)

pytestmark = pytest.mark.integration


_REVIEW_PAYLOAD_PASS = json.dumps(
    {
        "verdict": "pass",
        "requirement_coverage": 1.0,
        "critical_issues": [],
        "major_issues": [],
        "minor_issues": [],
        "test_concerns": [],
        "security_concerns": [],
        "recommended_revision_prompt": "",
    }
)


def _install_review_mock(
    monkeypatch: pytest.MonkeyPatch, payload: str = _REVIEW_PAYLOAD_PASS
) -> None:
    def review_behavior(request):  # noqa: ARG001
        return AgentResult(
            provider_id="mock_review",
            role="reviewer",
            success=True,
            stdout=payload,
            exit_code=0,
        )

    def patched(_cfg: DevforgeConfig) -> ProviderRegistry:
        reg = ProviderRegistry()
        reg.register(MockProvider("mock_impl", behavior=lambda r: None))
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


# ---------------------------------------------------------------------------
# parse_diff_spec
# ---------------------------------------------------------------------------

def test_parse_diff_spec_defaults_to_working() -> None:
    assert parse_diff_spec(None).kind == "working"
    assert parse_diff_spec("").kind == "working"
    assert parse_diff_spec("working").kind == "working"


def test_parse_diff_spec_staged() -> None:
    assert parse_diff_spec("staged").kind == "staged"


def test_parse_diff_spec_ref_prefix() -> None:
    s = parse_diff_spec("ref:main..HEAD")
    assert s.kind == "ref" and s.spec.endswith("main..HEAD")


def test_parse_diff_spec_bare_ref_range() -> None:
    s = parse_diff_spec("main..HEAD")
    assert s.kind == "ref" and "main..HEAD" in s.spec


def test_parse_diff_spec_file() -> None:
    s = parse_diff_spec("file:patches/x.patch")
    assert s.kind == "file"


def test_parse_diff_spec_unknown_raises() -> None:
    with pytest.raises(CodeReviewOnlyError):
        parse_diff_spec("weird-spec")


# ---------------------------------------------------------------------------
# changed_files_from_diff
# ---------------------------------------------------------------------------

def test_changed_files_extracts_b_paths() -> None:
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/x b/y.py\n"
        "+++ b/y.py\n"
    )
    assert changed_files_from_diff(diff) == ["foo.py", "y.py"]


def test_changed_files_skips_dev_null() -> None:
    diff = "+++ /dev/null\n"
    assert changed_files_from_diff(diff) == []


# ---------------------------------------------------------------------------
# Driver — working tree diff
# ---------------------------------------------------------------------------

def test_review_working_tree_diff_runs_end_to_end(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(base_config.project.root)
    # Create an uncommitted change in the test repo.
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "hello.py").write_text("def hi(): return 'hi'\n", encoding="utf-8")

    _install_review_mock(monkeypatch)

    ctx = create_run_context(
        repo,
        workflow="code_review_only",
        input_path=None,
        extra_metadata={"diff_spec": "working"},
    )
    engine = WorkflowEngine(base_config, ctx)
    engine.run("code_review_only", reviewer_override="mock_review")

    # Diff captured in the candidate dir.
    cand = ctx.root / "candidates" / "review_target"
    assert (cand / "diff.patch").exists()
    diff = (cand / "diff.patch").read_text(encoding="utf-8")
    assert "hello.py" in diff

    # Reviewer + judge artifacts present.
    assert (cand / "review.json").exists()
    decision = json.loads((cand / "decision.json").read_text(encoding="utf-8"))
    assert decision["verdict"] in {"accept", "revise", "discard", "human_review", "keep_candidate_but_continue"}

    # Run-level decision file + final report.
    run_decision = json.loads((ctx.root / "decision.json").read_text(encoding="utf-8"))
    assert run_decision["chosen_candidate"] == "review_target"
    final = (ctx.root / "final_report.md").read_text(encoding="utf-8")
    assert "Code review" in final
    assert "hello.py" in final

    # State store rows.
    state = StateStore(ctx.root)
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    assert steps == {
        "collect_diff": "completed",
        "review": "completed",
        "judge": "completed",
        "final_report": "completed",
    }
    cands = state.load_candidates()
    assert any(c["candidate_id"] == "review_target" for c in cands)


# ---------------------------------------------------------------------------
# Driver — empty diff fails cleanly
# ---------------------------------------------------------------------------

def test_review_empty_diff_aborts(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No uncommitted changes in the fixture repo.
    repo = Path(base_config.project.root)
    _install_review_mock(monkeypatch)

    ctx = create_run_context(
        repo,
        workflow="code_review_only",
        input_path=None,
        extra_metadata={"diff_spec": "working"},
    )
    engine = WorkflowEngine(base_config, ctx)
    engine.run("code_review_only", reviewer_override="mock_review")

    assert (ctx.root / "failure.json").exists()
    state = StateStore(ctx.root)
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    assert steps["collect_diff"] == "skipped"
    assert steps["review"] == "pending"


# ---------------------------------------------------------------------------
# Driver — patch file source
# ---------------------------------------------------------------------------

def test_review_from_patch_file(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(base_config.project.root)
    patch_path = tmp_path / "diff.patch"
    patch_path.write_text(
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n",
        encoding="utf-8",
    )
    _install_review_mock(monkeypatch)
    ctx = create_run_context(
        repo,
        workflow="code_review_only",
        input_path=None,
        extra_metadata={"diff_spec": f"file:{patch_path}"},
    )
    engine = WorkflowEngine(base_config, ctx)
    engine.run("code_review_only", reviewer_override="mock_review")
    cand = ctx.root / "candidates" / "review_target"
    assert "x.py" in (cand / "diff.patch").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Driver — ref range diff
# ---------------------------------------------------------------------------

def test_review_from_ref_range(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(base_config.project.root)
    # Add and commit a new file so we have a non-trivial ref range.
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "ranged.py").write_text("VAL = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add ranged"], cwd=repo,
        check=True, capture_output=True,
    )
    _install_review_mock(monkeypatch)
    ctx = create_run_context(
        repo,
        workflow="code_review_only",
        input_path=None,
        extra_metadata={"diff_spec": "ref:HEAD~1..HEAD"},
    )
    engine = WorkflowEngine(base_config, ctx)
    engine.run("code_review_only", reviewer_override="mock_review")
    cand = ctx.root / "candidates" / "review_target"
    assert "ranged.py" in (cand / "diff.patch").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SQLite index records the workflow
# ---------------------------------------------------------------------------

def test_code_review_only_recorded_in_sqlite(
    base_config: DevforgeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(base_config.project.root)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "indexed.py").write_text("X = 1\n", encoding="utf-8")
    _install_review_mock(monkeypatch)
    ctx = create_run_context(
        repo,
        workflow="code_review_only",
        input_path=None,
        extra_metadata={"diff_spec": "working"},
    )
    engine = WorkflowEngine(base_config, ctx)
    engine.run("code_review_only", reviewer_override="mock_review")

    from devforge.core.sqlite_index import SqliteIndex
    idx = SqliteIndex(repo / ".orchestrator" / "state.db")
    runs = idx.list_runs(workflow="code_review_only")
    assert any(r["run_id"] == ctx.run_id for r in runs)
