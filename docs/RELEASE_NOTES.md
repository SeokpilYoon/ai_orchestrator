# Release notes — 0.0.1 (alpha)

Date: 2026-05-11.

## What this release contains

The `feature` workflow runs end-to-end. With mock providers it powers the
default test suite; with a real `codex` and/or `claude` CLI on PATH it
drives those CLIs as bounded workers and selects the better candidate
per the rules in `docs/plan/03_Task_Plan_AI_Dev_Orchestrator.md`.

Full changelog: [`../CHANGELOG.md`](../CHANGELOG.md).

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
devforge --version          # → 0.0.1
pytest -q                   # → 168 passed, 8 skipped
```

For the full walkthrough see [`INSTALL.md`](INSTALL.md).

## Build a wheel

```bash
python -m pip wheel . --no-deps -w /tmp/devforge-wheel-test
ls /tmp/devforge-wheel-test/devforge-0.0.1-*.whl
```

The wheel ships the `devforge` package plus the YAML / Markdown /
JSON-schema data files declared in `pyproject.toml`. `tests/`,
`_workspace/`, `.orchestrator/`, and `docs/plan/` are excluded.
`dist/`, `build/`, and `*.egg-info/` are gitignored.

## Verification

| Command | Expected |
|---|---|
| `devforge --version` | `0.0.1` |
| `devforge version` | `0.0.1` |
| `devforge --help` | lists `version`, `init`, `run`, `providers`, `report`, `apply`, `cleanup`, `create-app` |
| `pytest -q` | 168 passed, 8 skipped |
| `pytest -m real_provider` | 6 Tier A passed (or skipped if no CLI), 2 Tier B skipped without `DEVFORGE_REAL_PROVIDER_RUN=1` |
| `ruff check devforge tests` | `All checks passed!` |
| `python -m pip wheel . --no-deps -w /tmp/devforge-wheel-test` | exactly one `.whl` |

## Known limitations

- `devforge create-app` is a **stub** that exits with a message. The full
  app-from-PRD pipeline (DEVF-060 – 071) is not implemented.
- `openai_api` and `claude_agent_sdk` provider types are accepted by the
  config but the registry delegates them to the CLI adapters as
  placeholders.
- State persists as JSON files under `.orchestrator/runs/<id>/state/`.
  SQLite (DEVF-080) and the local dashboard (DEVF-082, DEVF-083) are
  not yet built.
- `WorkflowEngine` only registers a handler for `feature`. Other
  workflows named in `docs/plan/02 §6` raise `WorkflowLoadError` —
  `bugfix`, `refactor`, `code_review_only`, `app_from_prd`,
  `research_optimize` are deferred to a future release.
- This release is **not published to PyPI**. `pip install -e ".[dev]"`
  against a local checkout is the supported install path.

See [`ROADMAP.md`](ROADMAP.md) for the full implemented / partial /
not-implemented matrix.

## Upgrade notes

This is the first tagged release; nothing to migrate from.
