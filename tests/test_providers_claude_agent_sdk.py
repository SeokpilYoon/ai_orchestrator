"""Tests for the Anthropic Messages provider adapter.

All tests inject a fake ``anthropic`` module via monkeypatch — no
network or real SDK is required.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from devforge.core.config_loader import ProviderConfig
from devforge.providers.base import (
    FAILURE_AUTH_EXPIRED,
    FAILURE_MALFORMED_OUTPUT,
    FAILURE_RATE_LIMIT,
    FAILURE_TIMEOUT,
    FAILURE_UNKNOWN,
    AgentRequest,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str, usage: dict | None = None) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = usage


class _FakeMessages:
    def __init__(self, *, text: str = "", exc: Exception | None = None) -> None:
        self._text = text
        self._exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(
            self._text, usage={"input_tokens": 5, "output_tokens": 11}
        )


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str = "hello",
    exc: Exception | None = None,
    auth_error_cls: type[Exception] | None = None,
    rate_error_cls: type[Exception] | None = None,
    timeout_error_cls: type[Exception] | None = None,
) -> _FakeMessages:
    messages = _FakeMessages(text=text, exc=exc)
    module = types.ModuleType("anthropic")
    module.Anthropic = lambda **_kwargs: _FakeClient(messages)
    if auth_error_cls:
        module.AuthenticationError = auth_error_cls
    if rate_error_cls:
        module.RateLimitError = rate_error_cls
    if timeout_error_cls:
        module.APITimeoutError = timeout_error_cls
    # Ensure claude_agent_sdk fallback doesn't accidentally win.
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return messages


def _request(*, role: str = "reviewer", expected_output: str = "text") -> AgentRequest:
    return AgentRequest(
        role=role,  # type: ignore[arg-type]
        prompt="hello?",
        cwd=Path("/tmp"),
        run_id="r1",
        timeout_sec=30,
        expected_output=expected_output,  # type: ignore[arg-type]
        allow_edit=False,
        allow_shell=False,
    )


def _provider(monkeypatch: pytest.MonkeyPatch):
    # Force a fresh import so the fake module wins for the lazy loader.
    monkeypatch.delitem(sys.modules, "devforge.providers.claude_agent_sdk", raising=False)
    from devforge.providers.claude_agent_sdk import ClaudeAgentSdkProvider
    return ClaudeAgentSdkProvider(
        "claude_agent_sdk", ProviderConfig(type="claude_agent_sdk")
    )


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------

def test_healthcheck_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = _provider(monkeypatch)
    assert p.healthcheck() is False


def test_healthcheck_passes_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    p = _provider(monkeypatch)
    assert p.healthcheck() is True


def test_healthcheck_ping_calls_api(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_HEALTHCHECK_PING", "1")
    p = _provider(monkeypatch)
    assert p.healthcheck() is True
    assert messages.calls and messages.calls[0]["max_tokens"] == 1


def test_healthcheck_ping_failure_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch, exc=RuntimeError("boom"))
    monkeypatch.setenv("ANTHROPIC_HEALTHCHECK_PING", "1")
    p = _provider(monkeypatch)
    assert p.healthcheck() is False


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_supports_text_only_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    p = _provider(monkeypatch)
    assert p.supports("review_only") is True
    assert p.supports("json_output") is True
    assert p.supports("read_repo") is True
    assert p.supports("non_interactive") is True
    assert p.supports("edit_files") is False
    assert p.supports("run_shell") is False


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def test_run_text_returns_assistant_content(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch, text="thanks")
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.success is True
    assert result.stdout == "thanks"
    assert result.usage_hint == {"input_tokens": 5, "output_tokens": 11}


def test_run_json_populates_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"verdict": "pass"}
    _install_fake_anthropic(monkeypatch, text=json.dumps(payload))
    p = _provider(monkeypatch)
    result = p.run(_request(expected_output="json"))
    assert result.success is True
    assert result.parsed_json == payload


def test_run_json_malformed_marks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch, text="not-json")
    p = _provider(monkeypatch)
    result = p.run(_request(expected_output="json"))
    assert result.success is False
    assert result.failure_class == FAILURE_MALFORMED_OUTPUT


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

class _AuthErr(Exception):  # noqa: N818 — local fake mirroring SDK shape
    pass


class _RateErr(Exception):  # noqa: N818
    pass


class _TimeoutErr(Exception):  # noqa: N818
    pass


def test_auth_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(
        monkeypatch, exc=_AuthErr("nope"), auth_error_cls=_AuthErr
    )
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.failure_class == FAILURE_AUTH_EXPIRED


def test_rate_limit_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(
        monkeypatch, exc=_RateErr("slow"), rate_error_cls=_RateErr
    )
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.failure_class == FAILURE_RATE_LIMIT


def test_timeout_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(
        monkeypatch, exc=_TimeoutErr("late"), timeout_error_cls=_TimeoutErr
    )
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.failure_class == FAILURE_TIMEOUT


def test_unknown_error_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch, exc=RuntimeError("???"))
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.failure_class == FAILURE_UNKNOWN


# ---------------------------------------------------------------------------
# Module resolution: prefer claude_agent_sdk over anthropic when both available
# ---------------------------------------------------------------------------

def test_prefers_claude_agent_sdk_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    # Install both modules and confirm the preferred one is used.
    preferred_messages = _FakeMessages(text="preferred")
    preferred = types.ModuleType("claude_agent_sdk")
    preferred.Anthropic = lambda **_kwargs: _FakeClient(preferred_messages)

    fallback_messages = _FakeMessages(text="fallback")
    fallback = types.ModuleType("anthropic")
    fallback.Anthropic = lambda **_kwargs: _FakeClient(fallback_messages)

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", preferred)
    monkeypatch.setitem(sys.modules, "anthropic", fallback)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delitem(sys.modules, "devforge.providers.claude_agent_sdk", raising=False)
    from devforge.providers.claude_agent_sdk import ClaudeAgentSdkProvider
    p = ClaudeAgentSdkProvider(
        "claude_agent_sdk", ProviderConfig(type="claude_agent_sdk")
    )
    result = p.run(_request())
    assert result.stdout == "preferred"


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_registry_picks_up_claude_agent_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    from devforge.core.config_loader import (
        DevforgeConfig,
        ProjectConfig,
    )
    from devforge.providers.registry import ProviderRegistry

    cfg = DevforgeConfig(
        project=ProjectConfig(name="t", root="."),
        providers={
            "claude_agent_sdk": ProviderConfig(
                type="claude_agent_sdk", enabled=True
            ),
        },
    )
    reg = ProviderRegistry.from_config(cfg)
    provider = reg.get("claude_agent_sdk")
    assert provider is not None
    assert type(provider).__name__ == "ClaudeAgentSdkProvider"


def test_registry_disables_when_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    monkeypatch.delitem(sys.modules, "devforge.providers.claude_agent_sdk", raising=False)

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"anthropic", "claude_agent_sdk"}:
            raise ImportError(f"{name} not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from devforge.core.config_loader import (
        DevforgeConfig,
        ProjectConfig,
    )
    from devforge.providers.registry import ProviderRegistry

    cfg = DevforgeConfig(
        project=ProjectConfig(name="t", root="."),
        providers={
            "claude_agent_sdk": ProviderConfig(
                type="claude_agent_sdk", enabled=True
            ),
        },
    )
    reg = ProviderRegistry.from_config(cfg)
    assert reg.get("claude_agent_sdk") is None
    status, detail = reg.healthcheck("claude_agent_sdk")
    assert status == "disabled"
    assert "anthropic" in detail.lower()
