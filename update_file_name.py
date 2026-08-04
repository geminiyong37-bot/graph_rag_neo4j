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

DEFAULT_FILE_NAME = "특례규칙 해설서"

def update_file_names():
    print(f"Connecting to Neo4j database...")
    driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))
    try:
        with driver.session(database=DATABASE) as session:
            # 모든 Chunk 노드에 file_name 속성 추가/업데이트
            query = """
            MATCH (c:Chunk)
            SET c.file_name = $file_name
            RETURN count(c) AS updated_count
            """
            result = session.run(query, {"file_name": DEFAULT_FILE_NAME})
            count = result.single()["updated_count"]
            print(f"[SUCCESS] 총 {count}개의 Chunk 데이터에 파일명 '{DEFAULT_FILE_NAME}'을(를) 성공적으로 등록했어!")
    except Exception as e:
        print(f"[ERROR] 업데이트 오류 발생: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    update_file_names()
