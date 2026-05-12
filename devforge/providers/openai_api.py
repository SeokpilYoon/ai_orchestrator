"""OpenAI Chat Completions API provider adapter.

Replaces the placeholder that used to route ``openai_api`` to the Codex
CLI adapter (see commit history for the prior shim).

Scope: **text-only** roles — reviewer, judge, and any role where the
prompt is self-contained. The adapter does not modify files on disk
(no ``edit_files`` / ``run_shell`` capability). For implementer roles
that need to write code, use the Codex CLI or Claude CLI adapters
instead — those run a real agent inside a worktree.

The ``openai`` package is an optional runtime dependency:

    pip install '.[providers-openai]'

The constructor raises :class:`OpenAiApiUnavailable` when the SDK is
missing. The registry catches the import failure and disables the
provider with a friendly reason rather than crashing.
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

_HEALTHCHECK_PING_ENV = "OPENAI_HEALTHCHECK_PING"
_DEFAULT_MODEL = "gpt-4o-mini"
_RESPONSE_BYTE_CAP = 200_000  # truncate stdout so candidate dirs stay sane


class OpenAiApiUnavailable(RuntimeError):  # noqa: N818 — historical name kept stable
    """Raised when the optional ``openai`` SDK is not importable."""


def _load_openai_module() -> Any:
    try:
        import openai  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without extras
        raise OpenAiApiUnavailable(
            "The 'openai' package is required for the openai_api provider. "
            "Install with: pip install '.[providers-openai]'"
        ) from exc
    return openai


class OpenAiApiProvider:
    """Adapter for the OpenAI Chat Completions API.

    Capabilities are limited to text I/O — the role router will keep this
    out of slots that require ``edit_files`` or ``run_shell``.
    """

    def __init__(self, provider_id: str, cfg: ProviderConfig) -> None:
        self.provider_id = provider_id
        self.cfg = cfg
        self._openai = _load_openai_module()
        # Lazy-construct the client on first run() so import-time failures
        # surface from healthcheck, not from registry construction.
        self._client: Any | None = None
        self._model = cfg.command or _DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def healthcheck(self) -> bool:
        # Required env vars take precedence — the registry already covers
        # this generically, but we mirror the check so a provider used
        # outside the registry still does the right thing.
        for env in self.cfg.env_required or ["OPENAI_API_KEY"]:
            if not os.environ.get(env):
                return False
        if os.environ.get(_HEALTHCHECK_PING_ENV) == "1":
            # Opt-in: actually hit the API. Keeps `devforge providers
            # status` offline-friendly by default.
            try:
                client = self._get_client()
                client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
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
        except OpenAiApiUnavailable as exc:
            return AgentResult(
                provider_id=self.provider_id,
                role=request.role,
                success=False,
                error=str(exc),
                failure_class=FAILURE_UNKNOWN,
            )

        messages = [{"role": "user", "content": request.prompt}]
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                timeout=request.timeout_sec,
            )
        except Exception as exc:  # noqa: BLE001 — classify below
            return _from_exception(self.provider_id, request, exc, self._openai)

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
            api_key = os.environ.get("OPENAI_API_KEY")
            self._client = self._openai.OpenAI(api_key=api_key)
        return self._client


# ---------------------------------------------------------------------------
# Helpers (module-level for testability)
# ---------------------------------------------------------------------------

def _extract_text(response: Any) -> str:
    """Pull the assistant message from a Chat Completions response."""
    try:
        choice = response.choices[0]
        message = choice.message
        # The SDK exposes content as a string for plain assistant replies.
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        # Defensive: some response shapes return a list of parts.
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")
                for part in content
            )
    except (AttributeError, IndexError, TypeError):
        return ""
    return ""


def _extract_usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    # Pydantic-style model: prefer model_dump, fall back to dict shape.
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump()
        except Exception:  # noqa: BLE001 — best-effort serialization
            pass
    if isinstance(usage, dict):
        return dict(usage)
    return None


def _from_exception(
    provider_id: str, request: AgentRequest, exc: Exception, openai_module: Any
) -> AgentResult:
    """Map an openai SDK exception into the right failure class."""
    failure_class = FAILURE_UNKNOWN
    name = type(exc).__name__
    # Use string-based class names so we don't hard-couple to the SDK's
    # internal hierarchy (which has shifted between minor releases).
    if name == "AuthenticationError":
        failure_class = FAILURE_AUTH_EXPIRED
    elif name == "RateLimitError":
        failure_class = FAILURE_RATE_LIMIT
    elif name in {"APITimeoutError", "Timeout"}:
        failure_class = FAILURE_TIMEOUT
    elif _maybe_is(openai_module, "AuthenticationError", exc):
        failure_class = FAILURE_AUTH_EXPIRED
    elif _maybe_is(openai_module, "RateLimitError", exc):
        failure_class = FAILURE_RATE_LIMIT
    elif _maybe_is(openai_module, "APITimeoutError", exc):
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
