# Roadmap

Single source of truth for "what's actually shipped" vs "still planned" in this
repo. Other docs cross-link here instead of duplicating status notes.

## Implemented

These DEVF tasks pass tests and are wired into the CLI:

| Area | DEVF | Notes |
|---|---|---|
| Repo / config / CLI | 001, 003, 010, 011, 012, 013 | repo skeleton, Pydantic config, Typer CLI, run context, workflow engine (feature only), JSON state store |
| Providers | 020, 021, 022, 023, 024 | base interface, registry, Codex CLI adapter, Claude CLI adapter, local rule-based provider. The `openai_api` provider type ships a real Chat Completions adapter and `claude_agent_sdk` ships a real Anthropic Messages adapter (both text-only — reviewer/judge roles). Install with `pip install '.[providers-openai]'` / `'.[providers-anthropic]'` |
| Git / validation | 030, 031, 032 | worktree manager, diff collector, validation runner |
| Policy / evaluation | 033, 034, 035, 036, 037, 038 | file policy, command policy, secret scanner, test mutation checker, score calculator, judge |
| Feature workflow stages | 040, 041, 042, 043, 044, 045, 046 | task normalizer, repo context collector, plan generator, implementer + reviewer stages, revision loop, final report |
| App-from-PRD pipeline | 060 – 071 | Full sequence: PRD intake, requirements schema, MVP scope freeze, UX flow / screen inventory, architecture generator, scaffold generator, vertical slice planner, vertical slice implementer, backlog generator, backlog implementation loop, acceptance coverage calculator, release packaging. `python-fastapi-only` scaffold ships a runnable FastAPI skeleton under `<run_root>/scaffold/`; other stacks are recorded as `skipped`. The vertical slice and backlog implementers reuse the `feature` pipeline (implementer → validation → reviewer → judge → revisions) against an isolated git repo inside `<run_root>/scaffold/` and sync accepted candidate files back into the scaffold tree. Validation is intentionally `python -m compileall` only so it works without installing scaffold dependencies — extend `cfg.validation` for stronger gates. The acceptance coverage calculator emits per-FR coverage with `slice`/`backlog`/`none` attribution. The release packaging stage emits `<run_root>/release/{README, deployment, release_notes, qa_report, final_report}.md` — a handoff bundle a new user can read to install, run, and assess the generated app |
| Routing & coordination | 050, 051, 052, 053, 054 | role router (incl. capability filtering), failure classifier, fallback executor, tournament mode, candidate comparison |
| State store | 013, 080 | per-run JSON state under `.orchestrator/runs/<id>/state/` (DEVF-013) plus a project-level SQLite index at `.orchestrator/state.db` (DEVF-080). The SQLite index mirrors every JSON write — runs, steps, candidates, evaluations, provider_status — so cross-run queries answer without walking the filesystem. JSON remains authoritative per run; SQLite is best-effort and silently degrades when unavailable |
| Report polish | 081 | `devforge report` markdown / json / state output; `devforge report --list` enumerates runs via the SQLite index with `--workflow`/`--limit`/`--format` filters |
| Local dashboard | 082, 083 | Read-only FastAPI app over the SQLite index (DEVF-080) + per-run JSON artifacts. Routes under `/api/*` (runs, candidates, diffs, providers, healthz). DEVF-083 ships a vanilla HTML/JS frontend (no build step) at `/` with three views: runs list, run detail (steps + candidates + evaluations + provider status), and candidate detail (decision, score, review, validation, diff). Serve via `devforge dashboard --host --port`. FastAPI/uvicorn are optional extras (`pip install '.[dashboard]'`) so the core CLI stays slim |
| Tests | 090, 091, 092, 093 | unit suite, mock integration suite, opt-in real provider smoke, security regression |
| Packaging | 095 | wheel/sdist build via `pyproject.toml`, console-script entry point, `devforge --version` + `devforge version`, `CHANGELOG.md`, `docs/RELEASE_NOTES.md`. PyPI publish is **not implemented yet** (see below) |

## Partial

| Area | DEVF | Gap |
|---|---|---|
| Documentation | 094 | covered by an earlier cycle; future expansion (HOWTOs, tutorials) deferred |

## Not implemented yet

| Area | DEVF | Status |
|---|---|---|
| Generic workflow dispatcher | (architecture §5.2) | `WorkflowEngine` registers handlers for all six spec workflows: `feature`, `bugfix`, `refactor`, `code_review_only`, `research_optimize`, and `app_from_prd`. `research_optimize` runs a bounded inspect → hypothesise → (optionally implement) → verify cycle around a user-supplied metric command |
| mypy strict pass | — | currently `mypy` runs in non-strict mode; type coverage is partial |
| CI for real provider smoke | — | opt-in suite is local-only; no automated runner for `pytest -m real_provider` |
| PyPI publish | post-095 | the release process is editable install + local wheel build; no automated upload pipeline yet |

## Out of scope

Per the original product spec (`docs/plan/01 §5`):

- Fully unattended production deployment
- Reselling third-party Claude / ChatGPT subscriptions as a SaaS
- Auto-supporting every project stack — only Python/Node/Unity/FastAPI profiles
- Allowing the agent to modify test thresholds or CI gates
- Auto-approving large dependency / Docker / infra changes

## How this list is maintained

When a DEVF moves to "Implemented", the changelog entry should also update the
relevant row here. When something gets added to "Not implemented yet" (e.g. a
new workflow type), keep the row pointing at the spec section so readers can
see *why* it's planned, not just *that* it's planned.
