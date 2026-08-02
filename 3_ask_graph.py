import os
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_neo4j import Neo4jGraph

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# 1. Neo4j 및 LLM / Embeddings 초기화
graph = Neo4jGraph(
    url=NEO4J_URI,
    username=NEO4J_USERNAME,
    password=NEO4J_PASSWORD,
    database=os.getenv("NEO4J_DATABASE"),
)

llm = ChatOpenAI(
    model=OPENAI_MODEL,
    temperature=0,
)

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

def hybrid_search_and_answer(question: str, top_k: int = 3) -> str:
    print(f"\n❓ 질문: {question}", flush=True)
    print("🔍 [1단계: 벡터 검색] 질문과 유사한 원문 Chunk 탐색 중...", flush=True)

    # 1. 질문을 임베딩 벡터로 변환
    query_embedding = embeddings.embed_query(question)

    # 2. Neo4j 벡터 인덱스 검색 + 연결된 지식 그래프 조인 (Hybrid Query)
    cypher_query = """
    CALL db.index.vector.queryNodes('chunk_vector_index', $top_k, $query_embedding)
    YIELD node AS chunk, score
    OPTIONAL MATCH (chunk)-[:MENTIONS]->(e:Entity)
    OPTIONAL MATCH (e)-[r]->(target:Entity)
    RETURN chunk.text AS text, score,
           collect(DISTINCT e.id + ' (' + e.type + ')') AS entities,
           collect(DISTINCT e.id + ' -[' + type(r) + ']-> ' + target.id) AS relationships
    """

    results = graph.query(cypher_query, params={"top_k": top_k, "query_embedding": query_embedding})

    if not results:
        print("❌ 관련된 데이터를 찾지 못했어.")
        return "관련 데이터를 찾을 수 없습니다."

    print(f"✅ 상위 {len(results)}개 관련 문단 및 연관 지식 그래프 추출 완료!", flush=True)

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
    # 테스트 질문 목록
    test_questions = [
        "사학기관 재무회계 규칙에서 이월금 처리는 어떻게 해야 해?",
        "예산 편성 시 주의해야 할 기본 원칙은 뭐야?"
    ]

    for q in test_questions:
        answer = hybrid_search_and_answer(q)
        print("💬 [AI 답변]:")
        print(answer)
        print("\n" + "="*60)
