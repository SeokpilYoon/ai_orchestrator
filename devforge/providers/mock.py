"""Mock provider for tests and dogfood smoke runs.

Not part of the production provider set — registered manually in tests/fixtures.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from devforge.providers.base import AgentRequest, AgentResult, AgentRole


class MockProvider:
    """Deterministic provider that replays scripted behavior.

    ``behavior`` may be:
    - an :class:`AgentResult` returned as-is
    - a callable ``(AgentRequest) -> AgentResult``
    - a callable ``(AgentRequest) -> None`` that performs filesystem changes,
      in which case a generic success result is returned.
    """

    def __init__(
        self,
        provider_id: str = "mock",
        *,
        behavior: AgentResult | Callable[[AgentRequest], AgentResult | None] | None = None,
        healthy: bool = True,
        capabilities: set[str] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._behavior = behavior
        self._healthy = healthy
        self._capabilities = capabilities or {
            "read_repo",
            "edit_files",
            "run_shell",
            "non_interactive",
            "json_output",
            "review_only",
        }

    def healthcheck(self) -> bool:
        return self._healthy

    def supports(self, capability: str) -> bool:
        return capability in self._capabilities

    def run(self, request: AgentRequest) -> AgentResult:
        if self._behavior is None:
            return _default_success(self.provider_id, request)
        if isinstance(self._behavior, AgentResult):
            return self._behavior
        result = self._behavior(request)
        if isinstance(result, AgentResult):
            return result
        return _default_success(self.provider_id, request)


def _default_success(provider_id: str, request: AgentRequest) -> AgentResult:
    return AgentResult(
        provider_id=provider_id,
        role=request.role,
        success=True,
        stdout="mock provider ran successfully",
        exit_code=0,
    )


# Helpers for common test scenarios -------------------------------------------------

def write_files_behavior(
    files: dict[str, str], role: AgentRole = "implementer"
) -> Callable[[AgentRequest], AgentResult]:
    """Return a behavior that writes ``files`` into ``request.cwd``."""

    def _behavior(request: AgentRequest) -> AgentResult:
        changed: list[str] = []
        for rel, content in files.items():
            target = Path(request.cwd) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            changed.append(rel)
        return AgentResult(
            provider_id="mock",
            role=role,
            success=True,
            stdout=f"wrote {len(changed)} files",
            changed_files=changed,
            exit_code=0,
        )

    return _behavior
