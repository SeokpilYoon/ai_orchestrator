"""Typer-based CLI entrypoint.

Authoritative reference: docs/plan/02 §11, docs/plan/03 DEVF-010.
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

from devforge import __version__

app = typer.Typer(
    name="devforge",
    help="AI Dev Orchestrator — orchestrate Claude Code / Codex as bounded workers.",
    no_args_is_help=True,
    add_completion=False,
)
providers_app = typer.Typer(help="Inspect and manage providers.")
app.add_typer(providers_app, name="providers")


# ---------------------------------------------------------------------------
# Root callback: --version
# ---------------------------------------------------------------------------

def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show devforge version and exit.",
    ),
) -> None:
    """AI Dev Orchestrator — orchestrate Claude Code / Codex as bounded workers."""
    # Eager --version callback prints and exits; otherwise pass through to subcommand.
    _ = version


# ---------------------------------------------------------------------------
# version (subcommand)
# ---------------------------------------------------------------------------

@app.command()
def version() -> None:
    """Print devforge version."""
    typer.echo(__version__)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init(
    path: Path = typer.Option(Path.cwd(), "--path", "-p", help="Target directory."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing devforge.yaml."),
) -> None:
    """Create a sample devforge.yaml in the target directory."""
    from devforge.core.config_loader import DevforgeConfig, ProjectConfig

    path.mkdir(parents=True, exist_ok=True)
    target = path / "devforge.yaml"
    if target.exists() and not force:
        typer.echo(f"devforge.yaml already exists at {target}. Use --force to overwrite.")
        raise typer.Exit(code=1)

    sample = DevforgeConfig(
        project=ProjectConfig(name=path.name, root=str(path), profile="python_fastapi"),
    )
    import yaml as _yaml

    target.write_text(
        _yaml.safe_dump(json.loads(sample.model_dump_json()), sort_keys=False),
        encoding="utf-8",
    )
    typer.echo(f"Created {target}")


# ---------------------------------------------------------------------------
# providers status
# ---------------------------------------------------------------------------

@providers_app.command("status")
def providers_status(
    config: Path = typer.Option(Path("devforge.yaml"), "--config", "-c"),
) -> None:
    """Show provider availability."""
    from devforge.core.config_loader import ConfigError, load_config
    from devforge.providers.registry import ProviderRegistry

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    registry = ProviderRegistry.from_config(cfg)
    rows = registry.status_rows()

    name_w = max((len(r.name) for r in rows), default=10)
    status_w = max((len(r.status) for r in rows), default=10)
    typer.echo(f"{'Provider':<{name_w}}  {'Status':<{status_w}}  Detail")
    typer.echo(f"{'-' * name_w}  {'-' * status_w}  {'-' * 30}")
    for r in rows:
        typer.echo(f"{r.name:<{name_w}}  {r.status:<{status_w}}  {r.detail}")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@app.command()
def run(
    workflow: str = typer.Option("feature", "--workflow", "-w"),
    task: Path = typer.Option(..., "--task", "-t", exists=True, readable=True),
    implementer: str | None = typer.Option(None, "--implementer"),
    reviewer: str | None = typer.Option(None, "--reviewer"),
    tournament: str | None = typer.Option(
        None, "--tournament", help="Comma-separated provider ids for tournament mode."
    ),
    config: Path = typer.Option(Path("devforge.yaml"), "--config", "-c"),
) -> None:
    """Execute a workflow on the given task."""
    from devforge.core.config_loader import ConfigError, load_config
    from devforge.core.run_context import create_run_context

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    ctx = create_run_context(
        project_root=Path(cfg.project.root),
        workflow=workflow,
        input_path=task,
        extra_metadata={
            "implementer_override": implementer,
            "reviewer_override": reviewer,
            "tournament": tournament.split(",") if tournament else None,
        },
    )
    typer.echo(f"Created run: {ctx.run_id}")
    typer.echo(f"Run directory: {ctx.root}")

    # Route through WorkflowEngine so every run records state (DEVF-012/013).
    try:
        from devforge.core.workflow_engine import WorkflowEngine, WorkflowLoadError

        engine = WorkflowEngine(cfg, ctx)
        engine.run(
            workflow,
            implementer_override=implementer,
            reviewer_override=reviewer,
        )
    except WorkflowLoadError as exc:
        typer.echo(f"Workflow error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:  # pragma: no cover - driver may be partial during dev
        typer.echo(f"Workflow driver error: {exc}", err=True)
        raise typer.Exit(code=3) from exc


# ---------------------------------------------------------------------------
# create-app  (DEVF-060..065 foundation; later stages DEVF-066+ pending)
# ---------------------------------------------------------------------------

@app.command("create-app")
def create_app(
    from_: Path = typer.Option(..., "--from", exists=True, readable=True),
    stack: str = typer.Option(
        "python-fastapi-only",
        "--stack",
        help=(
            "Target stack. `python-fastapi-only` emits a runnable scaffold; "
            "other stacks are recorded as skipped."
        ),
    ),
    implementer: str | None = typer.Option(
        None,
        "--implementer",
        help=(
            "Override the implementer provider for the vertical slice "
            "implementer stage (DEVF-067). Defaults to the provider order "
            "in cfg.roles['implementer']."
        ),
    ),
    reviewer: str | None = typer.Option(
        None,
        "--reviewer",
        help=(
            "Override the reviewer provider for the vertical slice "
            "implementer stage (DEVF-067). Defaults to the provider order "
            "in cfg.roles['reviewer']."
        ),
    ),
    config: Path = typer.Option(Path("devforge.yaml"), "--config", "-c"),
) -> None:
    """Run the app_from_prd workflow and write planning plus scaffold artifacts."""
    from devforge.core.config_loader import ConfigError, load_config
    from devforge.core.run_context import create_run_context
    from devforge.core.workflow_engine import WorkflowEngine, WorkflowLoadError

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    ctx = create_run_context(
        project_root=Path(cfg.project.root),
        workflow="app_from_prd",
        input_path=from_,
        extra_metadata={
            "stack": stack,
            "implementer_override": implementer,
            "reviewer_override": reviewer,
        },
    )
    typer.echo(f"Created run: {ctx.run_id}")
    typer.echo(f"Run directory: {ctx.root}")

    try:
        engine = WorkflowEngine(cfg, ctx)
        engine.run(
            "app_from_prd",
            implementer_override=implementer,
            reviewer_override=reviewer,
        )
    except WorkflowLoadError as exc:
        typer.echo(f"Workflow error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"Workflow driver error: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    typer.echo(f"Workflow artifacts written to {ctx.root}")


# ---------------------------------------------------------------------------
# dashboard (DEVF-082)
# ---------------------------------------------------------------------------

@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    config: Path = typer.Option(Path("devforge.yaml"), "--config", "-c"),
) -> None:
    """Serve the read-only dashboard backend over HTTP."""
    from devforge.core.config_loader import ConfigError, load_config

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    try:
        from devforge.dashboard.backend import DashboardImportError, serve
        serve(Path(cfg.project.root), host=host, port=port)
    except DashboardImportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@app.command()
def report(
    run_id: str | None = typer.Option(None, "--run"),
    latest: bool = typer.Option(False, "--latest"),
    list_: bool = typer.Option(
        False,
        "--list",
        help="List recent runs from the SQLite index instead of printing a single run.",
    ),
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        help="Filter --list by workflow id (e.g. feature, app_from_prd).",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        help="Maximum runs shown in --list mode.",
    ),
    fmt: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="text | markdown | json | state (text and markdown are aliases)",
    ),
    config: Path = typer.Option(Path("devforge.yaml"), "--config", "-c"),
) -> None:
    """Print a run report. Defaults to the latest run when --run is omitted."""
    from devforge.core.config_loader import ConfigError, load_config

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if list_:
        _emit_list(
            project_root=Path(cfg.project.root),
            workflow=workflow,
            limit=limit,
            fmt=fmt,
        )
        return

    runs_dir = Path(cfg.project.root) / ".orchestrator" / "runs"
    target = _resolve_run_dir(runs_dir, run_id=run_id, latest=latest)
    if target is None:
        typer.echo("No runs found.")
        raise typer.Exit(code=0)
    if not target.exists():
        typer.echo(f"Run not found: {target}", err=True)
        raise typer.Exit(code=2)

    if fmt == "json":
        _emit_json(target)
    elif fmt == "state":
        _emit_state(target)
    elif fmt in ("text", "markdown"):
        _emit_markdown(target)
    else:
        typer.echo(
            f"Unknown format '{fmt}'. Use text | markdown | json | state.", err=True
        )
        raise typer.Exit(code=2)


def _emit_list(
    *, project_root: Path, workflow: str | None, limit: int, fmt: str
) -> None:
    """Cross-run summary backed by the DEVF-080 SQLite index."""
    import json as _json

    from devforge.core.sqlite_index import SqliteIndex

    db_path = project_root / ".orchestrator" / "state.db"
    if not db_path.exists():
        typer.echo("No SQLite index yet — run a workflow first.")
        return
    idx = SqliteIndex(db_path)
    runs = idx.list_runs(workflow=workflow, limit=max(1, limit))

    if fmt == "json":
        typer.echo(_json.dumps(runs, indent=2, ensure_ascii=False))
        return

    if not runs:
        if workflow:
            typer.echo(f"No runs recorded for workflow '{workflow}'.")
        else:
            typer.echo("No runs recorded yet.")
        return

    lines: list[str] = []
    if fmt in ("text", "markdown"):
        lines.append(
            "| Run | Workflow | Status | Started | Completed | Chosen |"
        )
        lines.append("|---|---|---|---|---|---|")
        for r in runs:
            lines.append(
                f"| `{r.get('run_id', '?')}` | "
                f"{r.get('workflow', '?')} | "
                f"**{r.get('status', '?')}** | "
                f"{r.get('started_at') or '—'} | "
                f"{r.get('completed_at') or '—'} | "
                f"{r.get('chosen_candidate') or '—'} |"
            )
    elif fmt == "state":
        # Compact one-line-per-run for shell consumption.
        for r in runs:
            lines.append(
                f"{r.get('run_id', '?')}\t{r.get('workflow', '?')}\t"
                f"{r.get('status', '?')}\t"
                f"chosen={r.get('chosen_candidate') or '-'}"
            )
    else:
        typer.echo(
            f"Unknown format '{fmt}'. Use text | markdown | json | state.",
            err=True,
        )
        raise typer.Exit(code=2)
    typer.echo("\n".join(lines))


def _resolve_run_dir(
    runs_dir: Path, *, run_id: str | None, latest: bool
) -> Path | None:
    """Pick the run directory for a report. ``run_id`` wins; otherwise the latest run."""
    if not runs_dir.exists():
        return None
    if run_id:
        return runs_dir / run_id
    runs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    if not runs:
        return None
    _ = latest  # explicit intent; report always defaults to latest when --run is omitted
    return runs[-1]


def _emit_json(target: Path) -> None:
    """Legacy JSON output — emit decision.json verbatim with a state header."""
    from devforge.core.state_store import StateStore

    state = StateStore(target)
    summary = state.summary_line()
    if summary:
        typer.echo(f"# {summary}")
    decision = target / "decision.json"
    if decision.exists():
        typer.echo(decision.read_text(encoding="utf-8"))
    else:
        typer.echo("{}")


def _emit_state(target: Path) -> None:
    import contextlib
    import json as _json

    from devforge.core.state_store import StateStore, StateStoreError

    state = StateStore(target)
    summary = state.summary_line()
    if not summary:
        typer.echo(f"Run {target.name} has no state recorded.")
        return
    typer.echo(summary)
    with contextlib.suppress(StateStoreError):
        typer.echo(_json.dumps(state.load_run(), indent=2, ensure_ascii=False))


def _emit_markdown(target: Path) -> None:
    """Render a structured markdown report from state + artifacts on disk.

    See docs/plan/03 DEVF-081 — "run 요약과 candidate 비교 출력".
    """
    import json as _json

    from devforge.core.state_store import StateStore

    state = StateStore(target)
    state_initialized = state.is_initialized()
    run_meta = state.load_run() if state_initialized else None
    steps = state.load_steps() if state_initialized else []
    candidates = state.load_candidates() if state_initialized else []

    lines: list[str] = []
    workflow = run_meta.get("workflow") if run_meta else "?"
    lines.append(f"# Run {target.name} — {workflow}")
    lines.append("")
    if run_meta:
        lines.append(f"- Status: **{run_meta.get('status', 'unknown')}**")
        if run_meta.get("started_at"):
            lines.append(f"- Started: {run_meta['started_at']}")
        if run_meta.get("completed_at"):
            lines.append(f"- Completed: {run_meta['completed_at']}")
        chosen = run_meta.get("chosen_candidate")
        if chosen:
            score_suffix = ""
            verdict_suffix = ""
            for c in candidates:
                if c.get("candidate_id") == chosen:
                    score_suffix = f", score {float(c.get('score', 0.0)):.1f}"
                    verdict_suffix = f", decision={c.get('decision', '?')}"
                    break
            lines.append(
                f"- Chosen candidate: **{chosen}**{score_suffix}{verdict_suffix}"
            )
        if run_meta.get("error"):
            lines.append(f"- Error: {run_meta['error']}")
    else:
        lines.append("- _no recorded state_")
    lines.append("")

    if steps:
        done = sum(1 for s in steps if s.get("status") == "completed")
        lines.append(f"## Steps ({done}/{len(steps)} completed)")
        lines.append("")
        for s in steps:
            status = s.get("status", "?")
            marker = {
                "completed": "[x]",
                "running": "[~]",
                "failed": "[!]",
                "skipped": "[-]",
                "pending": "[ ]",
            }.get(status, "[?]")
            note = s.get("note")
            suffix = f" — {note}" if note else ""
            lines.append(
                f"- {marker} {s.get('stage_id', '?'):<24}{status}{suffix}"
            )
        lines.append("")

    if candidates:
        lines.append("## Candidates")
        lines.append("")
        lines.append("| Candidate | Provider | Score | Decision |")
        lines.append("|---|---|---:|---|")
        for c in sorted(candidates, key=lambda x: x.get("score", 0.0), reverse=True):
            lines.append(
                f"| {c.get('candidate_id', '?')} | {c.get('provider_id', '?')} | "
                f"{float(c.get('score', 0.0)):.1f} | {c.get('decision', '?')} |"
            )
        lines.append("")

    fallback_path = target / "fallback_history.json"
    lines.append("## Fallback history")
    lines.append("")
    if fallback_path.exists():
        try:
            history = _json.loads(fallback_path.read_text(encoding="utf-8")).get(
                "history", []
            )
        except _json.JSONDecodeError:
            history = []
        if history:
            lines.append("| Provider | Failure class | Error |")
            lines.append("|---|---|---|")
            for entry in history:
                lines.append(
                    f"| {entry.get('provider', '?')} | "
                    f"{entry.get('failure_class', '?')} | "
                    f"{(entry.get('error') or '')[:60]} |"
                )
        else:
            lines.append("_none_")
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    final_md = target / "final_report.md"
    decision = target / "decision.json"
    comparison = target / "comparison.md"
    failure = target / "failure.json"
    if final_md.exists():
        lines.append(f"- Final report: `{final_md.name}`")
    if decision.exists():
        lines.append(f"- Decision: `{decision.name}`")
    if comparison.exists():
        lines.append(f"- Comparison: `{comparison.name}`")
    if failure.exists():
        lines.append(f"- Failure: `{failure.name}`")
    lines.append("")

    typer.echo("\n".join(lines).rstrip())

    if final_md.exists():
        typer.echo("")
        typer.echo("---")
        typer.echo("")
        typer.echo(final_md.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

@app.command()
def apply(
    run_id: str = typer.Option(..., "--run"),
    candidate: str = typer.Option(..., "--candidate"),
    config: Path = typer.Option(Path("devforge.yaml"), "--config", "-c"),
    no_ff: bool = typer.Option(
        True, "--no-ff/--ff", help="Merge with --no-ff (default) or fast-forward."
    ),
) -> None:
    """Merge a candidate's ``agent/<run_id>-<candidate>`` branch into current HEAD."""
    import subprocess

    from devforge.core.config_loader import ConfigError, load_config

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    repo = Path(cfg.project.root)
    branch = f"agent/{run_id}-{candidate}"

    if not _git_clean(repo):
        typer.echo(
            "Working tree is dirty. Commit or stash before applying.", err=True
        )
        raise typer.Exit(code=2)
    if not _branch_exists(repo, branch):
        typer.echo(
            f"Branch '{branch}' does not exist. Try `devforge report --run {run_id}`.",
            err=True,
        )
        raise typer.Exit(code=2)

    args = ["git", "merge", "--no-edit"]
    if no_ff:
        args.append("--no-ff")
    args.append(branch)
    proc = subprocess.run(args, cwd=str(repo), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        subprocess.run(
            ["git", "merge", "--abort"], cwd=str(repo), capture_output=True, check=False
        )
        typer.echo(
            f"Merge failed; aborted. Resolve conflicts manually.\n{proc.stderr.strip()}",
            err=True,
        )
        raise typer.Exit(code=3)
    typer.echo(f"Merged {branch}.")


def _git_clean(repo: Path) -> bool:
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and not proc.stdout.strip()


def _branch_exists(repo: Path, branch: str) -> bool:
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

@app.command()
def cleanup(
    run_id: str = typer.Option(..., "--run"),
    config: Path = typer.Option(Path("devforge.yaml"), "--config", "-c"),
) -> None:
    """Remove worktrees associated with a run (keeps artifacts)."""
    from devforge.core.config_loader import ConfigError, load_config
    from devforge.git.worktree_manager import WorktreeManager

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    mgr = WorktreeManager(
        repo_root=Path(cfg.project.root),
        worktree_root=Path(cfg.project.worktree_root) if cfg.project.worktree_root else None,
    )
    removed = mgr.cleanup_run(run_id)
    typer.echo(f"Removed {len(removed)} worktree(s) for run {run_id}.")


if __name__ == "__main__":
    app()
