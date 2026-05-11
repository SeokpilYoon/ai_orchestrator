from __future__ import annotations

from pathlib import Path

import pytest

from devforge.core.config_loader import ProviderConfig
from devforge.providers.base import AgentRequest
from devforge.providers.claude_cli import ClaudeCliProvider
from devforge.providers.codex_cli import CodexCliProvider
from devforge.providers.local_rule_based import LocalRuleBasedProvider


def test_codex_forbidden_flag_rejected() -> None:
    cfg = ProviderConfig(type="codex_cli", default_args=["exec", "--danger-full-access"])
    with pytest.raises(ValueError):
        CodexCliProvider("p", cfg)


def test_claude_forbidden_flag_rejected() -> None:
    cfg = ProviderConfig(type="claude_cli", default_args=["--dangerously-skip-permissions"])
    with pytest.raises(ValueError):
        ClaudeCliProvider("p", cfg)


def test_codex_healthcheck_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    cfg = ProviderConfig(type="codex_cli", command="codex-not-installed-xyz")
    p = CodexCliProvider("codex_sub", cfg)
    assert p.healthcheck() is False


def test_claude_healthcheck_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    cfg = ProviderConfig(type="claude_cli", command="claude-not-installed-xyz")
    p = ClaudeCliProvider("claude_sub", cfg)
    assert p.healthcheck() is False


def test_codex_run_missing_command(tmp_path: Path) -> None:
    cfg = ProviderConfig(type="codex_cli", command="codex-not-installed-xyz")
    p = CodexCliProvider("codex_sub", cfg)
    res = p.run(
        AgentRequest(role="implementer", prompt="x", cwd=tmp_path, run_id="r1", timeout_sec=5)
    )
    assert not res.success
    assert res.failure_class == "command_missing"


def test_local_rule_based_always_healthy() -> None:
    p = LocalRuleBasedProvider("rule", ProviderConfig(type="local_rule_based"))
    assert p.healthcheck() is True
    res = p.run(AgentRequest(role="judge", prompt="x", cwd=Path("."), run_id="r1"))
    assert res.success
    assert res.parsed_json is not None
