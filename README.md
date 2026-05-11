# AI Dev Orchestrator (`devforge`)

Local-first CLI that orchestrates Claude Code and Codex CLI as **bounded
workers**. Deterministic policy + scoring + judge decide whether to accept,
revise, or discard each patch.

## Status

Alpha. The `feature` workflow runs end-to-end with mock providers and is
covered by 168 tests. An additional 8 opt-in tests exercise the real `codex` /
`claude` CLIs when they are installed.

App-from-PRD, dashboard, SQLite state store, and full OpenAI/Anthropic SDK
adapters are still **planned** — see [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate                # macOS / Linux
pip install -e ".[dev]"
devforge --help
devforge init                             # creates devforge.yaml
devforge providers status                 # shows which CLIs are available
```

A full walkthrough lives in [`docs/INSTALL.md`](docs/INSTALL.md).

## Tests

```bash
pytest -q                                 # 168 mock-only, no external CLI needed
pytest -m real_provider                   # opt-in; requires codex/claude on PATH
```

The default `pytest -q` run never touches real provider CLIs. See
[`docs/REAL_PROVIDER_SMOKE.md`](docs/REAL_PROVIDER_SMOKE.md) for the opt-in
suite's gating and Tier A / Tier B split.

## Documentation

| File | Topic |
|---|---|
| [`docs/INSTALL.md`](docs/INSTALL.md) | Local setup, venv, first commands |
| [`docs/CONFIG.md`](docs/CONFIG.md) | `devforge.yaml` schema |
| [`docs/PROVIDERS.md`](docs/PROVIDERS.md) | Provider types, capability routing, tournament/fallback |
| [`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) | `feature` workflow, run directory, revision loop |
| [`docs/REPORTS.md`](docs/REPORTS.md) | `report` / `apply` / `cleanup` commands |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Policy gates, secret scanner, judge decision matrix |
| [`docs/REAL_PROVIDER_SMOKE.md`](docs/REAL_PROVIDER_SMOKE.md) | Opt-in real-CLI smoke tests |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Implemented vs planned vs out-of-scope |

## Frozen specs

The product, architecture, and task specs under [`docs/plan/`](docs/plan) are
the authoritative source. They are not modified by Claude/Codex sessions:

- `docs/plan/01_개발_계획서_AI_Dev_Orchestrator.md` — product scope
- `docs/plan/02_아키텍처_설계서_AI_Dev_Orchestrator.md` — module / interface design
- `docs/plan/03_Task_Plan_AI_Dev_Orchestrator.md` — DEVF-xxx work breakdown

## Layout

```
devforge/
  cli.py             # Typer entrypoint
  core/              # config, run context, role router, state store, workflow engine
  providers/         # codex/claude/local/mock provider adapters
  git/               # worktree, diff, patch
  evaluators/        # validation, policy, secret scan, score, judge
  workflows/         # *.yaml workflow definitions
  stages/            # stage implementations + feature driver
  prompts/           # role prompts (rendered into provider calls at runtime)
  project_profiles/  # per-stack validation commands
tests/               # mock-only + opt-in real-provider suites
docs/                # user documentation
examples/            # sample devforge.yaml and sample task
.claude/             # Claude Code harness (agents, skills)
_workspace/          # harness scratch (gitignored)
.orchestrator/       # devforge runtime artifacts (gitignored)
```

## Safety summary

- Every candidate runs inside its own `git worktree`. The main working tree is
  never mutated by an agent.
- Hard policy gates: `.env`, `.env.*`, `secrets/**`, `.git/**` cannot be
  modified.
- Forbidden command patterns (`rm -rf`, `git push`, `git reset --hard`,
  `curl ... | sh`, `sudo`, …) cause a candidate to be discarded.
- Secret scanner runs on every diff and on agent stdout/stderr.
- Tests cannot be deleted or weakened without flipping to `human_review`.

Full details in [`docs/SECURITY.md`](docs/SECURITY.md).

## License

MIT.
