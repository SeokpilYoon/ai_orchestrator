"""Unit tests for the vertical slice planner (DEVF-066)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.stages.architecture_generator import build_architecture
from devforge.stages.mvp_scope import freeze_mvp_scope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import (
    FunctionalRequirement,
    Requirements,
)
from devforge.stages.ux_flow import build_ux_inventory
from devforge.stages.vertical_slice_planner import (
    VerticalSlicePlan,
    VerticalSlicePlannerError,
    plan_vertical_slice,
    save_vertical_slice_plan,
)


def _fr(
    idx: int,
    *,
    title: str,
    description: str,
    priority: str = "must",
    acceptance: list[str] | None = None,
) -> FunctionalRequirement:
    return FunctionalRequirement(
        id=f"FR-{idx:03d}",
        title=title,
        description=description,
        priority=priority,
        acceptance_criteria=acceptance or ["does X"],
        test_strategy="integration",
    )


def _bundle(
    *frs: FunctionalRequirement,
    stack: str = "python-fastapi-only",
    target_users: list[str] | None = None,
):
    intake = PrdIntake(
        target_users=target_users if target_users is not None else ["devs"],
        constraints=["No external database — use an in-memory store"],
    )
    reqs = Requirements(functional=list(frs))
    scope = freeze_mvp_scope(reqs, intake)
    inv = build_ux_inventory(reqs, intake, scope)
    arch = build_architecture(reqs, intake, scope, inv, stack)
    return reqs, intake, scope, inv, arch


_TASK_FRS = [
    _fr(
        1,
        title="Add task",
        description="POST /tasks returns 201",
        acceptance=[
            'POST /tasks with {"title": "buy milk"} returns 201',
            "Response body echoes the title",
        ],
    ),
    _fr(
        2,
        title="List tasks",
        description="GET /tasks returns JSON",
        acceptance=["GET /tasks returns the list of created tasks"],
    ),
    _fr(
        3,
        title="Mark complete",
        description="PATCH /tasks/{id} with done=true",
        priority="should",
        acceptance=["PATCH /tasks/{id} flips the done flag"],
    ),
    _fr(
        4,
        title="Delete task",
        description="DELETE /tasks/{id} returns 204",
        priority="could",
        acceptance=["DELETE /tasks/{id} returns 204"],
    ),
]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_plan_emits_required_fields_for_todo_app() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)

    assert plan.vertical_slice_name
    assert plan.user_journey, "user_journey should not be empty"
    assert plan.screens, "screens should not be empty"
    assert plan.api_endpoints, "api_endpoints should not be empty"
    assert plan.data_entities == ["Task"]
    assert plan.acceptance_criteria, "acceptance_criteria should not be empty"


def test_anchor_picks_first_must_have_flow() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)

    # Both FR-001 and FR-002 are must-have and share the Task entity, so the
    # planner should select them and skip the should/could-priority FRs.
    assert "FR-001" in plan.requirement_ids
    assert "FR-002" in plan.requirement_ids
    assert "FR-003" not in plan.requirement_ids
    assert "FR-004" not in plan.requirement_ids


def test_api_endpoints_render_method_and_path() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)
    assert any(ep.startswith("POST /tasks") for ep in plan.api_endpoints)
    assert any(ep.startswith("GET /tasks") for ep in plan.api_endpoints)


def test_acceptance_criteria_preserve_order_and_dedup() -> None:
    duplicate = _fr(
        1,
        title="Add task",
        description="POST /tasks returns 201",
        acceptance=["POST /tasks returns 201", "POST /tasks returns 201"],
    )
    reqs, intake, scope, inv, arch = _bundle(duplicate)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)
    assert plan.acceptance_criteria.count("POST /tasks returns 201") == 1


def test_slice_caps_at_three_flows() -> None:
    many = [
        _fr(
            i + 1,
            title=f"Op {i}",
            description="POST /widgets returns 201",
            acceptance=[f"step {i}"],
        )
        for i in range(6)
    ]
    reqs, intake, scope, inv, arch = _bundle(*many)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)
    # All six FRs share the same /widgets entity, so the greedy selector
    # could add every flow. The cap should keep us at 3 max.
    assert len(plan.requirement_ids) <= 3
    assert " + " in plan.vertical_slice_name  # composite name


# ---------------------------------------------------------------------------
# Backend / logical-only PRDs
# ---------------------------------------------------------------------------

def test_logical_only_prd_still_emits_a_slice() -> None:
    logical = [
        _fr(
            1,
            title="Compute score",
            description="Pure-Python computation of the score.",
            acceptance=["score(x) == 42 for the canonical fixture"],
        ),
        _fr(
            2,
            title="Cache results",
            description="Memoize the score computation.",
            acceptance=["second call returns from cache"],
        ),
    ]
    reqs, intake, scope, inv, arch = _bundle(*logical)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)
    assert plan.vertical_slice_name
    assert plan.api_endpoints == []
    assert plan.data_entities == []
    # The notes should explain why the slice is surface-only.
    assert any("surface-only" in n for n in plan.notes) or any(
        "No API operations" in n for n in plan.notes
    )


# ---------------------------------------------------------------------------
# Priority fallback
# ---------------------------------------------------------------------------

def test_fallback_to_should_when_no_must() -> None:
    only_should_could = [
        _fr(
            1,
            title="Browse",
            description="GET /items returns JSON",
            priority="should",
            acceptance=["GET /items lists items"],
        ),
        _fr(
            2,
            title="Search",
            description="GET /items?q= filters",
            priority="could",
            acceptance=["filter applied"],
        ),
    ]
    reqs, intake, scope, inv, arch = _bundle(*only_should_could)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)
    assert plan.requirement_ids == ["FR-001"] or "FR-001" in plan.requirement_ids
    assert any("Priority fallback" in n for n in plan.notes)


def test_fallback_to_could_when_no_must_or_should() -> None:
    only_could = [
        _fr(
            1,
            title="Nice-to-have",
            description="GET /maybe",
            priority="could",
            acceptance=["maybe works"],
        ),
    ]
    reqs, intake, scope, inv, arch = _bundle(*only_could)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)
    assert plan.requirement_ids == ["FR-001"]
    assert any("could" in n for n in plan.notes)


def test_no_flows_raises() -> None:
    reqs, intake, scope, inv, arch = _bundle(
        _fr(1, title="Anything", description="d", acceptance=["a"])
    )
    inv.flows = []  # simulate an empty inventory
    with pytest.raises(VerticalSlicePlannerError):
        plan_vertical_slice(reqs, intake, scope, inv, arch)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_to_dict_keys_match_spec() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)
    payload = plan.to_dict()
    for key in (
        "vertical_slice_name",
        "user_journey",
        "screens",
        "api_endpoints",
        "data_entities",
        "acceptance_criteria",
    ):
        assert key in payload


def test_save_writes_valid_json(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    plan = plan_vertical_slice(reqs, intake, scope, inv, arch)
    out = tmp_path / "vertical_slice_plan.json"
    save_vertical_slice_plan(plan, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["vertical_slice_name"] == plan.vertical_slice_name
    assert payload["acceptance_criteria"] == plan.acceptance_criteria


def test_round_trip_dataclass_to_dict() -> None:
    plan = VerticalSlicePlan(
        vertical_slice_name="x",
        user_journey=["a"],
        screens=["SCREEN-001"],
        api_endpoints=["GET /x"],
        data_entities=["X"],
        acceptance_criteria=["acc"],
        requirement_ids=["FR-001"],
        notes=["note"],
    )
    payload = plan.to_dict()
    assert payload["vertical_slice_name"] == "x"
    assert payload["screens"] == ["SCREEN-001"]
