# 임베딩 V4 1단계 구현 설계 — 청킹·Fact·Entity 표준화

**작성일:** 2026-08-20  
**상태:** 구현 계획 작성 전 사용자 검토 대기  
**상위 계획:** `docs/superpowers/plans/2026-08-20-chunk-fact-entity-embedding-v4.md`

## 1. 목적

전체 데이터를 Supabase와 Neo4j에 다시 적재하기 전에, 로컬에서 결과를 반복 검증할 수 있는 V4 기반 모듈을 만든다. 이번 범위는 데이터 모델, 결정적 ID, 용어 표준화, 의미 단위 청킹, 실행 기록과 단위 테스트까지다.

이번 단계에서는 외부 저장소에 쓰지 않는다. Supabase·Neo4j 스키마 변경, 임베딩 API 호출, LLM Fact 추출, n8n 변경, 전체 데이터 재적재는 후속 단계로 남긴다.

## 2. 선택한 접근

기존 `2_build_graph_from_md.py`를 확장하지 않고 `embedding_v4` 패키지를 새로 만든다.

- 기존 스크립트의 동작을 보존한다.
- 모델·ID·표준화·청킹·실행 기록을 서로 독립적으로 테스트할 수 있다.
- 후속 적재 파이프라인이 같은 계약을 재사용할 수 있다.
- 이번 단계에 필요 없는 저장소 연결과 모델 호출을 분리해 실패 범위를 줄인다.

검토한 다른 방법은 기존 스크립트를 직접 개조하는 방법과 단일 V4 스크립트를 만드는 방법이다. 전자는 기존 처리에 회귀 위험이 있고, 후자는 기능 간 경계가 흐려져 테스트와 교체가 어렵기 때문에 선택하지 않는다.

## 3. 범위와 파일 구조

```text
embedding_v4/
  __init__.py
  models.py
  ids.py
  normalization.py
  chunker.py
  manifest.py
  aliases.yaml
tests/
  test_embedding_v4_models.py
  test_embedding_v4_ids.py
  test_embedding_v4_normalization.py
  test_embedding_v4_chunker.py
  test_embedding_v4_manifest.py
```

`aliases.yaml`은 코드와 분리된 버전 관리 대상이다. 프로젝트에 Pydantic 2와 PyYAML이 이미 있으므로 새 의존성은 추가하지 않는다.

### 3.1 입력과 Markdown 정제 계약

입력 기준 폴더는 `data_embedding_ready`다. 입력은 UTF-8 Markdown이며 같은 문서의 `(1)`, `(2)` 분할 파일은 미리 병합되어 있어야 한다. 기존 `merge_embedding_parts.py`와 `prepare_embedding_md.py`의 결과를 사용할 수 있지만, V4 처리 전에 다음 조건을 다시 검증한다.

반드시 보존할 정보:

- 문서 제목과 원래 파일명·연도
- 장·절·조·항·호 제목과 `heading_path`
- 질의와 답변의 구분
- 표 제목, 열 헤더, 행 값의 관계
- 법령명, 조문 번호와 조문 제목
- 사례 일자와 사건·질의 제목
- 각주, 조건, 단서와 예외 문구

제거하거나 안전하게 정리할 정보:

- 반복 페이지 번호와 반복 머리말·꼬리말
- 목차에만 존재하는 페이지 번호 열
- OCR로 단어 내부에 생긴 불필요한 줄바꿈
- 의미 없는 연속 공백과 깨진 글머리 기호

정제는 의미와 원문 근거를 바꾸지 않아야 한다. 원본 파일은 수정하지 않고 정제 결과와 체크섬을 로컬 산출물로 남긴다. Section 노드는 만들지 않으며 현재 위치는 `heading_path` 문자열로만 보존한다.

## 4. 데이터 계약

### 4.1 Document

Document는 여러 Chunk가 공유하는 문서 정보와 처리 버전을 한 번만 보관한다.

- `document_id`, `display_name`, `file_name`
- `year`, `document_type`, `source_path`
- `content_checksum`
- `parser_version`, `chunker_version`

### 4.2 Chunk

Chunk는 검색과 근거 복원을 위한 원문 단위다. 최소 필드는 다음과 같다.

- `chunk_id`, `document_id`, `chunk_index`
- `content`, `embedding_text`, `content_checksum`
- `heading_path`
- `previous_chunk_id`, `next_chunk_id`

`document_type`, `file_name`, `year`, `parser_version`, `chunker_version`은 Chunk마다 반복하지 않고 `document_id`로 Document에서 찾는다. 길이는 정규화된 `content` 본문만 세며 제목 경로와 기타 메타데이터는 글자 수에서 제외한다.

`content`에는 출처 표시와 Fact 근거 검증을 위한 원본 Markdown을 그대로 보존한다. `embedding_text`에는 실제 벡터 생성에 사용할 텍스트를 저장한다. 일반 본문은 `content`와 같게 두고, 표는 표 제목·열 헤더·행 값의 관계가 드러나는 문장형 텍스트로 변환한다. 본문 의미가 제목에 의존하는 경우에만 `heading_path`를 `embedding_text` 앞에 붙인다. ID, 파일명, 연도, 체크섬, 버전은 임베딩 입력에 포함하지 않는다.

1단계에서는 두 값을 문서별 로컬 JSONL 결과 파일에 함께 저장해 사람이 원본과 변환 결과를 비교할 수 있게 한다. `content_checksum`은 변환문이 아닌 원본 `content`를 기준으로 계산한다. Supabase에 `embedding_text` 컬럼을 둘지는 표본 품질과 저장 용량을 확인한 후 후속 단계에서 결정한다.

### 4.3 Fact

Fact는 원문에 근거한 하나의 독립 판단·규칙·의무·허용·금지·분류·예외다. 검색용 Chunk와 재사용 개념인 Entity 사이에서 “무엇이 어떤 조건으로 성립하는가”를 보존한다.

항상 저장하는 핵심 항목은 `fact_id`, `statement`, `predicate`, `source_chunk_id`, `source_span`, `extractor_version`이다. `subject`, `object`, `conditions`, `exceptions`, `modality`, `result`, `legal_basis`, `effective_date`는 원문에 명확히 존재하거나 해당 Fact에 필요한 경우에만 저장한다. AI가 스스로 매기는 `confidence`는 정확성 보장이 약하므로 Fact 필드에서 제외한다.

조건부 항목을 작성하지 않았을 때는 `field_assessments`에 그 이유를 반드시 기록한다.

- `NOT_STATED`: 원문에 해당 정보가 없음
- `NOT_APPLICABLE`: 이 Fact에는 해당 항목이 필요하지 않음
- `REVIEW_REQUIRED`: 원문이 모호해 자동 결정하지 못함

값이 있는 항목은 `field_assessments`에 중복 기록하지 않는다. 조건부 항목에 값도 없고 상태도 없으면 의도적인 공란이 아닌 실제 누락으로 간주해 검증에서 거부한다. `subject`가 없으면 Fact 후보 자체는 보존할 수 있지만 Entity 관계를 만들지 않고 반드시 검토 대상으로 보낸다.

```json
{
  "fact_id": "..._f_01",
  "statement": "업무추진비의 교비회계 집행에는 사전 승인이 필요하다.",
  "predicate": "REQUIRES_APPROVAL",
  "subject": {"surface": "업무추진비", "type": "ExpenseItem"},
  "conditions": ["교비회계에서 집행하는 경우"],
  "source_chunk_id": "..._ch_0012",
  "source_span": "업무추진비를 교비회계에서 집행하려면 사전에 승인을 받아야 한다.",
  "extractor_version": "fact-v1",
  "field_assessments": {
    "object": "NOT_APPLICABLE",
    "exceptions": "NOT_STATED",
    "modality": "NOT_APPLICABLE",
    "result": "NOT_STATED",
    "legal_basis": "NOT_STATED",
    "effective_date": "NOT_STATED"
  }
}
```

- Fact 하나에는 통제된 predicate 하나만 둔다.
- `source_span`은 원문의 연속 구간이어야 하며 표준화하거나 고쳐 쓰지 않는다.
- 조건과 예외는 기본적으로 Fact의 속성으로 둔다.
- 필수 predicate 또는 원문 근거가 없으면 유효성 검사에서 거부한다.
- `statement`는 의미가 변할 수 있으므로 공격적으로 표준화하지 않는다.

초기 predicate 목록은 `CLASSIFIED_AS`, `APPLIES_TO`, `GOVERNS`, `REQUIRES`, `PERMITS`, `PROHIBITS`, `INCLUDES`, `EXCLUDES`, `EXCEPTS`, `RESULTS_IN`, `DEDUCTIBLE_AS`, `REQUIRES_APPROVAL`, `REQUIRES_REPORTING`, `RELATED_TO`로 제한한다. 이는 최종 고정 목록이 아니라 표본 테스트를 위한 버전 1 목록이다.

허용 목록으로 표현하기 어려운 관계가 나오면 `RELATED_TO`로 억지 변환하거나 새 predicate를 즉시 생성하지 않는다. 원문, 제안 predicate, 기존 목록으로 표현할 수 없는 이유, 발생 횟수를 `predicate_candidates.jsonl`에 기록한다. 표본 Fact 검토가 끝난 뒤 반복성과 검색 가치를 확인해 사용자가 승인한 항목만 목록에 추가하고 `predicate_version`을 올린다. 단순 표현 차이는 새 predicate를 만들지 않고 기존 predicate로 통합한다.

초기 표본 테스트에서는 특히 정의, 계산 방식, 자격·대상 여부, 제출·신고, 기한·시행 관계가 기존 목록으로 충분히 표현되는지 확인한다.

### 4.4 검토 대상과 승인 흐름

`subject`가 없는 Fact란 판단 문장은 발견했지만 무엇에 대한 판단인지 원문 구간만으로 확정할 수 없는 경우다. 예를 들어 표의 병합 셀, 앞 행을 이어받는 표현, “사전 승인을 받아야 한다”처럼 주체가 생략된 문장이 해당한다. AI가 주체를 추측해 잘못된 Entity 관계를 만드는 대신 Fact 후보와 원문 근거를 보존한다.

검토자는 프로젝트 사용자 또는 사용자가 지정한 운영자다. AI와 코드는 검토 자료를 만들지만 최종 확정은 하지 않는다. 검토는 표본 Fact 추출 후, Neo4j 표본 적재 전에 수행한다.

1. 시스템이 `review_required.jsonl`과 사람이 읽을 수 있는 요약 보고서를 생성한다.
2. 검토자는 원문 Chunk와 앞뒤 Chunk를 보고 subject를 확정하거나 Fact를 제외한다.
3. 승인된 수정에는 검토자, 검토 시각, 결정 사유를 기록한다.
4. 미해결 항목은 원문 검색용 후보로만 남기고 Neo4j Entity 관계와 전체 적재에서 제외한다.
5. Predicate 후보도 같은 검토 시점에 승인·기각·보류로 분류한다.

### 4.5 Entity

Entity는 여러 Fact에서 재사용되는 표준 개념이다. 법령뿐 아니라 기관, 계정과목, 절차, 비용 항목, 역할과 시스템도 표준화한다.

초기 유형은 `Organization`, `Regulation`, `Article`, `Account`, `Procedure`, `Concept`, `PersonRole`, `System`, `ExpenseItem`, `Document`다. 이는 최종 고정 목록이 아니라 표본 테스트를 위한 버전 1 목록이다. 조건과 예외 문구는 반복 검색 가치가 명확한 경우에만 Entity로 승격한다. 일반적인 조건·예외를 무조건 노드로 만들지 않는다.

초기 유형으로 정확히 표현하기 어려운 Entity는 `Concept`에 억지로 넣거나 새 유형을 즉시 생성하지 않는다. 원문 표현, 제안 유형, 기존 유형으로 표현할 수 없는 이유, 발생 횟수를 `entity_type_candidates.jsonl`에 기록한다. 표본 Entity 검토가 끝난 뒤 서로 구분할 검색 가치와 반복성이 확인되고 사용자가 승인한 유형만 추가하며 `entity_type_version`을 올린다. 이름만 다른 동일 개념은 새 유형이 아니라 기존 유형의 별칭으로 처리한다.

Entity는 `entity_id`, `entity_type`, `canonical_name`, `aliases`, `normalization_version`을 가진다. 원문 표현은 Fact의 subject/object에 `surface`로 별도 보존한다.

## 5. 결정적 ID

같은 입력과 같은 버전에서는 항상 같은 ID가 나와야 한다.

- Document ID: 연도와 정규화된 파일명으로 만들고, 충돌할 때만 내용 체크섬 접미사를 붙인다.
- Chunk ID: `{document_id}_ch_{순번 4자리}`
- Fact ID: `{chunk_id}_f_{원문 순서 2자리}`. 같은 Chunk와 같은 추출기 버전에서는 Fact를 원문 등장 순서로 정렬한다.
- Entity ID: `{entity_type}:{정규화된 canonical_name}`

파일의 임시 절대경로, 실행 시각, 데이터베이스 자동 증가값은 ID 재료로 쓰지 않는다. 체크섬 알고리즘과 정규화 버전은 상수로 명시한다.
체크섬 접미사는 내부 충돌 방지용이며 사용자에게 보여주는 `display_name`에는 포함하지 않는다.

## 6. 표준화와 별칭

표준화 순서는 다음과 같다.

1. Unicode NFC 적용
2. 앞뒤·중복 공백과 안전한 문장부호 변형 정리
3. Entity 유형별 별칭 사전 조회
4. 동일 유형의 정확한 canonical name 또는 검증된 alias 연결
5. 불확실한 후보는 자동 병합하지 않고 검토 대상으로 반환

예를 들어 `업무 추진비`, `업무추진 비용`, `업무추진비용`, `업무추진경비`는 검증된 Account 별칭으로 등록하고 `업무추진비`에 연결한다. 반면 `회의비`처럼 문서와 회계 기준에 따라 별도 계정일 수 있는 표현은 단어가 관련돼 보인다는 이유만으로 `업무추진비`에 자동 병합하지 않고 검토 대상으로 둔다. 유형이 다르면 표면 표현이 같아도 별도 Entity다.

별칭 사전은 유형별 `canonical_name`과 `aliases`를 담고 `normalization_version`으로 추적한다. 법령명·약칭, 기관명, 계정과목, 절차, 비용 항목의 띄어쓰기·약칭·복수 표현을 포함한다.

초기 별칭 fixture에는 최소한 다음 대응을 포함한다.

- `지방자치단체를 당사자로 하는 계약에 관한 법률 시행령` ↔ `지방계약법 시행령`
- `중소기업 구매촉진 및 판로지원에 관한 법률` ↔ `판로지원법`
- `조달사업에 관한 법률` ↔ `조달사업법`
- `업무추진비` ↔ `업무 추진비`, `업무추진 비용`, `업무추진비용`, `업무추진경비`

Entity 표준화는 Fact가 확정된 뒤 수행해 subject/object의 문맥과 유형을 함께 판단한다. 법령 조항은 법령명과 조문 번호가 모두 확인될 때만 Article Entity로 만든다. 불확실한 후보는 `surface`, 제안 Entity ID, 비교 근거, `source_chunk_id`, 상태를 검토 큐에 기록하고 검토 결과를 별칭 사전의 다음 버전에 반영한다.

검색 키워드도 동일한 기본 문자열 정규화를 사용하되 Entity 병합과는 분리한다. 핵심·선택 키워드는 안전한 표준형과 검증된 검색 별칭을 확장할 수 있지만, 그 결과가 Entity 동일성을 자동 확정하지는 않는다.

## 7. 의미 단위 청킹

### 7.1 크기

- 일반 목표: 700~1,000자
- 의미 완결성을 위한 일반 상한: 1,200자
- 긴 표 또는 완결된 법령 조항의 예외 상한: 1,500자
- 짧지만 완결된 조항·문답: 300자 이상이면 독립 Chunk 허용

1,200자까지 허용하는 경우는 문단, 질의·답변, 조건과 결론, 법령 본문과 단서가 1,000자 지점에서 끊기는 경우다. 크기를 맞추려고 완결된 단위를 억지로 합치거나 자르지 않는다. 1,500자를 넘는 Chunk는 생성 오류로 처리한다.

### 7.2 경계 우선순위

1. 장·절·조 등 제목 경계
2. 질의와 직접 답변의 묶음
3. 문단 경계
4. 완결된 문장 경계
5. 표 전체 또는 행 묶음

문장 중간, 표 헤더와 데이터 행 사이, 질문과 직접 답변 사이, 법령 본문과 단서 사이를 자르지 않는다.

### 7.3 오버랩과 인접 문맥

고정 글자 오버랩은 `0자`다. 중복 원문 대신 `previous_chunk_id`와 `next_chunk_id`를 저장하고, 검색 결과가 문장 시작·끝의 불완전성, 이어지는 표, 대명사 참조 같은 문맥 부족 신호를 가질 때만 이웃 Chunk를 추가 조회한다.

긴 표를 여러 Chunk로 나눌 때는 각 Chunk에 표 제목, 열 헤더, 단위와 기준일을 반복한다. 이는 표를 이해하기 위한 구조 정보 반복이며 일반 본문의 글자 오버랩이 아니다. 데이터 행은 중복하지 않는다.

## 8. Manifest와 재실행

`manifest.py`는 로컬 JSON/JSONL 형식으로 문서별 상태를 기록한다.

- `document_id`, `input_checksum`
- `parser_version`, `chunker_version`, `schema_version`, `normalization_version`
- `predicate_version`, `entity_type_version`, `alias_version`
- 생성 Chunk 수와 결과 체크섬
- 단계 상태와 오류 메시지

입력 체크섬과 관련 버전이 같고 이전 결과가 유효하면 다시 만들지 않는다. 하나라도 달라지면 해당 문서의 로컬 결과를 다시 계산한다. 실행 시각은 감사 정보에는 남길 수 있지만 동일성 판단에는 쓰지 않는다.

## 9. 처리 흐름과 오류 처리

```text
Markdown 입력
  → 문서 메타데이터와 제목 경로 해석
  → 의미 단위 Chunk 생성
  → 결정적 ID와 인접 ID 부여
  → Fact/Entity 계약 검증 및 표준화 도구 제공
  → Manifest 기록
```

이번 단계는 Fact를 LLM으로 추출하지 않는다. 대신 이후 추출기가 반환해야 할 입력·출력 계약과 검증기를 만든다.

- 핵심 필드 누락, 조건부 필드의 값·상태 동시 누락, 허용되지 않은 predicate는 명확한 검증 오류로 반환한다.
- `source_span`이 원문 Chunk에 없으면 Fact를 거부한다.
- 1,500자 초과, 빈 Chunk, 행이 중복된 분할 표는 청킹 오류로 반환한다.
- 별칭 사전에서 같은 유형·같은 alias가 서로 다른 표준명에 연결되면 시작 시 설정 오류로 중단한다.
- 유사도만 높은 표현은 병합하지 않고 `pending` 검토 후보로 반환한다.

## 10. 테스트와 완료 조건

단위 테스트는 외부 네트워크나 데이터베이스 없이 실행되어야 한다.

- UTF-8, 분할 파일 병합 여부와 입력 품질 위반을 검출한다.
- 제목·질의응답·표·법령 조문·각주·단서는 보존하고 반복 머리말·페이지 번호 등은 제거한다.
- Pydantic 모델이 유효 입력을 받고 필수 근거가 없는 Fact를 거부한다.
- 조건부 필드마다 값 또는 `field_assessments` 상태 중 정확히 하나가 있어야 한다.
- `REVIEW_REQUIRED` 항목이 있는 Fact는 검토 대상으로 표시되고 자동 관계 생성에서 제외된다.
- 허용되지 않은 관계는 `predicate_candidates.jsonl`에 기록되고 자동으로 `RELATED_TO`로 바뀌지 않는다.
- 승인된 Predicate 변경은 `predicate_version` 상승 없이는 적용되지 않는다.
- 기존 유형으로 분류하기 어려운 Entity는 `entity_type_candidates.jsonl`에 기록되고 자동으로 `Concept`가 되지 않는다.
- 승인된 Entity 유형 변경은 `entity_type_version` 상승 없이는 적용되지 않는다.
- 같은 입력에서 Document·Chunk·Fact·Entity ID와 체크섬이 반복 실행마다 같다.
- 제목 경로와 앞뒤 Chunk 연결이 정확하다.
- 일반 Chunk는 목표 범위를 우선하고 예외 없이 1,500자를 넘지 않는다.
- 질의·답변, 조건·결론, 법령 본문·단서를 분리하지 않는다.
- 긴 표 분할 시 제목·헤더는 반복하고 데이터 행은 중복하지 않는다.
- 로컬 JSONL에 원본 Markdown `content`와 재현 가능한 `embedding_text`가 함께 저장된다.
- 표의 `embedding_text`가 제목·열 헤더·행 값의 관계를 보존하고 원본 `content`를 변경하지 않는다.
- 일반 본문 Chunk 사이의 고정 글자 오버랩은 없다.
- 검증된 띄어쓰기·약칭·복수 표현은 같은 Entity로 연결된다.
- 유형이 다르거나 유사도만 높은 표현은 자동 병합되지 않는다.
- 원문 `surface`와 `source_span`은 그대로 보존된다.
- 동일 체크섬·버전은 건너뛰고 변경된 입력이나 버전만 다시 처리한다.

표본 fixture는 법령·법규 중심 문서 2개, 회계지침 2개, 해설서 2개, 표가 많은 문서 1개, 분할 파일을 병합한 문서 1개, 조건·예외가 많은 문서 1개로 구성한다. 실제 LLM Fact 추출은 후속 단계지만, 이 단계에서는 표본 원문의 수동 Fact 예시를 사용해 스키마·검증기·원문 근거 규칙을 시험한다.

완료 기준은 새 모듈 전체 테스트가 통과하고, 기존 테스트가 회귀 없이 통과하며, 외부 저장소에 쓰기가 발생하지 않는 것이다.

## 11. 후속 단계와의 경계

이 기반이 승인·구현된 뒤에 별도 계획으로 다음을 진행한다.

1. 표본 문서 Chunk 생성과 사람이 확인하는 품질 검증
2. LLM Fact 추출 및 Entity 후보 생성
3. Supabase·Neo4j 스키마와 적재기
4. 전체 데이터 백업·재적재
5. n8n V4 검색 전환과 회귀 테스트

외부 저장소 쓰기나 운영 워크플로우 변경은 각 단계의 미리보기, 백업, 승인 조건을 별도로 정의한 뒤 수행한다.
