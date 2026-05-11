# AI Dev Orchestrator Task Plan

문서 버전: v1.0  
작성일: 2026-05-11  
대상 제품명: **AI Dev Orchestrator**

---

## 1. Task Plan 목적

이 문서는 AI Dev Orchestrator를 실제로 구현하기 위한 작업 분해 문서다.

개발 목표는 다음 순서로 고정한다.

```text
MVP-1: 단일 기능 개발 루프
MVP-2: Claude/Codex 선택 및 fallback
MVP-3: app_from_prd workflow
MVP-4: observability/dashboard
```

작업은 `DEVF-*` ID를 사용한다.

---

## 2. Milestone 요약

| Milestone | 목표 | 완료 기준 |
|---|---|---|
| M0 | 프로젝트 초기화 및 요구사항 확정 | repo, config, CLI skeleton, 문서 구조 생성 |
| M1 | Core Orchestrator | workflow 실행, run directory, state 저장 |
| M2 | Provider Adapter | Codex/Claude CLI 실행 및 healthcheck |
| M3 | Git/Evaluator/Judge | worktree, validation, score, decision 구현 |
| M4 | Feature workflow | task 기반 구현·검증·리뷰·개선 루프 |
| M5 | Provider fallback/tournament | 구독 상태 변화 대응 및 후보 비교 |
| M6 | App-from-PRD | 기획서 기반 요구사항·설계·vertical slice 생성 |
| M7 | Observability | SQLite, report, dashboard/TUI |
| M8 | Hardening | 보안 정책, 테스트, 문서화, 패키징 |

---

## 3. M0: 프로젝트 초기화

## DEVF-001. Repository 생성

목표:

- Python 기반 CLI 프로젝트를 초기화한다.

작업:

- `pyproject.toml` 생성
- package 구조 생성
- test 구조 생성
- `README.md` 생성
- `.gitignore` 생성

산출물:

```text
pyproject.toml
devforge/
tests/
README.md
```

완료 기준:

- `python -m devforge --help` 또는 `devforge --help` 실행 가능
- `pytest` 실행 가능

---

## DEVF-002. 기본 디렉터리 구조 생성

작업:

```text
devforge/
  cli.py
  core/
  providers/
  git/
  evaluators/
  workflows/
  prompts/
  project_profiles/
  dashboard/
```

완료 기준:

- 각 모듈에 `__init__.py` 존재
- import smoke test 통과

---

## DEVF-003. Config schema 정의

목표:

- `devforge.yaml`의 최소 schema를 정의한다.

필드:

```yaml
project
providers
roles
validation
file_policy
command_policy
scoring
stop_conditions
```

작업:

- Pydantic 또는 dataclass 기반 schema 구현
- config validation error message 구현
- sample config 생성

산출물:

```text
devforge/core/config_loader.py
examples/devforge.yaml
```

완료 기준:

- 잘못된 config에 대해 명확한 오류 출력
- 정상 config 로드 테스트 통과

---

## 4. M1: Core Orchestrator

## DEVF-010. CLI command skeleton

명령:

```bash
devforge init
devforge providers status
devforge run --workflow feature --task task.md
devforge create-app --from app_plan.md
devforge report --run <run_id>
devforge cleanup --run <run_id>
```

작업:

- Typer 또는 Click 기반 CLI 구현
- 공통 logging 설정
- error handling 정책 구현

완료 기준:

- 모든 명령이 help와 기본 stub 동작 제공

---

## DEVF-011. Run context 생성

목표:

- 각 실행에 고유 run_id와 artifact directory를 부여한다.

작업:

- timestamp 기반 run_id 생성
- `.orchestrator/runs/<run_id>/` 생성
- input file 복사
- run metadata 저장

산출물:

```text
.orchestrator/runs/<run_id>/input.md
.orchestrator/runs/<run_id>/run.json
```

완료 기준:

- run 실행 시 artifact directory가 생성된다.
- 같은 task를 여러 번 실행해도 충돌하지 않는다.

---

## DEVF-012. Workflow loader 구현

목표:

- YAML workflow definition을 로드한다.

작업:

- `workflows/feature.yaml` 생성
- stage schema 구현
- stage dependency validation 구현

완료 기준:

- workflow 로드 테스트 통과
- 알 수 없는 role/stage type에 대해 오류 발생

---

## DEVF-013. State Store v1

목표:

- run 상태를 파일 기반 JSON으로 우선 저장한다.

작업:

- run status update
- step result 저장
- candidate result 저장

완료 기준:

- `devforge report --run <run_id>`가 JSON state를 읽어 요약 출력

---

## 5. M2: Provider Adapter

## DEVF-020. Provider base interface 구현

작업:

- `AgentRequest`
- `AgentResult`
- `AgentProvider` Protocol
- provider capability model

완료 기준:

- mock provider 테스트 통과

---

## DEVF-021. Provider Registry 구현

작업:

- config 기반 provider 생성
- enabled/disabled 상태 관리
- provider capability 확인
- provider healthcheck 실행

완료 기준:

```bash
devforge providers status
```

명령이 provider 목록과 상태를 출력한다.

---

## DEVF-022. Codex CLI Provider 구현

목표:

- Codex CLI를 비대화형 worker로 실행한다.

기본 실행:

```bash
codex exec \
  --cd <worktree> \
  --sandbox workspace-write \
  --ask-for-approval never \
  --ephemeral \
  "<prompt>"
```

작업:

- `codex --version` healthcheck
- read-only smoke test 옵션
- subprocess runner
- stdout/stderr capture
- timeout 처리
- changed files 수집

완료 기준:

- mock repo에서 Codex provider가 prompt를 실행하고 stdout/stderr를 저장한다.
- 실패 시 AgentResult.error가 채워진다.

주의:

- `danger-full-access`, `--yolo`, sandbox bypass 류 옵션은 config에서 명시적으로 금지한다.

---

## DEVF-023. Claude CLI Provider 구현

목표:

- Claude Code CLI를 비대화형 worker로 실행한다.

기본 실행:

```bash
claude -p "<prompt>" \
  --output-format json \
  --permission-mode acceptEdits \
  --tools "Read,Edit,Write,Bash" \
  --max-turns 20
```

작업:

- `claude --version` 또는 auth status healthcheck
- implementer/reviewer role별 permission mode 분기
- JSON output parsing
- stdout/stderr capture
- timeout 처리
- changed files 수집

완료 기준:

- mock repo에서 Claude provider가 prompt를 실행하고 결과를 저장한다.
- reviewer role에서는 edit 도구가 비활성화된다.

---

## DEVF-024. Local Rule Provider 구현

목표:

- deterministic judge와 rule-based reviewer를 구현한다.

작업:

- file policy result 요약
- validation result 요약
- score result 생성
- accept/revise/discard decision 생성

완료 기준:

- LLM provider 없이도 candidate evaluation decision이 가능하다.

---

## 6. M3: Git / Evaluator / Judge

## DEVF-030. Git Worktree Manager 구현

작업:

- base branch 확인
- worktree 생성
- branch 생성
- worktree cleanup
- patch export

명령 예:

```bash
git worktree add <path> -b agent/<run_id>-<provider> <base_branch>
```

완료 기준:

- candidate별 worktree가 독립 생성된다.
- cleanup 명령으로 worktree 제거 가능

---

## DEVF-031. Diff Collector 구현

작업:

- `git diff --stat`
- `git diff --name-only`
- `git diff`
- dependency file 변경 탐지
- test file 변경 탐지

산출물:

```text
diff.patch
changed_files.txt
diff_stat.txt
```

완료 기준:

- agent 실행 후 diff artifact가 저장된다.

---

## DEVF-032. Validation Runner 구현

작업:

- project profile의 validation command 실행
- command별 timeout 지원
- stdout/stderr tail 저장
- exit code 저장

출력:

```json
{
  "build": {"exit_code": 0, "passed": true},
  "test": {"exit_code": 1, "passed": false, "output_tail": "..."}
}
```

완료 기준:

- command 실패가 정확히 validation.json에 기록된다.

---

## DEVF-033. File Policy Checker 구현

작업:

- allowed_paths 검증
- blocked_paths 검증
- require_human_review_if_modified 검증

완료 기준:

- blocked path 수정 시 candidate가 discard 대상이 된다.

---

## DEVF-034. Command Policy Checker 구현

작업:

- stdout/stderr/log에서 blocked command pattern 탐지
- require_human_review command 탐지
- provider별 command log가 부족한 경우 한계 표시

완료 기준:

- 위험 명령 문자열 탐지 시 policy violation 기록

---

## DEVF-035. Secret Scanner 구현

작업:

- diff, stdout, stderr에서 secret pattern 탐지
- key-like value redaction
- `.env` 변경 탐지

완료 기준:

- secret 탐지 시 discard 또는 human_review로 전환

---

## DEVF-036. Test Mutation Checker 구현

작업:

- test file 삭제 탐지
- assert 제거 탐지
- threshold 완화 탐지
- skip/only 추가 탐지

완료 기준:

- 테스트 약화 가능성이 있으면 human_review로 전환

---

## DEVF-037. Score Calculator 구현

입력:

- validation result
- file policy result
- secret scan result
- review result
- acceptance coverage

점수:

```yaml
build_pass: 25
tests_pass: 25
lint_pass: 10
typecheck_pass: 10
acceptance_coverage: 20
reviewer_pass: 10
```

완료 기준:

- candidate별 score.json 생성

---

## DEVF-038. Judge 구현

Decision:

```text
accept
revise
discard
human_review
keep_candidate_but_continue
```

완료 기준:

- blocked file/secret은 즉시 discard
- build 실패는 revise
- score threshold 통과 시 accept
- 점수 개선 없으면 discard

---

## 7. M4: Feature/Bugfix/Refactor Workflow

## DEVF-040. Task Normalizer 구현

목표:

- 사용자 task를 structured task로 변환한다.

출력 schema:

```json
{
  "goal": "",
  "constraints": [],
  "acceptance_criteria": [],
  "likely_files": [],
  "risk_level": "low|medium|high",
  "workflow_recommendation": "feature|bugfix|refactor"
}
```

완료 기준:

- task.md 입력으로 normalized_task.json 생성

---

## DEVF-041. Repo Context Collector 구현

작업:

- 파일 tree 요약
- package/project metadata 수집
- test command 후보 수집
- 최근 git status 수집
- relevant files 검색

완료 기준:

- agent prompt에 넣을 수 있는 repo_context.md 생성

---

## DEVF-042. Implementation Plan Generator 구현

목표:

- 구현 전 단계적 계획을 생성한다.

출력:

```json
{
  "steps": [],
  "files_to_change": [],
  "tests_to_add_or_run": [],
  "risks": []
}
```

완료 기준:

- 계획이 없으면 구현 stage를 실행하지 않는다.

---

## DEVF-043. Implementer Stage 구현

작업:

- selected provider 실행
- prompt rendering
- worktree에서 agent 실행
- result 저장

완료 기준:

- feature workflow가 실제 patch를 생성한다.

---

## DEVF-044. Reviewer Stage 구현

작업:

- diff와 requirement를 reviewer prompt에 삽입
- 구현 provider와 다른 provider 선택
- JSON review parsing
- malformed output fallback

완료 기준:

- review.json 생성

---

## DEVF-045. Revision Loop 구현

작업:

- review critical issue를 revision prompt로 변환
- 같은 worktree 또는 새 worktree에서 revision 실행
- max_iterations 적용
- no improvement stop condition 구현

완료 기준:

- 실패한 build/test를 바탕으로 1회 이상 재시도 가능

---

## DEVF-046. Final Report 생성

내용:

```text
- task summary
- selected provider
- changed files
- validation result
- review summary
- score
- decision
- risks
- next recommended steps
```

완료 기준:

- `final_report.md` 생성

---

## 8. M5: Provider Fallback / Tournament

## DEVF-050. Role Router 고도화

작업:

- role별 provider_order 적용
- avoid_same_provider_as_implementer 적용
- capability filtering
- healthcheck result filtering

완료 기준:

- reviewer가 implementer와 같은 provider가 되지 않는다.

---

## DEVF-051. Provider Failure Classifier 구현

Failure class:

```text
auth_expired
usage_limit_hit
rate_limit
command_missing
timeout
malformed_output
policy_violation
unknown
```

완료 기준:

- provider 실패 시 reason이 기록된다.

---

## DEVF-052. Fallback Executor 구현

작업:

- 같은 role의 다음 provider 실행
- fallback log 저장
- fallback 이후 review/judge 흐름 유지

완료 기준:

- provider A 실패 시 provider B로 이어진다.

---

## DEVF-053. Tournament Mode 구현

작업:

- 같은 task를 여러 provider에 병렬 또는 순차 실행
- candidate별 worktree 생성
- candidate별 validation/review/score
- best candidate 선택

완료 기준:

- Codex candidate와 Claude candidate를 비교해 best patch 선택

---

## DEVF-054. Candidate Comparison Report 구현

내용:

```text
- provider별 score
- build/test 결과 비교
- changed files 비교
- review issue 비교
- selected candidate reason
```

완료 기준:

- tournament run에서 comparison.md 생성

---

## 9. M6: App-from-PRD Workflow

## DEVF-060. PRD Intake 구현

입력:

```text
app_plan.md
planning_doc.md
prd.md
```

출력:

```text
product_summary.md
requirements.json
ambiguity_log.json
assumptions.md
out_of_scope.md
```

완료 기준:

- PRD에서 functional/non-functional requirements가 추출된다.

---

## DEVF-061. Requirements Schema 구현

Schema:

```json
{
  "functional_requirements": [
    {
      "id": "FR-001",
      "title": "",
      "description": "",
      "priority": "must|should|could",
      "acceptance_criteria": [],
      "test_strategy": "unit|integration|e2e|manual"
    }
  ],
  "non_functional_requirements": [],
  "unknowns": []
}
```

완료 기준:

- 모든 requirement에 id와 acceptance criteria가 존재한다.

---

## DEVF-062. MVP Scope Freeze Stage 구현

작업:

- must/should/could 분류
- MVP 범위 확정
- out-of-scope 명시
- assumption 명시

완료 기준:

- 구현 전 `mvp_scope.md`가 생성된다.

---

## DEVF-063. UX Flow / Screen Inventory 생성

출력:

```text
user_flows.md
screen_inventory.json
navigation_map.md
```

완료 기준:

- 핵심 사용자 흐름과 화면 목록이 명시된다.

---

## DEVF-064. Architecture Generator 구현

출력:

```text
architecture.md
data_model.md
api_contract.yaml
tech_stack.md
```

완료 기준:

- stack별 scaffold에 필요한 설계 artifact가 존재한다.

---

## DEVF-065. Scaffold Generator 구현

지원 stack v1:

```text
react-fastapi-postgres
react-node-postgres
python-fastapi-only
unity-mobile-game-ui
```

완료 기준:

- 선택한 stack의 기본 프로젝트 구조가 생성된다.
- build 또는 import smoke가 통과한다.

---

## DEVF-066. Vertical Slice Planner 구현

목표:

- 앱 전체가 아니라 핵심 사용자 흐름 하나를 end-to-end로 구현하도록 제한한다.

예:

```text
로그인 → 메인 화면 → 핵심 데이터 생성 → 저장 → 목록 표시
```

출력:

```json
{
  "vertical_slice_name": "",
  "user_journey": [],
  "screens": [],
  "api_endpoints": [],
  "data_entities": [],
  "acceptance_criteria": []
}
```

완료 기준:

- vertical slice acceptance criteria가 생성된다.

---

## DEVF-067. Vertical Slice Implementer 구현

작업:

- scaffold 기반으로 vertical slice 구현
- integration/e2e test 추가
- validation 실행
- review/judge 적용

완료 기준:

- 하나의 사용자 흐름이 실제 실행 가능하다.

---

## DEVF-068. Backlog Generator 구현

출력:

```json
{
  "items": [
    {
      "id": "TASK-001",
      "title": "",
      "requirement_ids": [],
      "acceptance_criteria": [],
      "priority": "P0|P1|P2",
      "estimated_complexity": "S|M|L",
      "dependencies": []
    }
  ]
}
```

완료 기준:

- requirement가 backlog item으로 trace된다.

---

## DEVF-069. Backlog Implementation Loop 구현

작업:

- backlog item별 implementation prompt 생성
- candidate 실행
- validation/review/judge 반복
- acceptance coverage 업데이트

완료 기준:

- backlog item 단위로 구현 상태가 기록된다.

---

## DEVF-070. Acceptance Coverage Calculator 구현

계산:

```text
acceptance coverage = passed acceptance criteria / total acceptance criteria
```

완료 기준:

- coverage가 requirement id별로 출력된다.

---

## DEVF-071. Release Packaging Stage 구현

출력:

```text
README.md
deployment.md
release_notes.md
qa_report.md
final_report.md
```

완료 기준:

- 새 사용자가 README를 보고 실행할 수 있는 수준의 문서가 생성된다.

---

## 10. M7: Observability / Dashboard

## DEVF-080. SQLite State Store 구현

작업:

- runs table
- steps table
- candidates table
- evaluations table
- provider_status table

완료 기준:

- run 결과가 SQLite에 index된다.

---

## DEVF-081. Report Command 고도화

명령:

```bash
devforge report --run <run_id>
devforge report --latest
devforge report --run <run_id> --format markdown
```

완료 기준:

- run 요약과 candidate 비교 출력

---

## DEVF-082. Local Dashboard Backend

작업:

- FastAPI endpoint 구현
- run list API
- run detail API
- candidate diff API
- provider status API

완료 기준:

- browser에서 JSON API 확인 가능

---

## DEVF-083. Local Dashboard Frontend

화면:

```text
- Runs
- Run Detail
- Candidate Compare
- Validation Result
- Review Result
- Provider Status
```

완료 기준:

- 최소한 run list와 run detail 표시

---

## 11. M8: Hardening / Testing / Packaging

## DEVF-090. Unit Test Suite 구축

대상:

```text
- config loader
- provider registry
- role router
- worktree manager
- policy checker
- score calculator
- judge
```

완료 기준:

- 핵심 모듈 unit test 통과

---

## DEVF-091. Mock Provider Integration Test

목표:

- 실제 Claude/Codex 없이 workflow를 검증한다.

작업:

- mock implementer provider
- mock reviewer provider
- fake validation result

완료 기준:

- CI에서 외부 provider 없이 테스트 가능

---

## DEVF-092. Real Provider Smoke Test

목표:

- 실제 Codex/Claude CLI가 설치된 로컬 환경에서 최소 smoke test를 수행한다.

테스트:

```text
- provider status
- read-only prompt
- mock repo file edit
- validation failure handling
```

완료 기준:

- provider별 smoke result가 저장된다.

---

## DEVF-093. Security Regression Test

대상:

```text
- blocked file modified
- secret inserted
- rm -rf command attempted
- test deletion
- lockfile modification
```

완료 기준:

- 각 위험 사례가 discard 또는 human_review로 전환된다.

---

## DEVF-094. Documentation

문서:

```text
README.md
INSTALL.md
CONFIG.md
PROVIDERS.md
WORKFLOWS.md
SECURITY.md
APP_FROM_PRD.md
```

완료 기준:

- 새 사용자가 설치, provider 설정, 첫 run 실행 가능

---

## DEVF-095. Packaging

작업:

- Python package build
- CLI entrypoint
- version command
- release notes

완료 기준:

```bash
pip install -e .
devforge --version
devforge --help
```

---

## 12. 우선순위 Backlog

| ID | 작업 | 우선순위 | 선행 작업 | 완료 기준 |
|---|---|---:|---|---|
| DEVF-001 | Repository 생성 | P0 | 없음 | CLI skeleton 실행 |
| DEVF-003 | Config schema | P0 | DEVF-001 | config validation 통과 |
| DEVF-010 | CLI skeleton | P0 | DEVF-001 | 주요 명령 help 제공 |
| DEVF-011 | Run context | P0 | DEVF-010 | run directory 생성 |
| DEVF-020 | Provider base | P0 | DEVF-003 | mock provider 테스트 |
| DEVF-021 | Provider registry | P0 | DEVF-020 | status 출력 |
| DEVF-022 | Codex CLI provider | P0 | DEVF-021 | Codex 실행 가능 |
| DEVF-023 | Claude CLI provider | P0 | DEVF-021 | Claude 실행 가능 |
| DEVF-030 | Worktree manager | P0 | DEVF-011 | worktree 생성/삭제 |
| DEVF-032 | Validation runner | P0 | DEVF-011 | command 실행 결과 저장 |
| DEVF-033 | File policy checker | P0 | DEVF-031 | blocked path 탐지 |
| DEVF-037 | Score calculator | P0 | DEVF-032 | score.json 생성 |
| DEVF-038 | Judge | P0 | DEVF-037 | accept/revise/discard |
| DEVF-043 | Implementer stage | P0 | DEVF-022, DEVF-023, DEVF-030 | patch 생성 |
| DEVF-044 | Reviewer stage | P1 | DEVF-043 | review.json 생성 |
| DEVF-045 | Revision loop | P1 | DEVF-044 | 재시도 동작 |
| DEVF-050 | Role router 고도화 | P1 | DEVF-021 | provider 선택 정책 |
| DEVF-052 | Fallback executor | P1 | DEVF-050 | provider 실패 대응 |
| DEVF-053 | Tournament mode | P1 | DEVF-052 | 후보 비교 |
| DEVF-060 | PRD intake | P1 | DEVF-040 | requirements 생성 |
| DEVF-064 | Architecture generator | P1 | DEVF-060 | architecture.md 생성 |
| DEVF-067 | Vertical slice implementer | P1 | DEVF-065 | e2e slice 동작 |
| DEVF-080 | SQLite state store | P2 | DEVF-011 | run index 저장 |
| DEVF-082 | Dashboard backend | P2 | DEVF-080 | API 제공 |
| DEVF-090 | Unit tests | P0 | 지속 | 핵심 테스트 통과 |

---

## 13. 첫 번째 개발 순서

실제 구현은 다음 순서로 시작한다.

```text
1. DEVF-001 Repository 생성
2. DEVF-003 Config schema
3. DEVF-010 CLI skeleton
4. DEVF-011 Run context
5. DEVF-020 Provider base
6. DEVF-021 Provider registry
7. DEVF-030 Git worktree manager
8. DEVF-032 Validation runner
9. DEVF-022 Codex CLI provider
10. DEVF-023 Claude CLI provider
11. DEVF-033 File policy checker
12. DEVF-037 Score calculator
13. DEVF-038 Judge
14. DEVF-043 Implementer stage
15. DEVF-044 Reviewer stage
16. DEVF-046 Final report
```

이 순서의 이유:

- provider보다 먼저 run/worktree/validation 구조를 만든다.
- 실제 Claude/Codex 실행 전 mock provider로 workflow를 검증한다.
- app_from_prd는 feature workflow가 안정화된 뒤 확장한다.

---

## 14. Definition of Done

## 14.1 단일 task workflow 완료 조건

```text
- task.md 입력 가능
- worktree 생성
- provider 실행
- diff 수집
- validation 실행
- review 실행
- score 계산
- judge decision 생성
- final_report.md 생성
```

## 14.2 provider fallback 완료 조건

```text
- provider A healthcheck 실패 시 provider B 선택
- provider A 실행 중 timeout 시 provider B 재시도
- fallback reason 기록
- final report에 provider switch history 표시
```

## 14.3 app_from_prd 완료 조건

```text
- PRD 입력 가능
- requirements.json 생성
- architecture.md 생성
- backlog.json 생성
- scaffold 생성
- vertical slice 구현 시도
- acceptance coverage 계산
- release report 생성
```

---

## 15. 테스트 전략

## 15.1 Unit Tests

| 모듈 | 테스트 |
|---|---|
| config_loader | valid/invalid config |
| role_router | provider priority/fallback/avoid same provider |
| worktree_manager | create/cleanup/export patch |
| validation_runner | pass/fail/timeout |
| file_policy_checker | allowed/blocked/human review paths |
| secret_scanner | key patterns/redaction |
| score_calculator | score/penalty 계산 |
| judge | accept/revise/discard/human_review |

## 15.2 Integration Tests

```text
- mock provider가 파일 수정 → validation pass → accept
- mock provider가 blocked file 수정 → discard
- mock provider가 build failure 생성 → revise
- reviewer가 critical issue 반환 → revise
- two candidates 생성 → higher score candidate selected
```

## 15.3 Manual Smoke Tests

```text
- Codex CLI 설치 환경에서 providers status
- Claude CLI 설치 환경에서 providers status
- 작은 sample repo에서 파일 하나 수정
- validation command 실패 처리
- report 확인
```

---

## 16. Sample Acceptance Scenario

## Scenario A: Codex 구현 + Claude 리뷰

입력:

```bash
devforge run \
  --workflow feature \
  --task examples/tasks/add_health_endpoint.md \
  --implementer codex_sub_cli \
  --reviewer claude_sub_cli
```

기대 결과:

```text
- Codex worktree 생성
- Codex가 /health endpoint 구현
- pytest 통과
- Claude가 diff 리뷰
- score >= 85
- decision = accept
- final_report.md 생성
```

## Scenario B: Claude 사용량 제한 → Codex fallback

입력:

```bash
devforge run --workflow feature --task task.md
```

조건:

```text
claude_sub_cli: usage_limit_hit
codex_sub_cli: available
```

기대 결과:

```text
- role router가 Claude를 제외
- Codex로 구현 또는 리뷰 전환
- fallback history 기록
- run 계속 진행
```

## Scenario C: PRD에서 앱 vertical slice 생성

입력:

```bash
devforge create-app \
  --from examples/prd/mobile_game_lobby.md \
  --stack unity-mobile-game-ui
```

기대 결과:

```text
- requirements.json 생성
- screen_inventory.json 생성
- architecture.md 생성
- Unity scaffold 생성
- 닉네임 입력 → 로비 진입 → 하단 탭 표시 vertical slice 구현 시도
- validation/report 생성
```

---

## 17. Known Open Questions

| 질문 | 기본 결정 |
|---|---|
| CLI 구현 언어 | Python 우선. Typer + Pydantic + SQLite 사용 |
| dashboard 우선순위 | MVP 이후. 초기에는 markdown report와 CLI summary |
| provider SDK 사용 시점 | CLI adapter 안정화 후 Claude Agent SDK / Codex MCP 검토 |
| PRD ambiguity 처리 | 질문으로 막지 않고 assumptions.md에 기록 후 MVP 진행 |
| CI 적용 | subscription CLI는 local-first, CI는 API key provider 권장 |
| multi-agent 병렬 실행 | MVP에서는 순차 실행, 이후 병렬화 |

---

## 18. 참고 링크

- OpenAI Codex CLI: https://developers.openai.com/codex/cli
- OpenAI Codex non-interactive mode: https://developers.openai.com/codex/noninteractive
- OpenAI Codex authentication: https://developers.openai.com/codex/auth
- OpenAI Codex CLI reference: https://developers.openai.com/codex/cli/reference
- Anthropic Claude Code CLI reference: https://code.claude.com/docs/en/cli-reference
- Anthropic Claude Agent SDK overview: https://code.claude.com/docs/en/agent-sdk/overview
- Anthropic Claude Code hooks: https://code.claude.com/docs/en/hooks
- Anthropic Claude Code Pro/Max support: https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan
