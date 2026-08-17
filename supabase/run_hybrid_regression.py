"""Supabase 하이브리드 검색 V1과 V2의 근거 검색 품질을 비교한다."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = Path(__file__).with_name("regression_cases.json")
V1_FUNCTION = "match_univ_documents_hybrid"
V2_FUNCTION = "match_univ_documents_hybrid_v2"


def evaluate_result(rows, expected_text, expected_filename):
    """기대 문구와 파일명이 함께 나타나는 첫 검색 순위를 반환한다."""
    for rank, row in enumerate(rows, 1):
        content = row.get("content") or ""
        metadata = row.get("metadata") or {}
        filename = metadata.get("filename") or ""
        text_matches = expected_text in content
        filename_matches = not expected_filename or expected_filename in filename
        if text_matches and filename_matches:
            return {"passed": True, "rank": rank}
    return {"passed": False, "rank": None}


def build_rpc_payload(embedding, case):
    return {
        "query_embedding": embedding,
        "match_count": case.get("match_count", 20),
        "filter": case.get("filter", {}),
        "query_text": case["question"],
    }


def v2_has_regressed(v1_result, v2_result):
    return v1_result["passed"] and not v2_result["passed"]


def call_rpc(base_url, api_key, function_name, embedding, case):
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/rest/v1/rpc/{function_name}",
        data=json.dumps(build_rpc_payload(embedding, case)).encode("utf-8"),
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def load_environment():
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT_DIR / ".env")
    except ImportError:
        pass

    required = ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"필수 환경변수가 없습니다: {', '.join(missing)}")

    return os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]


def load_cases(path=DEFAULT_CASES_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_regression():
    from langchain_openai import OpenAIEmbeddings

    base_url, api_key = load_environment()
    cases = load_cases()
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    failed = False

    print("case\tV1\tV1 rank\tV2\tV2 rank\tseconds")
    for case in cases:
        started = time.perf_counter()
        embedding = embeddings.embed_query(case["question"])
        v1_rows = call_rpc(base_url, api_key, V1_FUNCTION, embedding, case)
        v2_rows = call_rpc(base_url, api_key, V2_FUNCTION, embedding, case)
        v1_result = evaluate_result(
            v1_rows, case["expected_text"], case.get("expected_filename", "")
        )
        v2_result = evaluate_result(
            v2_rows, case["expected_text"], case.get("expected_filename", "")
        )
        elapsed = time.perf_counter() - started
        print(
            f"{case['name']}\t{v1_result['passed']}\t{v1_result['rank']}\t"
            f"{v2_result['passed']}\t{v2_result['rank']}\t{elapsed:.2f}"
        )
        if not v2_result["passed"] or v2_has_regressed(v1_result, v2_result):
            failed = True

    return 1 if failed else 0


def main():
    try:
        return run_regression()
    except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError) as error:
        print(f"회귀 테스트 실행 실패: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
