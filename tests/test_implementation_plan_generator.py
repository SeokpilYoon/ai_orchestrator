from __future__ import annotations

import json
from pathlib import Path

from devforge.stages.implementation_plan_generator import (
    ImplementationPlan,
    generate_plan,
    save_plan,
)
from devforge.stages.repo_context_collector import RepoContext
from devforge.stages.task_normalizer import NormalizedTask


def _task(**overrides) -> NormalizedTask:
    defaults = dict(goal="do X", risk_level="low", workflow_recommendation="feature")
    defaults.update(overrides)
    return NormalizedTask(**defaults)


def test_plan_from_acceptance_criteria() -> None:
    t = _task(acceptance_criteria=["A", "B"], constraints=["no deps"])
    r = RepoContext(repo_name="x")
    plan = generate_plan(t, r)
    assert "Implement: A" in plan.steps
    assert "Implement: B" in plan.steps
    assert any("constraints" in s.lower() for s in plan.steps)


def test_plan_from_goal_when_no_acceptance() -> None:
    t = _task(goal="ship the feature")
    r = RepoContext(repo_name="x")
    plan = generate_plan(t, r)
    assert plan.steps == ["Implement: ship the feature"]


def test_empty_plan_when_no_goal_and_no_acceptance() -> None:
    t = NormalizedTask(goal="", acceptance_criteria=[], workflow_recommendation="feature")
    plan = generate_plan(t, RepoContext(repo_name="x"))
    assert plan.is_empty


def test_files_prefers_task_likely() -> None:
    t = _task(likely_files=["src/a.py", "src/b.py"])
    r = RepoContext(repo_name="x", relevant_files=["other/c.py"])
    plan = generate_plan(t, r)
    assert plan.files_to_change == ["src/a.py", "src/b.py"]


def test_files_falls_back_to_repo_relevant() -> None:
    t = _task()
    r = RepoContext(repo_name="x", relevant_files=["src/c.py", "src/d.py", "src/e.py"])
    plan = generate_plan(t, r)
    assert plan.files_to_change == ["src/c.py", "src/d.py", "src/e.py"]


def test_tests_includes_acceptance_and_repo_commands() -> None:
    t = _task(acceptance_criteria=["criterion-1"])
    r = RepoContext(repo_name="x", test_commands=["pytest -q"])
    plan = generate_plan(t, r)
    assert any("criterion-1" in s for s in plan.tests_to_add_or_run)
    assert any("pytest -q" in s for s in plan.tests_to_add_or_run)


def test_high_risk_adds_notes() -> None:
    t = _task(risk_level="high", goal="rewrite db")
    plan = generate_plan(t, RepoContext(repo_name="x"))
    assert any("High-risk" in r for r in plan.risks)


def test_save_round_trip(tmp_path: Path) -> None:
    plan = ImplementationPlan(steps=["one"], files_to_change=["a.py"])
    out = tmp_path / "p.json"
    save_plan(plan, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["steps"] == ["one"]


def test_dedup_likely_files() -> None:
    t = _task(likely_files=["src/a.py", "src/a.py", "src/b.py"])
    plan = generate_plan(t, RepoContext(repo_name="x"))
    assert plan.files_to_change == ["src/a.py", "src/b.py"]
