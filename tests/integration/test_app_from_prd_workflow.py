"""Integration coverage for the app_from_prd workflow (DEVF-060..065)."""
from __future__ import annotations

import json
from pathlib import Path

from devforge.core.config_loader import (
    DevforgeConfig,
    ProjectConfig,
)
from devforge.core.run_context import create_run_context
from devforge.core.state_store import StateStore
from devforge.core.workflow_engine import WorkflowEngine

_SAMPLE_PRD = """# Product

Tiny todo service.

## Target users

- Solo devs

## Functional requirements

- Add a task (must)
  - POST /tasks returns 201
- List tasks (should)
  - GET /tasks returns JSON

## Non-functional requirements

- Sub-200ms
"""


def _cfg(repo: Path) -> DevforgeConfig:
    return DevforgeConfig(
        project=ProjectConfig(name="t", root=str(repo), default_branch="main"),
    )


def test_full_run_writes_six_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _cfg(repo)

    prd = tmp_path / "prd.md"
    prd.write_text(_SAMPLE_PRD, encoding="utf-8")

    ctx = create_run_context(repo, workflow="app_from_prd", input_path=prd)
    engine = WorkflowEngine(cfg, ctx)
    engine.run("app_from_prd")

    for name in (
        "product_summary.md",
        "ambiguity_log.json",
        "assumptions.md",
        "out_of_scope.md",
        "requirements.json",
        "mvp_scope.md",
        "screen_inventory.json",
        "user_flows.md",
        "navigation_map.md",
        "architecture.md",
        "data_model.md",
        "api_contract.yaml",
        "tech_stack.md",
        "final_report.md",
    ):
        assert (ctx.root / name).exists(), f"missing artifact: {name}"

    requirements = json.loads(
        (ctx.root / "requirements.json").read_text(encoding="utf-8")
    )
    assert len(requirements["functional_requirements"]) == 2
    for fr in requirements["functional_requirements"]:
        assert fr["id"]
        assert fr["acceptance_criteria"]

    # UX inventory should also classify the API-flavored sample PRD as `api`.
    inventory = json.loads(
        (ctx.root / "screen_inventory.json").read_text(encoding="utf-8")
    )
    assert len(inventory["screens"]) == 2
    assert {s["kind"] for s in inventory["screens"]} == {"api"}
    # Navigation always starts at the synthetic START node.
    assert inventory["navigation"][0][0] == "START"

    # api_contract.yaml must be valid YAML and include the /tasks endpoint.
    import yaml as _yaml
    contract = _yaml.safe_load(
        (ctx.root / "api_contract.yaml").read_text(encoding="utf-8")
    )
    assert contract["openapi"].startswith("3.0")
    assert "/tasks" in contract["paths"]

    # Scaffold manifest + actual generated files.
    manifest = json.loads(
        (ctx.root / "scaffold_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["supported"] is True
    assert manifest["import_smoke_passed"] is True
    assert manifest["scaffold_root"] == "scaffold"
    for rel in (
        "scaffold/pyproject.toml",
        "scaffold/app/main.py",
        "scaffold/app/store.py",
        "scaffold/app/models/task.py",
        "scaffold/app/routes/tasks.py",
        "scaffold/app/services/tasks.py",
        "scaffold/tests/test_tasks.py",
    ):
        assert (ctx.root / rel).exists(), f"missing scaffold file: {rel}"

    state = StateStore(ctx.root)
    run = state.load_run()
    assert run["status"] == "completed"
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    assert steps == {
        "prd_intake": "completed",
        "requirements_inventory": "completed",
        "mvp_scope_freeze": "completed",
        "ux_flow_inventory": "completed",
        "architecture_design": "completed",
        "scaffold_generation": "completed",
    }


def test_empty_prd_fails_at_intake(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _cfg(repo)

    prd = tmp_path / "empty.md"
    prd.write_text("", encoding="utf-8")

    ctx = create_run_context(repo, workflow="app_from_prd", input_path=prd)
    engine = WorkflowEngine(cfg, ctx)
    engine.run("app_from_prd")  # driver swallows the error; engine completes

    assert (ctx.root / "failure.json").exists()
    state = StateStore(ctx.root)
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    assert steps["prd_intake"] == "failed"
    assert steps["requirements_inventory"] == "pending"
    assert steps["mvp_scope_freeze"] == "pending"
    assert steps["ux_flow_inventory"] == "pending"
    assert steps["architecture_design"] == "pending"
    assert steps["scaffold_generation"] == "pending"


def test_zero_functional_requirements_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _cfg(repo)

    prd = tmp_path / "prd.md"
    prd.write_text("# Product\n\nA thing without features.\n", encoding="utf-8")

    ctx = create_run_context(repo, workflow="app_from_prd", input_path=prd)
    engine = WorkflowEngine(cfg, ctx)
    engine.run("app_from_prd")

    assert (ctx.root / "failure.json").exists()
    state = StateStore(ctx.root)
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    assert steps["prd_intake"] == "completed"
    assert steps["requirements_inventory"] == "failed"
    assert steps["mvp_scope_freeze"] == "pending"
    assert steps["ux_flow_inventory"] == "pending"
    assert steps["architecture_design"] == "pending"
    assert steps["scaffold_generation"] == "pending"
    # PRD intake artifacts still written
    assert (ctx.root / "product_summary.md").exists()
    assert (ctx.root / "ambiguity_log.json").exists()
