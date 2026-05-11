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
| Routing & coordination | 050, 051, 052, 053, 054 | role router (incl. capability filtering), failure classifier, fallback executor, tournament mode, candidate comparison |
| Report polish | 081 | `devforge report` markdown / json / state output |
| Tests | 090, 091, 092, 093 | unit suite, mock integration suite, opt-in real provider smoke, security regression |

## Partial

| Area | DEVF | Gap |
|---|---|---|
| Directory layout | 002 | `devforge/dashboard/` directory does not exist yet |
| Documentation | 094 | covered by this cycle; future expansion (HOWTOs, tutorials) deferred |
| Packaging | 095 | `pyproject.toml` + console-script entry point work; `CHANGELOG.md` and release notes not written |

## Not implemented yet

| Area | DEVF | Status |
|---|---|---|
| App-from-PRD workflow | 060 – 071 | `devforge create-app` is a stub that exits with a message — no PRD intake, requirements schema, scaffold generator, vertical slice, backlog loop, or release packaging exists |
| SQLite state store | 080 | runs persist as JSON under `.orchestrator/runs/<id>/state/`; SQLite indexing is a future swap |
| Local dashboard backend | 082 | no FastAPI/web layer yet |
| Local dashboard frontend | 083 | no React/TUI yet |
| Generic workflow dispatcher | (architecture §5.2) | `WorkflowEngine` only registers a handler for `feature`. `bugfix`, `refactor`, `code_review_only`, `app_from_prd`, `research_optimize` workflows are listed in spec but not executable |
| OpenAI API provider | (architecture §5.5) | `providers.openai_api` type is accepted by config but the registry delegates it to the Codex CLI adapter as a placeholder |
| Claude Agent SDK provider | (architecture §5.5) | same — `providers.claude_agent_sdk` falls back to the Claude CLI adapter |
| mypy strict pass | — | currently `mypy` runs in non-strict mode; type coverage is partial |
| CI for real provider smoke | — | opt-in suite is local-only; no automated runner for `pytest -m real_provider` |

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
