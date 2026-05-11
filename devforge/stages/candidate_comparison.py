"""Candidate comparison report (DEVF-054).

Generates ``comparison.md`` when a run produced ≥2 candidates (tournament mode
or — rarely — a fallback chain that completed multiple candidates).
"""
from __future__ import annotations

from pathlib import Path

from devforge.core.run_context import RunContext
from devforge.stages.final_report import CandidateSummary

_HEADER = (
    "| Candidate | Provider | Score | Decision | Reviewer | "
    "Build | Test | Lint | Typecheck | Changed | Error |"
)
_SEPARATOR = "|---|---|---:|---|---|---|---|---|---|---:|---|"


def write_comparison_report(
    run_ctx: RunContext, candidates: list[CandidateSummary]
) -> Path | None:
    """Write ``comparison.md`` if there are ≥2 candidates."""
    if len(candidates) < 2:
        return None

    by_score = sorted(candidates, key=lambda c: c.score, reverse=True)
    chosen_id = by_score[0].candidate_id

    lines: list[str] = []
    lines.append(f"# Candidate comparison — run {run_ctx.run_id}")
    lines.append("")
    lines.append(f"- Number of candidates: **{len(candidates)}**")
    lines.append(f"- Best score: **{by_score[0].score:.1f}** ({by_score[0].candidate_id})")
    lines.append("")
    lines.append(_HEADER)
    lines.append(_SEPARATOR)
    for c in by_score:
        prefix = "**" if c.candidate_id == chosen_id else ""
        suffix = "**" if c.candidate_id == chosen_id else ""
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{prefix}{c.candidate_id}{suffix}",
                    c.provider_id,
                    f"{c.score:.1f}",
                    c.decision,
                    c.review_verdict,
                    _check(c, "build"),
                    _check(c, "test"),
                    _check(c, "lint"),
                    _check(c, "typecheck"),
                    str(len(c.changed_files)),
                    (c.error or "")[:40],
                ]
            )
            + " |"
        )
    lines.append("")

    # Per-candidate change lists (short).
    lines.append("## Changed files")
    for c in by_score:
        lines.append(f"### {c.candidate_id}")
        if not c.changed_files:
            lines.append("_no changes_")
        else:
            for f in c.changed_files[:20]:
                lines.append(f"- `{f}`")
            if len(c.changed_files) > 20:
                lines.append(f"- … and {len(c.changed_files) - 20} more")
        lines.append("")

    # Reason it was chosen.
    lines.append("## Why this candidate ranks first")
    lines.append(f"- `{chosen_id}`: {by_score[0].reason}")
    if len(by_score) >= 2:
        runner_up = by_score[1]
        delta = by_score[0].score - runner_up.score
        lines.append(
            f"- Margin over `{runner_up.candidate_id}`: **{delta:+.1f}** points "
            f"(runner-up score {runner_up.score:.1f}, decision `{runner_up.decision}`)"
        )

    text = "\n".join(lines).rstrip() + "\n"
    out = run_ctx.root / "comparison.md"
    out.write_text(text, encoding="utf-8")
    return out


def _check(c: CandidateSummary, key: str) -> str:
    val = c.validation_pass.get(key)
    if val is True:
        return "PASS"
    if val is False:
        return "FAIL"
    return "—"
