from __future__ import annotations

from pathlib import Path

from devforge.core.run_context import create_run_context
from devforge.stages.final_report import CandidateSummary, write_final_report


def test_final_report_written(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ctx = create_run_context(project, workflow="feature", input_path=None)
    cs = [
        CandidateSummary(
            candidate_id="cand1",
            provider_id="mock",
            score=95.0,
            decision="accept",
            reason="score_threshold_met",
            validation_pass={"test": True, "build": True},
            changed_files=["src/x.py"],
            review_verdict="pass",
        ),
    ]
    report = write_final_report(ctx, "do the thing", cs, cs[0])
    text = report.read_text(encoding="utf-8")
    assert "cand1" in text
    assert "accept" in text
    assert "score 95.0" in text or "Score: **95.0**" in text


def test_final_report_no_candidates(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ctx = create_run_context(project, workflow="feature", input_path=None)
    report = write_final_report(ctx, "task", [], None)
    text = report.read_text(encoding="utf-8")
    assert "No candidates" in text or "_none_" in text
