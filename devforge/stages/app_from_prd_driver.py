"""Driver for the ``app_from_prd`` workflow (DEVF-060..069).

Eight deterministic stages plus two provider-driven implementation stages:

1. ``prd_intake`` → ``product_summary.md`` + ``ambiguity_log.json``
   + ``assumptions.md`` + ``out_of_scope.md``
2. ``requirements_inventory`` → ``requirements.json``
3. ``mvp_scope_freeze`` → ``mvp_scope.md``
4. ``ux_flow_inventory`` → ``screen_inventory.json`` + ``user_flows.md``
   + ``navigation_map.md``
5. ``architecture_design`` → ``architecture.md`` + ``data_model.md``
   + ``api_contract.yaml`` + ``tech_stack.md``
6. ``scaffold_generation`` → ``scaffold/`` + ``scaffold_manifest.json``
7. ``vertical_slice_planner`` → ``vertical_slice_plan.json``
8. ``vertical_slice_implementer`` → ``vertical_slice_result.json`` +
   accepted candidate files synced back into ``scaffold/``
9. ``backlog_generation`` → ``backlog.json`` (one TASK-NNN per FR)
10. ``backlog_implementation`` → ``backlog_progress.json`` (per-task
    status + accepted candidate files committed into ``scaffold/``)

Stages 1–7 and 9 are deterministic. Stages 8 and 10 run the existing
feature-pipeline candidate loop (implementer → validation → reviewer →
judge → revisions) against a scaffold-scoped config; see
:mod:`devforge.stages.vertical_slice_implementer` and
:mod:`devforge.stages.backlog_implementer`.

A short ``final_report.md`` is also written so ``devforge report --latest``
shows a useful summary.
"""
from __future__ import annotations

import json
from typing import Any

from devforge.core.config_loader import DevforgeConfig
from devforge.core.run_context import RunContext
from devforge.core.state_store import StateStore
from devforge.stages.architecture_generator import (
    Architecture,
    build_architecture,
    save_api_contract,
    save_architecture,
    save_data_model,
    save_tech_stack,
)
from devforge.stages.backlog_generator import (
    Backlog,
    BacklogGeneratorError,
    generate_backlog,
    save_backlog,
)
from devforge.stages.backlog_implementer import (
    BacklogProgress,
    run_backlog_implementer,
    save_backlog_progress,
)
from devforge.stages.mvp_scope import MvpScope, freeze_mvp_scope, save_mvp_scope
from devforge.stages.prd_intake import (
    PrdIntake,
    PrdIntakeError,
    intake_prd,
    save_ambiguity_log,
    save_assumptions,
    save_out_of_scope,
    save_product_summary,
)
from devforge.stages.requirements_schema import (
    Requirements,
    RequirementsError,
    build_requirements,
    save_requirements,
)
from devforge.stages.scaffold_generator import (
    ScaffoldError,
    ScaffoldManifest,
    generate_scaffold,
    save_scaffold_manifest,
)
from devforge.stages.ux_flow import (
    UxInventory,
    build_ux_inventory,
    save_navigation_map,
    save_screen_inventory,
    save_user_flows,
)
from devforge.stages.vertical_slice_implementer import (
    VerticalSliceImplementerResult,
    run_vertical_slice_implementer,
    save_vertical_slice_result,
)
from devforge.stages.vertical_slice_implementer import (
    skip_reason as _vsi_skip_reason,
)
from devforge.stages.vertical_slice_planner import (
    VerticalSlicePlan,
    VerticalSlicePlannerError,
    plan_vertical_slice,
    save_vertical_slice_plan,
)

_STAGE_IDS = [
    "prd_intake",
    "requirements_inventory",
    "mvp_scope_freeze",
    "ux_flow_inventory",
    "architecture_design",
    "scaffold_generation",
    "vertical_slice_planner",
    "vertical_slice_implementer",
    "backlog_generation",
    "backlog_implementation",
]


def run_app_from_prd_workflow(
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    *,
    implementer_override: str | None = None,
    reviewer_override: str | None = None,
    state_store: StateStore | None = None,
    definition: Any = None,  # devforge.core.workflow_engine.WorkflowDefinition
) -> None:
    """Run the PRD-foundation workflow. Records state and writes artifacts."""
    if state_store is None:
        state_store = StateStore(run_ctx.root)
        if not state_store.is_initialized():
            state_store.init_run(
                workflow=run_ctx.workflow,
                input_ref=str(run_ctx.input_path) if run_ctx.input_path else None,
                stages=list(_STAGE_IDS),
            )

    prd_path = run_ctx.input_path
    if prd_path is None or not prd_path.exists():
        state_store.save_step("prd_intake", "failed", note="no PRD provided")
        _write_failure(run_ctx, "no PRD provided", {})
        return
    prd_text = prd_path.read_text(encoding="utf-8")

    # Stage 1: prd_intake
    state_store.save_step("prd_intake", "running")
    try:
        intake = intake_prd(prd_text)
    except PrdIntakeError as exc:
        state_store.save_step("prd_intake", "failed", note=str(exc))
        _write_failure(run_ctx, "PRD intake failed", {"reason": str(exc)})
        return
    save_product_summary(intake, run_ctx.root / "product_summary.md")
    save_ambiguity_log(intake, run_ctx.root / "ambiguity_log.json")
    save_assumptions(intake, run_ctx.root / "assumptions.md")
    save_out_of_scope(intake, run_ctx.root / "out_of_scope.md")
    state_store.save_step(
        "prd_intake", "completed", artifact_ref="product_summary.md"
    )

    # Stage 2: requirements_inventory
    state_store.save_step("requirements_inventory", "running")
    try:
        reqs = build_requirements(intake)
    except RequirementsError as exc:
        state_store.save_step("requirements_inventory", "failed", note=str(exc))
        _write_failure(
            run_ctx,
            "no functional requirements",
            {"reason": str(exc), "ambiguities": list(intake.ambiguities)},
        )
        return
    save_requirements(reqs, run_ctx.root / "requirements.json")
    state_store.save_step(
        "requirements_inventory", "completed", artifact_ref="requirements.json"
    )

    # Stage 3: mvp_scope_freeze
    state_store.save_step("mvp_scope_freeze", "running")
    scope = freeze_mvp_scope(reqs, intake)
    save_mvp_scope(scope, run_ctx.root / "mvp_scope.md")
    state_store.save_step(
        "mvp_scope_freeze", "completed", artifact_ref="mvp_scope.md"
    )

    # Stage 4: ux_flow_inventory (DEVF-063)
    state_store.save_step("ux_flow_inventory", "running")
    inventory = build_ux_inventory(reqs, intake, scope)
    save_screen_inventory(inventory, run_ctx.root / "screen_inventory.json")
    save_user_flows(inventory, run_ctx.root / "user_flows.md")
    save_navigation_map(inventory, run_ctx.root / "navigation_map.md")
    state_store.save_step(
        "ux_flow_inventory", "completed", artifact_ref="screen_inventory.json"
    )

    # Stage 5: architecture_design (DEVF-064)
    state_store.save_step("architecture_design", "running")
    stack = str(run_ctx.metadata.get("stack", "python-fastapi-only"))
    project_name = cfg.project.name or "app"
    arch = build_architecture(
        reqs, intake, scope, inventory, stack, project_name=project_name
    )
    save_architecture(arch, run_ctx.root / "architecture.md")
    save_data_model(arch, run_ctx.root / "data_model.md")
    save_api_contract(arch, run_ctx.root / "api_contract.yaml")
    save_tech_stack(arch, run_ctx.root / "tech_stack.md")
    state_store.save_step(
        "architecture_design", "completed", artifact_ref="architecture.md"
    )

    # Stage 6: scaffold_generation (DEVF-065)
    state_store.save_step("scaffold_generation", "running")
    scaffold_root = run_ctx.root / "scaffold"
    try:
        manifest = generate_scaffold(
            arch,
            reqs,
            scope,
            inventory,
            scaffold_root,
            run_root=run_ctx.root,
            project_name=project_name,
        )
    except ScaffoldError as exc:
        state_store.save_step("scaffold_generation", "failed", note=str(exc))
        _write_failure(
            run_ctx, "scaffold generation failed", {"reason": str(exc)}
        )
        return
    save_scaffold_manifest(manifest, run_ctx.root / "scaffold_manifest.json")
    if not manifest.supported:
        state_store.save_step(
            "scaffold_generation",
            "skipped",
            note=f"stack '{arch.stack}' has no scaffold profile",
        )
    elif not manifest.import_smoke_passed:
        state_store.save_step(
            "scaffold_generation",
            "failed",
            note="py_compile smoke failed; see scaffold_manifest.json notes",
        )
    else:
        state_store.save_step(
            "scaffold_generation", "completed", artifact_ref="scaffold/"
        )

    # Stage 7: vertical_slice_planner (DEVF-066)
    state_store.save_step("vertical_slice_planner", "running")
    try:
        slice_plan = plan_vertical_slice(reqs, intake, scope, inventory, arch)
    except VerticalSlicePlannerError as exc:
        state_store.save_step(
            "vertical_slice_planner", "failed", note=str(exc)
        )
        _write_failure(
            run_ctx, "vertical slice planning failed", {"reason": str(exc)}
        )
        return
    save_vertical_slice_plan(
        slice_plan, run_ctx.root / "vertical_slice_plan.json"
    )
    state_store.save_step(
        "vertical_slice_planner",
        "completed",
        artifact_ref="vertical_slice_plan.json",
    )

    # Stage 8: vertical_slice_implementer (DEVF-067)
    state_store.save_step("vertical_slice_implementer", "running")
    vsi_skip = _vsi_skip_reason(manifest, slice_plan)
    if vsi_skip is not None:
        vsi_result = VerticalSliceImplementerResult(
            decision="skipped", reason=vsi_skip
        )
        save_vertical_slice_result(
            vsi_result, run_ctx.root / "vertical_slice_result.json"
        )
        state_store.save_step(
            "vertical_slice_implementer",
            "skipped",
            note=vsi_skip,
        )
    else:
        try:
            vsi_result = run_vertical_slice_implementer(
                cfg,
                run_ctx,
                slice_plan=slice_plan,
                arch=arch,
                scaffold_manifest=manifest,
                implementer_override=implementer_override,
                reviewer_override=reviewer_override,
            )
        except Exception as exc:  # noqa: BLE001 — record + continue, never abort the run
            vsi_result = VerticalSliceImplementerResult(
                decision="failed",
                reason=f"vertical slice implementer raised: {exc}",
            )
        save_vertical_slice_result(
            vsi_result, run_ctx.root / "vertical_slice_result.json"
        )
        if vsi_result.decision == "skipped":
            state_store.save_step(
                "vertical_slice_implementer",
                "skipped",
                note=vsi_result.reason or None,
            )
        elif vsi_result.decision == "failed":
            state_store.save_step(
                "vertical_slice_implementer",
                "failed",
                note=vsi_result.reason or None,
            )
        else:
            state_store.save_step(
                "vertical_slice_implementer",
                "completed",
                artifact_ref="vertical_slice_result.json",
                note=vsi_result.reason or None,
            )

    # Stage 9: backlog_generation (DEVF-068)
    state_store.save_step("backlog_generation", "running")
    try:
        backlog = generate_backlog(reqs, scope, inventory, arch)
    except BacklogGeneratorError as exc:
        state_store.save_step("backlog_generation", "failed", note=str(exc))
        _write_failure(
            run_ctx, "backlog generation failed", {"reason": str(exc)}
        )
        return
    save_backlog(backlog, run_ctx.root / "backlog.json")
    state_store.save_step(
        "backlog_generation", "completed", artifact_ref="backlog.json"
    )

    # Stage 10: backlog_implementation (DEVF-069)
    state_store.save_step("backlog_implementation", "running")
    try:
        backlog_progress = run_backlog_implementer(
            cfg,
            run_ctx,
            backlog=backlog,
            slice_plan=slice_plan,
            slice_result=vsi_result,
            arch=arch,
            scaffold_manifest=manifest,
            implementer_override=implementer_override,
            reviewer_override=reviewer_override,
        )
    except Exception as exc:  # noqa: BLE001 — record + continue, never abort the run
        backlog_progress = BacklogProgress(
            decision="failed",
            reason=f"backlog implementer raised: {exc}",
            total_count=len(backlog.items),
        )
    save_backlog_progress(
        backlog_progress, run_ctx.root / "backlog_progress.json"
    )
    if backlog_progress.decision == "skipped":
        state_store.save_step(
            "backlog_implementation",
            "skipped",
            note=backlog_progress.reason or None,
        )
    elif backlog_progress.decision == "failed":
        state_store.save_step(
            "backlog_implementation",
            "failed",
            note=backlog_progress.reason or None,
        )
    else:
        state_store.save_step(
            "backlog_implementation",
            "completed",
            artifact_ref="backlog_progress.json",
        )

    _write_final_report(
        run_ctx,
        intake,
        reqs,
        scope,
        inventory,
        arch,
        manifest,
        slice_plan,
        vsi_result,
        backlog,
        backlog_progress,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_failure(run_ctx: RunContext, message: str, details: dict) -> None:
    (run_ctx.root / "failure.json").write_text(
        json.dumps(
            {"message": message, "details": details}, indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    (run_ctx.root / "final_report.md").write_text(
        f"# Final Report — run {run_ctx.run_id}\n\n"
        f"Workflow aborted: **{message}**\n\n"
        f"Details:\n```\n{json.dumps(details, indent=2, ensure_ascii=False)}\n```\n",
        encoding="utf-8",
    )


def _write_final_report(
    run_ctx: RunContext,
    intake: PrdIntake,
    reqs: Requirements,
    scope: MvpScope,
    inventory: UxInventory | None = None,
    arch: Architecture | None = None,
    scaffold: ScaffoldManifest | None = None,
    slice_plan: VerticalSlicePlan | None = None,
    slice_result: VerticalSliceImplementerResult | None = None,
    backlog: Backlog | None = None,
    backlog_progress: BacklogProgress | None = None,
) -> None:
    lines: list[str] = []
    lines.append(f"# Final Report — run {run_ctx.run_id}")
    lines.append("")
    lines.append("- Workflow: `app_from_prd`")
    lines.append(f"- PRD: `{run_ctx.input_path.name if run_ctx.input_path else 'n/a'}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    if intake.product_summary:
        lines.append(intake.product_summary)
    else:
        lines.append("_(no product summary found)_")
    lines.append("")

    lines.append("## Requirements")
    lines.append("")
    lines.append(
        f"- Functional: **{len(reqs.functional)}** "
        f"(must={len(scope.must)}, should={len(scope.should)}, could={len(scope.could)})"
    )
    lines.append(f"- Non-functional: **{len(reqs.non_functional)}**")
    lines.append(f"- Unknowns / ambiguities: **{len(reqs.unknowns)}**")
    lines.append("")

    if inventory is not None:
        lines.append("## UX inventory")
        lines.append("")
        lines.append(
            f"- Screens: **{len(inventory.screens)}**, "
            f"flows: **{len(inventory.flows)}**, "
            f"navigation edges: **{len(inventory.navigation)}**"
        )
        kinds = sorted({s.kind for s in inventory.screens})
        if kinds:
            lines.append(f"- Surface kinds: {', '.join(kinds)}")
        lines.append("")

    if arch is not None:
        lines.append("## Architecture")
        lines.append("")
        lines.append(
            f"- Stack: `{arch.stack}` "
            f"({'supported' if arch.supported_stack else 'planned'})"
        )
        lines.append(f"- Runtime: {arch.runtime}")
        lines.append(f"- Framework: {arch.framework}")
        lines.append(f"- Persistence: {arch.persistence}")
        lines.append(
            f"- Entities: **{len(arch.entities)}**, "
            f"API operations: **{len(arch.operations)}**"
        )
        lines.append("")

    if slice_plan is not None:
        lines.append("## Vertical slice")
        lines.append("")
        lines.append(f"- Name: **{slice_plan.vertical_slice_name}**")
        if slice_plan.requirement_ids:
            lines.append(
                f"- Requirements: {', '.join(slice_plan.requirement_ids)}"
            )
        lines.append(
            f"- Screens: **{len(slice_plan.screens)}**, "
            f"endpoints: **{len(slice_plan.api_endpoints)}**, "
            f"entities: **{len(slice_plan.data_entities)}**, "
            f"acceptance criteria: **{len(slice_plan.acceptance_criteria)}**"
        )
        lines.append("")

    if slice_result is not None:
        lines.append("## Vertical slice implementation")
        lines.append("")
        lines.append(f"- Decision: **{slice_result.decision}**")
        if slice_result.candidate_id:
            lines.append(
                f"- Candidate: `{slice_result.candidate_id}` "
                f"(provider `{slice_result.provider_id}`)"
            )
        if slice_result.reviewer_provider_id:
            lines.append(
                f"- Reviewer: `{slice_result.reviewer_provider_id}` "
                f"(verdict: {slice_result.reviewer_verdict or 'n/a'})"
            )
        if slice_result.score is not None:
            lines.append(f"- Score: **{slice_result.score:.1f}**")
        if slice_result.changed_files:
            sample = ", ".join(slice_result.changed_files[:5])
            extra = (
                f", ... ({len(slice_result.changed_files) - 5} more)"
                if len(slice_result.changed_files) > 5
                else ""
            )
            lines.append(f"- Changed files: {sample}{extra}")
        lines.append(
            f"- Synced into `scaffold/`: "
            f"**{'yes' if slice_result.synced_to_scaffold else 'no'}**"
        )
        if slice_result.candidate_artifacts:
            lines.append(f"- Artifacts: `{slice_result.candidate_artifacts}`")
        if slice_result.reason:
            lines.append(f"- Reason: {slice_result.reason}")
        if slice_result.notes:
            lines.append("- Notes:")
            for n in slice_result.notes:
                lines.append(f"  - {n}")
        lines.append("")

    if scaffold is not None:
        lines.append("## Scaffold")
        lines.append("")
        if scaffold.supported and scaffold.import_smoke_passed:
            lines.append(
                f"- Stack: `{scaffold.stack}` — generated **{len(scaffold.files)}** "
                f"file(s) under `{scaffold.scaffold_root}/`"
            )
            lines.append(f"- Test command: `{scaffold.test_command}`")
            lines.append("- Import smoke (py_compile): **passed**")
        elif scaffold.supported and not scaffold.import_smoke_passed:
            lines.append(
                f"- Stack: `{scaffold.stack}` — generated files but py_compile "
                f"smoke **failed**; see `scaffold_manifest.json` notes"
            )
        else:
            lines.append(
                f"- Stack: `{scaffold.stack}` — **skipped** (no scaffold "
                f"profile yet)"
            )
        lines.append("")

    if backlog_progress is not None:
        lines.append("## Backlog implementation")
        lines.append("")
        lines.append(f"- Decision: **{backlog_progress.decision}**")
        lines.append(
            f"- Accepted: **{backlog_progress.accepted_count}** of "
            f"**{backlog_progress.total_count}**"
        )
        lines.append(
            f"- Acceptance coverage: **{backlog_progress.acceptance_coverage:.1%}**"
        )
        by_status: dict[str, int] = {}
        for item in backlog_progress.items:
            by_status[item.status] = by_status.get(item.status, 0) + 1
        if by_status:
            breakdown = ", ".join(
                f"{status}={count}" for status, count in sorted(by_status.items())
            )
            lines.append(f"- Per-task status: {breakdown}")
        if backlog_progress.reason:
            lines.append(f"- Reason: {backlog_progress.reason}")
        lines.append("")

    if backlog is not None:
        lines.append("## Backlog")
        lines.append("")
        priority_counts = {"P0": 0, "P1": 0, "P2": 0}
        complexity_counts = {"S": 0, "M": 0, "L": 0}
        for item in backlog.items:
            priority_counts[item.priority] = priority_counts.get(item.priority, 0) + 1
            complexity_counts[item.estimated_complexity] = (
                complexity_counts.get(item.estimated_complexity, 0) + 1
            )
        lines.append(f"- Items: **{len(backlog.items)}**")
        lines.append(
            f"- Priority: P0={priority_counts['P0']}, "
            f"P1={priority_counts['P1']}, P2={priority_counts['P2']}"
        )
        lines.append(
            f"- Complexity: S={complexity_counts['S']}, "
            f"M={complexity_counts['M']}, L={complexity_counts['L']}"
        )
        dep_items = sum(1 for it in backlog.items if it.dependencies)
        lines.append(f"- Items with dependencies: **{dep_items}**")
        lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    for name in (
        "product_summary.md",
        "ambiguity_log.json",
        "assumptions.md",
        "out_of_scope.md",
        "requirements.json",
        "mvp_scope.md",
        "screen_inventory.json",
        "user_flows.md",
        "navigation_map.md",
        "architecture.md",
        "data_model.md",
        "api_contract.yaml",
        "tech_stack.md",
        "scaffold_manifest.json",
        "vertical_slice_plan.json",
        "vertical_slice_result.json",
        "backlog.json",
        "backlog_progress.json",
    ):
        if (run_ctx.root / name).exists():
            lines.append(f"- `{name}`")
    if scaffold is not None and scaffold.supported:
        lines.append(f"- `{scaffold.scaffold_root}/` (scaffold directory)")
    lines.append("")

    lines.append("## Next cycle")
    lines.append("")
    for item in scope.next_cycle:
        lines.append(f"- {item}")
    lines.append("")

    (run_ctx.root / "final_report.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )
