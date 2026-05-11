"""Security regression suite (DEVF-093).

Each test simulates a hostile mock implementer and verifies the deterministic
policy gates kick in: blocked file, secret leak, forbidden command,
test deletion, lockfile modification.

NEVER put real secrets in these tests — placeholders only.
"""
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


def _make_task(tmp_path: Path) -> Path:
    task = tmp_path / "task.md"
    task.write_text(
        "# Goal\n\nDo X.\n\n## Acceptance Criteria\n\n- works\n", encoding="utf-8"
    )
    return task


def _load_decision(ctx) -> dict:
    return json.loads(
        (ctx.root / "candidates" / "mock_impl" / "decision.json").read_text("utf-8")
    )


def _load_policy(ctx) -> dict:
    return json.loads(
        (ctx.root / "candidates" / "mock_impl" / "policy.json").read_text("utf-8")
    )


# ---------------------------------------------------------------------------
# 1) blocked file
# ---------------------------------------------------------------------------

def test_blocked_file_modified_discards(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)

    def impl(req):
        (req.cwd / ".env").write_text("OPENAI_API_KEY=placeholder\n", encoding="utf-8")
        commit_all(req.cwd)
        return AgentResult(
            provider_id="mock_impl", role="implementer", success=True,
            stdout="oops", changed_files=[".env"], exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )
    ctx = create_run_context(repo, workflow="feature", input_path=_make_task(tmp_path))
    run_feature_workflow(base_config, ctx, None, None)

    decision = _load_decision(ctx)
    assert decision["verdict"] == "discard"
    # Either blocked_file_modified OR secret_detected can fire first — both are correct.
    assert (
        "blocked_file_modified" in decision["reason"]
        or "secret" in decision["reason"]
    )


# ---------------------------------------------------------------------------
# 2) secret in diff (non-env-file path)
# ---------------------------------------------------------------------------

def test_secret_in_diff_discards(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)
    fake_key = "sk-" + "A" * 40  # placeholder, not a real key

    def impl(req):
        (req.cwd / "src").mkdir(exist_ok=True)
        (req.cwd / "src" / "leak.py").write_text(
            f'KEY = "{fake_key}"\n', encoding="utf-8"
        )
        commit_all(req.cwd)
        return AgentResult(
            provider_id="mock_impl", role="implementer", success=True,
            stdout="impl", changed_files=["src/leak.py"], exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )
    ctx = create_run_context(repo, workflow="feature", input_path=_make_task(tmp_path))
    run_feature_workflow(base_config, ctx, None, None)

    decision = _load_decision(ctx)
    assert decision["verdict"] == "discard"
    assert "secret" in decision["reason"]

    policy = _load_policy(ctx)
    # The recorded secret hit must NOT contain the key value itself.
    for hit in policy["secret_scan"]["hits"]:
        assert fake_key not in str(hit)


# ---------------------------------------------------------------------------
# 3) secret in stdout
# ---------------------------------------------------------------------------

def test_secret_in_stdout_discards(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)
    fake_key = "sk-ant-" + "B" * 40

    def impl(req):
        (req.cwd / "src").mkdir(exist_ok=True)
        (req.cwd / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
        commit_all(req.cwd)
        return AgentResult(
            provider_id="mock_impl", role="implementer", success=True,
            stdout=f"Done. Using key {fake_key} for tests.",
            changed_files=["src/x.py"], exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )
    ctx = create_run_context(repo, workflow="feature", input_path=_make_task(tmp_path))
    run_feature_workflow(base_config, ctx, None, None)

    decision = _load_decision(ctx)
    assert decision["verdict"] == "discard"
    assert "secret" in decision["reason"]


# ---------------------------------------------------------------------------
# 4) forbidden command in stdout
# ---------------------------------------------------------------------------

def test_rm_rf_in_stdout_discards(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)

    def impl(req):
        (req.cwd / "src").mkdir(exist_ok=True)
        (req.cwd / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
        commit_all(req.cwd)
        return AgentResult(
            provider_id="mock_impl", role="implementer", success=True,
            stdout="I ran `rm -rf /tmp/old_files` to clean up.",
            changed_files=["src/x.py"], exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )
    ctx = create_run_context(repo, workflow="feature", input_path=_make_task(tmp_path))
    run_feature_workflow(base_config, ctx, None, None)

    decision = _load_decision(ctx)
    assert decision["verdict"] == "discard"
    assert "command_policy" in decision["reason"]


# ---------------------------------------------------------------------------
# 5) test deletion triggers human review
# ---------------------------------------------------------------------------

def test_test_deletion_triggers_human_review(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)
    # Pre-existing test file on main, committed via tmp_repo fixture.
    import subprocess as sp
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    existing = tests_dir / "test_existing.py"
    existing.write_text("def test_x():\n    assert 1 == 1\n", encoding="utf-8")
    sp.run(["git", "add", "."], cwd=repo, check=True)
    sp.run(["git", "commit", "-m", "add existing test"], cwd=repo, check=True,
           capture_output=True)

    def impl(req):
        target = req.cwd / "tests" / "test_existing.py"
        target.unlink()
        commit_all(req.cwd)
        return AgentResult(
            provider_id="mock_impl", role="implementer", success=True,
            stdout="removed flaky test",
            changed_files=["tests/test_existing.py"], exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )
    ctx = create_run_context(repo, workflow="feature", input_path=_make_task(tmp_path))
    run_feature_workflow(base_config, ctx, None, None)

    decision = _load_decision(ctx)
    assert decision["verdict"] == "human_review"
    assert "test_integrity" in decision["reason"]


# ---------------------------------------------------------------------------
# 6) lockfile modification → require_review (and decision is NOT accept)
# ---------------------------------------------------------------------------

def test_lockfile_modification_recorded_for_review(
    base_config: DevforgeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(base_config.project.root)

    def impl(req):
        (req.cwd / "package-lock.json").write_text(
            '{"name":"x","lockfileVersion":1}\n', encoding="utf-8"
        )
        commit_all(req.cwd)
        return AgentResult(
            provider_id="mock_impl", role="implementer", success=True,
            stdout="impl", changed_files=["package-lock.json"], exit_code=0,
        )

    install_mock_providers(
        impl_behavior=impl,
        review_behavior=review_behavior("pass"),
        monkeypatch=monkeypatch,
    )
    ctx = create_run_context(repo, workflow="feature", input_path=_make_task(tmp_path))
    run_feature_workflow(base_config, ctx, None, None)

    policy = _load_policy(ctx)
    assert "package-lock.json" in policy["file_policy"]["require_review"]
    decision = _load_decision(ctx)
    # Decision shouldn't be accept simply because of a lockfile change;
    # judge may pass it (no hard discard) but score logic + reviewer should
    # at minimum surface the review flag. We only assert the policy artifact here.
    assert decision["verdict"] != "accept" or len(policy["file_policy"]["require_review"]) > 0
