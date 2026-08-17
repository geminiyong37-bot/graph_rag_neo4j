# Supabase Hybrid Search V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 Supabase 검색 함수를 보존하면서 한국어 OR 전문검색을 사용하는 V2 함수를 추가하고, V1과 V2의 검색 품질을 자동 비교한다.

**Architecture:** 데이터베이스 변경은 독립 SQL 파일로 제공하며 사용자가 Supabase SQL Editor에서 실행한다. 회귀 테스트 도구는 OpenAI에서 질문 임베딩을 생성한 뒤 Supabase RPC로 V1과 V2를 각각 호출하고, 기대 문구·파일명 포함 여부와 순위를 비교한다.

**Tech Stack:** PostgreSQL, pgvector, Supabase RPC/PostgREST, Python unittest, OpenAI Embeddings

---

## 파일 구조

- `supabase/sql/001_match_univ_documents_hybrid_v2.sql`: V2 함수 생성 및 기본 안전 검증
- `supabase/regression_cases.json`: 검색 회귀 테스트 질문과 기대 결과
- `supabase/run_hybrid_regression.py`: V1·V2 RPC 비교 실행기
- `tests/test_supabase_regression_runner.py`: 비교·판정 로직 단위 테스트
- `.env_example`: 회귀 실행에 필요한 Supabase 환경변수 이름 안내

### Task 1: V2 SQL 함수 작성

**Files:**
- Create: `supabase/sql/001_match_univ_documents_hybrid_v2.sql`

- [ ] **Step 1: 반환 형식과 검색 제한을 정의한다**

다음 서명으로 기존 함수와 반환 형식을 동일하게 유지한다.

```sql
create or replace function match_univ_documents_hybrid_v2 (
  query_embedding vector(1536),
  match_count int,
  filter jsonb default '{}',
  query_text text default ''
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
  fts_query tsquery := null;
begin
```

- [ ] **Step 2: 안전한 OR FTS 쿼리를 만든다**

`plainto_tsquery`가 정리한 검색식을 텍스트로 변환한 뒤 AND 연산자만 OR로 바꾼다. 빈 문자열은 `NULL`로 유지한다.

```sql
  if nullif(btrim(query_text), '') is not null then
    fts_query := replace(
      plainto_tsquery('simple', query_text)::text,
      ' & ',
      ' | '
    )::tsquery;
  end if;
```

- [ ] **Step 3: Vector·FTS 후보를 RRF로 결합한다**

```sql
  return query
  with vector_search as (
    select
      d.id,
      row_number() over (order by d.embedding <=> query_embedding) as rank
    from "대학 온라인 상담용 데이터" d
    where d.metadata @> coalesce(filter, '{}'::jsonb)
      and d.embedding is not null
    order by d.embedding <=> query_embedding
    limit safe_match_count * 3
  ),
  fts_search as (
    select
      d.id,
      row_number() over (order by ts_rank_cd(d.fts, fts_query) desc) as rank
    from "대학 온라인 상담용 데이터" d
    where fts_query is not null
      and d.fts @@ fts_query
      and d.metadata @> coalesce(filter, '{}'::jsonb)
    order by ts_rank_cd(d.fts, fts_query) desc
    limit safe_match_count * 3
  )
  select
    d.id,
    d.content,
    d.metadata,
    (
      coalesce(1.0 / (60 + v.rank), 0.0)
      + coalesce(1.0 / (60 + f.rank), 0.0)
    )::float as similarity
  from "대학 온라인 상담용 데이터" d
  left join vector_search v on d.id = v.id
  left join fts_search f on d.id = f.id
  where v.id is not null or f.id is not null
  order by similarity desc
  limit safe_match_count;
end;
$$;
```

- [ ] **Step 4: SQL Editor에서 V2만 생성한다**

Run: `supabase/sql/001_match_univ_documents_hybrid_v2.sql` 전체를 Supabase SQL Editor에서 실행

Expected: `Success. No rows returned`

- [ ] **Step 5: 함수 존재와 기존 함수 보존을 확인한다**

```sql
select routine_name
from information_schema.routines
where routine_schema = 'public'
  and routine_name in (
    'match_univ_documents_hybrid',
    'match_univ_documents_hybrid_v2'
  )
order by routine_name;
```

Expected: V1과 V2 두 행이 모두 반환된다.

- [ ] **Step 6: SQL 파일만 커밋한다**

```powershell
git add supabase/sql/001_match_univ_documents_hybrid_v2.sql
git commit -m "feat: add Supabase hybrid search v2"
```

### Task 2: 회귀 판정 로직을 테스트 주도로 작성

**Files:**
- Create: `tests/test_supabase_regression_runner.py`
- Create: `supabase/run_hybrid_regression.py`

- [ ] **Step 1: 실패하는 단위 테스트를 작성한다**

```python
import unittest

from supabase.run_hybrid_regression import evaluate_result


class EvaluateResultTests(unittest.TestCase):
    def test_passes_when_expected_text_and_filename_are_found(self):
        rows = [{
            "content": "교직원 내부 인원에 대한 식사비는 복리후생비로 처리한다.",
            "metadata": {"filename": "특례규칙 해설서.md"},
        }]

        result = evaluate_result(
            rows,
            expected_text="내부 인원에 대한 식사비",
            expected_filename="특례규칙",
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["rank"], 1)

    def test_fails_when_expected_evidence_is_missing(self):
        result = evaluate_result(
            [{"content": "무관한 내용", "metadata": {}}],
            expected_text="내부 인원에 대한 식사비",
            expected_filename="특례규칙",
        )

        self.assertFalse(result["passed"])
        self.assertIsNone(result["rank"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run:

```powershell
python -m unittest tests.test_supabase_regression_runner -v
```

Expected: `ModuleNotFoundError` 또는 `evaluate_result` 미정의로 FAIL

- [ ] **Step 3: 최소 판정 함수를 구현한다**

```python
def evaluate_result(rows, expected_text, expected_filename):
    for rank, row in enumerate(rows, 1):
        content = row.get("content") or ""
        metadata = row.get("metadata") or {}
        filename = metadata.get("filename") or ""
        if expected_text in content and expected_filename in filename:
            return {"passed": True, "rank": rank}
    return {"passed": False, "rank": None}
```

- [ ] **Step 4: 단위 테스트 통과를 확인한다**

Run:

```powershell
python -m unittest tests.test_supabase_regression_runner -v
```

Expected: 2 tests, `OK`

- [ ] **Step 5: 판정 로직을 커밋한다**

```powershell
git add supabase/run_hybrid_regression.py tests/test_supabase_regression_runner.py
git commit -m "test: add Supabase search regression evaluator"
```

### Task 3: 실제 V1·V2 비교 실행기를 완성

**Files:**
- Modify: `supabase/run_hybrid_regression.py`
- Modify: `.env_example`
- Create: `supabase/regression_cases.json`

- [ ] **Step 1: 회귀 사례 파일을 작성한다**

첫 사례는 이번 오류를 반드시 재현한다. 추가 사례는 기존 운영 질문에서 정답이 확인된 질문으로 채우되 모든 항목에 기대 문구를 명시한다.

```json
[
  {
    "name": "내부인원 식사비 분류",
    "question": "법인이 설치학교 교직원 등 내부인원과 간담회, 업무협의 또는 직원 격려 등을 목적으로 식사를 제공하는 경우 복리후생비 또는 업무추진비 중 어떻게 처리해야 하는가?",
    "expected_text": "교직원 내부 인원에 대한 식사비",
    "expected_filename": "특례규칙",
    "match_count": 20
  }
]
```

- [ ] **Step 2: 환경변수 예시를 추가한다**

`.env_example`에는 실제 값을 넣지 않고 다음 이름만 추가한다.

```dotenv
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
```

- [ ] **Step 3: OpenAI 임베딩과 Supabase RPC 호출을 구현한다**

실행기는 기존 프로젝트의 `OpenAIEmbeddings(model="text-embedding-3-small")`을 사용한다. RPC URL은 `{SUPABASE_URL}/rest/v1/rpc/{function_name}`이며 헤더에는 `apikey`와 `Authorization: Bearer ...`를 설정한다. 실제 비밀키나 질문 원문을 로그에 출력하지 않는다.

```python
def call_rpc(base_url, api_key, function_name, embedding, case):
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/rest/v1/rpc/{function_name}",
        data=json.dumps({
            "query_embedding": embedding,
            "match_count": case.get("match_count", 20),
            "filter": case.get("filter", {}),
            "query_text": case["question"],
        }).encode("utf-8"),
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))
```

- [ ] **Step 4: V1·V2 비교표와 종료 코드를 구현한다**

각 사례마다 V1/V2의 통과 여부와 정답 순위를 출력한다. V2가 정답을 놓치거나 V1이 통과한 사례에서 V2가 실패하면 프로세스를 종료 코드 1로 끝낸다. 모든 필수 사례가 통과하면 종료 코드 0으로 끝낸다.

- [ ] **Step 5: 전체 단위 테스트를 실행한다**

Run:

```powershell
python -m unittest discover -s tests -v
python -m py_compile supabase/run_hybrid_regression.py
```

Expected: 모든 테스트 `OK`, 문법 오류 없음

- [ ] **Step 6: 실행기와 사례 파일을 커밋한다**

```powershell
git add .env_example supabase/run_hybrid_regression.py supabase/regression_cases.json tests/test_supabase_regression_runner.py
git commit -m "feat: compare Supabase hybrid search versions"
```

### Task 4: 실제 회귀 테스트와 n8n 전환

**Files:**
- No repository file changes required

- [ ] **Step 1: 새로 발급한 Supabase 키를 현재 셸에만 설정한다**

채팅에 노출된 기존 키는 Supabase에서 교체한다. 새 키는 저장소 파일에 기록하지 않고 PowerShell 세션 환경변수로만 설정한다.

```powershell
$env:SUPABASE_URL='https://your-project.supabase.co'
$env:SUPABASE_ANON_KEY='new-anon-key'
```

- [ ] **Step 2: V1·V2 실제 회귀 테스트를 실행한다**

```powershell
python supabase/run_hybrid_regression.py
```

Expected: 각 사례에 V1/V2 통과 여부와 순위가 표시되고 최종 종료 코드 0

- [ ] **Step 3: 실패 사례를 판정한다**

V2가 실패하면 n8n을 변경하지 않는다. `regression_cases.json`의 기대 문구가 실제 원문과 정확히 일치하는지 먼저 확인하고, 기대값이 맞다면 SQL의 FTS 방식과 후보 수를 조정한 뒤 전체 회귀 테스트를 다시 실행한다.

- [ ] **Step 4: n8n의 함수명만 전환한다**

Supabase Vector Store 노드의 Query Name을 다음처럼 변경한다.

```text
match_univ_documents_hybrid_v2
```

다른 노드, 프롬프트, Cohere `topN=7` 설정은 변경하지 않는다.

- [ ] **Step 5: n8n에서 필수 질문을 최종 확인한다**

내부인원 식사비 질문을 실행한다. 답변이 `내부인원 식사비는 복리후생비`, `외부인원 식사비는 업무추진비`라고 구분하고 내부 간담회 목적을 업무추진비 예외로 추가하지 않아야 통과다.

- [ ] **Step 6: 실패 시 즉시 V1으로 복구한다**

```text
match_univ_documents_hybrid
```

Query Name을 기존 값으로 되돌린 뒤 저장하고 다시 실행한다.
