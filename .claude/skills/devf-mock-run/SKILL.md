---
name: devf-mock-run
description: 실제 Claude/Codex CLI 없이 mock provider로 devforge의 feature workflow를 end-to-end 실행해 회귀 확인. 변경 후 전체 흐름이 깨지지 않았는지 빠르게 검증. "mock 워크플로 실행", "전체 흐름 확인", "smoke test" 같은 요청에서 호출.
---

# devf-mock-run

provider adapter나 workflow 변경 후 통합 회귀를 확인한다. 외부 LLM 호출 없이 deterministic하게 돈다.

## 전제 (구현 상태에 따라 조정)

- `devforge` CLI가 최소한 stub 수준으로 동작 가능
- mock provider(`tests/fixtures/mock_provider.py` 또는 유사)가 존재 — fixed `AgentResult`를 반환
- 샘플 task: `tests/fixtures/sample_task.md` 또는 `examples/tasks/`의 작은 task
- 샘플 repo: `tests/fixtures/sample_repo/` (small fixture)

## 절차

1. **사전 확인**
   - `devforge` import 가능?
   - mock provider fixture 존재?
   - sample repo fixture 존재?
   - 없으면 사용자에게 어떤 fixture를 만들지 물어보고 중단

2. **mock workflow 실행**
   - pytest integration test가 있다면: `pytest tests/integration/test_feature_workflow.py -v`
   - 없다면 직접 호출:
     ```python
     from devforge.core.workflow_engine import WorkflowEngine
     engine = WorkflowEngine(config=test_config_with_mock_providers)
     result = engine.run(workflow_id="feature", input_ref="tests/fixtures/sample_task.md")
     ```
   - 모든 stage가 진행되는지: normalize_task → inspect_repo → plan → implement → validate → review → judge

3. **각 stage 산출물 확인**
   - `_workspace/scratch/mock_run_<timestamp>/`에 run artifact 복사
   - 또는 `.orchestrator/runs/<run_id>/`의 구조 확인:
     - `normalized_task.json` 존재?
     - `candidates/<provider>/diff.patch` 존재?
     - `validation.json` 존재?
     - `review.json` 존재?
     - `decision.json` 존재?
     - `final_report.md` 존재?

4. **상태 검증**
   - 각 step의 status 전이가 `pending → running → completed`로 잘 갔는지
   - judge decision이 `accept|revise|discard|human_review` 중 하나로 나왔는지
   - mock provider가 시뮬레이션한 시나리오(예: build fail → revise)가 실제로 그 decision으로 갔는지

5. **시나리오 매트릭스 (가능하면)**
   - 시나리오 A: mock이 정상 patch → judge=accept
   - 시나리오 B: mock이 blocked file 수정 → judge=discard
   - 시나리오 C: mock이 build fail → judge=revise
   - 시나리오 D: mock이 secret 포함 diff → judge=discard

6. **결과 리포트**
   - PASS: 모든 단계 + 시나리오 통과
   - FAIL: 어디서 어떻게 깨졌는지 (stage, exception, missing artifact)
   - 자동 분석 시도: 최근 변경분(`git diff`) 중 의심 위치 추정

## 출력 포맷

```
=== devf mock-run ===
Workflow: feature
Stages: <n> executed / <m> expected

Scenarios:
  [OK] accept-on-clean-patch
  [OK] discard-on-blocked-file
  [FAIL] revise-on-build-fail
        → expected decision=revise, got decision=discard
        → suspect: devforge/evaluators/score_calculator.py (recently modified)

Artifacts: _workspace/scratch/mock_run_<ts>/
Verdict: PASS | FAIL
```

## 금기

- **절대 실제 Claude/Codex CLI 호출하지 마라** — 이 skill의 목적은 mock-only smoke
- `.orchestrator/` 외부에 영구 산출물 만들지 마라
- 실패해도 코드 자동 수정하지 마라 — 진단만
- 외부 네트워크 호출 금지
