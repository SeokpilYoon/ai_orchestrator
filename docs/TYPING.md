# Typing roadmap

devforge ships with a **two-tier mypy configuration**:

1. **Default profile** (whole repo) — `strict = false`,
   `ignore_missing_imports = true`, `warn_unused_ignores = true`.
   Catches obvious bugs (`bytes + str`, `None` deref, wrong attr access)
   without forcing every function to be fully annotated.
2. **Strict overlay** (curated module list in
   `pyproject.toml [[tool.mypy.overrides]]`) — flagged modules get
   `disallow_untyped_defs`, `disallow_incomplete_defs`,
   `no_implicit_optional`, and `warn_return_any`.

The overlay strategy lets us tighten typing one module at a time
without forcing a single risky big-bang rewrite. As each module in the
"not yet" list below earns enough type coverage to pass strict, move
its dotted path into the overrides block in `pyproject.toml`.

## In the strict tier today

| Module | Why it qualified |
|---|---|
| `devforge.core.config_loader` | Pydantic models; no untyped surface. |
| `devforge.core.run_context` | Pure dataclass + utilities. |
| `devforge.core.sqlite_index` | DEVF-080 — clean DB-access shape. |
| `devforge.core.state_store` | DEVF-013/080 — annotated callables. |
| `devforge.evaluators.*` | Pure-function evaluators. |
| `devforge.providers.base` | Protocol + dataclasses only. |
| `devforge.providers.local_rule_based` | Reference adapter. |
| `devforge.providers.mock` | Test-friendly stand-in. |
| `devforge.providers.openai_api` | DEVF-Wave A — text-only adapter. |
| `devforge.providers.claude_agent_sdk` | DEVF-Wave B — text-only adapter. |
| `devforge.stages.backlog_generator` | Deterministic data projection. |
| `devforge.stages.acceptance_coverage` | DEVF-070 — pure aggregation. |
| `devforge.stages.release_packaging` | DEVF-071 — pure rendering. |
| `devforge.stages._workflow_variants` | Single-purpose helper. |

## Not yet in the strict tier

The remaining modules pass the default profile but would fail strict
checks today. Each has a specific reason; address them in this order
when extending strict coverage:

| Module | Blocker |
|---|---|
| `devforge.stages.feature_driver` | Heavy interaction with `Any`-typed `definition` and dynamic provider routing. |
| `devforge.stages.app_from_prd_driver` | Same — orchestrates 12 stages with optional payloads. |
| `devforge.stages.candidate_loop` | TypeVar inference around `run_with_fallback`; the two `type: ignore[arg-type]` comments document the source. |
| `devforge.stages.architecture_generator` | YAML-shaped config dict (`dict[str, object]`). |
| `devforge.stages.vertical_slice_planner` | Several heuristics over loosely-typed `Iterable[object]`. |
| `devforge.stages.vertical_slice_implementer` | Calls into the feature driver with deep optional chains. |
| `devforge.stages.backlog_implementer` | Synthetic stub (`_BACKLOG_GATE_STUB`) requires a documented `type: ignore`. |
| `devforge.stages.code_review_only_driver` | Builds an `EvaluationBundle` from a synthetic empty validation report. |
| `devforge.stages.research_optimize_driver` | Dynamic metric command via shell. |
| `devforge.providers.codex_cli` / `claude_cli` | Shell subprocess wrapping with regex-driven error classification. |
| `devforge.providers._subprocess` | Same — subprocess helpers. |
| `devforge.dashboard.backend` | FastAPI app factory — return type depends on optional extras. |
| `devforge.cli` | Typer's runtime introspection makes strict mode noisy. |
| `devforge.stages.implementer_stage` / `reviewer_stage` | Provider invocation glue. |
| `devforge.stages.fallback` | Generic over T; would need a `Protocol[T]` shape. |
| `devforge.core.workflow_engine` | Dynamic dispatch + YAML loading. |
| `devforge.core.role_router` | Capability-set membership checks. |
| `devforge.git.*` | Subprocess-heavy. |
| `devforge.stages.candidate_comparison` / `final_report` / `revision_loop` | Report writers / formatting glue. |

Anything not listed above is either already strict-clean or excluded by
`__init__.py` / package data conventions.

## Bounded `type: ignore` log

These are the only `type: ignore` comments currently in the codebase
(grep `type: ignore` in `devforge/`). Each is documented inline; if a
future SDK release fixes the underlying type, drop the comment and
re-run mypy.

- `devforge/stages/candidate_loop.py` — `run_with_fallback` TypeVar
  widens because the helper caches the last result; the local
  callables never see `None` so the casts are sound.
- `devforge/stages/backlog_implementer.py` — `_BACKLOG_GATE_STUB`
  exposes only the `acceptance_criteria` field
  `skip_reason` reads.
- `devforge/stages/architecture_generator.py` — historical comments
  around the old YAML profile dict (kept to acknowledge the typing
  loosening at that boundary).

## Local commands

```bash
# Run mypy across the whole package (default profile + strict overlay).
.venv/bin/mypy devforge

# Only check the strict-tier modules.
.venv/bin/mypy --strict devforge/core/config_loader.py
```

CI does not yet run mypy automatically — that's deferred until the
strict overlay covers the full driver tree.
