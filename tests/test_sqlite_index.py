"""Unit tests for the SQLite index (DEVF-080)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from devforge.core.run_context import create_run_context
from devforge.core.sqlite_index import SqliteIndex, index_path_for_run
from devforge.core.state_store import StateStore

# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def test_schema_creates_five_tables(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    SqliteIndex(db)
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r[0] for r in rows}
    for name in {"runs", "steps", "candidates", "evaluations", "provider_status"}:
        assert name in names


def test_schema_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    SqliteIndex(db)
    # Re-opening must not raise nor wipe existing rows.
    idx = SqliteIndex(db)
    idx.upsert_run(run_id="r1", workflow="feature", status="pending")
    SqliteIndex(db)
    assert idx.get_run("r1") is not None


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------

def test_upsert_run_then_update_status(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "state.db")
    idx.upsert_run(
        run_id="r1", workflow="feature", status="pending",
        started_at="2026-01-01T00:00:00+00:00",
    )
    idx.upsert_run(
        run_id="r1", workflow="feature", status="completed",
        completed_at="2026-01-01T00:00:30+00:00",
    )
    row = idx.get_run("r1")
    assert row is not None
    assert row["status"] == "completed"
    # started_at preserved by COALESCE.
    assert row["started_at"] == "2026-01-01T00:00:00+00:00"
    assert row["completed_at"] == "2026-01-01T00:00:30+00:00"


def test_upsert_step_preserves_started_at(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "state.db")
    idx.upsert_run(run_id="r1", workflow="feature", status="running")
    idx.upsert_step(
        run_id="r1", stage_id="plan", status="running",
        started_at="2026-01-01T00:00:01+00:00",
    )
    idx.upsert_step(
        run_id="r1", stage_id="plan", status="completed",
        completed_at="2026-01-01T00:00:09+00:00",
    )
    steps = idx.get_steps("r1")
    assert len(steps) == 1
    assert steps[0]["status"] == "completed"
    assert steps[0]["started_at"] == "2026-01-01T00:00:01+00:00"
    assert steps[0]["completed_at"] == "2026-01-01T00:00:09+00:00"


def test_upsert_candidate_overwrites_score(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "state.db")
    idx.upsert_run(run_id="r1", workflow="feature", status="running")
    idx.upsert_candidate(
        run_id="r1", candidate_id="mock_impl", provider_id="mock_impl",
        decision="revise", score=60.0, decision_ref="candidates/mock_impl/decision.json",
    )
    idx.upsert_candidate(
        run_id="r1", candidate_id="mock_impl", provider_id="mock_impl",
        decision="accept", score=88.0, decision_ref="candidates/mock_impl/decision.json",
    )
    cands = idx.get_candidates("r1")
    assert len(cands) == 1
    assert cands[0]["decision"] == "accept"
    assert cands[0]["score"] == 88.0


def test_upsert_evaluation_round_trips_details(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "state.db")
    idx.upsert_run(run_id="r1", workflow="feature", status="completed")
    idx.upsert_evaluation(
        run_id="r1", candidate_id="c1", kind="score",
        score=80.0, details={"breakdown": {"build_pass": 25}},
    )
    rows = idx.get_evaluations("r1")
    assert len(rows) == 1
    assert rows[0]["score"] == 80.0
    assert rows[0]["details"] == {"breakdown": {"build_pass": 25}}


def test_upsert_provider_status_sets_last_checked(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "state.db")
    idx.upsert_run(run_id="r1", workflow="feature", status="running")
    idx.upsert_provider_status(
        run_id="r1", provider_id="mock_impl",
        status="available", healthy=True, detail="auth=none",
    )
    rows = idx.get_provider_status("r1")
    assert rows[0]["status"] == "available"
    assert rows[0]["healthy"] == 1
    assert rows[0]["last_checked"]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def test_list_runs_filters_by_workflow(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "state.db")
    idx.upsert_run(run_id="r1", workflow="feature", status="completed",
                   started_at="2026-01-01T00:00:00+00:00")
    idx.upsert_run(run_id="r2", workflow="app_from_prd", status="completed",
                   started_at="2026-01-02T00:00:00+00:00")
    idx.upsert_run(run_id="r3", workflow="feature", status="failed",
                   started_at="2026-01-03T00:00:00+00:00")
    feature_runs = idx.list_runs(workflow="feature")
    assert [r["run_id"] for r in feature_runs] == ["r3", "r1"]
    assert len(idx.list_runs()) == 3


# ---------------------------------------------------------------------------
# StateStore integration — JSON writes mirror into SQLite automatically
# ---------------------------------------------------------------------------

def test_statestore_mirrors_run_and_steps_into_sqlite(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    ctx = create_run_context(project_root, workflow="feature", input_path=None)
    store = StateStore(ctx.root)
    store.init_run(workflow="feature", input_ref=None, stages=["a", "b"])
    store.save_step("a", "running")
    store.save_step("a", "completed", artifact_ref="x.json")
    store.update_run_status("completed")

    db = project_root / ".orchestrator" / "state.db"
    assert db.exists()
    idx = SqliteIndex(db)
    runs = idx.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == ctx.run_id
    assert runs[0]["status"] == "completed"
    steps = idx.get_steps(ctx.run_id)
    by_stage = {s["stage_id"]: s for s in steps}
    assert by_stage["a"]["status"] == "completed"
    assert by_stage["a"]["artifact_ref"] == "x.json"
    assert by_stage["b"]["status"] == "pending"


def test_statestore_mirrors_candidates_and_final_decision(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    ctx = create_run_context(project_root, workflow="feature", input_path=None)
    store = StateStore(ctx.root)
    store.init_run(workflow="feature", input_ref=None, stages=["a"])
    store.save_candidate(
        candidate_id="mock_impl",
        provider_id="mock_impl",
        decision="accept",
        score=85.0,
        decision_ref="candidates/mock_impl/decision.json",
    )
    store.save_final_decision(
        decision_ref="decision.json", chosen_candidate="mock_impl"
    )

    idx = SqliteIndex(project_root / ".orchestrator" / "state.db")
    cands = idx.get_candidates(ctx.run_id)
    assert cands[0]["candidate_id"] == "mock_impl"
    assert cands[0]["score"] == 85.0
    run = idx.get_run(ctx.run_id)
    assert run["chosen_candidate"] == "mock_impl"
    assert run["final_decision_ref"] == "decision.json"


def test_terminal_run_status_mirrors_evaluations_from_candidate_dir(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    ctx = create_run_context(project_root, workflow="feature", input_path=None)
    cand_dir = ctx.candidate_dir("mock_impl")
    (cand_dir / "score.json").write_text(
        json.dumps({"score": 80.0, "contributions": {"tests_pass": 25}}),
        encoding="utf-8",
    )
    (cand_dir / "decision.json").write_text(
        json.dumps({"verdict": "accept", "reason": "score_threshold_met",
                    "score": 80.0, "details": {}}),
        encoding="utf-8",
    )
    (cand_dir / "validation.json").write_text(
        json.dumps({"results": {"test": {"passed": True}, "lint": {"passed": True}}}),
        encoding="utf-8",
    )

    store = StateStore(ctx.root)
    store.init_run(workflow="feature", input_ref=None, stages=["a"])
    store.update_run_status("completed")

    idx = SqliteIndex(project_root / ".orchestrator" / "state.db")
    rows = idx.get_evaluations(ctx.run_id)
    by_kind = {r["kind"]: r for r in rows}
    assert "score" in by_kind and by_kind["score"]["score"] == 80.0
    assert by_kind["decision"]["passed"] == 1
    assert by_kind["validation"]["passed"] == 1


def test_index_path_for_run_resolves_to_project_state_db(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    ctx = create_run_context(project_root, workflow="feature", input_path=None)
    assert (
        index_path_for_run(ctx.root)
        == (project_root / ".orchestrator" / "state.db").resolve()
    )
