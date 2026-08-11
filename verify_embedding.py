import os
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

URI = os.getenv("NEO4J_URI")
USERNAME = os.getenv("NEO4J_USERNAME")
PASSWORD = os.getenv("NEO4J_PASSWORD")
DATABASE = os.getenv("NEO4J_DATABASE")

def verify_db_integrity():
    driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))
    try:
        with driver.session(database=DATABASE) as session:
            print("==================================================")
            print("🔍 [Neo4j 데이터베이스 실시간 데이터 검증 리포트]")
            print("==================================================\n")

            # 1. 청크 및 관계 전체 요약 통계
            summary_q = """
            MATCH (c:Chunk)
            WITH count(c) AS total_chunks,
                 sum(CASE WHEN c.is_parent = true THEN 1 ELSE 0 END) AS parent_count,
                 sum(CASE WHEN c.is_parent = false THEN 1 ELSE 0 END) AS child_count
            MATCH (e:Entity)
            WITH total_chunks, parent_count, child_count, count(e) AS total_entities
            MATCH ()-[r:MENTIONS]->()
            WITH total_chunks, parent_count, child_count, total_entities, count(r) AS mentions_count
            MATCH ()-[hp:HAS_PARENT]->()
            RETURN total_chunks, parent_count, child_count, total_entities, mentions_count, count(hp) AS parent_child_links
            """
            sum_res = session.run(summary_q).single()
            if sum_res:
                print(f"📊 [1. 데이터 규모 통계]")
                print(f"  - 전체 청크 수: {sum_res['total_chunks']}개 (어미: {sum_res['parent_count']}개 / 자식: {sum_res['child_count']}개)")
                print(f"  - 추출된 지식 노드(Entity): {sum_res['total_entities']}개")
                print(f"  - 어미-자식(Parent-Child) 사슬 연결 수: {sum_res['parent_child_links']}개")
                print(f"  - 지식 연결망(MENTIONS): {sum_res['mentions_count']}개\n")

            # 2. 샘플 자식 청크의 어미 연결 및 족보 검증
            sample_q = """
            MATCH (child:Chunk {is_parent: false})-[r:HAS_PARENT]->(parent:Chunk {is_parent: true})
            OPTIONAL MATCH (child)-[:MENTIONS]->(e:Entity)
            RETURN child.id AS child_id,
                   child.file_name AS file_name,
                   child.year AS year,
                   child.text AS child_text,
                   parent.title AS parent_title,
                   parent.text AS parent_text,
                   collect(DISTINCT e.id + ' (' + coalesce(e.type, '개체') + ')') AS entities
            LIMIT 2
            """
            sample_res = session.run(sample_q)
            print("🔬 [2. 실제 저장 데이터 족보 및 구조 검증 샘플]")
            for idx, row in enumerate(sample_res, 1):
                print(f"\n--- [검증 샘플 #{idx}] ---")
                print(f"📄 파일명: {row['file_name']} (등록 연도: {row['year']}년)")
                print(f"👩‍👧 어미(Parent) 제목: {row['parent_title']}")
                print(f"📌 자식(Child) 본문 족보 & 내용:")
                lines = row['child_text'].split('\n')
                print(f"   {lines[0] if len(lines) > 0 else ''}") # 족보 헤더
                print(f"   {lines[1] if len(lines) > 1 else ''}")
                print(f"   {lines[2] if len(lines) > 2 else ''}...")
                print(f"🕸️ 추출된 지식 노드 연결: {', '.join(row['entities'][:5])}")

    except Exception as e:
        print(f"❌ 검증 중 오류: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    verify_db_integrity()
