"""Anthropic Messages API provider adapter.

Replaces the placeholder that used to route ``claude_agent_sdk`` to
the Claude CLI adapter.

Scope: **text-only** roles — reviewer, judge, and any role where the
prompt is self-contained. The adapter wraps ``anthropic.Anthropic``'s
``messages.create`` shape. For implementer roles that need to write
code to disk, use the Claude CLI adapter — that drives a real agent
inside a worktree.

The ``anthropic`` package is an optional runtime dependency:

    pip install '.[providers-anthropic]'

The constructor raises :class:`ClaudeAgentSdkUnavailable` when the SDK
is missing; the registry catches it and disables the provider with a
friendly reason.
"""
from __future__ import annotations

import json
import os
from typing import Any

from devforge.core.config_loader import ProviderConfig
from devforge.providers.base import (
    FAILURE_AUTH_EXPIRED,
    FAILURE_MALFORMED_OUTPUT,
    FAILURE_RATE_LIMIT,
    FAILURE_TIMEOUT,
    FAILURE_UNKNOWN,
    AgentRequest,
    AgentResult,
)

_HEALTHCHECK_PING_ENV = "ANTHROPIC_HEALTHCHECK_PING"
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_MAX_TOKENS = 4096
_RESPONSE_BYTE_CAP = 200_000


class ClaudeAgentSdkUnavailable(RuntimeError):  # noqa: N818 — historical name kept stable
    """Raised when the optional ``anthropic`` SDK is not importable."""


def _load_anthropic_module() -> Any:
    """Try the standalone ``claude_agent_sdk`` package first, then ``anthropic``.

    The ``claude-agent-sdk`` package name shows up in upstream roadmaps but
    is not yet stable. ``anthropic`` is the canonical SDK and exposes the
    same ``Anthropic`` / ``messages.create`` shape we need.
    """
    try:
        import claude_agent_sdk as mod  # type: ignore[import-not-found]  # noqa: PLC0415

        return mod
    except ImportError:
        pass
    try:
        import anthropic  # noqa: PLC0415

        return anthropic
    except ImportError as exc:  # pragma: no cover - exercised only without extras
        raise ClaudeAgentSdkUnavailable(
            "The 'anthropic' package is required for the claude_agent_sdk "
            "provider. Install with: pip install '.[providers-anthropic]'"
        ) from exc


class ClaudeAgentSdkProvider:
    """Adapter for the Anthropic Messages API.

    Capabilities are text-only; the role router keeps this out of slots
    that require ``edit_files`` or ``run_shell``.
    """

    def __init__(self, provider_id: str, cfg: ProviderConfig) -> None:
        self.provider_id = provider_id
        self.cfg = cfg
        self._sdk = _load_anthropic_module()
        self._client: Any | None = None
        self._model = cfg.command or _DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def healthcheck(self) -> bool:
        for env in self.cfg.env_required or ["ANTHROPIC_API_KEY"]:
            if not os.environ.get(env):
                return False
        if os.environ.get(_HEALTHCHECK_PING_ENV) == "1":
            try:
                client = self._get_client()
                client.messages.create(
                    model=self._model,
                    max_tokens=1,
                    messages=[{"role": "user", "content": "ping"}],
                    timeout=10,
                )
            except Exception:  # noqa: BLE001 — healthcheck must not raise
                return False
        return True

    def supports(self, capability: str) -> bool:
        return capability in {
            "non_interactive",
            "json_output",
            "review_only",
            "read_repo",
        }

    def run(self, request: AgentRequest) -> AgentResult:
        try:
            client = self._get_client()
        except ClaudeAgentSdkUnavailable as exc:
            return AgentResult(
                provider_id=self.provider_id,
                role=request.role,
                success=False,
                error=str(exc),
                failure_class=FAILURE_UNKNOWN,
            )

        messages = [{"role": "user", "content": request.prompt}]
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=_DEFAULT_MAX_TOKENS,
                messages=messages,
                timeout=request.timeout_sec,
            )
        except Exception as exc:  # noqa: BLE001 — classify below
            return _from_exception(self.provider_id, request, exc, self._sdk)

        text = _extract_text(response)
        truncated = text[:_RESPONSE_BYTE_CAP]
        usage = _extract_usage(response)
        parsed: dict[str, Any] | None = None
        failure_class: str | None = None
        if request.expected_output == "json":
            try:
                parsed = json.loads(truncated)
            except json.JSONDecodeError:
                failure_class = FAILURE_MALFORMED_OUTPUT
        return AgentResult(
            provider_id=self.provider_id,
            role=request.role,
            success=failure_class is None,
            stdout=truncated,
            parsed_json=parsed,
            usage_hint=usage,
            exit_code=0,
            failure_class=failure_class,
            error=None if failure_class is None else "response was not valid JSON",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            cls = getattr(self._sdk, "Anthropic", None) or getattr(
                self._sdk, "Client", None
            )
            if cls is None:
                raise ClaudeAgentSdkUnavailable(
                    "Could not find Anthropic client class on the SDK module."
                )
            self._client = cls(api_key=api_key)
        return self._client


# ---------------------------------------------------------------------------
# Helpers (module-level for testability)
# ---------------------------------------------------------------------------

def _extract_text(response: Any) -> str:
    """Pull the assistant text from a Messages API response.

    The SDK returns a list of content blocks; for text-mode replies we
    concatenate every ``text`` block's body.
    """
    try:
        blocks = response.content
    except AttributeError:
        return ""
    if blocks is None:
        return ""
    out: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            out.append(text)
            continue
        # Defensive: some blocks are returned as dicts in tests.
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            out.append(block["text"])
    return "".join(out)


def _extract_usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump()
        except Exception:  # noqa: BLE001 — best-effort serialization
            pass
    if isinstance(usage, dict):
        return dict(usage)
    # Fallback: pluck the conventional keys.
    out: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            out[key] = value
    return out or None


def _from_exception(
    provider_id: str, request: AgentRequest, exc: Exception, sdk: Any
) -> AgentResult:
    """Map an anthropic SDK exception into the right failure class."""
    failure_class = FAILURE_UNKNOWN
    name = type(exc).__name__
    if name == "AuthenticationError":
        failure_class = FAILURE_AUTH_EXPIRED
    elif name == "RateLimitError":
        failure_class = FAILURE_RATE_LIMIT
    elif name in {"APITimeoutError", "APIConnectionError", "Timeout"}:
        failure_class = FAILURE_TIMEOUT
    elif _maybe_is(sdk, "AuthenticationError", exc):
        failure_class = FAILURE_AUTH_EXPIRED
    elif _maybe_is(sdk, "RateLimitError", exc):
        failure_class = FAILURE_RATE_LIMIT
    elif _maybe_is(sdk, "APITimeoutError", exc):
        failure_class = FAILURE_TIMEOUT
    return AgentResult(
        provider_id=provider_id,
        role=request.role,
        success=False,
        error=str(exc) or name,
        failure_class=failure_class,
    )


def _maybe_is(module: Any, attr: str, exc: Exception) -> bool:
    cls = getattr(module, attr, None)
    return isinstance(cls, type) and isinstance(exc, cls)
