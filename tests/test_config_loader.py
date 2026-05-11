from __future__ import annotations

from pathlib import Path

import pytest

from devforge.core.config_loader import ConfigError, load_config

VALID_YAML = """
project:
  name: my-app
  root: "."
  default_branch: main
providers:
  mock1:
    type: mock
    enabled: true
roles:
  implementer:
    provider_order: ["mock1"]
"""


def test_load_valid_config(tmp_path: Path) -> None:
    p = tmp_path / "devforge.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.project.name == "my-app"
    assert "mock1" in cfg.providers
    assert cfg.roles["implementer"].provider_order == ["mock1"]


def test_missing_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "devforge.yaml"
    p.write_text("project: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_schema_violation(tmp_path: Path) -> None:
    p = tmp_path / "devforge.yaml"
    p.write_text("providers: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_defaults_filled(tmp_path: Path) -> None:
    p = tmp_path / "devforge.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    cfg = load_config(p)
    # defaults from ScoringConfig / StopConditions kick in
    assert cfg.scoring.build_pass == 25
    assert cfg.stop_conditions.accept_when.min_score == 85
