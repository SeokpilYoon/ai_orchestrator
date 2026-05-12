"""Implementer stage — run a provider in a candidate worktree.

Authoritative reference: docs/plan/02 §6.1, docs/plan/03 DEVF-043.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from devforge.core.config_loader import DevforgeConfig
from devforge.core.prompt_renderer import load_role_prompt, render
from devforge.core.run_context import RunContext
from devforge.git.diff_collector import DiffArtifact, collect_diff
from devforge.git.worktree_manager import Worktree, WorktreeManager
from devforge.providers.base import AgentRequest, AgentResult
from devforge.providers.registry import ProviderRegistry


@dataclass
class CandidateResult:
    candidate_id: str
    provider_id: str
    worktree: Worktree | None
    agent_result: AgentResult
    diff: DiffArtifact | None


def build_implementer_prompt(
    task_text: str,
    repo_context: str,
    cfg: DevforgeConfig,
    acceptance_criteria: str,
) -> str:
    template = load_role_prompt("implementer")
    return render(
        template,
        variables={
            "task": task_text,
            "repo_context": repo_context,
            "constraints": "- Stay inside the worktree.\n- Run only validation commands the user configured.",
            "allowed_paths": "\n".join(f"- {p}" for p in cfg.file_policy.allowed_paths) or "(any)",
            "blocked_paths": "\n".join(f"- {p}" for p in cfg.file_policy.blocked_paths),
            "acceptance_criteria": acceptance_criteria,
        },
    )


def run_implementer_stage(
    *,
    cfg: DevforgeConfig,
    run_ctx: RunContext,
    registry: ProviderRegistry,
    worktree_mgr: WorktreeManager,
    provider_ids: list[str],
    task_text: str,
    repo_context: str,
    acceptance_criteria: str,
    candidate_ids: list[str] | None = None,
) -> list[CandidateResult]:
    """Run each selected provider in its own worktree and collect diffs.

    ``candidate_ids``, when supplied, must match the length of ``provider_ids``
    and provides per-provider candidate ids (e.g. ``TASK-001`` for backlog
    loops). Defaults to ``candidate_id == provider_id``.
    """
    prompt = build_implementer_prompt(task_text, repo_context, cfg, acceptance_criteria)

    if candidate_ids is not None and len(candidate_ids) != len(provider_ids):
        raise ValueError("candidate_ids must match the length of provider_ids")

    candidates: list[CandidateResult] = []
    for i, pid in enumerate(provider_ids):
        candidate_id = candidate_ids[i] if candidate_ids is not None else pid
        cand_dir = run_ctx.candidate_dir(candidate_id)
        (cand_dir / "prompt.md").write_text(prompt, encoding="utf-8")

        provider = registry.get(pid)
        if provider is None:
            err = AgentResult(
                provider_id=pid,
                role="implementer",
                success=False,
                error=f"provider not registered: {pid}",
            )
            _save_agent_result(cand_dir, err)
            candidates.append(CandidateResult(candidate_id, pid, None, err, None))
            continue

        try:
            worktree = worktree_mgr.create(
                run_id=run_ctx.run_id,
                candidate_id=candidate_id,
                base_branch=cfg.project.default_branch,
            )
        except Exception as exc:
            err = AgentResult(
                provider_id=pid,
                role="implementer",
                success=False,
                error=f"worktree creation failed: {exc}",
            )
            _save_agent_result(cand_dir, err)
            candidates.append(CandidateResult(candidate_id, pid, None, err, None))
            continue

        request = AgentRequest(
            role="implementer",
            prompt=prompt,
            cwd=worktree.path,
            run_id=run_ctx.run_id,
            timeout_sec=cfg.providers[pid].timeout_sec if pid in cfg.providers else 900,
            expected_output="text",
            allow_edit=True,
            allow_shell=True,
            allowed_paths=cfg.file_policy.allowed_paths,
            blocked_paths=cfg.file_policy.blocked_paths,
            metadata={"workflow": run_ctx.workflow, "candidate_id": candidate_id},
        )

        result = provider.run(request)
        _save_agent_result(cand_dir, result)

        diff = collect_diff(
            worktree_path=worktree.path,
            base_branch=cfg.project.default_branch,
            output_dir=cand_dir,
        )
        candidates.append(CandidateResult(candidate_id, pid, worktree, result, diff))

    return candidates


def _save_agent_result(cand_dir: Path, result: AgentResult) -> None:
    (cand_dir / "stdout.log").write_text(result.stdout, encoding="utf-8")
    (cand_dir / "stderr.log").write_text(result.stderr, encoding="utf-8")
    (cand_dir / "agent_result.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def re_run_implementer_in_existing_worktree(
    *,
    provider,
    worktree: Worktree,
    run_id: str,
    cand_dir: Path,
    prompt: str,
    timeout_sec: int,
    allowed_paths: list[str],
    blocked_paths: list[str],
    metadata: dict | None = None,
) -> AgentResult:
    """Re-run the implementer for a revision pass in an EXISTING worktree.

    DEVF-045. Overwrites ``prompt.md`` / ``stdout.log`` / ``stderr.log`` /
    ``agent_result.json`` at the top of ``cand_dir``. Callers are expected
    to snapshot the previous iteration before calling this.
    """
    (cand_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    request = AgentRequest(
        role="implementer",
        prompt=prompt,
        cwd=worktree.path,
        run_id=run_id,
        timeout_sec=timeout_sec,
        expected_output="text",
        allow_edit=True,
        allow_shell=True,
        allowed_paths=allowed_paths,
        blocked_paths=blocked_paths,
        metadata=dict(metadata or {}),
    )
    result = provider.run(request)
    _save_agent_result(cand_dir, result)
    return result
