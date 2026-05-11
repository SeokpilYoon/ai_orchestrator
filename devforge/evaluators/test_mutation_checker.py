"""Test mutation checker — detect test deletion/weakening.

Authoritative reference: docs/plan/02 §5.9, docs/plan/03 DEVF-036.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TEST_PATH = re.compile(r"(^|/)tests?/.*\.(py|ts|tsx|js|jsx)$", re.IGNORECASE)
_SKIP_ADDED = re.compile(r"\+\s*(@pytest\.mark\.(skip|xfail)|@unittest\.skip|it\.skip|test\.skip)")
_ASSERT_WEAKENED = re.compile(r"\+\s*assert\s+(True|true|1)\s*(?:#|$|,)")
_ASSERT_OR_TRUE = re.compile(r"\+\s*assert\s+.+\s+or\s+(True|true|1)\b")
_THRESHOLD_RELAXED = re.compile(r"-\s*assert\s+.+>=\s*(0\.\d+)\s*\n\+\s*assert\s+.+>=\s*(0\.\d+)")


@dataclass
class TestMutationResult:
    __test__ = False   # tell pytest not to collect this as a test class

    deleted_tests: list[str] = field(default_factory=list)
    weakened_tests: list[str] = field(default_factory=list)   # file paths
    suspicious_changes: list[str] = field(default_factory=list)  # snippets

    @property
    def has_concern(self) -> bool:
        return bool(self.deleted_tests or self.weakened_tests or self.suspicious_changes)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "deleted_tests": self.deleted_tests,
            "weakened_tests": self.weakened_tests,
            "suspicious_changes": self.suspicious_changes,
        }


def check_test_mutation(diff_text: str, changed_files: list[str]) -> TestMutationResult:
    """Inspect the diff for signs that tests were deleted or weakened."""
    result = TestMutationResult()

    for f in changed_files:
        if _TEST_PATH.search(f) and _looks_like_full_delete(diff_text, f):
            result.deleted_tests.append(f)

    for match in _SKIP_ADDED.finditer(diff_text):
        result.suspicious_changes.append(match.group(0).strip())

    for match in _ASSERT_WEAKENED.finditer(diff_text):
        result.suspicious_changes.append(match.group(0).strip())

    for match in _ASSERT_OR_TRUE.finditer(diff_text):
        result.suspicious_changes.append(match.group(0).strip())

    for match in _THRESHOLD_RELAXED.finditer(diff_text):
        try:
            before = float(match.group(1))
            after = float(match.group(2))
            if after < before:
                result.suspicious_changes.append(match.group(0).strip())
        except ValueError:
            continue

    # If a test file appears in changed_files and any suspicious change exists, flag it
    test_files = [f for f in changed_files if _TEST_PATH.search(f)]
    if result.suspicious_changes and test_files:
        result.weakened_tests.extend(test_files)

    return result


def _looks_like_full_delete(diff_text: str, path: str) -> bool:
    """Heuristic: a unified-diff hunk with ``deleted file mode`` for ``path``."""
    marker = "deleted file mode"
    a_path = f"--- a/{path}"
    # Scan only the section relevant to this file.
    idx = diff_text.find(a_path)
    if idx == -1:
        return False
    window = diff_text[max(0, idx - 200) : idx + 200]
    return marker in window
