"""Shared candidate execution + evaluation loop.

Extracted from :mod:`devforge.stages.feature_driver` so the
``vertical_slice_implementer`` stage (DEVF-067) can reuse the same
implementer → validation → reviewer → judge → revision-loop machinery
without forking it. ``feature_driver`` still owns the workflow-level
orchestration; this module owns one candidate's lifecycle.

No behavior change vs. the prior private helpers — same signatures,
same artifacts. The public functions are:

- :func:`execute_candidate`           run one provider through implementer + revision loop
- :func:`execute_with_fallback`       try providers in order, promote first success
- :func:`run_revision_loop`           evaluate + iterate one candidate up to ``max_iterations_per_task``
- :func:`evaluate_iteration`          single iteration: reviewer → validation → policy → score → judge
- :func:`failure_summary`             build a :class:`CandidateSummary` for an implementer that never produced anything
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from devforge.core.config_loader import DevforgeConfig
from devforge.core.role_router import RoleRouter
from devforge.core.run_context import RunContext
from devforge.evaluators.command_policy_checker import check_command_policy
from devforge.evaluators.file_policy_checker import check_file_policy
from devforge.evaluators.judge import Decision, decide
from devforge.evaluators.score_calculator import EvaluationBundle, calculate_score
from devforge.evaluators.secret_scanner import scan_diff_and_logs
from devforge.evaluators.test_mutation_checker import check_test_mutation
from devforge.evaluators.validation_runner import run_validation, save_validation_report
from devforge.git.diff_collector import collect_diff
from devforge.git.worktree_manager import WorktreeManager
from devforge.providers.registry import ProviderRegistry
from devforge.stages.fallback import FallbackEntry, run_with_fallback
from devforge.stages.final_report import CandidateSummary
from devforge.stages.implementer_stage import (
    CandidateResult,
    re_run_implementer_in_existing_worktree,
    run_implementer_stage,
)
from devforge.stages.reviewer_stage import run_reviewer_stage
from devforge.stages.revision_loop import build_revision_prompt, snapshot_iteration

TERMINAL_VERDICTS = {"accept", "discard", "human_review"}


@dataclass
class CandidateOutcome:
    """Internal record of a candidate's implementer outcome (for fallback)."""

    candidate: CandidateResult | None
    summary: CandidateSummary | None


# ---------------------------------------------------------------------------
# Per-candidate execution
# ---------------------------------------------------------------------------

def execute_candidate(
    provider_id: str,
    *,
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    registry: ProviderRegistry,
    router: RoleRouter,
    worktree_mgr: WorktreeManager,
    task_text: str,
    repo_context_md: str,
    acceptance: str,
    reviewer_override: str | None,
) -> CandidateSummary | None:
    """Run one provider through implementer + revision loop.

    Returns ``None`` only if the implementer itself failed (caller may fallback).
    Otherwise returns the final :class:`CandidateSummary`.
    """
    initial = run_implementer_stage(
        cfg=cfg,
        run_ctx=run_ctx,
        registry=registry,
        worktree_mgr=worktree_mgr,
        provider_ids=[provider_id],
        task_text=task_text,
        repo_context=repo_context_md,
        acceptance_criteria=acceptance,
    )
    if not initial:
        return None
    cand = initial[0]
    if not cand.agent_result.success:
        return None  # signal fallback opportunity to the caller

    return run_revision_loop(
        cand=cand,
        cfg=cfg,
        run_ctx=run_ctx,
        registry=registry,
        router=router,
        task_text=task_text,
        acceptance=acceptance,
        reviewer_override=reviewer_override,
    )


def execute_with_fallback(
    provider_ids: list[str],
    *,
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    registry: ProviderRegistry,
    router: RoleRouter,
    worktree_mgr: WorktreeManager,
    task_text: str,
    repo_context_md: str,
    acceptance: str,
    reviewer_override: str | None,
) -> tuple[CandidateSummary | None, list[FallbackEntry]]:
    """Try providers in order. Stop at first success."""

    def runner(pid: str) -> CandidateOutcome:
        # Run implementer alone (no revision loop yet — that comes after we commit
        # to this provider).
        initial = run_implementer_stage(
            cfg=cfg,
            run_ctx=run_ctx,
            registry=registry,
            worktree_mgr=worktree_mgr,
            provider_ids=[pid],
            task_text=task_text,
            repo_context=repo_context_md,
            acceptance_criteria=acceptance,
        )
        cand = initial[0] if initial else None
        return CandidateOutcome(candidate=cand, summary=None)

    def is_success(outcome: CandidateOutcome) -> bool:
        return outcome.candidate is not None and outcome.candidate.agent_result.success

    def classify(outcome: CandidateOutcome) -> tuple[str | None, str | None]:
        if outcome.candidate is None:
            return ("unknown", "no candidate produced")
        ar = outcome.candidate.agent_result
        return (ar.failure_class, ar.error)

    outcome, history = run_with_fallback(
        provider_ids,
        runner=runner,
        is_success=is_success,
        classify=classify,
    )
    if outcome is None or not is_success(outcome):
        # Build a failure summary if we have *some* candidate to point at.
        if outcome is not None and outcome.candidate is not None:
            return failure_summary(outcome.candidate), history
        return None, history

    # Promote the successful candidate through the revision loop.
    summary = run_revision_loop(
        cand=outcome.candidate,
        cfg=cfg,
        run_ctx=run_ctx,
        registry=registry,
        router=router,
        task_text=task_text,
        acceptance=acceptance,
        reviewer_override=reviewer_override,
    )
    return summary, history


# ---------------------------------------------------------------------------
# Revision loop
# ---------------------------------------------------------------------------

def run_revision_loop(
    *,
    cand: CandidateResult,
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    registry: ProviderRegistry,
    router: RoleRouter,
    task_text: str,
    acceptance: str,
    reviewer_override: str | None,
) -> CandidateSummary:
    """Evaluate ``cand`` and run up to ``max_iterations_per_task`` revisions."""
    cand_dir = run_ctx.candidate_dir(cand.candidate_id)
    original_prompt = (cand_dir / "prompt.md").read_text(encoding="utf-8") if (
        cand_dir / "prompt.md"
    ).exists() else ""

    prev_score: float | None = None
    iteration = 0
    last_summary: CandidateSummary | None = None
    last_decision: Decision | None = None
    max_iter = max(1, cfg.mode.max_iterations_per_task)

    while True:
        summary, decision, review_payload = evaluate_iteration(
            cand=cand,
            cfg=cfg,
            run_ctx=run_ctx,
            registry=registry,
            router=router,
            task_text=task_text,
            acceptance=acceptance,
            reviewer_override=reviewer_override,
        )
        last_summary = summary
        last_decision = decision

        snapshot_iteration(cand_dir, iteration)

        # Revision only continues on "revise". All other verdicts (accept /
        # discard / human_review / keep_candidate_but_continue) terminate.
        if decision.verdict != "revise":
            break
        if iteration + 1 >= max_iter:
            break
        if prev_score is not None and summary.score <= prev_score:
            break

        # Re-run implementer in the same worktree with a revision prompt.
        provider = registry.get(cand.provider_id)
        if provider is None or cand.worktree is None:
            break
        revision_prompt = build_revision_prompt(
            original_prompt=original_prompt,
            review_payload=review_payload,
            iteration=iteration + 1,
        )
        new_result = re_run_implementer_in_existing_worktree(
            provider=provider,
            worktree=cand.worktree,
            run_id=run_ctx.run_id,
            cand_dir=cand_dir,
            prompt=revision_prompt,
            timeout_sec=cfg.providers[cand.provider_id].timeout_sec
            if cand.provider_id in cfg.providers
            else 900,
            allowed_paths=cfg.file_policy.allowed_paths,
            blocked_paths=cfg.file_policy.blocked_paths,
            metadata={"workflow": run_ctx.workflow, "iteration": iteration + 1},
        )
        cand.agent_result = new_result
        if not new_result.success:
            # Take one more snapshot so the failure is visible, then stop.
            iteration += 1
            snapshot_iteration(cand_dir, iteration)
            break
        cand.diff = collect_diff(
            worktree_path=cand.worktree.path,
            base_branch=cfg.project.default_branch,
            output_dir=cand_dir,
        )

        prev_score = summary.score
        iteration += 1

    assert last_summary is not None and last_decision is not None
    return last_summary


# ---------------------------------------------------------------------------
# Single-iteration evaluation
# ---------------------------------------------------------------------------

def evaluate_iteration(
    *,
    cand: CandidateResult,
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    registry: ProviderRegistry,
    router: RoleRouter,
    task_text: str,
    acceptance: str,
    reviewer_override: str | None,
) -> tuple[CandidateSummary, Decision, dict | None]:
    cand_dir = run_ctx.candidate_dir(cand.candidate_id)

    rv_decision = router.select(
        "reviewer", override=reviewer_override, avoid_provider=cand.provider_id
    )
    review_verdict = "unknown"
    critical_count = 0
    review_payload: dict | None = None
    if rv_decision.selected:
        review = run_reviewer_stage(
            cfg=cfg,
            run_ctx=run_ctx,
            registry=registry,
            reviewer_provider_id=rv_decision.selected[0],
            candidate_dir=cand_dir,
            task_text=task_text,
            acceptance_criteria=acceptance,
        )
        review_verdict = review.verdict
        critical_count = review.critical_count
        review_payload = review.raw_json

    validation_cwd = cand.worktree.path if cand.worktree else Path(cfg.project.root)
    validation = run_validation(validation_cwd, cfg.validation)
    save_validation_report(validation, cand_dir / "validation.json")

    diff_text = ""
    diff_path = cand_dir / "diff.patch"
    if diff_path.exists():
        diff_text = diff_path.read_text(encoding="utf-8")

    changed = cand.agent_result.changed_files
    if not changed and cand.diff:
        changed = cand.diff.changed_files

    file_policy = check_file_policy(changed, cfg.file_policy)
    cmd_policy = check_command_policy(
        [cand.agent_result.stdout, cand.agent_result.stderr], cfg.command_policy
    )
    secret = scan_diff_and_logs(
        diff_text=diff_text,
        stdout=cand.agent_result.stdout,
        stderr=cand.agent_result.stderr,
        changed_files=changed,
    )
    mutation = check_test_mutation(diff_text, changed)

    (cand_dir / "policy.json").write_text(
        json.dumps(
            {
                "file_policy": file_policy.to_dict(),
                "command_policy": cmd_policy.to_dict(),
                "secret_scan": secret.to_dict(),
                "test_mutation": mutation.to_dict(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    bundle = EvaluationBundle(
        validation=validation,
        file_policy=file_policy,
        command_policy=cmd_policy,
        secret_scan=secret,
        test_mutation=mutation,
        reviewer_verdict=review_verdict,  # type: ignore[arg-type]
        acceptance_coverage=0.0,
        diff_size_lines=diff_text.count("\n"),
        previous_best_score=0.0,
        critical_review_issues=critical_count,
    )
    score = calculate_score(bundle, cfg.scoring)
    (cand_dir / "score.json").write_text(
        json.dumps(score.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    decision = decide(bundle, score, cfg.stop_conditions)
    (cand_dir / "decision.json").write_text(
        json.dumps(decision.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    summary = CandidateSummary(
        candidate_id=cand.candidate_id,
        provider_id=cand.provider_id,
        score=score.score,
        decision=decision.verdict,
        reason=decision.reason,
        validation_pass={k: v.passed for k, v in validation.results.items()},
        changed_files=list(changed),
        review_verdict=review_verdict,
        error=cand.agent_result.error,
    )
    return summary, decision, review_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def failure_summary(cand: CandidateResult) -> CandidateSummary:
    return CandidateSummary(
        candidate_id=cand.candidate_id,
        provider_id=cand.provider_id,
        score=0.0,
        decision="discard",
        reason=f"implementer_failed:{cand.agent_result.failure_class or 'unknown'}",
        validation_pass={},
        changed_files=list(cand.agent_result.changed_files),
        review_verdict="unknown",
        error=cand.agent_result.error,
    )
