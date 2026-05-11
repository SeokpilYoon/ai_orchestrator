from __future__ import annotations

import subprocess
from pathlib import Path

from devforge.stages.repo_context_collector import (
    collect_repo_context,
    render_repo_context_md,
    save_repo_context,
)


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def test_collect_minimal(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "README.md").write_text("hi", encoding="utf-8")
    ctx = collect_repo_context(repo)
    assert ctx.repo_name == "r"
    assert "src/" in ctx.top_level_entries
    assert "README.md" in ctx.top_level_entries
    assert ctx.git_status == ""  # not a git repo
    assert ctx.test_commands == []


def test_collect_pyproject_infers_pytest(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    ctx = collect_repo_context(repo)
    assert "pyproject.toml" in ctx.package_metadata
    assert "pytest -q" in ctx.test_commands


def test_collect_package_json_scripts(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "package.json").write_text(
        '{"name": "x", "scripts": {"test": "jest", "lint": "eslint .", "build": "tsc"}}',
        encoding="utf-8",
    )
    ctx = collect_repo_context(repo)
    assert "npm test" in ctx.test_commands
    assert "npm run lint" in ctx.test_commands
    assert "npm run build" in ctx.test_commands


def test_git_status_short(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git_init(repo)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    ctx = collect_repo_context(repo)
    assert "a.txt" in ctx.git_status


def test_relevant_files_confirmed_first(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    ctx = collect_repo_context(repo, likely_files=["src/x.py", "src/ghost.py"])
    assert "src/x.py" in ctx.relevant_files
    assert "src/ghost.py" in ctx.relevant_files
    assert ctx.relevant_files.index("src/x.py") < ctx.relevant_files.index("src/ghost.py")


def test_relevant_files_glob(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("A=1\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("B=1\n", encoding="utf-8")
    ctx = collect_repo_context(repo, likely_files=["src/*.py"])
    assert "src/a.py" in ctx.relevant_files
    assert "src/b.py" in ctx.relevant_files


def test_render_md_has_all_sections(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    ctx = collect_repo_context(repo)
    md = render_repo_context_md(ctx)
    for header in ("Top-level entries", "Git status", "Inferred test commands", "Relevant files"):
        assert header in md


def test_save_creates_files(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    ctx = collect_repo_context(repo)
    md = tmp_path / "out.md"
    js = tmp_path / "out.json"
    save_repo_context(ctx, md, js)
    assert md.exists()
    assert js.exists()


def test_missing_repo_safe(tmp_path: Path) -> None:
    ctx = collect_repo_context(tmp_path / "does-not-exist")
    assert ctx.repo_name == "does-not-exist"
    assert ctx.top_level_entries == []
