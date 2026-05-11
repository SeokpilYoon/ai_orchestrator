"""Shared pytest fixtures and collection hooks."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devforge.core.config_loader import (
    CommandPolicy,
    DevforgeConfig,
    FilePolicy,
    ProjectConfig,
    ProviderConfig,
    RoleConfig,
    ScoringConfig,
    StopConditions,
    ValidationCommands,
    ValidationConfig,
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip ``real_provider`` tests unless explicitly requested via ``-m real_provider``.

    Setting ``addopts = -m 'not real_provider'`` would conflict with a user-supplied
    ``-m real_provider`` (pytest ANDs them, yielding zero tests). A collection
    hook avoids that pitfall.
    """
    markexpr = config.getoption("-m", default="") or ""
    if "real_provider" in markexpr:
        return  # user explicitly asked for them
    skip = pytest.mark.skip(
        reason="real_provider tests require `pytest -m real_provider`"
    )
    for item in items:
        if "real_provider" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a tiny git repo at ``tmp_path`` and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def base_config(tmp_repo: Path) -> DevforgeConfig:
    return DevforgeConfig(
        project=ProjectConfig(
            name="testrepo",
            root=str(tmp_repo),
            default_branch="main",
            worktree_root=str(tmp_repo.parent / "worktrees"),
            profile="python_fastapi",
        ),
        providers={
            "mock_impl": ProviderConfig(type="mock", enabled=True),
            "mock_review": ProviderConfig(type="mock", enabled=True),
            "local_rule_based": ProviderConfig(type="local_rule_based", enabled=True),
        },
        roles={
            "implementer": RoleConfig(provider_order=["mock_impl"]),
            "reviewer": RoleConfig(
                provider_order=["mock_review"],
                avoid_same_provider_as_implementer=True,
            ),
            "judge": RoleConfig(provider_order=["local_rule_based"]),
        },
        validation=ValidationConfig(
            commands=ValidationCommands(test="true", lint="true"),
            default_timeout_sec=10,
        ),
        file_policy=FilePolicy(
            allowed_paths=["src/**", "app/**", "tests/**", "README.md", "*.py"],
            blocked_paths=[".git/**", ".env", ".env.*", "secrets/**"],
            require_human_review_if_modified=["Dockerfile", "package-lock.json"],
        ),
        command_policy=CommandPolicy(
            blocked_patterns=["rm -rf", "git push", "curl * | sh", "sudo"],
            require_human_review=["pip install", "npm install"],
        ),
        scoring=ScoringConfig(),
        stop_conditions=StopConditions(),
    )
