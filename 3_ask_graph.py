import os
import sys
import re
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

llm_fast = ChatOpenAI(model="gpt-4o-mini", temperature=0)

STOPWORDS = {
    "어떻게", "처리", "방법", "알려줘", "문의", "궁금합니다", "관하여", "대해",
    "무엇인가요", "무엇", "등", "및", "위한", "따른", "관한", "있나요", "하나요",
    "인가요", "경우", "관련", "질문", "답변", "규정", "내용", "설명"
}


def run_query(cypher: str, params: dict = None):
    """순수 neo4j 드라이버로 Cypher 쿼리 실행"""
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, params or {})
        return [record.data() for record in result]


def clean_korean_word(word: str) -> str:
    """한국어 조사 및 어미 제거"""
    suffixes = [
        "에서부터", "에서", "에대한", "에게는", "에게", "에서는", "의", "는", "은", "를", "을",
        "으로", "로", "와", "과", "에", "도", "인가요", "하나요", "하며", "하고", "해서", "입니다",
        "이라는", "라는", "에서는", "으로만"
    ]
    for s in suffixes:
        if word.endswith(s) and len(word) >= len(s) + 2:
            return word[:-len(s)]
    return word

def extract_search_keywords(question: str) -> str:
    """AI(gpt-4o-mini)를 활용하여 질의의 문맥과 뉘앙스를 종합 판단, BM25 키워드 검색에 최적화된 핵심 단어 5~10개를 동적으로 추출"""
    try:
        prompt = f"""당신은 검색엔진(BM25/Lucene)용 키워드 추출 전문가입니다.
사용자의 질의에서 지식 검색에 가장 결정적인 핵심 키워드(5개~10개)를 추출하세요.
질문의 길이가 길더라도 전체 뉘앙스와 의도를 파악하여 핵심 대상, 행위, 지침/규정 용어, 계정과목, 문제 상황을 나타내는 단어만 추출하세요.
불필요한 인사말, 문맥 수식어('문의드립니다', '경우', '관하여', '원칙적으로' 등)는 완전히 제외하세요.

[질문]: {question}

[출력 형식]: 키워드1 키워드2 키워드3 (단어만 공백으로 구분하여 출력, 부연설명 절대 금지)"""
        res = llm_fast.invoke(prompt)
        kw_str = res.content.strip()
        tokens = [clean_korean_word(w) for w in re.sub(r"[^\w\s]", " ", kw_str).split() if len(w) > 1]
        unique_tokens = list(dict.fromkeys(tokens))
        if unique_tokens:
            extracted_query = " OR ".join(unique_tokens[:10])
            print(f"🤖 [AI 키워드 추출기] 동적 추출 키워드: '{extracted_query}'", flush=True)
            return extracted_query
    except Exception as e:
        print(f"  [WARN] AI 키워드 추출 예외 발생 (기존 규칙 기반 추출로 대체): {e}", flush=True)

    # Fallback to rule-based tokenization if LLM call fails
    raw_tokens = re.sub(r"[^\w\s]", " ", question).split()
    clean_tokens = [clean_korean_word(w) for w in raw_tokens if len(w) > 1 and w not in STOPWORDS]
    unique_tokens = list(dict.fromkeys(clean_tokens))
    return " OR ".join(unique_tokens[:10])


PRIORITY_FILES_SCHOOL_FOUNDATION = [
    "[최우선]",
    "사학기관_재무회계_규칙에_관한_특례규칙_해설서",
    "사립대학(법인) 회계관리 안내서 개정본",
    "사립대학(법인) 기본재산 관리 안내서",
    "사학기관+회계기준+실무사례와+해설"
]

PRIORITY_FILES_LOW = [
    "[참고용]",
    "대학_ESG_가이드라인_배포용"
]


def build_rerank_passage(row: dict) -> str:
    """Cohere가 검색된 자식 근거 자체를 평가하도록 짧고 명확한 문서를 구성한다."""
    return f"""출처 파일: {row.get('file_name', '출처 미상')}
상위 주제: {row.get('parent_title', '주제 미상')}
직접 검색된 근거:
{row.get('child_text', '')}"""


def group_chunks_by_parent(rows: list[dict]) -> tuple[dict, list[str]]:
    """선택된 자식 근거를 보존하면서 동일한 부모 본문을 한 번만 묶는다."""
    parent_map = {}
    ordered_parents = []

    for row in rows:
        p_text = row.get("parent_text") or row.get("child_text", "")
        if p_text not in parent_map:
            parent_map[p_text] = {
                "parent_text": p_text,
                "parent_title": row.get("parent_title", "주제 미상"),
                "file_name": row.get("file_name", "출처 미상"),
                "year": row.get("year", 2024),
                "rerank_score": row.get("rerank_score", 0),
                "child_evidence": [],
                "entities": set(),
                "relations": set(),
            }
            ordered_parents.append(p_text)

        child_text = row.get("child_text", "").strip()
        if child_text and child_text not in parent_map[p_text]["child_evidence"]:
            parent_map[p_text]["child_evidence"].append(child_text)

        for entity in row.get("direct_entities", []):
            if isinstance(entity, dict) and entity.get("id"):
                parent_map[p_text]["entities"].add(
                    f"{entity['id']} ({entity.get('type', '개체')})"
                )

        for relation in row.get("hop1_relations", []):
            if (
                isinstance(relation, dict)
                and relation.get("source")
                and relation.get("target")
                and relation.get("rel")
            ):
                parent_map[p_text]["relations"].add(
                    f"{relation['source']} --[{relation['rel']}]--> {relation['target']}"
                )

    return parent_map, ordered_parents


def hybrid_search_and_answer(question: str, top_k: int = 5, category: str = "") -> str:
    print(f"\n❓ 질문: {question} (구분: {category or '기본'})", flush=True)
    print("🔍 [1단계: 고도화된 Neo4j 하이브리드 검색 - BM25 키워드 + 벡터 + 1-Hop 정밀 지식그래프]", flush=True)

    # 교비회계 / 법인회계 여부 감지
    is_school_or_foundation = False
    combined_query_str = f"{category} {question}"
    if any(kw in combined_query_str for kw in ["교비", "법인"]):
        is_school_or_foundation = True
        print("🏛️ [우선순위 인식] '교비회계/법인회계' 관련 질의 감지! [최우선] 3대 핵심 지침 문서 가중치 부여 시작...", flush=True)

    # 1. 질문을 임베딩 벡터로 변환
    query_embedding = embeddings.embed_query(question)
    initial_k = max(top_k * 4, 20)

    # --- [검색 1] Child Chunk 기반 벡터 유사도 검색 + Parent 해설 및 1-Hop 지식 그래프 ---
    vector_results = run_query("""
        CALL db.index.vector.queryNodes('chunk_vector_index', $initial_k, $query_embedding)
        YIELD node AS child, score
        OPTIONAL MATCH (child)-[:HAS_PARENT]->(parent:Chunk)
        OPTIONAL MATCH (child)-[:MENTIONS]->(e1:Entity)
        OPTIONAL MATCH (e1)-[r1]->(e2:Entity)
        RETURN child.id AS child_id,
               child.text AS child_text,
               coalesce(parent.text, child.text) AS parent_text,
               coalesce(parent.title, child.title, '주제 미상') AS parent_title,
               coalesce(child.file_name, '출처 미상') AS file_name,
               coalesce(child.year, 2024) AS year,
               score AS vector_score,
               collect(DISTINCT {id: e1.id, type: coalesce(e1.type, '개체')}) AS direct_entities,
               collect(DISTINCT {source: e1.id, rel: type(r1), target: e2.id}) AS hop1_relations
    """, {"initial_k": initial_k, "query_embedding": query_embedding})

    # --- [검색 2] BM25 기반 FULLTEXT 키워드 검색 ---
    lucene_keywords = extract_search_keywords(question)
    fulltext_results = []
    if lucene_keywords:
        try:
            fulltext_results = run_query("""
                CALL db.index.fulltext.queryNodes('chunk_fulltext_index', $keywords, {limit: $initial_k})
                YIELD node AS child, score
                OPTIONAL MATCH (child)-[:HAS_PARENT]->(parent:Chunk)
                OPTIONAL MATCH (child)-[:MENTIONS]->(e1:Entity)
                OPTIONAL MATCH (e1)-[r1]->(e2:Entity)
                RETURN child.id AS child_id,
                       child.text AS child_text,
                       coalesce(parent.text, child.text) AS parent_text,
                       coalesce(parent.title, child.title, '주제 미상') AS parent_title,
                       coalesce(child.file_name, '출처 미상') AS file_name,
                       coalesce(child.year, 2024) AS year,
                       score AS fulltext_score,
                       collect(DISTINCT {id: e1.id, type: coalesce(e1.type, '개체')}) AS direct_entities,
                       collect(DISTINCT {source: e1.id, rel: type(r1), target: e2.id}) AS hop1_relations
            """, {"keywords": lucene_keywords, "initial_k": initial_k})
        except Exception as ft_err:
            print(f"  [WARN] 키워드 검색 오류 (벡터 검색만 사용): {ft_err}", flush=True)

    print(f"✅ 벡터 검색: {len(vector_results)}개 / BM25 키워드 검색(쿼리: '{lucene_keywords}'): {len(fulltext_results)}개 후보 추출 완료!", flush=True)

    # --- [RRF 스코어링 & 계층별 가중치/감점 부여] 벡터 + BM25 키워드 결과 통합 ---
    RRF_K = 60
    chunk_scores = {}

    def process_rows(rows, is_vector=True):
        for rank, row in enumerate(rows, 1):
            cid = row.get("child_id")
            if not cid:
                continue
            fname = row.get("file_name", "출처 미상")
            
            # 기본 RRF 점수
            added_rrf = 1.0 / (RRF_K + rank)

            # 1. 교비/법인 회계 질문 시 [최우선] 3대 핵심 문서 가중치(Boost 1.8x) 적용
            if is_school_or_foundation and any(pf in fname for pf in PRIORITY_FILES_SCHOOL_FOUNDATION):
                added_rrf *= 1.8

            # 2. [참고용] 하위순위 문서 감점(Penalty 0.5x) 적용
            if any(lf in fname for lf in PRIORITY_FILES_LOW):
                added_rrf *= 0.5

            if cid not in chunk_scores:
                chunk_scores[cid] = {
                    "rrf_score": 0.0,
                    "child_id": cid,
                    "child_text": row.get("child_text", ""),
                    "parent_text": row.get("parent_text", ""),
                    "parent_title": row.get("parent_title", "주제 미상"),
                    "file_name": fname,
                    "year": row.get("year", 2024),
                    "vector_score": row.get("vector_score", 0) if is_vector else 0,
                    "direct_entities": row.get("direct_entities", []),
                    "hop1_relations": row.get("hop1_relations", []),
                }
            chunk_scores[cid]["rrf_score"] += added_rrf

    process_rows(vector_results, is_vector=True)
    process_rows(fulltext_results, is_vector=False)

    sorted_chunks = sorted(chunk_scores.values(), key=lambda x: x["rrf_score"], reverse=True)[:max(top_k * 2, 10)]

    if not sorted_chunks:
        print("❌ 관련된 데이터를 찾지 못했어.", flush=True)
        return "관련 데이터를 찾을 수 없습니다."

    print(f"🏆 RRF 1차 통합 후보 {len(sorted_chunks)}개 추출 완료!", flush=True)

    # --- [2단계: Cohere Reranker 정밀 재정렬 (부모 제목 + 자식 근거로 정밀 평가)] ---
    if HAS_COHERE and len(sorted_chunks) > 1:
        print("🎯 [2단계: Cohere Reranker v3.0] 부모 제목 + 자식 근거 기반 다국어 정밀 심사 중...", flush=True)
        passages = [build_rerank_passage(row) for row in sorted_chunks]
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
                
                # 교비/법인회계 시 [최우선] Rerank 점수 가중치 (1.25x)
                if is_school_or_foundation and any(pf in orig_item["file_name"] for pf in PRIORITY_FILES_SCHOOL_FOUNDATION):
                    orig_item["rerank_score"] *= 1.25
                
                # [참고용] 하위순위 문서 Rerank 점수 감점 (0.7x)
                if any(lf in orig_item["file_name"] for lf in PRIORITY_FILES_LOW):
                    orig_item["rerank_score"] *= 0.7
                
                final_chunks.append(orig_item)
            
            # 최종 점수 재정렬
            sorted_chunks = sorted(final_chunks, key=lambda x: x["rerank_score"], reverse=True)
            print(f"🏆 Cohere Rerank 정밀 심사 완료! 최종 상위 {len(sorted_chunks)}개 선택!", flush=True)
        except Exception as c_err:
            print(f"  [WARN] Cohere Rerank 예외 발생 (RRF 결과 사용): {c_err}", flush=True)
            sorted_chunks = sorted_chunks[:top_k]
    else:
        sorted_chunks = sorted_chunks[:top_k]

    # --- [3단계: 직접 근거 보존 + 부모 본문 중복 병합 + 참고 문단 후순위 배치] ---
    parent_map, ordered_parents = group_chunks_by_parent(sorted_chunks)

    # 하위순위([참고용]) 문단은 프롬프트 문맥 맨 뒤로 배치
    normal_parents = [p for p in ordered_parents if not any(lf in parent_map[p]["file_name"] for lf in PRIORITY_FILES_LOW)]
    low_priority_parents = [p for p in ordered_parents if any(lf in parent_map[p]["file_name"] for lf in PRIORITY_FILES_LOW)]
    final_ordered_parents = normal_parents + low_priority_parents

    context_blocks = []
    display_idx = 1

    for p_text in final_ordered_parents:
        info = parent_map[p_text]
        file_name = info["file_name"]
        year = info["year"]
        rerank_score = info["rerank_score"]
        parent_title = info["parent_title"]
        child_evidence = info["child_evidence"]
        
        entities_str = ", ".join(sorted(list(info["entities"]))) or "없음"
        relations_list = sorted(list(info["relations"]))
        relations_str = "\n  - ".join(relations_list[:6]) if relations_list else "없음"

        rerank_info = f" / Cohere Rerank 점수: {rerank_score:.4f}" if rerank_score > 0 else ""
        low_tag = " [하위순위 보충 참고용]" if any(lf in file_name for lf in PRIORITY_FILES_LOW) else ""
        evidence_str = "\n\n".join(
            f"[직접 근거 {idx}]\n{text}"
            for idx, text in enumerate(child_evidence, 1)
        ) or "직접 검색된 자식 근거 없음"

        block = f"""[참고 문단 {display_idx}{low_tag} (출처 파일: {file_name} / 지침 연도: {year}년{rerank_info})]
상위 주제: {parent_title}

질문과 직접 연관되어 검색·재정렬된 핵심 근거:
{evidence_str}

어미 해설 및 전체 보충 문맥:
{p_text}

직접 연관된 핵심 개체: {entities_str}
지식 그래프 정밀 연관망 (1-Hop 구조적 관계):
  - {relations_str}
"""
        context_blocks.append(block)
        display_idx += 1

    full_context = "\n----------------------------------------\n".join(context_blocks)

    priority_instruction = ""
    if is_school_or_foundation:
        priority_instruction = """
6. [교비회계/법인회계 3대 안전 프롬프트 장치]: 본 질의는 '교비회계' 또는 '법인회계' 관련 사안입니다.
   - [안전 장치 1: 명시적 규정 절대 적용]: 출처 파일명에 `[최우선]` 표기가 있는 핵심 문서에 구체적인 기준이나 원칙이 명시된 경우, 이를 절대적인 기준으로 삼아 답변하세요. 외부의 일반 기업 회계 상식이나 개인적 추측을 섞어 명시된 원칙을 자의적으로 해석하거나 예외를 지어내지 마세요.
   - [안전 장치 2: 미언급 시 차순위 유기적 보완]: 위 `[최우선]` 문서에 직접적인 언급이나 명시가 없는 사안에 한해서만, 차순위 안내서, 유의사항, Q&A 사례의 문맥을 유기적으로 통합하여 보완 설명하세요.
   - [안전 장치 3: 출처 엄격 인용 및 환각 방지]: 법령이나 조항 번호를 기재할 때는 오직 검색된 문맥에 실제로 존재하는 조항 번호와 출처 파일명만 엄격히 인용하세요. 본문에 없는 조항 번호를 임의로 조합하거나 추측하여 지어내지 마세요."""

    system_prompt = f"""당신은 사학기관 재무·회계 규칙 및 대학 온라인 상담 지식에 특화된 최고 수준의 GraphRAG 전문 AI입니다.

제공된 문맥에는 2가지 유형의 지식이 포함되어 있습니다:
1. [직접 근거 및 어미 해설]: Cohere Reranker가 부모 제목과 자식 근거로 정밀 심사한 핵심 문장 및 그 부모 보충 문맥
2. [지식 그래프 정밀 연관망]: 핵심 개체, 법률 조항, 계정과목, 예외 규정, 절차 간의 1-Hop 구조적 연관 관계망

답변 작성 지침 (엄격 적용):
1. [출처 파일 태그 인식 및 위계 적용]:
   - 출처 파일명에 `[최우선]`이 포함된 문서: 법적·행정적 최고 권위를 갖는 지침이므로, 명시된 내용이 있다면 **최우선 정답 절대 기준**으로 삼으세요.
   - 출처 파일명에 `[참고용]`이 포함된 문서: 보조 가이드라인 또는 범용 회계기준이므로, 상위 규정에 직접적인 언급이 없거나 보완이 필요한 경우에 한해 **가장 마지막 보충 참고용**으로만 활용하세요.
2. [법령/조항 번호 인용 엄격성]: 법령이나 조항 번호(예: 제36조, 제14조)를 언급할 때는 **제시된 참고 문단 본문에 명확히 표기된 조항 번호와 출처 파일명만 엄격히 인용**하세요. 문단에 없는 조항 번호를 추측하거나 지어내지 마세요.
3. [지식 그래프 연계]: [지식 그래프 정밀 연관망]에 제시된 관계망(예: 조항↔계정과목↔예외사항↔절차)을 본문 내용과 유기적으로 연계하여 논리적이고 깊이 있게 답변하세요.
4. [조항/절차 명시]: 관련된 구체적인 조항, 계정과목, 행정 절차 및 예외 조건이 지식 그래프나 본문에 포함되어 있다면 이를 명확하고 구조적으로 짚어주세요.
5. [연도별 개정사항 적용]: 검색된 문맥 지침 간에 연도별로 상충되거나 개정된 내용이 존재할 경우, 가장 최신 연도(최근 작성/개정된 문서)의 규정을 최우선으로 적용하고 이전 연도 대비 변경된 조항 및 내용을 분명히 비교하여 설명하세요.
6. 근거가 부족한 내용은 절대로 지어내지 말고, 제시된 문맥에 엄격히 기반하여 알기 쉽게 친절히 설명하세요.
{priority_instruction}"""

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
