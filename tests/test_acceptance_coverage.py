"""Unit tests for the acceptance coverage calculator (DEVF-070)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.stages.acceptance_coverage import (
    AcceptanceCoverage,
    FrCoverage,
    PriorityRollup,
    calculate_acceptance_coverage,
    save_acceptance_coverage,
)
from devforge.stages.backlog_generator import Backlog, BacklogItem
from devforge.stages.backlog_implementer import (
    BacklogProgress,
    BacklogProgressItem,
)
from devforge.stages.mvp_scope import freeze_mvp_scope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import (
    FunctionalRequirement,
    Requirements,
)
from devforge.stages.vertical_slice_implementer import (
    VerticalSliceImplementerResult,
)
from devforge.stages.vertical_slice_planner import VerticalSlicePlan

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fr(
    idx: int,
    *,
    priority: str = "must",
    acs: list[str] | None = None,
) -> FunctionalRequirement:
    return FunctionalRequirement(
        id=f"FR-{idx:03d}",
        title=f"Req {idx}",
        description=f"req {idx}",
        priority=priority,
        acceptance_criteria=acs or [f"ac{idx}.1"],
        test_strategy="integration",
    )


def _reqs(*frs: FunctionalRequirement) -> Requirements:
    return Requirements(functional=list(frs))


def _scope(reqs: Requirements):
    return freeze_mvp_scope(reqs, PrdIntake(target_users=["devs"]))


def _backlog_item(
    idx: int, *, fr_ids: list[str] | None = None, ac_count: int = 1
) -> BacklogItem:
    return BacklogItem(
        id=f"TASK-{idx:03d}",
        title=f"task {idx}",
        requirement_ids=fr_ids or [f"FR-{idx:03d}"],
        acceptance_criteria=[f"ac{idx}.{j}" for j in range(1, ac_count + 1)],
        priority="P0",
        estimated_complexity="S",
    )


def _progress(items: list[tuple[str, str]]) -> BacklogProgress:
    """``items`` is a list of (task_id, status) tuples."""
    return BacklogProgress(
        items=[
            BacklogProgressItem(task_id=tid, status=status)
            for tid, status in items
        ],
        decision="completed",
        accepted_count=sum(1 for _, s in items if s == "accept"),
        total_count=len(items),
    )


# ---------------------------------------------------------------------------
# Pure functional coverage
# ---------------------------------------------------------------------------

def test_no_artifacts_yields_zero_coverage_with_total() -> None:
    reqs = _reqs(_fr(1, acs=["a", "b"]), _fr(2, priority="should"))
    coverage = calculate_acceptance_coverage(reqs, _scope(reqs))
    assert coverage.overall_total == 3
    assert coverage.overall_passed == 0
    assert coverage.overall_coverage == 0.0
    for fr in coverage.by_requirement:
        assert fr.covered_by == "none"
        assert fr.coverage == 0.0


def test_slice_attribution_covers_all_listed_frs() -> None:
    reqs = _reqs(_fr(1, acs=["a", "b"]), _fr(2, acs=["c"]))
    plan = VerticalSlicePlan(
        vertical_slice_name="x",
        requirement_ids=["FR-001", "FR-002"],
        acceptance_criteria=["x"],
    )
    result = VerticalSliceImplementerResult(decision="accept")
    coverage = calculate_acceptance_coverage(
        reqs, _scope(reqs), slice_plan=plan, slice_result=result
    )
    assert coverage.overall_coverage == 1.0
    assert all(fr.covered_by == "slice" for fr in coverage.by_requirement)


def test_slice_not_accepted_is_not_attributed() -> None:
    reqs = _reqs(_fr(1))
    plan = VerticalSlicePlan(
        vertical_slice_name="x",
        requirement_ids=["FR-001"],
        acceptance_criteria=["x"],
    )
    result = VerticalSliceImplementerResult(decision="revise")
    coverage = calculate_acceptance_coverage(
        reqs, _scope(reqs), slice_plan=plan, slice_result=result
    )
    assert coverage.by_requirement[0].covered_by == "none"
    assert coverage.overall_coverage == 0.0


def test_backlog_accept_covers_fr() -> None:
    reqs = _reqs(_fr(1, acs=["a", "b"]), _fr(2))
    backlog = Backlog(items=[_backlog_item(1, ac_count=2), _backlog_item(2)])
    progress = _progress([("TASK-001", "accept"), ("TASK-002", "discard")])
    coverage = calculate_acceptance_coverage(
        reqs, _scope(reqs), backlog=backlog, backlog_progress=progress
    )
    by_id = {fr.requirement_id: fr for fr in coverage.by_requirement}
    assert by_id["FR-001"].covered_by == "backlog"
    assert by_id["FR-001"].source_task_ids == ["TASK-001"]
    assert by_id["FR-002"].covered_by == "none"


def test_already_in_slice_status_also_attributes_backlog_source() -> None:
    """When the backlog item is marked already_in_slice, the FR still counts
    as delivered (since the slice satisfied it)."""
    reqs = _reqs(_fr(1))
    backlog = Backlog(items=[_backlog_item(1)])
    progress = _progress([("TASK-001", "already_in_slice")])
    coverage = calculate_acceptance_coverage(
        reqs, _scope(reqs), backlog=backlog, backlog_progress=progress
    )
    fr = coverage.by_requirement[0]
    # No slice plan supplied here, so attribution falls back to backlog with
    # the source task recorded.
    assert fr.covered_by == "backlog"
    assert fr.source_task_ids == ["TASK-001"]


def test_slice_attribution_wins_over_backlog() -> None:
    reqs = _reqs(_fr(1))
    plan = VerticalSlicePlan(
        vertical_slice_name="x",
        requirement_ids=["FR-001"],
        acceptance_criteria=["x"],
    )
    result = VerticalSliceImplementerResult(decision="accept")
    backlog = Backlog(items=[_backlog_item(1)])
    progress = _progress([("TASK-001", "already_in_slice")])
    coverage = calculate_acceptance_coverage(
        reqs, _scope(reqs),
        slice_plan=plan, slice_result=result,
        backlog=backlog, backlog_progress=progress,
    )
    fr = coverage.by_requirement[0]
    assert fr.covered_by == "slice"


# ---------------------------------------------------------------------------
# Priority roll-up
# ---------------------------------------------------------------------------

def test_priority_rollup_aggregates_per_priority() -> None:
    reqs = _reqs(
        _fr(1, priority="must", acs=["a"]),
        _fr(2, priority="should", acs=["b", "c"]),
        _fr(3, priority="could", acs=["d"]),
    )
    backlog = Backlog(items=[
        _backlog_item(1),
        _backlog_item(2, ac_count=2),
        _backlog_item(3),
    ])
    progress = _progress([
        ("TASK-001", "accept"),
        ("TASK-002", "accept"),
        ("TASK-003", "discard"),
    ])
    coverage = calculate_acceptance_coverage(
        reqs, _scope(reqs), backlog=backlog, backlog_progress=progress
    )
    by_pri = {pr.priority: pr for pr in coverage.by_priority}
    assert by_pri["must"].passed == 1 and by_pri["must"].total == 1
    assert by_pri["should"].passed == 2 and by_pri["should"].total == 2
    assert by_pri["could"].passed == 0 and by_pri["could"].total == 1
    assert by_pri["must"].coverage == pytest.approx(1.0)
    assert by_pri["could"].coverage == pytest.approx(0.0)


def test_priority_rollup_skips_empty_buckets() -> None:
    reqs = _reqs(_fr(1, priority="must"))
    coverage = calculate_acceptance_coverage(reqs, _scope(reqs))
    by_pri = {pr.priority: pr for pr in coverage.by_priority}
    assert "must" in by_pri
    assert "should" not in by_pri
    assert "could" not in by_pri


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def test_notes_list_uncovered_frs() -> None:
    reqs = _reqs(_fr(1), _fr(2))
    coverage = calculate_acceptance_coverage(reqs, _scope(reqs))
    text = " ".join(coverage.notes)
    assert "FR-001" in text and "FR-002" in text


def test_notes_carry_backlog_skip_reason() -> None:
    reqs = _reqs(_fr(1))
    progress = BacklogProgress(
        decision="skipped",
        reason="no implementer provider available",
        total_count=1,
    )
    coverage = calculate_acceptance_coverage(
        reqs, _scope(reqs), backlog=Backlog(items=[]), backlog_progress=progress
    )
    text = " ".join(coverage.notes)
    assert "skipped" in text and "no implementer provider" in text


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_to_dict_keys_match_spec() -> None:
    reqs = _reqs(_fr(1))
    coverage = calculate_acceptance_coverage(reqs, _scope(reqs))
    payload = coverage.to_dict()
    assert payload["overall"]["passed"] == 0
    assert payload["overall"]["total"] == 1
    assert payload["overall"]["coverage"] == 0.0
    assert payload["by_requirement"][0]["requirement_id"] == "FR-001"
    assert "by_priority" in payload
    assert "notes" in payload


def test_save_acceptance_coverage_writes_valid_json(tmp_path: Path) -> None:
    reqs = _reqs(_fr(1, acs=["a", "b"]))
    backlog = Backlog(items=[_backlog_item(1, ac_count=2)])
    progress = _progress([("TASK-001", "accept")])
    coverage = calculate_acceptance_coverage(
        reqs, _scope(reqs), backlog=backlog, backlog_progress=progress
    )
    out = tmp_path / "acceptance_coverage.json"
    save_acceptance_coverage(coverage, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["overall"]["coverage"] == 1.0
    assert payload["by_requirement"][0]["covered_by"] == "backlog"


def test_round_trip_dataclass_to_dict() -> None:
    item = FrCoverage(
        requirement_id="FR-001",
        title="x",
        priority="must",
        total=2,
        passed=2,
        coverage=1.0,
        covered_by="slice",
        source_task_ids=[],
    )
    payload = item.to_dict()
    assert payload["requirement_id"] == "FR-001"
    assert payload["coverage"] == 1.0

    pr = PriorityRollup(priority="must", total=2, passed=2, coverage=1.0, fr_count=1)
    assert pr.to_dict()["coverage"] == 1.0

    full = AcceptanceCoverage(
        overall_total=2, overall_passed=2, overall_coverage=1.0,
        by_requirement=[item], by_priority=[pr],
    )
    assert full.to_dict()["overall"]["coverage"] == 1.0
