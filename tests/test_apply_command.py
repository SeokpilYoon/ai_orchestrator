"""Tests for `devforge apply`."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from typer.testing import CliRunner

from devforge.cli import app

runner = CliRunner()


def _write_config(repo: Path) -> Path:
    """Write devforge.yaml and commit it to main so it survives branch switches."""
    cfg = repo / "devforge.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "project": {
                    "name": repo.name,
                    "root": str(repo),
                    "default_branch": "main",
                }
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "devforge.yaml"], cwd=str(repo), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "add devforge.yaml"], cwd=str(repo), check=True,
                   capture_output=True)
    return cfg


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _make_branch_with_change(repo: Path, branch: str, file_rel: str, content: str) -> None:
    subprocess.run(
        ["git", "checkout", "-b", branch], cwd=str(repo), check=True, capture_output=True
    )
    (repo / file_rel).write_text(content, encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", f"{branch} change", cwd=repo)
    _git("checkout", "main", cwd=repo)


def test_apply_happy_path(tmp_repo: Path) -> None:
    cfg = _write_config(tmp_repo)
    _make_branch_with_change(tmp_repo, "agent/r1-cand", "new.txt", "hello\n")

    result = runner.invoke(
        app, ["apply", "--run", "r1", "--candidate", "cand", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert "Merged agent/r1-cand" in result.output
    assert (tmp_repo / "new.txt").exists()


def test_apply_refuses_dirty_tree(tmp_repo: Path) -> None:
    cfg = _write_config(tmp_repo)
    _make_branch_with_change(tmp_repo, "agent/r1-cand", "new.txt", "x\n")
    # Dirty: untracked file
    (tmp_repo / "dirty.txt").write_text("uncommitted", encoding="utf-8")

    result = runner.invoke(
        app, ["apply", "--run", "r1", "--candidate", "cand", "--config", str(cfg)]
    )
    assert result.exit_code == 2
    assert "dirty" in result.output.lower()


def test_apply_unknown_branch(tmp_repo: Path) -> None:
    cfg = _write_config(tmp_repo)
    result = runner.invoke(
        app,
        ["apply", "--run", "nope", "--candidate", "ghost", "--config", str(cfg)],
    )
    assert result.exit_code == 2
    assert "does not exist" in result.output.lower()


def test_apply_aborts_on_conflict(tmp_repo: Path) -> None:
    cfg = _write_config(tmp_repo)
    # Both main and the candidate branch modify README.md → conflict.
    _make_branch_with_change(tmp_repo, "agent/r1-cand", "README.md", "candidate\n")
    (tmp_repo / "README.md").write_text("main side\n", encoding="utf-8")
    _git("add", "README.md", cwd=tmp_repo)
    _git("commit", "-m", "main change", cwd=tmp_repo)

    result = runner.invoke(
        app, ["apply", "--run", "r1", "--candidate", "cand", "--config", str(cfg)]
    )
    assert result.exit_code == 3
    # Working tree should be clean again because we ran `git merge --abort`.
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(tmp_repo),
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""
