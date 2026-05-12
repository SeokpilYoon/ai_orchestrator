"""Integration coverage for the research_optimize workflow.

The driver is deterministic in dry_run mode — no provider is required.
The implement mode is exercised separately with a mock provider.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from devforge.core.config_loader import DevforgeConfig
from devforge.core.run_context import create_run_context
from devforge.core.state_store import StateStore
from devforge.core.workflow_engine import WorkflowEngine
from devforge.stages.research_optimize_driver import (
    Hypothesis,
    InspectionResult,
    ResearchOptimizeError,
    _parse_metric_value,
    generate_hypotheses,
    inspect_target,
    measure_metric,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# inspect_target
# ---------------------------------------------------------------------------

def test_inspect_target_summarises_directory(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("VAL = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("X = 2\n" * 200, encoding="utf-8")
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.pyc").write_bytes(b"bytes")

    result = inspect_target(tmp_path)
    assert result.file_count == 3
    assert result.extensions[".py"] == 2
    assert result.extensions[".md"] == 1
    paths = {entry["path"] for entry in result.largest_files}
    assert "b.py" in paths


def test_inspect_target_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(ResearchOptimizeError):
        inspect_target(tmp_path / "nope")


def test_inspect_target_handles_single_file(tmp_path: Path) -> None:
    f = tmp_path / "only.py"
    f.write_text("X = 1\n", encoding="utf-8")
    result = inspect_target(f)
    assert result.file_count == 1
    assert result.untested_modules == []


# ---------------------------------------------------------------------------
# measure_metric
# ---------------------------------------------------------------------------

def test_measure_metric_no_command_returns_skip(tmp_path: Path) -> None:
    m = measure_metric(None, None, tmp_path)
    assert m.command is None
    assert m.value is None
    assert "no metric_command" in m.note


def test_measure_metric_extracts_first_number(tmp_path: Path) -> None:
    m = measure_metric("echo 'count=42 done'", None, tmp_path)
    assert m.value == 42.0


def test_measure_metric_uses_pattern_capture(tmp_path: Path) -> None:
    m = measure_metric(
        "echo 'metric=3.14159 latency=10'", r"metric=([0-9.]+)", tmp_path
    )
    assert m.value == pytest.approx(3.14159)


def test_measure_metric_malformed_pattern_falls_back(tmp_path: Path) -> None:
    m = measure_metric("echo 'val 99'", "(unclosed", tmp_path)
    assert m.value == 99.0


def test_parse_metric_value_no_number_returns_none() -> None:
    assert _parse_metric_value("no numbers here", None) is None


# ---------------------------------------------------------------------------
# generate_hypotheses
# ---------------------------------------------------------------------------

def test_hypotheses_capture_longest_and_untested(tmp_path: Path) -> None:
    (tmp_path / "huge.py").write_text("LONG = '" + "a" * 4096 + "'\n", encoding="utf-8")
    (tmp_path / "small.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "test_small.py").write_text("def test_x(): pass\n", encoding="utf-8")
    inspection = inspect_target(tmp_path)
    hypotheses = generate_hypotheses(inspection, tmp_path, tmp_path)
    kinds = {h.kind for h in hypotheses}
    assert "long_file" in kinds
    # huge.py has no matching test → untested_module
    targets = {h.target_path for h in hypotheses if h.kind == "untested_module"}
    assert "huge.py" in targets


def test_hypotheses_dedup_under_cap(tmp_path: Path) -> None:
    for i in range(20):
        (tmp_path / f"m{i}.py").write_text(f"X = {i}\n" * 1000, encoding="utf-8")
    inspection = inspect_target(tmp_path)
    hypotheses = generate_hypotheses(inspection, tmp_path, tmp_path)
    assert len(hypotheses) <= 8


# ---------------------------------------------------------------------------
# End-to-end via WorkflowEngine (dry_run)
# ---------------------------------------------------------------------------

def test_workflow_runs_end_to_end_in_dry_run(
    base_config: DevforgeConfig, tmp_path: Path
) -> None:
    repo = Path(base_config.project.root)
    # Add a few python files for the inspection to chew on.
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "huge.py").write_text("L = '" + "x" * 4096 + "'\n", encoding="utf-8")
    (repo / "src" / "tiny.py").write_text("Y = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "research seed"], cwd=repo,
        check=True, capture_output=True,
    )

    ctx = create_run_context(
        repo,
        workflow="research_optimize",
        input_path=None,
        extra_metadata={
            "target_path": str(repo / "src"),
            "metric_command": "echo 'metric=42'",
            "metric_pattern": "metric=([0-9.]+)",
        },
    )
    engine = WorkflowEngine(base_config, ctx)
    engine.run("research_optimize")

    # Artifacts present.
    for name in (
        "research_inspection.json",
        "research_baseline.json",
        "research_hypotheses.json",
        "research_experiment.json",
        "research_verify.json",
        "research_report.json",
        "final_report.md",
    ):
        assert (ctx.root / name).exists(), f"missing artifact: {name}"

    report = json.loads((ctx.root / "research_report.json").read_text(encoding="utf-8"))
    assert report["experiment_mode"] == "dry_run"
    # Same baseline command runs twice in dry_run → verify echoes the same
    # metric, so direction is `unchanged` and decision is `no_change`.
    assert report["verify"]["direction"] == "unchanged"
    assert report["decision"] == "no_change"

    # State store rows.
    state = StateStore(ctx.root)
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    assert steps == {
        "target_inspection": "completed",
        "baseline_measurement": "completed",
        "hypothesis_generation": "completed",
        "experiment": "skipped",  # dry_run
        "verify_metric": "completed",
        "final_report": "completed",
    }


def test_workflow_skips_verify_without_metric(
    base_config: DevforgeConfig, tmp_path: Path
) -> None:
    repo = Path(base_config.project.root)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "x"], cwd=repo, check=True, capture_output=True
    )

    ctx = create_run_context(
        repo,
        workflow="research_optimize",
        input_path=None,
        extra_metadata={"target_path": str(repo / "src")},
    )
    engine = WorkflowEngine(base_config, ctx)
    engine.run("research_optimize")

    state = StateStore(ctx.root)
    steps = {s["stage_id"]: s["status"] for s in state.load_steps()}
    assert steps["baseline_measurement"] == "skipped"
    assert steps["verify_metric"] == "skipped"
    report = json.loads((ctx.root / "research_report.json").read_text(encoding="utf-8"))
    assert report["decision"] == "skipped"


def test_workflow_records_in_sqlite_index(
    base_config: DevforgeConfig, tmp_path: Path
) -> None:
    repo = Path(base_config.project.root)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "x"], cwd=repo, check=True, capture_output=True
    )

    ctx = create_run_context(
        repo,
        workflow="research_optimize",
        input_path=None,
        extra_metadata={"target_path": str(repo / "src")},
    )
    engine = WorkflowEngine(base_config, ctx)
    engine.run("research_optimize")

    from devforge.core.sqlite_index import SqliteIndex
    idx = SqliteIndex(repo / ".orchestrator" / "state.db")
    runs = idx.list_runs(workflow="research_optimize")
    assert any(r["run_id"] == ctx.run_id for r in runs)


# ---------------------------------------------------------------------------
# Hypothesis dataclass round-trip
# ---------------------------------------------------------------------------

def test_hypothesis_to_dict_keys() -> None:
    h = Hypothesis(
        id="H-001",
        kind="long_file",
        target_path="src/x.py",
        summary="long",
        suggested_action="refactor",
    )
    payload = h.to_dict()
    assert payload == {
        "id": "H-001",
        "kind": "long_file",
        "target_path": "src/x.py",
        "summary": "long",
        "suggested_action": "refactor",
    }


def test_inspection_result_round_trips() -> None:
    inspection = InspectionResult(target="/tmp/x", file_count=2, total_bytes=10)
    payload = inspection.to_dict()
    assert payload["target"] == "/tmp/x"
    assert payload["file_count"] == 2
