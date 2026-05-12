"""Validation runner — execute project-profile validation commands.

Authoritative reference: docs/plan/02 §5.9, docs/plan/03 DEVF-032.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.core.config_loader import ValidationConfig


@dataclass
class CommandResult:
    name: str
    command: str
    passed: bool
    exit_code: int
    duration_sec: float
    output_tail: str = ""
    timed_out: bool = False


@dataclass
class ValidationReport:
    cwd: str
    results: dict[str, CommandResult] = field(default_factory=dict)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results.values())

    def to_dict(self) -> dict[str, object]:
        return {
            "cwd": self.cwd,
            "all_passed": self.all_passed,
            "results": {k: asdict(v) for k, v in self.results.items()},
        }


# The order matters: we want fast checks first so failures surface quickly.
_COMMAND_ORDER = (
    "import_smoke",
    "install_check",
    "lint",
    "typecheck",
    "compile",
    "build",
    "test",
    "editmode_tests",
    "api_tests",
    "compose_config",
    "healthcheck",
)


def run_validation(cwd: Path, cfg: ValidationConfig) -> ValidationReport:
    """Execute every configured command in ``cwd`` and report results."""
    report = ValidationReport(cwd=str(cwd))
    commands = cfg.commands.model_dump()
    timeout = cfg.default_timeout_sec
    for name in _COMMAND_ORDER:
        cmd_str = commands.get(name)
        if not cmd_str:
            continue
        report.results[name] = _run_one(name, cmd_str, cwd, timeout)
    return report


def _run_one(name: str, command: str, cwd: Path, timeout_sec: int) -> CommandResult:
    import time

    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # ``text=True`` makes stdout/stderr ``str``, but TimeoutExpired exposes
        # them as ``bytes | str | None`` — normalise so we always concat strings.
        stdout_str = _to_str(exc.stdout)
        stderr_str = _to_str(exc.stderr)
        return CommandResult(
            name=name,
            command=command,
            passed=False,
            exit_code=124,
            duration_sec=round(time.monotonic() - started, 2),
            output_tail=stdout_str[-500:] + stderr_str[-500:],
            timed_out=True,
        )
    except (FileNotFoundError, OSError) as exc:
        return CommandResult(
            name=name,
            command=command,
            passed=False,
            exit_code=127,
            duration_sec=round(time.monotonic() - started, 2),
            output_tail=str(exc),
        )

    blob = (proc.stdout or "") + (proc.stderr or "")
    return CommandResult(
        name=name,
        command=command,
        passed=proc.returncode == 0,
        exit_code=proc.returncode,
        duration_sec=round(time.monotonic() - started, 2),
        output_tail=blob[-500:],
    )


def save_validation_report(report: ValidationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def _to_str(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


# Silence unused-import warnings on shlex (kept for future quoting needs).
_ = shlex
