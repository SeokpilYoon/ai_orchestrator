"""Judge — deterministic accept/revise/discard/human_review decision.

Authoritative reference: docs/plan/01 §10.3, docs/plan/02 §5.10, docs/plan/03 DEVF-038.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from devforge.core.config_loader import StopConditions
from devforge.evaluators.score_calculator import EvaluationBundle, ScoreBreakdown

Verdict = Literal["accept", "revise", "discard", "human_review", "keep_candidate_but_continue"]


@dataclass
class Decision:
    verdict: Verdict
    reason: str
    score: float
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def decide(
    bundle: EvaluationBundle,
    score: ScoreBreakdown,
    stop: StopConditions,
) -> Decision:
    """Map ``(bundle, score)`` to a verdict.

    Order of checks matters — safety gates come first.
    """
    details: dict[str, object] = {
        "score_breakdown": score.to_dict(),
        "reviewer_verdict": bundle.reviewer_verdict,
        "acceptance_coverage": bundle.acceptance_coverage,
    }

    # --- safety gates ----------------------------------------------------
    if stop.discard_when.secret_detected and bundle.secret_detected:
        return Decision("discard", "secret_detected", score.score, details)

    if stop.discard_when.blocked_file_modified and bundle.blocked_file_modified:
        return Decision(
            "discard",
            f"blocked_file_modified: {bundle.file_policy.blocked}",
            score.score,
            details,
        )

    if bundle.command_policy_blocked:
        return Decision(
            "discard",
            f"command_policy_violation: {[p for p, _ in bundle.command_policy.blocked_hits]}",
            score.score,
            details,
        )

    if bundle.test_deleted_or_weakened:
        return Decision(
            "human_review",
            "test_integrity_risk",
            score.score,
            details,
        )

    # --- functional gates -----------------------------------------------
    if not bundle.build_pass:
        return Decision("revise", "build_failed", score.score, details)
    if not bundle.tests_pass:
        return Decision("revise", "tests_failed", score.score, details)

    # --- acceptance ------------------------------------------------------
    accept = stop.accept_when
    reviewer_ok = (not accept.reviewer_verdict) or (bundle.reviewer_verdict == accept.reviewer_verdict)
    if (
        (not accept.build_pass or bundle.build_pass)
        and (not accept.tests_pass or bundle.tests_pass)
        and reviewer_ok
        and score.score >= accept.min_score
    ):
        return Decision("accept", "score_threshold_met", score.score, details)

    # --- improvement check ----------------------------------------------
    if score.score > bundle.previous_best_score:
        return Decision(
            "keep_candidate_but_continue",
            "improved_over_previous_best",
            score.score,
            details,
        )

    if bundle.reviewer_verdict == "needs_revision":
        return Decision("revise", "reviewer_needs_revision", score.score, details)

    return Decision("discard", "no_improvement", score.score, details)
