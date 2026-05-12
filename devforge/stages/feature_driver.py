"""Feature-workflow driver — glues all stages together.

This is the end-to-end orchestration for MVP-1 / DEVF-040..046, DEVF-050/052,
DEVF-045 (revision loop). Full WorkflowEngine + DAG (docs/plan/02 §5.2) is a
follow-up for M5+.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devforge.core.config_loader import DevforgeConfig
from devforge.core.role_router import RoleRouter
from devforge.core.run_context import RunContext
from devforge.core.state_store import StateStore
from devforge.evaluators.judge import Decision
from devforge.git.worktree_manager import WorktreeManager
from devforge.providers.registry import ProviderRegistry
from devforge.stages import candidate_loop as _candidate_loop
from devforge.stages.candidate_loop import TERMINAL_VERDICTS
from devforge.stages.final_report import CandidateSummary, save_decision, write_final_report
from devforge.stages.implementation_plan_generator import (
    ImplementationPlan,
    generate_plan,
    save_plan,
)
from devforge.stages.repo_context_collector import (
    RepoContext,
    collect_repo_context,
    render_repo_context_md,
    save_repo_context,
)
from devforge.stages.task_normalizer import (
    NormalizedTask,
    normalize_task,
    save_normalized_task,
)

__all__ = [
    "TERMINAL_VERDICTS",
    "run_feature_workflow",
]


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
    state_store.snapshot_provider_registry(registry)
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
            summary = _candidate_loop.execute_candidate(
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
        summary, attempts = _candidate_loop.execute_with_fallback(
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
# Internal: removed candidate-loop helpers (moved to devforge.stages.candidate_loop)
# ---------------------------------------------------------------------------

# The per-candidate execution + revision loop + iteration evaluator used to live
# here. They were extracted to `devforge.stages.candidate_loop` so DEVF-067
# (vertical slice implementer) can reuse the exact same evaluation pipeline.
# Behavior is unchanged; see the candidate_loop module for the actual code.


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
