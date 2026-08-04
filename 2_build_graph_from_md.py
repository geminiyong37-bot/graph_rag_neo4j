import os
import sys
import json
import uuid
from typing import Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_neo4j import Neo4jGraph

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# 1. Pydantic 추출 스키마 정의
class KGNode(BaseModel):
    id: str = Field(description="노드 이름/핵심 개체명 (예: 사학기관, 재무회계규칙, 이사회, 예산 등)")
    type: Literal["Organization", "Regulation", "Account", "Person", "Concept", "System", "Unknown"]

class KGRelationship(BaseModel):
    source: str = Field(description="출발 노드 id")
    target: str = Field(description="도착 노드 id")
    kind: str = Field(description="관계 종류 (예: APPLIES_TO, GOVERNS, INCLUDES, RESPONSIBLE_FOR, RELATED_TO 등)")

class KGGraph(BaseModel):
    nodes: list[KGNode]
    relationships: list[KGRelationship]

# 2. 순수 파이썬 초고속 문단 분할 함수 (청크 사이즈: 400자, 오버랩: 50자)
def split_text_into_chunks(text: str, chunk_size: int = 400, chunk_overlap: int = 50) -> list[str]:
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            last_newline = text.rfind("\n", start + chunk_size // 2, end)
            if last_newline != -1:
                end = last_newline + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - chunk_overlap if end < text_len else text_len
    return chunks

# 3. Neo4j 및 LLM / Embedding 초기화
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

structured_llm = llm.with_structured_output(KGGraph)

# 4. 제약 조건 및 벡터 인덱스 생성
graph.query("""
CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (e:Entity)
REQUIRE e.id IS UNIQUE
""")

graph.query("""
CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
FOR (c:Chunk)
REQUIRE c.id IS UNIQUE
""")

graph.query("""
CREATE VECTOR INDEX chunk_vector_index IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}}
""")

def ingest_chunk_text(chunk_text: str, source_name: str = "온라인상담_구글시트", custom_chunk_id: str = None) -> dict:
    if not custom_chunk_id:
        custom_chunk_id = f"chunk_sheet_{uuid.uuid4().hex[:10]}"

    # 1) 원문 Chunk 임베딩 벡터 생성
    chunk_embedding = embeddings.embed_query(chunk_text)

    # 2) Neo4j에 Chunk 노드 및 임베딩 저장
    graph.query(
        """
        MERGE (c:Chunk {id: $id})
        SET c.text = $text,
            c.embedding = $embedding,
            c.file_name = $file_name
        """,
        params={
            "id": custom_chunk_id,
            "text": chunk_text,
            "embedding": chunk_embedding,
            "file_name": source_name
        }
    )

    # 3) LLM 지식 그래프 추출
    prompt = f"""
다음 법률/회계 규칙 또는 Q&A 문서 텍스트에서 주요 개체(노드)와 관계를 추출하세요.

규칙:
- 텍스트에 포함된 내용만 추출하세요.
- 중요한 조항, 규칙, 기관, 계정과목, 개체, 지침 등을 노드로 추출하세요.
- relationship의 source와 target은 반드시 nodes의 id 중 하나여야 합니다.

텍스트:
{chunk_text}
"""
    nodes_created = 0
    rels_created = 0

    try:
        kg = structured_llm.invoke(prompt)
        if kg.nodes:
            nodes = [node.model_dump() for node in kg.nodes]
            relationships = [rel.model_dump() for rel in kg.relationships]
            nodes_created = len(nodes)
            rels_created = len(relationships)

            graph.query(
                """
                UNWIND $nodes AS node
                MERGE (e:Entity {id: node.id})
                SET e.type = node.type
                """,
                params={"nodes": nodes},
            )

            for node in kg.nodes:
                graph.query(
                    f"""
                    MATCH (e:Entity {{id: $id}})
                    SET e:{node.type}
                    """,
                    params={"id": node.id},
                )

            for rel in relationships:
                kind = "".join(c for c in rel["kind"].upper() if c.isalnum() or c == "_") or "RELATED_TO"
                graph.query(
                    f"""
                    MATCH (source:Entity {{id: $source}})
                    MATCH (target:Entity {{id: $target}})
                    MERGE (source)-[r:{kind}]->(target)
                    """,
                    params={"source": rel["source"], "target": rel["target"]},
                )

            entity_ids = [node.id for node in kg.nodes]
            graph.query(
                """
                MATCH (c:Chunk {id: $chunk_id})
                UNWIND $entity_ids AS e_id
                MATCH (e:Entity {id: e_id})
                MERGE (c)-[:MENTIONS]->(e)
                """,
                params={"chunk_id": custom_chunk_id, "entity_ids": entity_ids}
            )
    except Exception as e:
        print(f"[WARN] LLM 추출 실패 (Chunk 노드는 저장됨): {e}")

    return {
        "chunk_id": custom_chunk_id,
        "nodes_created": nodes_created,
        "relationships_created": rels_created
    }

def process_md_file(file_path: str):
    if not os.path.exists(file_path):
        print(f"[ERROR] 파일을 찾을 수 없어: {file_path}")
        return

    print(f"[READ] '{file_path}' 파일 읽는 중...", flush=True)
    with open(file_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    chunks = split_text_into_chunks(md_text, chunk_size=400, chunk_overlap=50)
    print(f"[SET] 청크 사이즈: 400자 / 오버랩: 50자 설정 완료!", flush=True)
    print(f"[START] 전체 문서를 {len(chunks)}개 청크로 쪼갰어. [청크 노드 + 벡터 임베딩 + 지식 그래프] 변환 시작!\n", flush=True)

    progress_file = "progress_checkpoint.json"
    completed_chunks = 0
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r") as pf:
                completed_chunks = json.load(pf).get("last_completed_chunk", 0)
            if completed_chunks > 0:
                print(f"[RESUME] 이전 기록 발견! {completed_chunks}번째 청크까지 저장되어 있어 이어서 시작해!", flush=True)
        except Exception:
            completed_chunks = 0

    for i, chunk in enumerate(chunks, 1):
        if i <= completed_chunks:
            continue

        print(f"[{i} / {len(chunks)}] 청크 벡터화 & 지식 추출 중...", flush=True)
        chunk_id = f"chunk_{i}"

        # 1) 원문 Chunk 임베딩 벡터 생성
        try:
            chunk_embedding = embeddings.embed_query(chunk)
        except Exception as embed_err:
            print(f"  [WARN] 임베딩 생성 오류 (건너뜀): {embed_err}", flush=True)
            continue

        # 2) Neo4j에 Chunk 노드 및 임베딩 저장 (파일명 메타데이터 추가)
        graph.query(
            """
            MERGE (c:Chunk {id: $id})
            SET c.text = $text,
                c.embedding = $embedding,
                c.file_name = $file_name
            """,
            params={
                "id": chunk_id,
                "text": chunk,
                "embedding": chunk_embedding,
                "file_name": os.path.basename(file_path)
            }
        )

        # 3) LLM 지식 그래프 추출
        prompt = f"""
다음 법률/회계 규칙 문서 텍스트에서 주요 개체(노드)와 관계를 추출하세요.

규칙:
- 텍스트에 포함된 내용만 추출하세요.
- 중요한 조항, 규칙, 기관, 계정과목, 개체, 지침 등을 노드로 추출하세요.
- relationship의 source와 target은 반드시 nodes의 id 중 하나여야 합니다.

텍스트:
{chunk}
"""
        try:
            kg = structured_llm.invoke(prompt)
            if kg.nodes:
                nodes = [node.model_dump() for node in kg.nodes]
                relationships = [rel.model_dump() for rel in kg.relationships]

                # Entity 노드 저장
                graph.query(
                    """
                    UNWIND $nodes AS node
                    MERGE (e:Entity {id: node.id})
                    SET e.type = node.type
                    """,
                    params={"nodes": nodes},
                )

                for node in kg.nodes:
                    graph.query(
                        f"""
                        MATCH (e:Entity {{id: $id}})
                        SET e:{node.type}
                        """,
                        params={"id": node.id},
                    )

                # Relationship 저장
                for rel in relationships:
                    kind = "".join(c for c in rel["kind"].upper() if c.isalnum() or c == "_") or "RELATED_TO"
                    graph.query(
                        f"""
                        MATCH (source:Entity {{id: $source}})
                        MATCH (target:Entity {{id: $target}})
                        MERGE (source)-[r:{kind}]->(target)
                        """,
                        params={"source": rel["source"], "target": rel["target"]},
                    )

                # 4) Chunk 노드 -> Entity 노드간 (:MENTIONS) 관계 연결
                entity_ids = [node.id for node in kg.nodes]
                graph.query(
                    """
                    MATCH (c:Chunk {id: $chunk_id})
                    UNWIND $entity_ids AS e_id
                    MATCH (e:Entity {id: e_id})
                    MERGE (c)-[:MENTIONS]->(e)
                    """,
                    params={"chunk_id": chunk_id, "entity_ids": entity_ids}
                )

                print(f"  └ Chunk 노드 생성 & 임베딩 + 노드 {len(nodes)}개, 관계 {len(relationships)}개 연결 완료!", flush=True)
            else:
                print(f"  └ Chunk 노드 생성 & 임베딩 완료! (개념 노드 없음)", flush=True)

            # 진행 상황 저장
            with open(progress_file, "w") as pf:
                json.dump({"last_completed_chunk": i}, pf)

        except Exception as e:
            print(f"  [WARN] 청크 {i} 처리 중 건너뜀: {e}", flush=True)

    print("\n[COMPLETE] 모든 청크 벡터화 & 지식 그래프 구축 완료! Neo4j 클라우드에서 확인해 봐!", flush=True)


if __name__ == "__main__":
    target_md_file = None
    search_dirs = ["data", "."]

    for s_dir in search_dirs:
        if os.path.exists(s_dir):
            for f in os.listdir(s_dir):
                if f.endswith(".md") and f.lower() != "readme.md":
                    target_md_file = os.path.join(s_dir, f)
                    break
        if target_md_file:
            break

    if target_md_file:
        print(f"[FOUND] '{target_md_file}' 파일을 찾았어!")
        process_md_file(target_md_file)
    else:
        print("[ERROR] data 폴더나 현재 폴더에서 .md 파일을 찾지 못했어.")
