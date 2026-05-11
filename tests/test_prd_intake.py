from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.stages.prd_intake import (
    PrdIntakeError,
    intake_prd,
    save_ambiguity_log,
    save_assumptions,
    save_out_of_scope,
    save_product_summary,
)

SAMPLE = """# Product

A tiny todo service.

## Target users

- Solo developers

## Functional requirements

- Add a task with a title (must)
  - POST /tasks returns 201
  - Unique id
- List all tasks (must)
  - GET /tasks returns JSON
- Mark a task complete (should)
- Delete a task (could)

## Non-functional requirements

- Sub-200ms responses

## Out of scope

- Authentication
"""


def test_full_parse() -> None:
    intake = intake_prd(SAMPLE)
    assert "todo service" in intake.product_summary
    assert intake.target_users == ["Solo developers"]
    assert intake.raw_non_functional == ["Sub-200ms responses"]
    assert intake.out_of_scope == ["Authentication"]
    assert len(intake.raw_functional) == 4
    titles = [fr.title for fr in intake.raw_functional]
    assert "Add a task with a title" in titles
    markers = [fr.raw_marker for fr in intake.raw_functional]
    assert markers == ["must", "must", "should", "could"]
    # Nested bullets folded into acceptance for FR-001
    assert intake.raw_functional[0].acceptance == [
        "POST /tasks returns 201",
        "Unique id",
    ]


def test_empty_prd_raises() -> None:
    with pytest.raises(PrdIntakeError):
        intake_prd("")
    with pytest.raises(PrdIntakeError):
        intake_prd("\n   \n\t\n")


def test_korean_headings() -> None:
    text = """## 제품

작은 서비스.

## 사용자

- 개발자

## 기능 요구사항

- 기능 1 (must)
- 기능 2

## 범위 외

- 인증
"""
    intake = intake_prd(text)
    assert "작은 서비스" in intake.product_summary
    assert intake.target_users == ["개발자"]
    assert len(intake.raw_functional) == 2
    assert intake.out_of_scope == ["인증"]


def test_priority_markers_extracted() -> None:
    intake = intake_prd(
        "## Functional\n\n- One (must)\n- Two (should)\n- Three (could)\n- Four\n"
    )
    markers = [fr.raw_marker for fr in intake.raw_functional]
    assert markers == ["must", "should", "could", None]
    titles = [fr.title for fr in intake.raw_functional]
    # Marker stripped from title.
    assert titles == ["One", "Two", "Three", "Four"]


def test_unknown_marker_is_ignored() -> None:
    intake = intake_prd("## Functional\n\n- One (maybe)\n")
    assert intake.raw_functional[0].raw_marker is None
    # The unknown marker stays attached to the title (we don't strip it).
    assert "(maybe)" in intake.raw_functional[0].title


def test_ambiguities_reported() -> None:
    intake = intake_prd(
        "## Functional requirements\n\n- thing\n"
    )
    issues = intake.ambiguities
    assert any("target users" in m.lower() for m in issues)
    assert any("priority" in m.lower() for m in issues)
    assert any("acceptance" in m.lower() for m in issues)


def test_zero_functional_reports_ambiguity() -> None:
    intake = intake_prd("## Product\n\nA thing.\n")
    assert any("No functional requirements" in m for m in intake.ambiguities)
    assert intake.raw_functional == []


def test_save_round_trip(tmp_path: Path) -> None:
    intake = intake_prd(SAMPLE)
    save_product_summary(intake, tmp_path / "product_summary.md")
    save_ambiguity_log(intake, tmp_path / "ambiguity_log.json")
    save_assumptions(intake, tmp_path / "assumptions.md")
    save_out_of_scope(intake, tmp_path / "out_of_scope.md")

    summary = (tmp_path / "product_summary.md").read_text(encoding="utf-8")
    assert "todo service" in summary

    log = json.loads((tmp_path / "ambiguity_log.json").read_text(encoding="utf-8"))
    assert "ambiguities" in log

    assumptions = (tmp_path / "assumptions.md").read_text(encoding="utf-8")
    assert assumptions.startswith("# Assumptions")

    oos = (tmp_path / "out_of_scope.md").read_text(encoding="utf-8")
    assert "Authentication" in oos
