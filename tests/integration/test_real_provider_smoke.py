"""Real provider smoke tests (DEVF-092). Opt-in.

These tests run only when invoked with ``pytest -m real_provider``. The
collection hook in ``tests/conftest.py`` auto-skips them otherwise. They
additionally skip when the relevant CLI is missing or, for Tier B, when
``DEVFORGE_REAL_PROVIDER_RUN=1`` is not set.

Tier A (token-free): healthcheck + command-builder shape checks.
Tier B (real LLM call): a tiny read-only prompt.

Artifacts written under ``tmp_path / "smoke"`` are auto-cleaned by pytest.
Optional mirror via ``DEVFORGE_SMOKE_ARTIFACT_DIR`` — always redacted by
the secret scanner and truncated to 4 KB.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from devforge.core.config_loader import ProviderConfig
from devforge.evaluators.secret_scanner import scan_diff_and_logs
from devforge.providers.base import (
    FAILURE_AUTH_EXPIRED,
    FAILURE_COMMAND_MISSING,
    FAILURE_RATE_LIMIT,
    FAILURE_USAGE_LIMIT,
    AgentRequest,
)
from devforge.providers.claude_cli import ClaudeCliProvider
from devforge.providers.codex_cli import CodexCliProvider

pytestmark = pytest.mark.real_provider


_ENV_ISSUE_FAILURES = {
    FAILURE_AUTH_EXPIRED,
    FAILURE_USAGE_LIMIT,
    FAILURE_RATE_LIMIT,
    FAILURE_COMMAND_MISSING,
}
_MAX_ARTIFACT_BYTES = 4096


# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------

def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} CLI not installed in PATH")


def _require_real_run() -> None:
    if os.environ.get("DEVFORGE_REAL_PROVIDER_RUN") != "1":
        pytest.skip(
            "Tier B real-CLI invocations are gated behind DEVFORGE_REAL_PROVIDER_RUN=1"
        )


def _save_artifact(tmp_path: Path, name: str, content: str) -> Path:
    """Write a smoke artifact after redacting secrets and truncating.

    Even though ``content`` should be benign (e.g. ``codex --version``),
    we run it through the secret scanner as defense in depth — the same
    way the orchestrator treats agent stdout/stderr.
    """
    scan = scan_diff_and_logs(stdout=content)
    safe = (
        "[REDACTED — secret pattern detected in output]"
        if scan.has_secret
        else content
    )
    payload = safe[:_MAX_ARTIFACT_BYTES]
    p = tmp_path / "smoke" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload, encoding="utf-8")
    extra = os.environ.get("DEVFORGE_SMOKE_ARTIFACT_DIR")
    if extra:
        mirror_dir = Path(extra)
        mirror_dir.mkdir(parents=True, exist_ok=True)
        (mirror_dir / name).write_text(payload, encoding="utf-8")
    return p


def _make_tmp_git_repo(tmp_path: Path) -> Path:
    """Stand up a minimal git repo (no real_provider tests share fixtures with the rest)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# smoke\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "smoke@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "smoke"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )
    return repo


# ===========================================================================
# Tier A — token-free smoke (healthcheck + command shape)
# ===========================================================================

def test_codex_healthcheck_passes_when_installed(tmp_path: Path) -> None:
    _require("codex")
    provider = CodexCliProvider(
        "codex_smoke", ProviderConfig(type="codex_cli", command="codex")
    )
    assert provider.healthcheck() is True
    # Sanity capability set.
    assert provider.supports("edit_files")
    assert provider.supports("run_shell")
    assert not provider.supports("deterministic")
    # Record a tiny artifact so a human running the smoke can see what was checked.
    proc = subprocess.run(
        ["codex", "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    _save_artifact(tmp_path, "codex_version.txt", proc.stdout or proc.stderr or "")


def test_codex_reviewer_command_uses_read_only_sandbox() -> None:
    provider = CodexCliProvider(
        "codex_smoke",
        ProviderConfig(
            type="codex_cli",
            command="codex",
            default_args=[
                "exec",
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "never",
                "--ephemeral",
            ],
        ),
    )
    request = AgentRequest(
        role="reviewer",
        prompt="placeholder",
        cwd=Path("."),
        run_id="r1",
        allow_edit=False,
    )
    cmd = provider._build_command(request)
    # Sandbox is downgraded for read-only roles.
    assert "--sandbox" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    # Forbidden flags must never appear regardless of role.
    for tok in cmd:
        assert "danger-full-access" not in tok
        assert "--yolo" not in tok
        assert "bypass-sandbox" not in tok


def test_codex_implementer_command_uses_workspace_write() -> None:
    provider = CodexCliProvider(
        "codex_smoke", ProviderConfig(type="codex_cli", command="codex")
    )
    request = AgentRequest(
        role="implementer",
        prompt="placeholder",
        cwd=Path("."),
        run_id="r1",
        allow_edit=True,
    )
    cmd = provider._build_command(request)
    assert "exec" in cmd
    assert "--sandbox" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert "--ask-for-approval" in cmd
    assert cmd[cmd.index("--ask-for-approval") + 1] == "never"
    assert "--ephemeral" in cmd


def test_claude_healthcheck_passes_when_installed(tmp_path: Path) -> None:
    _require("claude")
    provider = ClaudeCliProvider(
        "claude_smoke", ProviderConfig(type="claude_cli", command="claude")
    )
    assert provider.healthcheck() is True
    assert provider.supports("read_repo")
    assert provider.supports("review_only")
    proc = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    _save_artifact(tmp_path, "claude_version.txt", proc.stdout or proc.stderr or "")


def test_claude_reviewer_command_is_read_only() -> None:
    provider = ClaudeCliProvider(
        "claude_smoke", ProviderConfig(type="claude_cli", command="claude")
    )
    request = AgentRequest(
        role="reviewer",
        prompt="placeholder",
        cwd=Path("."),
        run_id="r1",
        allow_edit=False,
        expected_output="json",
    )
    cmd = provider._build_command(request)
    assert "--tools" in cmd
    tools_value = cmd[cmd.index("--tools") + 1]
    assert tools_value == "Read,Grep,Glob"
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "plan"
    for tok in cmd:
        assert "dangerously-skip-permissions" not in tok
        assert "bypassPermissions" not in tok


def test_claude_implementer_command_includes_edit_tools() -> None:
    provider = ClaudeCliProvider(
        "claude_smoke", ProviderConfig(type="claude_cli", command="claude")
    )
    request = AgentRequest(
        role="implementer",
        prompt="placeholder",
        cwd=Path("."),
        run_id="r1",
        allow_edit=True,
    )
    cmd = provider._build_command(request)
    assert "--tools" in cmd
    tools_value = cmd[cmd.index("--tools") + 1]
    assert tools_value == "Read,Edit,Write,Bash"
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"


# ===========================================================================
# Tier B — real CLI invocation (token-spending; gated)
# ===========================================================================

def test_codex_real_read_only_prompt(tmp_path: Path) -> None:
    _require("codex")
    _require_real_run()
    repo = _make_tmp_git_repo(tmp_path)
    provider = CodexCliProvider(
        "codex_smoke", ProviderConfig(type="codex_cli", command="codex")
    )
    request = AgentRequest(
        role="reviewer",
        prompt="Briefly list the files at the top level of this repository.",
        cwd=repo,
        run_id="smoke",
        timeout_sec=60,
        allow_edit=False,
    )
    result = provider.run(request)
    _save_artifact(
        tmp_path,
        "codex_real_stdout.txt",
        f"=== stdout ===\n{result.stdout}\n=== stderr ===\n{result.stderr}",
    )
    if not result.success and result.failure_class in _ENV_ISSUE_FAILURES:
        pytest.skip(
            f"codex environment issue ({result.failure_class}): {result.error or 'no detail'}"
        )
    assert result.success, f"codex run failed: {result.failure_class}: {result.error}"
    # Defense in depth: scan the captured output for accidental secret leaks.
    scan = scan_diff_and_logs(stdout=result.stdout, stderr=result.stderr)
    assert not scan.has_secret, "secret pattern detected in real-provider output"


def test_claude_real_read_only_prompt(tmp_path: Path) -> None:
    _require("claude")
    _require_real_run()
    repo = _make_tmp_git_repo(tmp_path)
    provider = ClaudeCliProvider(
        "claude_smoke", ProviderConfig(type="claude_cli", command="claude")
    )
    request = AgentRequest(
        role="reviewer",
        prompt='Reply with ONLY this JSON: {"ok": true}',
        cwd=repo,
        run_id="smoke",
        timeout_sec=60,
        allow_edit=False,
        expected_output="json",
    )
    result = provider.run(request)
    _save_artifact(
        tmp_path,
        "claude_real_stdout.txt",
        f"=== stdout ===\n{result.stdout}\n=== stderr ===\n{result.stderr}",
    )
    if not result.success and result.failure_class in _ENV_ISSUE_FAILURES:
        pytest.skip(
            f"claude environment issue ({result.failure_class}): {result.error or 'no detail'}"
        )
    assert result.success, f"claude run failed: {result.failure_class}: {result.error}"
    scan = scan_diff_and_logs(stdout=result.stdout, stderr=result.stderr)
    assert not scan.has_secret, "secret pattern detected in real-provider output"
