import os
import sys
import importlib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

# 숫자 시작 모듈 동적 임포트
ask_module = importlib.import_module("3_ask_graph")
hybrid_search_and_answer = ask_module.hybrid_search_and_answer

build_module = importlib.import_module("2_build_graph_from_md")
ingest_chunk_text = build_module.ingest_chunk_text

app = FastAPI(
    title="GraphRAG Neo4j API Server",
    description="24/7 Cloud API Server for n8n GraphRAG Integration",
    version="1.0.0"
)

class QuestionRequest(BaseModel):
    question: str
    top_k: int = 3

class AnswerResponse(BaseModel):
    status: str = "success"
    question: str
    answer: str

class IngestRequest(BaseModel):
    content: str
    source_name: str = "온라인상담_구글시트"

class IngestResponse(BaseModel):
    status: str = "success"
    chunk_id: str
    nodes_created: int
    relationships_created: int

@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "GraphRAG Neo4j API Server is running 24/7!",
        "docs": "/docs"
    }

@app.post("/ask", response_model=AnswerResponse)
def ask_question(req: QuestionRequest):
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    
    try:
        answer = hybrid_search_and_answer(req.question.strip(), top_k=req.top_k)
        return AnswerResponse(
            status="success",
            question=req.question,
            answer=answer
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ingest", response_model=IngestResponse)
def ingest_data(req: IngestRequest):
    if not req.content or not req.content.strip():
        raise HTTPException(status_code=400, detail="Content text cannot be empty.")
    
    try:
        res = ingest_chunk_text(req.content.strip(), req.source_name)
        return IngestResponse(
            status="success",
            chunk_id=res["chunk_id"],
            nodes_created=res["nodes_created"],
            relationships_created=res["relationships_created"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

