"""Role router — pick providers for a role based on config + healthcheck + capabilities.

Authoritative reference: docs/plan/02 §5.3, docs/plan/03 DEVF-050.
"""
from __future__ import annotations

from dataclasses import dataclass

from devforge.core.config_loader import DevforgeConfig, RoleConfig
from devforge.providers.registry import ProviderRegistry

# Default capabilities each role needs from its provider. Users may override
# per-role in ``devforge.yaml`` via ``roles.<name>.required_capabilities``.
# Empty default = no capability gate for that role.
_DEFAULT_ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "implementer": frozenset({"edit_files", "run_shell", "non_interactive"}),
    "reviewer": frozenset({"read_repo", "json_output", "non_interactive"}),
    "qa_engineer": frozenset({"read_repo", "run_shell"}),
    "security_reviewer": frozenset({"read_repo"}),
    "judge": frozenset({"deterministic"}),
    "product_manager": frozenset({"non_interactive"}),
    "system_architect": frozenset({"non_interactive"}),
    "technical_planner": frozenset({"non_interactive"}),
    "release_manager": frozenset({"non_interactive"}),
}


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

        required_caps = self._effective_capabilities(role, role_cfg)
        excluded: dict[str, str] = {}
        candidates: list[str] = []
        for pid in role_cfg.provider_order:
            if pid not in self._registry.ids():
                excluded[pid] = "not registered"
                continue
            if (
                avoid_provider
                and pid == avoid_provider
                and role_cfg.avoid_same_provider_as_implementer
            ):
                excluded[pid] = f"avoided (same as implementer {avoid_provider})"
                continue
            status, detail = self._registry.healthcheck(pid)
            if status != "available":
                excluded[pid] = f"{status}: {detail}"
                continue
            missing = self._missing_capabilities(pid, required_caps)
            if missing:
                excluded[pid] = f"missing capabilities: {','.join(missing)}"
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
            return RouteDecision(
                role=role,
                selected=[],
                mode="single",
                excluded={override: "not registered"},
            )
        status, detail = self._registry.healthcheck(override)
        if status != "available":
            excluded[override] = f"{status}: {detail}"
            return RouteDecision(role=role, selected=[], mode="single", excluded=excluded)
        role_cfg = self._cfg.roles.get(role)
        required_caps = (
            self._effective_capabilities(role, role_cfg)
            if role_cfg is not None
            else _DEFAULT_ROLE_CAPABILITIES.get(role, frozenset())
        )
        missing = self._missing_capabilities(override, required_caps)
        if missing:
            excluded[override] = f"missing capabilities: {','.join(missing)}"
            return RouteDecision(role=role, selected=[], mode="single", excluded=excluded)
        return RouteDecision(role=role, selected=[override], mode="single", excluded={})

    # ------------------------------------------------------------------
    # Capability helpers
    # ------------------------------------------------------------------

    def _effective_capabilities(self, role: str, role_cfg: RoleConfig) -> frozenset[str]:
        if role_cfg.required_capabilities:
            return frozenset(role_cfg.required_capabilities)
        return _DEFAULT_ROLE_CAPABILITIES.get(role, frozenset())

    def _missing_capabilities(self, pid: str, required: frozenset[str]) -> list[str]:
        if not required:
            return []
        provider = self._registry.get(pid)
        if provider is None:
            return sorted(required)
        return sorted(cap for cap in required if not provider.supports(cap))
