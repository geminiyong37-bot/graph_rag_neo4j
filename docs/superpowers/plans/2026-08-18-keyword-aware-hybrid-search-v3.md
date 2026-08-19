# Keyword-Aware Hybrid Search V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 원문 질문의 Vector 검색과 핵심어 기반 엄격·확장 FTS 검색을 하나의 V3 RPC로 통합하고, 기존 Draft Agent가 전용 n8n 검색 도구를 통해 이를 사용하게 한다.

**Architecture:** V3 SQL은 원문 질문 임베딩, 핵심어 전체 AND, 핵심어 부분 AND+보조어 OR 후보를 독립적으로 만들고 RRF로 합산한다. n8n에서는 기존 Draft Agent를 유지하되, Vector Store 도구를 `Call n8n Workflow Tool`로 교체한다. 자식 워크플로우가 원문 질문만 임베딩하고 V3 RPC·Cohere 재정렬 결과를 하나의 `chunks` 객체로 반환한다.

**Tech Stack:** PostgreSQL, pgvector, Supabase RPC/PostgREST, n8n, OpenAI Embeddings (`text-embedding-3-small`), Cohere Rerank API, Python unittest

---

## 파일 구조

- Create: `supabase/sql/002_match_univ_documents_hybrid_v3.sql` — V3 SQL 함수
- Create: `tests/test_supabase_hybrid_v3_sql.py` — V3 SQL 구조·안전성 정적 테스트
- Modify: `supabase/run_hybrid_regression.py` — V2 기준과 V3 후보 비교기
- Modify: `tests/test_supabase_regression_runner.py` — V3 RPC payload·중복·회귀 판정 테스트
- Modify: `supabase/regression_cases.json` — 핵심어·보조어가 포함된 회귀 사례
- Create: `n8n/keyword-aware-search-v3-setup.md` — 실제 n8n 화면에서 만드는 자식·부모 도구 설정 기록

V1, V2 SQL 파일과 기존 `Supabase as AI Agent` 노드는 삭제하지 않는다.

### Task 1: V3 SQL 계약의 실패하는 테스트 작성

**Files:**
- Create: `tests/test_supabase_hybrid_v3_sql.py`

- [ ] **Step 1: V3 함수 인터페이스와 세 검색 경로를 검증하는 테스트를 작성한다.**

```python
import unittest
from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "sql"
    / "002_match_univ_documents_hybrid_v3.sql"
)


class SupabaseHybridV3SqlTests(unittest.TestCase):
    def read_sql(self):
        return SQL_PATH.read_text(encoding="utf-8")

    def test_v3_accepts_keyword_arrays_without_replacing_v1_or_v2(self):
        sql = self.read_sql()

        self.assertIn("match_univ_documents_hybrid_v3", sql)
        self.assertIn("core_keywords text[] default '{}'", sql)
        self.assertIn("optional_keywords text[] default '{}'", sql)
        self.assertNotIn("drop function", sql.lower())

    def test_v3_builds_vector_strict_and_expanded_routes(self):
        sql = self.read_sql()

        self.assertIn("vector_search as", sql)
        self.assertIn("strict_search as", sql)
        self.assertIn("expanded_search as", sql)
        self.assertIn("union all", sql)
        self.assertIn("group by d.id, d.content, d.metadata", sql)
        self.assertIn("0.70::float8", sql)
        self.assertIn("1.00::float8", sql)
        self.assertIn("0.40::float8", sql)

    def test_v3_uses_safe_tokenization_not_raw_ai_tsquery(self):
        sql = self.read_sql()

        self.assertIn("to_tsvector('simple', btrim(keyword))", sql)
        self.assertIn("tsvector_to_array", sql)
        self.assertIn("quote_literal(lexeme)", sql)
        self.assertNotIn("to_tsquery('simple', query_text)", sql)

    def test_v3_handles_two_and_three_core_keyword_expansion(self):
        sql = self.read_sql()

        self.assertIn("when 2 then", sql)
        self.assertIn("when 3 then", sql)
        self.assertIn("optional_group_queries", sql)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다.**

Run:

```powershell
python -m unittest tests.test_supabase_hybrid_v3_sql -v
```

Expected: `FileNotFoundError` 또는 V3 SQL 파일 부재로 FAIL.

- [ ] **Step 3: 테스트 파일을 커밋한다.**

```powershell
git add tests/test_supabase_hybrid_v3_sql.py
git commit -m "test: define V3 hybrid search contract"
```

### Task 2: V3 SQL 함수 구현

**Files:**
- Create: `supabase/sql/002_match_univ_documents_hybrid_v3.sql`

- [ ] **Step 1: V3의 함수 시그니처와 안전한 키워드 그룹 생성을 작성한다.**

아래 구현을 파일에 넣는다. `core_group_queries`와 `optional_group_queries`는 키워드 하나를 하나의 괄호 그룹으로 보존한다. 예를 들어 `교육용 건물`은 토큰을 OR로 분해하지 않고 `('교육용' & '건물')`로 유지한다.

```sql
create or replace function match_univ_documents_hybrid_v3 (
  query_embedding vector(1536),
  match_count int,
  filter jsonb default '{}',
  query_text text default '',
  core_keywords text[] default '{}',
  optional_keywords text[] default '{}'
)
returns table (
  id int8,
  content text,
  metadata jsonb,
  similarity float
)
language plpgsql
stable
as $$
declare
  safe_match_count int := greatest(1, least(coalesce(match_count, 20), 50));
  core_group_queries text[] := array[]::text[];
  optional_group_queries text[] := array[]::text[];
  strict_query tsquery := null;
  expanded_query tsquery := null;
  core_count int := 0;
begin
  select coalesce(array_agg(group_query order by first_ordinal), array[]::text[])
  into core_group_queries
  from (
    select group_query, min(ordinality) as first_ordinal
    from (
      select
        source.ordinality,
        '(' || string_agg(quote_literal(tokens.lexeme), ' & ' order by tokens.lexeme) || ')'
          as group_query
      from (
        select keyword, ordinality
        from unnest(coalesce(core_keywords, array[]::text[]))
          with ordinality as input(keyword, ordinality)
        where nullif(btrim(keyword), '') is not null
        order by ordinality
        limit 3
      ) as source
      cross join lateral (
        select lexeme
        from unnest(tsvector_to_array(to_tsvector('simple', btrim(source.keyword))))
          as token(lexeme)
        where char_length(lexeme) >= 2
        group by lexeme
        order by char_length(lexeme) desc, lexeme
        limit 4
      ) as tokens
      group by source.ordinality
    ) as raw_groups
    group by group_query
  ) as deduplicated_groups;

  select coalesce(array_agg(group_query order by first_ordinal), array[]::text[])
  into optional_group_queries
  from (
    select group_query, min(ordinality) as first_ordinal
    from (
      select
        source.ordinality,
        '(' || string_agg(quote_literal(tokens.lexeme), ' & ' order by tokens.lexeme) || ')'
          as group_query
      from (
        select keyword, ordinality
        from unnest(coalesce(optional_keywords, array[]::text[]))
          with ordinality as input(keyword, ordinality)
        where nullif(btrim(keyword), '') is not null
        order by ordinality
        limit 6
      ) as source
      cross join lateral (
        select lexeme
        from unnest(tsvector_to_array(to_tsvector('simple', btrim(source.keyword))))
          as token(lexeme)
        where char_length(lexeme) >= 2
        group by lexeme
        order by char_length(lexeme) desc, lexeme
        limit 4
      ) as tokens
      group by source.ordinality
    ) as raw_groups
    group by group_query
  ) as deduplicated_groups;

  core_count := cardinality(core_group_queries);
  if core_count > 0 then
    strict_query := array_to_string(core_group_queries, ' & ')::tsquery;
  end if;

  if cardinality(optional_group_queries) > 0 then
    case core_count
      when 2 then
        expanded_query := (
          '(' || array_to_string(core_group_queries, ' | ') || ') & ('
          || array_to_string(optional_group_queries, ' | ') || ')'
        )::tsquery;
      when 3 then
        expanded_query := (
          '((' || core_group_queries[1] || ' & ' || core_group_queries[2] || ') | '
          || '(' || core_group_queries[1] || ' & ' || core_group_queries[3] || ') | '
          || '(' || core_group_queries[2] || ' & ' || core_group_queries[3] || ')) & ('
          || array_to_string(optional_group_queries, ' | ') || ')'
        )::tsquery;
    end case;
  end if;
```

- [ ] **Step 2: 세 후보 경로와 RRF 중복 제거를 이어서 작성한다.**

```sql
  return query
  with vector_search as (
    select
      d.id,
      row_number() over (order by d.embedding <=> query_embedding) as rank
    from "대학 온라인 상담용 데이터" d
    where query_embedding is not null
      and d.embedding is not null
      and d.metadata @> coalesce(filter, '{}'::jsonb)
    order by d.embedding <=> query_embedding
    limit safe_match_count * 3
  ),
  strict_search as (
    select
      d.id,
      row_number() over (order by ts_rank_cd(d.fts, strict_query) desc) as rank
    from "대학 온라인 상담용 데이터" d
    where strict_query is not null
      and d.fts @@ strict_query
      and d.metadata @> coalesce(filter, '{}'::jsonb)
    order by ts_rank_cd(d.fts, strict_query) desc
    limit safe_match_count * 3
  ),
  expanded_search as (
    select
      d.id,
      row_number() over (order by ts_rank_cd(d.fts, expanded_query) desc) as rank
    from "대학 온라인 상담용 데이터" d
    where expanded_query is not null
      and d.fts @@ expanded_query
      and d.metadata @> coalesce(filter, '{}'::jsonb)
    order by ts_rank_cd(d.fts, expanded_query) desc
    limit safe_match_count * 3
  ),
  weighted_candidates as (
    select id, 0.70::float8 / (60 + rank) as score from vector_search
    union all
    select id, 1.00::float8 / (60 + rank) as score from strict_search
    union all
    select id, 0.40::float8 / (60 + rank) as score from expanded_search
  )
  select
    d.id,
    d.content,
    d.metadata,
    sum(weighted_candidates.score)::float as similarity
  from weighted_candidates
  join "대학 온라인 상담용 데이터" d on d.id = weighted_candidates.id
  group by d.id, d.content, d.metadata
  order by sum(weighted_candidates.score) desc, d.id asc
  limit safe_match_count;
end;
$$;
```

- [ ] **Step 3: V3 SQL 테스트를 통과시킨다.**

Run:

```powershell
python -m unittest tests.test_supabase_hybrid_v3_sql -v
```

Expected: 4 tests, `OK`.

- [ ] **Step 4: SQL을 Supabase SQL Editor에서 실행하고 V1·V2·V3 공존을 확인한다.**

```sql
select routine_name
from information_schema.routines
where routine_schema = 'public'
  and routine_name in (
    'match_univ_documents_hybrid',
    'match_univ_documents_hybrid_v2',
    'match_univ_documents_hybrid_v3'
  )
order by routine_name;
```

Expected: 세 함수가 모두 한 행씩 반환된다.

- [ ] **Step 5: SQL과 테스트를 커밋한다.**

```powershell
git add supabase/sql/002_match_univ_documents_hybrid_v3.sql tests/test_supabase_hybrid_v3_sql.py
git commit -m "feat: add keyword-aware hybrid search v3"
```

### Task 3: V2 대 V3 회귀 실행기 확장

**Files:**
- Modify: `supabase/run_hybrid_regression.py`
- Modify: `tests/test_supabase_regression_runner.py`
- Modify: `supabase/regression_cases.json`

- [ ] **Step 1: V3 payload·중복 ID·회귀 판정의 실패 테스트를 추가한다.**

```python
from supabase.run_hybrid_regression import (
    V3_FUNCTION,
    build_rpc_payload,
    has_duplicate_ids,
    has_regressed,
)


def test_build_v3_rpc_payload_keeps_original_question_and_keywords(self):
    case = {
        "question": "내부인원 식사비 처리 기준",
        "core_keywords": ["내부인원", "식사비"],
        "optional_keywords": ["교직원", "간담회"],
    }

    payload = build_rpc_payload([0.1, 0.2], case, V3_FUNCTION)

    self.assertEqual("내부인원 식사비 처리 기준", payload["query_text"])
    self.assertEqual(["내부인원", "식사비"], payload["core_keywords"])
    self.assertEqual(["교직원", "간담회"], payload["optional_keywords"])


def test_detects_duplicate_ids(self):
    self.assertTrue(has_duplicate_ids([{"id": 7}, {"id": 7}]))
    self.assertFalse(has_duplicate_ids([{"id": 7}, {"id": 8}]))


def test_detects_regression_when_baseline_passes_and_candidate_fails(self):
    self.assertTrue(has_regressed({"passed": True}, {"passed": False}))
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다.**

Run:

```powershell
python -m unittest tests.test_supabase_regression_runner -v
```

Expected: V3 상수·함수 부재로 FAIL.

- [ ] **Step 3: 실행기를 V2 기준·V3 후보 비교기로 바꾼다.**

```python
V2_FUNCTION = "match_univ_documents_hybrid_v2"
V3_FUNCTION = "match_univ_documents_hybrid_v3"


def build_rpc_payload(embedding, case, function_name):
    payload = {
        "query_embedding": embedding,
        "match_count": case.get("match_count", 20),
        "filter": case.get("filter", {}),
        "query_text": case["question"],
    }
    if function_name == V3_FUNCTION:
        payload["core_keywords"] = case.get("core_keywords", [])
        payload["optional_keywords"] = case.get("optional_keywords", [])
    return payload


def has_regressed(baseline_result, candidate_result):
    return baseline_result["passed"] and not candidate_result["passed"]


def has_duplicate_ids(rows):
    ids = [row.get("id") for row in rows if row.get("id") is not None]
    return len(ids) != len(set(ids))
```

`run_regression()`은 각 사례에서 V2와 V3을 호출하고, V3이 정답을 놓치거나 중복 ID를 반환하면 종료 코드 1을 반환한다. 출력 열은 `case`, `V2`, `V2 rank`, `V3`, `V3 rank`, `V3 duplicate ids`, `seconds` 순서로 둔다.

- [ ] **Step 4: 회귀 사례에 키워드 배열을 추가한다.**

첫 사례는 아래처럼 수정한다.

```json
{
  "name": "내부인원 식사비 분류",
  "question": "법인이 설치학교 교직원 등 내부인원과 간담회, 업무협의 또는 직원 격려 등을 목적으로 식사를 제공하는 경우 복리후생비 또는 업무추진비 중 어떻게 처리해야 하는가?",
  "core_keywords": ["내부인원", "식사비"],
  "optional_keywords": ["교직원", "간담회", "복리후생비", "업무추진비"],
  "expected_text": "교직원 내부 인원에 대한 식사비",
  "expected_filename": "특례규칙",
  "match_count": 20
}
```

추가로 아래 세 입력은 `tests/test_supabase_hybrid_v3_sql.py`의 SQL 분기 검증 대상으로 넣어 라이브 데이터 의존 없이 보장한다.

```python
KEYWORD_BRANCH_CASES = [
    (["내부인원", "식사비"], ["교직원"], "two-core"),
    (["교직원", "내부인원", "식사비"], ["간담회"], "three-core"),
    (["내부인원"], [], "no-expanded-route"),
    ([" 내부인원 ", "내부인원", "'식사비'"], [""], "safe-normalization"),
]
```

- [ ] **Step 5: 전체 로컬 테스트를 통과시킨다.**

Run:

```powershell
python -m unittest discover -s tests -v
python -m py_compile supabase/run_hybrid_regression.py
git diff --check
```

Expected: 모든 테스트 `OK`, 문법·공백 오류 없음.

- [ ] **Step 6: 회귀 실행기와 사례를 커밋한다.**

```powershell
git add supabase/run_hybrid_regression.py supabase/regression_cases.json tests/test_supabase_regression_runner.py tests/test_supabase_hybrid_v3_sql.py
git commit -m "test: compare keyword-aware search v3"
```

### Task 4: n8n Keyword-Aware Search V3 서브워크플로우 구성

**Files:**
- Create: `n8n/keyword-aware-search-v3-setup.md`

- [ ] **Step 1: 자식 워크플로우 입력 스키마를 만든다.**

새 워크플로우 이름은 `Keyword-Aware Search V3`로 한다. 첫 노드는 `When Executed by Another Workflow`이며, `Define using JSON example`에 아래를 넣는다.

```json
{
  "question": {
    "제목": "",
    "1.사실관계": "",
    "2.질의사항": "",
    "3.관련법령": ""
  },
  "core_keywords": [],
  "optional_keywords": []
}
```

- [ ] **Step 2: `Prepare query` Code 노드를 추가한다.**

이 노드는 원문 질문만으로 `question_text`를 만들고 키워드 배열을 정규화한다. 키워드를 `question_text`에 합치지 않는다.

```javascript
const question = $json.question ?? {};
const asText = (value) => typeof value === 'string' ? value.trim() : '';
const normalizeKeywords = (value, maxLength) => [
  ...new Set((Array.isArray(value) ? value : [])
    .map(asText)
    .filter(Boolean)),
].slice(0, maxLength);

const questionText = [
  ['제목', asText(question['제목'])],
  ['사실관계', asText(question['1.사실관계'])],
  ['질의사항', asText(question['2.질의사항'])],
  ['관련법령', asText(question['3.관련법령'])],
]
  .filter(([, value]) => value)
  .map(([label, value]) => `${label}: ${value}`)
  .join('\n');

if (!questionText) {
  throw new Error('검색할 원문 질의가 없습니다.');
}

return [{
  json: {
    question_text: questionText,
    core_keywords: normalizeKeywords($json.core_keywords, 3),
    optional_keywords: normalizeKeywords($json.optional_keywords, 6),
    match_count: 20,
    filter: {},
  },
}];
```

- [ ] **Step 3: 원문 질문 임베딩 HTTP 요청을 구성한다.**

`HTTP Request` 노드를 추가하고 기존 OpenAI API 자격 증명을 사용한다. 다음 요청만 보낸다.

```text
POST https://api.openai.com/v1/embeddings
Content-Type: application/json
```

```json
{
  "model": "text-embedding-3-small",
  "input": "={{ $json.question_text }}"
}
```

응답 뒤 Code 노드 `Attach embedding`에서 `Prepare query`의 정규화 값과 임베딩을 합친다.

```javascript
const search = $('Prepare query').first().json;
const embedding = $json.data?.[0]?.embedding;

if (!Array.isArray(embedding) || embedding.length !== 1536) {
  throw new Error('text-embedding-3-small 임베딩(1536차원)을 받지 못했습니다.');
}

return [{ json: { ...search, query_embedding: embedding } }];
```

- [ ] **Step 4: V3 Supabase RPC HTTP 요청을 구성한다.**

두 번째 `HTTP Request` 노드를 추가한다. 기존 Supabase API 자격 증명을 선택하고 다음 JSON 본문을 사용한다.

```json
{
  "query_embedding": "={{ $json.query_embedding }}",
  "query_text": "={{ $json.question_text }}",
  "core_keywords": "={{ $json.core_keywords }}",
  "optional_keywords": "={{ $json.optional_keywords }}",
  "match_count": "={{ $json.match_count }}",
  "filter": "={{ $json.filter }}"
}
```

URL은 해당 Supabase 프로젝트의 아래 RPC 엔드포인트다.

```text
https://<project-ref>.supabase.co/rest/v1/rpc/match_univ_documents_hybrid_v3
```

- [ ] **Step 5: Cohere 재정렬과 단일 도구 응답을 구성한다.**

V3 RPC 결과를 후보 순서대로 20개 이하로 제한한 뒤, `POST https://api.cohere.com/v2/rerank` 요청을 보낸다. 기존 Cohere API 자격 증명을 사용한다.

```json
{
  "model": "rerank-multilingual-v3.0",
  "query": "={{ $('Prepare query').first().json.question_text }}",
  "documents": "={{ $json.map(row => row.content) }}",
  "top_n": 7
}
```

응답의 각 `results[index]`를 RPC 후보 배열의 동일한 인덱스에 연결하고, 마지막 Code 노드에서 하나의 item만 반환한다.

```javascript
const candidates = $('Supabase V3 RPC').all().map(item => item.json);
const rerankResults = $json.results ?? [];
const chunks = rerankResults.map(({ index, relevance_score }) => ({
  ...candidates[index],
  rerank_score: relevance_score,
}));

return [{ json: { chunks } }];
```

Expected: 이 서브워크플로우의 마지막 노드는 항상 `{ "chunks": [...] }` 한 item을 반환한다. Cohere Rerank API는 전달한 문서 배열의 `index`와 관련도 점수를 돌려준다. [Cohere Rerank API](https://docs.cohere.com/v2/reference/rerank)

- [ ] **Step 6: 부모 Draft Agent에 V3 도구를 연결한다.**

부모 워크플로우에 `Call n8n Workflow Tool`을 추가하고 위 자식 워크플로우를 선택한다. 자식 입력 스키마를 만든 뒤 부모에서 `Refresh`한다. 입력에는 `$fromAI()`를 쓰지 않고 다음 고정 표현식을 사용한다.

```text
question
={{ $('Key Words Extract AI').first().json.output.question }}

core_keywords
={{ $('Key Words Extract AI').first().json.output.core_keywords }}

optional_keywords
={{ $('Key Words Extract AI').first().json.output.optional_keywords ?? [] }}
```

이 도구의 `ai_tool` 출력을 `Draft Answer Generator`의 도구 포트에 연결한다. 기존 `Supabase as AI Agent`의 `ai_tool` 연결은 끊되 노드는 삭제하지 않는다.

도구 설명은 아래로 설정한다.

```text
사립대학 재무·회계 DB에서 답변 근거 청크를 검색하는 도구다.
답변 작성 전에 반드시 한 번 호출한다.
질문과 핵심·보조 키워드는 이미 전달되므로 새 키워드를 만들지 않는다.
```

- [ ] **Step 7: Draft Agent 프롬프트의 검색 지시를 바꾼다.**

기존의 아래 두 줄을 삭제한다.

```text
- 먼저 core_keywords만 공백으로 연결하여 검색해.
- 첫 검색에서 직접 적용할 기준을 찾지 못한 경우에만 core_keywords와 optional_keywords를 함께 검색해.
```

대신 아래를 넣는다.

```text
- 답변 전에 Keyword-Aware Search V3 도구를 반드시 한 번 호출해.
- 도구에는 원문 질의와 핵심·보조 키워드가 이미 고정값으로 전달된다. 검색어를 새로 만들거나 JSON·키워드 목록을 도구 입력으로 작성하지 마.
- 도구가 반환한 chunks의 본문과 metadata에 확인되는 내용만 근거로 사용해.
```

- [ ] **Step 8: 실제 n8n 구성값을 저장소 문서에 기록한다.**

`n8n/keyword-aware-search-v3-setup.md`에 노드 이름, 연결 방향, 입력 스키마, 각 HTTP 요청 본문, 기존 도구로 복구하는 방법을 위 내용 그대로 기록한다. API 키·Supabase URL·워크플로우 ID는 기록하지 않는다.

- [ ] **Step 9: n8n 설정 변경을 별도 커밋한다.**

```powershell
git add n8n/keyword-aware-search-v3-setup.md
git commit -m "docs: document keyword-aware n8n retrieval"
```

### Task 5: 실제 검증과 롤백 확인

**Files:**
- Modify: `supabase/regression_cases.json` (검증 사례 추가 시)

- [ ] **Step 1: V2·V3 회귀 비교를 실행한다.**

새 Supabase SQL 함수를 배포한 뒤, 실제 키를 현재 셸 환경 변수에만 설정하고 실행한다.

```powershell
$env:SUPABASE_URL='https://your-project.supabase.co'
$env:SUPABASE_ANON_KEY='new-anon-key'
python supabase/run_hybrid_regression.py
```

Expected: V3이 필수 사례를 통과하고 `V3 duplicate ids`가 모두 `False`이며 종료 코드가 0이다.

- [ ] **Step 2: n8n에서 핵심 사례를 실행한다.**

입력 질의는 아래를 사용한다.

```text
법인이 설치학교 교직원 등 내부인원과 간담회, 업무협의 또는 직원 격려 등을 목적으로 식사를 제공하는 경우 복리후생비 또는 업무추진비 중 어떻게 처리해야 하는가?
```

Expected:

- Key Words Extract AI가 `내부인원`, `식사비`를 핵심어로 출력한다.
- V3 도구가 한 번 호출된다.
- OpenAI Embeddings 요청 본문에는 원문 질문만 있고 키워드 JSON이 없다.
- V3 RPC 본문에는 `core_keywords`와 `optional_keywords`가 배열로 존재한다.
- 도구 결과에는 중복 청크 ID가 없다.
- 답변은 검색된 특례규칙 근거에 따라 내부인원 식사비와 외부인원 식사비를 구분하며, 검색 본문에 없는 예외를 만들지 않는다.

- [ ] **Step 3: 실패 시 안전하게 롤백한다.**

부모 `Draft Answer Generator`에서 `Keyword-Aware Search V3`의 `ai_tool` 연결을 끊고, 기존 `Supabase as AI Agent`의 `ai_tool` 연결을 다시 연결한다. V1·V2·V3 SQL 함수는 삭제하지 않는다.

- [ ] **Step 4: 최종 변경 검증과 커밋을 수행한다.**

Run:

```powershell
python -m unittest discover -s tests -v
git diff --check
git -c safe.directory='C:/Users/회계9/graph_rag/.worktrees/supabase-hybrid-v2' status --short
```

Expected: 테스트가 모두 통과하고 공백 오류가 없으며, 의도한 파일만 변경되어 있다.
