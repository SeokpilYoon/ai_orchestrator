"""SQLite index for run state (DEVF-080).

The per-run JSON store (:mod:`devforge.core.state_store`) remains the
authoritative on-disk record for one run — easy to grep, easy to copy
out of a run directory, easy to inspect by hand. This module adds a
project-level SQLite database that *indexes* every run, so cross-run
queries (latest run, runs by workflow, candidate scores, provider
health snapshots) can answer in a single query without walking the
filesystem.

The DB lives at ``<project_root>/.orchestrator/state.db``. Schema is
created on first use; subsequent opens are idempotent. All writes are
upserts so re-running the same stage / candidate is safe.

Five tables, mirroring ``docs/plan/03 §DEVF-080``:

- ``runs``            — one row per run
- ``steps``           — per-stage records
- ``candidates``      — candidate references with score + decision
- ``evaluations``     — per-candidate evaluation summaries (score,
                        decision, validation)
- ``provider_status`` — provider healthcheck snapshots per run

This module deliberately does not import :mod:`devforge.core.state_store`
so it can be reused from a future dashboard backend (DEVF-082) without
pulling in the JSON layer.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DB_FILENAME = "state.db"
_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    workflow            TEXT NOT NULL,
    status              TEXT NOT NULL,
    input_ref           TEXT,
    started_at          TEXT,
    completed_at        TEXT,
    chosen_candidate    TEXT,
    final_decision_ref  TEXT,
    error               TEXT,
    root_path           TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    run_id        TEXT NOT NULL,
    stage_id      TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT,
    completed_at  TEXT,
    artifact_ref  TEXT,
    note          TEXT,
    PRIMARY KEY (run_id, stage_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS candidates (
    run_id        TEXT NOT NULL,
    candidate_id  TEXT NOT NULL,
    provider_id   TEXT,
    decision      TEXT,
    score         REAL,
    decision_ref  TEXT,
    PRIMARY KEY (run_id, candidate_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evaluations (
    run_id        TEXT NOT NULL,
    candidate_id  TEXT NOT NULL,
    kind          TEXT NOT NULL,
    passed        INTEGER,
    score         REAL,
    details       TEXT,
    PRIMARY KEY (run_id, candidate_id, kind)
);

CREATE TABLE IF NOT EXISTS provider_status (
    run_id        TEXT NOT NULL,
    provider_id   TEXT NOT NULL,
    status        TEXT NOT NULL,
    healthy       INTEGER NOT NULL,
    detail        TEXT,
    last_checked  TEXT NOT NULL,
    PRIMARY KEY (run_id, provider_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_workflow ON runs(workflow);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_candidates_decision ON candidates(decision);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def index_path_for_run(run_root: Path) -> Path:
    """Resolve the project-level ``state.db`` from a ``<run_root>`` path.

    Run roots live at ``<project_root>/.orchestrator/runs/<run_id>/``,
    so the project-level DB sits at ``<project_root>/.orchestrator/state.db``.
    """
    run_root = Path(run_root).resolve()
    return run_root.parent.parent / _DB_FILENAME


class SqliteIndex:
    """Thin wrapper over a single ``state.db`` file.

    Each method opens its own connection so writes are safe from any
    code path (driver threads, tests, dashboard reads) without holding
    long-lived locks. SQLite's WAL journal is enabled to let readers
    proceed while a writer is active.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(_SCHEMA)
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1;")
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version(version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )

    # ------------------------------------------------------------------
    # Writes — runs
    # ------------------------------------------------------------------

    def upsert_run(
        self,
        *,
        run_id: str,
        workflow: str,
        status: str,
        input_ref: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        chosen_candidate: str | None = None,
        final_decision_ref: str | None = None,
        error: str | None = None,
        root_path: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(
                    run_id, workflow, status, input_ref,
                    started_at, completed_at,
                    chosen_candidate, final_decision_ref, error, root_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    workflow=excluded.workflow,
                    status=excluded.status,
                    input_ref=COALESCE(excluded.input_ref, runs.input_ref),
                    started_at=COALESCE(runs.started_at, excluded.started_at),
                    completed_at=COALESCE(excluded.completed_at, runs.completed_at),
                    chosen_candidate=COALESCE(excluded.chosen_candidate, runs.chosen_candidate),
                    final_decision_ref=COALESCE(excluded.final_decision_ref, runs.final_decision_ref),
                    error=COALESCE(excluded.error, runs.error),
                    root_path=COALESCE(excluded.root_path, runs.root_path)
                ;
                """,
                (
                    run_id, workflow, status, input_ref,
                    started_at, completed_at,
                    chosen_candidate, final_decision_ref, error, root_path,
                ),
            )

    # ------------------------------------------------------------------
    # Writes — steps
    # ------------------------------------------------------------------

    def upsert_step(
        self,
        *,
        run_id: str,
        stage_id: str,
        status: str,
        started_at: str | None = None,
        completed_at: str | None = None,
        artifact_ref: str | None = None,
        note: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO steps(
                    run_id, stage_id, status,
                    started_at, completed_at, artifact_ref, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, stage_id) DO UPDATE SET
                    status=excluded.status,
                    started_at=COALESCE(steps.started_at, excluded.started_at),
                    completed_at=COALESCE(excluded.completed_at, steps.completed_at),
                    artifact_ref=COALESCE(excluded.artifact_ref, steps.artifact_ref),
                    note=COALESCE(excluded.note, steps.note)
                ;
                """,
                (
                    run_id, stage_id, status,
                    started_at, completed_at, artifact_ref, note,
                ),
            )

    # ------------------------------------------------------------------
    # Writes — candidates
    # ------------------------------------------------------------------

    def upsert_candidate(
        self,
        *,
        run_id: str,
        candidate_id: str,
        provider_id: str | None,
        decision: str | None,
        score: float | None,
        decision_ref: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO candidates(
                    run_id, candidate_id, provider_id, decision, score, decision_ref
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, candidate_id) DO UPDATE SET
                    provider_id=COALESCE(excluded.provider_id, candidates.provider_id),
                    decision=COALESCE(excluded.decision, candidates.decision),
                    score=COALESCE(excluded.score, candidates.score),
                    decision_ref=COALESCE(excluded.decision_ref, candidates.decision_ref)
                ;
                """,
                (run_id, candidate_id, provider_id, decision, score, decision_ref),
            )

    # ------------------------------------------------------------------
    # Writes — evaluations
    # ------------------------------------------------------------------

    def upsert_evaluation(
        self,
        *,
        run_id: str,
        candidate_id: str,
        kind: str,
        passed: bool | None = None,
        score: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details_json = json.dumps(details, ensure_ascii=False) if details else None
        passed_int = None if passed is None else int(bool(passed))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evaluations(
                    run_id, candidate_id, kind, passed, score, details
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, candidate_id, kind) DO UPDATE SET
                    passed=excluded.passed,
                    score=excluded.score,
                    details=excluded.details
                ;
                """,
                (run_id, candidate_id, kind, passed_int, score, details_json),
            )

    # ------------------------------------------------------------------
    # Writes — provider_status
    # ------------------------------------------------------------------

    def upsert_provider_status(
        self,
        *,
        run_id: str,
        provider_id: str,
        status: str,
        healthy: bool,
        detail: str | None = None,
        last_checked: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_status(
                    run_id, provider_id, status, healthy, detail, last_checked
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, provider_id) DO UPDATE SET
                    status=excluded.status,
                    healthy=excluded.healthy,
                    detail=COALESCE(excluded.detail, provider_status.detail),
                    last_checked=excluded.last_checked
                ;
                """,
                (
                    run_id, provider_id, status, int(bool(healthy)),
                    detail, last_checked or _now_iso(),
                ),
            )

    # ------------------------------------------------------------------
    # Reads (used by DEVF-081/082 + tests)
    # ------------------------------------------------------------------

    def list_runs(
        self, *, workflow: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs"
        params: list[Any] = []
        if workflow is not None:
            sql += " WHERE workflow = ?"
            params.append(workflow)
        sql += " ORDER BY started_at DESC, run_id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY ROWID ASC",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_candidates(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM candidates WHERE run_id = ? "
                "ORDER BY score DESC, candidate_id ASC",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_evaluations(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluations WHERE run_id = ? "
                "ORDER BY candidate_id ASC, kind ASC",
                (run_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if d.get("details"):
                with suppress(json.JSONDecodeError):
                    d["details"] = json.loads(d["details"])
            out.append(d)
        return out

    def get_provider_status(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM provider_status WHERE run_id = ? "
                "ORDER BY provider_id ASC",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]
