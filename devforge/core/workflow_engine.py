"""Workflow loader + engine.

Authoritative reference: docs/plan/02 §5.2, docs/plan/03 DEVF-012.

The loader parses ``devforge/workflows/<id>.yaml`` and validates the stage
schema. The engine then drives the actual stage execution. For MVP, only the
``feature`` workflow has a registered handler — see ``WorkflowEngine.run``.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from devforge.core.config_loader import DevforgeConfig
from devforge.core.run_context import RunContext
from devforge.core.state_store import StateStore

VALID_ROLES: frozenset[str] = frozenset(
    {
        "product_manager",
        "system_architect",
        "technical_planner",
        "implementer",
        "reviewer",
        "qa_engineer",
        "security_reviewer",
        "release_manager",
        "judge",
    }
)

VALID_TYPES: frozenset[str] = frozenset(
    {
        "local_evaluator",
        "deterministic_plus_llm",
        "local_writer",
    }
)

VALID_MODES: frozenset[str] = frozenset({"read_only", "tournament", "single"})
VALID_STRATEGIES: frozenset[str] = frozenset({"reviewer_not_same_as_implementer"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class WorkflowLoadError(Exception):
    """Raised when a workflow YAML is missing or fails schema validation."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    id: str
    role: str | None = None
    type: str | None = None
    mode: str | None = None
    strategy: str | None = None
    output: str | None = None
    composite: bool = False

    @property
    def kind(self) -> str:
        """Either ``role:<role>`` or ``type:<type>`` — handy for diagnostics."""
        return f"role:{self.role}" if self.role else f"type:{self.type}"


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow: str
    stages: tuple[Stage, ...]
    source_path: Path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_workflow(workflow_id: str, base_dir: Path | None = None) -> WorkflowDefinition:
    """Load and validate ``<base_dir>/<workflow_id>.yaml``.

    ``base_dir`` defaults to the packaged ``devforge/workflows/`` directory.
    """
    path = _resolve_workflow_path(workflow_id, base_dir)
    if not path.exists() or not path.is_file():
        raise WorkflowLoadError(f"Workflow not found: {workflow_id} ({path})")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WorkflowLoadError(f"Invalid YAML in {path}: {exc}") from exc

    return _validate(raw, workflow_id, path)


def _resolve_workflow_path(workflow_id: str, base_dir: Path | None) -> Path:
    if base_dir is not None:
        return Path(base_dir) / f"{workflow_id}.yaml"
    # Locate the packaged workflows directory.
    try:
        files = resources.files("devforge.workflows")
        candidate = Path(str(files / f"{workflow_id}.yaml"))
        if candidate.exists():
            return candidate
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass
    # Fallback: repo-relative path during development.
    fallback = (
        Path(__file__).resolve().parent.parent / "workflows" / f"{workflow_id}.yaml"
    )
    return fallback


def _validate(raw: Any, workflow_id: str, path: Path) -> WorkflowDefinition:
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"{path}: root must be a mapping, got {type(raw).__name__}")

    workflow_name = raw.get("workflow")
    if not isinstance(workflow_name, str) or not workflow_name.strip():
        raise WorkflowLoadError(f"{path}: 'workflow' must be a non-empty string")
    if workflow_name != workflow_id:
        raise WorkflowLoadError(
            f"{path}: workflow id mismatch — file declares "
            f"'{workflow_name}' but was requested as '{workflow_id}'"
        )

    stages_raw = raw.get("stages")
    if not isinstance(stages_raw, list) or not stages_raw:
        raise WorkflowLoadError(f"{path}: 'stages' must be a non-empty list")

    stages: list[Stage] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(stages_raw):
        if not isinstance(item, dict):
            raise WorkflowLoadError(
                f"{path}: stage at index {idx} must be a mapping, got {type(item).__name__}"
            )
        stage = _validate_stage(item, path, idx)
        if stage.id in seen_ids:
            raise WorkflowLoadError(f"{path}: duplicate stage id '{stage.id}'")
        seen_ids.add(stage.id)
        stages.append(stage)

    return WorkflowDefinition(
        workflow=workflow_name, stages=tuple(stages), source_path=path
    )


def _validate_stage(item: dict[str, Any], path: Path, idx: int) -> Stage:
    sid = item.get("id")
    if not isinstance(sid, str) or not sid.strip():
        raise WorkflowLoadError(f"{path}: stage at index {idx} is missing 'id'")

    role = item.get("role")
    stage_type = item.get("type")
    if role is not None and stage_type is not None:
        raise WorkflowLoadError(
            f"{path}: stage '{sid}' specifies both 'role' and 'type' — choose one"
        )
    if role is None and stage_type is None:
        raise WorkflowLoadError(
            f"{path}: stage '{sid}' must specify either 'role' or 'type'"
        )
    if role is not None and role not in VALID_ROLES:
        raise WorkflowLoadError(
            f"{path}: stage '{sid}' has unknown role '{role}'. "
            f"Allowed: {sorted(VALID_ROLES)}"
        )
    if stage_type is not None and stage_type not in VALID_TYPES:
        raise WorkflowLoadError(
            f"{path}: stage '{sid}' has unknown type '{stage_type}'. "
            f"Allowed: {sorted(VALID_TYPES)}"
        )

    mode = item.get("mode")
    if mode is not None and mode not in VALID_MODES:
        raise WorkflowLoadError(
            f"{path}: stage '{sid}' has unknown mode '{mode}'. "
            f"Allowed: {sorted(VALID_MODES)}"
        )

    strategy = item.get("strategy")
    if strategy is not None and strategy not in VALID_STRATEGIES:
        raise WorkflowLoadError(
            f"{path}: stage '{sid}' has unknown strategy '{strategy}'. "
            f"Allowed: {sorted(VALID_STRATEGIES)}"
        )

    output = item.get("output")
    if output is not None and not isinstance(output, str):
        raise WorkflowLoadError(
            f"{path}: stage '{sid}' 'output' must be a string if present"
        )

    composite = bool(item.get("composite", False))

    return Stage(
        id=sid,
        role=role,
        type=stage_type,
        mode=mode,
        strategy=strategy,
        output=output,
        composite=composite,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class WorkflowEngine:
    """Top-level orchestrator.

    Wraps the per-workflow driver so that *every* run records a structured
    state trail (DEVF-013) regardless of which workflow handler is invoked.
    """

    def __init__(self, cfg: DevforgeConfig, run_ctx: RunContext) -> None:
        self.cfg = cfg
        self.run_ctx = run_ctx
        self.state_store = StateStore(run_ctx.root)

    def run(
        self,
        workflow_id: str,
        *,
        implementer_override: str | None = None,
        reviewer_override: str | None = None,
        base_dir: Path | None = None,
    ) -> WorkflowDefinition:
        # Try to load the workflow definition. If it fails (file missing /
        # schema invalid) we still want a state record so `devforge report`
        # can surface the failure reason.
        try:
            definition = load_workflow(workflow_id, base_dir=base_dir)
        except WorkflowLoadError as exc:
            self.state_store.init_run(
                workflow=workflow_id,
                input_ref=str(self.run_ctx.input_path) if self.run_ctx.input_path else None,
                stages=[],
            )
            self.state_store.update_run_status("failed", error=str(exc))
            raise

        self.state_store.init_run(
            workflow=workflow_id,
            input_ref=str(self.run_ctx.input_path) if self.run_ctx.input_path else None,
            stages=[s.id for s in definition.stages],
        )

        if workflow_id not in {"feature", "app_from_prd"}:
            self.state_store.update_run_status(
                "failed",
                error=f"workflow '{workflow_id}' has no engine handler registered",
            )
            raise WorkflowLoadError(
                f"Workflow '{workflow_id}' is defined but no engine handler is "
                f"registered yet. Supported workflows: feature, app_from_prd"
            )

        try:
            self.state_store.update_run_status("running")
            if workflow_id == "feature":
                from devforge.stages.feature_driver import run_feature_workflow

                run_feature_workflow(
                    self.cfg,
                    self.run_ctx,
                    implementer_override,
                    reviewer_override,
                    state_store=self.state_store,
                    definition=definition,
                )
            else:  # app_from_prd
                from devforge.stages.app_from_prd_driver import run_app_from_prd_workflow

                run_app_from_prd_workflow(
                    self.cfg,
                    self.run_ctx,
                    implementer_override=implementer_override,
                    reviewer_override=reviewer_override,
                    state_store=self.state_store,
                    definition=definition,
                )
            self.state_store.update_run_status("completed")
        except Exception as exc:
            self.state_store.update_run_status("failed", error=str(exc))
            raise
        return definition
