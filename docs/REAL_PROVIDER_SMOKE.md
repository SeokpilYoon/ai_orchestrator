# Real Provider Smoke Tests (DEVF-092)

These tests verify that `codex` / `claude` CLI providers actually work on a real
machine. They are **opt-in**: nothing in the default `pytest -q` run depends on
having a real CLI installed.

The default suite is mock-only by design. See `tests/integration/_mock_helpers.py`
for the standard mock provider fixtures used everywhere else.

## How the gating works

Three layers protect you from accidentally invoking external CLIs (or burning
tokens):

1. **Marker gate** — every smoke test carries `@pytest.mark.real_provider`. A
   collection hook in `tests/conftest.py` auto-skips them unless you pass
   `-m real_provider`.
2. **CLI presence gate** — `_require("codex")` / `_require("claude")` use
   `shutil.which`; missing binaries → `pytest.skip` with a clear reason.
3. **Real-run gate** — Tier B tests (the ones that actually call the LLM) also
   require `DEVFORGE_REAL_PROVIDER_RUN=1`.

## Tier A — token-free smoke

`codex` / `claude` only need to be on `PATH`. No LLM tokens consumed.

```bash
.venv/bin/pytest -m real_provider tests/integration/test_real_provider_smoke.py
```

What it checks:

- `provider.healthcheck()` returns `True` (provider runs `<cli> --version` internally)
- Codex command builder uses `--sandbox read-only` for reviewer roles and
  `--sandbox workspace-write --ask-for-approval never --ephemeral` for implementer
- Codex never emits `danger-full-access` / `--yolo` / sandbox-bypass flags
- Claude command builder restricts reviewer tools to `Read,Grep,Glob` with
  `--permission-mode plan`, and implementer tools to `Read,Edit,Write,Bash` with
  `--permission-mode acceptEdits`
- Claude never emits `dangerously-skip-permissions` / `bypassPermissions`

## Tier B — real LLM invocation (token-spending)

```bash
DEVFORGE_REAL_PROVIDER_RUN=1 .venv/bin/pytest -m real_provider \
    tests/integration/test_real_provider_smoke.py
```

What it does:

- Creates a throwaway `git init` repo under `pytest`'s `tmp_path`
- Asks each provider for a short read-only response (tens of tokens)
- Verifies success and that no secret pattern leaked into stdout/stderr
- On environment issues (`auth_expired`, `usage_limit_hit`, `rate_limit`,
  `command_missing`) it **skips** the test instead of failing — those are
  user-environment problems, not regressions

## Artifacts

| Where | When | How safe |
|---|---|---|
| `tmp_path/smoke/<name>` | always | pytest cleans up after the run |
| `$DEVFORGE_SMOKE_ARTIFACT_DIR/<name>` | only when env var is set | same secret scanner + 4 KB truncate |

Every artifact write runs through `secret_scanner.scan_diff_and_logs` and is
truncated to 4 KB. If a secret pattern is detected the body is replaced with
`[REDACTED — secret pattern detected in output]`.

## Hard prohibitions

The smoke tests must never:

- write to `.env`, `secrets/**`, or `.git/**`
- run `rm -rf`, `git push`, `git reset --hard`, `curl ... | sh`, `wget ... | sh`,
  `sudo`, or any other destructive / network-installer command
- embed real API keys / tokens / credentials in code, fixtures, or artifacts
- depend on a live network beyond what the CLI itself does

## Adding a new smoke test

1. Decorate with `@pytest.mark.real_provider` (or rely on `pytest_mark` at module level)
2. Call `_require("codex")` / `_require("claude")` before the provider work
3. For Tier B tests also call `_require_real_run()`
4. Persist artifacts only through `_save_artifact(...)` — never write
   raw stdout/stderr to disk

## Running in CI

This suite is intentionally not wired into CI. Two reasons:

- Credentials handling (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or interactive
  subscription auth) requires explicit security review
- Each Tier B run consumes provider tokens

A future cycle may add a separate dedicated workflow that exercises Tier A only
(no tokens, but needs CLIs installed in the runner image).
