"""Acceptance coverage calculator (DEVF-070).

Computes per-requirement acceptance coverage from the three earlier
artifacts of the app_from_prd workflow:

- :class:`Requirements` (DEVF-061) — total acceptance criteria per FR
- :class:`VerticalSlicePlan` + :class:`VerticalSliceImplementerResult`
  (DEVF-066/067) — which FRs were satisfied by the accepted slice
- :class:`Backlog` + :class:`BacklogProgress` (DEVF-068/069) — which
  backlog items (and therefore which FRs) ended up accepted

Formula::

    coverage(fr) = passed_ac(fr) / total_ac(fr)

In this release ``passed_ac`` is binary per FR: either every AC counts
as passed (when the slice or a backlog item that references the FR was
accepted) or none do. Future cycles can attribute partial coverage when
the judge starts grading individual criteria.

Output: ``acceptance_coverage.json`` with the overall fraction, the
per-FR breakdown (with attribution to ``slice``/``backlog``/``none``),
and a per-priority roll-up (``must``/``should``/``could``).

This stage is deterministic and always runs — even when the slice and
backlog were skipped, it reports total ACs and 0 passed.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.stages.backlog_generator import Backlog
from devforge.stages.backlog_implementer import BacklogProgress
from devforge.stages.mvp_scope import MvpScope
from devforge.stages.requirements_schema import Requirements
from devforge.stages.vertical_slice_implementer import (
    VerticalSliceImplementerResult,
)
from devforge.stages.vertical_slice_planner import VerticalSlicePlan

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

CoveredBy = str  # "slice" | "backlog" | "none"
Priority = str   # "must" | "should" | "could" | "unclassified"

# Backlog-item statuses that count an item as "delivered" for coverage
# purposes. ``already_in_slice`` is included so an item the slice covered
# does not falsely lower the coverage when its own backlog status is
# non-accept.
_DELIVERED_STATUSES = {"accept", "already_in_slice"}


@dataclass
class FrCoverage:
    requirement_id: str
    title: str = ""
    priority: Priority = "unclassified"
    total: int = 0
    passed: int = 0
    coverage: float = 0.0
    covered_by: CoveredBy = "none"
    source_task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class PriorityRollup:
    priority: Priority
    total: int = 0
    passed: int = 0
    coverage: float = 0.0
    fr_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class AcceptanceCoverage:
    overall_total: int = 0
    overall_passed: int = 0
    overall_coverage: float = 0.0
    by_requirement: list[FrCoverage] = field(default_factory=list)
    by_priority: list[PriorityRollup] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "overall": {
                "passed": self.overall_passed,
                "total": self.overall_total,
                "coverage": round(self.overall_coverage, 4),
            },
            "by_requirement": [fr.to_dict() for fr in self.by_requirement],
            "by_priority": [pr.to_dict() for pr in self.by_priority],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_acceptance_coverage(
    reqs: Requirements,
    scope: MvpScope,
    *,
    slice_plan: VerticalSlicePlan | None = None,
    slice_result: VerticalSliceImplementerResult | None = None,
    backlog: Backlog | None = None,
    backlog_progress: BacklogProgress | None = None,
) -> AcceptanceCoverage:
    """Compute per-FR coverage from the run's artifacts."""
    priority_by_fr = _priority_by_fr(scope)
    accepted_slice_frs = _accepted_slice_requirement_ids(slice_plan, slice_result)
    delivered_backlog_by_fr = _delivered_backlog_by_fr(backlog, backlog_progress)

    by_requirement: list[FrCoverage] = []
    for fr in reqs.functional:
        total = len(fr.acceptance_criteria)
        priority = priority_by_fr.get(fr.id, "unclassified")
        sources = delivered_backlog_by_fr.get(fr.id, [])

        if fr.id in accepted_slice_frs:
            covered_by: CoveredBy = "slice"
            passed = total
        elif sources:
            covered_by = "backlog"
            passed = total
        else:
            covered_by = "none"
            passed = 0

        by_requirement.append(
            FrCoverage(
                requirement_id=fr.id,
                title=fr.title,
                priority=priority,
                total=total,
                passed=passed,
                coverage=(passed / total) if total else 0.0,
                covered_by=covered_by,
                source_task_ids=list(sources),
            )
        )

    overall_total = sum(it.total for it in by_requirement)
    overall_passed = sum(it.passed for it in by_requirement)
    coverage = AcceptanceCoverage(
        overall_total=overall_total,
        overall_passed=overall_passed,
        overall_coverage=(overall_passed / overall_total) if overall_total else 0.0,
        by_requirement=by_requirement,
        by_priority=_rollup_by_priority(by_requirement),
        notes=_notes(
            reqs=reqs,
            by_requirement=by_requirement,
            slice_attributed=bool(accepted_slice_frs),
            backlog_attributed=bool(delivered_backlog_by_fr),
            backlog_progress=backlog_progress,
        ),
    )
    return coverage


def save_acceptance_coverage(
    coverage: AcceptanceCoverage, path: Path
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(coverage.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _priority_by_fr(scope: MvpScope) -> dict[str, Priority]:
    out: dict[str, Priority] = {}
    for fr in scope.must:
        out[fr.id] = "must"
    for fr in scope.should:
        out[fr.id] = "should"
    for fr in scope.could:
        out[fr.id] = "could"
    return out


def _accepted_slice_requirement_ids(
    slice_plan: VerticalSlicePlan | None,
    slice_result: VerticalSliceImplementerResult | None,
) -> set[str]:
    if slice_plan is None or slice_result is None:
        return set()
    if slice_result.decision != "accept":
        return set()
    return set(slice_plan.requirement_ids)


def _delivered_backlog_by_fr(
    backlog: Backlog | None,
    progress: BacklogProgress | None,
) -> dict[str, list[str]]:
    """Map FR id → list of backlog TASK ids that delivered it."""
    out: dict[str, list[str]] = {}
    if backlog is None or progress is None:
        return out
    status_by_task = {it.task_id: it.status for it in progress.items}
    for item in backlog.items:
        if status_by_task.get(item.id) not in _DELIVERED_STATUSES:
            continue
        for fr_id in item.requirement_ids:
            out.setdefault(fr_id, []).append(item.id)
    return out


def _rollup_by_priority(
    by_requirement: list[FrCoverage],
) -> list[PriorityRollup]:
    order: list[Priority] = ["must", "should", "could", "unclassified"]
    buckets: dict[Priority, PriorityRollup] = {
        p: PriorityRollup(priority=p) for p in order
    }
    for fr in by_requirement:
        bucket = buckets.setdefault(fr.priority, PriorityRollup(priority=fr.priority))
        bucket.total += fr.total
        bucket.passed += fr.passed
        bucket.fr_count += 1

    rollups: list[PriorityRollup] = []
    for p in order:
        roll = buckets[p]
        if roll.fr_count == 0:
            continue
        roll.coverage = (roll.passed / roll.total) if roll.total else 0.0
        rollups.append(roll)
    # Preserve any unexpected priority labels at the tail so they're not lost.
    for label, roll in buckets.items():
        if label in order or roll.fr_count == 0:
            continue
        roll.coverage = (roll.passed / roll.total) if roll.total else 0.0
        rollups.append(roll)
    return rollups


def _notes(
    *,
    reqs: Requirements,
    by_requirement: list[FrCoverage],
    slice_attributed: bool,
    backlog_attributed: bool,
    backlog_progress: BacklogProgress | None,
) -> list[str]:
    notes: list[str] = []
    if not reqs.functional:
        notes.append("No functional requirements — coverage is 0/0 by definition.")
    uncovered = [fr for fr in by_requirement if fr.covered_by == "none"]
    if uncovered:
        notes.append(
            "Uncovered requirements: "
            + ", ".join(fr.requirement_id for fr in uncovered)
        )
    if not slice_attributed and not backlog_attributed:
        notes.append(
            "Neither the vertical slice nor the backlog loop produced an "
            "accepted candidate; coverage will read 0% until a real "
            "implementer provider is configured."
        )
    if backlog_progress is not None and backlog_progress.decision == "skipped":
        notes.append(
            f"Backlog implementation was skipped: {backlog_progress.reason}"
        )
    notes.append(
        "Per-FR coverage is binary in this release — a fractional value "
        "appears only when the judge begins grading individual acceptance "
        "criteria (planned for a later DEVF cycle)."
    )
    return notes
