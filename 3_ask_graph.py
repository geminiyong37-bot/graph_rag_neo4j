import os
import sys
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from neo4j import GraphDatabase
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
COHERE_API_KEY = os.getenv("COHERE_API_KEY")

# 1. Cohere Reranker 클라이언트 초기화
HAS_COHERE = False
cohere_client = None

if COHERE_API_KEY:
    try:
        import cohere
        cohere_client = cohere.ClientV2(api_key=COHERE_API_KEY)
        HAS_COHERE = True
        print("🎯 Cohere Reranker v3.0 (Multilingual) 정밀 심사 엔진 활성화 완료!")
    except Exception as e:
        print(f"[WARN] Cohere Reranker 초기화 실패 (기본 RRF 검색으로 동작): {e}")

# 2. 순수 neo4j 드라이버 연결 (AuraDB 호환)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")


def run_query(cypher: str, params: dict = None):
    """순수 neo4j 드라이버로 Cypher 쿼리 실행"""
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, params or {})
        return [record.data() for record in result]


def hybrid_search_and_answer(question: str, top_k: int = 5) -> str:
    print(f"\n❓ 질문: {question}", flush=True)
    print("🔍 [1단계: 3종 하이브리드 검색 시작 - 키워드 + 벡터 + 2-Hop 지식 그래프]", flush=True)

    # 1. 질문을 임베딩 벡터로 변환
    query_embedding = embeddings.embed_query(question)
    initial_k = max(top_k * 4, 20)

    # --- [검색 1] Child Chunk 기반 벡터 유사도 검색 + Parent 해설 및 지식 그래프추적 ---
    vector_results = run_query("""
        CALL db.index.vector.queryNodes('chunk_vector_index', $initial_k, $query_embedding)
        YIELD node AS child, score
        OPTIONAL MATCH (child)-[:HAS_PARENT]->(parent:Chunk)
        OPTIONAL MATCH (child)-[:MENTIONS]->(e1:Entity)
        OPTIONAL MATCH (e1)-[r1]->(e2:Entity)
        OPTIONAL MATCH (e2)-[r2]->(e3:Entity)
        RETURN child.id AS child_id,
               child.text AS child_text,
               coalesce(parent.text, child.text) AS parent_text,
               coalesce(child.file_name, '출처 미상') AS file_name,
               coalesce(child.year, 2024) AS year,
               score AS vector_score,
               collect(DISTINCT e1.id + ' (' + coalesce(e1.type, '개체') + ')') AS direct_entities,
               collect(DISTINCT e1.id + ' --[' + type(r1) + ']--> ' + e2.id) AS hop1_relations,
               collect(DISTINCT e1.id + ' --[' + type(r1) + ']--> ' + e2.id + ' --[' + type(r2) + ']--> ' + e3.id) AS hop2_relations
    """, {"initial_k": initial_k, "query_embedding": query_embedding})

    # --- [검색 2] 키워드 FULLTEXT 검색 ---
    keywords = " AND ".join([w for w in question.split() if len(w) > 1][:8])
    fulltext_results = []
    if keywords:
        try:
            fulltext_results = run_query("""
                CALL db.index.fulltext.queryNodes('chunk_fulltext_index', $keywords, {limit: $initial_k})
                YIELD node AS child, score
                OPTIONAL MATCH (child)-[:HAS_PARENT]->(parent:Chunk)
                RETURN child.id AS child_id,
                       child.text AS child_text,
                       coalesce(parent.text, child.text) AS parent_text,
                       coalesce(child.file_name, '출처 미상') AS file_name,
                       coalesce(child.year, 2024) AS year,
                       score AS fulltext_score
            """, {"keywords": keywords, "initial_k": initial_k})
        except Exception as ft_err:
            print(f"  [WARN] 키워드 검색 오류 (벡터 검색만 사용): {ft_err}", flush=True)

    print(f"✅ 벡터 검색: {len(vector_results)}개 / 키워드 검색: {len(fulltext_results)}개 후보 추출 완료!", flush=True)

    # --- [RRF 스코어링] 벡터 + 키워드 결과 통합 ---
    RRF_K = 60
    chunk_scores = {}

    for rank, row in enumerate(vector_results, 1):
        cid = row.get("child_id")
        if not cid:
            continue
        if cid not in chunk_scores:
            chunk_scores[cid] = {
                "rrf_score": 0.0,
                "child_text": row.get("child_text", ""),
                "parent_text": row.get("parent_text", ""),
                "file_name": row.get("file_name", "출처 미상"),
                "year": row.get("year", 2024),
                "vector_score": row.get("vector_score", 0),
                "direct_entities": row.get("direct_entities", []),
                "hop1_relations": row.get("hop1_relations", []),
                "hop2_relations": row.get("hop2_relations", []),
            }
        chunk_scores[cid]["rrf_score"] += 1.0 / (RRF_K + rank)

    for rank, row in enumerate(fulltext_results, 1):
        cid = row.get("child_id")
        if not cid:
            continue
        if cid not in chunk_scores:
            chunk_scores[cid] = {
                "rrf_score": 0.0,
                "child_text": row.get("child_text", ""),
                "parent_text": row.get("parent_text", ""),
                "file_name": row.get("file_name", "출처 미상"),
                "year": row.get("year", 2024),
                "vector_score": 0,
                "direct_entities": [],
                "hop1_relations": [],
                "hop2_relations": [],
            }
        chunk_scores[cid]["rrf_score"] += 1.0 / (RRF_K + rank)

    sorted_chunks = sorted(chunk_scores.values(), key=lambda x: x["rrf_score"], reverse=True)[:max(top_k * 2, 10)]

    if not sorted_chunks:
        print("❌ 관련된 데이터를 찾지 못했어.", flush=True)
        return "관련 데이터를 찾을 수 없습니다."

    print(f"🏆 RRF 1차 통합 후보 {len(sorted_chunks)}개 추출 완료!", flush=True)

    # --- [2단계: Cohere Reranker 정밀 재정렬] ---
    if HAS_COHERE and len(sorted_chunks) > 1:
        print("🎯 [2단계: Cohere Reranker v3.0] 최신 다국어 딥러닝 정밀 심사 중...", flush=True)
        passages = [r["child_text"] for r in sorted_chunks]
        try:
            rerank_res = cohere_client.rerank(
                model="rerank-multilingual-v3.0",
                query=question,
                documents=passages,
                top_n=top_k
            )
            final_chunks = []
            for item in rerank_res.results:
                orig_item = sorted_chunks[item.index]
                orig_item["rerank_score"] = item.relevance_score
                final_chunks.append(orig_item)
            sorted_chunks = final_chunks
            print(f"🏆 Cohere Rerank 정밀 심사 완료! 최종 상위 {len(sorted_chunks)}개 선택!", flush=True)
        except Exception as c_err:
            print(f"  [WARN] Cohere Rerank 예외 발생 (RRF 결과 사용): {c_err}", flush=True)
            sorted_chunks = sorted_chunks[:top_k]
    else:
        sorted_chunks = sorted_chunks[:top_k]

    # --- [3단계: 어미 해설 텍스트 스위칭 + 중복 제거 + 지식그래프 문맥 구성] ---
    context_blocks = []
    seen_parents = set()
    display_idx = 1

    for row in sorted_chunks:
        parent_text = row.get("parent_text", row.get("child_text", ""))
        
        # 어미 해설 중복 제거
        if parent_text in seen_parents:
            continue
        seen_parents.add(parent_text)

        file_name = row.get("file_name", "출처 미상")
        year = row.get("year", 2024)
        entities = ", ".join([e for e in row.get("direct_entities", []) if e]) or "없음"
        hop1 = [r for r in row.get("hop1_relations", []) if r and "None" not in r]
        hop2 = [r for r in row.get("hop2_relations", []) if r and "None" not in r]
        all_graph_paths = hop1 + hop2
        graph_str = "\n  - ".join(all_graph_paths[:8]) if all_graph_paths else "없음"

        rerank_info = f" / Cohere Rerank 점수: {row.get('rerank_score', 0):.4f}" if "rerank_score" in row else ""

        block = f"""[참고 문단 {display_idx} (출처 파일: {file_name} / 지침 연도: {year}년{rerank_info})]
어미 해설 및 전체 본문 내용:
{parent_text}

직접 언급된 핵심 개체: {entities}
지식 그래프 추론 경로 (1-2Hop 다중 홉 연관망):
  - {graph_str}
"""
        context_blocks.append(block)
        display_idx += 1

    full_context = "\n----------------------------------------\n".join(context_blocks)

    system_prompt = """당신은 사학기관 재무·회계 규칙 및 대학 온라인 상담 지식에 특화된 최고 수준의 GraphRAG 전문 AI입니다.

제공된 문맥에는 2가지 유형의 지식이 포함되어 있습니다:
1. [어미 해설 및 전체 본문 내용]: Cohere Reranker와 하이브리드 검색으로 찾아낸 관련 지식 본문
2. [지식 그래프 추론 경로 (1-2Hop 연관망)]: 핵심 개체, 법률 조항, 계정과목, 예외 규정, 절차 간의 다중 구조적 연관망

답변 작성 지침:
1. 단순 텍스트 요약에 그치지 말고, [지식 그래프 추론 경로]에 제시된 화살표 관계망(예: 조항↔계정과목↔예외사항↔절차)을 적극적으로 연계하여 논리적이고 깊이 있게 분석하여 답변하세요.
2. 관련된 구체적인 조항, 계정과목, 행정 절차 및 예외 조건이 지식 그래프나 본문에 포함되어 있다면 이를 명확하고 구조적으로 짚어주세요.
3. 근거가 부족한 내용은 지어내지 말고, 제시된 문맥에 엄격히 기반하여 알기 쉽게 친절히 설명하세요.
4. 만약 검색된 여러 문맥이나 지침 간에 연도별로 상충되거나 변경된 내용이 존재할 경우, 가장 최신 연도(가장 최근 개정/작성된 문서)의 규정을 최우선으로 적용하여 답변하고, 필요한 경우 이전 연도 대비 개정되거나 변경된 포인트를 분명히 비교하여 설명하세요."""

    user_prompt = f"""[검색된 지식 그래프 & 본문 문맥]
{full_context}

[질문]
{question}

[GraphRAG 구조적 지식 기반 답변]"""

    print("🤖 [3단계: Enhanced Cohere GraphRAG 다중 홉 지식 기반 답변 생성 중...]\n", flush=True)
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    return response.content


if __name__ == "__main__":
    print("=" * 60)
    print("💬 Cohere Rerank v3.0 탑재 사학기관 재무회계 Multi-Hop GraphRAG AI 챗봇!")
    print("종료하려면 'exit' 또는 'q'를 입력하세요.")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n[질문 입력]: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "q", "quit", "종료"]:
                print("👋 챗봇을 종료합니다. 수고하셨습니다!")
                break

            answer = hybrid_search_and_answer(user_input)
            print("\n💬 [AI 답변]:")
            print(answer)
            print("-" * 60)
        except KeyboardInterrupt:
            print("\n👋 챗봇을 종료합니다.")
            break
        except Exception as err:
            print(f"❌ 오류 발생: {err}")
