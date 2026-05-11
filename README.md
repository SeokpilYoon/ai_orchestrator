# AI Dev Orchestrator (`devforge`)

Local-first CLI that orchestrates Claude Code and Codex CLI as **bounded workers**, with deterministic policy + scoring + judge deciding whether to accept, revise, or discard each patch.

## Status

Alpha — MVP-1 (single feature workflow) under construction. See `docs/plan/03_Task_Plan_AI_Dev_Orchestrator.md` for the DEVF-xxx task breakdown.

## Install (development)

```bash
python -m venv .venv
source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## Quick check

```bash
devforge --help
devforge providers status
```

## Authoritative docs

The 3 planning docs are the frozen source of truth — implementation should match them:

- `docs/plan/01_개발_계획서_AI_Dev_Orchestrator.md` — product scope
- `docs/plan/02_아키텍처_설계서_AI_Dev_Orchestrator.md` — module/interface design
- `docs/plan/03_Task_Plan_AI_Dev_Orchestrator.md` — DEVF-xxx work breakdown

## Layout

```
devforge/
  cli.py            # Typer entrypoint
  core/             # config, run context, role router, prompt renderer
  providers/        # codex/claude/local provider adapters
  git/              # worktree, diff, patch
  evaluators/       # validation, policy, secret scan, score, judge
  workflows/        # *.yaml DAG definitions
  stages/           # implementer / reviewer / final_report stage orchestration
  prompts/          # role prompts + JSON schemas (sent to LLM providers at runtime)
  project_profiles/ # per-stack validation commands
tests/
docs/
examples/
.claude/            # Claude Code harness (agents, skills)
_workspace/         # harness scratch (gitignored)
.orchestrator/      # devforge runtime artifacts (gitignored)
```

## Safety

- All agent work runs inside isolated `git worktree` directories
- Hard policy gates: `.env`, `secrets/**`, `.git/**` are blocked
- Forbidden commands (`rm -rf`, `git push`, `curl ... | sh`, …) detected and rejected
- Secret scanner runs on every candidate diff
- Tests cannot be deleted or weakened without `human_review`

## License

MIT.
