"""Revision loop helpers.

Authoritative reference: docs/plan/03 DEVF-045.

The actual control flow lives in ``feature_driver`` — this module owns the
prompt construction and the per-iteration snapshot copy so those concerns
can be tested independently.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

SNAPSHOT_FILES = (
    "prompt.md",
    "stdout.log",
    "stderr.log",
    "agent_result.json",
    "diff.patch",
    "changed_files.txt",
    "diff_stat.txt",
    "validation.json",
    "review_prompt.md",
    "review_stdout.log",
    "review_stderr.log",
    "review.json",
    "policy.json",
    "score.json",
    "decision.json",
)


def snapshot_iteration(cand_dir: Path, iteration: int) -> Path:
    """Copy the current iteration's artifacts into ``revision_NN/``."""
    snap = cand_dir / f"revision_{iteration:02d}"
    snap.mkdir(parents=True, exist_ok=True)
    for name in SNAPSHOT_FILES:
        src = cand_dir / name
        if src.exists() and src.is_file():
            shutil.copy2(src, snap / name)
    return snap


def build_revision_prompt(
    *,
    original_prompt: str,
    review_payload: dict[str, Any] | None,
    iteration: int,
) -> str:
    """Compose a revision prompt from the reviewer's critical/major issues."""
    critical = _issue_list(review_payload, "critical_issues")
    major = _issue_list(review_payload, "major_issues")
    recommended = ""
    if isinstance(review_payload, dict):
        recommended = str(review_payload.get("recommended_revision_prompt", "")).strip()

    lines: list[str] = []
    lines.append(f"# Revision iteration {iteration}")
    lines.append("")
    lines.append(
        "Your previous attempt is in the worktree. The reviewer found the following "
        "issues. Fix every CRITICAL item and as many MAJOR items as possible. "
        "Do not undo correct previous work. Keep all edits inside this worktree."
    )
    lines.append("")
    if critical:
        lines.append("## Critical issues (must fix)")
        for c in critical:
            lines.append(f"- {c}")
        lines.append("")
    if major:
        lines.append("## Major issues")
        for m in major:
            lines.append(f"- {m}")
        lines.append("")
    if recommended:
        lines.append("## Reviewer-suggested revision")
        lines.append(recommended)
        lines.append("")
    lines.append("## Original implementer prompt")
    lines.append("")
    lines.append(original_prompt.strip())
    return "\n".join(lines).rstrip() + "\n"


def _issue_list(payload: dict[str, Any] | None, key: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get(key, [])
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            # tolerate `{"issue": "...", ...}` shapes
            label = item.get("issue") or item.get("description") or item.get("title")
            if label:
                out.append(str(label))
    return out
