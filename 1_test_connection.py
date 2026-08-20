import os
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure environment is reloaded from .env file
load_dotenv(override=True)

URI = os.getenv("NEO4J_URI")
USERNAME = os.getenv("NEO4J_USERNAME")
PASSWORD = os.getenv("NEO4J_PASSWORD")

masked_pw = PASSWORD[:3] + "*" * (len(PASSWORD) - 6) + PASSWORD[-3:] if PASSWORD and len(PASSWORD) > 6 else "***"
print(f"Connecting to {URI} as user '{USERNAME}' with password '{masked_pw}' (length: {len(PASSWORD) if PASSWORD else 0})...")

try:
    driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))
    driver.verify_connectivity()
    print("🎉🎉🎉 SUCCESS: Neo4j 클라우드 데이터베이스 연결 완벽 성공! 🎉🎉🎉")

    # Query database info
    with driver.session() as session:
        result = session.run("MATCH (n) RETURN count(n) AS node_count")
        count = result.single()["node_count"]
        print(f"📊 현재 DB 내부 저장된 총 노드(데이터 점) 수: {count:,} 개")

    driver.close()
except Exception as e:
    print("❌ 연결 실패:", e)
