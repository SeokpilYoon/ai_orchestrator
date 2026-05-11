# AI Dev Orchestrator — Claude Code 운영 규칙

## 프로젝트 본질

이 repo는 **Claude Code CLI와 Codex CLI를 bounded worker로 부리는 local-first orchestrator(`devforge`)를 만드는 코드베이스**다.

메타한 성격을 인지하라:

- 우리가 만드는 **product의 role**(implementer, reviewer, judge 등)은 런타임에 devforge가 spawn하는 역할
- 이 `.claude/`의 **harness agent**는 이 코드베이스를 *개발*하는 도구
- 둘은 별개다. 혼동하지 말 것

## 권위 있는 사양 (단일 출처)

다음 3개 파일은 제품 사양의 frozen source of truth다.

| 파일 | 용도 |
|---|---|
| `docs/plan/01_개발_계획서_AI_Dev_Orchestrator.md` | 제품 목표·범위·MVP 단계 |
| `docs/plan/02_아키텍처_설계서_AI_Dev_Orchestrator.md` | 모듈 구성·인터페이스·workflow DAG |
| `docs/plan/03_Task_Plan_AI_Dev_Orchestrator.md` | DEVF-xxx 작업 분해·구현 순서·DoD |

**규칙**: 사용자의 명시 승인 없이 `docs/plan/` 하위 어떤 파일도 수정하지 않는다. 차이가 보이면 코드를 수정하거나 사용자에게 보고한다.

## 스택 / 컨벤션

- Python 3.11+
- Typer (CLI), Pydantic v2 (schema), pytest (test), ruff (lint), mypy (type)
- SQLite (state store), 이후 FastAPI + React (dashboard, Phase 6+)

## 모듈 경계

```
devforge/
  cli.py
  core/        # workflow_engine, state_store, role_router, policy_engine, config_loader, prompt_renderer
  providers/   # base, codex_cli, claude_cli, openai_api, anthropic_agent_sdk, local_rule_based
  git/         # worktree_manager, diff_collector, patch_manager
  evaluators/  # validation_runner, file/command/secret policy, test_mutation, score, acceptance
  workflows/   # *.yaml DAG 정의
  prompts/     # roles/*.md, schemas/*.json
  project_profiles/
  dashboard/
tests/
```

경계 침범(예: `providers/`가 `evaluators/`를 직접 import) 금지. `core/`만이 모듈 간 조율 위치다.

## 작업 규칙

- 새 작업은 `_workspace/tasks/<DEVF-xxx>/`에서 시작 (`/devf-task-start` 사용)
- 커밋 메시지: `DEVF-xxx: <요약>` 포맷 권장
- `providers/`, `evaluators/`, `git/` 수정 시 `policy-security-auditor` 호출 필수
- LLM provider에 의존하는 코드는 mock provider로 테스트 필수. CI/테스트는 실제 Claude/Codex CLI를 호출하지 않는다
- 큰 변경은 Claude Code의 worktree 모드 사용을 권장 (프로덕트 철학과 일치)

## 산출물 위치

| 종류 | 위치 | git 추적 |
|---|---|---|
| 코드 | `devforge/` | yes |
| 테스트 | `tests/` | yes |
| 영구 문서 | `docs/` | yes |
| harness 스크래치 / 작업 메모 | `_workspace/` | no (README만 yes) |
| 런타임 산출물 | `.orchestrator/` | no |

`docs/`, `devforge/`, `tests/` 외부에 영구 산출물을 만들지 마라.

## 안전 (dogfood — 자기 정책 준수)

프로덕트가 적용할 정책을 harness도 그대로 따른다.

**blocked paths** (어떤 이유로도 수정/생성/커밋 금지):
- `.env`, `.env.*`
- `secrets/**`
- `.git/**` (직접 편집)

**require human review** (수정 전 사용자 확인):
- `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `poetry.lock`, `uv.lock`
- `Dockerfile`, `docker-compose.yml`
- `infra/**`, `migrations/**`
- `docs/plan/**`

**금지 명령**:
- `rm -rf`, `git push`, `git reset --hard`, `git checkout --`
- `curl ... | sh`, `wget ... | sh`
- `sudo`, `docker system prune`

**민감 정보 기록 금지**:
- API key·토큰·credential·개인정보를 어떤 파일에도 기록하지 않는다
- 예시·문서는 항상 placeholder 사용: `OPENAI_API_KEY=<set-via-env>`, `ANTHROPIC_API_KEY=<set-via-env>`
- 사용자의 이메일·이름·실제 경로 등도 fictionalize 또는 placeholder

## Claude Code 도구 사용

- 에이전트별 권한은 각 agent 정의의 `tools` 필드를 따른다
- 단계 추적은 TaskCreate 사용
- 큰 작업은 Plan 모드로 먼저 합의

## 빠른 참조

- 다음 태스크 시작 → `/devf-task-start DEVF-xxx`
- 코드가 사양과 어긋난 듯 → `/devf-plan-sync`
- 변경분이 우리 정책 통과? → `/devf-policy-dogfood`
- 전체 workflow가 죽지 않는지 → `/devf-mock-run`
