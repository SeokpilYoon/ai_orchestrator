"""Scaffold generator (DEVF-065).

Generates a minimal runnable project skeleton for the ``python-fastapi-only``
stack inside ``<run_root>/scaffold/``. Other stacks are recorded as skipped.

Strong isolation: every write is constrained to ``scaffold_root.resolve()``
via :func:`_safe_join`. The orchestrator never touches the host repository,
never runs ``pip install``, and never starts a server. Import smoke is
limited to ``py_compile.compile(..., doraise=True)``.
"""
from __future__ import annotations

import hashlib
import json
import py_compile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devforge.stages.architecture_generator import (
    ApiOperation,
    Architecture,
    Entity,
)
from devforge.stages.mvp_scope import MvpScope
from devforge.stages.requirements_schema import Requirements
from devforge.stages.ux_flow import UxInventory

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class ScaffoldFile:
    path: str
    bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ScaffoldManifest:
    stack: str
    supported: bool
    scaffold_root: str
    files: list[ScaffoldFile] = field(default_factory=list)
    import_smoke_passed: bool = False
    test_command: str = ""
    project_name: str = "app"
    entities: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "stack": self.stack,
            "supported": self.supported,
            "scaffold_root": self.scaffold_root,
            "files": [f.to_dict() for f in self.files],
            "import_smoke_passed": self.import_smoke_passed,
            "test_command": self.test_command,
            "project_name": self.project_name,
            "entities": list(self.entities),
            "notes": list(self.notes),
        }


class ScaffoldError(Exception):
    """Raised when the scaffold generator refuses to proceed (safety)."""


_PY_TYPE_FROM_OPENAPI: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_scaffold(
    arch: Architecture,
    reqs: Requirements,
    scope: MvpScope,
    inventory: UxInventory,
    scaffold_root: Path,
    *,
    run_root: Path,
    project_name: str = "app",
) -> ScaffoldManifest:
    """Materialize a scaffold for the given Architecture.

    For ``python-fastapi-only`` this writes a runnable FastAPI skeleton.
    For any other stack the function returns a manifest with ``supported=False``
    and writes nothing.

    Raises:
        ScaffoldError: when the scaffold target would escape ``run_root``,
            already exists, or another safety invariant is violated.
    """
    run_root_abs = run_root.resolve()
    scaffold_root_abs = scaffold_root.resolve()
    if not _is_inside(scaffold_root_abs, run_root_abs):
        raise ScaffoldError(
            f"scaffold root {scaffold_root_abs} is outside the run directory "
            f"{run_root_abs}"
        )

    # Unsupported stack: short-circuit without creating anything on disk.
    if not arch.supported_stack:
        return ScaffoldManifest(
            stack=arch.stack,
            supported=False,
            scaffold_root=_relpath(scaffold_root, run_root),
            files=[],
            import_smoke_passed=False,
            test_command="",
            project_name=project_name,
            entities=[entity.name for entity in arch.entities],
            notes=[
                f"Stack '{arch.stack}' has no scaffold profile yet. "
                f"Only 'python-fastapi-only' generates files in this release."
            ],
        )

    # Refuse to overwrite an existing scaffold.
    if scaffold_root_abs.exists():
        raise ScaffoldError(
            f"scaffold directory already exists at {scaffold_root_abs}; "
            f"refuse to overwrite"
        )
    scaffold_root_abs.mkdir(parents=True, exist_ok=False)

    manifest = ScaffoldManifest(
        stack=arch.stack,
        supported=True,
        scaffold_root=_relpath(scaffold_root, run_root),
        test_command=arch.test_command,
        project_name=project_name,
        entities=[entity.name for entity in arch.entities],
    )

    # Ensure we always have at least one entity to anchor the FastAPI app.
    entities = arch.entities or [Entity(name="Item", fields={"id": "integer"})]
    if not arch.entities:
        manifest.notes.append(
            "No entities were derived from the PRD — emitting a placeholder "
            "'Item' resource so the FastAPI app remains importable."
        )

    operations_by_entity = _group_operations(arch.operations, entities)

    # Files that always exist.
    _write(scaffold_root_abs, "pyproject.toml", _render_pyproject(project_name), manifest)
    _write(scaffold_root_abs, "README.md", _render_readme(arch, manifest), manifest)
    _write(scaffold_root_abs, "app/__init__.py",
           '"""FastAPI application package (devforge scaffold)."""\n',
           manifest)
    _write(scaffold_root_abs, "app/main.py", _render_main(project_name, entities),
           manifest)
    _write(scaffold_root_abs, "app/store.py", _render_store(entities), manifest)
    _write(scaffold_root_abs, "app/models/__init__.py", "", manifest)
    _write(scaffold_root_abs, "app/routes/__init__.py", "", manifest)
    _write(scaffold_root_abs, "app/services/__init__.py", "", manifest)
    _write(scaffold_root_abs, "tests/__init__.py", "", manifest)

    # Per-entity files. Convention: models use the singular name
    # (``models/task.py``); routes / services / tests use the plural
    # collection name (``routes/tasks.py``).
    for entity in entities:
        singular = _singular_slug(entity.name)
        plural = _slug(entity.name)
        _write(
            scaffold_root_abs,
            f"app/models/{singular}.py",
            _render_model(entity),
            manifest,
        )
        _write(
            scaffold_root_abs,
            f"app/routes/{plural}.py",
            _render_route(entity, operations_by_entity.get(entity.name, [])),
            manifest,
        )
        _write(
            scaffold_root_abs,
            f"app/services/{plural}.py",
            _render_service(entity),
            manifest,
        )
        _write(
            scaffold_root_abs,
            f"tests/test_{plural}.py",
            _render_test(entity),
            manifest,
        )

    # py_compile smoke — check every generated .py file.
    smoke_ok = True
    for f in manifest.files:
        if not f.path.endswith(".py"):
            continue
        target = scaffold_root_abs / f.path
        try:
            py_compile.compile(str(target), doraise=True)
        except py_compile.PyCompileError as exc:
            smoke_ok = False
            manifest.notes.append(f"py_compile failed for {f.path}: {exc.msg.strip()}")
    manifest.import_smoke_passed = smoke_ok
    return manifest


def save_scaffold_manifest(manifest: ScaffoldManifest, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Internal — file system safety
# ---------------------------------------------------------------------------

def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _relpath(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _safe_join(scaffold_root: Path, rel: str) -> Path:
    target = (scaffold_root / rel).resolve()
    if not _is_inside(target, scaffold_root):
        raise ScaffoldError(
            f"refuses to write outside scaffold root: {rel} resolved to {target}"
        )
    return target


def _write(
    scaffold_root: Path, rel_path: str, content: str, manifest: ScaffoldManifest
) -> None:
    target = _safe_join(scaffold_root, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    data = content.encode("utf-8")
    manifest.files.append(
        ScaffoldFile(
            path=rel_path,
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest()[:16],
        )
    )


# ---------------------------------------------------------------------------
# Internal — slugs / operation grouping
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    out = name.strip().lower()
    if out.endswith("y") and not out.endswith(("ay", "ey", "oy", "uy")):
        return out[:-1] + "ies"
    if not out.endswith("s"):
        return out + "s"
    return out


def _singular_slug(name: str) -> str:
    return name.strip().lower()


def _group_operations(
    operations: list[ApiOperation], entities: list[Entity]
) -> dict[str, list[ApiOperation]]:
    """Map each operation back to its owning entity by matching the first
    path segment against the entity name (case-insensitive, plural-tolerant)."""
    entity_keys: dict[str, str] = {}
    for e in entities:
        entity_keys[_slug(e.name)] = e.name
        entity_keys[_singular_slug(e.name)] = e.name

    grouped: dict[str, list[ApiOperation]] = {e.name: [] for e in entities}
    for op in operations:
        first_segment = op.path.lstrip("/").split("/", 1)[0]
        entity_name = entity_keys.get(first_segment.lower())
        if entity_name is None:
            continue
        grouped[entity_name].append(op)
    return grouped


# ---------------------------------------------------------------------------
# Internal — file content
# ---------------------------------------------------------------------------

def _render_pyproject(project_name: str) -> str:
    return f'''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{project_name}"
version = "0.0.1"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.104",
    "pydantic>=2.6",
    "uvicorn>=0.24",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "httpx>=0.27",
]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
'''


def _render_readme(arch: Architecture, manifest: ScaffoldManifest) -> str:
    entities_line = ", ".join(manifest.entities) or "(none — placeholder Item)"
    ops_line = f"{len(arch.operations)} API operation(s)"
    return f"""# {manifest.project_name}

Auto-generated scaffold from devforge DEVF-065. Refine the templates before shipping.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
uvicorn app.main:app --reload
```

## Test

```bash
pytest -q
```

## Status

- Stack: `{arch.stack}`
- Entities: {entities_line}
- Operations: {ops_line}
- Persistence: {arch.persistence}
"""


def _render_main(project_name: str, entities: list[Entity]) -> str:
    imports = []
    includes = []
    for entity in entities:
        slug_plural = _slug(entity.name)
        slug_single = _singular_slug(entity.name)
        imports.append(f"from app.routes.{slug_plural} import router as {slug_single}_router")
        includes.append(
            f'app.include_router({slug_single}_router, prefix="/{slug_plural}", tags=["{slug_plural}"])'
        )
    imports_block = "\n".join(imports) if imports else "# no entity routers"
    includes_block = "\n".join(includes) if includes else "# no routers to include"
    return f'''"""FastAPI entry point. Auto-generated by devforge DEVF-065."""
from fastapi import FastAPI

{imports_block}


app = FastAPI(title="{project_name}", version="0.0.1")

{includes_block}


@app.get("/health")
def health() -> dict[str, str]:
    return {{"status": "ok"}}
'''


def _render_store(entities: list[Entity]) -> str:
    blocks: list[str] = []
    for entity in entities:
        slug = _singular_slug(entity.name)
        blocks.append(
            f'''_{slug}_store: dict[int, dict] = {{}}
_{slug}_next_id: int = 1


def next_{slug}_id() -> int:
    global _{slug}_next_id
    with _lock:
        value = _{slug}_next_id
        _{slug}_next_id += 1
    return value


def all_{slug}s() -> list[dict]:
    return list(_{slug}_store.values())


def get_{slug}(item_id: int) -> dict | None:
    return _{slug}_store.get(item_id)


def upsert_{slug}(item_id: int, data: dict) -> dict:
    _{slug}_store[item_id] = data
    return data


def delete_{slug}(item_id: int) -> bool:
    return _{slug}_store.pop(item_id, None) is not None
'''
        )
    return (
        '"""In-memory store. Replace with a real database before scaling."""\n'
        "from threading import Lock\n\n"
        "_lock = Lock()\n\n\n"
        + "\n".join(blocks)
    )


def _render_model(entity: Entity) -> str:
    field_lines: list[str] = []
    if not entity.fields:
        entity.fields["id"] = "integer"
    for fname, ftype in entity.fields.items():
        py_type = _PY_TYPE_FROM_OPENAPI.get(ftype, "str")
        # Pydantic v2 prefers Optional with default None for optional fields.
        field_lines.append(f"    {fname}: {py_type} | None = None")
    fields_block = "\n".join(field_lines)
    return f'''"""Pydantic model for the {entity.name} entity."""
from pydantic import BaseModel


class {entity.name}(BaseModel):
{fields_block}
'''


def _render_route(entity: Entity, operations: list[ApiOperation]) -> str:
    slug_single = _singular_slug(entity.name)
    methods_emitted: set[str] = set()
    handlers: list[str] = []
    skipped: list[str] = []

    for op in operations:
        method = op.method.upper()
        if method == "POST" and "post" not in methods_emitted:
            methods_emitted.add("post")
            handlers.append(_handler_create(entity))
        elif method == "GET" and "{" not in op.path and "list" not in methods_emitted:
            methods_emitted.add("list")
            handlers.append(_handler_list(entity))
        elif method == "GET" and "{" in op.path and "get" not in methods_emitted:
            methods_emitted.add("get")
            handlers.append(_handler_get(entity))
        elif method == "PATCH" and "patch" not in methods_emitted:
            methods_emitted.add("patch")
            handlers.append(_handler_patch(entity))
        elif method == "PUT" and "put" not in methods_emitted:
            methods_emitted.add("put")
            handlers.append(_handler_put(entity))
        elif method == "DELETE" and "delete" not in methods_emitted:
            methods_emitted.add("delete")
            handlers.append(_handler_delete(entity))
        else:
            skipped.append(f"{op.method} {op.path}")

    if not handlers:
        # No operation matched — emit a stub list handler so the router still imports.
        handlers.append(_handler_list(entity))

    skipped_comment = ""
    if skipped:
        skipped_comment = (
            "\n# Skipped operations (not matched to a default handler):\n"
            + "\n".join(f"#   - {s}" for s in skipped)
            + "\n"
        )

    return f'''"""Routes for the {entity.name} resource. Auto-generated by devforge."""
from fastapi import APIRouter, HTTPException

from app import store
from app.models.{slug_single} import {entity.name}

router = APIRouter()
{skipped_comment}

{"".join(handlers)}
'''


def _handler_create(entity: Entity) -> str:
    slug_single = _singular_slug(entity.name)
    return f'''
@router.post("/", status_code=201)
def create_{slug_single}(body: {entity.name}) -> dict:
    item_id = store.next_{slug_single}_id()
    data = body.model_dump()
    data["id"] = item_id
    return store.upsert_{slug_single}(item_id, data)
'''


def _handler_list(entity: Entity) -> str:
    slug_single = _singular_slug(entity.name)
    return f'''
@router.get("/")
def list_{slug_single}s() -> list[dict]:
    return store.all_{slug_single}s()
'''


def _handler_get(entity: Entity) -> str:
    slug_single = _singular_slug(entity.name)
    return f'''
@router.get("/{{item_id}}")
def get_{slug_single}(item_id: int) -> dict:
    item = store.get_{slug_single}(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="{entity.name} not found")
    return item
'''


def _handler_patch(entity: Entity) -> str:
    slug_single = _singular_slug(entity.name)
    return f'''
@router.patch("/{{item_id}}")
def patch_{slug_single}(item_id: int, patch: dict) -> dict:
    item = store.get_{slug_single}(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="{entity.name} not found")
    item.update(patch)
    return store.upsert_{slug_single}(item_id, item)
'''


def _handler_put(entity: Entity) -> str:
    slug_single = _singular_slug(entity.name)
    return f'''
@router.put("/{{item_id}}")
def replace_{slug_single}(item_id: int, body: {entity.name}) -> dict:
    data = body.model_dump()
    data["id"] = item_id
    return store.upsert_{slug_single}(item_id, data)
'''


def _handler_delete(entity: Entity) -> str:
    slug_single = _singular_slug(entity.name)
    return f'''
@router.delete("/{{item_id}}", status_code=204)
def delete_{slug_single}(item_id: int) -> None:
    if not store.delete_{slug_single}(item_id):
        raise HTTPException(status_code=404, detail="{entity.name} not found")
'''


def _render_service(entity: Entity) -> str:
    slug_single = _singular_slug(entity.name)
    return f'''"""Pure-Python service layer for the {entity.name} resource."""
from app import store


def add(payload: dict) -> dict:
    item_id = store.next_{slug_single}_id()
    payload = dict(payload)
    payload["id"] = item_id
    return store.upsert_{slug_single}(item_id, payload)


def list_all() -> list[dict]:
    return store.all_{slug_single}s()


def get(item_id: int) -> dict | None:
    return store.get_{slug_single}(item_id)


def update(item_id: int, patch: dict) -> dict | None:
    item = store.get_{slug_single}(item_id)
    if item is None:
        return None
    item.update(patch)
    return store.upsert_{slug_single}(item_id, item)


def remove(item_id: int) -> bool:
    return store.delete_{slug_single}(item_id)
'''


def _render_test(entity: Entity) -> str:
    slug_plural = _slug(entity.name)
    slug_single = _singular_slug(entity.name)
    return f'''"""Smoke tests for the {entity.name} resource. Requires fastapi + httpx."""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_create_{slug_single}() -> None:
    response = client.post("/{slug_plural}/", json={{}})
    assert response.status_code in (200, 201)


def test_list_{slug_single}s() -> None:
    response = client.get("/{slug_plural}/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
'''
