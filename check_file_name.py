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

def check_chunks():
    driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))
    try:
        with driver.session(database=DATABASE) as session:
            query = """
            MATCH (c:Chunk)
            RETURN c.id AS id, c.file_name AS file_name, substring(c.text, 0, 50) AS preview
            LIMIT 5
            """
            result = session.run(query)
            print("=== [Neo4j 데이터 샘플 5개 확인] ===")
            for row in result:
                print(f"ID: {row['id']} | 파일명 라벨: '{row['file_name']}' | 본문 미리보기: {row['preview']}...")
    except Exception as e:
        print(f"[ERROR] 조회 오류: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    check_chunks()
