"""PRD intake (DEVF-060).

Parses a markdown PRD into a structured ``PrdIntake`` and produces four
human/machine-readable artifacts:

- ``product_summary.md`` — first paragraph of the product section
- ``ambiguity_log.json`` — machine-readable list of detected ambiguities
- ``assumptions.md`` — readable narrative of working assumptions
- ``out_of_scope.md`` — explicit out-of-scope list

Heuristic-only. No LLM dependency. Output is consumed by the requirements
schema stage (DEVF-061) and the MVP scope freeze stage (DEVF-062).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class FunctionalRaw:
    """A single functional-requirements bullet plus its nested acceptance bullets."""

    title: str
    acceptance: list[str] = field(default_factory=list)
    raw_marker: str | None = None  # "must" | "should" | "could" | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class PrdIntake:
    product_summary: str = ""
    target_users: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    raw_functional: list[FunctionalRaw] = field(default_factory=list)
    raw_non_functional: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "product_summary": self.product_summary,
            "target_users": list(self.target_users),
            "constraints": list(self.constraints),
            "out_of_scope": list(self.out_of_scope),
            "raw_functional": [f.to_dict() for f in self.raw_functional],
            "raw_non_functional": list(self.raw_non_functional),
            "ambiguities": list(self.ambiguities),
        }


class PrdIntakeError(Exception):
    """Raised when the PRD is unusable (e.g. empty)."""


# ---------------------------------------------------------------------------
# Section header aliases (case-insensitive, normalized)
# ---------------------------------------------------------------------------

_PRODUCT_KEYS = {"product", "product summary", "제품", "개요", "summary", "overview"}
_FUNCTIONAL_KEYS = {
    "functional requirements",
    "functional",
    "기능 요구사항",
    "기능",
    "features",
}
_NON_FUNCTIONAL_KEYS = {
    "non-functional requirements",
    "non functional requirements",
    "nfr",
    "비기능 요구사항",
    "비기능",
}
_USERS_KEYS = {"target users", "users", "사용자", "고객"}
_CONSTRAINTS_KEYS = {"constraints", "제약", "constraint"}
_OOS_KEYS = {"out of scope", "범위 외", "out-of-scope"}

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_TOP_BULLET = re.compile(r"^([ \t]{0,1})[-*+]\s+(.+?)\s*$")
_NESTED_BULLET = re.compile(r"^(?:[ \t]{2,}|\t+)[-*+]\s+(.+?)\s*$")
_PRIORITY_MARKER = re.compile(r"\s*\((must|should|could)\)\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def intake_prd(prd_text: str) -> PrdIntake:
    """Parse a markdown PRD into a :class:`PrdIntake`.

    Raises:
        PrdIntakeError: when the PRD has no content at all.
    """
    if not (prd_text and prd_text.strip()):
        raise PrdIntakeError("PRD is empty")

    sections = _split_sections(prd_text)
    intake = PrdIntake()

    intake.product_summary = _extract_summary(sections, prd_text)
    intake.target_users = _flat_bullets(_first_match(sections, _USERS_KEYS))
    intake.constraints = _flat_bullets(_first_match(sections, _CONSTRAINTS_KEYS))
    intake.out_of_scope = _flat_bullets(_first_match(sections, _OOS_KEYS))
    intake.raw_functional = _parse_functional(_first_match(sections, _FUNCTIONAL_KEYS))
    intake.raw_non_functional = _flat_bullets(
        _first_match(sections, _NON_FUNCTIONAL_KEYS)
    )

    intake.ambiguities = _detect_ambiguities(intake)
    return intake


def save_product_summary(intake: PrdIntake, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = intake.product_summary.strip() or "_(no product summary found in PRD)_"
    path.write_text(f"# Product summary\n\n{body}\n", encoding="utf-8")
    return path


def save_ambiguity_log(intake: PrdIntake, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ambiguities": list(intake.ambiguities)}
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def save_assumptions(intake: PrdIntake, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Assumptions", ""]
    if intake.ambiguities:
        for note in intake.ambiguities:
            lines.append(f"- {note}")
    else:
        lines.append("_No ambiguities detected._")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def save_out_of_scope(intake: PrdIntake, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Out of scope", ""]
    if intake.out_of_scope:
        for item in intake.out_of_scope:
            lines.append(f"- {item}")
    else:
        lines.append("_No explicit out-of-scope items in the PRD._")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _normalize_key(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _split_sections(text: str) -> dict[str, str]:
    """Split markdown into ``{normalized_heading: body}``."""
    matches = list(_HEADING.finditer(text))
    sections: dict[str, str] = {}
    if not matches:
        return sections
    for i, m in enumerate(matches):
        title = _normalize_key(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()
    return sections


def _first_match(sections: dict[str, str], aliases: set[str]) -> str:
    for alias in aliases:
        if alias in sections:
            return sections[alias]
    return ""


def _extract_summary(sections: dict[str, str], full_text: str) -> str:
    body = _first_match(sections, _PRODUCT_KEYS)
    if body:
        return _first_paragraph(body)
    # Fallback: first non-heading paragraph of the document.
    return _first_paragraph(_HEADING.sub("", full_text))


def _first_paragraph(text: str) -> str:
    for chunk in text.split("\n\n"):
        c = chunk.strip()
        if c and not c.startswith("#"):
            return re.sub(r"\s+", " ", c).strip()
    return ""


def _flat_bullets(body: str) -> list[str]:
    """Top-level bullets only; nested ones are folded into their parent elsewhere."""
    items: list[str] = []
    for line in body.splitlines():
        m = _TOP_BULLET.match(line)
        if m:
            items.append(m.group(2).strip())
    return [it for it in items if it]


def _parse_functional(body: str) -> list[FunctionalRaw]:
    """Each top-level bullet becomes a :class:`FunctionalRaw`; the indented
    bullets that follow it become its ``acceptance`` list."""
    if not body:
        return []
    out: list[FunctionalRaw] = []
    current: FunctionalRaw | None = None
    for raw_line in body.splitlines():
        if not raw_line.strip():
            continue
        nested = _NESTED_BULLET.match(raw_line)
        if nested:
            if current is not None:
                current.acceptance.append(nested.group(1).strip())
            continue
        top = _TOP_BULLET.match(raw_line)
        if top:
            text = top.group(2).strip()
            marker = None
            marker_match = _PRIORITY_MARKER.search(text)
            if marker_match:
                marker = marker_match.group(1).lower()
                text = _PRIORITY_MARKER.sub("", text).strip()
            current = FunctionalRaw(title=text, raw_marker=marker)
            out.append(current)
    return out


def _detect_ambiguities(intake: PrdIntake) -> list[str]:
    issues: list[str] = []
    if not intake.product_summary:
        issues.append("Product summary missing")
    if not intake.target_users:
        issues.append("No target users declared; defaulting to repo developers")
    if not intake.raw_functional:
        issues.append("No functional requirements detected")
    for i, fr in enumerate(intake.raw_functional, start=1):
        if fr.raw_marker is None:
            issues.append(
                f"FR-{i:03d}: no priority marker, will default to 'must'"
            )
        if not fr.acceptance:
            issues.append(
                f"FR-{i:03d}: no acceptance criteria; a placeholder will be inserted"
            )
    return issues
