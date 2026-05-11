---
name: devf-plan-sync
description: 현재 devforge/ 코드베이스 상태와 docs/plan/02·03 사양의 차이(drift)를 점검. 어느 DEVF-xxx가 끝났고, 어느 모듈이 spec과 다른지 리포트. "계획 동기화", "drift 확인", "어디까지 했지" 같은 요청에서 호출.
---

# devf-plan-sync

코드와 사양 사이의 drift를 진단한다. 어느 한쪽도 자동 수정하지 않는다.

## 절차

1. **사양 인덱스 로드**
   - `docs/plan/03` §12 우선순위 backlog 표를 읽어 모든 DEVF-xxx의 완료 기준 추출
   - `docs/plan/02` §4 모듈 구성 트리 읽기
   - `docs/plan/02` §5의 component 책임 매핑

2. **코드 인덱스 수집**
   - `devforge/` 디렉터리 트리 (Glob)
   - 각 모듈의 주요 public symbol (Grep으로 `class `, `def ` 추출)
   - `tests/` 트리

3. **대조 (drift 카테고리)**

   **A. 누락 (spec에 있는데 코드에 없음)**
   - spec 명시 모듈이 없는지
   - spec 명시 클래스/함수가 없는지
   - spec 명시 workflow YAML이 없는지

   **B. 잉여 (코드에 있는데 spec에 없음)**
   - spec에 정의되지 않은 새 모듈/추상화
   - 정당화되지 않은 새 의존성

   **C. 인터페이스 불일치**
   - `AgentRequest`/`AgentResult` 필드가 spec과 다른지
   - provider Protocol 메서드가 다른지

   **D. 테스트 부재**
   - 핵심 모듈(`core/`, `providers/`, `evaluators/`, `git/`)에 대응 테스트 없음

   **E. 진행도**
   - 각 DEVF-xxx를 done/partial/not-started로 분류
   - `_workspace/tasks/DEVF-xxx/todo.md`의 체크 상태도 참고

4. **리포트 출력**

   `_workspace/drift/<YYYY-MM-DD>.md`로 저장. 포맷:

   ```markdown
   # Plan Sync — <날짜>

   ## 진행도 요약
   - 완료: DEVF-xxx, DEVF-xxx
   - 진행중: DEVF-xxx
   - 대기: DEVF-xxx ...

   ## Drift

   ### 누락
   - <spec ref>: <설명>

   ### 잉여
   - <file>: <설명>

   ### 인터페이스 불일치
   - <file>: <expected> vs <actual>

   ### 테스트 부재
   - devforge/<path>: 대응 테스트 없음

   ## 권고
   - 다음 작업: DEVF-xxx
   - 정리 필요: <항목>
   ```

5. **사용자에게 요약 보고**
   - 진행도 한 줄
   - critical drift (인터페이스 불일치) 우선
   - 다음 권고 작업

## 금기

- 코드·spec 자동 수정 금지 (리포트만)
- `docs/plan/` 절대 수정 금지
- `_workspace/drift/` 외부에 파일 만들지 마라
- 사용자 데이터·credential 리포트에 포함 금지
