# Reports, apply, cleanup

Three CLI commands inspect or act on completed runs:
[`devforge report`](#devforge-report),
[`devforge apply`](#devforge-apply),
[`devforge cleanup`](#devforge-cleanup).

## `devforge report`

```bash
devforge report [--run <id> | --latest] [--format <fmt>] [-c <config>]
```

| Option | Default | Notes |
|---|---|---|
| `--run` | (none) | specific run id |
| `--latest` | (implicit) | `--run` overrides; if both are omitted, the latest run is used automatically |
| `--format`, `-f` | `text` | `text` \| `markdown` (alias of text) \| `json` \| `state` |
| `--config`, `-c` | `devforge.yaml` | where the project root comes from |

### `text` / `markdown` output

Renders a structured report from the state store and on-disk artifacts:

```
# Run <run_id> — feature

- Status: completed
- Started: 2026-05-11T...
- Completed: 2026-05-11T...
- Chosen candidate: mock_impl, score 95.0, decision=accept

## Steps (5/6 completed)

- [x] normalize_task           completed
- [x] inspect_repo             completed
- [x] plan                     completed
- [x] implement_candidates     completed
- [-] comparison_report        skipped — fewer than 2 candidates
- [x] final_report             completed

## Candidates

| Candidate | Provider | Score | Decision |
|...|

## Fallback history
| Provider | Failure class | Error |
| ...      (or _none_ when no fallback happened)

## Artifacts
- Final report: `final_report.md`
- Decision: `decision.json`
- Comparison: `comparison.md`   (when present)
- Failure: `failure.json`       (when present)

---
<contents of final_report.md>
```

The body of `comparison.md` is intentionally **not** inlined — only the
pointer is shown — so reports stay short.

### `json` output

Prints the run's `decision.json` verbatim with a one-line state header in a
leading `#` comment.

### `state` output

Prints the state-store summary line plus a JSON dump of `state/run.json`. Use
this when you want machine-readable status without the full report.

### No runs found

If `.orchestrator/runs/` doesn't exist (or is empty) the command prints
`No runs found.` and exits 0.

## `devforge apply`

```bash
devforge apply --run <run_id> --candidate <candidate_id> [-c <config>] [--no-ff/--ff]
```

Merges `agent/<run_id>-<candidate_id>` into the current HEAD with
`git merge --no-edit` (and `--no-ff` by default for visibility).

Safety gates:

| Check | Failure | Exit code |
|---|---|---|
| Working tree clean | "Working tree is dirty. …" | 2 |
| Branch exists | "Branch '…' does not exist." | 2 |
| Merge conflict | `git merge --abort` runs automatically + clear message | 3 |
| Config invalid | prints `ConfigError` | 2 |
| Success | "Merged agent/<run>-<cand>." | 0 |

Use `--ff` if you want a plain fast-forward instead of a merge commit.

## `devforge cleanup`

```bash
devforge cleanup --run <run_id> [-c <config>]
```

Removes every worktree whose directory name starts with `<run_id>-` from the
configured worktree root, and deletes the matching `agent/<run_id>-*` branches.
**Artifacts under `.orchestrator/runs/<run_id>/` are kept** so reports remain
inspectable after cleanup.

Typical flow:

```bash
devforge run --workflow feature --task task.md -c devforge.yaml
devforge report --latest -c devforge.yaml
devforge apply --run 20260511_120000_001 --candidate codex_sub_cli -c devforge.yaml
devforge cleanup --run 20260511_120000_001 -c devforge.yaml
```
