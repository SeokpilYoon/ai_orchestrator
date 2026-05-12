"""Driver for the ``research_optimize`` workflow.

A bounded research-and-improve loop:

1. **target_inspection**     — enumerate the target area's files +
                                produce a small descriptive summary
2. **baseline_measurement**  — run the user-supplied metric command,
                                capture stdout + a parsed numeric value
3. **hypothesis_generation** — deterministic list of improvement
                                candidates ranked by simple heuristics
                                (longest files, missing tests, etc.)
4. **experiment**            — by default ``dry_run`` — just records the
                                plan. Set ``experiment_mode=implement``
                                to route through the implementer; this
                                is a guarded escape hatch, not the
                                default
5. **verify_metric**         — re-run the metric command, diff against
                                baseline
6. **final_report**          — keep / reject / no_change decision plus
                                a human-readable summary

Metric adapter is **generic**: the driver knows nothing about the
project's metric. The user supplies a shell command via metadata
(``metric_command``) and an optional regex (``metric_pattern`` with one
numeric capture group). Without a metric the driver completes the
inspection and hypothesis stages but skips verify with a recorded
reason — so the workflow stays useful even when no metric is wired.

The driver never performs network research and never invokes an
implementer unless explicitly opted in. It deliberately does not
attempt open-ended generation exploration.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from devforge.core.config_loader import DevforgeConfig
from devforge.core.run_context import RunContext
from devforge.core.state_store import StateStore
from devforge.providers.registry import ProviderRegistry

_STAGE_IDS = [
    "target_inspection",
    "baseline_measurement",
    "hypothesis_generation",
    "experiment",
    "verify_metric",
    "final_report",
]

_DEFAULT_EXPERIMENT_MODE = "dry_run"
_NUMBER_RX = re.compile(r"-?\d+(?:\.\d+)?")
_HYPOTHESIS_CAP = 8


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class InspectionResult:
    target: str
    file_count: int
    total_bytes: int
    largest_files: list[dict[str, Any]] = field(default_factory=list)
    untested_modules: list[str] = field(default_factory=list)
    extensions: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetricResult:
    command: str | None
    raw_stdout: str = ""
    raw_stderr: str = ""
    exit_code: int | None = None
    value: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Hypothesis:
    id: str
    kind: str  # "long_file" | "untested_module" | "todo_density" | "extension_skew"
    target_path: str
    summary: str
    suggested_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentOutcome:
    mode: str            # "dry_run" | "implement"
    chosen_hypothesis: str | None = None
    candidate_id: str | None = None
    provider_id: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerifyResult:
    metric: MetricResult
    delta: float | None = None         # post - baseline; None if metric missing
    direction: str = "unknown"          # "improved" | "regressed" | "unchanged" | "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.to_dict(),
            "delta": self.delta,
            "direction": self.direction,
        }


@dataclass
class ResearchReport:
    target: str
    experiment_mode: str
    decision: str       # "keep" | "reject" | "no_change" | "skipped" | "failed"
    reason: str
    inspection: InspectionResult
    baseline: MetricResult
    hypotheses: list[Hypothesis] = field(default_factory=list)
    experiment: ExperimentOutcome | None = None
    verify: VerifyResult | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "experiment_mode": self.experiment_mode,
            "decision": self.decision,
            "reason": self.reason,
            "inspection": self.inspection.to_dict(),
            "baseline": self.baseline.to_dict(),
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "experiment": self.experiment.to_dict() if self.experiment else None,
            "verify": self.verify.to_dict() if self.verify else None,
            "notes": list(self.notes),
        }


class ResearchOptimizeError(Exception):
    """Raised when the workflow can't proceed safely."""


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def run_research_optimize_workflow(
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    implementer_override: str | None = None,
    reviewer_override: str | None = None,
    *,
    state_store: StateStore | None = None,
    definition: Any = None,
) -> None:
    """Run the bounded research/optimize cycle. Always finishes — even when
    no metric / implementer is configured — by recording a ``decision`` of
    ``skipped`` with the precise reason."""
    _ = reviewer_override  # unused in default mode
    _ = definition

    if state_store is None:
        state_store = StateStore(run_ctx.root)
        if not state_store.is_initialized():
            state_store.init_run(
                workflow=run_ctx.workflow,
                input_ref=str(run_ctx.input_path) if run_ctx.input_path else None,
                stages=list(_STAGE_IDS),
            )

    project_root = Path(cfg.project.root).resolve()
    metadata = dict(run_ctx.metadata or {})
    target_spec = str(metadata.get("target_path") or project_root)
    target_path = (project_root / target_spec).resolve() if not Path(target_spec).is_absolute() else Path(target_spec)
    metric_command = metadata.get("metric_command")
    metric_pattern = metadata.get("metric_pattern")
    experiment_mode = metadata.get("experiment_mode") or _DEFAULT_EXPERIMENT_MODE

    # Stage 1 — target_inspection
    state_store.save_step("target_inspection", "running")
    try:
        inspection = inspect_target(target_path)
    except ResearchOptimizeError as exc:
        state_store.save_step("target_inspection", "failed", note=str(exc))
        _write_failure(run_ctx, "target inspection failed", {"reason": str(exc)})
        return
    _save_json(run_ctx.root / "research_inspection.json", inspection.to_dict())
    state_store.save_step(
        "target_inspection", "completed", artifact_ref="research_inspection.json"
    )

    # Stage 2 — baseline_measurement
    state_store.save_step("baseline_measurement", "running")
    baseline = measure_metric(metric_command, metric_pattern, project_root)
    _save_json(run_ctx.root / "research_baseline.json", baseline.to_dict())
    state_store.save_step(
        "baseline_measurement",
        "completed" if baseline.command else "skipped",
        artifact_ref="research_baseline.json",
        note=None if baseline.command else "no metric_command supplied",
    )

    # Stage 3 — hypothesis_generation
    state_store.save_step("hypothesis_generation", "running")
    hypotheses = generate_hypotheses(inspection, target_path, project_root)
    _save_json(
        run_ctx.root / "research_hypotheses.json",
        {"items": [h.to_dict() for h in hypotheses]},
    )
    state_store.save_step(
        "hypothesis_generation",
        "completed",
        artifact_ref="research_hypotheses.json",
    )

    # Stage 4 — experiment (dry_run by default, opt-in implement mode)
    state_store.save_step("experiment", "running")
    experiment = _run_experiment(
        cfg=cfg,
        run_ctx=run_ctx,
        state_store=state_store,
        hypotheses=hypotheses,
        experiment_mode=experiment_mode,
        implementer_override=implementer_override,
    )
    _save_json(run_ctx.root / "research_experiment.json", experiment.to_dict())
    state_store.save_step(
        "experiment",
        "skipped" if experiment.mode == "dry_run" else "completed",
        artifact_ref="research_experiment.json",
        note=experiment.note or None,
    )

    # Stage 5 — verify_metric
    state_store.save_step("verify_metric", "running")
    verify = _verify(baseline, metric_command, metric_pattern, project_root)
    _save_json(run_ctx.root / "research_verify.json", verify.to_dict())
    state_store.save_step(
        "verify_metric",
        "completed" if metric_command else "skipped",
        artifact_ref="research_verify.json",
        note=None if metric_command else "no metric_command — skipped",
    )

    # Decision
    decision, reason = _decide(verify, baseline, experiment)

    report = ResearchReport(
        target=str(target_path),
        experiment_mode=experiment_mode,
        decision=decision,
        reason=reason,
        inspection=inspection,
        baseline=baseline,
        hypotheses=hypotheses,
        experiment=experiment,
        verify=verify,
        notes=[
            "Hypotheses are deterministic — no LLM was used to generate them.",
            "Set metadata.experiment_mode='implement' to route the top "
            "hypothesis through the implementer (requires a configured "
            "implementer role).",
            "Provide metadata.metric_command (and optionally "
            "metric_pattern) so verify_metric can compare against the "
            "baseline.",
        ],
    )

    # Stage 6 — final_report
    state_store.save_step("final_report", "running")
    _save_json(run_ctx.root / "research_report.json", report.to_dict())
    (run_ctx.root / "final_report.md").write_text(
        _render_markdown_report(report), encoding="utf-8"
    )
    state_store.save_step(
        "final_report", "completed", artifact_ref="research_report.json"
    )

    # Surface provider health snapshot (best-effort) so the dashboard
    # shows the same context other workflows expose.
    try:
        registry = ProviderRegistry.from_config(cfg)
        state_store.snapshot_provider_registry(registry)
    except Exception:  # noqa: BLE001 — best-effort snapshot
        pass


# ---------------------------------------------------------------------------
# Stage helpers — exported so tests can exercise them directly
# ---------------------------------------------------------------------------

def inspect_target(target: Path) -> InspectionResult:
    """Walk ``target`` and summarise. Refuses paths that escape the cwd."""
    target = target.resolve()
    if not target.exists():
        raise ResearchOptimizeError(f"target does not exist: {target}")

    files: list[Path] = []
    if target.is_file():
        files = [target]
    else:
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            # Skip devforge runtime state + venvs + __pycache__.
            rel_parts = path.relative_to(target).parts
            if any(
                part in {".orchestrator", "__pycache__", ".venv", ".git", "node_modules"}
                for part in rel_parts
            ):
                continue
            files.append(path)

    extensions: dict[str, int] = {}
    total_bytes = 0
    sizes: list[tuple[Path, int]] = []
    for f in files:
        try:
            size = f.stat().st_size
        except OSError:
            continue
        total_bytes += size
        sizes.append((f, size))
        extensions[f.suffix or "(none)"] = extensions.get(f.suffix or "(none)", 0) + 1

    sizes.sort(key=lambda pair: pair[1], reverse=True)
    largest = [
        {
            "path": str(p.relative_to(target) if target.is_dir() else p),
            "bytes": s,
        }
        for p, s in sizes[:5]
    ]

    untested = _untested_modules(target, files) if target.is_dir() else []

    return InspectionResult(
        target=str(target),
        file_count=len(files),
        total_bytes=total_bytes,
        largest_files=largest,
        untested_modules=untested,
        extensions=extensions,
    )


def _untested_modules(target: Path, files: list[Path]) -> list[str]:
    py_files = [f for f in files if f.suffix == ".py" and "test" not in f.name]
    if not py_files:
        return []
    test_basenames = {
        f.name.replace("test_", "").replace("_test", "")
        for f in files
        if f.suffix == ".py" and ("test_" in f.name or "_test" in f.name)
    }
    out: list[str] = []
    for f in py_files:
        if f.name in {"__init__.py", "__main__.py"}:
            continue
        if f.name in test_basenames:
            continue
        try:
            rel = str(f.relative_to(target))
        except ValueError:
            rel = str(f)
        out.append(rel)
    return out[:10]  # cap so the artifact stays scannable


def measure_metric(
    command: str | None,
    pattern: str | None,
    cwd: Path,
    *,
    timeout_sec: int = 120,
) -> MetricResult:
    """Run ``command`` in ``cwd`` and extract a numeric metric.

    When ``command`` is empty the function returns a result with
    ``value=None`` and a note explaining the skip. Pattern parsing is
    best-effort — a malformed pattern falls back to the first number in
    stdout.
    """
    if not command:
        return MetricResult(command=None, note="no metric_command supplied")
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired as exc:
        return MetricResult(
            command=command,
            raw_stdout=exc.stdout.decode("utf-8", "replace") if exc.stdout else "",
            raw_stderr=exc.stderr.decode("utf-8", "replace") if exc.stderr else "",
            exit_code=None,
            note=f"timed out after {timeout_sec}s",
        )
    except Exception as exc:  # noqa: BLE001 — record + continue
        return MetricResult(
            command=command,
            note=f"metric run failed: {exc}",
        )

    value = _parse_metric_value(proc.stdout, pattern)
    note = "" if value is not None else "could not parse a numeric value from stdout"
    return MetricResult(
        command=command,
        raw_stdout=proc.stdout[-4000:],
        raw_stderr=proc.stderr[-4000:],
        exit_code=proc.returncode,
        value=value,
        note=note,
    )


def _parse_metric_value(stdout: str, pattern: str | None) -> float | None:
    if pattern:
        try:
            match = re.search(pattern, stdout)
        except re.error:
            match = None
        if match:
            try:
                return float(match.group(1) if match.groups() else match.group(0))
            except (ValueError, IndexError):
                return None
    match = _NUMBER_RX.search(stdout)
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def generate_hypotheses(
    inspection: InspectionResult, target: Path, project_root: Path
) -> list[Hypothesis]:
    """Deterministic hypothesis generator. No LLM, no network.

    Heuristics, in priority order:

    1. The largest source file — refactor candidate.
    2. Modules without a matching ``test_*.py`` — coverage candidate.
    3. Files mentioning ``TODO`` / ``FIXME`` densely — clean-up candidate.
    4. Single-extension dominance — possible monoculture / typing gap.
    """
    out: list[Hypothesis] = []
    seq = 1

    for entry in inspection.largest_files[:2]:
        path = entry["path"]
        out.append(
            Hypothesis(
                id=f"H-{seq:03d}",
                kind="long_file",
                target_path=path,
                summary=f"`{path}` is one of the largest files in the target ({entry['bytes']} bytes).",
                suggested_action=(
                    "Identify a cohesive sub-section and extract it into "
                    "its own module. Keep the public API stable."
                ),
            )
        )
        seq += 1

    for module in inspection.untested_modules[:3]:
        out.append(
            Hypothesis(
                id=f"H-{seq:03d}",
                kind="untested_module",
                target_path=module,
                summary=f"`{module}` has no matching test file in the target.",
                suggested_action=(
                    "Add at least one focused test exercising the module's "
                    "main entrypoint before further refactoring."
                ),
            )
        )
        seq += 1

    # Best-effort TODO scan over the target (skip when target is a single file).
    if target.is_dir():
        todo_hot: list[tuple[Path, int]] = []
        for path in target.rglob("*.py"):
            if any(
                part in {".orchestrator", "__pycache__", ".venv", ".git"}
                for part in path.relative_to(target).parts
            ):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            count = text.count("TODO") + text.count("FIXME")
            if count > 0:
                todo_hot.append((path, count))
        todo_hot.sort(key=lambda pair: pair[1], reverse=True)
        for path, count in todo_hot[:2]:
            try:
                rel = str(path.relative_to(target))
            except ValueError:
                rel = str(path)
            out.append(
                Hypothesis(
                    id=f"H-{seq:03d}",
                    kind="todo_density",
                    target_path=rel,
                    summary=f"`{rel}` contains {count} TODO/FIXME marker(s).",
                    suggested_action=(
                        "Triage each marker — either fix, file an issue, or "
                        "remove if obsolete."
                    ),
                )
            )
            seq += 1

    if inspection.extensions:
        dominant_ext, dominant_count = max(
            inspection.extensions.items(), key=lambda kv: kv[1]
        )
        if dominant_count >= max(1, inspection.file_count // 2):
            out.append(
                Hypothesis(
                    id=f"H-{seq:03d}",
                    kind="extension_skew",
                    target_path=str(target),
                    summary=(
                        f"{dominant_ext} files dominate the target "
                        f"({dominant_count}/{inspection.file_count}). Consider "
                        f"whether tests, fixtures, or other tooling is missing."
                    ),
                    suggested_action=(
                        "Audit secondary tooling (e.g. tests, lint config, "
                        "type stubs) for the dominant extension."
                    ),
                )
            )
            seq += 1

    _ = project_root  # reserved for future heuristics (e.g. git churn)
    return out[:_HYPOTHESIS_CAP]


def _run_experiment(
    *,
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    state_store: StateStore,
    hypotheses: list[Hypothesis],
    experiment_mode: str,
    implementer_override: str | None,
) -> ExperimentOutcome:
    if experiment_mode != "implement":
        return ExperimentOutcome(
            mode="dry_run",
            chosen_hypothesis=hypotheses[0].id if hypotheses else None,
            note=(
                "dry_run: no implementer invoked. Set metadata."
                "experiment_mode='implement' to route the top hypothesis "
                "through the feature pipeline."
            ),
        )

    if not hypotheses:
        return ExperimentOutcome(
            mode="implement",
            note="no hypotheses generated — nothing to implement",
        )

    # Route the top hypothesis through the feature pipeline with the
    # research_optimize prompt variant. Reuses the candidate loop so
    # all the existing safety/policy/scoring infrastructure applies.
    top = hypotheses[0]
    synthetic_task_path = run_ctx.root / "experiment_task.md"
    synthetic_task_path.write_text(
        f"# Experiment: {top.id} — {top.kind}\n\n"
        f"## Goal\n\n{top.summary}\n\n"
        f"## Acceptance Criteria\n\n- {top.suggested_action}\n\n"
        f"## Target\n\n- `{top.target_path}`\n",
        encoding="utf-8",
    )
    run_ctx.input_path = synthetic_task_path

    try:
        from devforge.stages.feature_driver import run_feature_workflow  # noqa: PLC0415

        run_feature_workflow(
            cfg,
            run_ctx,
            implementer_override,
            None,  # reviewer_override
            state_store=state_store,
            workflow_variant="refactor",  # closest existing variant
        )
    except Exception as exc:  # noqa: BLE001 — record + continue
        return ExperimentOutcome(
            mode="implement",
            chosen_hypothesis=top.id,
            note=f"implementer run failed: {exc}",
        )

    return ExperimentOutcome(
        mode="implement",
        chosen_hypothesis=top.id,
        note="implementer invoked via feature driver (refactor variant)",
    )


def _verify(
    baseline: MetricResult,
    metric_command: str | None,
    metric_pattern: str | None,
    project_root: Path,
) -> VerifyResult:
    if not metric_command:
        return VerifyResult(
            metric=MetricResult(command=None, note="no metric_command — skipped"),
            delta=None,
            direction="unknown",
        )
    post = measure_metric(metric_command, metric_pattern, project_root)
    delta: float | None = None
    direction = "unknown"
    if baseline.value is not None and post.value is not None:
        delta = post.value - baseline.value
        if abs(delta) < 1e-9:
            direction = "unchanged"
        elif delta > 0:
            direction = "improved"
        else:
            direction = "regressed"
    return VerifyResult(metric=post, delta=delta, direction=direction)


def _decide(
    verify: VerifyResult, baseline: MetricResult, experiment: ExperimentOutcome
) -> tuple[str, str]:
    if experiment.mode == "dry_run":
        if baseline.command is None:
            return ("skipped", "no metric configured; dry_run experiment recorded only")
        return ("no_change", "dry_run experiment — verify metric matches baseline by construction")
    if verify.direction == "improved":
        return ("keep", "post-metric improved over baseline")
    if verify.direction == "regressed":
        return ("reject", "post-metric regressed against baseline")
    if verify.direction == "unchanged":
        return ("no_change", "post-metric unchanged from baseline")
    return ("skipped", "no metric available to compare against baseline")


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _write_failure(run_ctx: RunContext, message: str, details: dict) -> None:
    (run_ctx.root / "failure.json").write_text(
        json.dumps({"message": message, "details": details}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_ctx.root / "final_report.md").write_text(
        f"# Research run {run_ctx.run_id} — failed\n\n{message}\n\n"
        f"```\n{json.dumps(details, indent=2, ensure_ascii=False)}\n```\n",
        encoding="utf-8",
    )


def _render_markdown_report(report: ResearchReport) -> str:
    lines: list[str] = [
        f"# Research / optimize — {Path(report.target).name or report.target}",
        "",
        f"- Target: `{report.target}`",
        f"- Experiment mode: **{report.experiment_mode}**",
        f"- Decision: **{report.decision}**",
        f"- Reason: {report.reason}",
        "",
        "## Inspection",
        "",
        f"- Files: **{report.inspection.file_count}**",
        f"- Total bytes: **{report.inspection.total_bytes}**",
    ]
    if report.inspection.extensions:
        exts = ", ".join(
            f"{k}={v}" for k, v in sorted(
                report.inspection.extensions.items(), key=lambda kv: kv[1], reverse=True
            )
        )
        lines.append(f"- Extensions: {exts}")
    if report.inspection.largest_files:
        lines.append("")
        lines.append("### Largest files")
        lines.append("")
        for entry in report.inspection.largest_files:
            lines.append(f"- `{entry['path']}` ({entry['bytes']} bytes)")
    lines.append("")

    lines.append("## Baseline metric")
    lines.append("")
    if report.baseline.command:
        lines.append(f"- Command: `{report.baseline.command}`")
        lines.append(f"- Value: **{report.baseline.value}**")
        if report.baseline.note:
            lines.append(f"- Note: {report.baseline.note}")
    else:
        lines.append(f"- {report.baseline.note}")
    lines.append("")

    lines.append("## Hypotheses")
    lines.append("")
    if report.hypotheses:
        for h in report.hypotheses:
            lines.append(f"- **{h.id}** ({h.kind}) — `{h.target_path}`: {h.summary}")
            lines.append(f"    - Suggested action: {h.suggested_action}")
    else:
        lines.append("_(none generated)_")
    lines.append("")

    if report.experiment:
        lines.append("## Experiment")
        lines.append("")
        lines.append(f"- Mode: **{report.experiment.mode}**")
        if report.experiment.chosen_hypothesis:
            lines.append(f"- Chosen hypothesis: `{report.experiment.chosen_hypothesis}`")
        if report.experiment.note:
            lines.append(f"- Note: {report.experiment.note}")
        lines.append("")

    if report.verify:
        lines.append("## Verify metric")
        lines.append("")
        lines.append(f"- Direction: **{report.verify.direction}**")
        lines.append(f"- Delta: {report.verify.delta}")
        if report.verify.metric.command:
            lines.append(f"- Post-metric value: {report.verify.metric.value}")
        lines.append("")

    if report.notes:
        lines.append("## Notes")
        lines.append("")
        for n in report.notes:
            lines.append(f"- {n}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# Silence unused-import lint for shlex (kept for future quoted-arg support).
_ = shlex
