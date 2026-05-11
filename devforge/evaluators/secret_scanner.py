"""Secret scanner — detect API keys / tokens / private keys in text blobs.

Authoritative reference: docs/plan/02 §10.2, docs/plan/03 DEVF-035.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_api_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    (
        "generic_password",
        re.compile(r"""(?i)(password|passwd|pwd|secret)\s*[:=]\s*['"]([^'"]{6,})['"]"""),
    ),
    (
        "generic_token",
        re.compile(r"""(?i)(api[_-]?token|access[_-]?token)\s*[:=]\s*['"]([^'"]{12,})['"]"""),
    ),
]


_ENV_FILE_MARKERS = ("OPENAI_API_KEY=", "ANTHROPIC_API_KEY=", "AWS_SECRET_ACCESS_KEY=")


@dataclass
class SecretHit:
    kind: str
    location: str        # filename or "stdout"/"stderr"
    line: int            # 1-indexed; 0 if unknown


@dataclass
class SecretScanResult:
    hits: list[SecretHit] = field(default_factory=list)
    env_file_modified: bool = False

    @property
    def has_secret(self) -> bool:
        return bool(self.hits) or self.env_file_modified

    def to_dict(self) -> dict[str, object]:
        return {
            "has_secret": self.has_secret,
            "env_file_modified": self.env_file_modified,
            "hits": [{"kind": h.kind, "location": h.location, "line": h.line} for h in self.hits],
        }


def scan_text(blob: str, location: str) -> list[SecretHit]:
    """Scan a single blob and return found secrets (values themselves are NOT recorded)."""
    hits: list[SecretHit] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(blob):
            line = blob.count("\n", 0, match.start()) + 1
            hits.append(SecretHit(kind=kind, location=location, line=line))
    return hits


def scan_diff_and_logs(
    *,
    diff_text: str = "",
    stdout: str = "",
    stderr: str = "",
    changed_files: list[str] | None = None,
) -> SecretScanResult:
    result = SecretScanResult()

    # Look for raw secret patterns
    if diff_text:
        result.hits.extend(scan_text(diff_text, "diff"))
        if any(marker in diff_text for marker in _ENV_FILE_MARKERS):
            result.env_file_modified = True
    if stdout:
        result.hits.extend(scan_text(stdout, "stdout"))
    if stderr:
        result.hits.extend(scan_text(stderr, "stderr"))

    # Flag explicit .env modifications regardless of content
    for f in changed_files or []:
        norm = f.replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        base = norm.rsplit("/", 1)[-1]
        if base == ".env" or base.startswith(".env."):
            result.env_file_modified = True

    return result
