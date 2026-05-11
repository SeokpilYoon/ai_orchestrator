# AI Dev Orchestrator 개발 계획서

문서 버전: v1.0  
작성일: 2026-05-11  
대상 제품명: **AI Dev Orchestrator**  
목표 사용자: Claude Code와 Codex를 구독형 또는 API 기반으로 병행 사용하는 개인 개발자/소규모 팀

---

## 1. 프로젝트 개요

AI Dev Orchestrator는 Claude Code, Codex, 향후 추가 가능한 CLI/SDK 기반 코딩 에이전트를 **역할별 작업자(worker)** 로 사용하고, 상위에서 기획·설계·구현·검토·테스트·개선·채택·롤백을 관리하는 local-first 개발 자동화 시스템이다.

핵심 목표는 다음 두 가지다.

1. **기능 개발 루프 자동화**
   - 기존 코드베이스에 기능 추가, 버그 수정, 리팩터링, 테스트 보강을 수행한다.
   - Claude Code와 Codex 중 사용 가능한 provider를 선택하거나 둘 다 병렬 실행한다.
   - 결과물을 build/test/lint/typecheck/review로 검증하고 더 나은 patch만 채택한다.

2. **기획서 기반 end-to-end 앱 제작**
   - 애플리케이션 기획서 또는 PRD를 입력으로 받아 요구사항, 화면, 사용자 흐름, 데이터 모델, API contract, backlog를 생성한다.
   - scaffold → vertical slice → 기능 backlog 구현 → QA → 문서화 → 배포 준비까지 단계적으로 진행한다.

---

## 2. 공식 문서 기준 전제

2026-05-11 기준으로 다음 전제를 설계에 반영한다.

### 2.1 Codex

- Codex CLI는 로컬 터미널에서 실행되는 코딩 에이전트이며, 선택된 디렉터리의 코드를 읽고 수정하고 명령을 실행할 수 있다.
- Codex CLI는 ChatGPT Plus, Pro, Business, Edu, Enterprise 플랜에 포함된다.
- `codex exec`는 비대화형 실행을 위한 공식 명령이며, CI, pre-merge check, scheduled job 같은 스크립트 기반 workflow에 사용할 수 있다.
- Codex CLI는 ChatGPT 구독 기반 로그인과 API key 기반 로그인을 모두 지원한다.
- 공식 문서는 programmatic workflow, 특히 CI/CD 성격의 사용에는 API key 인증을 권장한다.
- Codex CLI는 `--sandbox`, `--ask-for-approval`, `--cd`, `--ephemeral` 등 자동화에 필요한 플래그를 제공한다.

### 2.2 Claude Code

- Claude Code는 터미널 기반 코딩 workflow를 위한 command line tool이다.
- Pro/Max 구독 사용자는 Claude Code를 Claude 계정과 연결해 사용할 수 있으며, Claude 웹/앱 사용량과 Claude Code 사용량은 같은 usage limit을 공유한다.
- Claude Code CLI는 `claude -p` print mode, `--output-format json`, `--permission-mode`, `--tools`, `--max-turns`, `--worktree` 등 자동화에 필요한 옵션을 제공한다.
- Claude Agent SDK는 Claude Code의 agent loop, file read/edit, command execution, hooks, permissions, session 기능을 Python/TypeScript에서 사용할 수 있게 한다.
- Anthropic 문서상 제3자가 별도 승인 없이 claude.ai login/rate limit을 자기 제품에서 제공해서는 안 되므로, 공개 SaaS 제품화 시에는 API key 기반 경로를 기본값으로 잡아야 한다.

### 2.3 설계상 결론

따라서 초기 버전은 다음 원칙을 따른다.

```text
local-first CLI orchestrator
→ 사용자의 로컬 Claude Code/Codex 로그인 세션 사용 가능
→ provider adapter를 통해 구독형/ API형을 분리
→ 공개 제품화 또는 서버 자동화는 API key provider를 우선
→ 모든 agent 작업은 git worktree에서 격리
→ 최종 판단은 LLM이 아니라 deterministic evaluator + policy engine이 수행
```

---

## 3. 문제 정의

Claude Code와 Codex는 각각 강력하지만, 기본 사용 방식은 대체로 다음에 가깝다.

```text
사용자 명령 → agent 실행 → 결과 반환
```

이 방식의 한계는 다음이다.

| 문제 | 설명 |
|---|---|
| 단발성 실행 | 결과 검토와 재개선 루프가 자동으로 이어지지 않는다. |
| provider 종속 | Claude 또는 Codex 중 하나에 사용량 제한, 인증 문제, 품질 편차가 발생하면 workflow가 중단된다. |
| 자기검토 문제 | 구현한 agent가 자기 결과를 긍정적으로 평가할 수 있다. |
| 검증 부족 | “그럴듯한 코드”와 “실제로 빌드·테스트 통과하는 코드”를 구분하기 어렵다. |
| 기획서 기반 개발 부재 | PRD를 바로 코드 생성에 넣으면 요구사항 누락, 과도한 설계, 불완전한 앱이 되기 쉽다. |
| 롤백 어려움 | agent가 많은 파일을 수정한 뒤 실패하면 수동 복구가 필요하다. |

---

## 4. 목표

## 4.1 제품 목표

AI Dev Orchestrator는 다음을 가능하게 해야 한다.

```text
PRD / 기능 요청 / 버그 리포트 / 리팩터링 목표
  ↓
요구사항 정규화
  ↓
작업 계획 생성
  ↓
Claude/Codex 구현 후보 생성
  ↓
빌드·테스트·정적분석·요구사항 검증
  ↓
교차 리뷰
  ↓
개선 루프
  ↓
최고 patch 채택 또는 폐기
  ↓
보고서 생성
```

## 4.2 핵심 기능 목표

1. Provider 선택 및 fallback
   - 구현 provider: Claude, Codex, API provider 중 선택
   - 검토 provider: Claude, Codex, API provider 중 선택
   - 기획 provider: Claude, Codex, API provider 중 선택
   - 구독 사용량, 인증, timeout, 실패 시 자동 fallback

2. 역할 기반 orchestration
   - Product Manager
   - System Architect
   - Technical Planner
   - Implementer
   - Reviewer
   - QA Engineer
   - Security Reviewer
   - Judge
   - Release Manager

3. workflow 지원
   - `feature`
   - `bugfix`
   - `refactor`
   - `code_review_only`
   - `app_from_prd`
   - `research_optimize`

4. 검증 루프
   - build
   - unit test
   - integration test
   - e2e test
   - lint
   - typecheck
   - security scan
   - changed-file policy
   - command policy
   - acceptance criteria coverage
   - LLM cross-review

5. 산출물 관리
   - git worktree
   - patch
   - run log
   - score
   - review report
   - final report
   - rollback

---

## 5. 비목표

초기 MVP에서 하지 않을 것:

| 항목 | 이유 |
|---|---|
| 완전 무인 production 배포 | command/file/security gate가 안정화되기 전까지 위험하다. |
| 외부 사용자의 Claude/ChatGPT 구독을 대신 중계하는 SaaS | provider 약관 및 인증 경계가 복잡하다. |
| 모든 프로젝트 유형 자동 지원 | 먼저 Python/Node/Unity/FastAPI 등 제한된 profile부터 지원한다. |
| agent가 test threshold나 CI 기준을 수정하도록 허용 | verifier 조작 위험이 있다. |
| 대규모 dependency 변경 자동 허용 | lockfile, Docker, infra 변경은 human review가 필요하다. |

---

## 6. 사용자 시나리오

## 6.1 기존 코드베이스 기능 개발

```bash
devforge run \
  --workflow feature \
  --task task.md \
  --implementer codex_sub_cli \
  --reviewer claude_sub_cli
```

예상 동작:

1. `task.md`를 요구사항으로 정규화한다.
2. repository context를 읽는다.
3. isolated git worktree를 생성한다.
4. Codex가 구현한다.
5. build/test/lint/typecheck를 실행한다.
6. Claude가 diff를 검토한다.
7. judge가 accept/revise/discard를 결정한다.
8. 실패 시 revision prompt를 생성해 반복한다.
9. 최종 patch와 report를 저장한다.

## 6.2 Claude/Codex tournament

```bash
devforge run \
  --workflow refactor \
  --task refactor.md \
  --tournament claude_sub_cli,codex_sub_cli
```

예상 동작:

```text
same task
 ├─ candidate A: Claude Code
 └─ candidate B: Codex

각 candidate 검증
→ 반대 provider로 cross-review
→ deterministic score 계산
→ best patch 선택
→ 나머지 폐기
```

## 6.3 기획서 기반 앱 제작

```bash
devforge create-app \
  --from app_plan.md \
  --stack react-fastapi-postgres \
  --implementer codex_sub_cli \
  --reviewer claude_sub_cli
```

예상 산출물:

```text
generated/
  01_product_summary.md
  02_requirements.json
  03_assumptions.md
  04_user_stories.json
  05_screen_inventory.json
  06_user_flows.md
  07_architecture.md
  08_data_model.md
  09_api_contract.yaml
  10_acceptance_criteria.json
  11_backlog.json
  12_test_plan.md
```

앱 구현 순서:

```text
PRD 정규화
→ MVP 범위 freeze
→ architecture 설계
→ scaffold 생성
→ vertical slice 구현
→ backlog item별 구현/검증
→ e2e test
→ release packaging
```

---

## 7. 제품 범위

## 7.1 MVP-1: 단일 workflow 실행기

목표:

- 하나의 task를 읽고 하나의 provider를 실행한다.
- worktree를 만들고 patch/test/review/report를 저장한다.

기능:

| 기능 | 설명 |
|---|---|
| config loader | `devforge.yaml` 읽기 |
| provider registry | Claude/Codex provider healthcheck |
| git worktree manager | candidate별 독립 worktree 생성 |
| agent runner | CLI provider 실행 |
| test runner | project profile의 validation command 실행 |
| diff collector | patch, changed files, diff stat 저장 |
| simple judge | build/test/file policy 기준 accept/revise/discard |
| report writer | final_report.md 생성 |

## 7.2 MVP-2: provider 선택 및 fallback

목표:

- 구현/검토/기획 provider를 역할별로 선택한다.
- 구독형 provider 실패 시 다른 provider로 fallback한다.

기능:

| 기능 | 설명 |
|---|---|
| role router | role별 provider priority 적용 |
| fallback policy | auth failure, usage limit, timeout, malformed output 처리 |
| cross-review | 구현 provider와 다른 provider로 리뷰 |
| tournament mode | Claude/Codex 동시 candidate 생성 |
| usage warning parser | stdout/stderr 기반 사용량 경고 탐지 |

## 7.3 MVP-3: app_from_prd workflow

목표:

- 기획서를 정규화하고 end-to-end 앱 제작 workflow를 실행한다.

기능:

| 기능 | 설명 |
|---|---|
| PRD intake | 기획서 요약, 요구사항, unknowns 추출 |
| requirement inventory | FR/NFR/acceptance criteria 생성 |
| architecture generation | stack, data model, API contract 생성 |
| scaffold generation | 프로젝트 초기 구조 생성 |
| vertical slice | 핵심 사용자 흐름 하나를 끝까지 구현 |
| backlog loop | 기능별 구현/검증 반복 |
| acceptance coverage | 요구사항 충족률 계산 |

## 7.4 MVP-4: 관찰성과 UI

목표:

- run history, score, diff, review, provider status를 시각화한다.

기능:

| 기능 | 설명 |
|---|---|
| SQLite state store | runs/steps/candidates/evaluations 저장 |
| local dashboard | FastAPI + React 또는 TUI |
| score trend | iteration별 점수 변화 |
| diff viewer | candidate patch 비교 |
| provider health | Claude/Codex/API 상태 표시 |

---

## 8. 프로젝트 프로파일

Orchestrator는 프로젝트 유형별 validation profile을 지원해야 한다.

## 8.1 Node/React profile

```yaml
validation:
  commands:
    install_check: "node --version && npm --version"
    lint: "npm run lint"
    typecheck: "npm run typecheck"
    test: "npm test"
    build: "npm run build"
```

## 8.2 Python/FastAPI profile

```yaml
validation:
  commands:
    lint: "ruff check ."
    typecheck: "mypy ."
    test: "pytest -q"
    import_smoke: "python -c \"import app.main\""
```

## 8.3 Unity profile

```yaml
validation:
  commands:
    compile: "Unity.exe -batchmode -quit -projectPath . -logFile Logs/compile.log"
    editmode_tests: "Unity.exe -batchmode -quit -projectPath . -runTests -testPlatform EditMode -testResults TestResults/EditMode.xml"
```

## 8.4 Docker/FastAPI profile

```yaml
validation:
  commands:
    compose_config: "docker compose config"
    api_tests: "pytest tests/api -q"
    healthcheck: "python scripts/check_health_config.py"
```

---

## 9. Provider 전략

## 9.1 Provider 분류

| Provider | 인증 | 용도 | 장점 | 주의점 |
|---|---|---|---|---|
| `codex_sub_cli` | ChatGPT subscription | local personal workflow | 구독 사용량 활용, CLI 자동화 가능 | browser login/session 상태에 민감 |
| `codex_api_cli` | OpenAI API key | programmatic workflow, CI | 자동화 안정성, CI 적합 | API 과금 |
| `claude_sub_cli` | Claude subscription | local personal workflow | Claude Code 사용량 활용 | Claude 웹/앱과 사용량 공유 |
| `claude_api_sdk` | Anthropic API key | production automation | SDK hooks/permissions/observability | API 과금, 구현 복잡도 증가 |
| `local_rule_based` | 없음 | judge/security/policy | deterministic, 무료, 안정 | 창의적 판단 불가 |

## 9.2 Role별 기본 provider 순서

```yaml
roles:
  product_manager:
    provider_order:
      - claude_sub_cli
      - codex_sub_cli
      - claude_api_sdk
      - codex_api_cli

  system_architect:
    provider_order:
      - claude_sub_cli
      - codex_sub_cli

  implementer:
    provider_order:
      - codex_sub_cli
      - claude_sub_cli
    tournament: true

  reviewer:
    provider_order:
      - claude_sub_cli
      - codex_sub_cli
    avoid_same_provider_as_implementer: true

  qa_engineer:
    provider_order:
      - codex_sub_cli
      - claude_sub_cli
      - local_rule_based

  judge:
    provider_order:
      - local_rule_based
      - claude_sub_cli
      - codex_sub_cli
```

## 9.3 Fallback 정책

Fallback 조건:

```text
- provider healthcheck 실패
- 인증 만료
- 구독 사용량 제한 도달
- API quota/rate limit 도달
- timeout
- malformed output
- forbidden command attempt
- changed-file policy 위반
```

Fallback 순서:

```text
same role next provider
→ lower-cost provider
→ read-only review only
→ human action item 생성
```

---

## 10. 검증 및 점수화 전략

## 10.1 필수 통과 조건

| 조건 | 실패 시 결정 |
|---|---|
| blocked file 미수정 | 위반 시 discard |
| secret 미노출 | 위반 시 discard |
| build 성공 | 실패 시 revise |
| 기존 test 삭제 금지 | 위반 시 reject/human review |
| command policy 준수 | 위반 시 reject |
| acceptance criteria 추적 가능 | 낮으면 continue/revise |

## 10.2 점수 예시

```yaml
scoring:
  build_pass: 25
  tests_pass: 25
  lint_pass: 10
  typecheck_pass: 10
  acceptance_coverage: 20
  reviewer_pass: 10

penalties:
  blocked_file_modified: 100
  secret_detected: 100
  test_deleted: 60
  test_weakened: 50
  unrelated_large_diff: 30
  dependency_added_without_reason: 20
  critical_review_issue: 20
```

## 10.3 Judge decision

```python
if blocked_file_modified or secret_detected:
    decision = "discard"
elif not build_pass:
    decision = "revise"
elif tests_pass and reviewer_pass and acceptance_coverage >= 0.85:
    decision = "accept"
elif score > previous_best_score:
    decision = "keep_candidate_but_continue"
else:
    decision = "discard"
```

---

## 11. 보안 및 정책

## 11.1 파일 정책

```yaml
file_policy:
  allowed_paths:
    - "src/**"
    - "app/**"
    - "tests/**"
    - "docs/**"
    - "package.json"
    - "pyproject.toml"

  blocked_paths:
    - ".git/**"
    - ".env"
    - ".env.*"
    - "secrets/**"
    - "firebase/prod/**"
    - "ProjectSettings/**"

  require_human_review_if_modified:
    - "package-lock.json"
    - "pnpm-lock.yaml"
    - "yarn.lock"
    - "Dockerfile"
    - "docker-compose.yml"
    - "infra/**"
    - "migrations/**"
```

## 11.2 명령 정책

```yaml
command_policy:
  blocked_patterns:
    - "rm -rf"
    - "git push"
    - "git reset --hard"
    - "curl * | sh"
    - "wget * | sh"
    - "sudo"
    - "docker system prune"
    - "format "
    - "del /s"
    - "Remove-Item -Recurse -Force"

  require_human_review:
    - "npm install"
    - "pip install"
    - "poetry add"
    - "uv add"
    - "docker compose up"
    - "terraform apply"
    - "kubectl apply"
```

---

## 12. 산출물

각 run은 다음 구조를 생성한다.

```text
.orchestrator/
  runs/
    20260511_001/
      input.md
      normalized_task.json
      repo_context.md
      plan.json
      candidates/
        codex_sub_cli/
          stdout.log
          stderr.log
          diff.patch
          changed_files.txt
          validation.json
          review.json
          score.json
        claude_sub_cli/
          stdout.log
          stderr.log
          diff.patch
          changed_files.txt
          validation.json
          review.json
          score.json
      decision.json
      final_report.md
```

---

## 13. 성공 기준

## 13.1 MVP 성공 기준

| 기준 | 목표 |
|---|---|
| 단일 기능 개발 workflow | 하나의 task를 worktree에서 실행하고 patch/report 생성 |
| provider 선택 | CLI option 또는 config로 implementer/reviewer 선택 가능 |
| fallback | provider 실패 시 다음 provider로 전환 |
| 교차 리뷰 | 구현 provider와 다른 provider로 리뷰 수행 |
| deterministic judge | build/test/file policy 기준으로 accept/revise/discard |
| PRD workflow | 기획서에서 requirements/backlog/vertical slice 생성 |

## 13.2 품질 기준

| 항목 | 기준 |
|---|---|
| 재현성 | 같은 input/run config로 동일 workflow 재실행 가능 |
| 격리성 | 실패한 candidate가 main working tree를 오염시키지 않음 |
| 추적성 | 모든 prompt, output, diff, validation result 저장 |
| 확장성 | 새로운 provider 추가 시 Provider adapter만 구현 |
| 안전성 | blocked file/command/secret 위반 시 자동 폐기 |

---

## 14. 개발 단계

## Phase 0. 조사 및 요구사항 고정

- 공식 문서 기준 CLI/API 전제 정리
- provider 기능 matrix 확정
- MVP workflow 범위 확정
- local-first vs SaaS 경계 정의

## Phase 1. Core CLI

- `devforge` CLI 생성
- config loader
- run directory 생성
- logging 구조 생성
- project profile 인식

## Phase 2. Provider Adapter

- base interface
- Codex CLI provider
- Claude CLI provider
- local rule provider
- provider healthcheck
- fallback policy

## Phase 3. Git/Evaluator/Judge

- worktree manager
- diff collector
- validation command runner
- file policy checker
- command policy checker
- secret scanner
- scoring
- judge decision

## Phase 4. Feature/Bugfix/Refactor Workflow

- task normalization
- repo context collector
- implementation plan
- candidate execution
- cross-review
- revision loop
- final report

## Phase 5. App-from-PRD Workflow

- PRD intake
- requirements inventory
- screen/user flow/data model/API contract generation
- backlog generation
- scaffold generation
- vertical slice loop
- acceptance coverage

## Phase 6. Observability/Dashboard

- SQLite state store
- local web dashboard 또는 TUI
- run list
- candidate diff
- score trend
- provider status

---

## 15. 위험 및 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| 구독 사용량 제한 | workflow 중단 | provider fallback, API provider option, run resume |
| 인증 만료 | provider 실행 실패 | healthcheck, 명확한 action item, API fallback |
| agent가 테스트를 약화 | 잘못된 accept | test deletion/weakening detector, human review |
| agent가 불필요한 대규모 변경 | regression risk | changed file cap, diff size penalty, scope rule |
| prompt injection in repo docs | agent 오작동 | trusted context 분리, role prompt에서 repo instruction 격리 |
| destructive command | 데이터 손실 | sandbox, command blocklist, worktree 격리 |
| PRD ambiguity | 잘못된 앱 구현 | assumptions.md 생성, MVP freeze, vertical slice 우선 |
| provider 약관 변경 | 제품 중단 | provider adapter 분리, CLI/API/provider-independent core |

---

## 16. 참고 링크

- OpenAI Codex CLI: https://developers.openai.com/codex/cli
- OpenAI Codex non-interactive mode: https://developers.openai.com/codex/noninteractive
- OpenAI Codex authentication: https://developers.openai.com/codex/auth
- OpenAI Codex CLI reference: https://developers.openai.com/codex/cli/reference
- Anthropic Claude Code CLI reference: https://code.claude.com/docs/en/cli-reference
- Anthropic Claude Agent SDK overview: https://code.claude.com/docs/en/agent-sdk/overview
- Anthropic Claude Code Pro/Max support: https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan
- Anthropic Claude Code usage limit announcement, 2026-05-06: https://www.anthropic.com/news/higher-limits-spacex
