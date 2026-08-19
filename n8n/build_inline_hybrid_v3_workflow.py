"""Build an importable inline n8n workflow from the user's exported workflow."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(
    os.environ.get(
        "N8N_SOURCE_WORKFLOW",
        r"C:\Users\회계9\.codex\attachments\04e1ccc6-3998-41bf-b0b8-4bbb342b3ec3\pasted-text.txt",
    )
)
OUTPUT = ROOT / "n8n" / "univ-inline-hybrid-v3.workflow.json"
ENV_FILE = ROOT / ".env.supabase-hybrid-v2.local"


def load_supabase_url() -> str:
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPABASE_URL="):
            return line.split("=", 1)[1].strip().rstrip("/")
    raise RuntimeError("SUPABASE_URL is missing from the local environment file")


def credential(workflow: dict, node_name: str, credential_type: str) -> dict:
    node = next(node for node in workflow["nodes"] if node["name"] == node_name)
    return deepcopy(node["credentials"][credential_type])


def node(node_type: str, name: str, position: list[int], parameters: dict, credentials=None) -> dict:
    result = {
        "parameters": parameters,
        "type": node_type,
        "typeVersion": 4.3 if node_type == "n8n-nodes-base.httpRequest" else 2,
        "position": position,
        "id": str(uuid4()),
        "name": name,
    }
    if credentials:
        result["credentials"] = credentials
    return result


def main_connection(target: str) -> dict:
    return {"main": [[{"node": target, "type": "main", "index": 0}]]}


def build() -> dict:
    workflow = json.loads(SOURCE.read_text(encoding="utf-8"))
    supabase_url = load_supabase_url()
    openai = credential(workflow, "Embeddings OpenAI1", "openAiApi")
    supabase = credential(workflow, "Supabase as AI Agent", "supabaseApi")
    cohere = credential(workflow, "Reranker Cohere", "cohereApi")

    prepare_code = r'''const extracted = typeof $json.output === 'string'
  ? JSON.parse($json.output)
  : ($json.output ?? $json);
const question = extracted.question ?? {};
const asText = (value) => typeof value === 'string' ? value.trim() : '';
const normalize = (value, maxLength) => [...new Set(
  (Array.isArray(value) ? value : []).map(asText).filter(Boolean)
)].slice(0, maxLength);
const questionText = [
  ['제목', asText(question['제목'])],
  ['사실관계', asText(question['1.사실관계'])],
  ['질의사항', asText(question['2.질의사항'])],
  ['관련법령', asText(question['3.관련법령'])],
].filter(([, value]) => value).map(([label, value]) => `${label}: ${value}`).join('\n');
if (!questionText) throw new Error('검색할 원문 질의가 없습니다.');
return [{ json: {
  question,
  core_keywords: normalize(extracted.core_keywords, 3),
  optional_keywords: normalize(extracted.optional_keywords, 6),
  question_text: questionText,
  match_count: 20,
  filter: {},
} }];'''

    payload_code = r'''const prepared = $('Prepare Hybrid Search').item.json;
const embedding = $json.data?.[0]?.embedding;
if (!Array.isArray(embedding) || embedding.length !== 1536) {
  throw new Error('text-embedding-3-small 임베딩(1536차원)을 받지 못했습니다.');
}
return [{ json: { ...prepared, query_embedding: embedding } }];'''

    rerank_prepare_code = r'''const prepared = $('Prepare Hybrid Search').item.json;
const candidates = $input.all().flatMap((item) => {
  if (Array.isArray(item.json)) return item.json;
  if (Array.isArray(item.json.body)) return item.json.body;
  return item.json?.id ? [item.json] : [];
});
if (candidates.length === 0) {
  throw new Error('Supabase V3 검색 결과가 없습니다. RPC 입력과 필터를 확인하세요.');
}
return [{ json: {
  ...prepared,
  candidates,
  documents: candidates.map((row) => row.content ?? ''),
} }];'''

    attach_code = r'''const search = $('Prepare Cohere Rerank').item.json;
const results = Array.isArray($json.results) ? $json.results : [];
const chunks = results.slice(0, 7).map(({ index, relevance_score }) => ({
  ...search.candidates[index],
  rerank_score: relevance_score,
})).filter((row) => row.id !== undefined);
return [{ json: {
  question: search.question,
  core_keywords: search.core_keywords,
  optional_keywords: search.optional_keywords,
  chunks,
} }];'''

    added = [
        node("n8n-nodes-base.code", "Prepare Hybrid Search", [80, 2272], {"jsCode": prepare_code}),
        node(
            "n8n-nodes-base.httpRequest",
            "OpenAI Query Embedding",
            [304, 2272],
            {
                "method": "POST",
                "url": "https://api.openai.com/v1/embeddings",
                "authentication": "predefinedCredentialType",
                "nodeCredentialType": "openAiApi",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ { model: 'text-embedding-3-small', input: $json.question_text } }}",
                "options": {},
            },
            {"openAiApi": openai},
        ),
        node("n8n-nodes-base.code", "Build Supabase V3 Payload", [528, 2272], {"jsCode": payload_code}),
        node(
            "n8n-nodes-base.httpRequest",
            "Supabase V3 RPC",
            [752, 2272],
            {
                "method": "POST",
                "url": f"{supabase_url}/rest/v1/rpc/match_univ_documents_hybrid_v3",
                "authentication": "predefinedCredentialType",
                "nodeCredentialType": "supabaseApi",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ { query_embedding: $json.query_embedding, query_text: $json.question_text, core_keywords: $json.core_keywords, optional_keywords: $json.optional_keywords, match_count: $json.match_count, filter: $json.filter } }}",
                "options": {},
            },
            {"supabaseApi": supabase},
        ),
        node(
            "n8n-nodes-base.code",
            "Prepare Cohere Rerank",
            [976, 2272],
            {"mode": "runOnceForAllItems", "jsCode": rerank_prepare_code},
        ),
        node(
            "n8n-nodes-base.httpRequest",
            "Cohere Rerank HTTP",
            [1200, 2272],
            {
                "method": "POST",
                "url": "https://api.cohere.com/v2/rerank",
                "authentication": "predefinedCredentialType",
                "nodeCredentialType": "cohereApi",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ { model: 'rerank-v3.5', query: $json.question_text, documents: $json.documents, top_n: 7 } }}",
                "options": {},
            },
            {"cohereApi": cohere},
        ),
        node("n8n-nodes-base.code", "Attach Reranked Chunks", [1424, 2272], {"jsCode": attach_code}),
    ]
    workflow["nodes"].extend(added)

    draft = next(node for node in workflow["nodes"] if node["name"] == "Draft Answer Generator")
    draft["position"] = [1648, 2272]
    draft["parameters"]["text"] = r'''==[질의 및 핵심어]
{{ JSON.stringify({ question: $json.question, core_keywords: $json.core_keywords, optional_keywords: $json.optional_keywords }) }}

==[검색된 근거]
{{ JSON.stringify($json.chunks) }}

위 question의 질의에 답변해. 반드시 검색된 근거의 content와 metadata에서 확인되는 내용만 사용해.

[출력 양식]

질의 작성일 : question의 질의 작성일 원문
구분 : question의 구분 원문
제목 : question의 제목 원문

1. 질의 내용
- question의 1.사실관계 원문
- question의 2.질의사항 원문

2. 답변(안)
- 질문별로 검색된 근거와 판단을 작성해.
- 질문이 여러 개이면 각각 구분해.
- 각 답변은 원칙적으로 500자 내외로 작성해.

3. 관련 법령
- 답변에 실제로 사용한 법령의 명칭, 조항 번호, 조항 제목을 작성해.
- 정확한 정보가 확인되지 않으면 “확인 가능한 관련 법령 없음”으로 작성해.
- question의 3.관련법령은 참고정보로만 사용해.

4. 유사 사례
- 검색된 근거 중 [대학 온라인 상담]으로 표시된 사례만 사용해.
- 제목과 연·월·일이 모두 확인되는 유사사례를 최대 3개 작성해.
- 유사사례가 없으면 “해당 없음”으로 작성해.

설명용 안내 문구, 이모티콘 및 불필요한 마크다운은 사용하지 마.'''
    draft["parameters"]["options"]["systemMessage"] = r'''너는 사립대학 재무·회계 상담 답변 작성자야.

1. 제공된 검색 근거 chunks만 사용해 답변해.
2. 검색 결과에 명시된 법령·지침·해설서·과거 Q&A 내용만 근거로 사용해.
3. 일반 기업 회계 관행이나 사전 학습 지식으로 기준을 변경하거나 예외를 만들지 마.
4. 검색 결과에 없는 사실, 법령, 조항 번호, 조항 제목 및 유사사례를 만들지 마.
5. 법령의 명칭·조항·조항 제목이 검색 결과에서 모두 확인될 때만 관련 법령에 기재해.
6. 자료가 충돌하면 현재 질문에 직접 적용되는 구체적인 기준을 우선하고 충돌 사실을 밝혀.
7. 근거가 부족하면 단정하지 말고 검색 자료만으로 판단하기 어렵다고 밝혀.'''

    next(node for node in workflow["nodes"] if node["name"] == "OpenAI Chat")["position"] = [1648, 2496]
    next(node for node in workflow["nodes"] if node["name"] == "Wait1")["position"] = [1872, 2272]

    connections = workflow["connections"]
    connections["Key Words Extract AI"] = main_connection("Prepare Hybrid Search")
    connections["Prepare Hybrid Search"] = main_connection("OpenAI Query Embedding")
    connections["OpenAI Query Embedding"] = main_connection("Build Supabase V3 Payload")
    connections["Build Supabase V3 Payload"] = main_connection("Supabase V3 RPC")
    connections["Supabase V3 RPC"] = main_connection("Prepare Cohere Rerank")
    connections["Prepare Cohere Rerank"] = main_connection("Cohere Rerank HTTP")
    connections["Cohere Rerank HTTP"] = main_connection("Attach Reranked Chunks")
    connections["Attach Reranked Chunks"] = main_connection("Draft Answer Generator")
    connections["Supabase as AI Agent"].pop("ai_tool", None)

    return workflow


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)
