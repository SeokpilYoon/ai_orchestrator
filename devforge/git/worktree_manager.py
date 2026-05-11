"""Git worktree manager — isolates each candidate in its own worktree.

Authoritative reference: docs/plan/02 §5.8, docs/plan/03 DEVF-030.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""


@dataclass
class Worktree:
    path: Path
    branch: str
    base_branch: str
    repo_root: Path


class WorktreeManager:
    """Wraps the subset of ``git worktree`` we need.

    The repo at ``repo_root`` must already be a git repository. Worktrees are
    created as siblings under ``worktree_root`` (defaults to ``<repo>/.orchestrator/worktrees``).
    """

    def __init__(self, repo_root: Path, worktree_root: Path | None = None) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.worktree_root = (
            Path(worktree_root).resolve()
            if worktree_root is not None
            else self.repo_root / ".orchestrator" / "worktrees"
        )
        self.worktree_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_git_repo(self) -> bool:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return proc.returncode == 0 and proc.stdout.strip() == "true"
        except (FileNotFoundError, subprocess.SubprocessError):
            return False

    def create(self, run_id: str, candidate_id: str, base_branch: str = "main") -> Worktree:
        if not self.is_git_repo():
            raise WorktreeError(
                f"{self.repo_root} is not a git repository — initialize it before running devforge."
            )
        if not self._branch_exists(base_branch):
            raise WorktreeError(f"base branch '{base_branch}' does not exist")

        branch = f"agent/{run_id}-{candidate_id}"
        path = self.worktree_root / f"{run_id}-{candidate_id}"

        if path.exists():
            raise WorktreeError(f"worktree path already exists: {path}")
        if self._branch_exists(branch):
            raise WorktreeError(f"branch already exists: {branch}")

        self._run_git(
            ["worktree", "add", str(path), "-b", branch, base_branch],
            cwd=self.repo_root,
            error_prefix="git worktree add",
        )
        return Worktree(path=path, branch=branch, base_branch=base_branch, repo_root=self.repo_root)

    def export_patch(self, worktree: Worktree, output_path: Path) -> Path:
        """Write a diff of all changes (vs base branch) to ``output_path``."""
        proc = subprocess.run(
            ["git", "diff", f"{worktree.base_branch}...HEAD"],
            cwd=str(worktree.path),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise WorktreeError(f"git diff failed: {proc.stderr.strip()}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(proc.stdout, encoding="utf-8")
        return output_path

    def cleanup(self, worktree: Worktree, *, delete_branch: bool = True) -> None:
        """Remove a single worktree and optionally delete its branch."""
        # ``--force`` because we may have uncommitted candidate output.
        self._run_git(
            ["worktree", "remove", "--force", str(worktree.path)],
            cwd=self.repo_root,
            error_prefix="git worktree remove",
            check=False,
        )
        if worktree.path.exists():
            shutil.rmtree(worktree.path, ignore_errors=True)
        if delete_branch:
            self._run_git(
                ["branch", "-D", worktree.branch],
                cwd=self.repo_root,
                error_prefix="git branch -D",
                check=False,
            )

    def cleanup_run(self, run_id: str) -> list[Path]:
        """Remove all worktrees whose path starts with ``<run_id>-``."""
        removed: list[Path] = []
        for entry in self.worktree_root.iterdir():
            if not entry.is_dir() or not entry.name.startswith(f"{run_id}-"):
                continue
            candidate = entry.name[len(run_id) + 1 :]
            branch = f"agent/{run_id}-{candidate}"
            self._run_git(
                ["worktree", "remove", "--force", str(entry)],
                cwd=self.repo_root,
                error_prefix="git worktree remove",
                check=False,
            )
            if entry.exists():
                shutil.rmtree(entry, ignore_errors=True)
            self._run_git(
                ["branch", "-D", branch],
                cwd=self.repo_root,
                error_prefix="git branch -D",
                check=False,
            )
            removed.append(entry)
        return removed

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _branch_exists(self, branch: str) -> bool:
        proc = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    def _run_git(
        self,
        args: list[str],
        *,
        cwd: Path,
        error_prefix: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise WorktreeError(f"{error_prefix} failed: {proc.stderr.strip()}")
        return proc
