"""Helpers shared between integration tests.

Lets us install MockProvider instances into the ProviderRegistry without
re-implementing the monkeypatch dance in each test.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from devforge.core.config_loader import DevforgeConfig, ProviderConfig
from devforge.providers.base import AgentResult
from devforge.providers.local_rule_based import LocalRuleBasedProvider
from devforge.providers.mock import MockProvider
from devforge.providers.registry import ProviderRegistry


def commit_all(repo: Path) -> None:
    """Stage and commit every change, skipping silently if there's nothing to commit."""
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=str(repo), check=False
    )
    if staged.returncode == 0:
        return
    subprocess.run(
        ["git", "commit", "-m", "step"], cwd=str(repo), check=True, capture_output=True
    )


def install_mock_providers(
    *,
    impl_behavior: Callable | AgentResult | None,
    review_behavior: Callable | AgentResult | None,
    monkeypatch: pytest.MonkeyPatch,
    extra_impl: dict[str, Callable | AgentResult | None] | None = None,
) -> None:
    """Replace ``ProviderRegistry.from_config`` with one wired to mocks.

    ``extra_impl`` registers additional MockProviders for tournament/fallback tests.
    """

    def patched(cfg: DevforgeConfig) -> ProviderRegistry:  # noqa: ARG001
        reg = ProviderRegistry()
        reg.register(MockProvider("mock_impl", behavior=impl_behavior))
        reg.register(MockProvider("mock_review", behavior=review_behavior))
        for pid, behavior in (extra_impl or {}).items():
            reg.register(MockProvider(pid, behavior=behavior))
        reg.register(
            LocalRuleBasedProvider(
                "local_rule_based", ProviderConfig(type="local_rule_based")
            )
        )
        return reg

    monkeypatch.setattr(ProviderRegistry, "from_config", staticmethod(patched))


def review_payload(verdict: str, critical: list[str] | None = None) -> str:
    """Build a JSON string a mock reviewer can return."""
    import json

    return json.dumps(
        {
            "verdict": verdict,
            "requirement_coverage": 1.0 if verdict == "pass" else 0.5,
            "critical_issues": critical or [],
            "major_issues": [],
            "minor_issues": [],
            "test_concerns": [],
            "security_concerns": [],
            "recommended_revision_prompt": "",
        }
    )


def review_behavior(verdict: str, critical: list[str] | None = None) -> Callable:
    payload = review_payload(verdict, critical)

    def _behave(request):  # noqa: ARG001
        return AgentResult(
            provider_id="mock_review",
            role="reviewer",
            success=True,
            stdout=payload,
            exit_code=0,
        )

    return _behave
