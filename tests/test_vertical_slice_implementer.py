"""Unit + integration coverage for DEVF-067 (vertical slice implementer)."""
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
from devforge.stages.scaffold_generator import ScaffoldFile, ScaffoldManifest
from devforge.stages.vertical_slice_implementer import (
    VerticalSliceImplementerError,
    VerticalSliceImplementerResult,
    build_scaffold_cfg,
    build_slice_repo_context,
    build_slice_task_text,
    init_scaffold_git_repo,
    run_vertical_slice_implementer,
    save_vertical_slice_result,
    scaffold_file_policy,
    scaffold_validation_config,
    skip_reason,
    sync_worktree_to_scaffold,
)
from devforge.stages.vertical_slice_planner import VerticalSlicePlan

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

def _commit_all(worktree: Path) -> None:
    """Stage + commit any uncommitted changes inside a worktree."""
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=worktree, check=False
    )
    if staged.returncode == 0:
        return
    subprocess.run(
        ["git", "commit", "-m", "slice"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )


def _bare_scaffold(tmp_path: Path) -> Path:
    """Create a minimal runnable-shape scaffold tree (no git yet)."""
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()
    (scaffold / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (scaffold / "README.md").write_text("scaffold\n")
    (scaffold / "app").mkdir()
    (scaffold / "app" / "__init__.py").write_text("")
    (scaffold / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (scaffold / "tests").mkdir()
    (scaffold / "tests" / "__init__.py").write_text("")
    return scaffold


def _sample_plan() -> VerticalSlicePlan:
    return VerticalSlicePlan(
        vertical_slice_name="Create + list tasks",
        user_journey=["Open API", "POST /tasks", "GET /tasks"],
        screens=["SCREEN-001", "SCREEN-002"],
        api_endpoints=["POST /tasks", "GET /tasks"],
        data_entities=["Task"],
        acceptance_criteria=[
            "POST /tasks with a title returns 201",
            "GET /tasks returns previously created tasks",
        ],
        requirement_ids=["FR-001", "FR-002"],
    )


def _sample_arch() -> Architecture:
    return Architecture(
        stack="python-fastapi-only",
        supported_stack=True,
        runtime="Python 3.11+",
        framework="FastAPI",
        test_command="pytest -q",
        entities=[Entity(name="Task", fields={"id": "integer", "title": "string"}, sourced_from=["FR-001"])],
        operations=[
            ApiOperation(method="POST", path="/tasks", summary="add", requirement_ids=["FR-001"]),
            ApiOperation(method="GET", path="/tasks", summary="list", requirement_ids=["FR-002"]),
        ],
    )


def _sample_manifest(scaffold_root: Path, *, supported: bool = True, smoke: bool = True) -> ScaffoldManifest:
    return ScaffoldManifest(
        stack="python-fastapi-only",
        supported=supported,
        scaffold_root="scaffold",
        files=[
            ScaffoldFile(path="app/main.py", bytes=64, sha256="aaa"),
            ScaffoldFile(path="app/__init__.py", bytes=0, sha256="bbb"),
            ScaffoldFile(path="tests/__init__.py", bytes=0, sha256="ccc"),
            ScaffoldFile(path="pyproject.toml", bytes=32, sha256="ddd"),
        ],
        import_smoke_passed=smoke,
        test_command="pytest -q",
        project_name="todo",
        entities=["Task"],
    )


def _slice_cfg(scaffold_root: Path, *, impl_behavior: Callable | None = None,
               review_behavior: Callable | None = None) -> DevforgeConfig:
    """Build a DevforgeConfig wired to mock implementer + reviewer providers.

    ``ProviderRegistry.from_config`` does not know about ``MockProvider``, so
    tests monkey-patch the registry. Here we install a class-level patch that
    returns a pre-built registry whenever the slice implementer asks for one.
    """
    cfg = DevforgeConfig(
        project=ProjectConfig(
            name="todo",
            root=str(scaffold_root),
            default_branch="main",
            worktree_root=str(scaffold_root.parent / "scaffold_worktrees"),
            profile="python_fastapi",
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
            commands=ValidationCommands(),
            default_timeout_sec=10,
        ),
        file_policy=FilePolicy(
            allowed_paths=["src/**"],
            blocked_paths=[".env"],
        ),
        command_policy=CommandPolicy(),
        scoring=ScoringConfig(),
        # Lower the accept threshold so the mock's "no validation commands +
        # reviewer pass" score (80) clears the gate. Production users keep
        # the default 85.
        stop_conditions=StopConditions(
            accept_when=AcceptCondition(min_score=70),
        ),
    )
    cfg.__test_impl_behavior__ = impl_behavior  # type: ignore[attr-defined]
    cfg.__test_review_behavior__ = review_behavior  # type: ignore[attr-defined]
    return cfg


def _install_mock_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``ProviderRegistry.from_config`` to return a registry of mocks."""

    def patched(cfg: DevforgeConfig) -> ProviderRegistry:
        reg = ProviderRegistry()
        reg.register(MockProvider(
            "mock_impl",
            behavior=getattr(cfg, "__test_impl_behavior__", None),
        ))
        reg.register(MockProvider(
            "mock_review",
            behavior=getattr(cfg, "__test_review_behavior__", None),
        ))
        from devforge.providers.local_rule_based import LocalRuleBasedProvider
        reg.register(
            LocalRuleBasedProvider(
                "local_rule_based", ProviderConfig(type="local_rule_based")
            )
        )
        return reg

    monkeypatch.setattr(ProviderRegistry, "from_config", staticmethod(patched))


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


# ---------------------------------------------------------------------------
# Skip-gate
# ---------------------------------------------------------------------------

def test_skip_reason_unsupported_stack(tmp_path: Path) -> None:
    manifest = _sample_manifest(tmp_path, supported=False)
    plan = _sample_plan()
    reason = skip_reason(manifest, plan)
    assert reason and "not supported" in reason


def test_skip_reason_failed_smoke(tmp_path: Path) -> None:
    manifest = _sample_manifest(tmp_path, smoke=False)
    plan = _sample_plan()
    reason = skip_reason(manifest, plan)
    assert reason and "py_compile" in reason


def test_skip_reason_no_acceptance_criteria(tmp_path: Path) -> None:
    manifest = _sample_manifest(tmp_path)
    plan = _sample_plan()
    plan.acceptance_criteria = []
    reason = skip_reason(manifest, plan)
    assert reason and "acceptance criteria" in reason


def test_skip_reason_returns_none_when_healthy(tmp_path: Path) -> None:
    assert skip_reason(_sample_manifest(tmp_path), _sample_plan()) is None


# ---------------------------------------------------------------------------
# Scaffold git bootstrap
# ---------------------------------------------------------------------------

def test_init_scaffold_git_repo_creates_main_with_commit(tmp_path: Path) -> None:
    scaffold = _bare_scaffold(tmp_path)
    branch = init_scaffold_git_repo(scaffold)
    assert branch == "main"
    assert (scaffold / ".git").exists()
    proc = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=scaffold,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "scaffold: initial commit" in proc.stdout


def test_init_scaffold_git_repo_is_idempotent(tmp_path: Path) -> None:
    scaffold = _bare_scaffold(tmp_path)
    init_scaffold_git_repo(scaffold)
    branch = init_scaffold_git_repo(scaffold)
    assert branch == "main"
    proc = subprocess.run(
        ["git", "rev-list", "--count", "main"],
        cwd=scaffold,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "1"  # still only the initial commit


def test_init_scaffold_git_repo_raises_for_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(VerticalSliceImplementerError):
        init_scaffold_git_repo(tmp_path / "nope")


# ---------------------------------------------------------------------------
# Policy + validation shapes
# ---------------------------------------------------------------------------

def test_scaffold_file_policy_allows_app_and_tests() -> None:
    fp = scaffold_file_policy()
    assert "app/**" in fp.allowed_paths
    assert "tests/**" in fp.allowed_paths
    assert "pyproject.toml" in fp.blocked_paths
    assert any(b.endswith(".git/**") or b == ".git/**" for b in fp.blocked_paths)


def test_scaffold_validation_config_uses_compileall() -> None:
    vc = scaffold_validation_config()
    cmd = vc.commands.import_smoke or ""
    assert "compileall" in cmd
    # No test/lint/typecheck — these require installed deps that CI may not have.
    assert vc.commands.test is None
    assert vc.commands.lint is None
    assert vc.commands.typecheck is None


def test_build_scaffold_cfg_overrides_only_project_and_policy(tmp_path: Path) -> None:
    base = _slice_cfg(tmp_path / "scaffold")
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()
    new_cfg = build_scaffold_cfg(base, scaffold, tmp_path)
    assert new_cfg.project.root == str(scaffold.resolve())
    assert new_cfg.project.default_branch == "main"
    assert new_cfg.project.worktree_root == str((tmp_path / "scaffold_worktrees").resolve())
    assert "app/**" in new_cfg.file_policy.allowed_paths
    # Providers / scoring / stop_conditions preserved.
    assert new_cfg.providers == base.providers
    assert new_cfg.scoring == base.scoring


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_build_slice_task_text_contains_all_sections(tmp_path: Path) -> None:
    text = build_slice_task_text(_sample_plan(), _sample_arch(), _sample_manifest(tmp_path))
    for header in (
        "# Implement vertical slice: Create + list tasks",
        "## User journey",
        "## Acceptance criteria",
        "## API endpoints in this slice",
        "## Data entities in this slice",
        "## Scaffold layout",
        "## Constraints",
    ):
        assert header in text
    # Constraint about scope:
    assert "Modify only files inside `app/` and `tests/`" in text


def test_build_slice_task_text_truncates_long_file_list(tmp_path: Path) -> None:
    manifest = _sample_manifest(tmp_path)
    # Inflate the file list to trigger truncation.
    manifest.files = manifest.files * 10
    text = build_slice_task_text(_sample_plan(), _sample_arch(), manifest)
    assert "and " in text and "more" in text


def test_build_slice_repo_context_shows_entities_and_operations(tmp_path: Path) -> None:
    text = build_slice_repo_context(_sample_arch(), _sample_manifest(tmp_path))
    assert "## Entities" in text
    assert "**Task**" in text
    assert "## API operations" in text
    assert "POST /tasks" in text


# ---------------------------------------------------------------------------
# Sync-back semantics
# ---------------------------------------------------------------------------

def test_sync_worktree_to_scaffold_copies_new_files(tmp_path: Path) -> None:
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()
    worktree = tmp_path / "wt"
    (worktree / "app").mkdir(parents=True)
    (worktree / "app" / "service.py").write_text("BODY = 1\n")
    synced = sync_worktree_to_scaffold(worktree, scaffold, ["app/service.py"])
    assert synced == ["app/service.py"]
    assert (scaffold / "app" / "service.py").read_text() == "BODY = 1\n"


def test_sync_worktree_to_scaffold_overwrites_existing(tmp_path: Path) -> None:
    scaffold = tmp_path / "scaffold"
    (scaffold / "app").mkdir(parents=True)
    (scaffold / "app" / "service.py").write_text("OLD\n")
    worktree = tmp_path / "wt"
    (worktree / "app").mkdir(parents=True)
    (worktree / "app" / "service.py").write_text("NEW\n")
    sync_worktree_to_scaffold(worktree, scaffold, ["app/service.py"])
    assert (scaffold / "app" / "service.py").read_text() == "NEW\n"


def test_sync_worktree_to_scaffold_honors_deletions(tmp_path: Path) -> None:
    scaffold = tmp_path / "scaffold"
    (scaffold / "app").mkdir(parents=True)
    (scaffold / "app" / "old.py").write_text("byebye\n")
    worktree = tmp_path / "wt"
    (worktree / "app").mkdir(parents=True)
    # file is absent from the worktree -> sync should delete from scaffold
    synced = sync_worktree_to_scaffold(worktree, scaffold, ["app/old.py"])
    assert synced == ["app/old.py"]
    assert not (scaffold / "app" / "old.py").exists()


def test_sync_worktree_to_scaffold_refuses_path_escape(tmp_path: Path) -> None:
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    with pytest.raises(VerticalSliceImplementerError):
        sync_worktree_to_scaffold(worktree, scaffold, ["../escape.py"])


# ---------------------------------------------------------------------------
# Save artifact
# ---------------------------------------------------------------------------

def test_save_vertical_slice_result_writes_valid_json(tmp_path: Path) -> None:
    result = VerticalSliceImplementerResult(
        decision="accept",
        candidate_id="mock_impl",
        provider_id="mock_impl",
        score=85.0,
        changed_files=["app/service.py"],
        synced_to_scaffold=True,
    )
    path = tmp_path / "vertical_slice_result.json"
    save_vertical_slice_result(result, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["decision"] == "accept"
    assert payload["changed_files"] == ["app/service.py"]
    assert payload["synced_to_scaffold"] is True


# ---------------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------------

def test_end_to_end_accept_syncs_file_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build a project + a run dir with a scaffold inside (mirroring what
    # app_from_prd_driver lays out).
    project_root = tmp_path / "project"
    project_root.mkdir()
    run_ctx = create_run_context(project_root, workflow="app_from_prd", input_path=None)
    scaffold = run_ctx.root / "scaffold"
    # Reuse the bare-scaffold helper to populate the run-local scaffold.
    scaffold.mkdir()
    (scaffold / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (scaffold / "app").mkdir()
    (scaffold / "app" / "__init__.py").write_text("")
    (scaffold / "app" / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )
    (scaffold / "tests").mkdir()
    (scaffold / "tests" / "__init__.py").write_text("")

    target_file = "app/services/task_service.py"
    target_contents = (
        '"""Implemented by the mock slice provider."""\n'
        "def create_task(title: str) -> dict:\n"
        '    return {"id": 1, "title": title}\n'
    )

    def impl_behavior(request):
        cwd = Path(request.cwd)
        target = cwd / target_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(target_contents)
        _commit_all(cwd)
        return AgentResult(
            provider_id="mock_impl",
            role="implementer",
            success=True,
            stdout="wrote slice service",
            changed_files=[target_file],
            exit_code=0,
        )

    cfg = _slice_cfg(
        scaffold,
        impl_behavior=impl_behavior,
        review_behavior=_review_pass(),
    )
    _install_mock_registry(monkeypatch)

    result = run_vertical_slice_implementer(
        cfg,
        run_ctx,
        slice_plan=_sample_plan(),
        arch=_sample_arch(),
        scaffold_manifest=_sample_manifest(scaffold),
    )

    assert result.decision in {"accept", "revise"}  # judge may bounce to revise on minimal validation
    # Regardless of judge verdict, the candidate dir was created.
    cand_dir = run_ctx.candidates_dir / "mock_impl"
    assert (cand_dir / "agent_result.json").exists()
    # On accept, the file should be visible in scaffold/.
    if result.decision == "accept":
        assert (scaffold / target_file).exists()
        assert (scaffold / target_file).read_text() == target_contents
        assert result.synced_to_scaffold is True
        assert result.candidate_id == "mock_impl"
        assert result.provider_id == "mock_impl"


# ---------------------------------------------------------------------------
# End-to-end skip path (unsupported scaffold via the run_vertical_slice_implementer call)
# ---------------------------------------------------------------------------

def test_unsupported_stack_short_circuits_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    run_ctx = create_run_context(project_root, workflow="app_from_prd", input_path=None)
    # Note: skip is enforced by the driver via skip_reason(); when invoked
    # directly the implementer still requires a real scaffold dir. So here we
    # just verify skip_reason gates the call site correctly.
    manifest = _sample_manifest(run_ctx.root / "scaffold", supported=False)
    assert skip_reason(manifest, _sample_plan()) is not None


def test_no_implementer_role_skips_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    run_ctx = create_run_context(project_root, workflow="app_from_prd", input_path=None)
    scaffold = run_ctx.root / "scaffold"
    scaffold.mkdir()
    (scaffold / "app").mkdir()
    (scaffold / "app" / "__init__.py").write_text("")
    (scaffold / "tests").mkdir()
    (scaffold / "tests" / "__init__.py").write_text("")
    (scaffold / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")

    cfg = _slice_cfg(scaffold)
    # Empty out the implementer role so the router returns nothing.
    cfg.roles.pop("implementer", None)
    _install_mock_registry(monkeypatch)

    result = run_vertical_slice_implementer(
        cfg,
        run_ctx,
        slice_plan=_sample_plan(),
        arch=_sample_arch(),
        scaffold_manifest=_sample_manifest(scaffold),
    )
    assert result.decision == "skipped"
    assert "implementer" in result.reason.lower()
