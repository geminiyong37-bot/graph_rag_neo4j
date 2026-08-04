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

try:
    from flashrank import Ranker, RerankRequest
    ranker = Ranker()
    HAS_RERANKER = True
except Exception as e:
    print(f"[WARN] FlashRank 초기화 실패 (기본 검색으로 동작): {e}")
    HAS_RERANKER = False

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# 1. 순수 neo4j 드라이버 연결 (AuraDB 호환)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")


def run_query(cypher: str, params: dict = None):
    """순수 neo4j 드라이버로 Cypher 쿼리 실행"""
    with driver.session() as session:
        result = session.run(cypher, params or {})
        return [record.data() for record in result]


def hybrid_search_and_answer(question: str, top_k: int = 3) -> str:
    print(f"\n❓ 질문: {question}", flush=True)
    print("🔍 [1단계: Multi-Hop GraphRAG 벡터 & 2촌 지식 그래프 탐색] 후보 문단 및 확장 개체망 추출 중...", flush=True)

    # 1. 질문을 임베딩 벡터로 변환
    query_embedding = embeddings.embed_query(question)

    # 2. Neo4j 벡터 인덱스 검색 + 2-Hop Multi-hop 그래프 조인 (후보 10개 추출)
    initial_k = max(top_k * 3, 10)
    cypher_query = """
    CALL db.index.vector.queryNodes('chunk_vector_index', $initial_k, $query_embedding)
    YIELD node AS chunk, score
    OPTIONAL MATCH (chunk)-[:MENTIONS]->(e1:Entity)
    OPTIONAL MATCH (e1)-[r1]->(e2:Entity)
    OPTIONAL MATCH (e2)-[r2]->(e3:Entity)
    RETURN chunk.text AS text,
           coalesce(chunk.file_name, '출처 미상') AS file_name,
           score,
           collect(DISTINCT e1.id + ' (' + coalesce(e1.type, '개체') + ')') AS direct_entities,
           collect(DISTINCT e1.id + ' --[' + type(r1) + ']--> ' + e2.id) AS hop1_relations,
           collect(DISTINCT e1.id + ' --[' + type(r1) + ']--> ' + e2.id + ' --[' + type(r2) + ']--> ' + e3.id) AS hop2_relations
    """

    results = run_query(cypher_query, {"initial_k": initial_k, "query_embedding": query_embedding})

    if not results:
        print("❌ 관련된 데이터를 찾지 못했어.", flush=True)
        return "관련 데이터를 찾을 수 없습니다."

    print(f"✅ Neo4j에서 후보 문단 {len(results)}개 및 2-Hop 촘촘한 지식 그래프 추출 완료!", flush=True)

    # 3. 2차 정밀 재정렬 (FlashRank Reranking)
    if HAS_RERANKER and len(results) > 1:
        print("🎯 [2단계: FlashRank Reranker] 후보 문단 정밀 재정렬 중...", flush=True)
        passages = [{"id": idx, "text": r["text"]} for idx, r in enumerate(results)]
        rerank_request = RerankRequest(query=question, passages=passages)
        rerank_results = ranker.rerank(rerank_request)

        id_to_score = {item["id"]: item.get("score", 0) for item in rerank_results}
        for idx, r in enumerate(results):
            r["rerank_score"] = id_to_score.get(idx, 0)

        results = sorted(results, key=lambda x: x.get("rerank_score", 0), reverse=True)[:top_k]
        print(f"🏆 Rerank 정밀 심사 완료! 상위 {len(results)}개 최고 적합 문단 최종 선정!", flush=True)
    else:
        results = results[:top_k]

    # 4. 고급 지식 그래프 문맥(Context) 구성
    context_blocks = []
    for idx, row in enumerate(results, 1):
        chunk_text = row.get("text", "")
        file_name = row.get("file_name", "출처 미상")
        entities = ", ".join([e for e in row.get("direct_entities", []) if e]) or "없음"
        
        hop1 = [r for r in row.get("hop1_relations", []) if r and "None" not in r]
        hop2 = [r for r in row.get("hop2_relations", []) if r and "None" not in r]

        all_graph_paths = hop1 + hop2
        graph_str = "\n  - ".join(all_graph_paths[:8]) if all_graph_paths else "없음"

        block = f"""[참고 문단 {idx} (출처 파일: {file_name} / 유사도: {row.get('score', 0):.4f})]
원문 본문 내용:
{chunk_text}

직접 언급된 핵심 개체: {entities}
지식 그래프 추론 경로 (1-2Hop 다중 홉 연관망):
  - {graph_str}
"""
        context_blocks.append(block)

    full_context = "\n----------------------------------------\n".join(context_blocks)

    system_prompt = """당신은 사학기관 재무·회계 규칙 및 대학 온라인 상담 지식에 특화된 최고 수준의 GraphRAG 전문 AI입니다.

제공된 문맥에는 2가지 유형의 지식이 포함되어 있습니다:
1. [원문 본문 내용]: 벡터 검색으로 찾아낸 관련 지식 본문
2. [지식 그래프 추론 경로 (1-2Hop 연관망)]: 핵심 개체, 법률 조항, 계정과목, 예외 규정, 절차 간의 다중 구조적 연관망

답변 작성 지침:
1. 단순 텍스트 요약에 그치지 말고, [지식 그래프 추론 경로]에 제시된 화살표 관계망(예: 조항↔계정과목↔예외사항↔절차)을 적극적으로 연계하여 논리적이고 깊이 있게 분석하여 답변하세요.
2. 관련된 구체적인 조항, 계정과목, 행정 절차 및 예외 조건이 지식 그래프나 본문에 포함되어 있다면 이를 명확하고 구조적으로 짚어주세요.
3. 근거가 부족한 내용은 지어내지 말고, 제시된 문맥에 엄격히 기반하여 알기 쉽게 친절히 설명하세요."""

    user_prompt = f"""[검색된 지식 그래프 & 본문 문맥]
{full_context}

[질문]
{question}

[GraphRAG 구조적 지식 기반 답변]"""

    print("🤖 [3단계: Enhanced GraphRAG 다중 홉 지식 기반 답변 생성 중...]\n", flush=True)
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    return response.content


if __name__ == "__main__":
    print("=" * 60)
    print("💬 사학기관 재무회계 Multi-Hop GraphRAG AI 챗봇이 준비되었습니다!")
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
