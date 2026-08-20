# 임베딩 V4 전체 구축 계획 — Supabase 검색과 Neo4j 지식 그래프

**작성일:** 2026-08-20
**상태:** 구현 전 확정 계획
**대상:** `data_embedding_ready`의 신규 MD 지식자료, Supabase 기존 세법·온라인질의 데이터, Neo4j 신규 그래프
**목표:** 약 1,000자 의미 청크의 검색 재현율과 원자적 Fact 관계 그래프의 설명력을 결합하고, 두 저장소를 공통 ID로 안전하게 통합한다.

---

## 1. 결정 요약

최종 지식 구조는 다음과 같다.

```text
Document → Chunk → Fact → Entity
```

- Parent 청크를 만들지 않는다.
- Parent 임베딩을 만들지 않는다.
- Section 노드와 `section_id`를 만들지 않는다.
- 장·절·조의 계층은 Chunk의 `heading_path` 문자열 메타데이터로만 보존한다.
- Supabase에는 약 1,000자 전후의 의미 Chunk와 Chunk 임베딩을 저장한다. 정확한 경계는 1단계 구현 설계를 따른다.
- Neo4j에는 Supabase Chunk를 가리키는 참조 Chunk, 원자적 Fact, 표준 Entity와 관계를 저장한다.
- Neo4j 벡터 검색 대상은 중복된 1,000자 Chunk가 아니라 `Fact.statement`이다.
- Supabase와 Neo4j는 동일한 `document_id`와 `chunk_id`를 사용한다.
- Neo4j의 Fact는 반드시 `source_chunk_id`와 근거 원문 범위를 가져야 한다.
- 답변 모델은 그래프 관계만으로 답하지 않고, 해당 Fact의 Supabase 원문 Chunk를 함께 받아야 한다.

이 설계에서 두 저장소의 역할은 다음처럼 분리된다.

| 저장소 | 주요 검색 단위 | 역할 |
|---|---|---|
| Supabase | 약 1,000자 Chunk | 의미·핵심어 기반 원문 검색, 법령·온라인질의·지침·해설서 검색 |
| Neo4j | 원자적 Fact와 Entity 관계 | 조건·예외·절차·계정과목·법령 사이의 연결 발견 |

---

## 2. 현재 구현과 목표 구조의 차이

현재 `2_build_graph_from_md.py`는 아래 방식이다.

```text
Parent Chunk → Child Chunk → Entity
                         └→ Entity 간 직접 관계
```

현재 구현의 한계는 다음과 같다.

1. Parent/Child 구조가 남아 있다.
2. Child에서 Fact를 만들지 않고 Entity와 관계를 한 번에 추출한다.
3. Entity 표준화가 없고 LLM이 반환한 문자열을 그대로 `Entity.id`로 사용한다.
4. `업무추진비`, `업무 추진비`, `업무추진 비용`이 서로 다른 Entity가 될 수 있다.
5. Entity 간 직접 관계에 근거 Fact, 조건, 예외, 원문 범위가 없다.
6. 관계가 어느 문장의 어떤 판단에서 만들어졌는지 추적하기 어렵다.
7. Neo4j가 Chunk 임베딩을 다시 보관해 Supabase와 검색 역할이 중복된다.

따라서 기존 적재 프로그램을 부분 수정하지 않고 V4 파이프라인으로 명확히 분리하여 구현한다.

---

## 3. 범위와 비범위

### 3.1 이번 작업 범위

- 준비된 MD 파일의 재검증과 결정적 ID 생성
- 의미 단위 Chunk 생성
- Supabase Document/Chunk 적재
- Chunk별 원자적 Fact 추출
- Entity 추출 및 표준화
- Neo4j Document/Chunk 참조/Fact/Entity 적재
- Fact 벡터 인덱스 및 그래프 인덱스 생성
- 품질 검증과 재실행 가능한 체크포인트
- n8n V3의 Fact 기반 Neo4j 검색 전환
- 장애 시 Supabase 단독 검색으로의 우회

### 3.2 이번 작업의 비범위

- 기존 세법과 온라인질의를 Neo4j Fact 그래프로 변환하지 않는다.
- Parent 청크를 생성하거나 임베딩하지 않는다.
- Section 노드를 만들지 않는다.
- 모든 Entity 후보를 무조건 자동 병합하지 않는다.
- 그래프 경로만으로 법률·회계 판단을 생성하지 않는다.
- 전체 품질 검증 전에 운영 n8n 워크플로우를 활성화하지 않는다.

---

## 4. 입력 데이터 정책

### 4.1 신규 MD 대상

신규 MD 입력 기준 폴더는 `data_embedding_ready`다. 파일 형식, 병합 상태, 보존·제거 항목과 품질 검사는 [1단계 구현 설계](../specs/2026-08-20-embedding-v4-local-foundation-design.md)를 따른다.

### 4.2 기존 Supabase 데이터

- 기존 `세법` 데이터는 그대로 보존한다.
- 기존 `온라인질의` 데이터는 그대로 보존한다.
- 이번 단계에서는 두 유형을 Neo4j에 적재하지 않는다.
- 검색 통합에 필요한 경우 기존 데이터에 `document_type`, `document_id`, `chunk_id`를 점진적으로 보강한다.
- 원본 행의 기존 기본키와 메타데이터는 변경하지 않는다.

### 4.3 백업

파괴적 재적재 전에 다음을 로컬 백업 폴더에 저장한다.

- Supabase 신규 MD 출처 행과 관련 Chunk 행
- Neo4j 전체 노드·관계 수 요약
- Neo4j Document/Chunk/Fact/Entity 식별자 목록
- 임베딩 모델, 프롬프트 버전, 입력 파일 manifest
- 적재 전 품질 보고서

기존 세법과 온라인질의는 삭제 대상에서 제외한다.

백업 기본 위치는 `backups/embedding-v4/{실행시각}`으로 한다. 데이터 덤프와 manifest에는 자격증명이나 API 키를 포함하지 않는다.

### 4.4 기존 데이터 삭제와 초기화 범위

전체 재적재는 다음 순서와 범위를 지킨다.

1. Supabase에서 삭제 예정 행을 먼저 조회하여 파일명, 자료 유형, 행 수를 보고한다.
2. 사용자 승인 후 파일명 기반으로 적재됐던 신규 MD 자료와 그 검색 청크만 삭제한다.
3. `document_type = 세법` 또는 `document_type = 온라인질의`인 기존 데이터는 삭제하지 않는다.
4. 자료 유형이 없거나 분류가 애매한 행은 자동 삭제하지 않고 보류 목록에 넣는다.
5. Neo4j는 백업과 삭제 전 개수 검증 후 기존 노드·관계를 전체 초기화한다.
6. 기존 Parent/Child/Entity 직접 관계 그래프는 V4 적재에 재사용하지 않는다.
7. `data_embedding_ready`의 승인된 신규 MD 전체를 Supabase Chunk와 Neo4j Fact Graph로 처음부터 다시 적재한다.
8. 삭제 전후 행·노드 수와 보존 대상 수를 감사 로그에 남긴다.

삭제 SQL과 Cypher는 미리보기 결과 및 백업 확인 없이는 실행하지 않는다.

---

## 5. 1단계 로컬 기반 요약

**목적:** 외부 저장소를 변경하기 전에 Markdown 정제, 의미 단위 Chunk, Fact·Entity 계약, 결정적 ID, 별칭 표준화와 재실행 기록을 로컬에서 검증한다.

**산출물:** 독립 `embedding_v4` 패키지, 버전 관리되는 별칭 사전, 원본 `content`와 표 변환용 `embedding_text`를 포함한 로컬 JSONL, manifest·검토 보고서와 단위 테스트다.

**완료 조건:** 입력·청킹·ID·Fact·Entity·별칭·검토 대상·manifest 테스트가 통과하고 기존 테스트에 회귀가 없으며 Supabase·Neo4j·n8n에 쓰기가 발생하지 않아야 한다.

세부 계약과 테스트 기준의 단일 기준 문서는 [`임베딩 V4 1단계 구현 설계 — 청킹·Fact·Entity 표준화`](../specs/2026-08-20-embedding-v4-local-foundation-design.md)다. 이 전체 계획과 표현이 다르면 1단계 범위에서는 해당 구현 설계서를 우선한다.

---

## 6. Supabase 데이터 모델

실제 테이블명은 현재 운영 스키마와 충돌 여부를 확인한 뒤 확정하되 논리 구조는 다음과 같다.

### 6.1 Document 테이블

필수 필드:

```text
document_id          text primary key
display_name         text
file_name            text
year                 integer
document_type        text
source_path          text
content_checksum     text
parser_version       text
chunker_version      text
embedding_model      text
embedding_version    text
created_at           timestamptz
updated_at           timestamptz
```

### 6.2 Chunk 테이블

필수 필드:

```text
chunk_id             text primary key
document_id          text
chunk_index          integer
content              text
embedding            vector(1536)
heading_path         text
previous_chunk_id    text null
next_chunk_id        text null
content_checksum     text
```

문서 공통값인 `document_type`, `file_name`, `year`와 처리·임베딩 버전은 Chunk마다 반복하지 않고 `document_id`로 Document 테이블에서 조회한다. Chunk에는 원문 검색, 연결, 무결성 확인에 필요한 값만 둔다.

임베딩 입력 생성과 로컬 비교 산출물의 상세 규칙은 [1단계 구현 설계](../specs/2026-08-20-embedding-v4-local-foundation-design.md)를 따른다. Supabase에 변환 텍스트를 별도 컬럼으로 저장할지는 1단계 표본 검증 후 결정한다.

### 6.3 검색 인덱스

- Chunk embedding cosine/vector index
- 한국어 FTS에 사용하는 `fts` 컬럼과 GIN 인덱스
- Chunk의 `document_id` 인덱스와 Document의 `document_type`, `year` 필터 인덱스
- 기존 V3 하이브리드 RPC가 새 Chunk 테이블을 대상으로 동작하도록 별도 버전으로 작성

### 6.4 기존 데이터 호환

기존 세법·온라인질의는 즉시 물리 테이블을 이동하지 않아도 된다. 통합 검색 RPC 또는 호환 View에서 신규 Chunk와 동일한 반환 형식으로 정규화한다.

공통 반환 필드:

```json
{
  "id": "chunk_id 또는 기존 행 ID",
  "document_id": "...",
  "content": "...",
  "document_type": "세법|온라인질의|법령|회계지침|해설서|사례집",
  "file_name": "...",
  "year": 2023,
  "score": 0.0,
  "metadata": {}
}
```

---

## 7. Neo4j 데이터 모델

### 7.1 노드

#### Document

```text
document_id
display_name
file_name
year
document_type
content_checksum
```

#### Chunk

Neo4j Chunk는 원문과 Chunk 임베딩을 중복 저장하는 검색 노드가 아니라 Supabase 원문을 찾기 위한 참조 노드다.

```text
chunk_id
document_id
heading_path
chunk_index
content_checksum
```

#### Fact

```text
fact_id
statement
predicate
conditions_json
exceptions_json
modality
result
field_assessments_json
source_span
source_chunk_id
embedding
embedding_model
extractor_version
```

#### Entity

```text
entity_id
canonical_name
entity_type
aliases
normalization_version
```

### 7.2 기본 관계

```text
(Document)-[:HAS_CHUNK]->(Chunk)
(Chunk)-[:SUPPORTS]->(Fact)
(Fact)-[:SUBJECT]->(Entity)
(Fact)-[:OBJECT]->(Entity)
(Fact)-[:CONDITION]->(Entity)
(Fact)-[:EXCEPTION]->(Entity)
(Fact)-[:LEGAL_BASIS]->(Entity)
```

`CONDITION`과 `EXCEPTION` 관계는 반복 검색 가치가 확인되어 승인된 Entity로 승격된 경우에만 만든다. 일반적인 조건과 예외는 Fact의 속성으로 유지한다.

### 7.3 탐색 가속용 Entity 직접 관계

필요하면 Fact의 핵심 관계를 Entity 사이에 물질화한다.

```text
(Entity)-[:CLASSIFIED_AS]->(Entity)
```

직접 관계에는 반드시 다음 속성을 둔다.

```text
fact_id
source_chunk_id
predicate_version
```

직접 관계는 원본이 아니다. 원본은 Fact와 `source_span`이며, 직접 관계는 탐색 속도를 위한 파생 데이터다.

### 7.4 제약조건과 인덱스

```text
Document.document_id unique
Chunk.chunk_id unique
Fact.fact_id unique
Entity.entity_id unique
Fact.statement vector index, 1536 dimensions, cosine
Entity.canonical_name lookup index
Fact.predicate lookup index
```

Neo4j Chunk에는 `chunk_vector_index`를 새로 만들지 않는다. 기존 그래프를 초기화한 뒤 Fact 벡터 인덱스로 대체한다.

---

## 8. 임베딩 모델과 버전 관리

초기 모델은 현재 검색 호환성을 위해 `text-embedding-3-small`, 1536차원을 사용한다.

저장해야 할 버전 정보:

```text
embedding_model
embedding_dimensions
embedding_version
parser_version
chunker_version
fact_prompt_version
entity_normalizer_version
schema_version
```

Chunk와 Fact는 같은 모델을 사용할 수 있지만 서로 다른 벡터 공간의 검색 단위다.

- Supabase: Chunk 내용 임베딩
- Neo4j: Fact statement 임베딩

모델이나 프롬프트 버전이 바뀌면 기존 행을 덮어쓰기 전에 변경 범위와 재처리 대상을 manifest에 기록한다.

---

## 9. 재실행 가능한 처리 파이프라인

권장 실행 단계:

```text
01_validate_inputs
02_build_documents
03_build_chunks
04_embed_supabase_chunks
05_extract_facts
06_normalize_entities
07_embed_facts
08_load_neo4j
09_verify_cross_store_ids
10_generate_quality_report
```

각 단계는 입력과 출력을 JSONL manifest로 남긴다.

### 9.1 체크포인트

파일별로 다음 상태를 기록한다.

```json
{
  "document_id": "...",
  "input_checksum": "...",
  "chunks_created": 42,
  "supabase_loaded": true,
  "facts_extracted": 118,
  "entities_normalized": true,
  "neo4j_loaded": true,
  "verified": true,
  "pipeline_version": "v4"
}
```

실패한 단계부터 재개할 수 있어야 하며 완료된 문서를 중복 적재하지 않아야 한다.

### 9.2 Upsert 정책

- 동일 ID·동일 체크섬: 건너뜀
- 동일 ID·변경 체크섬: 해당 Document의 신규 MD Chunk와 Fact만 교체
- 삭제된 파일: 자동 삭제하지 않고 manifest에서 `missing`으로 표시 후 승인받아 처리
- Entity: 다른 문서가 참조할 수 있으므로 문서 재처리 시 무조건 삭제하지 않음
- 고아 Entity: 전체 적재 완료 후 별도 검증을 거쳐 정리

---

## 10. 검증 단계

1단계의 표본 구성과 로컬 Chunk·Fact·Entity 검증 기준은 [1단계 구현 설계](../specs/2026-08-20-embedding-v4-local-foundation-design.md)를 따른다. 외부 저장소 적재가 시작된 뒤에는 다음 동기화 검증을 추가한다.

### 10.1 저장소 동기화 검증

- Neo4j의 모든 Chunk 참조가 Supabase Chunk에 존재하는가
- 모든 Fact가 유효한 `source_chunk_id`를 가지는가
- Supabase 신규 MD Chunk 중 Neo4j 참조가 없는 항목을 보고하는가
- 동일 `chunk_id`의 `content_checksum`이 두 저장소에서 일치하는가

---

## 11. 품질 기준과 중단 조건

1단계 로컬 품질 기준은 [1단계 구현 설계](../specs/2026-08-20-embedding-v4-local-foundation-design.md)를 따른다. 외부 저장소 적재 이후에는 다음 기준을 추가로 충족해야 한다.

- 공통 ID 참조 무결성: 100%
- Fact `source_span` 원문 포함률: 100%
- Fact의 유효한 `source_chunk_id` 비율: 100%
- LLM 추출 표본 Fact의 원문 충실도: 95% 이상
- 적재 표본 Entity의 정확 일치·별칭 자동 연결 정확도: 95% 이상
- 기존 세법·온라인질의 행 손실: 0건

다음 중 하나라도 발생하면 전체 적재를 중단한다.

- 공통 ID 충돌
- 기존 보존 데이터 삭제 또는 변경
- Fact 출처 누락
- Entity 대량 오병합
- 표 구조의 광범위한 손실
- 임베딩 차원 또는 모델 불일치

---

## 12. n8n V3 수정 계획

현재 V3는 Supabase Chunk 검색 뒤 기존 Neo4j Child Chunk 벡터 인덱스와 `MENTIONS` 관계를 조회한다. V4 적재 후에는 이 부분을 Fact 기반으로 교체한다.

현재 저장된 V3의 Neo4j 검색 구간은 기존 스키마 확인을 위한 임시 구현이다. V4 운영본으로 간주하지 않으며, 신규 임베딩 완료 전에는 publish하거나 schedule을 활성화하지 않는다. V4 전환 시 기존 `chunk_vector_index`, `HAS_PARENT`, `MENTIONS` 기반 쿼리를 제거하고 `fact_vector_index`와 Fact 출처 복원 흐름으로 대체한다.

### 12.1 최종 실행 흐름

```text
질문 원문 정규화
→ 핵심어·보조어 추출
→ 질문 임베딩 1회 생성
→ Supabase 하이브리드 검색 30개
→ Cohere 1차 리랭킹 15개
→ Neo4j Fact 벡터 검색
→ Fact의 Entity 관계 확장
→ Neo4j Fact 후보 최대 8개
→ source_chunk_id로 Supabase 원문 Chunk 조회
→ 동일 chunk_id 병합
→ 통합 후보 최대 25개
→ Cohere 최종 리랭킹 최대 10개
→ 근거 기반 답변 생성
→ 기존 최종 정리와 Google Sheets 저장
```

모든 질문이 동일한 검색 흐름을 사용한다. 단순 질문과 복잡 질문을 사전에 분기하지 않는다.

### 12.2 Supabase 1차 검색

- `match_count = 30`
- 질문 원문, core keywords, optional keywords를 V4 RPC에 전달
- 법령·온라인질의·신규 MD 자료를 공통 형식으로 반환
- Cohere 1차 리랭킹에서 최대 15개 선택

자료 유형 쏠림을 방지하기 위해 관련 후보가 존재할 경우 다음을 보정한다.

- 전체 점수 상위 후보 유지
- 법령 후보 최소 확보
- 온라인질의 후보 최소 확보
- 지침·해설서·사례집 후보 최소 확보

무조건적인 고정 할당으로 품질을 떨어뜨리지 않도록 `전체 상위 + 유형별 최고점 보충` 방식으로 구현한다.

### 12.3 Neo4j Fact 검색

1. 질문 임베딩으로 `fact_vector_index` 조회
2. 직접 유사 Fact 확보
3. Fact의 SUBJECT/OBJECT 및 승인된 CONDITION/EXCEPTION Entity 탐색
4. 같은 Entity에 연결된 다른 Fact 확장
5. 각 후보가 원문 Chunk와 연결되는지 검증
6. 후보별 관계 근거 최대 2개
7. 전체 Neo4j 후보 최대 8개

Neo4j 후보 반환 예시:

```json
{
  "fact_id": "...",
  "statement": "...",
  "predicate": "CLASSIFIED_AS",
  "conditions": ["..."],
  "exceptions": [],
  "source_chunk_id": "...",
  "graph_score": 0.87,
  "relationship_evidence": [
    {
      "from": "사은품 지급액",
      "relation": "CLASSIFIED_AS",
      "to": "판매부대비용"
    }
  ]
}
```

### 12.4 원문 복원

Neo4j Fact 문자열만 답변 모델에 전달하지 않는다.

- Neo4j 결과의 `source_chunk_id` 목록을 수집
- Supabase에서 해당 원문 Chunk를 일괄 조회
- Fact, 조건, 예외와 원문 Chunk를 하나의 후보 객체로 구성
- 원문 조회에 실패한 Fact는 답변 근거에서 제외하고 로그에 기록

### 12.5 후보 통합

- Supabase 후보와 Neo4j 원문 후보를 `chunk_id`로 병합
- 같은 Chunk이면 중복으로 세지 않음
- 기존 Supabase 후보에 Neo4j Fact·조건·예외·관계 근거를 추가
- Neo4j에서만 발견된 Chunk는 새 후보로 추가
- 통합 후보는 최대 25개

### 12.6 최종 Cohere 리랭킹

리랭킹 문서에는 다음 요소를 명시한다.

```text
자료 유형
파일명·연도
heading_path
원문 Chunk
관련 Fact
조건
예외
관계 근거
```

최종 출력은 최대 10개로 제한한다. 리랭킹 점수와 1차 점수, graph score는 모두 로그에 남긴다.

### 12.7 답변 생성 규칙

- 원문 Chunk에 없는 내용을 만들지 않는다.
- 그래프 관계는 관련 근거를 찾는 데 사용하며 단독 법적 근거로 사용하지 않는다.
- Fact는 반드시 연결된 원문과 함께 사용할 때만 근거로 인정한다.
- 조건과 예외를 답변에서 누락하지 않는다.
- 관련 법령은 명칭·조항·제목이 원문에서 확인될 때만 쓴다.
- 온라인질의는 유사 사례 섹션에서 사용한다.
- 날짜가 완전하지 않은 온라인질의는 유사 사례에서 제외한다.
- 자료가 충돌하면 충돌 사실과 각 출처를 표시한다.

### 12.8 장애 우회

- Supabase 검색 실패: 답변 생성 중단
- Neo4j 실패 또는 0건: Supabase 1차 후보만으로 최종 리랭킹 계속
- Neo4j 원문 복원 일부 실패: 실패 Fact 제외 후 계속
- Cohere 1차 실패: Supabase RPC 순위로 제한 실행하거나 명시적 오류 처리
- Cohere 최종 실패: 1차 점수와 graph score를 결합한 결정적 대체 순위 사용
- OpenAI 답변 생성 실패: 재시도 후 저장 단계 중단

Neo4j 장애 때문에 전체 상담 수집과 Supabase 기반 답변이 중단되지 않도록 한다.

---

## 13. n8n 관찰 가능성

각 질의 실행마다 다음 요약을 남긴다.

```json
{
  "supabase_candidates": 30,
  "supabase_first_rerank": 15,
  "neo4j_direct_facts": 4,
  "neo4j_expanded_facts": 4,
  "neo4j_restored_chunks": 7,
  "deduplicated_chunks": 3,
  "combined_candidates": 19,
  "final_evidence": 10,
  "fallback_used": false,
  "retrieval_version": "v4"
}
```

최소 로그 항목:

- 질문 ID 또는 질의 날짜·제목
- 검색 키워드
- 각 단계 후보 수
- 각 후보의 자료 유형과 ID
- 제외 사유
- 재시도와 fallback 여부
- 처리 시간
- 모델과 프롬프트 버전

---

## 14. 테스트 계획

### 14.1 단위 테스트

1단계 로컬 단위 테스트의 전체 목록과 완료 기준은 [1단계 구현 설계](../specs/2026-08-20-embedding-v4-local-foundation-design.md)를 따른다.

### 14.2 저장소 통합 테스트

- Supabase Document/Chunk upsert
- Neo4j Document/Chunk 참조/Fact/Entity upsert
- Fact vector index 검색
- Entity 관계 확장
- `source_chunk_id`를 통한 Supabase 원문 복원
- 두 저장소의 checksum 비교

### 14.3 n8n 격리 테스트

실제 상담 수집·Google Sheets 저장과 분리하여 검색 구간만 테스트한다.

테스트 입력 유형:

- 계정과목 분류 질문
- 법령 적용 질문
- 조건과 예외가 포함된 질문
- 절차와 승인 주체 질문
- 관련 온라인질의가 있는 질문
- Neo4j 결과가 없는 질문
- Neo4j 장애를 모의한 질문

검증 결과:

- Supabase 30→15 적용
- Neo4j 최대 8 적용
- 관계 근거 후보당 최대 2 적용
- 통합 최대 25 적용
- 동일 `chunk_id` 중복 제거
- 최종 최대 10 적용
- fallback 정상 동작

### 14.4 회귀 테스트

현재 Supabase V3 회귀 질문 세트에 다음 기대값을 추가한다.

- 반드시 포함돼야 할 자료 유형
- 기대 Entity 또는 Fact
- 금지되는 잘못된 Entity 병합
- 기대 법령
- 기대 유사 사례
- 근거 부족 시 단정 금지

---

## 15. 구현 순서와 승인 지점

### 단계 1: 로컬 기반 구현

목적, 산출물, 완료 조건은 이 문서의 5절 요약과 [1단계 구현 설계](../specs/2026-08-20-embedding-v4-local-foundation-design.md)를 따른다. 해당 설계의 로컬 테스트와 사용자 검토를 통과하기 전에는 외부 저장소 적재로 넘어가지 않는다.

### 단계 2: Supabase 표본 적재

- Document/Chunk 스키마 적용
- 표본 Chunk 임베딩
- V4 검색 RPC 초안
- 기존 세법·온라인질의 보존 확인

**승인 조건:** 검색과 보존 데이터 무결성 통과

### 단계 3: Fact와 Entity 파이프라인 구현

- 구조화 Fact 추출
- source span 검증
- Entity 표준화와 검토 큐
- Fact 임베딩

**승인 조건:** Fact 충실도와 Entity 정확도 기준 통과

### 단계 4: Neo4j 표본 적재

- V4 제약조건과 인덱스
- Document/Chunk 참조/Fact/Entity 적재
- 직접 관계 materialization
- 원문 복원 검증

**승인 조건:** 공통 ID 참조 무결성 100%

### 단계 5: 전체 적재

- 기존 신규 MD 데이터 백업
- Supabase 파일명 기반 신규 MD 행·청크 삭제(세법·온라인질의 제외)
- Neo4j 기존 그래프 전체 초기화
- 전체 Supabase Chunk 적재
- 전체 Neo4j Fact 그래프 적재
- 품질 보고서 생성

**승인 조건:** 품질 기준 전체 통과

### 단계 6: n8n V3 전환

- 기존 Child vector 쿼리 제거
- Fact vector 및 관계 확장 쿼리 추가
- Supabase 원문 복원 추가
- 자료 유형 보정과 fallback 추가
- 검색 격리 테스트

**승인 조건:** 격리·회귀 테스트 통과

### 단계 7: 운영 전환

- V3 수동 전체 실행
- 생성 답변과 Google Sheets 입력 직전 결과 검토
- 기존 워크플로우와 비교
- 승인 후 publish 및 schedule 활성화

---

## 16. 구현 완료 정의

다음 조건이 모두 충족돼야 임베딩과 n8n V4 전환이 완료된 것으로 본다.

- Parent Chunk와 Section 노드 없이 신규 데이터가 적재됨
- Supabase에 모든 신규 MD Chunk 임베딩이 존재함
- Neo4j에 모든 승인 Fact와 표준 Entity가 존재함
- 모든 Neo4j Fact가 유효한 Supabase `source_chunk_id`를 가짐
- Entity alias와 보류 큐가 작동함
- Fact 벡터 검색과 관계 확장이 작동함
- n8n이 Supabase 30→15, Neo4j 최대 8, 통합 최대 25, 최종 최대 10을 지킴
- 동일 Chunk 중복이 제거됨
- Neo4j 장애 시 Supabase 단독 흐름이 작동함
- 기존 세법과 온라인질의가 보존됨
- 테스트와 품질 보고서가 저장됨
- 운영 워크플로우는 사용자 승인 후에만 활성화됨

---

## 17. 바로 다음 작업

[1단계 구현 설계](../specs/2026-08-20-embedding-v4-local-foundation-design.md)에 따라 로컬 기반을 구현하고 검증한다. 완료 조건과 사용자 검토를 통과한 뒤에만 Supabase 표본 적재를 진행한다. 기존 `2_build_graph_from_md.py`로 전체 임베딩을 먼저 실행하지 않는다.
