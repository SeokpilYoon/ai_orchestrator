"""Command policy checker — scan agent stdout/stderr/scripts for blocked patterns.

Authoritative reference: docs/plan/01 §11.2, docs/plan/02 §5.9, docs/plan/03 DEVF-034.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from devforge.core.config_loader import CommandPolicy


@dataclass
class CommandPolicyResult:
    blocked_hits: list[tuple[str, str]] = field(default_factory=list)  # (pattern, snippet)
    review_hits: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_blocked(self) -> bool:
        return bool(self.blocked_hits)

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        return {
            "blocked": [{"pattern": p, "snippet": s} for p, s in self.blocked_hits],
            "require_review": [{"pattern": p, "snippet": s} for p, s in self.review_hits],
        }


def _pattern_to_regex(glob_like: str) -> re.Pattern[str]:
    # Treat config patterns as substring with ``*`` as wildcard.
    escaped = re.escape(glob_like).replace(r"\*", r".*")
    return re.compile(escaped, re.IGNORECASE)


def check_command_policy(text_blobs: list[str], policy: CommandPolicy) -> CommandPolicyResult:
    """Scan each blob (stdout/stderr/log) for blocked or review-required commands."""
    result = CommandPolicyResult()
    blocked_regexes = [(p, _pattern_to_regex(p)) for p in policy.blocked_patterns]
    review_regexes = [(p, _pattern_to_regex(p)) for p in policy.require_human_review]

    for blob in text_blobs:
        if not blob:
            continue
        for pattern, regex in blocked_regexes:
            match = regex.search(blob)
            if match:
                snippet = _snippet_around(blob, match.start(), match.end())
                result.blocked_hits.append((pattern, snippet))
        for pattern, regex in review_regexes:
            match = regex.search(blob)
            if match:
                snippet = _snippet_around(blob, match.start(), match.end())
                result.review_hits.append((pattern, snippet))
    return result


def _snippet_around(blob: str, start: int, end: int, window: int = 40) -> str:
    a = max(0, start - window)
    b = min(len(blob), end + window)
    return blob[a:b].replace("\n", " ")
