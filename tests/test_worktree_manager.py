from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devforge.git.worktree_manager import WorktreeError, WorktreeManager


def test_create_and_cleanup(tmp_repo: Path) -> None:
    mgr = WorktreeManager(repo_root=tmp_repo)
    wt = mgr.create(run_id="r1", candidate_id="cand", base_branch="main")
    assert wt.path.exists()
    (wt.path / "new.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=wt.path, check=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=wt.path, check=True, capture_output=True)

    patch = mgr.export_patch(wt, tmp_repo.parent / "out.patch")
    assert "new.txt" in patch.read_text()

    mgr.cleanup(wt)
    assert not wt.path.exists()


def test_not_a_git_repo(tmp_path: Path) -> None:
    mgr = WorktreeManager(repo_root=tmp_path)
    with pytest.raises(WorktreeError):
        mgr.create(run_id="r1", candidate_id="cand", base_branch="main")


def test_cleanup_run_removes_multiple(tmp_repo: Path) -> None:
    mgr = WorktreeManager(repo_root=tmp_repo)
    mgr.create(run_id="r1", candidate_id="a", base_branch="main")
    mgr.create(run_id="r1", candidate_id="b", base_branch="main")
    removed = mgr.cleanup_run("r1")
    assert len(removed) == 2
