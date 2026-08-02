import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

URI = os.getenv("NEO4J_URI")
USERNAME = os.getenv("NEO4J_USERNAME")
PASSWORD = os.getenv("NEO4J_PASSWORD")
DATABASE = os.getenv("NEO4J_DATABASE")

print(f"Connecting to {URI} as user {USERNAME} (db: {DATABASE})...")

driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))

try:
    driver.verify_connectivity()
    print("✅ Neo4j 클라우드 연결 성공!")
except Exception as e:
    print("❌ 연결 실패:", e)
finally:
    driver.close()