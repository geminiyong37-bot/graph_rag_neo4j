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
- Supabase에는 약 700~1,200자의 의미 Chunk와 Chunk 임베딩을 저장한다.
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

입력 기준 폴더는 `data_embedding_ready`다. 이 폴더에는 다음 조건을 만족한 모든 임베딩 대상 파일이 있어야 한다.

- UTF-8 Markdown
- 같은 문서의 `(1)`, `(2)` 분할 파일 병합 완료
- 페이지 번호와 반복 머리말·꼬리말 제거
- 깨진 특수문자와 불필요한 줄바꿈 정리
- 표의 헤더와 행 관계 보존
- 원래 파일명과 연도 보존

현재 `merge_embedding_parts.py`와 `prepare_embedding_md.py`의 결과물을 입력으로 사용하되, V4 적재 전에 품질 검사를 다시 실행한다.

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

## 5. 공통 식별자 설계

### 5.1 원칙

- ID는 재실행해도 동일해야 한다.
- Supabase와 Neo4j가 같은 ID를 사용해야 한다.
- 파일 경로의 일시적인 위치나 데이터베이스 자동 증가값에 의존하지 않는다.
- 사용자에게는 파일명과 연도를 표시하되, 내부 충돌 검증을 위해 체크섬을 보관한다.

### 5.2 Document ID

기본 재료:

```text
연도 + 정규화된 논리 파일명
```

예시:

```text
2023_지방계약길라잡이_공사계약
```

동일 연도·동일 파일명이 충돌할 때만 짧은 원문 체크섬을 접미사로 사용한다.

```text
2023_지방계약길라잡이_공사계약_a13f62c1
```

체크섬은 파일의 동일성·변경 여부를 확인하는 내부 정보다. 사용자에게 표시되는 문서명에는 포함하지 않는다.

### 5.3 Chunk ID

```text
{document_id}_ch_{순번 4자리}
```

예시:

```text
2023_지방계약길라잡이_공사계약_ch_0032
```

Chunk 순번은 최종 정제 텍스트를 위에서 아래로 처리한 결정적 순서다.

### 5.4 Fact ID

```text
{chunk_id}_f_{순번 2자리}
```

예시:

```text
2023_지방계약길라잡이_공사계약_ch_0032_f_01
```

동일 Chunk를 동일 프롬프트 버전으로 재처리했을 때 Fact 정렬이 안정적이어야 한다. Fact는 원문 등장 순서로 정렬한다.

### 5.5 Entity ID

Entity ID는 표준 명칭과 유형을 기반으로 생성한다.

```text
{entity_type}:{canonical_name 정규형}
```

예시:

```text
Account:판매부대비용
Regulation:지방계약법_시행령
Condition:사전약정
```

표시 이름은 `canonical_name`에 원형으로 따로 저장한다.

---

## 6. Markdown 정제와 구조 인식

### 6.1 보존해야 할 정보

- 문서 제목
- 장·절·조·항·호 제목
- 질의와 답변의 구분
- 표 제목, 열 헤더, 행 값
- 법령명, 조문 번호, 조문 제목
- 사례 일자와 사건·질의 제목
- 각주와 단서 문구

### 6.2 제거 또는 정규화할 정보

- 반복 페이지 번호
- 반복 머리말과 꼬리말
- OCR로 분리된 단어 내부 줄바꿈
- 의미 없는 연속 공백
- 깨진 글머리 기호
- 목차에만 존재하는 페이지 번호 열

### 6.3 Heading Path

Section 노드를 만들지 않고 현재 위치만 문자열로 기록한다.

```text
제3장 공사의 발주 > 제2절 추정가격의 작성 > 1. 추정가격의 개념
```

각 Chunk의 메타데이터에 다음처럼 저장한다.

```json
{
  "heading_path": "제3장 공사의 발주 > 제2절 추정가격의 작성",
  "heading_level": 2
}
```

`heading_path`는 검색 결과 설명과 문맥 복원에 사용하며 별도의 Section ID나 Parent 본문을 만들지 않는다.

---

## 7. Chunk 생성 규칙

### 7.1 목표 크기

- 일반 목표: 700~1,200자
- 짧은 완결 조항·답변: 300자 이상이면 독립 Chunk 허용
- 긴 표·완결 조항: 의미 보존을 위해 최대 약 1,500자 허용
- 300자 미만 단편은 같은 `heading_path`의 다음 내용과 병합
- 단순 고정 50자 중첩은 사용하지 않는다.

크기는 기계적 절단 기준이 아니라 보조 기준이다. 문장·조항·질의응답·표의 완결성이 우선한다.

### 7.2 분리 우선순위

1. 장·절·조 경계
2. 질의/답변 세트 경계
3. 문단 경계
4. 완결된 문장 경계
5. 표 전체 또는 표의 논리적 행 묶음

### 7.3 금지되는 분리

- 문장 중간 절단
- 법령의 본문과 단서 분리
- 표 헤더와 데이터 행 분리
- 질문과 그에 대한 직접 답변 분리
- 열거 조건과 그 결과의 분리

### 7.4 인접 문맥

Parent 대신 각 Chunk에 다음 값을 저장한다.

```json
{
  "previous_chunk_id": "..._ch_0031",
  "next_chunk_id": "..._ch_0033"
}
```

답변 생성에 항상 인접 Chunk를 넣지 않는다. 선택된 Chunk가 문장 시작·끝의 불완전성, 표 연결, 대명사 참조 등 문맥 부족 신호를 가질 때만 추가 조회한다.

---

## 8. Supabase 데이터 모델

실제 테이블명은 현재 운영 스키마와 충돌 여부를 확인한 뒤 확정하되 논리 구조는 다음과 같다.

### 8.1 Document 테이블

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

### 8.2 Chunk 테이블

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

`content`에는 출처 표시와 Fact 근거 검증을 위한 원본 Markdown을 보존한다. 임베딩 모델에는 별도로 만든 `embedding_text`를 전달한다. 일반 본문은 원문과 같게 두고, 표는 표 제목·열 헤더·행 값의 관계가 드러나는 문장형 텍스트로 변환한다. 본문이 제목에 의존하는 경우에만 `heading_path`를 함께 사용하며, ID·파일명·연도·체크섬·버전 정보는 임베딩하지 않는다.

1단계 로컬 검증에서는 문서별 JSONL 파일에 `content`와 `embedding_text`를 함께 저장하여 원본과 변환 결과를 비교한다. `content_checksum`은 원본 `content` 기준이다. Supabase에 `embedding_text`를 별도 컬럼으로 저장할지는 표본 검증 후 결정하며, 그 전에는 Chunk 테이블의 필수 필드로 확정하지 않는다.

### 8.3 검색 인덱스

- Chunk embedding cosine/vector index
- 한국어 FTS에 사용하는 `fts` 컬럼과 GIN 인덱스
- Chunk의 `document_id` 인덱스와 Document의 `document_type`, `year` 필터 인덱스
- 기존 V3 하이브리드 RPC가 새 Chunk 테이블을 대상으로 동작하도록 별도 버전으로 작성

### 8.4 기존 데이터 호환

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

## 9. Fact 추출 규칙

### 9.1 Fact의 정의

Fact는 원문에 근거한 하나의 독립적인 판단, 규칙, 의무, 허용, 금지, 분류 또는 예외다.

Fact는 단순 핵심어 묶음이나 요약문이 아니다. 다음 질문에 답할 수 있어야 한다.

```text
무엇이(subject), 어떤 관계나 판단(predicate)으로,
무엇(object/result)에 연결되며,
어떤 조건(condition)과 예외(exception) 아래 적용되는가?
```

이전 논의에서 사용한 “Neo4j를 위한 소단위 청크”는 이 Fact를 뜻한다. 다만 Fact는 300자나 400자처럼 길이를 고정해 다시 자른 텍스트 청크가 아니다. 약 1,000자 Chunk 안에서 독립된 판단을 원문 순서대로 추출한 구조화 단위이며, 하나의 Chunk에서 0개 이상의 Fact가 만들어질 수 있다.

### 9.2 원자성 기준

- Fact 하나에는 핵심 predicate 하나만 둔다.
- 한 문장에 독립 판단이 여러 개면 여러 Fact로 나눈다.
- 관계 수를 기준으로 기계적으로 2개 또는 3개로 나누지 않는다.
- 조건, 단서, 예외는 적용 대상 Fact에서 분리하지 않는다.
- 동일 결론을 뒷받침하는 열거 조건은 배열로 보존할 수 있다.

예시 원문:

```text
사전약정에 따라 거래실적을 기준으로 지급하는 사은품은 판매부대비용에 해당하며 손금에 산입할 수 있다.
```

권장 Fact:

```text
Fact 1: 사은품 지급액 → 판매부대비용에 해당
조건: 사전약정, 거래실적 기준

Fact 2: 판매부대비용 → 손금에 산입 가능
조건: Fact 1의 조건을 승계
```

### 9.3 Fact 스키마

항상 저장하는 핵심 필드는 `fact_id`, `statement`, `predicate`, `source_chunk_id`, `source_span`, `extractor_version`이다. `subject`, `object`, `conditions`, `exceptions`, `modality`, `result`, `legal_basis`, `effective_date`는 원문에 명확히 있거나 해당 Fact에 필요한 경우에만 저장한다. AI가 스스로 출력하는 `confidence`는 Fact 필드로 사용하지 않는다.

조건부 필드를 작성하지 않았다면 `field_assessments`에 `NOT_STATED`, `NOT_APPLICABLE`, `REVIEW_REQUIRED` 중 하나를 기록한다. 값과 상태가 모두 없으면 실제 누락으로 거부한다. `REVIEW_REQUIRED`가 있거나 subject가 없는 Fact는 검토 후보로 보존하되 Entity 관계를 자동 생성하지 않는다.

```json
{
  "fact_id": "..._f_01",
  "statement": "사전약정에 따른 사은품 지급액은 판매부대비용에 해당한다.",
  "subject": {
    "surface": "사은품 지급액",
    "type": "ExpenseItem"
  },
  "predicate": "CLASSIFIED_AS",
  "object": {
    "surface": "판매부대비용",
    "type": "Account"
  },
  "conditions": ["사전약정", "거래실적 기준"],
  "modality": "PERMITTED",
  "result": "손금 산입 가능",
  "legal_basis": ["법인세법 제52조"],
  "source_chunk_id": "..._ch_0032",
  "source_span": "원문에서 해당 판단을 직접 뒷받침하는 문장",
  "extractor_version": "fact-v1",
  "field_assessments": {
    "exceptions": "NOT_STATED",
    "effective_date": "NOT_STATED"
  }
}
```

### 9.4 허용 Predicate

초기에는 표본 테스트용 버전 1 통제 목록을 사용한다. 이 목록은 최종 고정 목록이 아니다.

```text
CLASSIFIED_AS
APPLIES_TO
GOVERNS
REQUIRES
PERMITS
PROHIBITS
INCLUDES
EXCLUDES
EXCEPTS
RESULTS_IN
DEDUCTIBLE_AS
REQUIRES_APPROVAL
REQUIRES_REPORTING
RELATED_TO
```

LLM이 새로운 관계명을 임의로 생성하지 않도록 한다. 목록에 없는 관계는 `RELATED_TO`로 곧바로 낮추지 않고 원문, 제안 관계명, 기존 목록으로 표현할 수 없는 이유, 발생 횟수를 `predicate_candidates.jsonl`에 저장한다.

표본 Fact 검토에서는 정의, 계산 방식, 자격·대상 여부, 제출·신고, 기한·시행 관계가 기존 목록으로 충분한지 확인한다. 반복성과 검색 가치가 확인되고 사용자가 승인한 관계만 추가하며 `predicate_version`을 올린다. 단순 동의어는 새 관계로 만들지 않고 기존 predicate에 통합한다.

### 9.5 Fact 품질 거부 조건

다음 Fact는 적재하지 않는다.

- `source_span`이 원문에 존재하지 않음
- 핵심 판단이 없음
- 조건부 필드의 값과 `field_assessments` 상태가 모두 없음
- 원문에 없는 법령·조항을 생성함
- 조건이나 예외를 제거해 의미가 반대로 바뀜
- 단순 주제어 나열
- 동일 Chunk 안에서 완전히 중복됨

Subject가 없거나 `REVIEW_REQUIRED`가 있는 Fact는 삭제하지 않고 검토 목록에 두며, 검토 완료 전 Entity 관계를 만들지 않는다.

여기서 subject가 없다는 것은 판단은 있으나 표의 병합 셀, 앞 문장 참조, 생략 표현 때문에 주체를 원문 구간만으로 확정할 수 없다는 뜻이다. AI가 주체를 추측하지 않도록 후보와 원문 근거만 보존한다.

검토자는 프로젝트 사용자 또는 사용자가 지정한 운영자다. 표본 Fact 추출 후 Neo4j 표본 적재 전에 시스템이 `review_required.jsonl`과 요약 보고서를 생성한다. 검토자는 앞뒤 Chunk를 확인해 subject를 확정하거나 Fact를 제외하고, 검토자·시각·결정 사유를 남긴다. 미해결 Fact는 원문 검색용 후보로만 보존하고 Entity 관계와 전체 Neo4j 적재에서 제외한다. Predicate 후보도 이 단계에서 승인·기각·보류로 분류한다.

---

## 10. Entity 추출과 표준화

### 10.1 실행 시점

```text
Chunk 생성 → Fact 추출 → Entity 후보 추출 → Entity 표준화 → Neo4j 저장
```

Entity 표준화는 LLM 추출 전에 임의로 수행하지 않고 Fact가 확정된 뒤 수행한다. 그래야 문맥에 따른 Entity 유형을 함께 판단할 수 있다.

### 10.2 Entity 유형

초기 유형은 다음과 같다.

```text
Organization
Regulation
Article
Account
Procedure
Concept
PersonRole
System
ExpenseItem
Document
```

이 목록은 표본 테스트용 버전 1이며 최종 고정 목록이 아니다. 기존 유형으로 정확히 표현하기 어려운 Entity는 `Concept`에 억지로 넣거나 새 유형을 즉시 생성하지 않는다. 원문 표현, 제안 유형, 기존 유형으로 표현할 수 없는 이유, 발생 횟수를 `entity_type_candidates.jsonl`에 저장한다.

표본 Entity 검토에서 반복성과 별도 검색 가치가 확인되고 사용자가 승인한 유형만 추가하며 `entity_type_version`을 올린다. 단순 명칭 차이는 새 유형으로 만들지 않고 기존 유형의 별칭으로 통합한다.

### 10.3 표준화 단계

1. Unicode NFC 정규화
2. 양끝 공백 및 중복 공백 제거
3. 비교용 문자열에서 허용된 범위의 띄어쓰기·기호 정규화
4. 법령명·기관명·계정과목 표준용어 사전 조회
5. 별칭 사전 조회
6. 동일 유형의 기존 `canonical_name` 정확 일치 조회
7. 동일 유형의 기존 alias 조회
8. 후보 유사도 조회
9. 높은 확신일 때만 기존 Entity에 연결
10. 애매하면 신규 Entity로 자동 병합하지 않고 검토 큐에 저장

### 10.4 별칭 사전

다음과 같은 법령 약칭은 초기 사전에 포함한다.

```text
지방자치단체를 당사자로 하는 계약에 관한 법률 시행령
  → 지방계약법 시행령

중소기업 구매촉진 및 판로지원에 관한 법률
  → 판로지원법

조달사업에 관한 법률
  → 조달사업법
```

사전은 코드에 하드코딩하지 않고 버전 관리되는 JSON 또는 YAML로 관리한다.

### 10.5 자동 병합 기준

- 정확 일치 또는 검증된 alias: 자동 연결
- 정규화 후 정확 일치이며 유형도 동일: 자동 연결
- 의미 유사도만 높음: 자동 병합 금지
- 유형이 다름: 같은 표면어라도 별도 Entity 유지
- 법령 조항: 법령명과 조문 번호가 모두 확인될 때만 Article Entity 생성

### 10.6 검토 큐

```json
{
  "surface": "업무 추진 비용",
  "suggested_entity_id": "Account:업무추진비",
  "similarity": 0.91,
  "source_chunk_id": "...",
  "status": "pending"
}
```

검토 결과는 별칭 사전에 누적하여 다음 재처리의 정확도를 높인다.

---

## 11. Neo4j 데이터 모델

### 11.1 노드

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

### 11.2 기본 관계

```text
(Document)-[:HAS_CHUNK]->(Chunk)
(Chunk)-[:SUPPORTS]->(Fact)
(Fact)-[:SUBJECT]->(Entity)
(Fact)-[:OBJECT]->(Entity)
(Fact)-[:CONDITION]->(Entity)
(Fact)-[:EXCEPTION]->(Entity)
(Fact)-[:LEGAL_BASIS]->(Entity)
```

### 11.3 탐색 가속용 Entity 직접 관계

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

### 11.4 제약조건과 인덱스

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

## 12. 임베딩 모델과 버전 관리

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

## 13. 재실행 가능한 처리 파이프라인

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

### 13.1 체크포인트

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

### 13.2 Upsert 정책

- 동일 ID·동일 체크섬: 건너뜀
- 동일 ID·변경 체크섬: 해당 Document의 신규 MD Chunk와 Fact만 교체
- 삭제된 파일: 자동 삭제하지 않고 manifest에서 `missing`으로 표시 후 승인받아 처리
- Entity: 다른 문서가 참조할 수 있으므로 문서 재처리 시 무조건 삭제하지 않음
- 고아 Entity: 전체 적재 완료 후 별도 검증을 거쳐 정리

---

## 14. 표본 검증 단계

전체 적재 전에 다음 표본을 선정한다.

- 법령 또는 법규 중심 문서 2개
- 회계지침 2개
- 해설서 2개
- 표가 많은 문서 1개
- `(1)`, `(2)`에서 병합된 문서 1개
- 조건과 예외가 많은 문서 1개

### 14.1 Chunk 검증

- 중앙값이 목표 범위에 있는가
- 300자 미만 단편 비율이 과도하지 않은가
- 1,500자 초과 Chunk가 사유 없이 존재하지 않는가
- 문장 중간 절단이 없는가
- 표 헤더가 유실되지 않았는가
- `heading_path`가 실제 위치와 일치하는가
- `previous_chunk_id`, `next_chunk_id`가 끊기지 않는가

### 14.2 Fact 검증

- Fact 하나에 핵심 판단 하나만 있는가
- 조건과 예외가 보존되는가
- 조건부 항목마다 값 또는 미작성 이유가 존재하는가
- `REVIEW_REQUIRED` Fact가 자동 관계 생성에서 제외되는가
- `source_span`이 원문에 실제로 존재하는가
- 원문에 없는 법령과 결론을 만들지 않았는가
- 너무 일반적인 `RELATED_TO`가 과다하지 않은가
- 동일 Chunk 내부 중복 Fact가 제거됐는가

### 14.3 Entity 검증

- 약칭이 표준 법령명에 연결되는가
- 계정과목 띄어쓰기 변형이 중복 Entity를 만들지 않는가
- 동일 표면어의 서로 다른 유형이 잘못 합쳐지지 않는가
- 유사도만으로 위험하게 자동 병합되지 않는가
- 보류 후보가 검토 큐에 남는가

### 14.4 저장소 동기화 검증

- Neo4j의 모든 Chunk 참조가 Supabase Chunk에 존재하는가
- 모든 Fact가 유효한 `source_chunk_id`를 가지는가
- Supabase 신규 MD Chunk 중 Neo4j 참조가 없는 항목을 보고하는가
- 동일 `chunk_id`의 `content_checksum`이 두 저장소에서 일치하는가

---

## 15. 품질 기준과 중단 조건

초기 승인 기준:

- 공통 ID 참조 무결성: 100%
- Fact `source_span` 원문 포함률: 100%
- Fact의 유효한 `source_chunk_id` 비율: 100%
- 표본 Entity 정확/alias 자동 연결 정확도: 95% 이상
- 표본 Fact의 원문 충실도: 95% 이상
- 이유 없는 1,500자 초과 Chunk: 0개
- 문장 중간 절단: 표본에서 0건
- 기존 세법·온라인질의 행 손실: 0건

다음 중 하나라도 발생하면 전체 적재를 중단한다.

- 공통 ID 충돌
- 기존 보존 데이터 삭제 또는 변경
- Fact 출처 누락
- Entity 대량 오병합
- 표 구조의 광범위한 손실
- 임베딩 차원 또는 모델 불일치

---

## 16. n8n V3 수정 계획

현재 V3는 Supabase Chunk 검색 뒤 기존 Neo4j Child Chunk 벡터 인덱스와 `MENTIONS` 관계를 조회한다. V4 적재 후에는 이 부분을 Fact 기반으로 교체한다.

현재 저장된 V3의 Neo4j 검색 구간은 기존 스키마 확인을 위한 임시 구현이다. V4 운영본으로 간주하지 않으며, 신규 임베딩 완료 전에는 publish하거나 schedule을 활성화하지 않는다. V4 전환 시 기존 `chunk_vector_index`, `HAS_PARENT`, `MENTIONS` 기반 쿼리를 제거하고 `fact_vector_index`와 Fact 출처 복원 흐름으로 대체한다.

### 16.1 최종 실행 흐름

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

### 16.2 Supabase 1차 검색

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

### 16.3 Neo4j Fact 검색

1. 질문 임베딩으로 `fact_vector_index` 조회
2. 직접 유사 Fact 확보
3. Fact의 SUBJECT/OBJECT/CONDITION/EXCEPTION Entity 탐색
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

### 16.4 원문 복원

Neo4j Fact 문자열만 답변 모델에 전달하지 않는다.

- Neo4j 결과의 `source_chunk_id` 목록을 수집
- Supabase에서 해당 원문 Chunk를 일괄 조회
- Fact, 조건, 예외와 원문 Chunk를 하나의 후보 객체로 구성
- 원문 조회에 실패한 Fact는 답변 근거에서 제외하고 로그에 기록

### 16.5 후보 통합

- Supabase 후보와 Neo4j 원문 후보를 `chunk_id`로 병합
- 같은 Chunk이면 중복으로 세지 않음
- 기존 Supabase 후보에 Neo4j Fact·조건·예외·관계 근거를 추가
- Neo4j에서만 발견된 Chunk는 새 후보로 추가
- 통합 후보는 최대 25개

### 16.6 최종 Cohere 리랭킹

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

### 16.7 답변 생성 규칙

- 원문 Chunk에 없는 내용을 만들지 않는다.
- 그래프 관계는 관련 근거를 찾는 데 사용하며 단독 법적 근거로 사용하지 않는다.
- Fact는 반드시 연결된 원문과 함께 사용할 때만 근거로 인정한다.
- 조건과 예외를 답변에서 누락하지 않는다.
- 관련 법령은 명칭·조항·제목이 원문에서 확인될 때만 쓴다.
- 온라인질의는 유사 사례 섹션에서 사용한다.
- 날짜가 완전하지 않은 온라인질의는 유사 사례에서 제외한다.
- 자료가 충돌하면 충돌 사실과 각 출처를 표시한다.

### 16.8 장애 우회

- Supabase 검색 실패: 답변 생성 중단
- Neo4j 실패 또는 0건: Supabase 1차 후보만으로 최종 리랭킹 계속
- Neo4j 원문 복원 일부 실패: 실패 Fact 제외 후 계속
- Cohere 1차 실패: Supabase RPC 순위로 제한 실행하거나 명시적 오류 처리
- Cohere 최종 실패: 1차 점수와 graph score를 결합한 결정적 대체 순위 사용
- OpenAI 답변 생성 실패: 재시도 후 저장 단계 중단

Neo4j 장애 때문에 전체 상담 수집과 Supabase 기반 답변이 중단되지 않도록 한다.

---

## 17. n8n 관찰 가능성

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

## 18. 테스트 계획

### 18.1 단위 테스트

- 파일명·연도 정규화와 Document ID 안정성
- 분할 파일 병합 결과
- Chunk 경계와 크기
- `heading_path` 추적
- previous/next 연결
- Fact JSON 스키마 검증
- Fact 원자성 기본 규칙
- `source_span` 원문 검증
- Entity alias 정규화
- Entity 오병합 방지
- Predicate 후보 수집, 중복 통합, 승인 및 `predicate_version` 변경
- Entity 유형 후보 수집, 별칭 여부 검토, 승인 및 `entity_type_version` 변경
- Subject 미확정 Fact의 검토 보고서와 적재 제외
- 원본 Markdown 표와 임베딩용 `embedding_text` 분리 및 재현성
- 공통 ID 생성
- 재실행 체크포인트

### 18.2 저장소 통합 테스트

- Supabase Document/Chunk upsert
- Neo4j Document/Chunk 참조/Fact/Entity upsert
- Fact vector index 검색
- Entity 관계 확장
- `source_chunk_id`를 통한 Supabase 원문 복원
- 두 저장소의 checksum 비교

### 18.3 n8n 격리 테스트

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

### 18.4 회귀 테스트

현재 Supabase V3 회귀 질문 세트에 다음 기대값을 추가한다.

- 반드시 포함돼야 할 자료 유형
- 기대 Entity 또는 Fact
- 금지되는 잘못된 Entity 병합
- 기대 법령
- 기대 유사 사례
- 근거 부족 시 단정 금지

---

## 19. 구현 순서와 승인 지점

### 단계 1: 스키마와 계약 확정

- Fact JSON 스키마 확정
- Predicate 목록 확정
- Entity 유형 확정
- 별칭 사전 초기본 작성
- Supabase/Neo4j 공통 ID 함수 확정

**승인 조건:** 표본 원문 10개에 대한 수동 Fact 예시 검토 완료

### 단계 2: 전처리와 Chunk 구현

- Parent/Section 제거
- 의미 Chunker 구현
- heading path와 인접 ID 구현
- 결정적 ID와 manifest 구현

**승인 조건:** 표본 문서의 Chunk 경계와 표 보존 검토 완료

### 단계 3: Supabase 표본 적재

- Document/Chunk 스키마 적용
- 표본 Chunk 임베딩
- V4 검색 RPC 초안
- 기존 세법·온라인질의 보존 확인

**승인 조건:** 검색과 보존 데이터 무결성 통과

### 단계 4: Fact와 Entity 파이프라인 구현

- 구조화 Fact 추출
- source span 검증
- Entity 표준화와 검토 큐
- Fact 임베딩

**승인 조건:** Fact 충실도와 Entity 정확도 기준 통과

### 단계 5: Neo4j 표본 적재

- V4 제약조건과 인덱스
- Document/Chunk 참조/Fact/Entity 적재
- 직접 관계 materialization
- 원문 복원 검증

**승인 조건:** 공통 ID 참조 무결성 100%

### 단계 6: 전체 적재

- 기존 신규 MD 데이터 백업
- Supabase 파일명 기반 신규 MD 행·청크 삭제(세법·온라인질의 제외)
- Neo4j 기존 그래프 전체 초기화
- 전체 Supabase Chunk 적재
- 전체 Neo4j Fact 그래프 적재
- 품질 보고서 생성

**승인 조건:** 품질 기준 전체 통과

### 단계 7: n8n V3 전환

- 기존 Child vector 쿼리 제거
- Fact vector 및 관계 확장 쿼리 추가
- Supabase 원문 복원 추가
- 자료 유형 보정과 fallback 추가
- 검색 격리 테스트

**승인 조건:** 격리·회귀 테스트 통과

### 단계 8: 운영 전환

- V3 수동 전체 실행
- 생성 답변과 Google Sheets 입력 직전 결과 검토
- 기존 워크플로우와 비교
- 승인 후 publish 및 schedule 활성화

---

## 20. 구현 완료 정의

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

## 21. 바로 다음 작업

1. 현재 `2_build_graph_from_md.py`를 V4 요구사항과 분리할 신규 모듈 구조 설계
2. Fact와 Entity Pydantic 스키마 작성
3. 법령 약칭·계정과목 alias 사전 초기본 작성
4. 공통 ID 생성기와 manifest 테스트 작성
5. 의미 Chunker 테스트를 먼저 작성한 뒤 구현
6. 표본 문서 8~10개로 Chunk/Fact/Entity 결과 생성
7. 사용자 표본 검토 후 데이터베이스 스키마 변경 진행

이 순서를 건너뛰고 기존 `2_build_graph_from_md.py`로 전체 임베딩을 실행하면, Fact·Entity 표준화와 공통 ID 설계가 반영되지 않은 기존 그래프가 다시 생성된다.
