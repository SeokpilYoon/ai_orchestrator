"""Local dashboard backend (DEVF-082).

A read-only FastAPI app over the project's SQLite index (DEVF-080) and
the per-run JSON artifacts. Mounted by the ``devforge dashboard``
subcommand; can also be imported directly via ``create_app(project_root)``
to embed in another ASGI host.

Routes (all under ``/api/`` so a future static frontend can sit at ``/``):

- ``GET /api/runs``                                       list of runs
- ``GET /api/runs/{run_id}``                              run detail + steps
                                                          + candidates +
                                                          evaluations +
                                                          provider_status
- ``GET /api/runs/{run_id}/candidates``                   per-run candidate list
- ``GET /api/runs/{run_id}/candidates/{cid}/diff``        ``diff.patch`` body
                                                          (text/plain)
- ``GET /api/runs/{run_id}/providers``                    provider_status rows
- ``GET /api/healthz``                                    liveness probe

A tiny HTML index at ``GET /`` lists the API routes — enough so
"browser에서 JSON API 확인 가능" (DEVF-082 DoD) is satisfied with one
visit. The dashboard frontend (DEVF-083) replaces this index when it
ships.

FastAPI is an optional dependency. ``create_app`` raises
:class:`DashboardImportError` with a remediation tip when the extras
are missing, so the CLI can surface a friendly error.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devforge.core.sqlite_index import SqliteIndex

_INDEX_DB_REL = Path(".orchestrator") / "state.db"


class DashboardImportError(RuntimeError):
    """Raised when the optional dashboard extras are not installed."""


def _resolve_run_root(project_root: Path, run_id: str) -> Path:
    return project_root / ".orchestrator" / "runs" / run_id


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def create_app(project_root: Path):  # noqa: ANN201 — return type depends on FastAPI being installed
    """Build the FastAPI app bound to ``<project_root>/.orchestrator/state.db``.

    Lazy imports keep ``devforge`` itself importable without the dashboard
    extras. Routes serialise data from the SQLite index + per-run JSON
    artifacts — no writes happen here, this is a read-only surface.
    """
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse, PlainTextResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - exercised only without extras
        raise DashboardImportError(
            "FastAPI is not installed. Install the dashboard extras: "
            "pip install '.[dashboard]'"
        ) from exc

    project_root = Path(project_root).resolve()
    db_path = project_root / _INDEX_DB_REL

    app = FastAPI(
        title="devforge dashboard",
        version="0.0.1",
        description=(
            "Read-only HTTP surface over the project's SQLite state index. "
            "DEVF-082."
        ),
    )

    def _index() -> SqliteIndex:
        # SqliteIndex creates the file + schema on first touch — so even a
        # never-run project shows an empty list rather than a 500.
        return SqliteIndex(db_path)

    # ----- Static frontend (DEVF-083) ----------------------------------

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )

        @app.get("/", include_in_schema=False)
        def root_index() -> FileResponse:
            return FileResponse(str(static_dir / "index.html"))
    else:  # pragma: no cover — only hit if the package was assembled wrong
        from fastapi.responses import HTMLResponse  # noqa: PLC0415

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def root_index() -> str:
            return (
                "<!doctype html><html><body>"
                f"<p>Static dashboard files missing under {static_dir}. "
                "Reinstall with the dashboard extras.</p></body></html>"
            )

    # ----- Liveness ----------------------------------------------------

    @app.get("/api/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "db_exists": "yes" if db_path.exists() else "no"}

    # ----- Runs --------------------------------------------------------

    @app.get("/api/runs")
    def list_runs(
        workflow: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        idx = _index()
        return {
            "items": idx.list_runs(workflow=workflow, limit=max(1, min(limit, 500))),
        }

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        idx = _index()
        run = idx.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return {
            "run": run,
            "steps": idx.get_steps(run_id),
            "candidates": idx.get_candidates(run_id),
            "evaluations": idx.get_evaluations(run_id),
            "provider_status": idx.get_provider_status(run_id),
        }

    @app.get("/api/runs/{run_id}/candidates")
    def run_candidates(run_id: str) -> dict[str, Any]:
        idx = _index()
        run = idx.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return {"items": idx.get_candidates(run_id)}

    @app.get("/api/runs/{run_id}/providers")
    def run_providers(run_id: str) -> dict[str, Any]:
        idx = _index()
        run = idx.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return {"items": idx.get_provider_status(run_id)}

    # ----- Candidate diff (reads diff.patch off disk) ------------------

    @app.get(
        "/api/runs/{run_id}/candidates/{candidate_id}/diff",
        response_class=PlainTextResponse,
    )
    def candidate_diff(run_id: str, candidate_id: str) -> str:
        run_root = _resolve_run_root(project_root, run_id)
        if not run_root.exists():
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        diff_path = run_root / "candidates" / candidate_id / "diff.patch"
        if not diff_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"diff.patch not found for candidate '{candidate_id}'",
            )
        try:
            return diff_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to read diff: {exc}"
            ) from exc

    @app.get("/api/runs/{run_id}/candidates/{candidate_id}")
    def candidate_detail(run_id: str, candidate_id: str) -> dict[str, Any]:
        run_root = _resolve_run_root(project_root, run_id)
        cand_dir = run_root / "candidates" / candidate_id
        if not cand_dir.exists():
            raise HTTPException(
                status_code=404,
                detail=f"candidate '{candidate_id}' not found in run '{run_id}'",
            )
        return {
            "agent_result": _read_json_if_exists(cand_dir / "agent_result.json"),
            "decision": _read_json_if_exists(cand_dir / "decision.json"),
            "score": _read_json_if_exists(cand_dir / "score.json"),
            "policy": _read_json_if_exists(cand_dir / "policy.json"),
            "review": _read_json_if_exists(cand_dir / "review.json"),
            "validation": _read_json_if_exists(cand_dir / "validation.json"),
        }

    return app


def serve(
    project_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Run the dashboard backend via uvicorn (blocking).

    Raises :class:`DashboardImportError` when the extras aren't installed.
    """
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without extras
        raise DashboardImportError(
            "uvicorn is not installed. Install the dashboard extras: "
            "pip install '.[dashboard]'"
        ) from exc

    app = create_app(project_root)
    uvicorn.run(app, host=host, port=port, log_level="info")
