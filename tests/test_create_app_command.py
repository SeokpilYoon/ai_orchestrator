"""CliRunner coverage for `devforge create-app`."""
from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from devforge.cli import app

runner = CliRunner()

_PRD = """# Product

Tiny service.

## Functional requirements

- Add a task (must)
  - POST /tasks returns 201
- List tasks (should)
"""


def _write_config(repo: Path) -> Path:
    cfg = repo / "devforge.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"project": {"name": repo.name, "root": str(repo), "default_branch": "main"}}
        ),
        encoding="utf-8",
    )
    return cfg


def test_create_app_happy_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    prd = tmp_path / "prd.md"
    prd.write_text(_PRD, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "create-app",
            "--from",
            str(prd),
            "--stack",
            "python-fastapi-only",
            "--config",
            str(cfg),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Workflow artifacts written" in result.output

    runs = list((repo / ".orchestrator" / "runs").iterdir())
    assert len(runs) == 1
    run_root = runs[0]
    for name in (
        "product_summary.md",
        "requirements.json",
        "mvp_scope.md",
        "scaffold_manifest.json",
    ):
        assert (run_root / name).exists()


def test_create_app_rejects_missing_prd(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    result = runner.invoke(
        app,
        [
            "create-app",
            "--from",
            str(tmp_path / "nonexistent.md"),
            "--config",
            str(cfg),
        ],
    )
    # Typer's `exists=True` produces a usage error.
    assert result.exit_code != 0


def test_create_app_with_empty_prd_records_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    prd = tmp_path / "empty.md"
    prd.write_text("", encoding="utf-8")

    result = runner.invoke(
        app,
        ["create-app", "--from", str(prd), "--config", str(cfg)],
    )
    # Driver writes failure.json and returns normally — engine sees no exception
    # so the CLI exits 0. The failure is visible in the artifacts.
    assert result.exit_code == 0, result.output
    runs = list((repo / ".orchestrator" / "runs").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "failure.json").exists()
