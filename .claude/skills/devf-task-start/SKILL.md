---
name: devf-task-start
description: AI Dev Orchestrator의 DEVF-xxx 태스크를 시작할 때 사용. docs/plan/03에서 해당 태스크 섹션을 추출하고 _workspace/tasks/DEVF-xxx/ 에 작업 폴더(notes.md, todo.md, links.md)를 만든다. "DEVF-043 시작", "다음 태스크 준비", "DEVF-022 작업 폴더 만들어줘" 같은 요청에서 호출.
---

# devf-task-start

DEVF-xxx 태스크를 본격 구현하기 전 작업 폴더와 컨텍스트를 준비한다.

## 호출 인자

태스크 ID(예: `DEVF-043`) 하나. 없으면 사용자에게 묻는다.

## 절차

1. **태스크 섹션 추출**
   - `docs/plan/03_Task_Plan_AI_Dev_Orchestrator.md`에서 해당 DEVF-xxx 섹션을 Read로 읽는다
   - 섹션의 목표·작업·산출물·완료 기준을 파악
   - 선행 작업(`docs/plan/03` §12 우선순위 backlog 표)도 확인

2. **관련 spec 위치 매핑**
   - 01 개발 계획서에서 이 작업이 속한 MVP 단계
   - 02 아키텍처 설계서에서 영향받는 모듈/인터페이스 (§4 모듈 구성, §5 component 설계 참조)
   - 영향받는 디렉터리: `devforge/<sub>/`

3. **작업 폴더 생성**
   - `_workspace/tasks/<DEVF-xxx>/` 디렉터리 생성
   - 다음 3개 파일 생성:

   `notes.md`:
   ```markdown
   # DEVF-xxx <태스크 제목>

   ## Spec 요약
   <docs/plan/03의 목표·작업 압축>

   ## 영향 모듈
   - devforge/<sub>/<file>.py
   - tests/<sub>/test_<file>.py

   ## 선행 의존성
   - DEVF-xxx (상태: 확인 필요)

   ## 열린 질문
   - <설계 결정이 필요한 지점>

   ## 결정 로그
   - <날짜>: <결정 내용>
   ```

   `todo.md`:
   ```markdown
   # DEVF-xxx TODO

   - [ ] spec 재독·이해 확인
   - [ ] 기존 코드 영향 분석
   - [ ] 인터페이스 확정
   - [ ] 구현
   - [ ] unit test 작성
   - [ ] 자가 검증 (ruff/mypy/pytest)
   - [ ] /devf-policy-dogfood 통과
   - [ ] devforge-reviewer 통과
   - [ ] /devf-plan-sync 갱신
   ```

   `links.md`:
   ```markdown
   # DEVF-xxx 참조

   ## Spec
   - docs/plan/03 §<섹션 번호> <태스크 이름>
   - docs/plan/02 §<관련 절>
   - docs/plan/01 §<관련 절>

   ## 영향 파일
   - devforge/<path>
   - tests/<path>

   ## 선행 작업
   - DEVF-xxx
   ```

4. **사용자에게 보고**
   - 어디에 폴더 만들었는지
   - 핵심 작업 항목 3-5개
   - 선행 의존성 미충족 시 경고
   - 다음 단계 권고 (보통 `devforge-implementer` 호출)

## 금기

- 코드는 작성하지 마라 (이 skill은 컨텍스트 준비만)
- `_workspace/` 외부 파일 수정 금지
- 실제 secret·개인정보 기록 금지
- `docs/plan/` 수정 절대 금지
