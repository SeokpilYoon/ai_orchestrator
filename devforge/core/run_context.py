"""Run context — per-execution artifact directory and metadata.

Authoritative reference: docs/plan/02 §5.11, docs/plan/03 DEVF-011.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class RunContext:
    run_id: str
    workflow: str
    root: Path                 # .orchestrator/runs/<run_id>/
    project_root: Path
    input_path: Path | None
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def candidates_dir(self) -> Path:
        return self.root / "candidates"

    def candidate_dir(self, candidate_id: str) -> Path:
        d = self.candidates_dir / candidate_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["root"] = str(self.root)
        d["project_root"] = str(self.project_root)
        d["input_path"] = str(self.input_path) if self.input_path else None
        return d


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _short_seq(d: Path) -> str:
    """Return a 3-digit sequence so concurrent runs in the same second don't collide."""
    if not d.exists():
        return "001"
    existing = sorted(p.name for p in d.iterdir() if p.is_dir())
    return f"{len(existing) + 1:03d}"


def create_run_context(
    project_root: Path,
    workflow: str,
    input_path: Path | None,
    *,
    orchestrator_dir: Path | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> RunContext:
    """Create a new run directory under ``.orchestrator/runs/`` and return its context.

    Args:
        project_root: repo root the run targets.
        workflow: workflow id (e.g. "feature").
        input_path: optional task/PRD file to copy into the run directory.
        orchestrator_dir: override for ``<project_root>/.orchestrator``.
        extra_metadata: free-form metadata stored alongside run.json.
    """
    base = orchestrator_dir or (project_root / ".orchestrator")
    runs_dir = base / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _utc_timestamp()
    seq = _short_seq(runs_dir)
    run_id = f"{timestamp}_{seq}"
    run_root = runs_dir / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    (run_root / "candidates").mkdir()

    ctx = RunContext(
        run_id=run_id,
        workflow=workflow,
        root=run_root,
        project_root=project_root.resolve(),
        input_path=None,
        created_at=datetime.now(UTC).isoformat(),
        metadata=dict(extra_metadata or {}),
    )

    if input_path is not None and input_path.exists():
        target = run_root / "input.md"
        shutil.copy2(input_path, target)
        ctx.input_path = target

    (run_root / "run.json").write_text(
        json.dumps(ctx.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return ctx


def load_run_context(run_root: Path) -> RunContext:
    """Load a previously-created run directory."""
    meta = json.loads((run_root / "run.json").read_text(encoding="utf-8"))
    return RunContext(
        run_id=meta["run_id"],
        workflow=meta["workflow"],
        root=Path(meta["root"]),
        project_root=Path(meta["project_root"]),
        input_path=Path(meta["input_path"]) if meta.get("input_path") else None,
        created_at=meta["created_at"],
        metadata=meta.get("metadata", {}),
    )
