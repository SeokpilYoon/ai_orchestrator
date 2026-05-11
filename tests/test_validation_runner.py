from __future__ import annotations

from pathlib import Path

from devforge.core.config_loader import ValidationCommands, ValidationConfig
from devforge.evaluators.validation_runner import run_validation


def test_validation_passes(tmp_path: Path) -> None:
    cfg = ValidationConfig(
        commands=ValidationCommands(test="true", lint="true"),
        default_timeout_sec=5,
    )
    report = run_validation(tmp_path, cfg)
    assert report.all_passed
    assert report.results["test"].passed
    assert report.results["lint"].passed


def test_validation_fails(tmp_path: Path) -> None:
    cfg = ValidationConfig(commands=ValidationCommands(test="false"), default_timeout_sec=5)
    report = run_validation(tmp_path, cfg)
    assert not report.all_passed
    assert report.results["test"].exit_code != 0


def test_validation_timeout(tmp_path: Path) -> None:
    cfg = ValidationConfig(commands=ValidationCommands(test="sleep 3"), default_timeout_sec=1)
    report = run_validation(tmp_path, cfg)
    assert report.results["test"].timed_out
