"""Requirements schema (DEVF-061).

Converts a :class:`PrdIntake` into a fully-validated :class:`Requirements`
object. The schema matches ``docs/plan/03 DEVF-061``: every FR has an id,
title, description, priority, acceptance_criteria, and test_strategy;
every NFR has an id, title, description, priority.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.stages.prd_intake import PrdIntake

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class FunctionalRequirement:
    id: str
    title: str
    description: str
    priority: str                # "must" | "should" | "could"
    acceptance_criteria: list[str]
    test_strategy: str           # "unit" | "integration" | "e2e" | "manual"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class NonFunctionalRequirement:
    id: str
    title: str
    description: str
    priority: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Requirements:
    functional: list[FunctionalRequirement] = field(default_factory=list)
    non_functional: list[NonFunctionalRequirement] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        # Match the spec key names (functional_requirements / non_functional_requirements).
        return {
            "functional_requirements": [fr.to_dict() for fr in self.functional],
            "non_functional_requirements": [nfr.to_dict() for nfr in self.non_functional],
            "unknowns": list(self.unknowns),
        }


class RequirementsError(Exception):
    """Raised when no functional requirements can be derived from the PRD."""


# ---------------------------------------------------------------------------
# Test-strategy keyword map
# ---------------------------------------------------------------------------

# Keywords are deliberately specific to avoid matching substrings of common
# English words (e.g. "ui" was matching inside "requirement").
_E2E_KEYWORDS = ("user journey", "screen", "navigates", "clicks", "ui flow")
_INTEGRATION_KEYWORDS = ("api", "endpoint", "/tasks", "/users", "http", "request")
_UNIT_KEYWORDS = ("pure function", "calculate", "convert", "parser")

_TITLE_MAX_LEN = 80


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_requirements(intake: PrdIntake) -> Requirements:
    """Project a :class:`PrdIntake` onto the strict requirements schema.

    Raises:
        RequirementsError: when ``intake.raw_functional`` is empty.
    """
    if not intake.raw_functional:
        raise RequirementsError("no functional requirements detected")

    unknowns: list[str] = list(intake.ambiguities)
    functional: list[FunctionalRequirement] = []
    for i, raw in enumerate(intake.raw_functional, start=1):
        fr_id = f"FR-{i:03d}"
        description = raw.title
        title = description[:_TITLE_MAX_LEN]
        priority = raw.raw_marker or "must"
        acceptance = list(raw.acceptance)
        if not acceptance:
            acceptance = ["Behavior matches the requirement description"]
            unknowns.append(f"{fr_id}: placeholder acceptance criterion inserted")
        test_strategy = _infer_test_strategy(description, acceptance)
        functional.append(
            FunctionalRequirement(
                id=fr_id,
                title=title,
                description=description,
                priority=priority,
                acceptance_criteria=acceptance,
                test_strategy=test_strategy,
            )
        )

    non_functional: list[NonFunctionalRequirement] = []
    for i, raw in enumerate(intake.raw_non_functional, start=1):
        nfr_id = f"NFR-{i:03d}"
        description = raw
        title = description[:_TITLE_MAX_LEN]
        non_functional.append(
            NonFunctionalRequirement(
                id=nfr_id, title=title, description=description, priority="must"
            )
        )

    return Requirements(
        functional=functional,
        non_functional=non_functional,
        unknowns=unknowns,
    )


def save_requirements(reqs: Requirements, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(reqs.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _infer_test_strategy(description: str, acceptance: list[str]) -> str:
    blob = (description + " " + " ".join(acceptance)).lower()
    if any(k in blob for k in _E2E_KEYWORDS):
        return "e2e"
    if any(k in blob for k in _INTEGRATION_KEYWORDS):
        return "integration"
    if any(k in blob for k in _UNIT_KEYWORDS):
        return "unit"
    return "manual"
