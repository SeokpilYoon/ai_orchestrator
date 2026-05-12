"""Provider registry.

Authoritative reference: docs/plan/02 §5.4, docs/plan/03 DEVF-021.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from devforge.core.config_loader import DevforgeConfig, ProviderConfig
from devforge.providers.base import AgentProvider

if TYPE_CHECKING:  # pragma: no cover
    pass


PROVIDER_STATUS = (
    "available",
    "unavailable_auth",
    "unavailable_usage_limit",
    "unavailable_command_missing",
    "unavailable_timeout",
    "disabled_by_policy",
    "disabled",
)


@dataclass
class ProviderStatusRow:
    name: str
    status: str
    detail: str


def _build_provider(provider_id: str, cfg: ProviderConfig) -> AgentProvider:
    """Factory — instantiate the right provider class from ``cfg.type``."""
    if cfg.type == "codex_cli":
        from devforge.providers.codex_cli import CodexCliProvider

        return CodexCliProvider(provider_id, cfg)
    if cfg.type == "claude_cli":
        from devforge.providers.claude_cli import ClaudeCliProvider

        return ClaudeCliProvider(provider_id, cfg)
    if cfg.type == "claude_agent_sdk":
        from devforge.providers.claude_agent_sdk import (
            ClaudeAgentSdkProvider,
            ClaudeAgentSdkUnavailable,
        )

        try:
            return ClaudeAgentSdkProvider(provider_id, cfg)
        except ClaudeAgentSdkUnavailable as exc:
            raise ValueError(str(exc)) from exc
    if cfg.type == "openai_api":
        from devforge.providers.openai_api import (
            OpenAiApiProvider,
            OpenAiApiUnavailable,
        )

        try:
            return OpenAiApiProvider(provider_id, cfg)
        except OpenAiApiUnavailable as exc:
            raise ValueError(str(exc)) from exc
    if cfg.type == "local_rule_based":
        from devforge.providers.local_rule_based import LocalRuleBasedProvider

        return LocalRuleBasedProvider(provider_id, cfg)
    if cfg.type == "mock":
        from devforge.providers.mock import MockProvider

        return MockProvider(provider_id)
    raise ValueError(f"Unknown provider type: {cfg.type}")


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AgentProvider] = {}
        self._configs: dict[str, ProviderConfig] = {}
        self._disabled: dict[str, str] = {}  # provider_id -> reason

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, cfg: DevforgeConfig) -> ProviderRegistry:
        reg = cls()
        for pid, pcfg in cfg.providers.items():
            if not pcfg.enabled:
                reg._disabled[pid] = "disabled in config"
                continue
            try:
                reg._providers[pid] = _build_provider(pid, pcfg)
                reg._configs[pid] = pcfg
            except ValueError as exc:
                reg._disabled[pid] = str(exc)
        return reg

    # ------------------------------------------------------------------
    # Manual registration (tests)
    # ------------------------------------------------------------------

    def register(self, provider: AgentProvider, cfg: ProviderConfig | None = None) -> None:
        self._providers[provider.provider_id] = provider
        if cfg is not None:
            self._configs[provider.provider_id] = cfg

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, provider_id: str) -> AgentProvider | None:
        return self._providers.get(provider_id)

    def ids(self) -> list[str]:
        return list(self._providers.keys())

    def healthcheck(self, provider_id: str) -> tuple[str, str]:
        """Return ``(status, detail)`` for one provider."""
        if provider_id in self._disabled:
            return ("disabled", self._disabled[provider_id])
        provider = self._providers.get(provider_id)
        if provider is None:
            return ("disabled", "not registered")
        cfg = self._configs.get(provider_id)

        if cfg is not None:
            missing = [env for env in cfg.env_required if not os.environ.get(env)]
            if missing:
                return ("disabled_by_policy", f"missing env: {','.join(missing)}")

        try:
            ok = provider.healthcheck()
        except Exception as exc:  # pragma: no cover - defensive
            return ("unavailable_timeout", f"healthcheck error: {exc}")

        if ok:
            detail = f"auth={cfg.auth}" if cfg else "auth=none"
            return ("available", detail)
        return ("unavailable_command_missing", "healthcheck failed")

    def status_rows(self) -> list[ProviderStatusRow]:
        rows: list[ProviderStatusRow] = []
        seen: set[str] = set()
        for pid in self._providers:
            status, detail = self.healthcheck(pid)
            rows.append(ProviderStatusRow(name=pid, status=status, detail=detail))
            seen.add(pid)
        for pid, reason in self._disabled.items():
            if pid in seen:
                continue
            rows.append(ProviderStatusRow(name=pid, status="disabled", detail=reason))
        rows.sort(key=lambda r: r.name)
        return rows
