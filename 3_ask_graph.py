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

# 1. 순수 neo4j 드라이버로 연결 (AuraDB 완전 호환, langchain_neo4j 라우팅 충돌 없음)
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
    print("🔍 [1단계: Neo4j 벡터 & 지식 그래프 검색] 후보 문단 10개 추출 중...", flush=True)

    # 1. 질문을 임베딩 벡터로 변환
    query_embedding = embeddings.embed_query(question)

    # 2. Neo4j 벡터 인덱스 검색 + 연결된 지식 그래프 조인 (후보 10개 추출)
    initial_k = max(top_k * 3, 10)
    cypher_query = """
    CALL db.index.vector.queryNodes('chunk_vector_index', $initial_k, $query_embedding)
    YIELD node AS chunk, score
    OPTIONAL MATCH (chunk)-[:MENTIONS]->(e:Entity)
    OPTIONAL MATCH (e)-[r]->(target:Entity)
    RETURN chunk.text AS text, score,
           collect(DISTINCT e.id + ' (' + e.type + ')') AS entities,
           collect(DISTINCT e.id + ' -[' + type(r) + ']-> ' + target.id) AS relationships
    """

    results = run_query(cypher_query, {"initial_k": initial_k, "query_embedding": query_embedding})

    if not results:
        print("❌ 관련된 데이터를 찾지 못했어.", flush=True)
        return "관련 데이터를 찾을 수 없습니다."

    print(f"✅ Neo4j에서 후보 문단 {len(results)}개 및 연관 지식 그래프 추출 완료!", flush=True)

    # 3. 2차 정밀 재정렬 (FlashRank Reranking)
    if HAS_RERANKER and len(results) > 1:
        print("🎯 [2단계: FlashRank Reranker] 후보 문단 정밀 재정렬 중...", flush=True)
        passages = [{"id": idx, "text": r["text"]} for idx, r in enumerate(results)]
        rerank_request = RerankRequest(query=question, passages=passages)
        rerank_results = ranker.rerank(rerank_request)

        # Rerank 점수 기준으로 results 재정렬
        id_to_score = {item["id"]: item.get("score", 0) for item in rerank_results}
        for idx, r in enumerate(results):
            r["rerank_score"] = id_to_score.get(idx, 0)

        results = sorted(results, key=lambda x: x.get("rerank_score", 0), reverse=True)[:top_k]
        print(f"🏆 Rerank 정밀 심사 완료! 상위 {len(results)}개 최고 적합 문단 최종 선정!", flush=True)
    else:
        results = results[:top_k]

    # 3. 프롬프트 문맥(Context) 구성
    context_blocks = []
    for idx, row in enumerate(results, 1):
        chunk_text = row.get("text", "")
        entities = ", ".join(row.get("entities", [])) or "없음"
        relationships = ", ".join(row.get("relationships", [])) or "없음"

        block = f"""[참고 문단 {idx} (유사도: {row.get('score', 0):.4f})]
원문 내용:
{chunk_text}

연관 개체(노드): {entities}
연관 관계(화살표): {relationships}
"""
        context_blocks.append(block)

    full_context = "\n----------------------------------------\n".join(context_blocks)

    system_prompt = """당신은 사학기관 재무회계 규칙 및 회계 관련 전문 AI입니다.
제공된 [참고 문단]과 [연관 개체/관계 지식 그래프] 정보를 바탕으로 질문에 대해 명확하고 정확하게 답변하세요.

규칙:
1. 제공된 검색 결과 문맥에 기초해서 답변하세요.
2. 회계/법률 용어는 알기 쉽게 설명하세요.
3. 관련된 조항이나 예산 규칙이 있으면 명확히 언급하세요."""

    user_prompt = f"""[검색된 문맥 정보]
{full_context}

[질문]
{question}

[답변]"""

    print("🤖 [2단계: Hybrid GraphRAG 답변 생성 중...]\n", flush=True)
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    return response.content

if __name__ == "__main__":
    print("=" * 60)
    print("💬 사학기관 재무회계 지식 그래프 AI 챗봇이 준비되었습니다!")
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
