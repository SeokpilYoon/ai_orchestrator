"""Tests for the OpenAI Chat Completions provider adapter.

All tests inject a fake ``openai`` module via monkeypatch — no network
or real SDK is required. If the real ``openai`` package is installed
the lazy import inside the adapter is bypassed by the fake provided
through ``sys.modules``.
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

class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str, usage: dict | None = None) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, *, content: str = "", exc: Exception | None = None) -> None:
        self._content = content
        self._exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._content, usage={"prompt_tokens": 3, "completion_tokens": 7})


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = _FakeChat(completions)


def _install_fake_openai(
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str = "hello",
    exc: Exception | None = None,
    auth_error_cls: type[Exception] | None = None,
    rate_error_cls: type[Exception] | None = None,
    timeout_error_cls: type[Exception] | None = None,
) -> _FakeCompletions:
    completions = _FakeCompletions(content=content, exc=exc)
    module = types.ModuleType("openai")
    module.OpenAI = lambda **_kwargs: _FakeClient(completions)
    if auth_error_cls:
        module.AuthenticationError = auth_error_cls
    if rate_error_cls:
        module.RateLimitError = rate_error_cls
    if timeout_error_cls:
        module.APITimeoutError = timeout_error_cls
    monkeypatch.setitem(sys.modules, "openai", module)
    # Force the adapter to re-import the (faked) module on next construction.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return completions


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
    from devforge.providers.openai_api import OpenAiApiProvider
    return OpenAiApiProvider("openai_api", ProviderConfig(type="openai_api"))


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------

def test_healthcheck_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = _provider(monkeypatch)
    assert p.healthcheck() is False


def test_healthcheck_passes_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch)
    p = _provider(monkeypatch)
    assert p.healthcheck() is True


def test_healthcheck_ping_actually_calls_api(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_HEALTHCHECK_PING", "1")
    p = _provider(monkeypatch)
    assert p.healthcheck() is True
    assert completions.calls and completions.calls[0]["max_tokens"] == 1


def test_healthcheck_ping_failure_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch, exc=RuntimeError("boom"))
    monkeypatch.setenv("OPENAI_HEALTHCHECK_PING", "1")
    p = _provider(monkeypatch)
    assert p.healthcheck() is False


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_supports_text_only_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch)
    p = _provider(monkeypatch)
    assert p.supports("review_only") is True
    assert p.supports("json_output") is True
    assert p.supports("read_repo") is True
    assert p.supports("non_interactive") is True
    # Crucially NOT in the set — keeps the router from picking it for
    # implementer slots that need real file edits.
    assert p.supports("edit_files") is False
    assert p.supports("run_shell") is False


# ---------------------------------------------------------------------------
# run() — happy paths
# ---------------------------------------------------------------------------

def test_run_text_returns_assistant_content(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch, content="quack")
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.success is True
    assert result.stdout == "quack"
    assert result.parsed_json is None
    assert result.usage_hint == {"prompt_tokens": 3, "completion_tokens": 7}
    assert result.failure_class is None


def test_run_json_populates_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"verdict": "pass", "critical_issues": []}
    _install_fake_openai(monkeypatch, content=json.dumps(payload))
    p = _provider(monkeypatch)
    result = p.run(_request(expected_output="json"))
    assert result.success is True
    assert result.parsed_json == payload


def test_run_json_malformed_marks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch, content="not-json")
    p = _provider(monkeypatch)
    result = p.run(_request(expected_output="json"))
    assert result.success is False
    assert result.failure_class == FAILURE_MALFORMED_OUTPUT


# ---------------------------------------------------------------------------
# run() — failure classification
# ---------------------------------------------------------------------------

class _AuthErr(Exception):  # noqa: N818 — local fake mirroring SDK shape
    pass


class _RateErr(Exception):  # noqa: N818
    pass


class _TimeoutErr(Exception):  # noqa: N818
    pass


def test_auth_error_maps_to_failure_class(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(
        monkeypatch, exc=_AuthErr("nope"), auth_error_cls=_AuthErr
    )
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.failure_class == FAILURE_AUTH_EXPIRED


def test_rate_limit_maps_to_failure_class(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(
        monkeypatch, exc=_RateErr("slow"), rate_error_cls=_RateErr
    )
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.failure_class == FAILURE_RATE_LIMIT


def test_timeout_maps_to_failure_class(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(
        monkeypatch, exc=_TimeoutErr("late"), timeout_error_cls=_TimeoutErr
    )
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.failure_class == FAILURE_TIMEOUT


def test_unknown_error_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch, exc=RuntimeError("???"))
    p = _provider(monkeypatch)
    result = p.run(_request())
    assert result.failure_class == FAILURE_UNKNOWN


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_registry_picks_up_openai_api_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch)
    from devforge.core.config_loader import (
        DevforgeConfig,
        ProjectConfig,
    )
    from devforge.providers.registry import ProviderRegistry

    cfg = DevforgeConfig(
        project=ProjectConfig(name="t", root="."),
        providers={
            "openai_api": ProviderConfig(type="openai_api", enabled=True),
        },
    )
    reg = ProviderRegistry.from_config(cfg)
    provider = reg.get("openai_api")
    assert provider is not None
    # The provider class name (not just the id) confirms the real adapter
    # is used instead of the old Codex CLI fallback.
    assert type(provider).__name__ == "OpenAiApiProvider"


def test_registry_disables_when_openai_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the SDK not being installed by removing it from sys.modules
    # AND making the import raise. We use a stub module that raises on
    # attribute access so the lazy import inside the adapter trips.
    monkeypatch.delitem(sys.modules, "openai", raising=False)
    monkeypatch.delitem(sys.modules, "devforge.providers.openai_api", raising=False)

    # Block the import.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("openai not installed")
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
            "openai_api": ProviderConfig(type="openai_api", enabled=True),
        },
    )
    reg = ProviderRegistry.from_config(cfg)
    # Provider absent from the live registry; status row reports disabled.
    assert reg.get("openai_api") is None
    status, detail = reg.healthcheck("openai_api")
    assert status == "disabled"
    assert "openai" in detail.lower()
