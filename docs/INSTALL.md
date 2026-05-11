# Install

Local-only development setup. Everything runs in a Python venv; no daemons,
no network services.

## Requirements

- **Python 3.11+** (3.12, 3.13, 3.14 all work)
- **git** (worktree manager + tests depend on it)
- Optional: **`codex`** and/or **`claude`** CLIs for opt-in real-provider
  smoke tests — see [`REAL_PROVIDER_SMOKE.md`](REAL_PROVIDER_SMOKE.md)

The default test suite is mock-only and does not need the codex/claude CLIs.

## Steps

```bash
# 1. Create and activate a venv
python -m venv .venv
source .venv/bin/activate           # macOS / Linux
# .venv\Scripts\activate            # Windows

# 2. Install in editable mode with dev extras
pip install -e ".[dev]"

# 3. Smoke-check the CLI
devforge --help
devforge version

# 4. Run the mock-only test suite
pytest -q                            # 168 passed, 8 skipped (real_provider)

# 5. Generate a sample config in the current directory
devforge init --path .               # writes ./devforge.yaml

# 6. Inspect provider availability
devforge providers status -c devforge.yaml
```

After step 6 you'll see a table like:

```
Provider          Status     Detail
----------------  ---------  ------------------------------
claude_api_sdk    disabled   disabled in config
claude_sub_cli    available  auth=claude_subscription
codex_api_cli     disabled   disabled in config
codex_sub_cli     available  auth=chatgpt_subscription
local_rule_based  available  auth=none
```

If a CLI isn't installed, the corresponding row will show
`unavailable_command_missing` — that's fine for the mock suite.

## Where things live

| Path | Purpose | Tracked in git? |
|---|---|---|
| `devforge/` | package source | yes |
| `tests/` | mock + opt-in real-provider tests | yes |
| `docs/` | this guide and others | yes |
| `examples/` | sample `devforge.yaml`, sample task | yes |
| `.orchestrator/runs/<id>/` | per-run artifacts (created at runtime) | no |
| `.orchestrator/worktrees/<id>/` | per-candidate git worktrees | no |
| `_workspace/` | harness scratch / notes | no (except its README) |

## Building a wheel

```bash
python -m pip wheel . --no-deps -w /tmp/devforge-wheel-test
ls /tmp/devforge-wheel-test/devforge-0.0.1-*.whl
```

The wheel is self-contained — the runtime dependencies (`typer`,
`pydantic`, `pyyaml`) are already declared in `pyproject.toml`. Build
artifacts (`dist/`, `build/`, `*.egg-info/`) are gitignored, so you can
build into the repo or into `/tmp/...` without worrying about commits.

For per-release detail see [`../CHANGELOG.md`](../CHANGELOG.md) and
[`RELEASE_NOTES.md`](RELEASE_NOTES.md).

## Next steps

- [`CONFIG.md`](CONFIG.md) — what each `devforge.yaml` section means
- [`PROVIDERS.md`](PROVIDERS.md) — connecting Codex / Claude / local rule-based
- [`WORKFLOWS.md`](WORKFLOWS.md) — running the `feature` workflow
- [`ROADMAP.md`](ROADMAP.md) — which capabilities are alpha-ready vs planned
