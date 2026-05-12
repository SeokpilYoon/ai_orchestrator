"""Tests for the dashboard backend (DEVF-082).

FastAPI + httpx are optional dev deps — skip the whole module when
they aren't installed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from devforge.core.run_context import create_run_context  # noqa: E402
from devforge.core.sqlite_index import SqliteIndex  # noqa: E402
from devforge.core.state_store import StateStore  # noqa: E402
from devforge.dashboard.backend import create_app  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A bare project root + minimal SQLite index with one run."""
    root = tmp_path / "project"
    root.mkdir()
    ctx = create_run_context(root, workflow="feature", input_path=None)
    store = StateStore(ctx.root)
    store.init_run(workflow="feature", input_ref=None, stages=["plan", "implement"])
    store.save_step("plan", "completed", artifact_ref="implementation_plan.json")
    store.save_step("implement", "running")
    store.save_candidate(
        candidate_id="mock_impl",
        provider_id="mock_impl",
        decision="accept",
        score=85.0,
        decision_ref="candidates/mock_impl/decision.json",
    )
    store.record_provider_status(
        provider_id="mock_impl",
        status="available",
        healthy=True,
        detail="auth=none",
    )
    # Sample diff for the diff route.
    cand_dir = ctx.candidate_dir("mock_impl")
    (cand_dir / "diff.patch").write_text(
        "diff --git a/src/x.py b/src/x.py\n+++ added\n",
        encoding="utf-8",
    )
    (cand_dir / "decision.json").write_text(
        json.dumps({"verdict": "accept", "reason": "ok", "score": 85.0, "details": {}}),
        encoding="utf-8",
    )
    # Save a known run_id under the project for the client to query.
    project_state = {"run_id": ctx.run_id, "root": str(ctx.root)}
    (root / "_test_run.json").write_text(json.dumps(project_state), encoding="utf-8")
    return root


def _run_id(project: Path) -> str:
    return json.loads((project / "_test_run.json").read_text(encoding="utf-8"))["run_id"]


@pytest.fixture
def client(project: Path):
    return TestClient(create_app(project))


# ---------------------------------------------------------------------------
# Liveness + index
# ---------------------------------------------------------------------------

def test_healthz_returns_ok(client) -> None:
    resp = client.get("/api/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_root_html_lists_api_routes(client) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "/api/runs" in body
    assert "/api/healthz" in body


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def test_list_runs_returns_seeded_run(client, project) -> None:
    rid = _run_id(project)
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(r["run_id"] == rid for r in items)


def test_list_runs_filters_by_workflow(client, project) -> None:
    _run_id(project)
    resp = client.get("/api/runs", params={"workflow": "feature"})
    assert resp.status_code == 200
    assert all(r["workflow"] == "feature" for r in resp.json()["items"])

    resp = client.get("/api/runs", params={"workflow": "missing"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_run_detail_includes_steps_and_candidates(client, project) -> None:
    rid = _run_id(project)
    resp = client.get(f"/api/runs/{rid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run"]["run_id"] == rid
    stage_ids = {s["stage_id"] for s in body["steps"]}
    assert {"plan", "implement"} <= stage_ids
    cand_ids = {c["candidate_id"] for c in body["candidates"]}
    assert "mock_impl" in cand_ids


def test_run_detail_404_for_unknown_run(client) -> None:
    resp = client.get("/api/runs/does-not-exist")
    assert resp.status_code == 404


def test_run_candidates_route(client, project) -> None:
    rid = _run_id(project)
    resp = client.get(f"/api/runs/{rid}/candidates")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["candidate_id"] == "mock_impl"


def test_run_providers_route(client, project) -> None:
    rid = _run_id(project)
    resp = client.get(f"/api/runs/{rid}/providers")
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert rows[0]["provider_id"] == "mock_impl"
    assert rows[0]["healthy"] == 1


# ---------------------------------------------------------------------------
# Candidate diff + detail
# ---------------------------------------------------------------------------

def test_candidate_diff_route_returns_patch_text(client, project) -> None:
    rid = _run_id(project)
    resp = client.get(f"/api/runs/{rid}/candidates/mock_impl/diff")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "added" in resp.text


def test_candidate_diff_404_when_missing(client, project) -> None:
    rid = _run_id(project)
    resp = client.get(f"/api/runs/{rid}/candidates/nope/diff")
    assert resp.status_code == 404


def test_candidate_detail_returns_known_jsons(client, project) -> None:
    rid = _run_id(project)
    resp = client.get(f"/api/runs/{rid}/candidates/mock_impl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"]["verdict"] == "accept"
    # Files we didn't seed return null.
    assert body["agent_result"] is None


# ---------------------------------------------------------------------------
# Empty DB fallback
# ---------------------------------------------------------------------------

def test_empty_project_returns_empty_runs(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    app = create_app(root)
    with TestClient(app) as c:
        resp = c.get("/api/runs")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}
        # SqliteIndex creates the DB lazily; healthz now reports yes.
        resp = c.get("/api/healthz")
        assert resp.json()["db_exists"] == "yes"


# ---------------------------------------------------------------------------
# SqliteIndex direct sanity for the routes' upstream
# ---------------------------------------------------------------------------

def test_sqlite_index_is_visible_through_dashboard(client, project) -> None:
    rid = _run_id(project)
    db = project / ".orchestrator" / "state.db"
    idx = SqliteIndex(db)
    assert idx.get_run(rid) is not None
    resp = client.get(f"/api/runs/{rid}")
    assert resp.status_code == 200
