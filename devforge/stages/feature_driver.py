"""Feature-workflow driver — glues all stages together.

This is the end-to-end orchestration for MVP-1 / DEVF-040..046, DEVF-050/052,
DEVF-045 (revision loop). Full WorkflowEngine + DAG (docs/plan/02 §5.2) is a
follow-up for M5+.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devforge.core.config_loader import DevforgeConfig
from devforge.core.role_router import RoleRouter
from devforge.core.run_context import RunContext
from devforge.core.state_store import StateStore
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
from devforge.stages.final_report import CandidateSummary, save_decision, write_final_report
from devforge.stages.implementation_plan_generator import (
    ImplementationPlan,
    generate_plan,
    save_plan,
)
from devforge.stages.implementer_stage import (
    CandidateResult,
    re_run_implementer_in_existing_worktree,
    run_implementer_stage,
)
from devforge.stages.repo_context_collector import (
    RepoContext,
    collect_repo_context,
    render_repo_context_md,
    save_repo_context,
)
from devforge.stages.reviewer_stage import run_reviewer_stage
from devforge.stages.revision_loop import build_revision_prompt, snapshot_iteration
from devforge.stages.task_normalizer import (
    NormalizedTask,
    normalize_task,
    save_normalized_task,
)

TERMINAL_VERDICTS = {"accept", "discard", "human_review"}


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

_FEATURE_STAGE_IDS = [
    "normalize_task",
    "inspect_repo",
    "plan",
    "implement_candidates",
    "comparison_report",
    "final_report",
]


def run_feature_workflow(
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    implementer_override: str | None,
    reviewer_override: str | None,
    *,
    state_store: StateStore | None = None,
    definition: Any = None,    # devforge.core.workflow_engine.WorkflowDefinition
) -> None:
    """Run the feature workflow end-to-end.

    ``state_store`` is optional for backwards compatibility. When omitted the
    driver constructs a :class:`StateStore` automatically so every run still
    records its progress under ``<run_root>/state/``.
    """
    if state_store is None:
        state_store = StateStore(run_ctx.root)
        if not state_store.is_initialized():
            state_store.init_run(
                workflow=run_ctx.workflow,
                input_ref=str(run_ctx.input_path) if run_ctx.input_path else None,
                stages=list(_FEATURE_STAGE_IDS),
            )

    registry = ProviderRegistry.from_config(cfg)
    router = RoleRouter(cfg, registry)
    worktree_mgr = WorktreeManager(
        repo_root=Path(cfg.project.root),
        worktree_root=Path(cfg.project.worktree_root) if cfg.project.worktree_root else None,
    )

    # 0) artifact stages (DEVF-040/041/042)
    task_text = _read_task(run_ctx.input_path)
    state_store.save_step("normalize_task", "running")
    state_store.save_step("inspect_repo", "running")
    state_store.save_step("plan", "running")
    normalized, repo_ctx, plan = _build_artifact_stages(cfg, run_ctx, task_text)
    state_store.save_step(
        "normalize_task", "completed", artifact_ref="normalized_task.json"
    )
    state_store.save_step(
        "inspect_repo", "completed", artifact_ref="repo_context.md"
    )
    state_store.save_step("plan", "completed", artifact_ref="implementation_plan.json")
    if plan.is_empty:
        state_store.save_step(
            "implement_candidates", "skipped", note="aborted: empty plan"
        )
        _write_failure(
            run_ctx,
            "no plan generated",
            {"reason": "empty implementation_plan.steps", "task_goal": normalized.goal},
        )
        return

    # 1) provider selection
    impl_decision = router.select("implementer", override=implementer_override)
    if not impl_decision.selected:
        state_store.save_step(
            "implement_candidates", "failed", note="no implementer available"
        )
        _write_failure(run_ctx, "no implementer available", impl_decision.excluded)
        return

    repo_context_md = render_repo_context_md(repo_ctx)
    acceptance = _format_acceptance(normalized)

    summaries: list[CandidateSummary] = []
    fallback_history: list[dict[str, Any]] = []

    state_store.save_step("implement_candidates", "running")
    if impl_decision.mode == "tournament":
        # Tournament: each selected provider runs once, no fallback between them.
        for pid in impl_decision.selected:
            summary = _execute_candidate(
                pid,
                cfg=cfg,
                run_ctx=run_ctx,
                registry=registry,
                router=router,
                worktree_mgr=worktree_mgr,
                task_text=task_text,
                repo_context_md=repo_context_md,
                acceptance=acceptance,
                reviewer_override=reviewer_override,
            )
            if summary is not None:
                summaries.append(summary)
    else:
        # Single mode with fallback through impl_decision.selected.
        summary, attempts = _execute_with_fallback(
            impl_decision.selected,
            cfg=cfg,
            run_ctx=run_ctx,
            registry=registry,
            router=router,
            worktree_mgr=worktree_mgr,
            task_text=task_text,
            repo_context_md=repo_context_md,
            acceptance=acceptance,
            reviewer_override=reviewer_override,
        )
        if summary is not None:
            summaries.append(summary)
        if attempts:
            fallback_history.extend(
                {"provider": a.provider, "failure_class": a.failure_class, "error": a.error}
                for a in attempts
            )

    for s in summaries:
        state_store.save_candidate(
            candidate_id=s.candidate_id,
            provider_id=s.provider_id,
            decision=s.decision,
            score=float(s.score),
            decision_ref=f"candidates/{s.candidate_id}/decision.json",
        )
    state_store.save_step(
        "implement_candidates",
        "completed" if summaries else "failed",
        note=None if summaries else "no candidate produced",
    )

    if fallback_history:
        (run_ctx.root / "fallback_history.json").write_text(
            json.dumps({"history": fallback_history}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    _finalize(run_ctx, task_text, summaries, state_store)


# ---------------------------------------------------------------------------
# Per-candidate execution
# ---------------------------------------------------------------------------

@dataclass
class _CandidateOutcome:
    """Internal record of a candidate's implementer outcome (for fallback)."""

    candidate: CandidateResult | None
    summary: CandidateSummary | None


def _execute_candidate(
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

    return _run_revision_loop(
        cand=cand,
        cfg=cfg,
        run_ctx=run_ctx,
        registry=registry,
        router=router,
        task_text=task_text,
        acceptance=acceptance,
        reviewer_override=reviewer_override,
    )


def _execute_with_fallback(
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

    def runner(pid: str) -> _CandidateOutcome:
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
        return _CandidateOutcome(candidate=cand, summary=None)

    def is_success(outcome: _CandidateOutcome) -> bool:
        return outcome.candidate is not None and outcome.candidate.agent_result.success

    def classify(outcome: _CandidateOutcome) -> tuple[str | None, str | None]:
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
            return _failure_summary(outcome.candidate), history
        return None, history

    # Promote the successful candidate through the revision loop.
    summary = _run_revision_loop(
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

def _run_revision_loop(
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
        summary, decision, review_payload = _evaluate_iteration(
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

def _evaluate_iteration(
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

def _read_task(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _format_acceptance(task: NormalizedTask) -> str:
    if task.acceptance_criteria:
        return "\n".join(f"- {ac}" for ac in task.acceptance_criteria)
    if task.goal:
        return f"- {task.goal}"
    return "See task body above."


def _build_artifact_stages(
    cfg: DevforgeConfig, run_ctx: RunContext, task_text: str
) -> tuple[NormalizedTask, RepoContext, ImplementationPlan]:
    repo_root = Path(cfg.project.root)
    normalized = normalize_task(task_text, repo_root)
    save_normalized_task(normalized, run_ctx.root / "normalized_task.json")

    repo_ctx = collect_repo_context(repo_root, likely_files=normalized.likely_files)
    save_repo_context(
        repo_ctx,
        md_path=run_ctx.root / "repo_context.md",
        json_path=run_ctx.root / "repo_context.json",
    )

    plan = generate_plan(normalized, repo_ctx)
    save_plan(plan, run_ctx.root / "implementation_plan.json")
    return normalized, repo_ctx, plan


def _failure_summary(cand: CandidateResult) -> CandidateSummary:
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


def _finalize(
    run_ctx: RunContext,
    task_text: str,
    summaries: list[CandidateSummary],
    state_store: StateStore,
) -> None:
    chosen: CandidateSummary | None = None
    accepted = [s for s in summaries if s.decision == "accept"]
    if accepted:
        chosen = max(accepted, key=lambda s: s.score)
    elif summaries:
        chosen = max(summaries, key=lambda s: s.score)

    if chosen is not None:
        cand_dir = run_ctx.candidate_dir(chosen.candidate_id)
        decision_path = cand_dir / "decision.json"
        if decision_path.exists():
            try:
                payload = json.loads(decision_path.read_text(encoding="utf-8"))
                save_decision(
                    run_ctx,
                    Decision(
                        verdict=payload.get("verdict", "discard"),
                        reason=payload.get("reason", ""),
                        score=float(payload.get("score", 0.0)),
                        details=payload.get("details", {}),
                    ),
                    chosen.candidate_id if chosen.decision == "accept" else None,
                )
            except json.JSONDecodeError:
                pass

    # Optional comparison report (DEVF-054) — module is imported lazily to keep
    # this driver importable even if the module is being edited.
    state_store.save_step("comparison_report", "running")
    if len(summaries) >= 2:
        try:
            from devforge.stages.candidate_comparison import write_comparison_report

            write_comparison_report(run_ctx, summaries)
            state_store.save_step(
                "comparison_report", "completed", artifact_ref="comparison.md"
            )
        except Exception as exc:
            # Comparison failure must not block final report.
            state_store.save_step("comparison_report", "failed", note=str(exc)[:200])
    else:
        state_store.save_step(
            "comparison_report", "skipped", note="fewer than 2 candidates"
        )

    state_store.save_step("final_report", "running")
    write_final_report(run_ctx, task_text, summaries, chosen)
    state_store.save_step("final_report", "completed", artifact_ref="final_report.md")

    # Record the run-level final decision pointer for `devforge report`.
    final_decision_ref = "decision.json" if (run_ctx.root / "decision.json").exists() else None
    state_store.save_final_decision(
        decision_ref=final_decision_ref,
        chosen_candidate=chosen.candidate_id if chosen is not None else None,
    )


def _write_failure(run_ctx: RunContext, message: str, details: dict) -> None:
    (run_ctx.root / "failure.json").write_text(
        json.dumps({"message": message, "details": details}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_ctx.root / "final_report.md").write_text(
        f"# Final Report — run {run_ctx.run_id}\n\nWorkflow aborted: **{message}**\n\n"
        f"Details:\n```\n{json.dumps(details, indent=2, ensure_ascii=False)}\n```\n",
        encoding="utf-8",
    )
