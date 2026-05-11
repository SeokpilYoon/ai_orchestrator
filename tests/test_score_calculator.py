from __future__ import annotations

from devforge.core.config_loader import ScoringConfig
from devforge.evaluators.command_policy_checker import CommandPolicyResult
from devforge.evaluators.file_policy_checker import FilePolicyResult
from devforge.evaluators.score_calculator import EvaluationBundle, calculate_score
from devforge.evaluators.secret_scanner import SecretScanResult
from devforge.evaluators.test_mutation_checker import TestMutationResult
from devforge.evaluators.validation_runner import CommandResult, ValidationReport


def _bundle(**overrides) -> EvaluationBundle:
    val = ValidationReport(cwd="/x")
    val.results["build"] = CommandResult("build", "build", True, 0, 0.1)
    val.results["test"] = CommandResult("test", "test", True, 0, 0.1)
    val.results["lint"] = CommandResult("lint", "lint", True, 0, 0.1)
    val.results["typecheck"] = CommandResult("typecheck", "typecheck", True, 0, 0.1)
    defaults = dict(
        validation=val,
        file_policy=FilePolicyResult(),
        command_policy=CommandPolicyResult(),
        secret_scan=SecretScanResult(),
        test_mutation=TestMutationResult(),
        reviewer_verdict="pass",
        acceptance_coverage=1.0,
    )
    defaults.update(overrides)
    return EvaluationBundle(**defaults)


def test_perfect_score() -> None:
    s = calculate_score(_bundle(), ScoringConfig())
    # 25 + 25 + 10 + 10 + 20 + 10 = 100
    assert s.score == 100.0


def test_blocked_file_penalty() -> None:
    fp = FilePolicyResult(blocked=[".env"])
    s = calculate_score(_bundle(file_policy=fp), ScoringConfig())
    # Penalty equals max base score by default, so score is non-positive.
    assert s.score <= 0
    assert "blocked_file_modified" in s.penalties
    assert s.penalties["blocked_file_modified"] >= s.base_total


def test_secret_penalty() -> None:
    sec = SecretScanResult()
    sec.env_file_modified = True
    s = calculate_score(_bundle(secret_scan=sec), ScoringConfig())
    assert "secret_detected" in s.penalties


def test_partial_acceptance_coverage() -> None:
    s = calculate_score(_bundle(acceptance_coverage=0.5), ScoringConfig())
    # 25 + 25 + 10 + 10 + 10 (half of 20) + 10 = 90
    assert s.score == 90.0
