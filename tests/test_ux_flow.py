from __future__ import annotations

import json
from pathlib import Path

from devforge.stages.mvp_scope import freeze_mvp_scope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import (
    FunctionalRequirement,
    Requirements,
)
from devforge.stages.ux_flow import (
    build_ux_inventory,
    save_navigation_map,
    save_screen_inventory,
    save_user_flows,
)


def _fr(
    idx: int,
    *,
    title: str,
    priority: str = "must",
    acceptance: list[str] | None = None,
    description: str | None = None,
    test_strategy: str = "manual",
) -> FunctionalRequirement:
    return FunctionalRequirement(
        id=f"FR-{idx:03d}",
        title=title,
        description=description or title,
        priority=priority,
        acceptance_criteria=acceptance or ["does X"],
        test_strategy=test_strategy,
    )


def _bundle(*frs: FunctionalRequirement, target_users: list[str] | None = None):
    intake = PrdIntake(target_users=target_users if target_users is not None else ["devs"])
    reqs = Requirements(functional=list(frs))
    scope = freeze_mvp_scope(reqs, intake)
    return reqs, intake, scope


# ---------------------------------------------------------------------------
# Surface classification
# ---------------------------------------------------------------------------

def test_api_only_prd_builds_api_screens() -> None:
    reqs, intake, scope = _bundle(
        _fr(1, title="Add task",
            description="POST /tasks accepts JSON and returns 201"),
        _fr(2, title="List tasks",
            description="GET /tasks returns the list as JSON"),
    )
    inv = build_ux_inventory(reqs, intake, scope)
    assert all(s.kind == "api" for s in inv.screens)
    assert all(f.actor == "API client" for f in inv.flows)


def test_ui_keywords_yield_ui_screens() -> None:
    reqs, intake, scope = _bundle(
        _fr(1, title="Dashboard view",
            description="User opens the dashboard screen and clicks the refresh button"),
    )
    inv = build_ux_inventory(reqs, intake, scope)
    assert inv.screens[0].kind == "ui"
    assert inv.flows[0].actor == "end user"


def test_cli_keywords_yield_cli_screens() -> None:
    reqs, intake, scope = _bundle(
        _fr(1, title="Run subcommand",
            description="The CLI subcommand executes when invoked from the terminal with a --flag argument"),
    )
    inv = build_ux_inventory(reqs, intake, scope)
    assert inv.screens[0].kind == "cli"
    assert inv.flows[0].actor == "operator"


def test_backend_only_falls_back_to_logical() -> None:
    reqs, intake, scope = _bundle(
        _fr(1, title="Compute totals",
            description="The pricing module sums values for an order"),
    )
    inv = build_ux_inventory(reqs, intake, scope)
    assert inv.screens[0].kind == "logical"
    assert inv.flows[0].actor == "calling code"
    assert any("No UI surface" in n for n in inv.notes)
    assert any("No external surface" in n for n in inv.notes)


# ---------------------------------------------------------------------------
# Inputs / outputs / navigation
# ---------------------------------------------------------------------------

def test_inputs_outputs_extracted_for_api_path() -> None:
    reqs, intake, scope = _bundle(
        _fr(
            1,
            title="Add task",
            description="POST /tasks returns 201",
            acceptance=['{"title": "buy milk"}'],
        ),
    )
    inv = build_ux_inventory(reqs, intake, scope)
    screen = inv.screens[0]
    assert "/tasks" in screen.inputs
    assert "HTTP 201" in screen.outputs
    # JSON object body is preserved as an input.
    assert any("title" in i for i in screen.inputs)


def test_must_should_could_order_in_navigation() -> None:
    reqs, intake, scope = _bundle(
        _fr(1, title="Could thing",
            description="GET /low (priority later)", priority="could"),
        _fr(2, title="Should thing",
            description="GET /mid", priority="should"),
        _fr(3, title="Must thing",
            description="GET /high", priority="must"),
    )
    inv = build_ux_inventory(reqs, intake, scope)
    # Edges should walk through screens in must → should → could order.
    # First edge is START → screen_for_must (FR-003 → SCREEN-003).
    assert inv.navigation[0] == ("START", "SCREEN-003")
    visited = [edge[1] for edge in inv.navigation]
    assert visited == ["SCREEN-003", "SCREEN-002", "SCREEN-001"]


# ---------------------------------------------------------------------------
# Save round-trips
# ---------------------------------------------------------------------------

def test_save_round_trip_screen_inventory(tmp_path: Path) -> None:
    reqs, intake, scope = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201"),
        _fr(2, title="List tasks", description="GET /tasks returns JSON"),
    )
    inv = build_ux_inventory(reqs, intake, scope)
    out = tmp_path / "screen_inventory.json"
    save_screen_inventory(inv, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {s["id"] for s in data["screens"]} == {"SCREEN-001", "SCREEN-002"}
    assert "navigation" in data
    assert "notes" in data


def test_save_round_trip_user_flows_navigation(tmp_path: Path) -> None:
    reqs, intake, scope = _bundle(
        _fr(1, title="Add task", description="POST /tasks returns 201",
            acceptance=["POST accepts JSON", "Returns 201"]),
    )
    inv = build_ux_inventory(reqs, intake, scope)

    flows_path = tmp_path / "user_flows.md"
    save_user_flows(inv, flows_path)
    flows_md = flows_path.read_text(encoding="utf-8")
    assert "# User flows" in flows_md
    assert "FLOW-001" in flows_md
    assert "Actor:" in flows_md
    assert "1. POST accepts JSON" in flows_md

    nav_path = tmp_path / "navigation_map.md"
    save_navigation_map(inv, nav_path)
    nav_md = nav_path.read_text(encoding="utf-8")
    assert "# Navigation map" in nav_md
    assert "START" in nav_md
    assert "SCREEN-001" in nav_md
