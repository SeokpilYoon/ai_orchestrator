"""UX flow + screen inventory (DEVF-063).

Deterministic projection of :class:`Requirements` (DEVF-061) and the frozen
:class:`MvpScope` (DEVF-062) onto three artifacts:

- ``screen_inventory.json`` — every functional requirement becomes one
  ``Screen`` with a surface ``kind`` of ``ui`` / ``api`` / ``cli`` / ``logical``
- ``user_flows.md`` — every functional requirement becomes one ``UserFlow``
  whose steps are derived from the acceptance criteria
- ``navigation_map.md`` — the order in which surfaces would be exercised,
  grouped by priority (must → should → could) and base path

No LLM dependency. Backend-only PRDs are supported — surfaces without UI
keywords fall back to ``logical``.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.stages.mvp_scope import MvpScope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import FunctionalRequirement, Requirements

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Kind = str  # "ui" | "api" | "cli" | "logical"


@dataclass
class Screen:
    id: str
    kind: Kind
    title: str
    requirement_ids: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class UserFlow:
    id: str
    title: str
    actor: str
    requirement_ids: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    screens: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class UxInventory:
    screens: list[Screen] = field(default_factory=list)
    flows: list[UserFlow] = field(default_factory=list)
    navigation: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "screens": [s.to_dict() for s in self.screens],
            "flows": [f.to_dict() for f in self.flows],
            "navigation": [list(edge) for edge in self.navigation],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Heuristic patterns
# ---------------------------------------------------------------------------

_API_PATTERNS = (
    re.compile(r"\b(GET|POST|PATCH|PUT|DELETE)\b"),
    re.compile(r"(?:^|\s)/[A-Za-z0-9_/{}.-]+"),
    re.compile(r"\b(endpoint|api|http|rest)\b", re.IGNORECASE),
)
_UI_PATTERNS = (
    re.compile(
        r"\b(screen|page|view|dashboard|form|button|click|display|UI|render)\b",
        re.IGNORECASE,
    ),
)
_CLI_PATTERNS = (
    re.compile(
        r"\b(cli|command|terminal|subcommand|flag|argument|stdout|stderr)\b",
        re.IGNORECASE,
    ),
)

_PATH_RX = re.compile(r"(?:^|\s)(/[A-Za-z0-9_/{}.-]+)")
_HTTP_STATUS_RX = re.compile(r"returns?\s+(\d{3})", re.IGNORECASE)
_JSON_OBJECT_RX = re.compile(r"\{[^{}]{0,200}\}")
_TITLE_MAX = 80
_ACCEPTANCE_PLACEHOLDER = "Behavior matches the requirement description"


_ACTOR_FOR_KIND: dict[Kind, str] = {
    "ui": "end user",
    "api": "API client",
    "cli": "operator",
    "logical": "calling code",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_ux_inventory(
    reqs: Requirements, intake: PrdIntake, scope: MvpScope
) -> UxInventory:
    """Project requirements onto an ordered UX inventory."""
    inv = UxInventory()

    # Build screens and flows in PRD order (preserves FR-001..N numbering).
    for i, fr in enumerate(reqs.functional, start=1):
        screen_id = f"SCREEN-{i:03d}"
        flow_id = f"FLOW-{i:03d}"
        kind = _infer_kind(fr)
        actor = _ACTOR_FOR_KIND[kind]
        title = fr.title[:_TITLE_MAX]
        inputs, outputs = _extract_io(fr, kind)
        inv.screens.append(
            Screen(
                id=screen_id,
                kind=kind,
                title=title,
                requirement_ids=[fr.id],
                inputs=inputs,
                outputs=outputs,
                notes=_screen_note(kind),
            )
        )
        steps = list(fr.acceptance_criteria)
        if not steps or steps == [_ACCEPTANCE_PLACEHOLDER]:
            steps = [
                "Trigger the requirement",
                "Verify the described behavior",
            ]
        inv.flows.append(
            UserFlow(
                id=flow_id,
                title=title,
                actor=actor,
                requirement_ids=[fr.id],
                steps=steps,
                screens=[screen_id],
            )
        )

    inv.navigation = _build_navigation(inv.screens, scope)
    inv.notes = _inventory_notes(inv.screens, intake)
    return inv


def save_screen_inventory(inv: UxInventory, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(inv.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def save_user_flows(inv: UxInventory, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# User flows", ""]
    if inv.notes:
        lines.append("> Notes")
        for n in inv.notes:
            lines.append(f"> - {n}")
        lines.append("")
    if not inv.flows:
        lines.append("_No flows derived from the PRD._")
    for flow in inv.flows:
        lines.append(f"## {flow.id} — {flow.title}")
        lines.append("")
        lines.append(f"- Actor: **{flow.actor}**")
        if flow.requirement_ids:
            lines.append(f"- Covers: {', '.join(flow.requirement_ids)}")
        if flow.screens:
            lines.append(f"- Surfaces: {', '.join(flow.screens)}")
        lines.append("")
        lines.append("Steps:")
        for j, step in enumerate(flow.steps, start=1):
            lines.append(f"{j}. {step}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def save_navigation_map(inv: UxInventory, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# Navigation map", ""]
    if not inv.screens:
        lines.append("_No surfaces detected._")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    by_id = {s.id: s for s in inv.screens}
    lines.append("Order of surfaces (must → should → could; grouped by surface kind / base path):")
    lines.append("")
    lines.append("| From | To | Surface kind | Title |")
    lines.append("|---|---|---|---|")
    for src, dst in inv.navigation:
        target = by_id.get(dst)
        kind = target.kind if target else "—"
        title = target.title if target else ""
        lines.append(f"| `{src}` | `{dst}` | {kind} | {title} |")
    lines.append("")

    if inv.notes:
        lines.append("## Notes")
        lines.append("")
        for n in inv.notes:
            lines.append(f"- {n}")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Internal — surface classification
# ---------------------------------------------------------------------------

def _blob_for(fr: FunctionalRequirement) -> str:
    parts = [fr.description, fr.title, *fr.acceptance_criteria]
    return "\n".join(parts)


def _infer_kind(fr: FunctionalRequirement) -> Kind:
    blob = _blob_for(fr)
    if any(p.search(blob) for p in _API_PATTERNS):
        return "api"
    if any(p.search(blob) for p in _UI_PATTERNS):
        return "ui"
    if any(p.search(blob) for p in _CLI_PATTERNS):
        return "cli"
    return "logical"


def _screen_note(kind: Kind) -> str:
    if kind == "api":
        return "HTTP surface — verify request/response contract"
    if kind == "ui":
        return "Visual surface — verify rendering and interaction"
    if kind == "cli":
        return "CLI surface — verify subcommand exit code and output"
    return "Internal surface — verify behavior via unit/integration tests"


def _extract_io(fr: FunctionalRequirement, kind: Kind) -> tuple[list[str], list[str]]:
    blob = _blob_for(fr)
    inputs: list[str] = []
    outputs: list[str] = []
    seen_in: set[str] = set()
    seen_out: set[str] = set()

    for path in _PATH_RX.findall(blob):
        if path not in seen_in:
            seen_in.add(path)
            inputs.append(path)

    for status in _HTTP_STATUS_RX.findall(blob):
        marker = f"HTTP {status}"
        if marker not in seen_out:
            seen_out.add(marker)
            outputs.append(marker)

    for chunk in _JSON_OBJECT_RX.findall(blob):
        # Body shape gives input/output, depending on context — be conservative
        # and tag everything as ``input`` (the request body for APIs, the
        # rendered struct for UI/CLI surfaces).
        snippet = chunk.strip()
        if snippet and snippet not in seen_in:
            seen_in.add(snippet)
            inputs.append(snippet)

    # Suppress noise for non-API kinds: the path/status heuristics above are
    # mostly meaningful for ``api``. For UI/CLI/logical we keep them only when
    # they appear (which is rare).
    if kind == "logical" and not inputs and not outputs:
        return [], []
    return inputs, outputs


# ---------------------------------------------------------------------------
# Internal — navigation
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"must": 0, "should": 1, "could": 2}


def _navigation_key(screen: Screen, priority: int) -> tuple[int, str, str]:
    """Sort screens by (priority, kind, base path) so that
    grouped surfaces appear together."""
    base = ""
    if screen.inputs:
        first = screen.inputs[0]
        # Take the first path segment as the grouping key for APIs.
        if first.startswith("/"):
            stripped = first.lstrip("/").split("/", 1)[0]
            base = "/" + stripped
    return (priority, screen.kind, base)


def _build_navigation(
    screens: list[Screen], scope: MvpScope
) -> list[tuple[str, str]]:
    """Order screens by (priority bucket, kind, base path) and emit edges.

    The first edge is always ``("START", first_screen.id)`` so consumers can
    see the entry point without inspecting the screens list separately.
    """
    if not screens:
        return []

    priority_for: dict[str, int] = {}
    for fr in scope.must:
        priority_for[fr.id] = _PRIORITY_ORDER["must"]
    for fr in scope.should:
        priority_for[fr.id] = _PRIORITY_ORDER["should"]
    for fr in scope.could:
        priority_for[fr.id] = _PRIORITY_ORDER["could"]

    decorated: list[tuple[tuple[int, str, str], int, Screen]] = []
    for original_idx, screen in enumerate(screens):
        # Default to "must" when an FR isn't classified (defensive — shouldn't
        # happen in practice because mvp_scope absorbs unknown priorities).
        pri = (
            priority_for.get(screen.requirement_ids[0], _PRIORITY_ORDER["must"])
            if screen.requirement_ids
            else _PRIORITY_ORDER["must"]
        )
        decorated.append((_navigation_key(screen, pri), original_idx, screen))

    decorated.sort(key=lambda triple: (triple[0], triple[1]))
    ordered = [triple[2] for triple in decorated]

    edges: list[tuple[str, str]] = [("START", ordered[0].id)]
    for prev, curr in zip(ordered, ordered[1:], strict=False):
        edges.append((prev.id, curr.id))
    return edges


# ---------------------------------------------------------------------------
# Internal — notes
# ---------------------------------------------------------------------------

def _inventory_notes(screens: list[Screen], intake: PrdIntake) -> list[str]:
    notes: list[str] = []
    if not screens:
        notes.append("No screens derived from the requirements.")
        return notes
    kinds = {s.kind for s in screens}
    if "ui" not in kinds:
        notes.append("No UI surface detected — flows are backend-oriented.")
    if kinds == {"logical"}:
        notes.append(
            "No external surface — flows describe internal procedures only."
        )
    # Surface-kind heuristic is keyword-based; remind the caller.
    notes.append(
        "Surface kind classification is heuristic; revise screen_inventory.json "
        "if a requirement was misclassified."
    )
    # Echo a high-level reminder that target users may be assumed.
    if not intake.target_users:
        notes.append(
            "Target users were not declared in the PRD; flows use the default "
            "actor for each surface kind."
        )
    return notes
