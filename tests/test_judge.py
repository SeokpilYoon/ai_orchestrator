from __future__ import annotations

from devforge.core.config_loader import ScoringConfig, StopConditions
from devforge.evaluators.command_policy_checker import CommandPolicyResult
from devforge.evaluators.file_policy_checker import FilePolicyResult
from devforge.evaluators.judge import decide
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


def test_accept_when_perfect() -> None:
    b = _bundle()
    s = calculate_score(b, ScoringConfig())
    d = decide(b, s, StopConditions())
    assert d.verdict == "accept"


def test_discard_on_secret() -> None:
    sec = SecretScanResult()
    sec.env_file_modified = True
    b = _bundle(secret_scan=sec)
    s = calculate_score(b, ScoringConfig())
    d = decide(b, s, StopConditions())
    assert d.verdict == "discard"
    assert "secret" in d.reason


def test_discard_on_blocked_file() -> None:
    fp = FilePolicyResult(blocked=[".env"])
    b = _bundle(file_policy=fp)
    s = calculate_score(b, ScoringConfig())
    d = decide(b, s, StopConditions())
    assert d.verdict == "discard"


def test_human_review_on_test_mutation() -> None:
    tm = TestMutationResult(weakened_tests=["tests/test_x.py"])
    b = _bundle(test_mutation=tm)
    s = calculate_score(b, ScoringConfig())
    d = decide(b, s, StopConditions())
    assert d.verdict == "human_review"


def test_revise_on_build_fail() -> None:
    val = ValidationReport(cwd="/x")
    val.results["build"] = CommandResult("build", "build", False, 1, 0.1)
    val.results["test"] = CommandResult("test", "test", True, 0, 0.1)
    b = _bundle(validation=val)
    s = calculate_score(b, ScoringConfig())
    d = decide(b, s, StopConditions())
    assert d.verdict == "revise"
    assert "build" in d.reason


def test_command_policy_blocked_means_discard() -> None:
    cp = CommandPolicyResult(blocked_hits=[("rm -rf", "rm -rf /tmp")])
    b = _bundle(command_policy=cp)
    s = calculate_score(b, ScoringConfig())
    d = decide(b, s, StopConditions())
    assert d.verdict == "discard"
