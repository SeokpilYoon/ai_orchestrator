# _workspace/

이 디렉터리는 **Claude Code harness 전용 스크래치 공간**이다. devforge 개발 중 생기는 임시 산출물·작업 메모·드래프트가 여기에 모인다.

## 영구 산출물과의 구분

| 종류 | 위치 | git 추적 |
|---|---|---|
| 제품 코드 | `devforge/` | yes |
| 테스트 | `tests/` | yes |
| 영구 문서 | `docs/` | yes |
| 런타임 산출물 (devforge가 만든 run) | `.orchestrator/` | no |
| **harness 작업 스크래치 (여기)** | `_workspace/` | no (이 README만 yes) |

`_workspace/` 안의 무엇이든 언제든 지워질 수 있다. 중요한 것은 `docs/`나 `devforge/`로 옮겨라.

## 구조

```
_workspace/
  README.md           ← 이 파일 (git 추적됨)
  tasks/              ← DEVF-xxx 작업 폴더
    DEVF-001/
      notes.md        ← 현재 이해, 설계 결정, 열린 질문
      todo.md         ← 체크리스트
      links.md        ← 관련 spec 섹션·파일 포인터
      drafts/         ← devforge/로 옮기기 전 코드 초안 (있다면)
      validation.log  ← 로컬 검증 로그 (있다면)
  decisions/          ← ADR-lite — 기록 가치 있는 설계 판단
    0001-<topic>.md
  scratch/            ← 일회성 실험·grep 결과·임시 메모
  drift/              ← /devf-plan-sync 출력 보관
```

## 사용 규칙

- **새 DEVF-xxx 작업 시작**: `/devf-task-start DEVF-xxx` 호출 → `tasks/DEVF-xxx/` 자동 생성
- **설계 결정 기록**: `decisions/`에 ADR-lite 작성 (제목·맥락·결정·결과 4단)
- **plan 동기화 결과**: `/devf-plan-sync` 실행 시 `drift/<YYYY-MM-DD>.md` 자동 저장
- **민감 정보 금지**: API key, 토큰, 개인정보, 실제 secret은 어떤 파일에도 기록하지 않는다. placeholder만 사용.

## 정리

`_workspace/`가 너무 커지거나 오래된 메모가 쌓이면 통째로 비워도 무방하다. 영구 가치가 있는 것은 이미 `docs/`나 `devforge/`로 옮겨졌어야 한다.

```bash
# 안전한 정리 (README는 보존)
find _workspace -mindepth 1 -not -name README.md -delete
```
