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
# version
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

    # Stage execution is wired in stages/* — invoked here once feature workflow lands.
    # For MVP-1 we hand off to a minimal driver:
    try:
        from devforge.stages.feature_driver import run_feature_workflow

        run_feature_workflow(cfg, ctx, implementer, reviewer)
    except Exception as exc:  # pragma: no cover - driver may be partial during dev
        typer.echo(f"Workflow driver error: {exc}", err=True)
        raise typer.Exit(code=3) from exc


# ---------------------------------------------------------------------------
# create-app  (stub — DEVF-060+ scope, M6)
# ---------------------------------------------------------------------------

@app.command("create-app")
def create_app(
    from_: Path = typer.Option(..., "--from", exists=True, readable=True),
    stack: str = typer.Option("python-fastapi-only", "--stack"),
) -> None:
    """Generate an app from a PRD (M6 — currently a stub)."""
    typer.echo(
        "create-app workflow is part of M6 (app_from_prd). "
        f"Inputs accepted: from={from_}, stack={stack}. Not yet implemented."
    )
    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@app.command()
def report(
    run_id: str | None = typer.Option(None, "--run"),
    latest: bool = typer.Option(False, "--latest"),
    fmt: str = typer.Option("text", "--format", "-f", help="text|json|markdown"),
    config: Path = typer.Option(Path("devforge.yaml"), "--config", "-c"),
) -> None:
    """Print a run report."""
    from devforge.core.config_loader import ConfigError, load_config

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    runs_dir = Path(cfg.project.root) / ".orchestrator" / "runs"
    if not runs_dir.exists():
        typer.echo("No runs found.")
        raise typer.Exit(code=0)

    target: Path | None = None
    if latest:
        runs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
        target = runs[-1] if runs else None
    elif run_id:
        target = runs_dir / run_id
    else:
        typer.echo("Provide --run <id> or --latest", err=True)
        raise typer.Exit(code=2)

    if target is None or not target.exists():
        typer.echo(f"Run not found: {target}", err=True)
        raise typer.Exit(code=2)

    final_md = target / "final_report.md"
    decision = target / "decision.json"
    if fmt == "json" and decision.exists():
        typer.echo(decision.read_text(encoding="utf-8"))
    elif final_md.exists():
        typer.echo(final_md.read_text(encoding="utf-8"))
    else:
        typer.echo(f"Run {target.name} has not produced a report yet.")


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
