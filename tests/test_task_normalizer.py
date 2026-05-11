from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.stages.task_normalizer import (
    NormalizedTask,
    normalize_task,
    save_normalized_task,
)


def test_full_structured_task() -> None:
    text = """# Title

## Goal

Add a health endpoint.

## Constraints

- Do not modify the database
- No new dependencies

## Acceptance Criteria

- `GET /health` returns 200
- Response is `{"status": "ok"}`
- A pytest test covers it

Files of interest: `src/app/main.py`, `tests/test_health.py`.
"""
    t = normalize_task(text)
    assert "health endpoint" in t.goal.lower()
    assert "Do not modify the database" in t.constraints
    assert any("GET /health" in c for c in t.acceptance_criteria)
    assert "src/app/main.py" in t.likely_files
    assert "tests/test_health.py" in t.likely_files
    assert t.workflow_recommendation == "feature"


def test_korean_headings() -> None:
    text = """## 목표

새로운 엔드포인트를 추가한다.

## 수락 기준

- /ping 200 반환
- 응답은 pong

## 제약

- 기존 테스트 유지
"""
    t = normalize_task(text)
    assert "엔드포인트" in t.goal
    assert any("ping" in c for c in t.acceptance_criteria)
    assert "기존 테스트 유지" in t.constraints


def test_no_headings_fallback_goal() -> None:
    text = "Just a plain instruction to add foo.\n\nMore details follow."
    t = normalize_task(text)
    assert "plain instruction" in t.goal
    assert t.constraints == []
    assert t.acceptance_criteria == []


def test_workflow_bugfix() -> None:
    t = normalize_task("# Bug\n\nFix the login bug where users see an error.\n")
    assert t.workflow_recommendation == "bugfix"


def test_workflow_refactor() -> None:
    t = normalize_task("# Cleanup\n\nRefactor the auth module to reduce duplication.\n")
    assert t.workflow_recommendation == "refactor"


def test_risk_high_from_multiple_keywords() -> None:
    t = normalize_task("Drop the old table and migrate schema. Delete legacy code.")
    assert t.risk_level == "high"


def test_risk_medium_from_single_keyword() -> None:
    t = normalize_task("Refactor — no migration needed but rename helpers.")
    # 'migration' alone (one keyword) → medium; refactor keyword isn't risk.
    assert t.risk_level == "medium"


def test_risk_low_default() -> None:
    t = normalize_task("Add a small button to the dashboard.")
    assert t.risk_level == "low"


def test_likely_files_confirmed_first(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "real.py").write_text("x", encoding="utf-8")
    text = "Change `src/real.py` and also `src/ghost.py`."
    t = normalize_task(text, repo_root=repo)
    # confirmed file comes first
    assert t.likely_files[0] == "src/real.py"
    assert "src/ghost.py" in t.likely_files


def test_bare_path_detected() -> None:
    t = normalize_task("Edit src/app/handlers.py to add the new route.")
    assert "src/app/handlers.py" in t.likely_files


def test_save_round_trip(tmp_path: Path) -> None:
    t = normalize_task("# Goal\n\nDo X.\n")
    out = tmp_path / "normalized_task.json"
    save_normalized_task(t, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["goal"] == "Do X."
    assert loaded["workflow_recommendation"] == "feature"


@pytest.mark.parametrize(
    "text,expected_workflow",
    [
        ("Just add a feature.", "feature"),
        ("Fix the bug in login.", "bugfix"),
        ("Refactor the module.", "refactor"),
    ],
)
def test_workflow_parametrized(text: str, expected_workflow: str) -> None:
    assert normalize_task(text).workflow_recommendation == expected_workflow


def test_empty_task_safe() -> None:
    t = normalize_task("")
    assert isinstance(t, NormalizedTask)
    assert t.goal == ""
    assert t.constraints == []
    assert t.workflow_recommendation == "feature"
