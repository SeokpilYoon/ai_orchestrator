"""Driver for the ``app_from_prd`` workflow (DEVF-060..065).

Six deterministic stages — no provider / no worktree / no judge:

1. ``prd_intake`` → ``product_summary.md`` + ``ambiguity_log.json``
   + ``assumptions.md`` + ``out_of_scope.md``
2. ``requirements_inventory`` → ``requirements.json``
3. ``mvp_scope_freeze`` → ``mvp_scope.md``
4. ``ux_flow_inventory`` → ``screen_inventory.json`` + ``user_flows.md``
   + ``navigation_map.md``
5. ``architecture_design`` → ``architecture.md`` + ``data_model.md``
   + ``api_contract.yaml`` + ``tech_stack.md``
6. ``scaffold_generation`` → ``scaffold/`` + ``scaffold_manifest.json``

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

_STAGE_IDS = [
    "prd_intake",
    "requirements_inventory",
    "mvp_scope_freeze",
    "ux_flow_inventory",
    "architecture_design",
    "scaffold_generation",
]


def run_app_from_prd_workflow(
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    *,
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

    _write_final_report(run_ctx, intake, reqs, scope, inventory, arch, manifest)


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
