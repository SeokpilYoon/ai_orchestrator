"""DEVF-095 — version surface tests.

Three sources of truth must agree on the version string:
  1. ``devforge.__version__`` (the package itself)
  2. ``pyproject.toml`` (the build metadata)
  3. ``importlib.metadata.version("devforge")`` (the installed distribution)

The CLI exposes both ``devforge version`` (subcommand) and ``devforge --version``
(root option). These tests guarantee they all agree.
"""
from __future__ import annotations

import tomllib
from importlib.metadata import version as pkg_version
from pathlib import Path

from typer.testing import CliRunner

import devforge
from devforge.cli import app

runner = CliRunner()


def test_version_subcommand_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    assert devforge.__version__ in result.output


def test_version_root_option_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert devforge.__version__ in result.output


def test_version_subcommand_and_root_option_match() -> None:
    sub = runner.invoke(app, ["version"]).output.strip()
    root = runner.invoke(app, ["--version"]).output.strip()
    assert sub == root == devforge.__version__


def test_pyproject_version_matches_package() -> None:
    """``pyproject.toml:project.version`` must mirror ``devforge.__version__``."""
    pyproject_path = (
        Path(__file__).resolve().parent.parent / "pyproject.toml"
    )
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    assert data["project"]["version"] == devforge.__version__


def test_installed_metadata_matches_package() -> None:
    """Editable install metadata must mirror ``devforge.__version__``."""
    assert pkg_version("devforge") == devforge.__version__


def test_help_exposes_core_commands() -> None:
    """The root --help must continue to list every public command."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "version",
        "init",
        "run",
        "report",
        "apply",
        "cleanup",
        "providers",
        "create-app",
    ):
        assert cmd in result.output, f"missing command in help: {cmd}"
