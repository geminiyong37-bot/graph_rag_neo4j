# 핵심어 인지형 하이브리드 검색 V3 설계

## 목적

질의에서 추출한 `core_keywords`와 `optional_keywords`를 실제 Supabase 검색 조건에 반영한다. 원문 질의의 의미 검색(Vector)은 유지하면서, 전문 검색(FTS)은 엄격·확장 경로로 나누고, 같은 청크는 한 번만 반환한다.

기존 `Draft Answer Generator`는 답변 작성과 검색 결과 해석을 계속 담당한다. 바꾸는 대상은 Draft Agent에 연결된 검색 도구뿐이다.

## 확인된 현재 상태

- `Key Words Extract AI`는 아래 구조를 Draft Agent의 주 입력으로 전달한다.

  ```json
  {
    "question": { "...": "원문 질의" },
    "core_keywords": ["핵심어1", "핵심어2"],
    "optional_keywords": ["보조어1", "보조어2"]
  }
  ```

- 현재 `Supabase as AI Agent`는 Vector Store 도구다. 이 도구가 받는 단일 검색문으로 임베딩을 만들고, 같은 검색문을 기존 V2 함수의 `query_text`로 전달한다.
- V2는 전달된 전체 검색어를 OR FTS로 바꾼다. 따라서 핵심어·보조어의 구분이 SQL까지 전달되지 않으며, 엄격 AND·확장 검색식도 보장하지 못한다.

## 결정

`Draft Answer Generator`는 유지한다. 기존 `Supabase as AI Agent`의 AI 도구 연결만 `Keyword-Aware Search V3` 서브워크플로우 도구로 교체한다.

서브워크플로우 도구는 부모 워크플로우의 이미 계산된 값을 고정 입력으로 받는다. Agent가 키워드 배열이나 원문 질문을 새로 만들지 않는다.

```text
Key Words Extract AI
  └─ question, core_keywords, optional_keywords
       └─ Draft Answer Generator
            └─ Keyword-Aware Search V3 (AI 도구)
                 ├─ question_text만 임베딩 생성
                 ├─ V3 Supabase RPC
                 ├─ Cohere 재정렬
                 └─ 정규화한 chunks 반환
```

## 검색 계약

V3 RPC 함수는 아래 시그니처를 사용한다. V1과 V2는 수정·삭제하지 않는다.

```sql
match_univ_documents_hybrid_v3(
  query_embedding vector(1536),
  match_count int,
  filter jsonb default '{}',
  query_text text default '',
  core_keywords text[] default '{}',
  optional_keywords text[] default '{}'
)
```

- `query_embedding`: `question_text`만 `text-embedding-3-small`으로 임베딩한 값
- `query_text`: 같은 원문 질문. V3의 FTS 조건을 만들 때는 사용하지 않으며 RPC 추적·호환용으로 보존한다.
- `core_keywords`: Key Words Extract AI가 반환한 핵심어 배열
- `optional_keywords`: Key Words Extract AI가 반환한 보조어 배열

## 검색 경로

모든 질의에서 Vector 검색은 원문 질문 임베딩으로 실행한다.

| 유효 핵심어 수 | 엄격 검색 | 확장 검색 |
| --- | --- | --- |
| 0개 | 실행 안 함 | 실행 안 함 |
| 1개 | `C1` | 실행 안 함 |
| 2개 | `C1 AND C2` | `(C1 OR C2) AND (O1 OR O2 ...)` |
| 3개 | `C1 AND C2 AND C3` | `((C1 AND C2) OR (C1 AND C3) OR (C2 AND C3)) AND (O1 OR O2 ...)` |

보조어가 없으면 확장 검색은 실행하지 않는다. 키워드 추출이 비어 있거나 잘못되어도 넓은 OR FTS로 대체하지 않고 Vector 결과만 사용한다.

각 경로는 후보를 `match_count * 3`개까지 반환한다. 경로별 후보는 `UNION ALL` 후 `id`로 묶어 최종에는 청크 하나만 남긴다.

```text
strict   1.00 / (60 + rank)
vector   0.70 / (60 + rank)
expanded 0.40 / (60 + rank)
```

여러 경로에서 발견된 청크는 중복 반환되지 않지만, 여러 검색 방식이 근거로 선택했다는 신호는 점수에 반영한다.

## FTS 안전성

AI가 생성한 문자열을 `to_tsquery()`에 직접 넣지 않는다. 각 배열 원소는 아래 순서로 처리한다.

1. 공백을 제거하고 빈 값은 버린다.
2. `to_tsvector('simple', keyword)`와 `tsvector_to_array()`로 토큰화한다.
3. 두 글자 이상 토큰만 쓰고, 키워드당 최대 4개 토큰·핵심어 최대 3개·보조어 최대 6개로 제한한다.
4. 토큰은 `quote_literal()`로 이스케이프한다.
5. 한 키워드 안의 여러 토큰은 AND로 묶고, 키워드 그룹 사이에는 위 표의 조건을 적용한다.

## n8n 서브워크플로우 계약

부모 도구 입력은 다음 세 개다. 자식 워크플로우의 `When Executed by Another Workflow` 입력 스키마를 만든 뒤, 부모의 `Call n8n Workflow Tool`에서 스키마를 새로 고쳐 생성한다.

```json
{
  "question": {
    "제목": "원문 제목",
    "1.사실관계": "원문 사실관계",
    "2.질의사항": "원문 질의사항",
    "3.관련법령": "원문 관련법령"
  },
  "core_keywords": ["핵심어1", "핵심어2"],
  "optional_keywords": ["보조어1", "보조어2"]
}
```

부모의 각 입력은 `$fromAI()`가 아니라 `Key Words Extract AI` 결과를 참조하는 고정 표현식으로 매핑한다. 따라서 Draft Agent는 검색 도구를 호출할지 결정할 뿐, 원문·키워드를 새로 만들어 전달하지 않는다. 자식 워크플로우 첫 단계는 배열 길이·중복·빈 문자열을 검증하고 `question_text`를 만든다.

도구는 최종적으로 항상 item 하나를 반환한다.

```json
{
  "chunks": [
    {
      "id": 123,
      "content": "근거 청크 본문",
      "metadata": {},
      "similarity": 0.03
    }
  ]
}
```

이 형식은 n8n 버전에 따라 다수 item 반환 방식이 달라도 Draft Agent가 항상 하나의 일관된 도구 응답을 받게 한다.

## Cohere와 답변 생성

기존 Cohere Reranker는 Vector Store의 하위 노드이므로, V3 서브워크플로우 내부로 옮긴다. V3 SQL의 상위 후보를 Cohere에 보내고 상위 7개 청크를 `chunks`로 반환한다.

Draft Agent의 프롬프트는 검색 도구를 한 번 먼저 사용하도록 유지한다. 다만 기존의 “핵심어만 공백으로 연결해 도구에 전달” 지시는 제거한다. 검색 조건과 임베딩 입력은 서브워크플로우가 고정값으로 관리한다.

## 오류 처리와 롤백

- 키워드 배열 파싱 오류: 서브워크플로우는 원문 질문과 빈 배열을 사용해 Vector 전용 검색을 실행한다.
- 임베딩·RPC·Cohere 오류: n8n의 재시도 후 도구 오류를 Draft Agent에 반환한다. Draft Agent는 근거가 없음을 명시하고 사실·조항을 만들지 않는다.
- V3 검색 품질이 기준을 충족하지 않으면 부모에서 도구 연결을 기존 `Supabase as AI Agent`로 되돌린다.
- V1·V2 함수와 기존 n8n 노드는 삭제하지 않는다.

## 검증 기준

- 2개 핵심어·3개 핵심어·보조어 없음·비정상 키워드의 SQL 정적 단위 테스트가 통과한다.
- V3 RPC는 최종 결과에 중복 `id`를 반환하지 않는다.
- Vector 입력은 원문 질문이며 키워드 JSON이 포함되지 않는다.
- 내부인원 식사비 기준 사례에서 특례규칙 해설서의 관련 청크가 상위 후보에 포함된다.
- n8n 실행 결과에서 Draft Agent가 검색 도구를 호출하고, 반환 청크를 근거로만 답변한다.
