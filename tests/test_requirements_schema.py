from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.stages.prd_intake import FunctionalRaw, PrdIntake
from devforge.stages.requirements_schema import (
    RequirementsError,
    build_requirements,
    save_requirements,
)


def _intake(raw_functional=None, raw_non_functional=None, **overrides) -> PrdIntake:
    return PrdIntake(
        product_summary=overrides.get("product_summary", "x"),
        target_users=overrides.get("target_users", ["devs"]),
        raw_functional=raw_functional or [],
        raw_non_functional=raw_non_functional or [],
        ambiguities=overrides.get("ambiguities", []),
    )


def test_full_build_assigns_ids_priorities_acceptance() -> None:
    intake = _intake(
        raw_functional=[
            FunctionalRaw(title="Add a task", acceptance=["POST /tasks 201"], raw_marker="must"),
            FunctionalRaw(title="List tasks", acceptance=["GET /tasks JSON"], raw_marker="should"),
        ],
        raw_non_functional=["Sub-200ms response"],
    )
    reqs = build_requirements(intake)
    ids = [fr.id for fr in reqs.functional]
    assert ids == ["FR-001", "FR-002"]
    priorities = [fr.priority for fr in reqs.functional]
    assert priorities == ["must", "should"]
    # Every FR has at least one acceptance criterion.
    for fr in reqs.functional:
        assert fr.acceptance_criteria
    # NFR ids assigned and default to must.
    assert reqs.non_functional[0].id == "NFR-001"
    assert reqs.non_functional[0].priority == "must"


def test_missing_priority_defaults_to_must() -> None:
    intake = _intake(
        raw_functional=[FunctionalRaw(title="thing", acceptance=["does X"], raw_marker=None)],
        ambiguities=["FR-001: no priority marker, will default to 'must'"],
    )
    reqs = build_requirements(intake)
    assert reqs.functional[0].priority == "must"
    # Ambiguity preserved in unknowns.
    assert any("priority" in m for m in reqs.unknowns)


def test_missing_acceptance_inserts_placeholder() -> None:
    intake = _intake(
        raw_functional=[FunctionalRaw(title="thing", acceptance=[], raw_marker="must")],
    )
    reqs = build_requirements(intake)
    assert reqs.functional[0].acceptance_criteria == [
        "Behavior matches the requirement description"
    ]
    assert any("placeholder" in m.lower() for m in reqs.unknowns)


@pytest.mark.parametrize(
    "title,acceptance,expected",
    [
        ("Hit POST /tasks endpoint", [], "integration"),
        ("User navigates through screens", [], "e2e"),
        ("Pure function to calculate total", [], "unit"),
        ("Generic behavior", ["something happens"], "manual"),
    ],
)
def test_test_strategy_inference(title: str, acceptance: list[str], expected: str) -> None:
    intake = _intake(
        raw_functional=[FunctionalRaw(title=title, acceptance=acceptance, raw_marker="must")],
    )
    reqs = build_requirements(intake)
    assert reqs.functional[0].test_strategy == expected


def test_zero_functional_raises() -> None:
    intake = _intake(raw_functional=[])
    with pytest.raises(RequirementsError):
        build_requirements(intake)


def test_save_round_trip(tmp_path: Path) -> None:
    intake = _intake(
        raw_functional=[FunctionalRaw(title="Add", acceptance=["does X"], raw_marker="must")],
    )
    reqs = build_requirements(intake)
    out = tmp_path / "requirements.json"
    save_requirements(reqs, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "functional_requirements" in data
    assert "non_functional_requirements" in data
    assert "unknowns" in data
    assert data["functional_requirements"][0]["id"] == "FR-001"
