"""Reviewer stage — run a different provider over the candidate diff.

Authoritative reference: docs/plan/02 §6.1, docs/plan/03 DEVF-044.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from devforge.core.config_loader import DevforgeConfig
from devforge.core.prompt_renderer import load_role_prompt, render
from devforge.core.run_context import RunContext
from devforge.providers.base import AgentRequest, AgentResult
from devforge.providers.registry import ProviderRegistry


@dataclass
class ReviewResult:
    provider_id: str
    verdict: str                 # "pass" | "needs_revision" | "reject" | "unknown"
    critical_count: int
    raw_json: dict = field(default_factory=dict)
    agent_result: AgentResult | None = None


def build_reviewer_prompt(task_text: str, acceptance_criteria: str, diff_text: str) -> str:
    template = load_role_prompt("reviewer")
    truncated_diff = diff_text if len(diff_text) <= 20000 else diff_text[:20000] + "\n…(truncated)"
    return render(
        template,
        variables={
            "task": task_text,
            "acceptance_criteria": acceptance_criteria,
            "diff": truncated_diff,
        },
    )


def run_reviewer_stage(
    *,
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    registry: ProviderRegistry,
    reviewer_provider_id: str,
    candidate_dir: Path,
    task_text: str,
    acceptance_criteria: str,
) -> ReviewResult:
    diff_text = ""
    diff_path = candidate_dir / "diff.patch"
    if diff_path.exists():
        diff_text = diff_path.read_text(encoding="utf-8")

    prompt = build_reviewer_prompt(task_text, acceptance_criteria, diff_text)
    (candidate_dir / "review_prompt.md").write_text(prompt, encoding="utf-8")

    provider = registry.get(reviewer_provider_id)
    if provider is None:
        err = AgentResult(
            provider_id=reviewer_provider_id,
            role="reviewer",
            success=False,
            error="reviewer provider not registered",
        )
        return ReviewResult(reviewer_provider_id, "unknown", 0, {}, err)

    request = AgentRequest(
        role="reviewer",
        prompt=prompt,
        cwd=candidate_dir,
        run_id=run_ctx.run_id,
        timeout_sec=cfg.providers[reviewer_provider_id].timeout_sec
        if reviewer_provider_id in cfg.providers
        else 600,
        expected_output="json",
        allow_edit=False,
        allow_shell=False,
        allowed_paths=cfg.file_policy.allowed_paths,
        blocked_paths=cfg.file_policy.blocked_paths,
        metadata={"workflow": run_ctx.workflow},
    )
    result = provider.run(request)

    (candidate_dir / "review_stdout.log").write_text(result.stdout, encoding="utf-8")
    (candidate_dir / "review_stderr.log").write_text(result.stderr, encoding="utf-8")

    parsed = result.parsed_json
    if parsed is None:
        parsed = _try_parse_json(result.stdout)

    verdict = "unknown"
    critical_count = 0
    if isinstance(parsed, dict):
        v = parsed.get("verdict")
        if v in ("pass", "needs_revision", "reject"):
            verdict = v
        critical = parsed.get("critical_issues", [])
        if isinstance(critical, list):
            critical_count = len(critical)

    review = ReviewResult(
        provider_id=reviewer_provider_id,
        verdict=verdict,
        critical_count=critical_count,
        raw_json=parsed if isinstance(parsed, dict) else {},
        agent_result=result,
    )
    (candidate_dir / "review.json").write_text(
        json.dumps(
            {
                "provider_id": review.provider_id,
                "verdict": review.verdict,
                "critical_count": review.critical_count,
                "raw": review.raw_json,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return review


def _try_parse_json(stdout: str) -> dict | None:
    stripped = stdout.strip()
    if not stripped:
        return None
    # Some CLIs wrap JSON inside fences or noise — try a few strategies.
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
