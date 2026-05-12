"""Vertical slice planner (DEVF-066).

Deterministic projection of the foundation artifacts onto a single, thin
end-to-end user journey. The downstream implementer (DEVF-067) targets the
acceptance criteria emitted here instead of "the whole MVP" — this is the
constraint that keeps the AI worker from drifting.

Inputs (all already produced by earlier app_from_prd stages and available
in-memory in :mod:`devforge.stages.app_from_prd_driver`):

- :class:`Requirements`        — full FR/NFR inventory (DEVF-061)
- :class:`PrdIntake`           — original PRD intake (DEVF-060)
- :class:`MvpScope`            — must / should / could classification (DEVF-062)
- :class:`UxInventory`         — screens + flows + navigation order (DEVF-063)
- :class:`Architecture`        — entities + API operations (DEVF-064)

Output: a single :class:`VerticalSlicePlan` saved as
``vertical_slice_plan.json``. The schema matches
``docs/plan/03 §DEVF-066``.

Selection algorithm
-------------------

1. Build a *priority pool* of FR ids: ``scope.must`` first, falling back to
   ``scope.should`` then ``scope.could`` so a PRD with no must-haves still
   produces a slice.
2. Walk flows in **navigation order** (which is already priority + base-path
   sorted by ``_build_navigation`` in :mod:`devforge.stages.ux_flow`) and
   keep those whose FR ids intersect the priority pool.
3. The first such flow becomes the **anchor**.
4. Greedily attach subsequent candidates that share at least one data
   entity with the anchor (via ``Entity.sourced_from`` ∩ flow FR ids).
   Cap at three flows total to keep the slice thin.

No LLM dependency. Identical determinism guarantees to the rest of the
``app_from_prd`` pipeline.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.stages.architecture_generator import (
    ApiOperation,
    Architecture,
    Entity,
)
from devforge.stages.mvp_scope import MvpScope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import Requirements
from devforge.stages.ux_flow import Screen, UserFlow, UxInventory

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

_TITLE_MAX = 80
_MAX_FLOWS = 3


@dataclass
class VerticalSlicePlan:
    vertical_slice_name: str
    user_journey: list[str] = field(default_factory=list)
    screens: list[str] = field(default_factory=list)
    api_endpoints: list[str] = field(default_factory=list)
    data_entities: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class VerticalSlicePlannerError(Exception):
    """Raised when no slice can be derived from the upstream artifacts."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan_vertical_slice(
    reqs: Requirements,
    intake: PrdIntake,
    scope: MvpScope,
    inventory: UxInventory,
    arch: Architecture,
) -> VerticalSlicePlan:
    """Select a thin end-to-end slice and emit its acceptance criteria."""
    if not inventory.flows:
        raise VerticalSlicePlannerError(
            "no user flows available — cannot plan a vertical slice"
        )

    priority_pool, priority_label = _priority_pool(scope)
    if not priority_pool:
        raise VerticalSlicePlannerError(
            "no functional requirements at any priority — cannot plan a slice"
        )

    nav_flows = _flows_in_navigation_order(inventory)
    candidates = [
        flow
        for flow in nav_flows
        if any(rid in priority_pool for rid in flow.requirement_ids)
    ]
    if not candidates:
        raise VerticalSlicePlannerError(
            f"no flows matched the priority pool ({priority_label})"
        )

    anchor = candidates[0]
    anchor_entities = _entities_touched_by(anchor.requirement_ids, arch.entities)

    selected: list[UserFlow] = [anchor]
    for flow in candidates[1:]:
        if len(selected) >= _MAX_FLOWS:
            break
        if not anchor_entities:
            # No anchoring entity — keep the slice tight, do not add more flows.
            break
        flow_entities = _entities_touched_by(flow.requirement_ids, arch.entities)
        if anchor_entities & flow_entities:
            selected.append(flow)

    selected_fr_ids = _ordered_union(
        rid for flow in selected for rid in flow.requirement_ids
    )

    screen_by_id: dict[str, Screen] = {s.id: s for s in inventory.screens}

    journey = _build_journey(selected, screen_by_id)
    screens = _ordered_union(sid for flow in selected for sid in flow.screens)
    api_endpoints = _api_endpoints_for(arch.operations, selected_fr_ids)
    data_entities = _data_entities_for(arch.entities, selected_fr_ids)
    acceptance = _ordered_union(step for flow in selected for step in flow.steps)

    plan = VerticalSlicePlan(
        vertical_slice_name=_slice_name(anchor, len(selected)),
        user_journey=journey,
        screens=screens,
        api_endpoints=api_endpoints,
        data_entities=data_entities,
        acceptance_criteria=acceptance,
        requirement_ids=selected_fr_ids,
        notes=_slice_notes(
            priority_label,
            selected,
            anchor_entities,
            data_entities,
            api_endpoints,
            intake,
        ),
    )
    return plan


def save_vertical_slice_plan(plan: VerticalSlicePlan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Internal — selection helpers
# ---------------------------------------------------------------------------

def _priority_pool(scope: MvpScope) -> tuple[set[str], str]:
    """Return the FR ids to anchor the slice on, plus a human label."""
    if scope.must:
        return {fr.id for fr in scope.must}, "must"
    if scope.should:
        return {fr.id for fr in scope.should}, "should (no must-have FRs)"
    if scope.could:
        return {fr.id for fr in scope.could}, "could (no must- or should-have FRs)"
    return set(), "empty"


def _flows_in_navigation_order(inventory: UxInventory) -> list[UserFlow]:
    """Order flows by the first time their screens appear in inventory.navigation.

    ``UxInventory.navigation`` is already priority-sorted (must → should → could)
    by :func:`devforge.stages.ux_flow._build_navigation`. We walk the edges,
    collect screen ids in order, then map back to the owning flow. Flows whose
    screens never appear in the navigation list (defensive — should not happen)
    fall through to the end in their original order.
    """
    flow_by_screen: dict[str, UserFlow] = {}
    for flow in inventory.flows:
        for sid in flow.screens:
            flow_by_screen.setdefault(sid, flow)

    seen_flow_ids: set[str] = set()
    ordered: list[UserFlow] = []
    for src, dst in inventory.navigation:
        for sid in (src, dst):
            if sid == "START":
                continue
            flow = flow_by_screen.get(sid)
            if flow is None or flow.id in seen_flow_ids:
                continue
            seen_flow_ids.add(flow.id)
            ordered.append(flow)

    for flow in inventory.flows:
        if flow.id not in seen_flow_ids:
            seen_flow_ids.add(flow.id)
            ordered.append(flow)
    return ordered


def _entities_touched_by(fr_ids: list[str], entities: list[Entity]) -> set[str]:
    fr_set = set(fr_ids)
    return {
        entity.name
        for entity in entities
        if fr_set.intersection(entity.sourced_from)
    }


# ---------------------------------------------------------------------------
# Internal — output rendering
# ---------------------------------------------------------------------------

def _slice_name(anchor: UserFlow, n_flows: int) -> str:
    base = anchor.title.strip() or anchor.id
    base = base[:_TITLE_MAX]
    if n_flows > 1:
        return f"{base} + {n_flows - 1} more"
    return base


def _build_journey(
    selected: list[UserFlow], screen_by_id: dict[str, Screen]
) -> list[str]:
    """Render the journey as: ``At <screen>:`` followed by the flow's steps,
    deduped while preserving order."""
    out: list[str] = []
    seen: set[str] = set()

    def _push(line: str) -> None:
        if line and line not in seen:
            seen.add(line)
            out.append(line)

    for flow in selected:
        for sid in flow.screens:
            screen = screen_by_id.get(sid)
            if screen is None:
                continue
            label = f"At {screen.title}".strip()
            if screen.kind and screen.kind != "logical":
                label = f"{label} ({screen.kind})"
            _push(label)
        for step in flow.steps:
            _push(step)
    return out


def _api_endpoints_for(
    operations: list[ApiOperation], selected_fr_ids: list[str]
) -> list[str]:
    fr_set = set(selected_fr_ids)
    out: list[str] = []
    seen: set[str] = set()
    for op in operations:
        if not fr_set.intersection(op.requirement_ids):
            continue
        rendered = f"{op.method.upper()} {op.path}"
        if rendered not in seen:
            seen.add(rendered)
            out.append(rendered)
    return out


def _data_entities_for(
    entities: list[Entity], selected_fr_ids: list[str]
) -> list[str]:
    fr_set = set(selected_fr_ids)
    out: list[str] = []
    for entity in entities:
        if fr_set.intersection(entity.sourced_from) and entity.name not in out:
            out.append(entity.name)
    return out


def _ordered_union(items: object) -> list[str]:
    """Deduplicate an iterable of strings while preserving first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:  # type: ignore[assignment]
        if not isinstance(raw, str):
            continue
        if raw and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def _slice_notes(
    priority_label: str,
    selected: list[UserFlow],
    anchor_entities: set[str],
    data_entities: list[str],
    api_endpoints: list[str],
    intake: PrdIntake,
) -> list[str]:
    notes: list[str] = []
    if priority_label != "must":
        notes.append(
            f"Priority fallback applied: slice anchored on {priority_label} "
            f"requirements. Review the PRD if must-have FRs were expected."
        )
    if len(selected) == 1:
        notes.append(
            "Single-flow slice — no other candidate flow shared a data entity "
            "with the anchor, or there was only one candidate."
        )
    if not anchor_entities:
        notes.append(
            "Anchor flow does not touch any data entity from the architecture; "
            "the slice is surface-only. Add resource paths to the PRD if "
            "persistence is in scope."
        )
    if not api_endpoints:
        notes.append(
            "No API operations match the slice — the implementer (DEVF-067) "
            "will work against in-process logic only."
        )
    if not data_entities:
        notes.append(
            "No data entities match the slice — verify the architecture "
            "extracted resource paths from the requirements."
        )
    if not intake.target_users:
        notes.append(
            "Target users were not declared in the PRD; the journey uses the "
            "default actor for each surface kind."
        )
    return notes
