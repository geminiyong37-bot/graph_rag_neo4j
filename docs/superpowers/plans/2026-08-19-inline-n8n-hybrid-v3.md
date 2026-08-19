# Inline n8n Hybrid Search V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 n8n 워크플로우 안에서 원문 질문 임베딩과 핵심·보조 키워드를 V3 Supabase RPC에 직접 전달하고, Cohere 재정렬 결과를 Draft Agent가 근거로 사용하게 한다.

**Architecture:** `Key Words Extract AI` 뒤에 준비, OpenAI 임베딩 HTTP, V3 RPC HTTP, Cohere 재정렬 HTTP, 결과 정리 노드를 직렬로 삽입한다. 기존 Supabase Vector Store AI 도구 연결은 제거하되 노드는 롤백용으로 보존한다.

**Tech Stack:** n8n workflow JSON, OpenAI Embeddings API, Supabase PostgREST RPC, Cohere Rerank API

---

### Task 1: 변환 계약 테스트

**Files:**
- Create: `tests/test_n8n_inline_hybrid_v3_workflow.py`
- Create: `n8n/build_inline_hybrid_v3_workflow.py`

- [ ] 원본 워크플로우를 변환한 결과에 5개 검색 노드가 존재하는지 검사한다.
- [ ] 질문만 OpenAI 임베딩 요청에 들어가는지 검사한다.
- [ ] V3 RPC 본문에 `query_embedding`, `query_text`, `core_keywords`, `optional_keywords`, `match_count`, `filter`가 모두 있는지 검사한다.
- [ ] 기존 Supabase AI 도구 연결이 끊기고 `Key Words Extract AI`에서 새 검색 흐름을 거쳐 Draft Agent로 연결되는지 검사한다.
- [ ] Draft Agent 프롬프트가 `chunks`만 근거로 사용하도록 바뀌는지 검사한다.

### Task 2: 워크플로우 변환기와 산출물 생성

**Files:**
- Create: `n8n/build_inline_hybrid_v3_workflow.py`
- Create: `n8n/univ-inline-hybrid-v3.workflow.json`

- [ ] 첨부된 원본 JSON을 읽고 기존 노드·자격증명을 보존한다.
- [ ] `Prepare Hybrid Search`, `OpenAI Query Embedding`, `Build Supabase V3 Payload`, `Supabase V3 RPC`, `Cohere Rerank`, `Attach Reranked Chunks` 노드를 추가한다.
- [ ] OpenAI, Supabase, Cohere의 기존 자격증명 ID를 HTTP Request 노드에 재사용한다.
- [ ] Supabase URL은 로컬 환경설정에서 읽되 API 키는 산출물에 기록하지 않는다.
- [ ] 기존 Draft Agent의 Supabase AI 도구 연결을 끊고 검색 결과를 주 입력으로 연결한다.

### Task 3: 검증

**Files:**
- Test: `tests/test_n8n_inline_hybrid_v3_workflow.py`
- Verify: `n8n/univ-inline-hybrid-v3.workflow.json`

- [ ] `python -m unittest tests.test_n8n_inline_hybrid_v3_workflow -v`를 실행해 모두 통과시킨다.
- [ ] Python으로 산출물 JSON을 다시 파싱해 유효성을 확인한다.
- [ ] `git diff --check`로 공백 오류와 의도하지 않은 변경을 확인한다.
- [ ] n8n 가져오기 후 HTTP 자격증명과 노드별 요청 본문을 확인하는 사용 안내를 함께 제공한다.
