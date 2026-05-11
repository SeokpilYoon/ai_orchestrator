"""Task normalizer — deterministic conversion of task.md → NormalizedTask.

Authoritative reference: docs/plan/03 DEVF-040.

Heuristic-only. No LLM dependency. The output is consumed by the implementation
plan generator (DEVF-042) and rendered into the implementer prompt.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class NormalizedTask:
    goal: str
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    likely_files: list[str] = field(default_factory=list)
    risk_level: str = "low"             # "low" | "medium" | "high"
    workflow_recommendation: str = "feature"  # "feature" | "bugfix" | "refactor"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# Section header aliases (lowercase, stripped).
_GOAL_KEYS = {"goal", "목표", "summary", "요약"}
_CONSTRAINT_KEYS = {"constraints", "제약", "constraint", "rules"}
_ACCEPTANCE_KEYS = {
    "acceptance criteria",
    "acceptance",
    "수락 기준",
    "수락기준",
    "acceptance_criteria",
}

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_LIST_ITEM = re.compile(r"^\s*[-*+]\s+(.+?)\s*$", re.MULTILINE)
_BACKTICK_PATH = re.compile(r"`([^`\s]+\.(?:py|ts|tsx|js|jsx|yaml|yml|toml|md|json|sql|rs|go))`")
_BARE_PATH = re.compile(r"(?<![\w/])((?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx|js|jsx|yaml|yml|toml|json|sql|rs|go))")

_HIGH_RISK_KEYWORDS = (
    "delete",
    "drop",
    "migration",
    "schema change",
    "rewrite",
    "breaking change",
    "삭제",
    "마이그레이션",
    "스키마 변경",
)
_REFACTOR_KEYWORDS = ("refactor", "리팩터", "리팩토링")
_BUGFIX_KEYWORDS = ("bug", "fix", "버그", "수정")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_task(task_text: str, repo_root: Path | None = None) -> NormalizedTask:
    """Parse a markdown task into a :class:`NormalizedTask` using heuristics."""
    text = task_text or ""
    sections = _split_sections(text)

    goal = _extract_goal(sections, text)
    constraints = _extract_section_list(sections, _CONSTRAINT_KEYS)
    acceptance = _extract_section_list(sections, _ACCEPTANCE_KEYS)
    likely_files = _extract_likely_files(text, repo_root)
    risk_level = _infer_risk(text)
    workflow = _infer_workflow(text)

    return NormalizedTask(
        goal=goal,
        constraints=constraints,
        acceptance_criteria=acceptance,
        likely_files=likely_files,
        risk_level=risk_level,
        workflow_recommendation=workflow,
    )


def save_normalized_task(task: NormalizedTask, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _normalize_key(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _split_sections(text: str) -> dict[str, str]:
    """Split markdown into ``{normalized_heading: body}``."""
    sections: dict[str, str] = {}
    matches = list(_HEADING.finditer(text))
    if not matches:
        return sections
    for i, m in enumerate(matches):
        title = _normalize_key(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()
    return sections


def _extract_goal(sections: dict[str, str], full_text: str) -> str:
    for key in _GOAL_KEYS:
        if key in sections and sections[key]:
            return _first_paragraph(sections[key])
    # No explicit goal section — first non-empty, non-heading paragraph.
    return _first_paragraph(_strip_headings(full_text))


def _first_paragraph(text: str) -> str:
    for chunk in text.split("\n\n"):
        c = chunk.strip()
        if c and not c.startswith("#"):
            return _collapse_whitespace(c)
    return ""


def _strip_headings(text: str) -> str:
    return _HEADING.sub("", text)


def _collapse_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _extract_section_list(sections: dict[str, str], keys: set[str]) -> list[str]:
    for key in keys:
        if key in sections:
            return _extract_list_items(sections[key])
    return []


def _extract_list_items(body: str) -> list[str]:
    items = [m.group(1).strip() for m in _LIST_ITEM.finditer(body)]
    return [it for it in items if it]


def _extract_likely_files(text: str, repo_root: Path | None) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _BACKTICK_PATH.finditer(text):
        path = match.group(1)
        if path not in seen:
            seen.add(path)
            found.append(path)
    for match in _BARE_PATH.finditer(text):
        path = match.group(1)
        if path not in seen:
            seen.add(path)
            found.append(path)
    # If we have a repo root, prefer paths that actually exist (don't filter out
    # ones we can't confirm — just sort confirmed ones first).
    if repo_root and repo_root.exists():
        confirmed: list[str] = []
        unconfirmed: list[str] = []
        for p in found:
            if (repo_root / p).exists():
                confirmed.append(p)
            else:
                unconfirmed.append(p)
        return confirmed + unconfirmed
    return found


def _infer_risk(text: str) -> str:
    blob = text.lower()
    high_hits = sum(1 for kw in _HIGH_RISK_KEYWORDS if kw in blob)
    if high_hits >= 2:
        return "high"
    if high_hits == 1:
        return "medium"
    # Very long tasks are at least medium risk.
    if len(text) > 4000:
        return "medium"
    return "low"


def _infer_workflow(text: str) -> str:
    blob = text.lower()
    if any(kw in blob for kw in _REFACTOR_KEYWORDS):
        return "refactor"
    if any(kw in blob for kw in _BUGFIX_KEYWORDS):
        return "bugfix"
    return "feature"
