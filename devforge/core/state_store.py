"""File-based state store (DEVF-013).

State is stored under ``<run_root>/state/`` so existing artifacts at
``<run_root>/`` (``run.json``, ``decision.json``, ``final_report.md``) are
untouched. Three JSON files capture the run progress:

- ``state/run.json`` — run-level metadata + status
- ``state/steps.json`` — per-stage records
- ``state/candidates.json`` — candidate references with score/decision

This is the v1 backend; DEVF-080 (SQLite) replaces the persistence layer
while keeping this public API.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

    def __init__(self, run_root: Path) -> None:
        self.run_root = Path(run_root)
        self.state_dir = self.run_root / "state"

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

    def save_final_decision(
        self,
        decision_ref: str | None,
        chosen_candidate: str | None,
    ) -> None:
        doc = _read_json(self.run_path) or {}
        doc["final_decision_ref"] = decision_ref
        doc["chosen_candidate"] = chosen_candidate
        _write_json(self.run_path, doc)

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
        for entry in steps:
            if entry.get("stage_id") == stage_id:
                # Update in place — preserve started_at on first transition.
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
                _write_json(self.steps_path, doc)
                return
        # New stage entry (was not declared in init_run — allowed).
        new_entry = {
            "stage_id": stage_id,
            "status": status,
            "started_at": now if status != "pending" else None,
            "completed_at": now
            if status in {"completed", "failed", "skipped"}
            else None,
            "artifact_ref": artifact_ref,
            "note": note,
        }
        steps.append(new_entry)
        _write_json(self.steps_path, doc)

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
        for i, entry in enumerate(cands):
            if entry.get("candidate_id") == candidate_id:
                cands[i] = record
                _write_json(self.candidates_path, doc)
                return
        cands.append(record)
        _write_json(self.candidates_path, doc)

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
