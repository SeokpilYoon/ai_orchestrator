"""Minimal prompt renderer.

Authoritative reference: docs/plan/02 §9.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

_PROMPTS_PACKAGE = "devforge.prompts.roles"


def load_role_prompt(role: str) -> str:
    """Load ``devforge/prompts/roles/<role>.md`` as text."""
    filename = f"{role}.md"
    try:
        return resources.files(_PROMPTS_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        # Fallback to repo-relative path during development.
        fallback = Path(__file__).resolve().parent.parent / "prompts" / "roles" / filename
        return fallback.read_text(encoding="utf-8")


def render(template: str, variables: dict[str, str]) -> str:
    """Tiny ``{{ var }}`` substitution. Unknown variables are left intact so
    missing context is easy to spot in saved prompts."""
    out = template
    for key, value in variables.items():
        out = out.replace("{{ " + key + " }}", value)
        out = out.replace("{{" + key + "}}", value)
    return out
