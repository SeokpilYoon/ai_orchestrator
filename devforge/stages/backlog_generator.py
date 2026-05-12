"""Backlog generator (DEVF-068).

Deterministic projection of the foundation artifacts onto a flat task
backlog suitable for an iterative implementation loop (DEVF-069). The
schema mirrors ``docs/plan/03 §DEVF-068``::

    {
      "items": [
        {
          "id": "TASK-001",
          "title": "...",
          "requirement_ids": ["FR-001"],
          "acceptance_criteria": ["..."],
          "priority": "P0|P1|P2",
          "estimated_complexity": "S|M|L",
          "dependencies": ["TASK-000"]
        }
      ]
    }

One backlog item per functional requirement. Priority is taken from the
MVP scope classification (``must→P0``, ``should→P1``, ``could→P2``).
Complexity is a heuristic over the FR's API operations, data entities,
and acceptance-criteria count. Dependencies link items that share a data
entity, preferring lower-priority items to wait on the higher-priority
producer of the same entity.

No LLM dependency. The output traces every functional requirement to a
backlog item, which is the DoD for this task.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.stages.architecture_generator import Architecture
from devforge.stages.mvp_scope import MvpScope
from devforge.stages.requirements_schema import FunctionalRequirement, Requirements
from devforge.stages.ux_flow import UxInventory

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Priority = str  # "P0" | "P1" | "P2"
Complexity = str  # "S" | "M" | "L"

_PRIORITY_FOR = {"must": "P0", "should": "P1", "could": "P2"}
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


@dataclass
class BacklogItem:
    id: str
    title: str
    requirement_ids: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    priority: Priority = "P1"
    estimated_complexity: Complexity = "M"
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Backlog:
    items: list[BacklogItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [item.to_dict() for item in self.items],
            "notes": list(self.notes),
        }


class BacklogGeneratorError(Exception):
    """Raised when a backlog cannot be derived from the upstream artifacts."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_backlog(
    reqs: Requirements,
    scope: MvpScope,
    inventory: UxInventory,
    arch: Architecture,
) -> Backlog:
    """Project functional requirements onto a backlog of trace-able items."""
    if not reqs.functional:
        raise BacklogGeneratorError(
            "no functional requirements — cannot build a backlog"
        )

    priority_by_fr = _priority_by_fr(scope)

    # Build items in the same order requirements appear, with stable TASK-NNN ids.
    items: list[BacklogItem] = []
    item_by_fr: dict[str, BacklogItem] = {}
    for idx, fr in enumerate(reqs.functional, start=1):
        item = BacklogItem(
            id=f"TASK-{idx:03d}",
            title=fr.title or fr.id,
            requirement_ids=[fr.id],
            acceptance_criteria=list(fr.acceptance_criteria),
            priority=priority_by_fr.get(fr.id, "P0"),
            estimated_complexity=_estimate_complexity(fr, arch),
        )
        items.append(item)
        item_by_fr[fr.id] = item

    # Derive dependencies: items that share a data entity wait on the
    # highest-priority producer of that entity (typically the must-have
    # creator before a should/could consumer).
    _attach_entity_dependencies(items, item_by_fr, arch)

    backlog = Backlog(items=items, notes=_backlog_notes(items, inventory, arch))
    return backlog


def save_backlog(backlog: Backlog, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(backlog.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Internal — priority + complexity
# ---------------------------------------------------------------------------

def _priority_by_fr(scope: MvpScope) -> dict[str, str]:
    out: dict[str, str] = {}
    for fr in scope.must:
        out[fr.id] = _PRIORITY_FOR["must"]
    for fr in scope.should:
        out[fr.id] = _PRIORITY_FOR["should"]
    for fr in scope.could:
        out[fr.id] = _PRIORITY_FOR["could"]
    return out


def _estimate_complexity(fr: FunctionalRequirement, arch: Architecture) -> Complexity:
    """Heuristic: ops + entities + acceptance-criteria count → S / M / L.

    - S: trivial single-surface change (≤1 op, ≤1 entity, ≤1 AC)
    - L: multi-surface or many criteria (≥3 ops or ≥3 entities or ≥5 AC)
    - M: everything in between
    """
    ops_for_fr = sum(1 for op in arch.operations if fr.id in op.requirement_ids)
    entities_for_fr = sum(
        1 for entity in arch.entities if fr.id in entity.sourced_from
    )
    ac_count = len(fr.acceptance_criteria)

    if ops_for_fr >= 3 or entities_for_fr >= 3 or ac_count >= 5:
        return "L"
    if ops_for_fr <= 1 and entities_for_fr <= 1 and ac_count <= 1:
        return "S"
    return "M"


# ---------------------------------------------------------------------------
# Internal — dependencies
# ---------------------------------------------------------------------------

def _attach_entity_dependencies(
    items: list[BacklogItem],
    item_by_fr: dict[str, BacklogItem],
    arch: Architecture,
) -> None:
    """Link items that share a data entity.

    For each entity, sort its sourcing FRs by priority (P0 < P1 < P2) then
    by their backlog position. Every later FR in that order depends on the
    *first* FR (highest priority, earliest position) — that item is the
    "producer". This avoids ping-pong dependencies while still tracing
    the ordering an implementer should respect.
    """
    for entity in arch.entities:
        sourcing = [fr_id for fr_id in entity.sourced_from if fr_id in item_by_fr]
        if len(sourcing) < 2:
            continue
        sourcing.sort(
            key=lambda fr_id: (
                _PRIORITY_ORDER.get(item_by_fr[fr_id].priority, 9),
                _task_index(item_by_fr[fr_id].id),
            )
        )
        producer = item_by_fr[sourcing[0]]
        for fr_id in sourcing[1:]:
            consumer = item_by_fr[fr_id]
            if producer.id == consumer.id:
                continue
            if producer.id not in consumer.dependencies:
                consumer.dependencies.append(producer.id)


def _task_index(task_id: str) -> int:
    try:
        return int(task_id.split("-", 1)[1])
    except (IndexError, ValueError):
        return 9999


# ---------------------------------------------------------------------------
# Internal — notes
# ---------------------------------------------------------------------------

def _backlog_notes(
    items: list[BacklogItem], inventory: UxInventory, arch: Architecture
) -> list[str]:
    notes: list[str] = []
    p0 = sum(1 for it in items if it.priority == "P0")
    if p0 == 0:
        notes.append(
            "No P0 items — every requirement was classified as should/could. "
            "Verify priorities in the PRD before running the backlog loop."
        )
    if not arch.entities:
        notes.append(
            "No data entities derived from the architecture; backlog items "
            "have no entity-based dependencies."
        )
    if not inventory.flows:
        notes.append(
            "No UX flows available; backlog items reflect requirements only, "
            "not user-journey ordering."
        )
    notes.append(
        "Backlog items are deterministic projections of requirements + MVP "
        "scope. Re-classify FRs in the PRD if priorities need adjusting."
    )
    return notes
