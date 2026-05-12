from __future__ import annotations

from pathlib import Path

from devforge.stages.mvp_scope import freeze_mvp_scope, render_mvp_scope_md, save_mvp_scope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import (
    FunctionalRequirement,
    NonFunctionalRequirement,
    Requirements,
)


def _fr(idx: int, priority: str, title: str = "thing") -> FunctionalRequirement:
    return FunctionalRequirement(
        id=f"FR-{idx:03d}",
        title=title,
        description=title,
        priority=priority,
        acceptance_criteria=["does X"],
        test_strategy="manual",
    )


def test_classification_must_should_could() -> None:
    reqs = Requirements(
        functional=[
            _fr(1, "must"),
            _fr(2, "should"),
            _fr(3, "could"),
            _fr(4, "must"),
        ]
    )
    scope = freeze_mvp_scope(reqs, PrdIntake(target_users=["devs"]))
    assert [fr.id for fr in scope.must] == ["FR-001", "FR-004"]
    assert [fr.id for fr in scope.should] == ["FR-002"]
    assert [fr.id for fr in scope.could] == ["FR-003"]


def test_standing_out_of_scope_entries_added() -> None:
    intake = PrdIntake(out_of_scope=["Authentication"], target_users=["devs"])
    reqs = Requirements(functional=[_fr(1, "must")])
    scope = freeze_mvp_scope(reqs, intake)
    assert "Authentication" in scope.out_of_scope
    assert any("Backlog implementation loop" in item for item in scope.out_of_scope)
    assert any("DEVF-068" in item for item in scope.out_of_scope)
    assert any("Release packaging" in item for item in scope.out_of_scope)


def test_target_users_missing_adds_assumption() -> None:
    intake = PrdIntake(target_users=[])
    reqs = Requirements(functional=[_fr(1, "must")])
    scope = freeze_mvp_scope(reqs, intake)
    assert any("developers of this repo" in a for a in scope.assumptions)


def test_no_must_warns_user() -> None:
    intake = PrdIntake(target_users=["devs"])
    reqs = Requirements(functional=[_fr(1, "should"), _fr(2, "could")])
    scope = freeze_mvp_scope(reqs, intake)
    assert any("no must-have requirements" in w for w in scope.warnings)


def test_markdown_contains_all_sections(tmp_path: Path) -> None:
    intake = PrdIntake(target_users=["devs"])
    reqs = Requirements(
        functional=[_fr(1, "must"), _fr(2, "should")],
        non_functional=[
            NonFunctionalRequirement(
                id="NFR-001",
                title="Sub-200ms",
                description="Sub-200ms",
                priority="must",
            )
        ],
    )
    scope = freeze_mvp_scope(reqs, intake)
    md = render_mvp_scope_md(scope)
    for header in (
        "## Must have",
        "## Should have",
        "## Could have",
        "## Non-functional requirements",
        "## Out of scope",
        "## Assumptions",
        "## Next cycle",
    ):
        assert header in md
    out = tmp_path / "mvp_scope.md"
    save_mvp_scope(scope, out)
    assert out.read_text(encoding="utf-8").startswith("# MVP scope")
