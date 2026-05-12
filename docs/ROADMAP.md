# Roadmap

Single source of truth for "what's actually shipped" vs "still planned" in this
repo. Other docs cross-link here instead of duplicating status notes.

## Implemented

These DEVF tasks pass tests and are wired into the CLI:

| Area | DEVF | Notes |
|---|---|---|
| Repo / config / CLI | 001, 003, 010, 011, 012, 013 | repo skeleton, Pydantic config, Typer CLI, run context, workflow engine (feature only), JSON state store |
| Providers | 020, 021, 022, 023, 024 | base interface, registry, Codex CLI adapter, Claude CLI adapter, local rule-based provider |
| Git / validation | 030, 031, 032 | worktree manager, diff collector, validation runner |
| Policy / evaluation | 033, 034, 035, 036, 037, 038 | file policy, command policy, secret scanner, test mutation checker, score calculator, judge |
| Feature workflow stages | 040, 041, 042, 043, 044, 045, 046 | task normalizer, repo context collector, plan generator, implementer + reviewer stages, revision loop, final report |
| App-from-PRD foundation | 060, 061, 062, 063, 064, 065, 066, 067 | PRD intake, requirements schema, MVP scope freeze, UX flow / screen inventory, architecture generator, scaffold generator, vertical slice planner, vertical slice implementer. `python-fastapi-only` scaffold ships a runnable FastAPI skeleton under `<run_root>/scaffold/`; other stacks are recorded as `skipped`. The vertical slice implementer reuses the `feature` pipeline (implementer → validation → reviewer → judge → revisions) against an isolated git repo inside `<run_root>/scaffold/` and syncs accepted candidate files back into the scaffold tree (`vertical_slice_result.json`). Validation is intentionally `python -m compileall` only so it works without installing scaffold dependencies — extend `cfg.validation` for stronger gates |
| Routing & coordination | 050, 051, 052, 053, 054 | role router (incl. capability filtering), failure classifier, fallback executor, tournament mode, candidate comparison |
| Report polish | 081 | `devforge report` markdown / json / state output |
| Tests | 090, 091, 092, 093 | unit suite, mock integration suite, opt-in real provider smoke, security regression |
| Packaging | 095 | wheel/sdist build via `pyproject.toml`, console-script entry point, `devforge --version` + `devforge version`, `CHANGELOG.md`, `docs/RELEASE_NOTES.md`. PyPI publish is **not implemented yet** (see below) |

## Partial

| Area | DEVF | Gap |
|---|---|---|
| Directory layout | 002 | `devforge/dashboard/` directory does not exist yet |
| Documentation | 094 | covered by an earlier cycle; future expansion (HOWTOs, tutorials) deferred |

## Not implemented yet

| Area | DEVF | Status |
|---|---|---|
| App-from-PRD downstream stages | 068 – 071 | backlog generator and loop, acceptance coverage, and app release packaging are not built yet |
| SQLite state store | 080 | runs persist as JSON under `.orchestrator/runs/<id>/state/`; SQLite indexing is a future swap |
| Local dashboard backend | 082 | no FastAPI/web layer yet |
| Local dashboard frontend | 083 | no React/TUI yet |
| Generic workflow dispatcher | (architecture §5.2) | `WorkflowEngine` only registers handlers for `feature` and `app_from_prd`. `bugfix`, `refactor`, `code_review_only`, and `research_optimize` workflows are listed in spec but not executable |
| OpenAI API provider | (architecture §5.5) | `providers.openai_api` type is accepted by config but the registry delegates it to the Codex CLI adapter as a placeholder |
| Claude Agent SDK provider | (architecture §5.5) | same — `providers.claude_agent_sdk` falls back to the Claude CLI adapter |
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
