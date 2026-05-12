"""Architecture generator (DEVF-064).

Deterministic projection of Requirements + PrdIntake + MvpScope + UxInventory
onto four planning artifacts:

- ``architecture.md``     — stack / runtime / layers / modules / persistence /
                            tradeoffs
- ``data_model.md``       — entities + fields with their source FR ids
- ``api_contract.yaml``   — OpenAPI 3.0 contract (one operation per api screen)
- ``tech_stack.md``       — runtime / framework / test command / scaffold outline

This stage does **not** generate actual project source code or scaffold files
— it only produces planning documents that DEVF-065 (scaffold) can consume.

Only the ``python-fastapi-only`` stack has a concrete profile in this release;
other stack values are marked ``supported_stack=False`` and produce a generic
outline plus a "planned" note.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from devforge.stages.mvp_scope import MvpScope
from devforge.stages.prd_intake import PrdIntake
from devforge.stages.requirements_schema import Requirements
from devforge.stages.ux_flow import Screen, UxInventory

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    name: str
    fields: dict[str, str] = field(default_factory=dict)   # field name → openapi type
    sourced_from: list[str] = field(default_factory=list)  # FR ids

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ApiOperation:
    method: str
    path: str
    summary: str
    request_body_schema: str | None = None
    responses: dict[str, str] = field(default_factory=dict)
    requirement_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Architecture:
    stack: str
    supported_stack: bool
    runtime: str
    framework: str
    test_command: str
    scaffold_outline: list[str] = field(default_factory=list)
    layers: list[tuple[str, str]] = field(default_factory=list)
    module_boundaries: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    persistence: str = "in-memory"
    operations: list[ApiOperation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    project_name: str = "app"


# ---------------------------------------------------------------------------
# Stack profiles
# ---------------------------------------------------------------------------

_STACK_PROFILES: dict[str, dict[str, object]] = {
    "python-fastapi-only": {
        "runtime": "Python 3.11+",
        "framework": "FastAPI",
        "test_command": "pytest -q",
        "scaffold_outline": [
            "app/main.py              # FastAPI() instance + router include",
            "app/routes/<resource>.py # router modules per resource",
            "app/services/<resource>.py  # pure-Python business logic",
            "app/models/<resource>.py    # Pydantic models / entities",
            "app/store.py            # in-memory data structures (MVP)",
            "tests/test_<resource>.py",
            "pyproject.toml",
        ],
        "layers": [
            ("api", "FastAPI routers translate HTTP into service calls"),
            ("service", "Pure-Python business logic with no HTTP concerns"),
            ("model", "Pydantic models for request/response shapes"),
            ("store", "In-memory data structures (swap for a real DB later)"),
        ],
    },
}


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_HTTP_METHOD_RX = re.compile(r"\b(GET|POST|PATCH|PUT|DELETE)\b")
_PATH_RX = re.compile(r"(/[A-Za-z0-9_/{}.-]+)")
_HTTP_STATUS_RX = re.compile(r"HTTP\s+(\d{3})")
_JSON_OBJECT_RX = re.compile(r"\{[^{}]*\}")
_JSON_PAIR_RX = re.compile(
    r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*'
    r'(true|false|\d+\.\d+|\d+|"[^"]*"|\[[^\]]*\])'
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_architecture(
    reqs: Requirements,
    intake: PrdIntake,
    scope: MvpScope,
    inventory: UxInventory,
    stack: str,
    *,
    project_name: str = "app",
) -> Architecture:
    """Build a planning architecture record from the upstream PRD artifacts."""
    maybe_profile = _STACK_PROFILES.get(stack)
    supported = maybe_profile is not None
    notes: list[str] = []
    profile: dict[str, object] = (
        maybe_profile if maybe_profile is not None else _infer_unsupported_profile(stack)
    )
    if not supported:
        notes.append(
            f"Stack profile '{stack}' is planned — treat the generated outline "
            f"as a placeholder. Only 'python-fastapi-only' has a concrete profile."
        )

    runtime = str(profile["runtime"])
    framework = str(profile["framework"])
    test_command = str(profile["test_command"])
    raw_outline = profile["scaffold_outline"]
    scaffold_outline = list(raw_outline) if isinstance(raw_outline, list) else []
    raw_layers = profile["layers"]
    layers = (
        [tuple(item) for item in raw_layers]
        if isinstance(raw_layers, list)
        else []
    )

    entities = _extract_entities(reqs, inventory)
    operations = _extract_operations(reqs, inventory, entities)
    persistence = _infer_persistence(intake, scope)
    module_boundaries = _module_boundaries_for(stack, entities, operations)

    tradeoffs = [
        "MVP uses in-memory storage; swap for a real database before "
        "multi-process deployment.",
        "Surfaces are derived heuristically from the PRD — revise "
        "architecture.md if a requirement was misclassified.",
        "OpenAPI contract uses inferred schemas — flesh out request and "
        "response types as the design firms up.",
    ]
    if not supported:
        tradeoffs.append(
            f"Stack profile for '{stack}' is not natively supported by devforge "
            f"yet; treat scaffold outline as a placeholder."
        )

    if not operations and any(s.kind == "api" for s in inventory.screens):
        notes.append(
            "No API operations were extracted from the screen inventory even "
            "though api screens exist — the screens may lack explicit method/path "
            "tokens. Review requirements descriptions."
        )
    if not any(s.kind == "api" for s in inventory.screens):
        notes.append(
            "No API surface detected in the screen inventory — api_contract.yaml "
            "will have an empty 'paths' map."
        )

    return Architecture(
        stack=stack,
        supported_stack=supported,
        runtime=runtime,
        framework=framework,
        test_command=test_command,
        scaffold_outline=scaffold_outline,
        layers=layers,
        module_boundaries=module_boundaries,
        tradeoffs=tradeoffs,
        entities=entities,
        persistence=persistence,
        operations=operations,
        notes=notes,
        project_name=project_name,
    )


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_architecture(arch: Architecture, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# Architecture", ""]

    lines.append("## Stack")
    lines.append("")
    lines.append(f"- Declared stack: `{arch.stack}`")
    lines.append(
        f"- Supported by devforge: **{'yes' if arch.supported_stack else 'planned'}**"
    )
    lines.append(f"- Runtime: {arch.runtime}")
    lines.append(f"- Framework: {arch.framework}")
    lines.append("")

    lines.append("## Layers")
    lines.append("")
    if arch.layers:
        lines.append("| Layer | Purpose |")
        lines.append("|---|---|")
        for name, purpose in arch.layers:
            lines.append(f"| {name} | {purpose} |")
    else:
        lines.append("_no layered breakdown for this stack profile_")
    lines.append("")

    lines.append("## Module boundaries")
    lines.append("")
    if arch.module_boundaries:
        for module in arch.module_boundaries:
            lines.append(f"- `{module}`")
    else:
        lines.append("_no concrete module suggestions (stack profile pending)_")
    lines.append("")

    lines.append("## Persistence")
    lines.append("")
    lines.append(f"- {arch.persistence}")
    lines.append("")

    lines.append("## Tradeoffs")
    lines.append("")
    for t in arch.tradeoffs:
        lines.append(f"- {t}")
    lines.append("")

    if arch.notes:
        lines.append("## Notes")
        lines.append("")
        for n in arch.notes:
            lines.append(f"- {n}")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def save_data_model(arch: Architecture, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# Data model", ""]
    lines.append(f"Persistence assumption: **{arch.persistence}**")
    lines.append("")

    if not arch.entities:
        lines.append("_No entities derived from the PRD. Add resource-style paths "
                     "(e.g. `/tasks`) or JSON shapes to the functional requirements._")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return path

    for entity in arch.entities:
        lines.append(f"## {entity.name}")
        lines.append("")
        lines.append(f"- Sourced from: {', '.join(entity.sourced_from) or 'n/a'}")
        lines.append("")
        lines.append("| Field | Type |")
        lines.append("|---|---|")
        for fname, ftype in entity.fields.items():
            lines.append(f"| {fname} | {ftype} |")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def save_api_contract(arch: Architecture, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict[str, object] = {
        "openapi": "3.0.3",
        "info": {
            "title": arch.project_name,
            "version": "0.0.1",
            "description": (
                "Auto-generated planning contract from devforge DEVF-064. "
                "Refine before implementation — schemas and responses are inferred."
            ),
        },
        "paths": {},
        "components": {"schemas": {}},
    }

    paths: dict[str, dict[str, object]] = {}
    for op in arch.operations:
        item = paths.setdefault(op.path, {})
        operation: dict[str, object] = {
            "summary": op.summary,
            "responses": _render_responses(op),
        }
        if op.request_body_schema:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": f"#/components/schemas/{op.request_body_schema}"
                        }
                    }
                },
            }
        if op.requirement_ids:
            operation["x-requirement-ids"] = list(op.requirement_ids)
        item[op.method.lower()] = operation
    doc["paths"] = paths

    schemas: dict[str, object] = {}
    for entity in arch.entities:
        schemas[entity.name] = {
            "type": "object",
            "properties": {
                fname: {"type": ftype} for fname, ftype in entity.fields.items()
            },
        }
    if schemas:
        doc["components"] = {"schemas": schemas}
    else:
        # Drop the empty components block so the contract stays minimal.
        doc.pop("components", None)

    path.write_text(
        yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return path


def save_tech_stack(arch: Architecture, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# Tech stack", ""]
    lines.append(f"- Stack: `{arch.stack}`")
    lines.append(
        f"- Supported by devforge: **{'yes' if arch.supported_stack else 'planned'}**"
    )
    lines.append("")
    lines.append("## Runtime")
    lines.append("")
    lines.append(f"- {arch.runtime}")
    lines.append("")
    lines.append("## Framework")
    lines.append("")
    lines.append(f"- {arch.framework}")
    lines.append("")
    lines.append("## Test command")
    lines.append("")
    lines.append(f"```bash\n{arch.test_command}\n```")
    lines.append("")
    lines.append("## Scaffold outline")
    lines.append("")
    if arch.scaffold_outline:
        for item in arch.scaffold_outline:
            lines.append(f"- {item}")
    else:
        lines.append("_no scaffold outline for this stack profile_")
    lines.append("")
    if arch.notes:
        lines.append("## Notes")
        lines.append("")
        for n in arch.notes:
            lines.append(f"- {n}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Internal — entity / operation extraction
# ---------------------------------------------------------------------------

def _singularize(noun: str) -> str:
    if noun.endswith("ies") and len(noun) > 3:
        return noun[:-3].capitalize() + "y"
    if noun.endswith("s") and not noun.endswith("ss") and len(noun) > 1:
        return noun[:-1].capitalize()
    return noun.capitalize()


def _resource_from_path(path: str) -> str | None:
    parts = [p for p in path.split("/") if p and not p.startswith("{")]
    if not parts:
        return None
    return _singularize(parts[0])


def _infer_type(literal: str) -> str:
    s = literal.strip()
    if s in ("true", "false"):
        return "boolean"
    if s.startswith("["):
        return "array"
    if s.startswith('"'):
        return "string"
    try:
        int(s)
        return "integer"
    except ValueError:
        pass
    try:
        float(s)
        return "number"
    except ValueError:
        pass
    return "string"


def _fields_from_blob(blob: str) -> dict[str, str]:
    """Extract field-name → type guesses from any JSON-like objects in ``blob``."""
    fields: dict[str, str] = {}
    for match in _JSON_OBJECT_RX.findall(blob):
        for key, value in _JSON_PAIR_RX.findall(match):
            fields.setdefault(key, _infer_type(value))
    return fields


def _extract_entities(
    reqs: Requirements, inventory: UxInventory
) -> list[Entity]:
    """One entity per top-level resource path appearing in api-kind screens."""
    by_name: dict[str, Entity] = {}
    for screen in inventory.screens:
        if screen.kind != "api":
            continue
        for raw_path in screen.inputs:
            if not raw_path.startswith("/"):
                continue
            name = _resource_from_path(raw_path)
            if not name:
                continue
            entity = by_name.get(name)
            if entity is None:
                entity = Entity(name=name)
                by_name[name] = entity
            entity.sourced_from.extend(
                rid for rid in screen.requirement_ids if rid not in entity.sourced_from
            )
            if "{id}" in raw_path or "{" in raw_path:
                entity.fields.setdefault("id", "integer")
            for blob_chunk in screen.inputs + screen.outputs:
                for fname, ftype in _fields_from_blob(blob_chunk).items():
                    entity.fields.setdefault(fname, ftype)
            # Pull richer descriptions from the matching FR (acceptance criteria).
            for fr in reqs.functional:
                if fr.id in screen.requirement_ids:
                    blob = fr.description + "\n" + "\n".join(fr.acceptance_criteria)
                    for fname, ftype in _fields_from_blob(blob).items():
                        entity.fields.setdefault(fname, ftype)
    return list(by_name.values())


def _extract_operations(
    reqs: Requirements, inventory: UxInventory, entities: list[Entity]
) -> list[ApiOperation]:
    entity_by_resource = {e.name: e for e in entities}
    fr_by_id = {fr.id: fr for fr in reqs.functional}
    operations: list[ApiOperation] = []
    for screen in inventory.screens:
        if screen.kind != "api":
            continue
        # The HTTP method usually lives in the FR description (which is what
        # the UX stage *classified* on), not in the screen.inputs/outputs which
        # only carry the extracted path + status tokens.
        fr_blobs = [
            fr_by_id[rid].description + " " + " ".join(fr_by_id[rid].acceptance_criteria)
            for rid in screen.requirement_ids
            if rid in fr_by_id
        ]
        blob = " ".join(fr_blobs + screen.inputs + screen.outputs + [screen.title])
        method_match = _HTTP_METHOD_RX.search(blob)
        if not method_match:
            continue
        method = method_match.group(1).upper()
        path = _first_path(screen)
        if not path:
            continue
        responses = _responses_for(screen, method)
        resource_name = _resource_from_path(path)
        body_schema: str | None = None
        if method in {"POST", "PUT", "PATCH"} and resource_name in entity_by_resource:
            body_schema = resource_name
        operations.append(
            ApiOperation(
                method=method,
                path=_normalize_path(path),
                summary=screen.title,
                request_body_schema=body_schema,
                responses=responses,
                requirement_ids=list(screen.requirement_ids),
            )
        )
    return operations


def _first_path(screen: Screen) -> str | None:
    for token in screen.inputs:
        if token.startswith("/"):
            return token
    return None


def _normalize_path(path: str) -> str:
    # Drop trailing fragments past the resource segment so multiple operations
    # on the same collection land under the same paths entry.
    stripped = path.split()[0]
    return stripped


def _responses_for(screen: Screen, method: str) -> dict[str, str]:
    responses: dict[str, str] = {}
    for tok in screen.outputs:
        m = _HTTP_STATUS_RX.search(tok)
        if m:
            code = m.group(1)
            responses[code] = _default_status_text(code)
    if not responses:
        if method == "POST":
            responses["201"] = _default_status_text("201")
        elif method == "DELETE":
            responses["204"] = _default_status_text("204")
        else:
            responses["200"] = _default_status_text("200")
    return responses


def _default_status_text(code: str) -> str:
    table = {
        "200": "OK",
        "201": "Created",
        "204": "No Content",
        "400": "Bad Request",
        "404": "Not Found",
        "500": "Internal Server Error",
    }
    return table.get(code, "Response")


def _render_responses(op: ApiOperation) -> dict[str, dict[str, str]]:
    return {code: {"description": text} for code, text in op.responses.items()}


# ---------------------------------------------------------------------------
# Internal — persistence + modules
# ---------------------------------------------------------------------------

def _infer_persistence(intake: PrdIntake, scope: MvpScope) -> str:
    blob_sources: list[str] = list(intake.constraints) + list(scope.assumptions)
    blob = " ".join(blob_sources).lower()
    if "postgres" in blob:
        return "PostgreSQL (per PRD constraints)"
    if "mysql" in blob:
        return "MySQL (per PRD constraints)"
    if "sqlite" in blob:
        return "SQLite (per PRD constraints)"
    if "in-memory" in blob or "no external database" in blob or "in memory" in blob:
        return "In-memory store (per PRD constraints)"
    return "In-memory store (MVP default — swap for an external database before scaling)"


def _module_boundaries_for(
    stack: str,
    entities: list[Entity],
    operations: list[ApiOperation],
) -> list[str]:
    if stack != "python-fastapi-only":
        return []
    modules: list[str] = []
    for entity in entities:
        slug = entity.name.lower()
        modules.append(f"app.routes.{slug}")
        modules.append(f"app.services.{slug}")
        modules.append(f"app.models.{slug}")
    if not modules and operations:
        modules.append("app.routes.<resource>")
    modules.append("app.store")
    return modules


def _infer_unsupported_profile(stack: str) -> dict[str, object]:
    runtime = "(planned — provide manually)"
    framework = "(planned — provide manually)"
    lowered = stack.lower()
    if "python" in lowered:
        runtime = "Python 3.11+ (planned)"
    if "node" in lowered:
        runtime = "Node 20+ (planned)"
    if "react" in lowered:
        framework = "React (planned)"
    elif "fastapi" in lowered:
        framework = "FastAPI (planned)"
    elif "express" in lowered:
        framework = "Express (planned)"
    elif "unity" in lowered:
        runtime = "Unity (planned)"
        framework = "Unity (planned)"
    return {
        "runtime": runtime,
        "framework": framework,
        "test_command": "(planned)",
        "scaffold_outline": [
            "(scaffold outline pending — devforge does not yet ship a profile for this stack)",
        ],
        "layers": [],
    }


# Silence unused-import lint when these helpers expand later.
_ = json
