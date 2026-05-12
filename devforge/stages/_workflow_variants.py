"""Workflow-variant prompt scaffolding.

The ``feature`` driver also handles ``bugfix`` and ``refactor`` because
their stage shape is identical — the only meaningful difference is the
framing given to the implementer. We prepend a short guidance block to
the task text so ``normalize_task`` picks up the workflow-specific
intent (and the implementer prompt rendered downstream inherits it).

Adding a new variant is one entry in :data:`_GUIDANCE` plus an entry in
``workflow_engine.py``'s dispatch allowlist. No driver fork required.
"""
from __future__ import annotations

# Each variant's guidance is rendered as a standalone markdown block
# prepended to the user's task text. Keep them short, imperative, and
# free of project-specific assumptions.
_GUIDANCE: dict[str, str] = {
    "feature": "",  # default — no prelude
    "bugfix": (
        "# Workflow variant: bugfix\n"
        "\n"
        "This task is a **bug fix**. Apply these constraints on top of the "
        "task description that follows:\n"
        "\n"
        "- Reproduce the bug with a **failing test first**, then make the "
        "  minimum code change required to turn it green.\n"
        "- **Preserve the existing public API** — no signature changes, no "
        "  renames, no scope expansion.\n"
        "- Do not refactor unrelated code. A bug fix is the wrong moment "
        "  for cleanup work.\n"
        "- If the root cause is unclear, write a probing test that surfaces "
        "  more information rather than guessing.\n"
    ),
    "refactor": (
        "# Workflow variant: refactor\n"
        "\n"
        "This task is a **refactor**. Apply these constraints on top of the "
        "task description that follows:\n"
        "\n"
        "- **Preserve all observable behavior**. The existing test suite "
        "  must continue to pass without test modifications.\n"
        "- Improve structure, readability, naming, or type coverage. Do "
        "  not add features or change public behavior.\n"
        "- Prefer small reversible steps. A refactor that requires "
        "  simultaneously changing call sites in many files is a smell — "
        "  break it down.\n"
        "- If a test breaks, that's a behavior change — revert that part "
        "  of the refactor instead of editing the test.\n"
    ),
}


def is_known_variant(workflow_variant: str) -> bool:
    return workflow_variant in _GUIDANCE


def apply_guidance(workflow_variant: str, task_text: str) -> str:
    """Prepend the variant-specific guidance to ``task_text``.

    Unknown variants and the ``feature`` default return ``task_text``
    unchanged — the caller does not need to special-case anything.
    """
    prelude = _GUIDANCE.get(workflow_variant, "")
    if not prelude:
        return task_text
    if not task_text:
        return prelude
    return prelude + "\n" + task_text
