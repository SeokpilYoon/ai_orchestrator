# Security model

devforge treats every agent as untrusted and every patch as needing proof. The
controls are deterministic rules + a judge — not vibes from the LLM that wrote
the patch.

## Isolation

- Every candidate runs inside its own `git worktree` under
  `<repo>/.orchestrator/worktrees/<run_id>-<candidate_id>/` (or the
  `project.worktree_root` you configure).
- The main working tree is never written to by an agent. Promoting changes
  requires the explicit [`devforge apply`](REPORTS.md#devforge-apply) command,
  which merges from the candidate branch and refuses to start on a dirty
  working tree.

## File policy

Set under `file_policy:` in `devforge.yaml` (see
[`CONFIG.md`](CONFIG.md#file_policy--command_policy)):

- **`blocked_paths`** — if any changed file matches one of these globs the
  candidate's decision becomes `discard`. Defaults include `.git/**`, `.env`,
  `.env.*`, `secrets/**`.
- **`require_human_review_if_modified`** — flags the candidate's policy result
  but does not auto-discard. Typical entries: `package-lock.json`, `Dockerfile`,
  `infra/**`, `migrations/**`.
- **`allowed_paths`** — when non-empty, any file outside this set is recorded
  as `outside_allowed`.

## Command policy

`command_policy:` scans the agent's stdout/stderr for hostile commands. Two
buckets:

- **`blocked_patterns`** → `discard`. Defaults: `rm -rf`, `git push`,
  `git reset --hard`, `curl * | sh`, `wget * | sh`, `sudo`,
  `docker system prune`.
- **`require_human_review`** → flags only. Defaults: `pip install`,
  `npm install`, `poetry add`, `uv add`, `docker compose up`, `terraform apply`,
  `kubectl apply`.

Patterns are case-insensitive substring matches with `*` as a wildcard.

## Secret scanner

`devforge/evaluators/secret_scanner.py` runs against every candidate diff,
stdout, and stderr. Detected patterns:

| Kind | Pattern |
|---|---|
| `openai_api_key` | `sk-[A-Za-z0-9]{20,}` |
| `anthropic_api_key` | `sk-ant-[A-Za-z0-9_-]{20,}` |
| `google_api_key` | `AIza...` |
| `aws_access_key` | `AKIA[0-9A-Z]{16}` |
| `github_token` | `gh[pousr]_...` |
| `private_key` | `-----BEGIN ... PRIVATE KEY-----` |
| `jwt` | `eyJ...eyJ...sig` |
| `generic_password` / `generic_token` | `password = "..."` / `token = "..."` |

Detected matches are recorded by `(kind, location, line)` only — the secret
value is never stored in artifacts. `.env` and `.env.*` are also flagged
explicitly regardless of content.

## Test mutation checker

`devforge/evaluators/test_mutation_checker.py` looks for:

- Whole test files deleted in the diff
- Newly added `@pytest.mark.skip` / `@pytest.mark.xfail` / `it.skip` /
  `test.skip`
- Weakened assertions: `assert True`, `assert ... or True`
- Lowered thresholds (e.g. `>= 0.9` → `>= 0.1`)

Any of these flips the decision to `human_review`.

## Judge decision

Order of checks (in `devforge/evaluators/judge.py`):

1. `secret_detected` → `discard`
2. `blocked_file_modified` → `discard`
3. `command_policy_blocked` → `discard`
4. `test_deleted_or_weakened` → `human_review`
5. `not build_pass` → `revise`
6. `not tests_pass` → `revise`
7. `score >= min_score && reviewer == pass` → `accept`
8. `score > previous_best` → `keep_candidate_but_continue`
9. `reviewer == needs_revision` → `revise`
10. else → `discard`

`min_score` and reviewer expectations come from
`stop_conditions.accept_when` in `devforge.yaml`.

## Real provider smoke

Opt-in tests under
[`REAL_PROVIDER_SMOKE.md`](REAL_PROVIDER_SMOKE.md) actually invoke `codex` /
`claude` CLIs. Even there:

- Artifacts pass through the same secret scanner and are truncated to 4 KB
  before being written.
- A second env-var gate (`DEVFORGE_REAL_PROVIDER_RUN=1`) is required before
  any LLM token is consumed.
- `auth_expired` / `usage_limit_hit` / `rate_limit` / `command_missing`
  trigger `pytest.skip` so user-environment issues never read as a real
  regression.

## Dogfood

The development harness (`.claude/`, `CLAUDE.md`, `_workspace/`) is intended
to follow the same rules as the product:

- Never commit `.env`, `.env.*`, `secrets/**`, or anything under `.git/`.
- Treat blocked-command patterns as off-limits in scripts and docs.
- Use placeholders (`<set-via-env>`) instead of real API keys anywhere.

The skill `/devf-policy-dogfood` exists to apply these gates to harness
changes before they land.
