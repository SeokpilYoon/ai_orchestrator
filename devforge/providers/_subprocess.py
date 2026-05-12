"""Shared subprocess helper for CLI providers.

Captures stdout/stderr, enforces timeout, collects ``git diff --name-only`` from cwd.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from devforge.providers.base import (
    FAILURE_COMMAND_MISSING,
    FAILURE_TIMEOUT,
    FAILURE_UNKNOWN,
    AgentRequest,
    AgentResult,
    AgentRole,
)


def _to_str(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _collect_changed_files(cwd: Path) -> list[str]:
    """Collect changed files via ``git diff --name-only HEAD`` (best-effort)."""
    if not (cwd / ".git").exists() and not _inside_worktree(cwd):
        return []
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.SubprocessError):
        return []


def _inside_worktree(cwd: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def run_cli(
    provider_id: str,
    role: AgentRole,
    cmd: list[str],
    request: AgentRequest,
    *,
    classify_failure=None,
) -> AgentResult:
    """Run ``cmd`` as a subprocess and wrap the result in :class:`AgentResult`."""
    binary = cmd[0]
    if shutil.which(binary) is None:
        return AgentResult(
            provider_id=provider_id,
            role=role,
            success=False,
            exit_code=127,
            error=f"command not found: {binary}",
            failure_class=FAILURE_COMMAND_MISSING,
        )

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(request.cwd),
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # ``text=True`` makes stdout/stderr ``str``, but TimeoutExpired exposes
        # them as ``bytes | str | None`` — normalise so downstream sees ``str``.
        return AgentResult(
            provider_id=provider_id,
            role=role,
            success=False,
            exit_code=124,
            stdout=_to_str(exc.stdout),
            stderr=_to_str(exc.stderr),
            error=f"timeout after {request.timeout_sec}s",
            failure_class=FAILURE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return AgentResult(
            provider_id=provider_id,
            role=role,
            success=False,
            exit_code=1,
            error=str(exc),
            failure_class=FAILURE_UNKNOWN,
        )

    failure_class = None
    if proc.returncode != 0 and classify_failure is not None:
        failure_class = classify_failure(proc.returncode, proc.stdout, proc.stderr)

    return AgentResult(
        provider_id=provider_id,
        role=role,
        success=proc.returncode == 0,
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        changed_files=_collect_changed_files(request.cwd),
        error=None if proc.returncode == 0 else (proc.stderr or "").strip()[:500] or "non-zero exit",
        failure_class=failure_class,
    )
