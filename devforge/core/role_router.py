"""Role router — pick providers for a role based on config + healthcheck.

Authoritative reference: docs/plan/02 §5.3, docs/plan/03 DEVF-050.
"""
from __future__ import annotations

from dataclasses import dataclass

from devforge.core.config_loader import DevforgeConfig, RoleConfig
from devforge.providers.registry import ProviderRegistry


@dataclass
class RouteDecision:
    role: str
    selected: list[str]            # ordered provider ids
    mode: str                      # "single" | "tournament"
    excluded: dict[str, str]       # provider_id -> reason


class RoleRouter:
    def __init__(self, cfg: DevforgeConfig, registry: ProviderRegistry) -> None:
        self._cfg = cfg
        self._registry = registry

    def select(
        self,
        role: str,
        *,
        override: str | None = None,
        avoid_provider: str | None = None,
    ) -> RouteDecision:
        if override:
            return self._validate_override(role, override)

        role_cfg: RoleConfig | None = self._cfg.roles.get(role)
        if role_cfg is None:
            return RouteDecision(role=role, selected=[], mode="single", excluded={})

        excluded: dict[str, str] = {}
        candidates: list[str] = []
        for pid in role_cfg.provider_order:
            if pid not in self._registry.ids():
                excluded[pid] = "not registered"
                continue
            if avoid_provider and pid == avoid_provider and role_cfg.avoid_same_provider_as_implementer:
                excluded[pid] = f"avoided (same as implementer {avoid_provider})"
                continue
            status, detail = self._registry.healthcheck(pid)
            if status != "available":
                excluded[pid] = f"{status}: {detail}"
                continue
            candidates.append(pid)

        mode = "tournament" if (role_cfg.tournament and len(candidates) >= 2) else "single"
        # ``selected`` is the full ordered list of healthy providers in either mode.
        # In single mode the caller treats it as a fallback chain (try in order);
        # in tournament mode each provider runs as a separate candidate.
        return RouteDecision(role=role, selected=candidates, mode=mode, excluded=excluded)

    def _validate_override(self, role: str, override: str) -> RouteDecision:
        excluded: dict[str, str] = {}
        if override not in self._registry.ids():
            return RouteDecision(role=role, selected=[], mode="single",
                                 excluded={override: "not registered"})
        status, detail = self._registry.healthcheck(override)
        if status != "available":
            excluded[override] = f"{status}: {detail}"
            return RouteDecision(role=role, selected=[], mode="single", excluded=excluded)
        return RouteDecision(role=role, selected=[override], mode="single", excluded={})
