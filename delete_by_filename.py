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

def delete_chunks_by_filename(target_filename: str):
    if not target_filename or not target_filename.strip():
        print("❌ 삭제할 파일명을 입력해주세요.")
        return

    driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))
    try:
        with driver.session(database=DATABASE) as session:
            # 1. 삭제 전 대상 청크 수 확인
            check_query = """
            MATCH (c:Chunk)
            WHERE c.file_name = $file_name OR c.file_name CONTAINS $file_name
            RETURN count(c) AS count, collect(DISTINCT c.file_name) AS matched_files
            """
            res = session.run(check_query, file_name=target_filename.strip()).single()
            count = res["count"]
            matched_files = res["matched_files"]

            if count == 0:
                print(f"⚠️ '{target_filename}' 키워드와 일치하는 데이터를 찾지 못했어.")
                return

            print(f"🔍 발견된 대상 파일명: {matched_files}")
            print(f"🗑️ 총 {count}개의 청크 노드 및 연결된 지식 관계를 삭제합니다...")

            # 2. 해당 file_name을 가진 Chunk 및 외톨이가 된 Entity 정리
            delete_query = """
            MATCH (c:Chunk)
            WHERE c.file_name = $file_name OR c.file_name CONTAINS $file_name
            DETACH DELETE c
            """
            session.run(delete_query, file_name=target_filename.strip())

            # 3. 더 이상 어떤 Chunk에서도 언급되지 않는 외톨이 Entity 정리
            clean_orphan_query = """
            MATCH (e:Entity)
            WHERE NOT (e)<-[:MENTIONS]-(:Chunk)
            DETACH DELETE e
            """
            clean_res = session.run(clean_orphan_query)

            print(f"✅ '{target_filename}' 관련 데이터 {count}개 완전히 삭제 완료!")
            print("✨ 연결이 끊어진 외톨이 개체들도 깔끔하게 정리되었어.")

    except Exception as e:
        print(f"❌ 삭제 도중 오류 발생: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        target = input("🗑️ 삭제하고 싶은 파일명(또는 키워드)을 입력하세요: ").strip()
    delete_chunks_by_filename(target)
