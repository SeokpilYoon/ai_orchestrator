"""Diff collector — extract patch/changed_files/diff_stat from a worktree.

Authoritative reference: docs/plan/02 §5.8, docs/plan/03 DEVF-031.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DiffArtifact:
    patch_path: Path
    changed_files_path: Path
    diff_stat_path: Path
    changed_files: list[str]


def collect_diff(
    worktree_path: Path,
    base_branch: str,
    output_dir: Path,
) -> DiffArtifact:
    """Run git diff vs ``base_branch`` and save patch / changed_files / stat."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rev = f"{base_branch}...HEAD"

    patch = _git(["diff", rev], cwd=worktree_path)
    names = _git(["diff", "--name-only", rev], cwd=worktree_path)
    stat = _git(["diff", "--stat", rev], cwd=worktree_path)

    patch_path = output_dir / "diff.patch"
    names_path = output_dir / "changed_files.txt"
    stat_path = output_dir / "diff_stat.txt"

    patch_path.write_text(patch, encoding="utf-8")
    names_path.write_text(names, encoding="utf-8")
    stat_path.write_text(stat, encoding="utf-8")

    changed = [line.strip() for line in names.splitlines() if line.strip()]
    return DiffArtifact(
        patch_path=patch_path,
        changed_files_path=names_path,
        diff_stat_path=stat_path,
        changed_files=changed,
    )


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout
