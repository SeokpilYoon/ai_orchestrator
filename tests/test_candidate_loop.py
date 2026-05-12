"""Smoke test for the candidate_loop module.

The heavy regression coverage stays in ``test_feature_workflow.py``,
``test_revision_loop.py``, and ``test_fallback_tournament.py`` — the
feature pipeline still exercises these helpers end-to-end. This file
just verifies the public surface is importable and callable.
"""
from __future__ import annotations

from devforge.stages import candidate_loop


def test_public_surface_is_importable() -> None:
    assert callable(candidate_loop.execute_candidate)
    assert callable(candidate_loop.execute_with_fallback)
    assert callable(candidate_loop.run_revision_loop)
    assert callable(candidate_loop.evaluate_iteration)
    assert callable(candidate_loop.failure_summary)
    assert {"accept", "discard", "human_review"} == candidate_loop.TERMINAL_VERDICTS


def test_candidate_outcome_dataclass_exists() -> None:
    # Internal record used by execute_with_fallback's runner.
    assert hasattr(candidate_loop, "CandidateOutcome")
    fields = candidate_loop.CandidateOutcome.__dataclass_fields__
    assert {"candidate", "summary"} == set(fields)
