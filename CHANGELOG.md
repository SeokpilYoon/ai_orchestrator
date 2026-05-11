# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/) format.
Version numbers follow [Semantic Versioning](https://semver.org/) `0.x.y`
rules (breaking changes allowed in any release while pre-1.0).

## [0.0.1] - 2026-05-11

First alpha. The `feature` workflow runs end-to-end and is covered by
168 mock tests plus 8 opt-in real-provider smoke tests.

### Added

- Typer CLI: `version` / `init` / `providers status` / `run` / `report` /
  `apply` / `cleanup` / `create-app` (stub), plus root `--version` option
- Pydantic v2 config loader for `devforge.yaml` with upward `find_config`
  search
- Run context + JSON file-based state store under
  `.orchestrator/runs/<id>/state/` (run / steps / candidates)
- `WorkflowEngine` that loads and validates `devforge/workflows/<id>.yaml`;
  only the `feature` workflow has an executable handler in this release
- Provider registry with Codex CLI adapter, Claude CLI adapter,
  local rule-based provider, and a mock provider for tests
- Git worktree manager + diff collector — every candidate runs in its
  own isolated worktree
- Evaluators: validation runner, file-policy checker, command-policy
  checker, secret scanner, test-mutation checker, score calculator,
  deterministic judge
- Feature stages: task normalizer, repo-context collector,
  implementation-plan generator, implementer, reviewer, revision loop,
  fallback executor, tournament mode, candidate comparison report,
  final report writer
- Role router with capability gating and per-role defaults
- Opt-in real-provider smoke tests behind `pytest -m real_provider`,
  with a second `DEVFORGE_REAL_PROVIDER_RUN=1` env gate for token-spending
  Tier B cases
- Security regression suite covering blocked file modification, secret
  injection, forbidden commands, test deletion, and lockfile changes
- User documentation: `docs/INSTALL.md`, `docs/CONFIG.md`,
  `docs/PROVIDERS.md`, `docs/WORKFLOWS.md`, `docs/REPORTS.md`,
  `docs/SECURITY.md`, `docs/REAL_PROVIDER_SMOKE.md`, `docs/ROADMAP.md`,
  `docs/RELEASE_NOTES.md`

### Build

- `pyproject.toml` declares the package, console script
  (`devforge = "devforge.cli:app"`), and the YAML / Markdown / JSON-schema
  data files shipped inside the wheel
- `python -m pip wheel . --no-deps -w <dir>` produces a working wheel
  with no runtime dependencies beyond `typer`, `pydantic`, and `pyyaml`

### Known limitations

See `docs/ROADMAP.md` for the full matrix. Not implemented yet in this
alpha:

- `devforge create-app` (the entire DEVF-060 – 071 app-from-PRD track)
- Real `openai_api` and `claude_agent_sdk` providers — currently
  placeholders that delegate to the CLI adapters
- SQLite state store (DEVF-080) and the local dashboard
  (DEVF-082 / DEVF-083)
- Generic DAG dispatcher in `WorkflowEngine`; only `feature` is wired
- PyPI publish pipeline — the supported install is editable install
  against a local checkout
