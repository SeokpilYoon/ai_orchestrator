"""Implementation plan generator — deterministic baseline.

Authoritative reference: docs/plan/03 DEVF-042.

Converts a :class:`NormalizedTask` + :class:`RepoContext` into an
:class:`ImplementationPlan`. If the resulting plan has no steps, the
caller (``feature_driver``) MUST abort: spec line 591 says
"계획이 없으면 구현 stage를 실행하지 않는다".
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.stages.repo_context_collector import RepoContext
from devforge.stages.task_normalizer import NormalizedTask


@dataclass
class ImplementationPlan:
    steps: list[str] = field(default_factory=list)
    files_to_change: list[str] = field(default_factory=list)
    tests_to_add_or_run: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.steps

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def generate_plan(task: NormalizedTask, repo: RepoContext) -> ImplementationPlan:
    """Produce a minimum-viable plan derived from the task + repo context."""
    steps = _derive_steps(task)
    files = _derive_files(task, repo)
    tests = _derive_tests(task, repo)
    risks = _derive_risks(task)
    return ImplementationPlan(
        steps=steps,
        files_to_change=files,
        tests_to_add_or_run=tests,
        risks=risks,
    )


def save_plan(plan: ImplementationPlan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _derive_steps(task: NormalizedTask) -> list[str]:
    steps: list[str] = []
    if task.acceptance_criteria:
        steps.extend(f"Implement: {ac}" for ac in task.acceptance_criteria)
    elif task.goal:
        steps.append(f"Implement: {task.goal}")
    if task.constraints:
        steps.append(
            "Respect constraints: " + "; ".join(task.constraints[:5])
        )
    return steps


def _derive_files(task: NormalizedTask, repo: RepoContext) -> list[str]:
    if task.likely_files:
        return list(dict.fromkeys(task.likely_files))[:20]
    return list(dict.fromkeys(repo.relevant_files))[:5]


def _derive_tests(task: NormalizedTask, repo: RepoContext) -> list[str]:
    tests: list[str] = []
    for ac in task.acceptance_criteria:
        tests.append(f"Cover: {ac}")
    for cmd in repo.test_commands:
        tests.append(f"Run: {cmd}")
    return tests


def _derive_risks(task: NormalizedTask) -> list[str]:
    risks = [f"Risk level: {task.risk_level}"]
    if task.risk_level == "high":
        risks.append("High-risk change — favour a narrow vertical slice.")
        risks.append("Confirm tests cover regression-prone paths before broadening scope.")
    elif task.risk_level == "medium":
        risks.append("Watch for hidden coupling; check related modules after changes.")
    return risks
