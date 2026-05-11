"""Tests for `devforge report` (DEVF-081)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from devforge.cli import app
from devforge.core.state_store import StateStore

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures (hand-crafted run directories — fast + deterministic)
# ---------------------------------------------------------------------------

def _write_config(repo: Path) -> Path:
    cfg = repo / "devforge.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "project": {
                    "name": repo.name,
                    "root": str(repo),
                    "default_branch": "main",
                }
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _make_run(
    repo: Path,
    run_id: str,
    *,
    with_state: bool = True,
    with_final_report: bool = True,
    with_decision: bool = True,
    with_fallback: bool = False,
    with_comparison: bool = False,
    chosen: str | None = "mock_impl",
    candidate_score: float = 95.0,
    candidate_decision: str = "accept",
) -> Path:
    run_root = repo / ".orchestrator" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "candidates").mkdir(exist_ok=True)
    (run_root / "run.json").write_text(
        json.dumps({"run_id": run_id, "workflow": "feature"}), encoding="utf-8"
    )

    if with_state:
        state = StateStore(run_root)
        state.init_run(
            workflow="feature",
            input_ref="input.md",
            stages=[
                "normalize_task",
                "inspect_repo",
                "plan",
                "implement_candidates",
                "comparison_report",
                "final_report",
            ],
        )
        for sid in (
            "normalize_task",
            "inspect_repo",
            "plan",
            "implement_candidates",
            "final_report",
        ):
            state.save_step(sid, "completed", artifact_ref=f"{sid}.json")
        state.save_step(
            "comparison_report", "skipped", note="fewer than 2 candidates"
        )
        if chosen:
            state.save_candidate(
                chosen,
                provider_id=chosen,
                decision=candidate_decision,
                score=candidate_score,
                decision_ref=f"candidates/{chosen}/decision.json",
            )
            state.save_final_decision("decision.json", chosen)
        state.update_run_status("completed")

    if with_final_report:
        (run_root / "final_report.md").write_text(
            f"# Final Report — run {run_id}\n\nCandidate {chosen} accepted.\n",
            encoding="utf-8",
        )
    if with_decision:
        (run_root / "decision.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "chosen_candidate": chosen,
                    "decision": {
                        "verdict": candidate_decision,
                        "reason": "score_threshold_met",
                        "score": candidate_score,
                    },
                }
            ),
            encoding="utf-8",
        )
    if with_fallback:
        (run_root / "fallback_history.json").write_text(
            json.dumps(
                {
                    "history": [
                        {
                            "provider": "codex_sub_cli",
                            "failure_class": "usage_limit_hit",
                            "error": "limit reached",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    if with_comparison:
        (run_root / "comparison.md").write_text(
            "# Comparison\n\nDummy table.\n", encoding="utf-8"
        )
    return run_root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_report_no_runs_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    result = runner.invoke(app, ["report", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "No runs found." in result.output


def test_report_latest_renders_markdown(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    _make_run(repo, "20260101_000000_001")
    _make_run(repo, "20260101_000000_002", chosen="mock_other", candidate_score=80.0)

    # No --run → latest.
    result = runner.invoke(app, ["report", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    # Latest is the lexicographically last (..._002).
    assert "20260101_000000_002" in result.output
    assert "Chosen candidate: **mock_other**" in result.output
    assert "## Candidates" in result.output
    assert "| mock_other | mock_other | 80.0 | accept |" in result.output
    assert "## Fallback history" in result.output
    assert "_none_" in result.output


def test_report_specific_run_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    _make_run(repo, "run-a", chosen="mock_a", candidate_score=70.0)
    _make_run(repo, "run-b", chosen="mock_b", candidate_score=90.0)

    result = runner.invoke(
        app, ["report", "--run", "run-a", "--config", str(cfg)]
    )
    assert result.exit_code == 0
    assert "Run run-a — feature" in result.output
    assert "mock_a" in result.output
    assert "mock_b" not in result.output


def test_report_with_fallback_history_rows(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    _make_run(repo, "run-x", with_fallback=True)
    result = runner.invoke(
        app, ["report", "--run", "run-x", "--config", str(cfg)]
    )
    assert result.exit_code == 0
    assert "## Fallback history" in result.output
    assert "codex_sub_cli" in result.output
    assert "usage_limit_hit" in result.output


def test_report_with_comparison_artifact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    _make_run(repo, "run-c", with_comparison=True)
    result = runner.invoke(
        app, ["report", "--run", "run-c", "--config", str(cfg)]
    )
    assert result.exit_code == 0
    # Pointer only (not full body)
    assert "- Comparison: `comparison.md`" in result.output
    assert "Dummy table." not in result.output


def test_report_format_json_uses_decision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    _make_run(repo, "run-j")
    result = runner.invoke(
        app,
        ["report", "--run", "run-j", "--format", "json", "--config", str(cfg)],
    )
    assert result.exit_code == 0
    # Header + decision.json contents.
    assert "# state:" in result.output
    assert "score_threshold_met" in result.output


def test_report_format_state_emits_summary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    _make_run(repo, "run-s")
    result = runner.invoke(
        app,
        ["report", "--run", "run-s", "--format", "state", "--config", str(cfg)],
    )
    assert result.exit_code == 0
    assert "state: completed" in result.output
    # Embedded JSON dump of the run document.
    assert '"workflow": "feature"' in result.output


def test_report_unknown_format_errors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = _write_config(repo)
    _make_run(repo, "run-u")
    result = runner.invoke(
        app,
        ["report", "--run", "run-u", "--format", "vibes", "--config", str(cfg)],
    )
    assert result.exit_code == 2
    assert "Unknown format" in result.output
