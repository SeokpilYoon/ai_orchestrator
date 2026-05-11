"""Repo context collector — produce repo_context.md for the implementer prompt.

Authoritative reference: docs/plan/03 DEVF-041.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RepoContext:
    repo_name: str
    top_level_entries: list[str] = field(default_factory=list)
    package_metadata: dict[str, str] = field(default_factory=dict)
    git_status: str = ""
    test_commands: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_METADATA_FILES = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml")
_METADATA_MAX_BYTES = 2048
_MAX_TOP_LEVEL = 40
_DEFAULT_HIDDEN_PREFIX = ("__pycache__",)


def collect_repo_context(
    repo_root: Path,
    likely_files: list[str] | None = None,
    *,
    include_hidden: bool = False,
) -> RepoContext:
    """Collect a deterministic snapshot of the repo state."""
    repo_root = Path(repo_root)
    ctx = RepoContext(repo_name=repo_root.name)
    if not repo_root.exists():
        return ctx

    ctx.top_level_entries = _list_top_level(repo_root, include_hidden=include_hidden)
    ctx.package_metadata = _read_package_metadata(repo_root)
    ctx.git_status = _git_status_short(repo_root)
    ctx.test_commands = _infer_test_commands(repo_root, ctx.package_metadata)
    ctx.relevant_files = _resolve_relevant_files(repo_root, likely_files or [])
    return ctx


def render_repo_context_md(ctx: RepoContext) -> str:
    lines: list[str] = []
    lines.append(f"# Repo context: {ctx.repo_name}")
    lines.append("")

    lines.append("## Top-level entries")
    if ctx.top_level_entries:
        for name in ctx.top_level_entries:
            lines.append(f"- {name}")
    else:
        lines.append("_empty_")
    lines.append("")

    if ctx.package_metadata:
        lines.append("## Package metadata")
        for fname, snippet in ctx.package_metadata.items():
            lines.append(f"### {fname}")
            lines.append("```")
            lines.append(snippet.strip())
            lines.append("```")
        lines.append("")

    lines.append("## Git status")
    if ctx.git_status:
        lines.append("```")
        lines.append(ctx.git_status.strip())
        lines.append("```")
    else:
        lines.append("_clean or not a git repository_")
    lines.append("")

    lines.append("## Inferred test commands")
    if ctx.test_commands:
        for cmd in ctx.test_commands:
            lines.append(f"- `{cmd}`")
    else:
        lines.append("_none detected_")
    lines.append("")

    lines.append("## Relevant files")
    if ctx.relevant_files:
        for f in ctx.relevant_files:
            lines.append(f"- `{f}`")
    else:
        lines.append("_none provided_")
    return "\n".join(lines).rstrip() + "\n"


def save_repo_context(ctx: RepoContext, md_path: Path, json_path: Path | None = None) -> Path:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_repo_context_md(ctx), encoding="utf-8")
    if json_path is not None:
        json_path.write_text(
            json.dumps(ctx.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return md_path


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _list_top_level(repo_root: Path, *, include_hidden: bool) -> list[str]:
    entries: list[str] = []
    try:
        for child in sorted(repo_root.iterdir(), key=lambda p: p.name):
            name = child.name
            if name in _DEFAULT_HIDDEN_PREFIX:
                continue
            if not include_hidden and name.startswith("."):
                continue
            entries.append(name + ("/" if child.is_dir() else ""))
    except (FileNotFoundError, PermissionError):
        return []
    return entries[:_MAX_TOP_LEVEL]


def _read_package_metadata(repo_root: Path) -> dict[str, str]:
    snippets: dict[str, str] = {}
    for fname in _METADATA_FILES:
        p = repo_root / fname
        if not p.exists() or not p.is_file():
            continue
        try:
            data = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippets[fname] = data[:_METADATA_MAX_BYTES]
    return snippets


def _git_status_short(repo_root: Path) -> str:
    if not (repo_root / ".git").exists():
        return ""
    try:
        proc = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""


def _infer_test_commands(repo_root: Path, metadata: dict[str, str]) -> list[str]:
    cmds: list[str] = []
    pyproject = metadata.get("pyproject.toml", "")
    if "pytest" in pyproject or (repo_root / "tests").exists():
        cmds.append("pytest -q")

    pkg_json = metadata.get("package.json", "")
    if pkg_json:
        try:
            obj = json.loads(pkg_json)
            scripts = obj.get("scripts", {}) if isinstance(obj, dict) else {}
            if isinstance(scripts, dict):
                if "test" in scripts:
                    cmds.append("npm test")
                if "lint" in scripts and "npm run lint" not in cmds:
                    cmds.append("npm run lint")
                if "build" in scripts and "npm run build" not in cmds:
                    cmds.append("npm run build")
        except json.JSONDecodeError:
            pass

    if "Cargo.toml" in metadata:
        cmds.append("cargo test")
    if "go.mod" in metadata:
        cmds.append("go test ./...")
    return cmds


def _resolve_relevant_files(repo_root: Path, likely_files: list[str]) -> list[str]:
    if likely_files:
        resolved: list[str] = []
        seen: set[str] = set()
        for raw in likely_files:
            # Support both explicit relative paths and simple glob patterns.
            if any(ch in raw for ch in "*?["):
                for match in repo_root.glob(raw):
                    rel = _safe_relative(match, repo_root)
                    if rel and rel not in seen:
                        seen.add(rel)
                        resolved.append(rel)
            else:
                target = repo_root / raw
                if target.exists():
                    rel = _safe_relative(target, repo_root)
                    if rel and rel not in seen:
                        seen.add(rel)
                        resolved.append(rel)
                elif raw not in seen:
                    seen.add(raw)
                    resolved.append(raw)
        return resolved[:20]
    return _recent_git_files(repo_root)[:20]


def _safe_relative(path: Path, root: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return None


def _recent_git_files(repo_root: Path) -> list[str]:
    if not (repo_root / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            ["git", "log", "--name-only", "--pretty=format:", "-n", "20"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    files: list[str] = []
    seen: set[str] = set()
    for line in proc.stdout.splitlines():
        f = line.strip()
        if not f or f in seen:
            continue
        seen.add(f)
        files.append(f)
    return files


# Silence "unused import" warnings if the module shrinks later.
_ = re
