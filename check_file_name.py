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
            RETURN coalesce(c.file_name, '출처 미상') AS file_name, count(c) AS count
            ORDER BY count DESC
            """
            result = session.run(query)
            print("=== [Neo4j 데이터베이스에 임베딩된 전체 파일 목록] ===")
            records = list(result)
            if not records:
                print("⚠️ DB에 저장된 데이터(Chunk)가 하나도 없어!")
            else:
                for row in records:
                    print(f"📄 파일명: '{row['file_name']}' | (청크 수: {row['count']}개)")
    except Exception as e:
        print(f"[ERROR] 조회 오류: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    check_chunks()

