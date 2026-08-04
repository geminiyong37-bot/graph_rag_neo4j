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

# 1. 사학회계/대학Q&A 특화 정밀 Pydantic 스키마 정의
class KGNode(BaseModel):
    id: str = Field(description="노드 이름/핵심 개체명 (예: 사학기관, 교비회계, 이사회, 제14조, 수입조정, 예비비 등)")
    type: Literal["Organization", "Regulation", "Account", "Procedure", "Exception", "Concept", "Person", "System", "Unknown"]

class KGRelationship(BaseModel):
    source: str = Field(description="출발 노드 id")
    target: str = Field(description="도착 노드 id")
    kind: str = Field(description="관계 종류 (예: GOVERNS, APPLIES_TO, INCLUDES, EXCEPT_FOR, REQUIRES_PROCEDURE, RELATED_TO 등)")

class KGGraph(BaseModel):
    nodes: list[KGNode]
    relationships: list[KGRelationship]

# Lazy Global Objects
_graph = None
_llm = None
_embeddings = None
_structured_llm = None
_schema_initialized = False

def get_graph():
    global _graph
    if _graph is None:
        neo4j_uri = os.getenv("NEO4J_URI")
        neo4j_user = os.getenv("NEO4J_USERNAME")
        neo4j_pass = os.getenv("NEO4J_PASSWORD")
        neo4j_db = os.getenv("NEO4J_DATABASE")
        _graph = Neo4jGraph(
            url=neo4j_uri,
            username=neo4j_user,
            password=neo4j_pass,
            database=neo4j_db,
        )
    return _graph

def get_models():
    global _llm, _embeddings, _structured_llm
    if _llm is None:
        model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        _llm = ChatOpenAI(model=model_name, temperature=0)
        _embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        _structured_llm = _llm.with_structured_output(KGGraph)
    return _llm, _embeddings, _structured_llm

def ensure_schema():
    global _schema_initialized
    if not _schema_initialized:
        g = get_graph()
        try:
            g.query("""
            CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
            FOR (e:Entity)
            REQUIRE e.id IS UNIQUE
            """)
            g.query("""
            CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
            FOR (c:Chunk)
            REQUIRE c.id IS UNIQUE
            """)
            g.query("""
            CREATE VECTOR INDEX chunk_vector_index IF NOT EXISTS
            FOR (c:Chunk) ON (c.embedding)
            OPTIONS { indexConfig: {
              `vector.dimensions`: 1536,
              `vector.similarity_function`: 'cosine'
            }}
            """)
            _schema_initialized = True
        except Exception as e:
            print(f"[WARN] Schema initialization warning: {e}", flush=True)

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

def ingest_chunk_text(chunk_text: str, source_name: str = "온라인상담_구글시트", custom_chunk_id: str = None) -> dict:
    if not custom_chunk_id:
        custom_chunk_id = f"chunk_sheet_{uuid.uuid4().hex[:10]}"

    g = get_graph()
    _, embeddings, structured_llm = get_models()
    ensure_schema()

    # 1) 원문 Chunk 임베딩 벡터 생성
    chunk_embedding = embeddings.embed_query(chunk_text)

    # 2) Neo4j에 Chunk 노드 및 임베딩 저장
    g.query(
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

    # 3) LLM 고도화 지식 그래프 추출
    prompt = f"""
다음 사학기관 재무·회계 규칙, 법령 또는 대학 Q&A 텍스트에서 주요 개체(노드)와 개체 간의 구조적 연관 관계를 추출하세요.

[추출 대상 노드 유형 (type)]:
- Organization: 대학, 사학법인, 이사회, 교육부, 주무관청 등
- Regulation: 재무·회계 규칙, 사립학교법, 정관, 세부 지침, 관련 조항 (예: 제14조, 제21조)
- Account: 수입/지출 계정과목, 예산/결산 항목 (예: 등록금수입, 교비회계, 법인회계, 예비비 등)
- Procedure: 결재, 편성, 집행, 변경, 이월, 승인 절차 (예: 이사회 의결, 주무관청 보고)
- Exception: 예외 규정, 금지 사항, 단서 조항
- Concept: 핵심 개념 및 회계/행정 용어
- Person: 이사장, 총장, 회계책임자 등

[추출 대상 관계 종류 (kind)]:
- GOVERNS (관할/규정함)
- APPLIES_TO (적용됨)
- INCLUDES (포함함/상위계정)
- EXCEPT_FOR (예외사항)
- REQUIRES_PROCEDURE (절차필요)
- RELATED_TO (연관됨)

규칙:
- 텍스트에 실질적으로 명시되거나 추론 가능한 명확한 관계만 추출하세요.
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

            g.query(
                """
                UNWIND $nodes AS node
                MERGE (e:Entity {id: node.id})
                SET e.type = node.type
                """,
                params={"nodes": nodes},
            )

            for node in kg.nodes:
                g.query(
                    f"""
                    MATCH (e:Entity {{id: $id}})
                    SET e:{node.type}
                    """,
                    params={"id": node.id},
                )

            for rel in relationships:
                kind = "".join(c for c in rel["kind"].upper() if c.isalnum() or c == "_") or "RELATED_TO"
                g.query(
                    f"""
                    MATCH (source:Entity {{id: $source}})
                    MATCH (target:Entity {{id: $target}})
                    MERGE (source)-[r:{kind}]->(target)
                    """,
                    params={"source": rel["source"], "target": rel["target"]},
                )

            entity_ids = [node.id for node in kg.nodes]
            g.query(
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

    g = get_graph()
    _, embeddings, structured_llm = get_models()
    ensure_schema()

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
        g.query(
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
다음 사학기관 재무·회계 규칙, 법령 또는 대학 Q&A 텍스트에서 주요 개체(노드)와 개체 간의 구조적 연관 관계를 추출하세요.

[추출 대상 노드 유형 (type)]:
- Organization: 대학, 사학법인, 이사회, 교육부, 주무관청 등
- Regulation: 재무·회계 규칙, 사립학교법, 정관, 세부 지침, 관련 조항 (예: 제14조, 제21조)
- Account: 수입/지출 계정과목, 예산/결산 항목 (예: 등록금수입, 교비회계, 법인회계, 예비비 등)
- Procedure: 결재, 편성, 집행, 변경, 이월, 승인 절차 (예: 이사회 의결, 주무관청 보고)
- Exception: 예외 규정, 금지 사항, 단서 조항
- Concept: 핵심 개념 및 회계/행정 용어
- Person: 이사장, 총장, 회계책임자 등

[추출 대상 관계 종류 (kind)]:
- GOVERNS (관할/규정함)
- APPLIES_TO (적용됨)
- INCLUDES (포함함/상위계정)
- EXCEPT_FOR (예외사항)
- REQUIRES_PROCEDURE (절차필요)
- RELATED_TO (연관됨)

규칙:
- 텍스트에 실질적으로 명시되거나 추론 가능한 명확한 관계만 추출하세요.
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
                g.query(
                    """
                    UNWIND $nodes AS node
                    MERGE (e:Entity {id: node.id})
                    SET e.type = node.type
                    """,
                    params={"nodes": nodes},
                )

                for node in kg.nodes:
                    g.query(
                        f"""
                        MATCH (e:Entity {{id: $id}})
                        SET e:{node.type}
                        """,
                        params={"id": node.id},
                    )

                # Relationship 저장
                for rel in relationships:
                    kind = "".join(c for c in rel["kind"].upper() if c.isalnum() or c == "_") or "RELATED_TO"
                    g.query(
                        f"""
                        MATCH (source:Entity {{id: $source}})
                        MATCH (target:Entity {{id: $target}})
                        MERGE (source)-[r:{kind}]->(target)
                        """,
                        params={"source": rel["source"], "target": rel["target"]},
                    )

                # 4) Chunk 노드 -> Entity 노드간 (:MENTIONS) 관계 연결
                entity_ids = [node.id for node in kg.nodes]
                g.query(
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
