"""Claude Code CLI provider adapter.

Authoritative reference: docs/plan/02 §5.7, docs/plan/03 DEVF-023.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from devforge.core.config_loader import ProviderConfig
from devforge.providers._subprocess import run_cli
from devforge.providers.base import (
    FAILURE_AUTH_EXPIRED,
    FAILURE_MALFORMED_OUTPUT,
    FAILURE_POLICY_VIOLATION,
    FAILURE_RATE_LIMIT,
    FAILURE_UNKNOWN,
    FAILURE_USAGE_LIMIT,
    AgentRequest,
    AgentResult,
)

_FORBIDDEN_FLAGS = (
    "--dangerously-skip-permissions",
    "bypassPermissions",
)


class ClaudeCliProvider:
    """Adapter for the ``claude`` CLI in print (`-p`) mode."""

    def __init__(self, provider_id: str, cfg: ProviderConfig) -> None:
        self.provider_id = provider_id
        self.cfg = cfg
        self._command = cfg.command or "claude"
        for arg in cfg.default_args:
            for forbidden in _FORBIDDEN_FLAGS:
                if forbidden in arg:
                    raise ValueError(
                        f"Provider {provider_id}: forbidden flag '{arg}' is not allowed."
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
            "review_only",
        }

    def run(self, request: AgentRequest) -> AgentResult:
        cmd = self._build_command(request)
        result = run_cli(
            provider_id=self.provider_id,
            role=request.role,
            cmd=cmd,
            request=request,
            classify_failure=_classify_claude_failure,
        )
        if result.success and request.expected_output == "json":
            self._attach_parsed_json(result)
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_command(self, request: AgentRequest) -> list[str]:
        tools = "Read,Edit,Write,Bash" if request.allow_edit else "Read,Grep,Glob"
        permission_mode = "acceptEdits" if request.allow_edit else "plan"

        cmd: list[str] = [self._command, "-p", request.prompt]
        cmd.extend(
            [
                "--output-format",
                "json",
                "--permission-mode",
                permission_mode,
                "--tools",
                tools,
                "--max-turns",
                "20",
            ]
        )
        return cmd

    def _attach_parsed_json(self, result: AgentResult) -> None:
        try:
            obj = json.loads(result.stdout)
        except json.JSONDecodeError:
            result.parsed_json = None
            result.failure_class = FAILURE_MALFORMED_OUTPUT
            result.success = False
            result.error = "expected JSON output but could not parse stdout"
            return
        result.parsed_json = obj if isinstance(obj, dict) else {"value": obj}


_AUTH_PATTERNS = re.compile(
    r"(not authenticated|please run.*login|session.*expired|invalid.*api.?key)", re.I
)
_USAGE_PATTERNS = re.compile(r"(usage limit|monthly limit|quota.*exceeded)", re.I)
_RATE_PATTERNS = re.compile(r"(rate.?limit|429|too many requests)", re.I)
_POLICY_PATTERNS = re.compile(r"(refused|policy violation|disallowed)", re.I)


def _classify_claude_failure(exit_code: int, stdout: str, stderr: str) -> str:
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
