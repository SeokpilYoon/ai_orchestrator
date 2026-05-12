"""File-based state store (DEVF-013) + SQLite index (DEVF-080).

State is stored under ``<run_root>/state/`` as three JSON files —
``run.json``, ``steps.json``, ``candidates.json``. The JSON layer is
the per-run source of truth (easy to grep, easy to copy out of a run
directory).

DEVF-080 layered a project-level SQLite index on top: every write is
mirrored into ``<project_root>/.orchestrator/state.db`` so cross-run
queries (latest run, runs by workflow, candidate scores, provider
health snapshots) answer without walking the filesystem. The SQLite
mirror is opened lazily on first use and silently disabled if SQLite
is unavailable — the JSON layer remains authoritative.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devforge.core.sqlite_index import SqliteIndex, index_path_for_run

# ---------------------------------------------------------------------------
# Status enums (kept as plain strings to stay JSON-friendly)
# ---------------------------------------------------------------------------

RUN_STATUSES = frozenset(
    {
        "pending",
        "running",
        "completed",
        "failed",
        "human_review",
        "accepted",
        "discarded",
    }
)
STEP_STATUSES = frozenset({"pending", "running", "completed", "failed", "skipped"})
_TERMINAL_RUN_STATUSES = {"completed", "failed", "human_review", "accepted", "discarded"}


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

class StateStoreError(Exception):
    """Raised when state files are missing or malformed."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise StateStoreError(f"corrupt state file {path}: {exc}") from exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


class StateStore:
    """JSON-file-backed run state for one :class:`RunContext`."""

    RUN_FILE = "run.json"
    STEPS_FILE = "steps.json"
    CANDIDATES_FILE = "candidates.json"

    def __init__(
        self,
        run_root: Path,
        *,
        sqlite_index: SqliteIndex | None = None,
    ) -> None:
        self.run_root = Path(run_root)
        self.state_dir = self.run_root / "state"
        # Lazy SQLite mirror. The mirror failures must never break the
        # JSON write — the JSON layer is authoritative on disk.
        if sqlite_index is None:
            try:
                sqlite_index = SqliteIndex(index_path_for_run(self.run_root))
            except Exception:  # noqa: BLE001 — opt-in mirror, never block JSON writes
                sqlite_index = None
        self._sqlite: SqliteIndex | None = sqlite_index

    @property
    def run_id(self) -> str:
        return self.run_root.name

    def _safe_mirror(self, fn) -> None:
        """Run a SQLite mirror update; swallow errors so JSON writes never fail."""
        if self._sqlite is None:
            return
        try:
            fn(self._sqlite)
        except Exception:  # noqa: BLE001 — mirror is best-effort
            return

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @property
    def run_path(self) -> Path:
        return self.state_dir / self.RUN_FILE

    @property
    def steps_path(self) -> Path:
        return self.state_dir / self.STEPS_FILE

    @property
    def candidates_path(self) -> Path:
        return self.state_dir / self.CANDIDATES_FILE

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def is_initialized(self) -> bool:
        return self.run_path.exists()

    def init_run(
        self,
        *,
        workflow: str,
        input_ref: str | None,
        stages: list[str],
    ) -> None:
        """Idempotent initialization. Re-calling preserves existing status."""
        if self.is_initialized():
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        run_doc = {
            "run_id": self.run_root.name,
            "workflow": workflow,
            "input_ref": input_ref,
            "status": "pending",
            "stages": list(stages),
            "started_at": _now_iso(),
            "completed_at": None,
            "chosen_candidate": None,
            "final_decision_ref": None,
            "error": None,
        }
        _write_json(self.run_path, run_doc)
        _write_json(
            self.steps_path,
            {
                "steps": [
                    {
                        "stage_id": sid,
                        "status": "pending",
                        "started_at": None,
                        "completed_at": None,
                        "artifact_ref": None,
                        "note": None,
                    }
                    for sid in stages
                ]
            },
        )
        _write_json(self.candidates_path, {"candidates": []})

        def _mirror(idx: SqliteIndex) -> None:
            idx.upsert_run(
                run_id=self.run_id,
                workflow=workflow,
                status="pending",
                input_ref=input_ref,
                started_at=run_doc["started_at"],
                root_path=str(self.run_root.resolve()),
            )
            for sid in stages:
                idx.upsert_step(
                    run_id=self.run_id, stage_id=sid, status="pending"
                )
        self._safe_mirror(_mirror)

    # ------------------------------------------------------------------
    # Run-level
    # ------------------------------------------------------------------

    def update_run_status(self, status: str, **extra: Any) -> None:
        if status not in RUN_STATUSES:
            raise StateStoreError(
                f"invalid run status '{status}'. Allowed: {sorted(RUN_STATUSES)}"
            )
        doc = _read_json(self.run_path) or {}
        doc["status"] = status
        if status in _TERMINAL_RUN_STATUSES:
            doc["completed_at"] = _now_iso()
        for key, value in extra.items():
            doc[key] = value
        _write_json(self.run_path, doc)

        def _mirror(idx: SqliteIndex) -> None:
            idx.upsert_run(
                run_id=self.run_id,
                workflow=doc.get("workflow", ""),
                status=status,
                input_ref=doc.get("input_ref"),
                started_at=doc.get("started_at"),
                completed_at=doc.get("completed_at"),
                chosen_candidate=doc.get("chosen_candidate"),
                final_decision_ref=doc.get("final_decision_ref"),
                error=doc.get("error"),
                root_path=str(self.run_root.resolve()),
            )
            if status in _TERMINAL_RUN_STATUSES:
                _mirror_candidate_evaluations(self.run_root, self.run_id, idx)
        self._safe_mirror(_mirror)

    def save_final_decision(
        self,
        decision_ref: str | None,
        chosen_candidate: str | None,
    ) -> None:
        doc = _read_json(self.run_path) or {}
        doc["final_decision_ref"] = decision_ref
        doc["chosen_candidate"] = chosen_candidate
        _write_json(self.run_path, doc)

        def _mirror(idx: SqliteIndex) -> None:
            idx.upsert_run(
                run_id=self.run_id,
                workflow=doc.get("workflow", ""),
                status=doc.get("status", "running"),
                input_ref=doc.get("input_ref"),
                started_at=doc.get("started_at"),
                completed_at=doc.get("completed_at"),
                chosen_candidate=chosen_candidate,
                final_decision_ref=decision_ref,
                error=doc.get("error"),
                root_path=str(self.run_root.resolve()),
            )
        self._safe_mirror(_mirror)

    # ------------------------------------------------------------------
    # Step-level
    # ------------------------------------------------------------------

    def save_step(
        self,
        stage_id: str,
        status: str,
        *,
        artifact_ref: str | None = None,
        note: str | None = None,
    ) -> None:
        if status not in STEP_STATUSES:
            raise StateStoreError(
                f"invalid step status '{status}'. Allowed: {sorted(STEP_STATUSES)}"
            )
        doc = _read_json(self.steps_path) or {"steps": []}
        steps = doc.setdefault("steps", [])
        now = _now_iso()
        target: dict[str, Any] | None = None
        for entry in steps:
            if entry.get("stage_id") == stage_id:
                if status == "running" and not entry.get("started_at"):
                    entry["started_at"] = now
                if status in {"completed", "failed", "skipped"}:
                    entry["completed_at"] = now
                    if not entry.get("started_at"):
                        entry["started_at"] = now
                entry["status"] = status
                if artifact_ref is not None:
                    entry["artifact_ref"] = artifact_ref
                if note is not None:
                    entry["note"] = note
                target = entry
                break
        if target is None:
            target = {
                "stage_id": stage_id,
                "status": status,
                "started_at": now if status != "pending" else None,
                "completed_at": now
                if status in {"completed", "failed", "skipped"}
                else None,
                "artifact_ref": artifact_ref,
                "note": note,
            }
            steps.append(target)
        _write_json(self.steps_path, doc)

        def _mirror(idx: SqliteIndex) -> None:
            idx.upsert_step(
                run_id=self.run_id,
                stage_id=stage_id,
                status=status,
                started_at=target["started_at"],
                completed_at=target["completed_at"],
                artifact_ref=target["artifact_ref"],
                note=target["note"],
            )
        self._safe_mirror(_mirror)

    # ------------------------------------------------------------------
    # Candidate-level
    # ------------------------------------------------------------------

    def save_candidate(
        self,
        candidate_id: str,
        *,
        provider_id: str,
        decision: str,
        score: float,
        decision_ref: str,
    ) -> None:
        doc = _read_json(self.candidates_path) or {"candidates": []}
        cands = doc.setdefault("candidates", [])
        record = {
            "candidate_id": candidate_id,
            "provider_id": provider_id,
            "decision": decision,
            "score": score,
            "decision_ref": decision_ref,
        }
        replaced = False
        for i, entry in enumerate(cands):
            if entry.get("candidate_id") == candidate_id:
                cands[i] = record
                replaced = True
                break
        if not replaced:
            cands.append(record)
        _write_json(self.candidates_path, doc)

        def _mirror(idx: SqliteIndex) -> None:
            idx.upsert_candidate(
                run_id=self.run_id,
                candidate_id=candidate_id,
                provider_id=provider_id,
                decision=decision,
                score=float(score),
                decision_ref=decision_ref,
            )
        self._safe_mirror(_mirror)

    # ------------------------------------------------------------------
    # Provider status (DEVF-080 — populated by drivers after they build
    # the provider registry)
    # ------------------------------------------------------------------

    def record_provider_status(
        self,
        *,
        provider_id: str,
        status: str,
        healthy: bool,
        detail: str | None = None,
    ) -> None:
        def _mirror(idx: SqliteIndex) -> None:
            idx.upsert_provider_status(
                run_id=self.run_id,
                provider_id=provider_id,
                status=status,
                healthy=healthy,
                detail=detail,
            )
        self._safe_mirror(_mirror)

    def snapshot_provider_registry(self, registry: Any) -> None:
        """Record every provider's healthcheck status to the SQLite index.

        Accepts any object exposing ``status_rows()`` that returns rows
        with ``name``, ``status``, and ``detail`` attributes — i.e. a
        :class:`devforge.providers.registry.ProviderRegistry`. No-op
        when the SQLite mirror is unavailable.
        """
        if self._sqlite is None or registry is None:
            return
        try:
            rows = registry.status_rows()
        except Exception:  # noqa: BLE001 — best-effort snapshot
            return
        for row in rows:
            self.record_provider_status(
                provider_id=row.name,
                status=row.status,
                healthy=(row.status == "available"),
                detail=row.detail,
            )

    # ------------------------------------------------------------------
    # Read-side
    # ------------------------------------------------------------------

    def load_run(self) -> dict[str, Any]:
        doc = _read_json(self.run_path)
        if doc is None:
            raise StateStoreError(f"run state not found: {self.run_path}")
        return doc

    def load_steps(self) -> list[dict[str, Any]]:
        doc = _read_json(self.steps_path)
        if doc is None:
            return []
        steps = doc.get("steps", [])
        return list(steps) if isinstance(steps, list) else []

    def load_candidates(self) -> list[dict[str, Any]]:
        doc = _read_json(self.candidates_path)
        if doc is None:
            return []
        cands = doc.get("candidates", [])
        return list(cands) if isinstance(cands, list) else []

    def summary_line(self) -> str | None:
        """One-line text summary used by ``devforge report``."""
        try:
            run = self.load_run()
        except StateStoreError:
            return None
        steps = self.load_steps()
        cands = self.load_candidates()
        done = sum(1 for s in steps if s.get("status") == "completed")
        return (
            f"state: {run.get('status', 'unknown')} | "
            f"{done}/{len(steps)} steps completed | "
            f"{len(cands)} candidate(s) | "
            f"chosen={run.get('chosen_candidate') or '-'}"
        )


# ---------------------------------------------------------------------------
# Internal — sweep per-candidate JSON artifacts into the evaluations table
# ---------------------------------------------------------------------------

def _mirror_candidate_evaluations(
    run_root: Path, run_id: str, idx: SqliteIndex
) -> None:
    """Populate the ``evaluations`` table from the run's candidate dirs.

    Called once when a run reaches a terminal status. Reads
    ``candidates/<id>/{score,decision,validation}.json`` per candidate
    and upserts one row per kind. Missing files are skipped silently —
    not every candidate produces every artifact (e.g. a fallback path
    may stop before validation runs).
    """
    candidates_root = Path(run_root) / "candidates"
    if not candidates_root.is_dir():
        return
    for cand_dir in candidates_root.iterdir():
        if not cand_dir.is_dir():
            continue
        candidate_id = cand_dir.name

        score_path = cand_dir / "score.json"
        if score_path.exists():
            try:
                payload = json.loads(score_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            idx.upsert_evaluation(
                run_id=run_id,
                candidate_id=candidate_id,
                kind="score",
                score=float(payload.get("score", 0.0))
                if isinstance(payload.get("score"), (int, float))
                else None,
                details=payload,
            )

        decision_path = cand_dir / "decision.json"
        if decision_path.exists():
            try:
                payload = json.loads(decision_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            verdict = payload.get("verdict")
            passed = (verdict == "accept") if isinstance(verdict, str) else None
            idx.upsert_evaluation(
                run_id=run_id,
                candidate_id=candidate_id,
                kind="decision",
                passed=passed,
                score=float(payload.get("score", 0.0))
                if isinstance(payload.get("score"), (int, float))
                else None,
                details=payload,
            )

        validation_path = cand_dir / "validation.json"
        if validation_path.exists():
            try:
                payload = json.loads(validation_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            results = payload.get("results") if isinstance(payload, dict) else None
            passed = None
            if isinstance(results, dict) and results:
                passed = all(
                    bool(v.get("passed")) for v in results.values()
                    if isinstance(v, dict)
                )
            idx.upsert_evaluation(
                run_id=run_id,
                candidate_id=candidate_id,
                kind="validation",
                passed=passed,
                details=payload,
            )
