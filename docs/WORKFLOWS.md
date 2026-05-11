# Workflows

Only the **`feature`** workflow has an executable handler today. Spec lists
`bugfix`, `refactor`, `code_review_only`, `app_from_prd`, and
`research_optimize` — those are stage definitions only and not dispatched
(see [`ROADMAP.md`](ROADMAP.md)).

## The feature workflow

Definition: [`devforge/workflows/feature.yaml`](../devforge/workflows/feature.yaml).
The `WorkflowEngine` loads, validates it, then drives the stages:

| Stage | Kind | Output |
|---|---|---|
| `normalize_task` | `technical_planner` (read-only) | `normalized_task.json` |
| `inspect_repo` | `technical_planner` (read-only) | `repo_context.md` |
| `plan` | `system_architect` (read-only) | `implementation_plan.json` |
| `implement_candidates` | `implementer` (composite — see below) | per-candidate dir |
| `comparison_report` | local writer | `comparison.md` (≥2 candidates) |
| `final_report` | local writer | `final_report.md` |

The composite `implement_candidates` stage internally runs implementer →
validation → reviewer → judge for every candidate, with the revision loop
nested per candidate (`docs/plan/03 DEVF-045`).

## Running a feature run

```bash
devforge run --workflow feature \
  --task examples/tasks/add_health_endpoint.md \
  -c devforge.yaml
```

Optional overrides:

```bash
--implementer codex_sub_cli       # force a specific implementer provider
--reviewer claude_sub_cli         # force a specific reviewer provider
--tournament codex_sub_cli,claude_sub_cli   # comma-separated; tournament mode
```

The CLI prints the new `run_id` and the run directory, then hands off to the
engine. State is recorded as the run progresses; see
[`REPORTS.md`](REPORTS.md) for inspection.

## Task file conventions

The task normalizer (`devforge/stages/task_normalizer.py`) reads any markdown.
It looks for these section headings (case-insensitive, English or Korean):

- `# Goal` / `## Goal` / `## 목표` — first paragraph becomes the goal
- `## Constraints` / `## 제약` — bullets become a list
- `## Acceptance Criteria` / `## Acceptance` / `## 수락 기준` — bullets become
  the acceptance criteria list

Files referenced in backticks (`` `src/foo.py` ``) or bare paths with known
extensions are extracted as `likely_files`.

If the resulting plan has **no steps**, the workflow aborts and writes
`failure.json` instead of running the implementer. This is the DEVF-042 gate.

A small sample task lives at
[`examples/tasks/add_health_endpoint.md`](../examples/tasks/add_health_endpoint.md).

## Run directory layout

Every run produces a directory under `<repo>/.orchestrator/runs/<run_id>/`:

```
.orchestrator/runs/<run_id>/
  run.json                           # run context metadata
  input.md                           # copy of the task file
  normalized_task.json               # DEVF-040
  repo_context.md  repo_context.json # DEVF-041
  implementation_plan.json           # DEVF-042
  state/
    run.json                         # state-store run record (DEVF-013)
    steps.json                       # per-stage status trail
    candidates.json                  # candidate index w/ score + decision_ref
  candidates/<candidate_id>/
    prompt.md
    stdout.log  stderr.log
    agent_result.json
    diff.patch  changed_files.txt  diff_stat.txt
    validation.json
    review_prompt.md  review_stdout.log  review_stderr.log  review.json
    policy.json                      # file/command/secret/test-mutation results
    score.json
    decision.json
    revision_NN/                     # snapshot of each revision iteration
  decision.json                      # run-level final decision (if any)
  final_report.md                    # human-readable summary
  fallback_history.json              # only when fallback was used
  comparison.md                      # only when there are ≥2 candidates
  failure.json                       # only when the workflow aborted
```

`run.json` at the top level is the original run-context metadata; `state/run.json`
is the live state-store record. They intentionally live at different paths so
neither overwrites the other.

## Revision loop

`devforge/stages/revision_loop.py` + `feature_driver.py`:

- Iterates **only when the judge returns `revise`**. `accept`, `discard`,
  `human_review`, and `keep_candidate_but_continue` are all terminal.
- Re-runs the implementer in the **same** candidate worktree with a revision
  prompt built from the reviewer's critical / major issues.
- Captures every iteration's artifacts under `revision_NN/`. The top of
  `candidates/<id>/` always reflects the latest iteration.
- Stops at `mode.max_iterations_per_task` (default 4) or when score stops
  improving (`new_score <= prev_score`).

## Fallback and tournament

Single mode:

- `provider_order` is treated as a fallback chain
- First provider that succeeds becomes the candidate
- Recoverable failures (`auth_expired`, `usage_limit_hit`, `rate_limit`,
  `timeout`, `command_missing`, `malformed_output`) move on to the next
- Non-recoverable failures (`policy_violation`, `unknown`) short-circuit
- The full history is saved to `fallback_history.json`

Tournament mode (`tournament: true` + ≥2 healthy providers):

- Each provider gets its own candidate; no inter-provider fallback
- `comparison.md` is written automatically when there are ≥2 candidates

## The app_from_prd workflow (foundation)

`devforge create-app --from <prd>.md --stack <name>` runs five deterministic
planning stages and writes planning artifacts to a new run directory:

- `prd_intake` → `product_summary.md`, `ambiguity_log.json`,
  `assumptions.md`, `out_of_scope.md`
- `requirements_inventory` → `requirements.json` (matches the
  `docs/plan/03 DEVF-061` schema; every FR has an id and acceptance criteria)
- `mvp_scope_freeze` → `mvp_scope.md` (must / should / could classification
  plus standing out-of-scope entries pointing at the deferred stages)
- `ux_flow_inventory` → `screen_inventory.json`, `user_flows.md`,
  `navigation_map.md` (one surface + flow per FR; surfaces are classified as
  `ui` / `api` / `cli` / `logical` from description keywords — backend-only
  PRDs fall back to `logical`)
- `architecture_design` → `architecture.md`, `data_model.md`,
  `api_contract.yaml` (OpenAPI 3.0), `tech_stack.md`. Only the
  `python-fastapi-only` stack has a concrete profile in this release; other
  stack values are recorded but marked **planned** in the artifacts.

The PRD is a markdown file with `## Functional requirements`,
`## Non-functional requirements`, and (optionally) `## Out of scope`
sections. See [`../examples/prds/sample_todo_app.md`](../examples/prds/sample_todo_app.md).

Subsequent stages (scaffold, vertical slice, backlog loop, release
packaging — DEVF-065 to DEVF-071) are **not yet implemented**. The
`--stack` argument is recorded in run metadata but does not drive any
generator in this release.

Empty PRDs and PRDs with zero functional requirements abort the workflow:
a `failure.json` is written and the corresponding step in `state/steps.json`
is marked `failed`.

## Other workflows

`bugfix`, `refactor`, `code_review_only`, `research_optimize` are listed in
`docs/plan/02 §6` but **not yet implemented**. `WorkflowEngine` raises
`WorkflowLoadError("workflow … has no engine handler")` for those. See
[`ROADMAP.md`](ROADMAP.md).
