from __future__ import annotations

from pathlib import Path

import yaml

from devforge.stages.architecture_generator import (
    build_architecture,
    save_api_contract,
    save_architecture,
    save_data_model,
    save_tech_stack,
)
from devforge.stages.mvp_scope import freeze_mvp_scope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import (
    FunctionalRequirement,
    Requirements,
)
from devforge.stages.ux_flow import build_ux_inventory


def _fr(
    idx: int,
    *,
    title: str,
    description: str | None = None,
    priority: str = "must",
    acceptance: list[str] | None = None,
) -> FunctionalRequirement:
    return FunctionalRequirement(
        id=f"FR-{idx:03d}",
        title=title,
        description=description or title,
        priority=priority,
        acceptance_criteria=acceptance or ["does X"],
        test_strategy="integration",
    )


def _bundle(*frs: FunctionalRequirement, constraints: list[str] | None = None):
    intake = PrdIntake(
        target_users=["devs"],
        constraints=list(constraints or []),
    )
    reqs = Requirements(functional=list(frs))
    scope = freeze_mvp_scope(reqs, intake)
    inv = build_ux_inventory(reqs, intake, scope)
    return reqs, intake, scope, inv


# ---------------------------------------------------------------------------
# Stack profile
# ---------------------------------------------------------------------------

def test_supported_stack_yields_full_profile() -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201"),
    )
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    assert arch.supported_stack is True
    assert arch.runtime.startswith("Python")
    assert arch.framework == "FastAPI"
    assert arch.test_command == "pytest -q"
    assert any("FastAPI" in purpose for _, purpose in arch.layers)
    assert any("app.routes" in m for m in arch.module_boundaries)


def test_unknown_stack_marks_unsupported_but_still_builds() -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201"),
    )
    arch = build_architecture(reqs, intake, scope, inv, "react-fastapi-postgres")
    assert arch.supported_stack is False
    assert any("planned" in n.lower() for n in arch.notes)
    # The architecture still has *something* — just marked as planned.
    assert arch.runtime
    assert arch.framework
    assert arch.scaffold_outline


# ---------------------------------------------------------------------------
# Operations + entities
# ---------------------------------------------------------------------------

def test_api_operations_extracted() -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201",
            acceptance=['{"title": "buy milk"}']),
        _fr(2, title="List tasks", description="GET /tasks returns JSON"),
    )
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    methods = sorted({op.method for op in arch.operations})
    assert methods == ["GET", "POST"]
    post = next(op for op in arch.operations if op.method == "POST")
    assert post.path == "/tasks"
    assert post.request_body_schema == "Task"
    assert "201" in post.responses


def test_entity_inferred_from_path_and_json_body() -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task",
            description="POST /tasks returns 201",
            acceptance=['{"title": "buy milk", "done": false}']),
    )
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    assert len(arch.entities) == 1
    task = arch.entities[0]
    assert task.name == "Task"
    assert task.fields.get("title") == "string"
    assert task.fields.get("done") == "boolean"
    assert "FR-001" in task.sourced_from


def test_persistence_inferred_from_constraints() -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201"),
        constraints=["No external database — use an in-memory store"],
    )
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    assert "in-memory" in arch.persistence.lower()


# ---------------------------------------------------------------------------
# Round-trip saves
# ---------------------------------------------------------------------------

def test_save_round_trip_architecture_markdown(tmp_path: Path) -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201"),
    )
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    out = tmp_path / "architecture.md"
    save_architecture(arch, out)
    md = out.read_text(encoding="utf-8")
    for header in ("# Architecture", "## Stack", "## Layers", "## Persistence", "## Tradeoffs"):
        assert header in md


def test_save_round_trip_data_model(tmp_path: Path) -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201",
            acceptance=['{"title": "buy milk"}']),
    )
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    out = tmp_path / "data_model.md"
    save_data_model(arch, out)
    md = out.read_text(encoding="utf-8")
    assert "Task" in md
    assert "title" in md
    assert "string" in md


def test_save_round_trip_api_contract_yaml_parses(tmp_path: Path) -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201",
            acceptance=['{"title": "buy milk"}']),
        _fr(2, title="List tasks", description="GET /tasks returns JSON"),
    )
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    out = tmp_path / "api_contract.yaml"
    save_api_contract(arch, out)
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert doc["openapi"].startswith("3.0")
    assert "/tasks" in doc["paths"]
    assert "post" in doc["paths"]["/tasks"]
    assert "get" in doc["paths"]["/tasks"]
    # The Task schema is present because of the JSON body in FR-001.
    assert "Task" in doc["components"]["schemas"]


def test_save_round_trip_tech_stack(tmp_path: Path) -> None:
    reqs, intake, scope, inv = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201"),
    )
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    out = tmp_path / "tech_stack.md"
    save_tech_stack(arch, out)
    md = out.read_text(encoding="utf-8")
    assert "# Tech stack" in md
    assert "FastAPI" in md
    assert "pytest -q" in md
    assert "## Scaffold outline" in md
