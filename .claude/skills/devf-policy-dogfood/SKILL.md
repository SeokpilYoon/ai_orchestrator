---
name: devf-policy-dogfood
description: 우리가 만드는 devforge의 file_policy / command_policy / secret_scanner 규칙을 *현재 변경분*에 적용해 자가검증. 우리가 정한 규칙으로 우리 코드를 검사한다. "정책 자가검증", "policy check", "dogfood" 같은 요청에서 호출.
---

# devf-policy-dogfood

이 skill은 메타한 자가검증이다 — devforge가 사용자 patch에 적용할 정책을 harness의 변경분에도 그대로 적용한다.

## 적용 대상

- 가장 최근 변경분: `git status`, `git diff` (uncommitted) 또는 사용자가 지정한 commit range
- 검사 범위: 변경된 파일들의 경로·내용·실행 흔적

## 검사 항목 (docs/plan/01 §11, devforge.yaml 예시 참조)

### 1. File Policy

**blocked_paths**:
- `.git/**` (직접 편집은 금지)
- `.env`, `.env.*`
- `secrets/**`

→ 위반 시: **critical**, 사용자에게 즉시 보고하고 변경 되돌리기 권고

**require_human_review_if_modified**:
- `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `poetry.lock`, `uv.lock`
- `Dockerfile`, `docker-compose.yml`
- `infra/**`, `migrations/**`
- `docs/plan/**` (이건 절대 금지에 가깝다 — 사용자 명시 승인 없이는 reject)

→ 위반 시: **review_required**, 변경 이유 확인

**allowed_paths** (positive):
- `devforge/**`, `tests/**`, `docs/**` (단 `docs/plan/`은 별도 규칙), `pyproject.toml`, `README.md`, `.claude/**`, `_workspace/**`, `.gitignore`

### 2. Command Policy

검사 대상: 최근 세션의 Bash 도구 호출 흔적, 새로 만든 스크립트, README/CONTRIBUTING의 명령 예시

**blocked patterns**:
- `rm -rf`
- `git push` (사용자 명시 요청 없이)
- `git reset --hard`
- `curl ... | sh`, `wget ... | sh`
- `sudo`
- `docker system prune`
- `Remove-Item -Recurse -Force`

→ 위반 시: **critical**

**require_human_review**:
- `npm install`, `pip install`, `poetry add`, `uv add` (의존성 추가)
- `docker compose up`
- `terraform apply`, `kubectl apply`

### 3. Secret Scanner

변경된 파일 내용 + 새 파일 + 문서에서 탐지:

- API key 패턴: `sk-[A-Za-z0-9]{20,}`, `sk-ant-[A-Za-z0-9-]{20,}`, `AIza[0-9A-Za-z\-_]{35}`
- AWS: `AKIA[0-9A-Z]{16}`
- 일반: `password\s*=\s*["'][^"']+`, `token\s*=\s*["'][^"']+`
- `.env` 내용 패턴
- JWT 모양: `eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}`
- private key: `-----BEGIN .* PRIVATE KEY-----`

→ 탐지 시: **critical**. 사용자에게 즉시 알리고 해당 부분 즉시 redact 권고. commit 됐다면 history rewrite도 고려해야 함을 명시 (단 직접 실행은 X).

### 4. Test Mutation

- `tests/` 하위 파일 삭제 여부
- `pytest.mark.skip` / `pytest.mark.xfail` 새로 추가
- `assert ... or True`, `assert True` 같은 무의미 assert
- threshold 완화 (숫자가 작아진 비교)

→ 위반 시: **review_required**, 정당한 이유 없으면 reject

## 절차

1. `git status` + `git diff`로 변경분 수집
2. 위 4개 카테고리 검사
3. 결과를 리포트로 출력 (콘솔 출력으로 충분, 영구 저장은 필요한 경우만 `_workspace/scratch/`)
4. critical 발견 시 사용자에게 강조해서 알림

## 출력 포맷

```
=== devf policy dogfood ===
Files changed: <n>
File policy:        PASS | <n violations>
Command policy:     PASS | <n violations>
Secret scan:        PASS | <n hits>  ← critical시 강조
Test mutation:      PASS | <n flags>

Critical:
- <file>:<line> — <issue>

Review required:
- <file> — <reason>

Verdict: PASS | NEEDS_REVIEW | CRITICAL
```

## 금기

- 발견 시 자동으로 commit revert 하지 마라 — 사용자가 결정
- 실제 secret이 발견되면 **그 값을 리포트에 그대로 적지 마라**. 위치(파일:라인)만 보고, 값은 `[REDACTED]`
- 외부 네트워크 호출 금지 (오프라인 검사)
- `docs/plan/` 수정 금지
