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

def print_chunk_lengths():
    driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))
    with driver.session(database=DATABASE) as session:
        query = """
        MATCH (c:Chunk {file_name: '[2000년] 사은품, 경품, 선물에 관련된 세무회계처리.md'})
        RETURN c.is_parent AS is_parent, size(c.text) AS char_count, c.text AS text
        ORDER BY is_parent DESC, char_count DESC
        """
        res = session.run(query)
        print("=== [ [2000년] 사은품, 경품, 선물에 관련된 세무회계처리.md 청킹 글자 수 ] ===")
        for idx, r in enumerate(res, 1):
            type_name = "👩‍👧 어미(Parent)" if r['is_parent'] else "👶 자식(Child)"
            first_line = r['text'].split('\n')[0][:40]
            print(f"{idx}. [{type_name}] {r['char_count']}자 | 첫줄: {first_line}")
    driver.close()

if __name__ == "__main__":
    print_chunk_lengths()
