"""File policy checker.

Authoritative reference: docs/plan/01 §11.1, docs/plan/02 §5.9, docs/plan/03 DEVF-033.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from devforge.core.config_loader import FilePolicy


@dataclass
class FilePolicyResult:
    allowed: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    require_review: list[str] = field(default_factory=list)
    outside_allowed: list[str] = field(default_factory=list)

    @property
    def has_blocked(self) -> bool:
        return bool(self.blocked)

    @property
    def has_review(self) -> bool:
        return bool(self.require_review)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "allowed": self.allowed,
            "blocked": self.blocked,
            "require_review": self.require_review,
            "outside_allowed": self.outside_allowed,
        }


def _normalize(path: str) -> str:
    """Normalize a path for glob matching: posix separators, no leading ``./``."""
    p = PurePosixPath(path.replace("\\", "/"))
    parts = [seg for seg in p.parts if seg not in ("", ".")]
    return "/".join(parts)


def _matches_any(path: str, patterns: list[str]) -> bool:
    norm = _normalize(path)
    for pattern in patterns:
        npat = _normalize(pattern)
        if fnmatch.fnmatch(norm, npat):
            return True
        # ``foo/**`` should also match ``foo`` itself and any depth — fnmatch handles
        # the recursive form when we normalize separators.
        if npat.endswith("/**") and (norm == npat[:-3] or norm.startswith(npat[:-3] + "/")):
            return True
    return False


def check_file_policy(changed_files: list[str], policy: FilePolicy) -> FilePolicyResult:
    """Classify each changed file according to the policy."""
    result = FilePolicyResult()
    for f in changed_files:
        if _matches_any(f, policy.blocked_paths):
            result.blocked.append(f)
            continue
        if _matches_any(f, policy.require_human_review_if_modified):
            result.require_review.append(f)
        if policy.allowed_paths and not _matches_any(f, policy.allowed_paths):
            result.outside_allowed.append(f)
        else:
            result.allowed.append(f)
    return result
