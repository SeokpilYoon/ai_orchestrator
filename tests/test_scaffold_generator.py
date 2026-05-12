from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.stages.architecture_generator import build_architecture
from devforge.stages.mvp_scope import freeze_mvp_scope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import (
    FunctionalRequirement,
    Requirements,
)
from devforge.stages.scaffold_generator import (
    ScaffoldError,
    generate_scaffold,
    save_scaffold_manifest,
)
from devforge.stages.ux_flow import build_ux_inventory


def _fr(
    idx: int,
    *,
    title: str,
    description: str,
    priority: str = "must",
    acceptance: list[str] | None = None,
) -> FunctionalRequirement:
    return FunctionalRequirement(
        id=f"FR-{idx:03d}",
        title=title,
        description=description,
        priority=priority,
        acceptance_criteria=acceptance or ["does X"],
        test_strategy="integration",
    )


def _bundle(*frs: FunctionalRequirement, stack: str = "python-fastapi-only"):
    intake = PrdIntake(
        target_users=["devs"],
        constraints=["No external database — use an in-memory store"],
    )
    reqs = Requirements(functional=list(frs))
    scope = freeze_mvp_scope(reqs, intake)
    inv = build_ux_inventory(reqs, intake, scope)
    arch = build_architecture(reqs, intake, scope, inv, stack)
    return reqs, intake, scope, inv, arch


_TASK_FRS = [
    _fr(
        1,
        title="Add task",
        description="POST /tasks returns 201",
        acceptance=['{"title": "buy milk"}'],
    ),
    _fr(2, title="List tasks", description="GET /tasks returns JSON"),
    _fr(3, title="Mark complete", description="PATCH /tasks/{id} with done=true"),
    _fr(4, title="Delete task", description="DELETE /tasks/{id} returns 204"),
]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_generates_all_expected_files_for_python_fastapi(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    manifest = generate_scaffold(
        arch, reqs, scope, inv,
        scaffold_root=tmp_path / "scaffold",
        run_root=tmp_path,
        project_name="todo",
    )
    assert manifest.supported is True
    scaffold = tmp_path / "scaffold"
    expected = [
        "pyproject.toml",
        "README.md",
        "app/__init__.py",
        "app/main.py",
        "app/store.py",
        "app/models/__init__.py",
        "app/routes/__init__.py",
        "app/services/__init__.py",
        "tests/__init__.py",
        "app/models/task.py",      # singular for the entity model
        "app/routes/tasks.py",     # plural for the collection route
        "app/services/tasks.py",   # plural for the collection service
        "tests/test_tasks.py",     # plural for the route test
    ]
    for rel in expected:
        assert (scaffold / rel).exists(), f"missing scaffold file: {rel}"


def test_each_generated_file_passes_py_compile(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    manifest = generate_scaffold(
        arch, reqs, scope, inv,
        scaffold_root=tmp_path / "scaffold",
        run_root=tmp_path,
    )
    assert manifest.import_smoke_passed is True
    # No py_compile-failure note recorded.
    assert not any("py_compile failed" in n for n in manifest.notes)


def test_manifest_lists_every_generated_file(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    manifest = generate_scaffold(
        arch, reqs, scope, inv,
        scaffold_root=tmp_path / "scaffold",
        run_root=tmp_path,
    )
    listed = {f.path for f in manifest.files}
    scaffold = tmp_path / "scaffold"
    on_disk = {
        str(p.relative_to(scaffold)).replace("\\", "/")
        for p in scaffold.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    # Every source file on disk must appear in the manifest. The .pyc files
    # produced by py_compile during import smoke are ignored here.
    assert on_disk == listed


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def test_existing_scaffold_directory_raises(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()  # pre-existing
    with pytest.raises(ScaffoldError, match="already exists"):
        generate_scaffold(
            arch, reqs, scope, inv,
            scaffold_root=scaffold,
            run_root=tmp_path,
        )


def test_refuses_scaffold_root_outside_run_root(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    elsewhere = tmp_path.parent / "evil_scaffold"
    with pytest.raises(ScaffoldError, match="outside the run directory"):
        generate_scaffold(
            arch, reqs, scope, inv,
            scaffold_root=elsewhere,
            run_root=tmp_path,
        )


# ---------------------------------------------------------------------------
# Unsupported stack
# ---------------------------------------------------------------------------

def test_unsupported_stack_skips_generation(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS, stack="react-fastapi-postgres")
    manifest = generate_scaffold(
        arch, reqs, scope, inv,
        scaffold_root=tmp_path / "scaffold",
        run_root=tmp_path,
    )
    assert manifest.supported is False
    assert manifest.files == []
    # scaffold/ directory is *not* created when the stack is unsupported.
    assert not (tmp_path / "scaffold").exists()
    assert any("has no scaffold profile yet" in n for n in manifest.notes)


# ---------------------------------------------------------------------------
# Content sanity
# ---------------------------------------------------------------------------

def test_pyproject_includes_fastapi_dependency(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    generate_scaffold(
        arch, reqs, scope, inv,
        scaffold_root=tmp_path / "scaffold",
        run_root=tmp_path,
    )
    pyproj = (tmp_path / "scaffold" / "pyproject.toml").read_text(encoding="utf-8")
    assert "fastapi" in pyproj
    assert "pytest" in pyproj


def test_route_module_references_entity_path(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    generate_scaffold(
        arch, reqs, scope, inv,
        scaffold_root=tmp_path / "scaffold",
        run_root=tmp_path,
    )
    route_src = (tmp_path / "scaffold" / "app" / "routes" / "tasks.py").read_text(
        encoding="utf-8"
    )
    # The router defines at least one POST and one GET handler for the resource.
    assert "@router.post" in route_src
    assert "@router.get" in route_src
    assert "from app.models.task import Task" in route_src


def test_test_module_uses_fastapi_testclient(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    generate_scaffold(
        arch, reqs, scope, inv,
        scaffold_root=tmp_path / "scaffold",
        run_root=tmp_path,
    )
    test_src = (tmp_path / "scaffold" / "tests" / "test_tasks.py").read_text(
        encoding="utf-8"
    )
    assert "TestClient" in test_src
    assert "from app.main import app" in test_src


# ---------------------------------------------------------------------------
# Manifest round-trip
# ---------------------------------------------------------------------------

def test_manifest_round_trip(tmp_path: Path) -> None:
    reqs, intake, scope, inv, arch = _bundle(*_TASK_FRS)
    manifest = generate_scaffold(
        arch, reqs, scope, inv,
        scaffold_root=tmp_path / "scaffold",
        run_root=tmp_path,
        project_name="todo",
    )
    out = tmp_path / "scaffold_manifest.json"
    save_scaffold_manifest(manifest, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["stack"] == "python-fastapi-only"
    assert data["supported"] is True
    assert data["import_smoke_passed"] is True
    assert data["project_name"] == "todo"
    assert any(f["path"] == "app/main.py" for f in data["files"])
    # Every file entry has the three expected keys.
    for f in data["files"]:
        assert {"path", "bytes", "sha256"} <= set(f)
