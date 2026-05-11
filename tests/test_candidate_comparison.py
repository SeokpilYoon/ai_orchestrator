from __future__ import annotations

from pathlib import Path

from devforge.core.run_context import create_run_context
from devforge.stages.candidate_comparison import write_comparison_report
from devforge.stages.final_report import CandidateSummary


def _summary(cid: str, provider: str, score: float, decision: str, **vals) -> CandidateSummary:
    return CandidateSummary(
        candidate_id=cid,
        provider_id=provider,
        score=score,
        decision=decision,
        reason="ok",
        validation_pass={"build": True, "test": True, **vals},
        changed_files=["src/x.py"],
        review_verdict="pass",
    )


def test_single_candidate_no_report(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ctx = create_run_context(project, workflow="feature", input_path=None)
    result = write_comparison_report(ctx, [_summary("a", "a", 80, "accept")])
    assert result is None
    assert not (ctx.root / "comparison.md").exists()


def test_two_candidates_table(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ctx = create_run_context(project, workflow="feature", input_path=None)
    summaries = [
        _summary("a", "codex_sub_cli", 70, "discard"),
        _summary("b", "claude_sub_cli", 90, "accept"),
    ]
    result = write_comparison_report(ctx, summaries)
    assert result is not None
    text = result.read_text(encoding="utf-8")
    # higher score should come first; chosen marker bolded
    a_pos = text.index("codex_sub_cli")
    b_pos = text.index("claude_sub_cli")
    assert b_pos < a_pos
    assert "**b**" in text
    assert "Margin over `a`" in text


def test_validation_marks(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ctx = create_run_context(project, workflow="feature", input_path=None)
    summaries = [
        _summary("a", "p1", 50, "revise", test=False),
        _summary("b", "p2", 80, "accept"),
    ]
    out = write_comparison_report(ctx, summaries)
    text = out.read_text(encoding="utf-8")
    assert "PASS" in text
    assert "FAIL" in text
