"""Codex CLI provider adapter.

Authoritative reference: docs/plan/02 §5.6, docs/plan/03 DEVF-022.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from devforge.core.config_loader import ProviderConfig
from devforge.providers._subprocess import run_cli
from devforge.providers.base import (
    FAILURE_AUTH_EXPIRED,
    FAILURE_POLICY_VIOLATION,
    FAILURE_RATE_LIMIT,
    FAILURE_UNKNOWN,
    FAILURE_USAGE_LIMIT,
    AgentRequest,
    AgentResult,
)

_FORBIDDEN_FLAGS = ("danger-full-access", "--yolo", "--bypass-approvals", "--bypass-sandbox")


class CodexCliProvider:
    """Adapter for the ``codex`` CLI in non-interactive (`exec`) mode."""

    def __init__(self, provider_id: str, cfg: ProviderConfig) -> None:
        self.provider_id = provider_id
        self.cfg = cfg
        self._command = cfg.command or "codex"
        for flag in cfg.default_args:
            for forbidden in _FORBIDDEN_FLAGS:
                if forbidden in flag:
                    raise ValueError(
                        f"Provider {provider_id}: forbidden flag '{flag}' is not allowed."
                    )

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def healthcheck(self) -> bool:
        if shutil.which(self._command) is None:
            return False
        if self.cfg.env_required:
            for env in self.cfg.env_required:
                if not os.environ.get(env):
                    return False
        try:
            proc = subprocess.run(
                [self._command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return proc.returncode == 0
        except (FileNotFoundError, subprocess.SubprocessError):
            return False

    def supports(self, capability: str) -> bool:
        return capability in {
            "read_repo",
            "edit_files",
            "run_shell",
            "non_interactive",
            "json_output",
        }

    def run(self, request: AgentRequest) -> AgentResult:
        cmd = self._build_command(request)
        return run_cli(
            provider_id=self.provider_id,
            role=request.role,
            cmd=cmd,
            request=request,
            classify_failure=_classify_codex_failure,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_command(self, request: AgentRequest) -> list[str]:
        sandbox = "workspace-write" if request.allow_edit else "read-only"
        cmd: list[str] = [self._command]
        # If config provides default_args, use them but force sandbox/approval per request.
        if self.cfg.default_args:
            cmd.extend(self.cfg.default_args)
            # Override sandbox flag for reviewer (read-only).
            if not request.allow_edit:
                cmd = _replace_arg(cmd, "--sandbox", "read-only")
        else:
            cmd.extend(
                [
                    "exec",
                    "--sandbox",
                    sandbox,
                    "--ask-for-approval",
                    "never",
                    "--ephemeral",
                ]
            )
        cmd.extend(["--cd", str(request.cwd)])
        cmd.append(request.prompt)
        return cmd


def _replace_arg(cmd: list[str], flag: str, value: str) -> list[str]:
    out = list(cmd)
    for i, tok in enumerate(out):
        if tok == flag and i + 1 < len(out):
            out[i + 1] = value
            return out
    out.extend([flag, value])
    return out


_AUTH_PATTERNS = re.compile(r"(unauthorized|sign in|please log in|auth.*expired|401)", re.I)
_USAGE_PATTERNS = re.compile(r"(usage limit|quota.*exceeded|out of credits)", re.I)
_RATE_PATTERNS = re.compile(r"(rate.?limit|too many requests|429)", re.I)
_POLICY_PATTERNS = re.compile(r"(refused|policy|disallowed)", re.I)


def _classify_codex_failure(exit_code: int, stdout: str, stderr: str) -> str:
    blob = f"{stdout}\n{stderr}"
    if _AUTH_PATTERNS.search(blob):
        return FAILURE_AUTH_EXPIRED
    if _USAGE_PATTERNS.search(blob):
        return FAILURE_USAGE_LIMIT
    if _RATE_PATTERNS.search(blob):
        return FAILURE_RATE_LIMIT
    if _POLICY_PATTERNS.search(blob):
        return FAILURE_POLICY_VIOLATION
    return FAILURE_UNKNOWN
