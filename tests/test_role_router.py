from __future__ import annotations

from devforge.core.config_loader import (
    DevforgeConfig,
    ProjectConfig,
    ProviderConfig,
    RoleConfig,
)
from devforge.core.role_router import RoleRouter
from devforge.providers.registry import ProviderRegistry


def _cfg() -> DevforgeConfig:
    return DevforgeConfig(
        project=ProjectConfig(name="x", root="."),
        providers={
            "a": ProviderConfig(type="mock", enabled=True),
            "b": ProviderConfig(type="mock", enabled=True),
        },
        roles={
            "implementer": RoleConfig(provider_order=["a", "b"], tournament=False),
            "reviewer": RoleConfig(
                provider_order=["a", "b"],
                avoid_same_provider_as_implementer=True,
            ),
        },
    )


def test_select_returns_full_fallback_chain() -> None:
    cfg = _cfg()
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("implementer")
    # Both providers healthy → full ordered fallback chain in single mode.
    assert d.selected == ["a", "b"]
    assert d.mode == "single"


def test_reviewer_avoids_implementer() -> None:
    cfg = _cfg()
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("reviewer", avoid_provider="a")
    assert d.selected == ["b"]
    assert "a" in d.excluded


def test_override_unknown_provider() -> None:
    cfg = _cfg()
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("implementer", override="ghost")
    assert d.selected == []
    assert "ghost" in d.excluded


def test_tournament_selects_multiple() -> None:
    cfg = _cfg()
    cfg.roles["implementer"].tournament = True
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("implementer")
    assert d.mode == "tournament"
    assert set(d.selected) == {"a", "b"}
