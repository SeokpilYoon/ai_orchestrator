"""Score calculator + evaluation bundle.

Authoritative reference: docs/plan/01 §10.2, docs/plan/02 §5.9, docs/plan/03 DEVF-037.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from devforge.core.config_loader import ScoringConfig
from devforge.evaluators.command_policy_checker import CommandPolicyResult
from devforge.evaluators.file_policy_checker import FilePolicyResult
from devforge.evaluators.secret_scanner import SecretScanResult
from devforge.evaluators.test_mutation_checker import TestMutationResult
from devforge.evaluators.validation_runner import ValidationReport

ReviewerVerdict = Literal["pass", "needs_revision", "reject", "unknown"]


@dataclass
class EvaluationBundle:
    """All the deterministic signals the Judge consumes."""

    validation: ValidationReport
    file_policy: FilePolicyResult
    command_policy: CommandPolicyResult
    secret_scan: SecretScanResult
    test_mutation: TestMutationResult
    reviewer_verdict: ReviewerVerdict = "unknown"
    acceptance_coverage: float = 0.0          # 0.0..1.0
    diff_size_lines: int = 0
    previous_best_score: float = 0.0
    critical_review_issues: int = 0

    # Derived properties used by the judge
    @property
    def build_pass(self) -> bool:
        r = self.validation.results.get("build")
        return r.passed if r else True   # treat 'no build command' as pass

    @property
    def tests_pass(self) -> bool:
        r = self.validation.results.get("test")
        return r.passed if r else True

    @property
    def lint_pass(self) -> bool:
        r = self.validation.results.get("lint")
        return r.passed if r else True

    @property
    def typecheck_pass(self) -> bool:
        r = self.validation.results.get("typecheck")
        return r.passed if r else True

    @property
    def blocked_file_modified(self) -> bool:
        return self.file_policy.has_blocked

    @property
    def secret_detected(self) -> bool:
        return self.secret_scan.has_secret

    @property
    def test_deleted_or_weakened(self) -> bool:
        return self.test_mutation.has_concern

    @property
    def command_policy_blocked(self) -> bool:
        return self.command_policy.has_blocked


@dataclass
class ScoreBreakdown:
    base_total: int = 0
    penalty_total: int = 0
    score: float = 0.0
    contributions: dict[str, int] = field(default_factory=dict)
    penalties: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def calculate_score(bundle: EvaluationBundle, scoring: ScoringConfig) -> ScoreBreakdown:
    """Combine deterministic signals into a single 0..(some max) score.

    Caps at the sum of positive contributions; negative penalties can drive
    the result below zero, which the Judge interprets as ``discard``.
    """
    contributions: dict[str, int] = {}
    if bundle.build_pass:
        contributions["build_pass"] = scoring.build_pass
    if bundle.tests_pass:
        contributions["tests_pass"] = scoring.tests_pass
    if bundle.lint_pass:
        contributions["lint_pass"] = scoring.lint_pass
    if bundle.typecheck_pass:
        contributions["typecheck_pass"] = scoring.typecheck_pass
    if scoring.acceptance_coverage > 0:
        contributions["acceptance_coverage"] = int(
            round(scoring.acceptance_coverage * max(0.0, min(1.0, bundle.acceptance_coverage)))
        )
    if bundle.reviewer_verdict == "pass":
        contributions["reviewer_pass"] = scoring.reviewer_pass

    penalties: dict[str, int] = {}
    if bundle.blocked_file_modified:
        penalties["blocked_file_modified"] = scoring.blocked_file_modified
    if bundle.secret_detected:
        penalties["secret_detected"] = scoring.secret_detected
    if bundle.test_mutation.deleted_tests:
        penalties["test_deleted"] = scoring.test_deleted
    if bundle.test_mutation.weakened_tests or bundle.test_mutation.suspicious_changes:
        penalties["test_weakened"] = scoring.test_weakened
    if bundle.diff_size_lines > 2000:
        penalties["unrelated_large_diff"] = scoring.unrelated_large_diff
    if bundle.critical_review_issues > 0:
        penalties["critical_review_issue"] = (
            scoring.critical_review_issue * bundle.critical_review_issues
        )

    base_total = sum(contributions.values())
    penalty_total = sum(penalties.values())
    return ScoreBreakdown(
        base_total=base_total,
        penalty_total=penalty_total,
        score=float(base_total - penalty_total),
        contributions=contributions,
        penalties=penalties,
    )
