import os
import sys
import json
import uuid
import re
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
            g.query("""
            CREATE FULLTEXT INDEX chunk_fulltext_index IF NOT EXISTS
            FOR (c:Chunk) ON EACH [c.text]
            """)
            _schema_initialized = True
        except Exception as e:
            print(f"[WARN] Schema initialization warning: {e}", flush=True)

def extract_year_from_filename(filename: str) -> int:
    match = re.search(r'\[(\d{4})년\]|(\d{4})년|(\d{4})회계연도', filename)
    if match:
        for group in match.groups():
            if group:
                return int(group)
    return 2024  # 기본값

def auto_structure_headers(text: str) -> str:
    """
    일반 번호(제N장, 제N조, 1., 가., Q1. 등) 텍스트를 마크다운 ##, ### 헤더 계층 구조로 자동 변환하는 파서
    """
    lines = text.split("\n")
    new_lines = []
    
    # 1) 대분류 (Parent ##) 패턴: 제N장, 제N절, 제N관, [사례 N], 1. 제목 등
    parent_pattern = re.compile(r'^(제\s*\d+\s*[장절관]|\[\s*사례\s*\d+\s*\]|\d+\.\s+[가-힣A-Za-z0-9])')
    
    # 2) 소분류 (Child ###) 패턴: 제N조, (1), 가., Q1., 질문 1. 등
    child_pattern = re.compile(r'^(제\s*\d+\s*조|\(\d+\)|[가-하]\.|\bQ\d+[\.\:\s]|\b질문\s*\d+[\.\:\s]|【\s*질문\s*】)')
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            new_lines.append(line)
        elif parent_pattern.match(stripped):
            new_lines.append(f"## {stripped}")
        elif child_pattern.match(stripped):
            new_lines.append(f"### {stripped}")
        else:
            new_lines.append(line)
            
    return "\n".join(new_lines)

def preprocess_text(text: str) -> str:
    # 1. <br>, <br/>, <br  /> 태그를 띄어쓰기로 치환
    clean = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
    # 2. 일반 번호/조항 표기를 마크다운 ##, ### 계층 구조로 자동 승격
    structured = auto_structure_headers(clean)
    return structured

def generate_table_summary(table_text: str) -> str:
    lines = [l.strip() for l in table_text.strip().split("\n") if l.strip()]
    if not lines:
        return "표 데이터"
    headers = [col.strip() for col in lines[0].split("|") if col.strip()]
    header_str = ", ".join(headers[:5]) if headers else "기본 항목"
    return f"표 항목 ({header_str})"

def parse_markdown_into_parent_child_chunks(md_text: str) -> list[dict]:
    """
    마크다운 해설서/지침서 고도화 청킹 파이프라인
    - ## 장/절 기준 Parent Chunk (1,000~1,500자)
    - ### 조항/세부항목 및 문단 기준 Child Chunk (300~800자)
    - 표(Table) 구획 자동 인식 및 덩어리 보존 + AI 요약 간판
    """
    cleaned_text = preprocess_text(md_text)
    lines = cleaned_text.split("\n")

    parent_child_blocks = []
    current_parent_title = "개요 및 기본지침"
    current_child_title = "기본항목"
    
    child_buf = []
    current_text_lines = []

    def flush_current_text():
        nonlocal current_text_lines
        if not current_text_lines:
            return
        text_content = "\n".join(current_text_lines).strip()
        current_text_lines = []
        if not text_content:
            return
        
        header_breadcrumb = f"[족보: {current_parent_title} ➔ {current_child_title}]"
        full_child_text = f"{header_breadcrumb}\n{text_content}"
        child_buf.append(full_child_text)

    def flush_table(table_lines):
        if not table_lines:
            return
        c_text = "\n".join(table_lines).strip()
        if not c_text:
            return

        summary = generate_table_summary(c_text)
        c_text = f"[표 요약설명: {summary}]\n\n{c_text}"

        header_breadcrumb = f"[족보: {current_parent_title} ➔ {current_child_title}]"
        full_child_text = f"{header_breadcrumb}\n{c_text}"
        child_buf.append(full_child_text)

    def flush_parent():
        nonlocal child_buf, current_parent_title
        flush_current_text()
        if child_buf:
            parent_text = "\n\n".join(child_buf)
            parent_child_blocks.append({
                "parent_title": current_parent_title,
                "parent_text": parent_text,
                "children": child_buf[:]
            })
            child_buf = []

    in_table = False
    table_lines = []

    for line in lines:
        stripped = line.strip()
        
        # 표 구획 시작/종료 체크
        if stripped.startswith("|") and "|" in stripped[1:]:
            if not in_table:
                flush_current_text()
                in_table = True
            table_lines.append(line)
            continue
        elif in_table:
            in_table = False
            flush_table(table_lines)
            table_lines = []

        # 헤더 체크
        if stripped.startswith("## "):
            flush_parent()
            current_parent_title = stripped.replace("##", "").strip()
            current_child_title = current_parent_title
            continue
        elif stripped.startswith("### "):
            flush_current_text()
            current_child_title = stripped.replace("###", "").strip()
            continue

        current_text_lines.append(line)
        current_len = sum(len(l) for l in current_text_lines)
        if current_len >= 800:
            flush_current_text()

    if in_table and table_lines:
        flush_table(table_lines)
    flush_parent()

    # 헤더 구획이 전무한 문서인 경우 400자 분할 기본 처리
    if not parent_child_blocks:
        raw_chunks = []
        start = 0
        text_len = len(cleaned_text)
        chunk_size = 500
        chunk_overlap = 50
        while start < text_len:
            end = min(start + chunk_size, text_len)
            if end < text_len:
                last_newline = cleaned_text.rfind("\n", start + chunk_size // 2, end)
                if last_newline != -1:
                    end = last_newline + 1
            chunk = cleaned_text[start:end].strip()
            if chunk:
                raw_chunks.append(chunk)
            start = end - chunk_overlap if end < text_len else text_len

        parent_child_blocks.append({
            "parent_title": "일반 문서",
            "parent_text": cleaned_text,
            "children": raw_chunks
        })

    return parent_child_blocks

def process_md_file(file_path: str) -> int:
    if not os.path.exists(file_path):
        print(f"[ERROR] 파일을 찾을 수 없어: {file_path}")
        return 0

    g = get_graph()
    _, embeddings, structured_llm = get_models()
    ensure_schema()

    filename = os.path.basename(file_path)
    file_year = extract_year_from_filename(filename)
    safe_file_tag = re.sub(r'[^a-zA-Z0-9가-힣]', '_', filename)

    print(f"\n📄 [READ] '{filename}' 파일 고도화 업로드 시작 (연도: {file_year}년)...", flush=True)
    with open(file_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    blocks = parse_markdown_into_parent_child_chunks(md_text)
    
    total_children = 0
    p_idx = 0
    for block in blocks:
        p_idx += 1
        parent_id = f"{safe_file_tag}_parent_{p_idx}"
        parent_text = block["parent_text"]

        # 1) Neo4j에 Parent Chunk 저장 (is_parent = true, 임베딩 없이 텍스트 보관)
        g.query("""
        MERGE (p:Chunk {id: $id})
        SET p.text = $text,
            p.is_parent = true,
            p.file_name = $file_name,
            p.year = $year,
            p.title = $title
        """, params={
            "id": parent_id,
            "text": parent_text,
            "file_name": filename,
            "year": file_year,
            "title": block["parent_title"]
        })

        c_idx = 0
        total_in_block = len(block["children"])
        for child_text in block["children"]:
            c_idx += 1
            total_children += 1
            child_id = f"{parent_id}_child_{c_idx}"
            print(f"    🧩 [{c_idx}/{total_in_block}] 자식 청크 저장 및 지식 추출 중...", flush=True)

            # 2) Child Chunk 임베딩 생성 & 저장 (is_parent = false, c.embedding 저장)
            try:
                c_embed = embeddings.embed_query(child_text)
            except Exception as e:
                print(f"    [WARN] 임베딩 생성 오류 (건너뜀): {e}", flush=True)
                continue

            g.query("""
            MERGE (c:Chunk {id: $id})
            SET c.text = $text,
                c.embedding = $embedding,
                c.is_parent = false,
                c.file_name = $file_name,
                c.year = $year
            WITH c
            MATCH (p:Chunk {id: $parent_id})
            MERGE (c)-[:HAS_PARENT]->(p)
            """, params={
                "id": child_id,
                "text": child_text,
                "embedding": c_embed,
                "file_name": filename,
                "year": file_year,
                "parent_id": parent_id
            })

            # 3) LLM 지식 그래프 (Entity/Relation) 추출
            prompt = f"""다음 사학기관 재무·회계 규칙, 법령 또는 대학 Q&A 텍스트에서 주요 개체(노드)와 개체 간의 구조적 연관 관계를 추출하세요.

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

텍스트:
{child_text}
"""
            try:
                kg = structured_llm.invoke(prompt)
                if kg.nodes:
                    nodes = [node.model_dump() for node in kg.nodes]
                    relationships = [rel.model_dump() for rel in kg.relationships]

                    g.query("""
                    UNWIND $nodes AS node
                    MERGE (e:Entity {id: node.id})
                    SET e.type = node.type
                    """, params={"nodes": nodes})

                    nodes_by_type = {}
                    for node in kg.nodes:
                        safe_label = "".join(c for c in node.type if c.isalnum()) or "Entity"
                        nodes_by_type.setdefault(safe_label, []).append(node.id)

                    for label_name, id_list in nodes_by_type.items():
                        g.query(f"""
                        UNWIND $ids AS id
                        MATCH (e:Entity {{id: id}})
                        SET e:{label_name}
                        """, params={"ids": id_list})

                    rels_by_kind = {}
                    for rel in relationships:
                        kind = "".join(c for c in rel["kind"].upper() if c.isalnum() or c == "_") or "RELATED_TO"
                        rels_by_kind.setdefault(kind, []).append({"source": rel["source"], "target": rel["target"]})

                    for kind_name, rel_list in rels_by_kind.items():
                        g.query(f"""
                        UNWIND $rels AS rel
                        MATCH (source:Entity {{id: rel.source}})
                        MATCH (target:Entity {{id: rel.target}})
                        MERGE (source)-[r:{kind_name}]->(target)
                        """, params={"rels": rel_list})

                    entity_ids = [node.id for node in kg.nodes]
                    g.query("""
                    MATCH (c:Chunk {id: $chunk_id})
                    UNWIND $entity_ids AS e_id
                    MATCH (e:Entity {id: e_id})
                    MERGE (c)-[:MENTIONS]->(e)
                    """, params={"chunk_id": child_id, "entity_ids": entity_ids})
            except Exception as e:
                print(f"    [WARN] 청크 지식 추출/저장 예외 발생: {e}", flush=True)

    print(f"✅ '{filename}' 고도화 임베딩 및 지식 그래프 생성 완료! (Parent: {len(blocks)}개 / Child: {total_children}개)", flush=True)
    return total_children

def process_all_md_files(data_dir: str = "data"):
    if not os.path.exists(data_dir):
        print(f"[ERROR] '{data_dir}' 폴더가 존재하지 않습니다.")
        return

    md_files = [f for f in os.listdir(data_dir) if f.endswith(".md") and f.lower() != "readme.md"]
    md_files.sort()

    print(f"🚀 [BATCH] 총 {len(md_files)}개 고도화 마크다운 문서 업로드 시작!", flush=True)

    progress_file = "progress_checkpoint.json"
    completed_files = []
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as pf:
                cp_data = json.load(pf)
                completed_files = cp_data.get("completed_files", [])
        except Exception:
            completed_files = []

    total_files = len(md_files)
    for idx, f_name in enumerate(md_files, 1):
        if f_name in completed_files:
            print(f"⏩ [{idx}/{total_files}] '{f_name}' 이미 완료됨 (건너뜀)", flush=True)
            continue

        file_path = os.path.join(data_dir, f_name)
        print(f"\n==========================================")
        print(f"📌 [{idx}/{total_files}] 파일 처리 중: {f_name}")
        print(f"==========================================")
        process_md_file(file_path)

        completed_files.append(f_name)
        with open(progress_file, "w", encoding="utf-8") as pf:
            json.dump({"completed_files": completed_files}, pf, ensure_ascii=False)

    print("\n🎉 [COMPLETE] 59개 전체 파일 고도화 지식 그래프 업로드 완료!", flush=True)

if __name__ == "__main__":
    data_folder = "data"
    if os.path.exists(data_folder):
        process_all_md_files(data_folder)
    else:
        print("[ERROR] data 폴더를 찾지 못했어.")
