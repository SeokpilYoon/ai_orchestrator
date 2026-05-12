"""Unit tests for the backlog generator (DEVF-068)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.stages.architecture_generator import build_architecture
from devforge.stages.backlog_generator import (
    Backlog,
    BacklogGeneratorError,
    BacklogItem,
    generate_backlog,
    save_backlog,
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


def _bundle(*frs: FunctionalRequirement, stack: str = "python-fastapi-only"):
    intake = PrdIntake(
        target_users=["devs"],
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
        acceptance=['POST /tasks returns 201', "Response echoes the title"],
    ),
    _fr(
        2,
        title="List tasks",
        description="GET /tasks returns JSON",
        acceptance=["GET /tasks returns the list"],
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
# Trace + priority mapping
# ---------------------------------------------------------------------------

def test_one_backlog_item_per_functional_requirement() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    backlog = generate_backlog(reqs, scope, inv, arch)
    assert [item.id for item in backlog.items] == [
        "TASK-001",
        "TASK-002",
        "TASK-003",
        "TASK-004",
    ]
    assert [item.requirement_ids for item in backlog.items] == [
        ["FR-001"],
        ["FR-002"],
        ["FR-003"],
        ["FR-004"],
    ]


def test_priority_mapping_must_should_could() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    backlog = generate_backlog(reqs, scope, inv, arch)
    priorities = {item.requirement_ids[0]: item.priority for item in backlog.items}
    assert priorities == {
        "FR-001": "P0",
        "FR-002": "P0",
        "FR-003": "P1",
        "FR-004": "P2",
    }


def test_acceptance_criteria_copied_per_item() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    backlog = generate_backlog(reqs, scope, inv, arch)
    assert backlog.items[0].acceptance_criteria == [
        "POST /tasks returns 201",
        "Response echoes the title",
    ]
    assert backlog.items[2].acceptance_criteria == [
        "PATCH /tasks/{id} flips the done flag"
    ]


# ---------------------------------------------------------------------------
# Complexity heuristic
# ---------------------------------------------------------------------------

def test_complexity_small_for_minimal_fr() -> None:
    fr = _fr(
        1,
        title="Tiny",
        description="A pure-python helper.",
        acceptance=["does the thing"],
    )
    reqs, intake, scope, inv, arch = _bundle(fr)
    backlog = generate_backlog(reqs, scope, inv, arch)
    # No API ops, no entities, 1 AC → S.
    assert backlog.items[0].estimated_complexity == "S"


def test_complexity_large_for_many_acceptance_criteria() -> None:
    fr = _fr(
        1,
        title="Big",
        description="big feature",
        acceptance=[f"criterion {i}" for i in range(6)],
    )
    reqs, intake, scope, inv, arch = _bundle(fr)
    backlog = generate_backlog(reqs, scope, inv, arch)
    assert backlog.items[0].estimated_complexity == "L"


def test_complexity_medium_for_typical_api_fr() -> None:
    fr = _fr(
        1,
        title="Add task",
        description="POST /tasks returns 201",
        acceptance=["does X", "does Y"],
    )
    reqs, intake, scope, inv, arch = _bundle(fr)
    backlog = generate_backlog(reqs, scope, inv, arch)
    # 1 op, 1 entity, 2 ACs → M.
    assert backlog.items[0].estimated_complexity == "M"


# ---------------------------------------------------------------------------
# Dependency derivation
# ---------------------------------------------------------------------------

def test_shared_entity_creates_dependency_on_must_have() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    backlog = generate_backlog(reqs, scope, inv, arch)
    by_id = {item.id: item for item in backlog.items}
    # FR-001 is P0 producer of Task. FR-002/003/004 also touch Task → depend on FR-001's item.
    assert by_id["TASK-001"].dependencies == []
    for follower in ("TASK-002", "TASK-003", "TASK-004"):
        assert "TASK-001" in by_id[follower].dependencies


def test_no_shared_entity_no_dependency() -> None:
    independent = [
        _fr(1, title="Compute A", description="pure-python A", acceptance=["a"]),
        _fr(2, title="Compute B", description="pure-python B", acceptance=["b"]),
    ]
    reqs, intake, scope, inv, arch = _bundle(*independent)
    backlog = generate_backlog(reqs, scope, inv, arch)
    assert all(item.dependencies == [] for item in backlog.items)


# ---------------------------------------------------------------------------
# Error / edge cases
# ---------------------------------------------------------------------------

def test_empty_requirements_raises() -> None:
    reqs = Requirements(functional=[])
    intake = PrdIntake()
    scope = freeze_mvp_scope(reqs, intake)
    inv = build_ux_inventory(reqs, intake, scope)
    arch = build_architecture(reqs, intake, scope, inv, "python-fastapi-only")
    with pytest.raises(BacklogGeneratorError):
        generate_backlog(reqs, scope, inv, arch)


def test_no_p0_items_emits_warning_note() -> None:
    only_should = [
        _fr(
            1,
            title="Browse",
            description="GET /items returns JSON",
            priority="should",
            acceptance=["lists items"],
        ),
    ]
    reqs, intake, scope, inv, arch = _bundle(*only_should)
    backlog = generate_backlog(reqs, scope, inv, arch)
    assert any("No P0" in n for n in backlog.notes)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_to_dict_keys_match_spec() -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    backlog = generate_backlog(reqs, scope, inv, arch)
    payload = backlog.to_dict()
    assert "items" in payload
    first = payload["items"][0]
    for key in (
        "id",
        "title",
        "requirement_ids",
        "acceptance_criteria",
        "priority",
        "estimated_complexity",
        "dependencies",
    ):
        assert key in first


def test_save_backlog_writes_valid_json(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    backlog = generate_backlog(reqs, scope, inv, arch)
    out = tmp_path / "backlog.json"
    save_backlog(backlog, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["items"]) == 4
    assert payload["items"][0]["id"] == "TASK-001"


def test_round_trip_dataclass_to_dict() -> None:
    item = BacklogItem(
        id="TASK-001",
        title="x",
        requirement_ids=["FR-001"],
        acceptance_criteria=["ac"],
        priority="P0",
        estimated_complexity="M",
        dependencies=["TASK-000"],
    )
    payload = item.to_dict()
    assert payload["id"] == "TASK-001"
    assert payload["dependencies"] == ["TASK-000"]

    backlog = Backlog(items=[item])
    assert backlog.to_dict()["items"][0]["priority"] == "P0"
