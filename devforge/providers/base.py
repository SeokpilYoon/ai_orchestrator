"""Provider base interfaces.

Authoritative reference: docs/plan/02 §5.5, docs/plan/03 DEVF-020.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

AgentRole = Literal[
    "product_manager",
    "system_architect",
    "technical_planner",
    "implementer",
    "reviewer",
    "qa_engineer",
    "security_reviewer",
    "release_manager",
    "judge",
]

ExpectedOutput = Literal["text", "json", "patch", "report"]


@dataclass
class AgentRequest:
    role: AgentRole
    prompt: str
    cwd: Path
    run_id: str
    timeout_sec: int = 600
    expected_output: ExpectedOutput = "text"
    allow_edit: bool = True
    allow_shell: bool = True
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    provider_id: str
    role: AgentRole
    success: bool
    stdout: str = ""
    stderr: str = ""
    parsed_json: dict[str, Any] | None = None
    changed_files: list[str] = field(default_factory=list)
    exit_code: int = 0
    usage_hint: dict[str, Any] | None = None
    error: str | None = None
    failure_class: str | None = None  # DEVF-051

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "role": self.role,
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout_len": len(self.stdout),
            "stderr_len": len(self.stderr),
            "changed_files": list(self.changed_files),
            "usage_hint": self.usage_hint,
            "error": self.error,
            "failure_class": self.failure_class,
            "parsed_json": self.parsed_json,
        }


# Capability strings used by Role Router for filtering.
CAPABILITIES = {
    "read_repo",
    "edit_files",
    "run_shell",
    "non_interactive",
    "json_output",
    "review_only",
    "deterministic",
}


@runtime_checkable
class AgentProvider(Protocol):
    provider_id: str

    def healthcheck(self) -> bool:
        ...

    def run(self, request: AgentRequest) -> AgentResult:
        ...

    def supports(self, capability: str) -> bool:
        ...


# Failure classes (DEVF-051) — kept as constants so checkers can grep them.
FAILURE_AUTH_EXPIRED = "auth_expired"
FAILURE_USAGE_LIMIT = "usage_limit_hit"
FAILURE_RATE_LIMIT = "rate_limit"
FAILURE_COMMAND_MISSING = "command_missing"
FAILURE_TIMEOUT = "timeout"
FAILURE_MALFORMED_OUTPUT = "malformed_output"
FAILURE_POLICY_VIOLATION = "policy_violation"
FAILURE_UNKNOWN = "unknown"
