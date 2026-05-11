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


# ---------------------------------------------------------------------------
# DEVF-050 capability filtering
# ---------------------------------------------------------------------------

def _cfg_with_local_rule_at_implementer() -> DevforgeConfig:
    return DevforgeConfig(
        project=ProjectConfig(name="x", root="."),
        providers={
            "mock1": ProviderConfig(type="mock", enabled=True),
            "lrb": ProviderConfig(type="local_rule_based", enabled=True),
        },
        roles={
            # Misconfigured intentionally: local_rule_based at implementer.
            "implementer": RoleConfig(provider_order=["mock1", "lrb"]),
            "reviewer": RoleConfig(provider_order=["mock1"]),
            "judge": RoleConfig(provider_order=["lrb", "mock1"]),
        },
    )


def test_implementer_excludes_provider_missing_edit_files() -> None:
    cfg = _cfg_with_local_rule_at_implementer()
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("implementer")
    assert d.selected == ["mock1"], d.selected
    assert "lrb" in d.excluded
    assert "missing capabilities" in d.excluded["lrb"]
    assert "edit_files" in d.excluded["lrb"]


def test_reviewer_excludes_provider_missing_read_repo() -> None:
    """LocalRuleBased lacks read_repo + run_shell — reviewer default should drop it."""
    cfg = DevforgeConfig(
        project=ProjectConfig(name="x", root="."),
        providers={
            "lrb": ProviderConfig(type="local_rule_based", enabled=True),
            "mock_ok": ProviderConfig(type="mock", enabled=True),
        },
        roles={"reviewer": RoleConfig(provider_order=["lrb", "mock_ok"])},
    )
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("reviewer")
    assert d.selected == ["mock_ok"]
    assert "missing capabilities" in d.excluded["lrb"]


def test_judge_default_passes_local_rule_based() -> None:
    cfg = _cfg_with_local_rule_at_implementer()
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("judge")
    # judge needs 'deterministic' — lrb has it, mock has it too via default capabilities.
    assert "lrb" in d.selected


def test_explicit_required_capabilities_override_defaults() -> None:
    """Override forces a capability the otherwise-fine mock provider lacks."""
    from devforge.providers.base import AgentRequest
    from devforge.providers.mock import MockProvider

    cfg = DevforgeConfig(
        project=ProjectConfig(name="x", root="."),
        providers={
            "limited": ProviderConfig(type="mock", enabled=True),
        },
        roles={
            "implementer": RoleConfig(
                provider_order=["limited"],
                required_capabilities=["only_i_have_this"],
            )
        },
    )
    reg = ProviderRegistry.from_config(cfg)
    # Replace the auto-built MockProvider with one that has a constrained set.
    reg._providers["limited"] = MockProvider(  # type: ignore[attr-defined]
        "limited", capabilities={"read_repo", "edit_files"}
    )
    router = RoleRouter(cfg, reg)
    d = router.select("implementer")
    assert d.selected == []
    assert "limited" in d.excluded
    assert "only_i_have_this" in d.excluded["limited"]
    # Sanity: the request type is importable (defensive — keeps lint happy).
    _ = AgentRequest


def test_override_blocked_when_provider_misses_capability() -> None:
    cfg = _cfg_with_local_rule_at_implementer()
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("implementer", override="lrb")
    assert d.selected == []
    assert "missing capabilities" in d.excluded["lrb"]


def test_default_caps_for_unknown_role_is_empty() -> None:
    """A role with no default + no override gates nothing."""
    cfg = DevforgeConfig(
        project=ProjectConfig(name="x", root="."),
        providers={"lrb": ProviderConfig(type="local_rule_based", enabled=True)},
        roles={"custom_role": RoleConfig(provider_order=["lrb"])},
    )
    reg = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, reg)
    d = router.select("custom_role")
    # No default caps → no filter → lrb is fine.
    assert d.selected == ["lrb"]
