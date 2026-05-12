"""MVP scope freeze (DEVF-062).

Takes the validated :class:`Requirements` plus the original :class:`PrdIntake`
and produces an :class:`MvpScope` summary — a deterministic must / should /
could classification of functional requirements, plus an explicit
out-of-scope list and assumption log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import (
    FunctionalRequirement,
    NonFunctionalRequirement,
    Requirements,
)

_STANDING_OUT_OF_SCOPE = [
    "Vertical slice planning and implementation (DEVF-066/067) — not in this MVP cycle",
    "Backlog implementation loop (DEVF-068/069) — not in this MVP cycle",
    "Release packaging (DEVF-071) — not in this MVP cycle",
]

_NEXT_CYCLE = [
    "DEVF-066: vertical slice planner",
    "DEVF-067: vertical slice implementer",
    "DEVF-068/069: backlog generator and implementation loop",
    "DEVF-070: acceptance coverage and validation",
    "DEVF-071: app packaging / release handoff",
]


@dataclass
class MvpScope:
    must: list[FunctionalRequirement] = field(default_factory=list)
    should: list[FunctionalRequirement] = field(default_factory=list)
    could: list[FunctionalRequirement] = field(default_factory=list)
    non_functional: list[NonFunctionalRequirement] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    next_cycle: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def freeze_mvp_scope(reqs: Requirements, intake: PrdIntake) -> MvpScope:
    """Classify requirements and bundle assumptions / out-of-scope items."""
    scope = MvpScope()
    for fr in reqs.functional:
        if fr.priority == "must":
            scope.must.append(fr)
        elif fr.priority == "should":
            scope.should.append(fr)
        elif fr.priority == "could":
            scope.could.append(fr)
        else:
            scope.must.append(fr)  # be conservative for unknown priorities
            scope.warnings.append(
                f"{fr.id}: unknown priority '{fr.priority}', treated as 'must'"
            )

    scope.non_functional = list(reqs.non_functional)

    # Out-of-scope = PRD list + standing entries describing what *this cycle*
    # does not deliver, so that downstream automation has an honest record.
    scope.out_of_scope = list(intake.out_of_scope) + list(_STANDING_OUT_OF_SCOPE)

    assumptions = list(intake.ambiguities)
    if not intake.target_users:
        assumptions.append("Assumed end-users are developers of this repo")
    scope.assumptions = assumptions

    scope.next_cycle = list(_NEXT_CYCLE)

    if not scope.must:
        scope.warnings.append(
            "MVP has no must-have requirements — verify priorities in the PRD"
        )
    return scope


def render_mvp_scope_md(scope: MvpScope) -> str:
    lines: list[str] = ["# MVP scope (frozen)", ""]

    def section(title: str, items: list[str]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if items:
            for it in items:
                lines.append(f"- {it}")
        else:
            lines.append("_none_")
        lines.append("")

    def fr_section(title: str, items: list[FunctionalRequirement]) -> None:
        rendered = [f"{fr.id} — {fr.title}" for fr in items]
        section(title, rendered)

    fr_section("Must have", scope.must)
    fr_section("Should have", scope.should)
    fr_section("Could have", scope.could)

    nfr_rendered = [f"{nfr.id} — {nfr.title}" for nfr in scope.non_functional]
    section("Non-functional requirements", nfr_rendered)

    section("Out of scope", scope.out_of_scope)
    section("Assumptions", scope.assumptions)
    section("Next cycle", scope.next_cycle)

    if scope.warnings:
        section("Warnings", scope.warnings)

    return "\n".join(lines).rstrip() + "\n"


def save_mvp_scope(scope: MvpScope, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_mvp_scope_md(scope), encoding="utf-8")
    return path
