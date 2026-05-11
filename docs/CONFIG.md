# Config

`devforge.yaml` is the single configuration file. The CLI accepts it via
`-c <path>`; if you omit `-c`, commands use `devforge.yaml` in the current
working directory.

The loader (`devforge.core.config_loader.find_config`) can also search upward
from a starting path until it finds `devforge.yaml` or hits the filesystem
root — handy for sub-directory invocations.

A full sample lives at [`examples/devforge.yaml`](../examples/devforge.yaml).

## Schema

The Pydantic model is `DevforgeConfig` in
[`devforge/core/config_loader.py`](../devforge/core/config_loader.py). Sections:

```yaml
project: { ... }            # required
mode: { ... }               # optional, has defaults
providers: { id: { ... } }  # zero or more
roles: { name: { ... } }    # zero or more
validation: { ... }
file_policy: { ... }
command_policy: { ... }
scoring: { ... }
stop_conditions: { ... }
```

### `project`

```yaml
project:
  name: my-app
  root: "."                  # repo root; usually the cwd
  default_branch: main
  worktree_root: "../my-app-worktrees"   # optional; default is <root>/.orchestrator/worktrees
  profile: python_fastapi    # references devforge/project_profiles/*.yaml
```

### `mode`

```yaml
mode:
  max_iterations_per_task: 4          # revision loop hard limit
  max_candidates_per_task: 2          # NOT YET ENFORCED — see ROADMAP
  keep_best_candidate: true
```

### `providers`

Each entry is keyed by an id you choose. See [`PROVIDERS.md`](PROVIDERS.md)
for the per-type fields.

```yaml
providers:
  codex_sub_cli:
    type: codex_cli           # one of: codex_cli | claude_cli | local_rule_based
                              #         | mock (testing) | openai_api | claude_agent_sdk
    enabled: true
    auth: chatgpt_subscription   # informational; see PROVIDERS.md
    command: codex
    default_args: ["exec", "--sandbox", "workspace-write", "--ask-for-approval", "never", "--ephemeral"]
    timeout_sec: 900
    env_required: []          # provider is treated as disabled if any of these envs is missing
```

### `roles`

```yaml
roles:
  implementer:
    provider_order: ["codex_sub_cli", "claude_sub_cli"]
    tournament: false
    avoid_same_provider_as_implementer: false
    required_capabilities: []   # empty → role default applies (see PROVIDERS.md)
  reviewer:
    provider_order: ["claude_sub_cli", "codex_sub_cli"]
    avoid_same_provider_as_implementer: true
  judge:
    provider_order: ["local_rule_based"]
```

### `validation`

`commands` accepts any of the 11 named keys understood by
`devforge/evaluators/validation_runner.py`. Empty / missing keys are skipped.

```yaml
validation:
  default_timeout_sec: 300
  commands:
    lint: "ruff check ."
    typecheck: "mypy ."
    test: "pytest -q"
    build: ""                 # leave blank if your project has no build step
    import_smoke: "python -c 'import app.main'"
```

### `file_policy` / `command_policy`

```yaml
file_policy:
  allowed_paths: ["src/**", "tests/**", "docs/**", "pyproject.toml"]
  blocked_paths: [".git/**", ".env", ".env.*", "secrets/**"]
  require_human_review_if_modified:
    ["package-lock.json", "Dockerfile", "infra/**", "migrations/**"]

command_policy:
  blocked_patterns: ["rm -rf", "git push", "git reset --hard", "curl * | sh", "sudo"]
  require_human_review: ["npm install", "pip install", "poetry add", "uv add"]
```

### `scoring` / `stop_conditions`

```yaml
scoring:
  build_pass: 25
  tests_pass: 25
  lint_pass: 10
  typecheck_pass: 10
  acceptance_coverage: 20
  reviewer_pass: 10
  # negative contributions
  blocked_file_modified: 100
  secret_detected: 100
  test_deleted: 60
  test_weakened: 50
  unrelated_large_diff: 30
  critical_review_issue: 20

stop_conditions:
  accept_when:
    build_pass: true
    tests_pass: true
    reviewer_verdict: pass
    min_score: 85
  discard_when:
    blocked_file_modified: true
    secret_detected: true
```

See [`SECURITY.md`](SECURITY.md) for the judge decision matrix and
[`WORKFLOWS.md`](WORKFLOWS.md) for how `scoring` flows through the feature
workflow.

## Validation errors

If a value is invalid (e.g. unknown provider `type`, missing `project.name`),
the loader raises `ConfigError` and the CLI exits with code `2` and a
human-readable message.
