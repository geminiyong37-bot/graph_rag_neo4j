# 대학·사학기관 회계 GraphRAG

대학과 학교법인의 재무·회계 질의에 근거 문서를 찾아 답변하는 GraphRAG 프로젝트야. 기존 Neo4j 검색 경로를 보존하면서, n8n에서 사용하는 Supabase 하이브리드 검색을 단계적으로 개선하고 있어.

## 현재 구성

```text
회계·법률 Markdown 문서
  ├─ Neo4j: Parent/Child 청크 + Vector/BM25 + 지식 그래프
  │    └─ Cohere 재정렬 → 근거 기반 답변
  └─ Supabase: pgvector + PostgreSQL FTS
       └─ n8n Draft Agent → Cohere 재정렬 → 답변
```

| 영역 | 상태 | 설명 |
| --- | --- | --- |
| Neo4j GraphRAG | 유지 | 문서 적재, Vector/BM25 검색, 지식 그래프, 답변 검증 로직 |
| Supabase V2 | 구현 완료 | 안전한 OR FTS와 Vector 결과를 RRF로 결합 |
| Supabase V3 | 설계·구현 계획 완료 | 원문 질문 Vector, 핵심어 엄격 검색, 확장 검색을 분리할 예정 |
| n8n | 외부 운영 | 현재 워크플로우는 n8n에서 관리하며 V3 연결 절차는 계획서에 기록 |

> V3 SQL과 n8n 서브워크플로우는 아직 구현 전이야. 저장소에 있는 V3 문서는 확정된 설계와 실행 계획이야.

## 핵심 파일

| 경로 | 역할 |
| --- | --- |
| `data/` | 대학·사학기관 회계 원문 Markdown 자료 |
| `2_build_graph_from_md.py` | 원문을 Parent/Child 청크와 지식 그래프로 Neo4j에 적재 |
| `3_ask_graph.py` | Neo4j 하이브리드 검색, Cohere 재정렬, 근거 기반 답변 생성 |
| `main.py` / `Procfile` | 기존 Render API 진입점과 배포 설정 |
| `supabase/sql/001_match_univ_documents_hybrid_v2.sql` | Supabase 하이브리드 검색 V2 함수 |
| `supabase/run_hybrid_regression.py` | V1·V2 검색 품질 비교 도구 |
| `supabase/regression_cases.json` | 검색 회귀 사례 |
| `tests/` | Neo4j 답변 문맥과 Supabase SQL·회귀 도구의 단위 테스트 |
| `docs/superpowers/specs/` | 검색 구조 설계서 |
| `docs/superpowers/plans/` | 구현·통합 계획과 검증 절차 |

## 환경 설정

Python 3.10 이상을 권장해.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env_example .env
```

`.env`에 실제 환경 값을 입력해.

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=...

NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_USERNAME=...
NEO4J_PASSWORD=...
NEO4J_DATABASE=neo4j
```

## 실행과 검증

Neo4j 연결 확인:

```powershell
python 1_test_connection.py
```

Neo4j 문서 적재:

```powershell
python 2_build_graph_from_md.py
```

Neo4j 질의:

```powershell
python 3_ask_graph.py
```

전체 단위 테스트:

```powershell
python -m unittest discover -s tests -v
```

Supabase V1·V2 회귀 비교:

```powershell
python supabase/run_hybrid_regression.py
```

## Supabase 문서

- [V2 설계](docs/superpowers/specs/2026-08-17-supabase-hybrid-search-v2-design.md)
- [V2 구현 계획](docs/superpowers/plans/2026-08-17-supabase-hybrid-search-v2.md)
- [V3 핵심어 인지형 검색 설계](docs/superpowers/specs/2026-08-18-keyword-aware-hybrid-search-v3-design.md)
- [V3 구현 계획](docs/superpowers/plans/2026-08-18-keyword-aware-hybrid-search-v3.md)

## 보안과 자료 관리

- `.env`, API 키, n8n 자격 증명, 로컬 데이터베이스는 커밋하지 않아.
- n8n 워크플로우를 내보낼 때는 자격 증명 ID와 비밀값을 제거해야 해.
- `data/`의 원문을 외부에 재배포하기 전에는 출처, 이용 조건, 포함된 연락처 정보를 확인해야 해.
