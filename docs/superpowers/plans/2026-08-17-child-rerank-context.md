# Child-Centered Cohere Reranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cohere가 부모 전체 본문 대신 부모 제목과 자식 청크를 평가하고, 최종 LLM에는 선택된 자식 근거를 부모 문맥보다 먼저 전달한다.

**Architecture:** Neo4j 검색 결과에 부모 제목을 추가하고, Cohere 입력을 `파일명 + 부모 제목 + 자식 청크`로 제한한다. Cohere가 선택한 여러 자식 청크는 부모별로 묶되 모두 직접 근거로 유지하고, 부모 전체 본문은 보충 문맥으로 한 번만 제공한다.

**Tech Stack:** Python, Neo4j Cypher, Cohere Rerank API, unittest

---

### Task 1: Cohere 입력과 최종 문맥의 회귀 테스트

**Files:**
- Create: `tests/test_rerank_context.py`
- Modify: `3_ask_graph.py`

- [x] **Step 1: 실패하는 테스트 작성**

`build_rerank_passage()`가 부모 전체 본문을 제외하고 파일명, 부모 제목, 자식 본문만 반환하는지 검사한다. `group_chunks_by_parent()`가 동일 부모의 여러 자식 근거를 보존하는지 검사한다.

- [x] **Step 2: 실패 확인**

Run: `python -m unittest tests.test_rerank_context -v`

Expected: 두 헬퍼가 아직 없어 FAIL.

- [x] **Step 3: 최소 구현**

`3_ask_graph.py`에 두 순수 함수를 추가하고 검색 쿼리에서 `parent.title`을 반환한다. Cohere passage 생성과 최종 부모 병합 로직이 두 함수를 사용하도록 바꾼다.

- [x] **Step 4: 단위 테스트 통과 확인**

Run: `python -m unittest tests.test_rerank_context -v`

Expected: PASS.

- [x] **Step 5: 문법 검사와 변경 범위 확인**

Run: `python -m py_compile 3_ask_graph.py tests/test_rerank_context.py`

Run: `git diff --check`

Expected: exit code 0.
