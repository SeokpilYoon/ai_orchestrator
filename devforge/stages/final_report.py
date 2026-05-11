"""Final report writer.

Authoritative reference: docs/plan/03 DEVF-046.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devforge.core.run_context import RunContext
from devforge.evaluators.judge import Decision


@dataclass
class CandidateSummary:
    candidate_id: str
    provider_id: str
    score: float
    decision: str
    reason: str
    validation_pass: dict[str, bool]
    changed_files: list[str]
    review_verdict: str
    error: str | None = None


def write_final_report(
    run_ctx: RunContext,
    task_text: str,
    candidates: list[CandidateSummary],
    chosen: CandidateSummary | None,
    notes: list[str] | None = None,
) -> Path:
    lines: list[str] = []
    lines.append(f"# Final Report — run {run_ctx.run_id}")
    lines.append("")
    lines.append(f"- Workflow: `{run_ctx.workflow}`")
    lines.append(f"- Created at: {run_ctx.created_at}")
    lines.append(f"- Project root: `{run_ctx.project_root}`")
    if chosen:
        lines.append(f"- Chosen candidate: **{chosen.candidate_id}** (score {chosen.score:.1f})")
        lines.append(f"- Decision: **{chosen.decision}** — {chosen.reason}")
    else:
        lines.append("- Chosen candidate: _none_")
    lines.append("")

    lines.append("## Task")
    lines.append("")
    lines.append("```")
    lines.append(task_text.strip() or "(empty task)")
    lines.append("```")
    lines.append("")

    lines.append("## Candidates")
    lines.append("")
    if not candidates:
        lines.append("_No candidates were produced._")
    for c in candidates:
        lines.append(f"### {c.candidate_id} — provider `{c.provider_id}`")
        lines.append("")
        lines.append(f"- Score: **{c.score:.1f}**")
        lines.append(f"- Decision: **{c.decision}** — {c.reason}")
        lines.append(f"- Reviewer verdict: `{c.review_verdict}`")
        if c.error:
            lines.append(f"- Error: {c.error}")
        if c.validation_pass:
            kv = ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in c.validation_pass.items())
            lines.append(f"- Validation: {kv}")
        if c.changed_files:
            lines.append("- Changed files:")
            for f in c.changed_files[:30]:
                lines.append(f"  - `{f}`")
            if len(c.changed_files) > 30:
                lines.append(f"  - … and {len(c.changed_files) - 30} more")
        lines.append("")

    if notes:
        lines.append("## Notes")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("## Next steps")
    if chosen and chosen.decision == "accept":
        lines.append(f"- Inspect the worktree for `{chosen.candidate_id}` and merge if desired.")
        lines.append(f"  Apply with: `devforge apply --run {run_ctx.run_id} --candidate {chosen.candidate_id}`")
    elif chosen and chosen.decision == "revise":
        lines.append("- The judge requested a revision. Re-run with refined task input.")
    elif chosen and chosen.decision == "human_review":
        lines.append("- A safety-critical signal was detected (e.g. test integrity). Human review required.")
    else:
        lines.append("- All candidates were discarded. Inspect logs in `.orchestrator/runs/` and try again.")

    text = "\n".join(lines).rstrip() + "\n"
    out = run_ctx.root / "final_report.md"
    out.write_text(text, encoding="utf-8")
    return out


def save_decision(run_ctx: RunContext, decision: Decision, candidate_id: str | None) -> Path:
    path = run_ctx.root / "decision.json"
    payload: dict[str, Any] = {
        "run_id": run_ctx.run_id,
        "chosen_candidate": candidate_id,
        "decision": decision.to_dict(),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
