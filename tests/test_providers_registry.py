from __future__ import annotations

from devforge.core.config_loader import (
    DevforgeConfig,
    ProjectConfig,
    ProviderConfig,
    RoleConfig,
)
from devforge.providers.registry import ProviderRegistry


def test_registry_from_config() -> None:
    cfg = DevforgeConfig(
        project=ProjectConfig(name="x", root="."),
        providers={
            "m1": ProviderConfig(type="mock", enabled=True),
            "m2": ProviderConfig(type="mock", enabled=False),
            "rule": ProviderConfig(type="local_rule_based", enabled=True),
        },
        roles={"implementer": RoleConfig(provider_order=["m1"])},
    )
    reg = ProviderRegistry.from_config(cfg)
    assert "m1" in reg.ids()
    assert "m2" not in reg.ids()
    assert "rule" in reg.ids()


def test_status_rows_includes_disabled() -> None:
    cfg = DevforgeConfig(
        project=ProjectConfig(name="x", root="."),
        providers={
            "m1": ProviderConfig(type="mock", enabled=True),
            "m2": ProviderConfig(type="mock", enabled=False),
        },
    )
    reg = ProviderRegistry.from_config(cfg)
    rows = reg.status_rows()
    by_name = {r.name: r for r in rows}
    assert by_name["m1"].status == "available"
    assert by_name["m2"].status == "disabled"


def test_env_required_marks_disabled_by_policy(monkeypatch) -> None:
    cfg = DevforgeConfig(
        project=ProjectConfig(name="x", root="."),
        providers={
            "needs_key": ProviderConfig(
                type="local_rule_based",
                enabled=True,
                env_required=["DEVFORGE_TEST_REQUIRED_KEY"],
            ),
        },
    )
    monkeypatch.delenv("DEVFORGE_TEST_REQUIRED_KEY", raising=False)
    reg = ProviderRegistry.from_config(cfg)
    rows = {r.name: r for r in reg.status_rows()}
    assert rows["needs_key"].status == "disabled_by_policy"
