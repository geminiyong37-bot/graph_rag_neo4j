# Main Consolidation and Repository Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 현재 GraphRAG의 Neo4j·n8n·Supabase 자산과 미커밋 작업을 보존하면서 작업 브랜치를 `main`에 통합하고, 명백한 포크 실습 잔재와 개인 도구 설정을 제거한다.

**Architecture:** `main`의 미커밋 답변 검증 작업을 먼저 독립 커밋으로 보존하고 작업 브랜치에 병합한다. 정리는 과거 이력을 재작성하지 않는 일반 삭제·문서 수정 커밋으로 수행한 뒤, 검증된 결과를 `main`에 fast-forward 가능한 형태로 푸시한다. 원격 반영을 확인한 뒤에만 작업 브랜치와 worktree를 제거한다.

**Tech Stack:** Git, Python, unittest, Neo4j GraphRAG, Supabase PostgreSQL/pgvector, n8n, Markdown

---

### Task 1: 로컬 main의 미커밋 작업 보존

**Files:**
- Modify: `3_ask_graph.py`
- Modify: `tests/test_rerank_context.py`
- Preserve locally: `.claude/settings.local.json`

- [ ] **Step 1: 변경 범위 확인**

Run:

```powershell
git status -sb
git diff -- 3_ask_graph.py tests/test_rerank_context.py
```

Expected: 답변 검증 가드레일과 그 단위 테스트만 표시되고 `.claude/settings.local.json`은 추적되지 않는다.

- [ ] **Step 2: 변경 코드 검증**

Run:

```powershell
python -m unittest tests.test_rerank_context -v
python -m py_compile 3_ask_graph.py tests/test_rerank_context.py
```

Expected: 모든 테스트가 통과하고 두 Python 파일의 문법 오류가 없다.

- [ ] **Step 3: 관련 파일만 커밋**

Run:

```powershell
git add -- 3_ask_graph.py tests/test_rerank_context.py
git diff --cached --check
git commit -m "fix: verify answers against retrieved evidence"
```

Expected: `.claude/settings.local.json`을 제외한 두 파일만 새 커밋에 포함된다.

### Task 2: 작업 브랜치에 main 변경 통합

**Files:**
- Modify through merge: `3_ask_graph.py`
- Modify through merge: `tests/test_rerank_context.py`

- [ ] **Step 1: 작업 브랜치 상태 확인**

Run:

```powershell
git status -sb
git branch --show-current
```

Expected: `feature/supabase-hybrid-v2`이며 계획 문서 외의 예기치 않은 변경이 없다.

- [ ] **Step 2: 로컬 main 병합**

Run:

```powershell
git merge main
```

Expected: 충돌 없이 main의 답변 검증 커밋이 작업 브랜치에 포함된다.

### Task 3: 명백한 포크 잔재와 일회성 파일 정리

**Files:**
- Delete: `.cursor/rules/karpathy-guidelines.mdc`
- Delete: `CLAUDE.md`
- Delete: `2_build_graph.py`
- Delete: `inspect_chunk_sizes.py`
- Delete: `update_file_name.py`
- Delete: `requirements_macos.txt`
- Modify: `.gitignore`

- [ ] **Step 1: 삭제 대상 의존성 재확인**

Run:

```powershell
rg -n "2_build_graph\.py|inspect_chunk_sizes|update_file_name|requirements_macos|karpathy-guidelines|CLAUDE\.md" --glob '!docs/superpowers/plans/2026-08-19-main-consolidation-and-cleanup.md'
```

Expected: 실행 코드의 import 의존성이 없고, 구형 README 또는 파일 자체 외에는 참조가 없다.

- [ ] **Step 2: 대상 파일 삭제 및 로컬 산출물 제외**

`.gitignore`에 다음 항목을 추가한다.

```gitignore
.claude/
.n8n/
node_modules/
*.sqlite
*.sqlite3
```

그 뒤 위의 여섯 삭제 대상 파일을 Git에서 제거한다.

- [ ] **Step 3: 보존 자산 확인**

Run:

```powershell
git status --short
```

Expected: `data/**`, `2_build_graph_from_md.py`, `3_ask_graph.py`, `main.py`, `Procfile`, `supabase/**`, `tests/**`가 삭제 목록에 없다.

### Task 4: 프로젝트 문서와 의존성 정리

**Files:**
- Modify: `README.md`
- Modify: `requirements.txt`
- Modify: `docs/superpowers/plans/2026-08-18-keyword-aware-hybrid-search-v3.md`

- [ ] **Step 1: README를 현재 구조로 교체**

README에는 다음을 명시한다.

- 대학·사학기관 회계 상담용 GraphRAG라는 목적
- Neo4j 검색/API와 Supabase 하이브리드 검색 실험의 역할
- V2는 구현되어 있고 V3는 설계·계획 상태라는 현재 진행도
- 핵심 디렉터리와 실행·테스트 명령
- `.env`를 커밋하지 않는다는 보안 안내

- [ ] **Step 2: 깨진 requirements 인코딩 복구**

`requirements.txt`의 NUL 문자가 섞인 마지막 줄을 정상 UTF-8의 아래 한 줄로 바꾼다.

```text
cohere==7.0.8
```

- [ ] **Step 3: 개인화된 n8n 자격 증명 표시명 일반화**

V3 계획서의 표시명을 다음처럼 바꾼다.

```text
OpenAi account → OpenAI API 자격 증명
Hybrid Search for QnA → Supabase API 자격 증명
CohereApi geminiyong → Cohere API 자격 증명
```

### Task 5: 정리 결과 검증과 커밋

**Files:**
- Test: `tests/test_rerank_context.py`
- Test: `tests/test_supabase_hybrid_sql.py`
- Test: `tests/test_supabase_regression_runner.py`

- [ ] **Step 1: 전체 단위 테스트**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 모든 테스트가 통과한다.

- [ ] **Step 2: Python 문법과 저장소 텍스트 검사**

Run:

```powershell
python -m py_compile 1_test_connection.py 2_build_graph_from_md.py 3_ask_graph.py main.py supabase/run_hybrid_regression.py
git diff --check
```

Expected: 문법 오류, NUL 문자, 공백 오류가 없다.

- [ ] **Step 3: 정리 커밋 생성**

Run:

```powershell
git add --all
git diff --cached --check
git commit -m "chore: consolidate GraphRAG repository"
```

Expected: 계획된 삭제·문서·설정 변경만 포함된다.

### Task 6: main 승격과 원격 정리

**Files:**
- Git refs: `main`
- Git refs: `feature/supabase-hybrid-v2`

- [ ] **Step 1: main을 정리 커밋으로 fast-forward**

Run from the main worktree:

```powershell
git merge --ff-only feature/supabase-hybrid-v2
python -m unittest discover -s tests -v
```

Expected: main이 작업 브랜치의 최신 커밋을 가리키고 전체 테스트가 통과한다.

- [ ] **Step 2: main 푸시**

Run:

```powershell
git push origin main
```

Expected: 강제 푸시 없이 원격 `main`이 새 커밋으로 갱신된다.

- [ ] **Step 3: 원격 확인 후 작업 브랜치 정리**

Run:

```powershell
git ls-remote --heads origin main feature/supabase-hybrid-v2
git push origin --delete feature/supabase-hybrid-v2
git fetch --prune origin
```

Expected: `main`만 최신 커밋을 가리키고 원격 작업 브랜치는 제거된다.

- [ ] **Step 4: worktree와 로컬 브랜치 정리**

작업 브랜치가 main에 완전히 포함됐는지 확인한 뒤 연결된 worktree를 제거하고 로컬 작업 브랜치를 안전 삭제한다. 로컬 `.env`와 `.claude/settings.local.json`은 main 작업공간에 그대로 둔다.

Run:

```powershell
git branch --merged main
git status -sb
```

Expected: `main`이 원격과 일치하고 추적 파일의 미커밋 변경이 없다.
