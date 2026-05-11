# Providers

devforge treats each LLM / rule engine as a **bounded worker**. The
orchestrator picks workers per role, applies a capability gate, and never
gives them more authority than the role requires.

## Provider types

| `type` | Status | Adapter file | Auth |
|---|---|---|---|
| `codex_cli` | implemented | `devforge/providers/codex_cli.py` | ChatGPT subscription **or** `OPENAI_API_KEY` |
| `claude_cli` | implemented | `devforge/providers/claude_cli.py` | Claude subscription **or** `ANTHROPIC_API_KEY` |
| `local_rule_based` | implemented | `devforge/providers/local_rule_based.py` | none |
| `mock` | testing only | `devforge/providers/mock.py` | none |
| `openai_api` | **placeholder** | delegates to `codex_cli` adapter | `OPENAI_API_KEY` |
| `claude_agent_sdk` | **placeholder** | delegates to `claude_cli` adapter | `ANTHROPIC_API_KEY` |

The two placeholder types accept config so existing samples keep loading, but
they don't add real SDK behavior yet — see [`ROADMAP.md`](ROADMAP.md).

## Provider entry fields

```yaml
providers:
  codex_sub_cli:
    type: codex_cli
    enabled: true
    auth: chatgpt_subscription   # informational
    command: codex               # binary to invoke (default = type-specific)
    default_args: ["exec", "--sandbox", "workspace-write",
                   "--ask-for-approval", "never", "--ephemeral"]
    env_required: []             # provider disabled if any env is missing
    timeout_sec: 900
```

Forbidden flags are rejected at *construction* time:

- Codex: `danger-full-access`, `--yolo`, `--bypass-approvals`, `--bypass-sandbox`
- Claude: `--dangerously-skip-permissions`, `bypassPermissions`

Trying to put any of those in `default_args` raises a `ValueError` before the
adapter is registered.

## Capability gate

`RoleRouter.select` excludes any provider that does not support every
capability required by the role. Defaults live in
[`devforge/core/role_router.py`](../devforge/core/role_router.py)
(`_DEFAULT_ROLE_CAPABILITIES`):

| Role | Required capabilities (default) |
|---|---|
| `implementer` | `edit_files`, `run_shell`, `non_interactive` |
| `reviewer` | `read_repo`, `json_output`, `non_interactive` |
| `qa_engineer` | `read_repo`, `run_shell` |
| `security_reviewer` | `read_repo` |
| `judge` | `deterministic` |
| `product_manager` / `system_architect` / `technical_planner` / `release_manager` | `non_interactive` |

Provider capability sets (from each adapter's `supports()`):

| Provider | Capabilities |
|---|---|
| `CodexCliProvider` | read_repo, edit_files, run_shell, non_interactive, json_output |
| `ClaudeCliProvider` | read_repo, edit_files, run_shell, non_interactive, json_output, review_only |
| `LocalRuleBasedProvider` | deterministic, review_only, json_output, non_interactive |
| `MockProvider` | all six (testing convenience) |

Consequences:

- `local_rule_based` is rejected from `implementer` (no `edit_files`) and
  `reviewer` (no `read_repo`). It's the right pick for `judge`.
- Codex / Claude work for both `implementer` and `reviewer`.

### Per-role override

```yaml
roles:
  reviewer:
    provider_order: ["claude_sub_cli"]
    required_capabilities: ["read_repo", "json_output"]   # narrower than default
```

Set `required_capabilities` to an empty list to keep the role default; set it
to any non-empty list to override.

## Selection rules

1. **`override`** — `--implementer X` / `--reviewer X` on the CLI: only that
   provider is considered. The capability gate still applies, so an incompatible
   override is rejected with `missing capabilities: …`.
2. **`provider_order`** — for `single` mode this is the fallback chain; for
   `tournament` mode each entry runs as a separate candidate.
3. **healthcheck** — providers in `unavailable_*` / `disabled_by_policy` are
   skipped with the reason recorded under `excluded[provider_id]`.
4. **`avoid_same_provider_as_implementer`** — when set on the reviewer role,
   the reviewer is never the same provider that wrote the patch.

## Tournament vs single mode

- `tournament: true` + ≥2 healthy providers → each provider produces its own
  candidate worktree. The orchestrator emits `comparison.md` automatically.
- Otherwise → single mode. The first provider in `provider_order` is tried;
  on a *recoverable* failure (`auth_expired`, `usage_limit_hit`, `rate_limit`,
  `timeout`, `command_missing`, `malformed_output`) the next provider is tried.
  Non-recoverable failures (`policy_violation`, `unknown`) short-circuit.

See [`WORKFLOWS.md`](WORKFLOWS.md#fallback-and-tournament) for how this looks
in artifacts.

## Inspecting status

```bash
devforge providers status -c devforge.yaml
```

Output legend:

| Status | Meaning |
|---|---|
| `available` | binary on PATH, healthcheck (`<cli> --version`) returned 0 |
| `unavailable_command_missing` | binary not on PATH |
| `unavailable_auth` / `unavailable_usage_limit` / `unavailable_timeout` | inferred from stderr patterns when healthcheck runs |
| `disabled_by_policy` | `env_required` entries are missing |
| `disabled` | `enabled: false` in config |
