from __future__ import annotations

import json
from pathlib import Path

from devforge.core.run_context import create_run_context, load_run_context


def test_create_run_context(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    task = project / "task.md"
    task.write_text("do the thing", encoding="utf-8")

    ctx = create_run_context(project, workflow="feature", input_path=task)

    assert ctx.root.exists()
    assert ctx.workflow == "feature"
    assert (ctx.root / "input.md").read_text(encoding="utf-8") == "do the thing"
    assert (ctx.root / "run.json").exists()
    assert (ctx.root / "candidates").is_dir()


def test_run_context_sequence_no_collision(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    a = create_run_context(project, workflow="feature", input_path=None)
    b = create_run_context(project, workflow="feature", input_path=None)
    assert a.run_id != b.run_id


def test_load_run_context_round_trip(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ctx = create_run_context(project, workflow="feature", input_path=None,
                             extra_metadata={"k": "v"})
    loaded = load_run_context(ctx.root)
    assert loaded.run_id == ctx.run_id
    assert loaded.metadata == {"k": "v"}
    data = json.loads((ctx.root / "run.json").read_text())
    assert data["workflow"] == "feature"
