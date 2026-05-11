"""Local rule-based provider — deterministic stand-in for judge / fallback review.

Authoritative reference: docs/plan/02 §9.1 (provider table row 'local_rule_based'),
docs/plan/03 DEVF-024.
"""
from __future__ import annotations

import json

from devforge.core.config_loader import ProviderConfig
from devforge.providers.base import AgentRequest, AgentResult


class LocalRuleBasedProvider:
    """Always available. Returns deterministic ``parsed_json`` describing the
    request — it is the orchestrator's responsibility to use it sensibly
    (e.g. as a judge fallback when no LLM provider is healthy).
    """

    def __init__(self, provider_id: str, cfg: ProviderConfig) -> None:
        self.provider_id = provider_id
        self.cfg = cfg

    def healthcheck(self) -> bool:
        return True

    def supports(self, capability: str) -> bool:
        return capability in {"deterministic", "review_only", "json_output", "non_interactive"}

    def run(self, request: AgentRequest) -> AgentResult:
        # Echo a structured response. The orchestrator should not rely on this
        # provider for creative work — it exists to keep the workflow moving
        # when no LLM provider is available.
        payload = {
            "provider": self.provider_id,
            "role": request.role,
            "verdict": "needs_revision" if request.role == "reviewer" else "noop",
            "note": "local_rule_based provider — deterministic placeholder",
            "metadata": dict(request.metadata),
        }
        return AgentResult(
            provider_id=self.provider_id,
            role=request.role,
            success=True,
            stdout=json.dumps(payload),
            parsed_json=payload,
            exit_code=0,
        )
