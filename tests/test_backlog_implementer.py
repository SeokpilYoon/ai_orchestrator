"""Unit + integration coverage for the backlog implementer (DEVF-069)."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from devforge.core.config_loader import (
    AcceptCondition,
    CommandPolicy,
    DevforgeConfig,
    FilePolicy,
    ProjectConfig,
    ProviderConfig,
    RoleConfig,
    ScoringConfig,
    StopConditions,
    ValidationCommands,
    ValidationConfig,
)
from devforge.core.run_context import create_run_context
from devforge.providers.base import AgentResult
from devforge.providers.mock import MockProvider
from devforge.providers.registry import ProviderRegistry
from devforge.stages.architecture_generator import (
    ApiOperation,
    Architecture,
    Entity,
)
from devforge.stages.backlog_generator import Backlog, BacklogItem
from devforge.stages.backlog_implementer import (
    BacklogProgress,
    BacklogProgressItem,
    run_backlog_implementer,
    save_backlog_progress,
)
from devforge.stages.scaffold_generator import ScaffoldFile, ScaffoldManifest
from devforge.stages.vertical_slice_implementer import (
    VerticalSliceImplementerResult,
)
from devforge.stages.vertical_slice_planner import VerticalSlicePlan

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

def _commit_in(cwd: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, capture_output=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=cwd, check=False
    )
    if staged.returncode == 0:
        return
    subprocess.run(
        ["git", "commit", "-m", "task"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _seed_scaffold(scaffold: Path) -> None:
    scaffold.mkdir(parents=True)
    (scaffold / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (scaffold / "app").mkdir()
    (scaffold / "app" / "__init__.py").write_text("")
    (scaffold / "app" / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )
    (scaffold / "tests").mkdir()
    (scaffold / "tests" / "__init__.py").write_text("")


def _arch() -> Architecture:
    return Architecture(
        stack="python-fastapi-only",
        supported_stack=True,
        runtime="Python 3.11+",
        framework="FastAPI",
        test_command="pytest -q",
        entities=[Entity(name="Task", fields={"id": "integer"}, sourced_from=["FR-001"])],
        operations=[
            ApiOperation(method="POST", path="/tasks", summary="add",
                         requirement_ids=["FR-001"]),
        ],
    )


def _manifest(*, supported: bool = True, smoke: bool = True) -> ScaffoldManifest:
    return ScaffoldManifest(
        stack="python-fastapi-only",
        supported=supported,
        scaffold_root="scaffold",
        files=[
            ScaffoldFile(path="app/__init__.py", bytes=0, sha256="aaa"),
            ScaffoldFile(path="app/main.py", bytes=64, sha256="bbb"),
        ],
        import_smoke_passed=smoke,
        test_command="pytest -q",
        project_name="todo",
        entities=["Task"],
    )


def _backlog(items: list[BacklogItem]) -> Backlog:
    return Backlog(items=items)


def _item(
    idx: int,
    *,
    priority: str = "P0",
    fr_id: str | None = None,
    deps: list[str] | None = None,
    acs: list[str] | None = None,
) -> BacklogItem:
    return BacklogItem(
        id=f"TASK-{idx:03d}",
        title=f"Task {idx}",
        requirement_ids=[fr_id or f"FR-{idx:03d}"],
        acceptance_criteria=acs or [f"does step {idx}"],
        priority=priority,
        estimated_complexity="S",
        dependencies=deps or [],
    )


def _cfg(scaffold: Path) -> DevforgeConfig:
    return DevforgeConfig(
        project=ProjectConfig(
            name="todo",
            root=str(scaffold),
            default_branch="main",
            worktree_root=str(scaffold.parent / "scaffold_worktrees"),
        ),
        providers={
            "mock_impl": ProviderConfig(type="mock", enabled=True),
            "mock_review": ProviderConfig(type="mock", enabled=True),
            "local_rule_based": ProviderConfig(type="local_rule_based", enabled=True),
        },
        roles={
            "implementer": RoleConfig(provider_order=["mock_impl"]),
            "reviewer": RoleConfig(
                provider_order=["mock_review"],
                avoid_same_provider_as_implementer=True,
            ),
            "judge": RoleConfig(provider_order=["local_rule_based"]),
        },
        validation=ValidationConfig(
            commands=ValidationCommands(), default_timeout_sec=10
        ),
        file_policy=FilePolicy(allowed_paths=["src/**"], blocked_paths=[".env"]),
        command_policy=CommandPolicy(),
        scoring=ScoringConfig(),
        stop_conditions=StopConditions(accept_when=AcceptCondition(min_score=70)),
    )


def _review_pass() -> Callable:
    payload = json.dumps(
        {
            "verdict": "pass",
            "requirement_coverage": 1.0,
            "critical_issues": [],
            "major_issues": [],
            "minor_issues": [],
            "test_concerns": [],
            "security_concerns": [],
            "recommended_revision_prompt": "",
        }
    )

    def _behave(request):  # noqa: ARG001
        return AgentResult(
            provider_id="mock_review",
            role="reviewer",
            success=True,
            stdout=payload,
            exit_code=0,
        )

    return _behave


def _impl_writes_per_task() -> Callable:
    """Mock impl that writes a per-task file derived from candidate_id."""

    def _behave(request):
        candidate_id = (request.metadata or {}).get("candidate_id", "anon")
        rel = f"app/services/{candidate_id.lower().replace('-', '_')}.py"
        target = Path(request.cwd) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f'"""mock {candidate_id}"""\ndef run(): return "{candidate_id}"\n'
        )
        _commit_in(Path(request.cwd))
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout=f"wrote {rel}",
            changed_files=[rel],
            exit_code=0,
        )

    return _behave


def _install_registry(
    monkeypatch: pytest.MonkeyPatch,
    *,
    impl_behavior: Callable | None = None,
    review_behavior: Callable | None = None,
) -> None:
    impl = impl_behavior or _impl_writes_per_task()
    review = review_behavior or _review_pass()

    def patched(_cfg: DevforgeConfig) -> ProviderRegistry:
        reg = ProviderRegistry()
        reg.register(MockProvider("mock_impl", behavior=impl))
        reg.register(MockProvider("mock_review", behavior=review))
        from devforge.providers.local_rule_based import LocalRuleBasedProvider
        reg.register(
            LocalRuleBasedProvider(
                "local_rule_based", ProviderConfig(type="local_rule_based")
            )
        )
        return reg

    monkeypatch.setattr(ProviderRegistry, "from_config", staticmethod(patched))


def _setup_run(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    run_ctx = create_run_context(project_root, workflow="app_from_prd", input_path=None)
    scaffold = run_ctx.root / "scaffold"
    _seed_scaffold(scaffold)
    return run_ctx, scaffold


# ---------------------------------------------------------------------------
# Whole-stage skip gates
# ---------------------------------------------------------------------------

def test_unsupported_scaffold_skips_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ctx, scaffold = _setup_run(tmp_path)
    cfg = _cfg(scaffold)
    _install_registry(monkeypatch)

    backlog = _backlog([_item(1)])
    progress = run_backlog_implementer(
        cfg, run_ctx,
        backlog=backlog,
        slice_plan=None, slice_result=None,
        arch=_arch(),
        scaffold_manifest=_manifest(supported=False),
    )
    assert progress.decision == "skipped"
    assert "not supported" in progress.reason
    assert all(it.status == "skipped" for it in progress.items)


def test_failed_smoke_skips_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ctx, scaffold = _setup_run(tmp_path)
    cfg = _cfg(scaffold)
    _install_registry(monkeypatch)

    progress = run_backlog_implementer(
        cfg, run_ctx,
        backlog=_backlog([_item(1)]),
        slice_plan=None, slice_result=None,
        arch=_arch(),
        scaffold_manifest=_manifest(smoke=False),
    )
    assert progress.decision == "skipped"
    assert "py_compile" in progress.reason


def test_empty_backlog_skips_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ctx, scaffold = _setup_run(tmp_path)
    cfg = _cfg(scaffold)
    _install_registry(monkeypatch)

    progress = run_backlog_implementer(
        cfg, run_ctx,
        backlog=_backlog([]),
        slice_plan=None, slice_result=None,
        arch=_arch(),
        scaffold_manifest=_manifest(),
    )
    assert progress.decision == "skipped"
    assert "empty" in progress.reason


def test_no_implementer_role_skips_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ctx, scaffold = _setup_run(tmp_path)
    cfg = _cfg(scaffold)
    cfg.roles.pop("implementer", None)
    _install_registry(monkeypatch)

    progress = run_backlog_implementer(
        cfg, run_ctx,
        backlog=_backlog([_item(1)]),
        slice_plan=None, slice_result=None,
        arch=_arch(),
        scaffold_manifest=_manifest(),
    )
    assert progress.decision == "skipped"
    assert "implementer" in progress.reason


# ---------------------------------------------------------------------------
# Per-item skip rules
# ---------------------------------------------------------------------------

def test_already_in_slice_skips_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ctx, scaffold = _setup_run(tmp_path)
    cfg = _cfg(scaffold)
    _install_registry(monkeypatch)

    backlog = _backlog([_item(1, fr_id="FR-001"), _item(2, fr_id="FR-002")])
    slice_plan = VerticalSlicePlan(
        vertical_slice_name="x",
        requirement_ids=["FR-001"],
        acceptance_criteria=["doesnt matter"],
    )
    slice_result = VerticalSliceImplementerResult(
        decision="accept",
        candidate_id="mock_impl",
        provider_id="mock_impl",
    )
    progress = run_backlog_implementer(
        cfg, run_ctx,
        backlog=backlog,
        slice_plan=slice_plan,
        slice_result=slice_result,
        arch=_arch(),
        scaffold_manifest=_manifest(),
    )
    by_task = {it.task_id: it for it in progress.items}
    assert by_task["TASK-001"].status == "already_in_slice"
    assert by_task["TASK-002"].status == "accept"


def test_dependency_failed_cascades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ctx, scaffold = _setup_run(tmp_path)
    cfg = _cfg(scaffold)

    def failing_impl(request):
        candidate_id = (request.metadata or {}).get("candidate_id", "")
        if candidate_id == "TASK-001":
            # Producer fails completely.
            return AgentResult(
                provider_id="mock_impl",
                role="implementer",
                success=False,
                stderr="boom",
                error="boom",
                failure_class="unknown",
                exit_code=1,
            )
        # Consumer would succeed if it got there, but it never should.
        return _impl_writes_per_task()(request)

    _install_registry(monkeypatch, impl_behavior=failing_impl)

    backlog = _backlog([
        _item(1, fr_id="FR-001"),
        _item(2, fr_id="FR-002", deps=["TASK-001"]),
    ])
    progress = run_backlog_implementer(
        cfg, run_ctx,
        backlog=backlog,
        slice_plan=None, slice_result=None,
        arch=_arch(),
        scaffold_manifest=_manifest(),
    )
    by_task = {it.task_id: it for it in progress.items}
    # Producer is discarded (implementer failed → failure_summary discard).
    assert by_task["TASK-001"].status in {"failed", "discard"}
    # Consumer must have cascaded to dependency_failed.
    assert by_task["TASK-002"].status == "dependency_failed"
    assert "TASK-001" in by_task["TASK-002"].reason


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_two_accepted_items_sync_files_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ctx, scaffold = _setup_run(tmp_path)
    cfg = _cfg(scaffold)
    _install_registry(monkeypatch)

    backlog = _backlog([
        _item(1, fr_id="FR-001"),
        _item(2, fr_id="FR-002"),
    ])
    progress = run_backlog_implementer(
        cfg, run_ctx,
        backlog=backlog,
        slice_plan=None, slice_result=None,
        arch=_arch(),
        scaffold_manifest=_manifest(),
    )
    assert progress.decision == "completed"
    assert progress.accepted_count == 2
    assert progress.acceptance_coverage == pytest.approx(1.0)
    for rel in ("app/services/task_001.py", "app/services/task_002.py"):
        assert (scaffold / rel).exists()
    # Two backlog commits + the initial commit in scaffold.
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=scaffold, capture_output=True, text=True, check=True,
    )
    assert log.stdout.count("backlog: accept") == 2


def test_coverage_partial_when_some_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ctx, scaffold = _setup_run(tmp_path)
    cfg = _cfg(scaffold)
    _install_registry(monkeypatch)

    backlog = _backlog([
        _item(1, fr_id="FR-001", acs=["a"]),
        _item(2, fr_id="FR-002", acs=["b", "c"]),
    ])
    slice_plan = VerticalSlicePlan(
        vertical_slice_name="x",
        requirement_ids=["FR-001"],
        acceptance_criteria=["x"],
    )
    slice_result = VerticalSliceImplementerResult(decision="accept")
    progress = run_backlog_implementer(
        cfg, run_ctx,
        backlog=backlog,
        slice_plan=slice_plan,
        slice_result=slice_result,
        arch=_arch(),
        scaffold_manifest=_manifest(),
    )
    # TASK-001 already_in_slice (skipped, 0 accepted ACs counted here).
    # TASK-002 accept (2 accepted ACs).
    # Total ACs counted from backlog items: 1 + 2 = 3. Accepted: 2 → 0.6667
    assert progress.accepted_count == 1
    assert progress.acceptance_coverage == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_to_dict_keys_match_spec() -> None:
    progress = BacklogProgress(
        items=[BacklogProgressItem(task_id="TASK-001", status="accept")],
        total_count=1,
        accepted_count=1,
        acceptance_coverage=1.0,
    )
    payload = progress.to_dict()
    for key in (
        "decision",
        "reason",
        "accepted_count",
        "total_count",
        "acceptance_coverage",
        "items",
        "notes",
    ):
        assert key in payload
    assert payload["items"][0]["task_id"] == "TASK-001"


def test_save_backlog_progress_writes_valid_json(tmp_path: Path) -> None:
    progress = BacklogProgress(
        items=[BacklogProgressItem(task_id="TASK-001", status="accept")],
        total_count=1, accepted_count=1, acceptance_coverage=1.0,
    )
    out = tmp_path / "backlog_progress.json"
    save_backlog_progress(progress, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["items"][0]["task_id"] == "TASK-001"
    assert payload["acceptance_coverage"] == 1.0
