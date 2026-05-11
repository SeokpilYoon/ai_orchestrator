"""Config schema and loader for devforge.yaml.

Authoritative reference: docs/plan/02 §8, docs/plan/03 DEVF-003.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

ProviderType = Literal[
    "codex_cli",
    "claude_cli",
    "openai_api",
    "claude_agent_sdk",
    "local_rule_based",
    "mock",
]

AuthKind = Literal[
    "chatgpt_subscription",
    "openai_api_key",
    "claude_subscription",
    "anthropic_api_key",
    "none",
]


class ProjectConfig(BaseModel):
    name: str
    root: str
    default_branch: str = "main"
    worktree_root: str | None = None
    profile: str = "python_fastapi"


class ModeConfig(BaseModel):
    max_iterations_per_task: int = 4
    max_candidates_per_task: int = 2
    keep_best_candidate: bool = True


class ProviderConfig(BaseModel):
    type: ProviderType
    enabled: bool = True
    auth: AuthKind = "none"
    command: str | None = None
    default_args: list[str] = Field(default_factory=list)
    env_required: list[str] = Field(default_factory=list)
    timeout_sec: int = 600


class RoleConfig(BaseModel):
    provider_order: list[str]
    tournament: bool = False
    avoid_same_provider_as_implementer: bool = False
    # Optional override for capabilities the role needs from its provider.
    # When empty, ``RoleRouter`` uses a role-specific default (see role_router).
    required_capabilities: list[str] = Field(default_factory=list)


class ValidationCommands(BaseModel):
    install_check: str | None = None
    lint: str | None = None
    typecheck: str | None = None
    test: str | None = None
    build: str | None = None
    import_smoke: str | None = None
    compose_config: str | None = None
    api_tests: str | None = None
    healthcheck: str | None = None
    compile: str | None = None
    editmode_tests: str | None = None


class ValidationConfig(BaseModel):
    commands: ValidationCommands = Field(default_factory=ValidationCommands)
    default_timeout_sec: int = 300


class FilePolicy(BaseModel):
    allowed_paths: list[str] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=list)
    require_human_review_if_modified: list[str] = Field(default_factory=list)


class CommandPolicy(BaseModel):
    blocked_patterns: list[str] = Field(default_factory=list)
    require_human_review: list[str] = Field(default_factory=list)


class ScoringConfig(BaseModel):
    build_pass: int = 25
    tests_pass: int = 25
    lint_pass: int = 10
    typecheck_pass: int = 10
    acceptance_coverage: int = 20
    reviewer_pass: int = 10

    blocked_file_modified: int = 100
    secret_detected: int = 100
    test_deleted: int = 60
    test_weakened: int = 50
    unrelated_large_diff: int = 30
    dependency_added_without_reason: int = 20
    critical_review_issue: int = 20


class AcceptCondition(BaseModel):
    build_pass: bool = True
    tests_pass: bool = True
    reviewer_verdict: Literal["pass", "needs_revision", "reject"] = "pass"
    min_score: int = 85


class DiscardCondition(BaseModel):
    blocked_file_modified: bool = True
    secret_detected: bool = True


class StopConditions(BaseModel):
    accept_when: AcceptCondition = Field(default_factory=AcceptCondition)
    discard_when: DiscardCondition = Field(default_factory=DiscardCondition)


class DevforgeConfig(BaseModel):
    project: ProjectConfig
    mode: ModeConfig = Field(default_factory=ModeConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    file_policy: FilePolicy = Field(default_factory=FilePolicy)
    command_policy: CommandPolicy = Field(default_factory=CommandPolicy)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)


class ConfigError(Exception):
    """Raised when devforge.yaml is missing, malformed, or fails validation."""


def load_config(path: str | Path) -> DevforgeConfig:
    """Load and validate devforge.yaml at ``path``.

    Raises:
        ConfigError: file missing, not valid YAML, or schema validation failed.
    """
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping in {p}")
    try:
        return DevforgeConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Config validation failed for {p}:\n{exc}") from exc


def find_config(start: Path | None = None) -> Path | None:
    """Search for devforge.yaml from ``start`` upward to the filesystem root."""
    cur = (start or Path.cwd()).resolve()
    while True:
        candidate = cur / "devforge.yaml"
        if candidate.exists():
            return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent
