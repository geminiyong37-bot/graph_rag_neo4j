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
