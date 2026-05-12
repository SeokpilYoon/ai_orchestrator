"""Integration coverage for the app_from_prd workflow (DEVF-060..067)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from devforge.core.config_loader import (
    AcceptCondition,
    DevforgeConfig,
    ProjectConfig,
    ProviderConfig,
    RoleConfig,
    StopConditions,
)
from devforge.core.run_context import create_run_context
from devforge.core.state_store import StateStore
from devforge.core.workflow_engine import WorkflowEngine
from devforge.providers.base import AgentResult
from devforge.providers.mock import MockProvider
from devforge.providers.registry import ProviderRegistry

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


def test_full_run_writes_all_artifacts(tmp_path: Path) -> None:
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
        "vertical_slice_plan.json",
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

    # Vertical slice plan (DEVF-066) is emitted from the must-have FRs.
    slice_plan = json.loads(
        (ctx.root / "vertical_slice_plan.json").read_text(encoding="utf-8")
    )
    assert slice_plan["vertical_slice_name"]
    assert slice_plan["acceptance_criteria"]
    assert slice_plan["requirement_ids"] == ["FR-001"]
    assert any(ep.startswith("POST /tasks") for ep in slice_plan["api_endpoints"])

    state = StateStore(ctx.root)
    run = state.load_run()
    assert run["status"] == "completed"
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    # No implementer role in this cfg, so the slice implementer skips cleanly.
    assert steps == {
        "prd_intake": "completed",
        "requirements_inventory": "completed",
        "mvp_scope_freeze": "completed",
        "ux_flow_inventory": "completed",
        "architecture_design": "completed",
        "scaffold_generation": "completed",
        "vertical_slice_planner": "completed",
        "vertical_slice_implementer": "skipped",
    }

    # Slice implementer recorded a skip artifact with a reason.
    vsi = json.loads(
        (ctx.root / "vertical_slice_result.json").read_text(encoding="utf-8")
    )
    assert vsi["decision"] == "skipped"
    assert "implementer" in vsi["reason"].lower()


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
    assert steps["vertical_slice_planner"] == "pending"
    assert steps["vertical_slice_implementer"] == "pending"


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
    assert steps["vertical_slice_planner"] == "pending"
    assert steps["vertical_slice_implementer"] == "pending"
    # PRD intake artifacts still written
    assert (ctx.root / "product_summary.md").exists()
    assert (ctx.root / "ambiguity_log.json").exists()


# ---------------------------------------------------------------------------
# Happy path with mock providers — exercises DEVF-067 end-to-end
# ---------------------------------------------------------------------------

def _commit_all_in(cwd: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=cwd, check=False
    )
    if staged.returncode == 0:
        return
    subprocess.run(
        ["git", "commit", "-m", "slice"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _cfg_with_mocks(repo: Path) -> DevforgeConfig:
    """Build a cfg that wires mock implementer/reviewer for the slice stage."""
    return DevforgeConfig(
        project=ProjectConfig(name="t", root=str(repo), default_branch="main"),
        providers={
            "mock_impl": ProviderConfig(type="mock", enabled=True),
            "mock_review": ProviderConfig(type="mock", enabled=True),
            "local_rule_based": ProviderConfig(type="local_rule_based", enabled=True),
        },
        roles={
            "implementer": RoleConfig(provider_order=["mock_impl"]),
            "reviewer": RoleConfig(
                provider_order=["mock_review"],
                avoid_same_provider_as_implementer=True,
            ),
            "judge": RoleConfig(provider_order=["local_rule_based"]),
        },
        # Lower the accept threshold so the mock's score clears the gate.
        stop_conditions=StopConditions(accept_when=AcceptCondition(min_score=70)),
    )


def _install_mock_registry(
    monkeypatch: pytest.MonkeyPatch, *, impl_target: str, impl_contents: str
) -> None:
    review_payload = json.dumps(
        {
            "verdict": "pass",
            "requirement_coverage": 1.0,
            "critical_issues": [],
            "major_issues": [],
            "minor_issues": [],
            "test_concerns": [],
            "security_concerns": [],
            "recommended_revision_prompt": "",
        }
    )

    def impl_behavior(request):
        cwd = Path(request.cwd)
        target = cwd / impl_target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(impl_contents, encoding="utf-8")
        _commit_all_in(cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="slice implemented",
            changed_files=[impl_target],
            exit_code=0,
        )

    def review_behavior(request):  # noqa: ARG001
        return AgentResult(
            provider_id="mock_review",
            role="reviewer",
            success=True,
            stdout=review_payload,
            exit_code=0,
        )

    def patched(cfg: DevforgeConfig) -> ProviderRegistry:  # noqa: ARG001
        reg = ProviderRegistry()
        reg.register(MockProvider("mock_impl", behavior=impl_behavior))
        reg.register(MockProvider("mock_review", behavior=review_behavior))
        from devforge.providers.local_rule_based import LocalRuleBasedProvider
        reg.register(
            LocalRuleBasedProvider(
                "local_rule_based", ProviderConfig(type="local_rule_based")
            )
        )
        return reg

    monkeypatch.setattr(ProviderRegistry, "from_config", staticmethod(patched))


def test_slice_implementer_accepts_and_syncs_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _cfg_with_mocks(repo)

    prd = tmp_path / "prd.md"
    prd.write_text(_SAMPLE_PRD, encoding="utf-8")

    impl_target = "app/services/slice_logic.py"
    impl_contents = (
        '"""Slice logic written by the mock implementer."""\n'
        "def handle_task(title: str) -> dict:\n"
        '    return {"id": 1, "title": title}\n'
    )
    _install_mock_registry(
        monkeypatch, impl_target=impl_target, impl_contents=impl_contents
    )

    ctx = create_run_context(repo, workflow="app_from_prd", input_path=prd)
    engine = WorkflowEngine(cfg, ctx)
    engine.run("app_from_prd")

    # Vertical slice result artifact.
    vsi_path = ctx.root / "vertical_slice_result.json"
    assert vsi_path.exists()
    vsi = json.loads(vsi_path.read_text(encoding="utf-8"))
    assert vsi["decision"] == "accept"
    assert vsi["candidate_id"] == "mock_impl"
    assert vsi["provider_id"] == "mock_impl"
    assert vsi["reviewer_provider_id"] == "mock_review"
    assert vsi["synced_to_scaffold"] is True
    assert impl_target in vsi["changed_files"]

    # Accepted file is visible in the scaffold tree.
    synced = ctx.root / "scaffold" / impl_target
    assert synced.exists()
    assert synced.read_text() == impl_contents

    # Candidate artifacts under <run_root>/candidates/mock_impl/.
    cand_dir = ctx.root / "candidates" / "mock_impl"
    for name in ("prompt.md", "agent_result.json", "review.json", "decision.json"):
        assert (cand_dir / name).exists(), f"missing candidate artifact: {name}"

    # State store records the new stage as completed.
    state = StateStore(ctx.root)
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    assert steps["vertical_slice_implementer"] == "completed"

    # Final report mentions the slice implementation.
    final = (ctx.root / "final_report.md").read_text(encoding="utf-8")
    assert "Vertical slice implementation" in final
    assert "accept" in final
    assert "mock_impl" in final
