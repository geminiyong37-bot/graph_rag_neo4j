def evaluate_result(rows, expected_text, expected_filename):
    """기대 문구와 파일명이 함께 나타나는 첫 검색 순위를 반환한다."""
    for rank, row in enumerate(rows, 1):
        content = row.get("content") or ""
        metadata = row.get("metadata") or {}
        filename = metadata.get("filename") or ""
        if expected_text in content and expected_filename in filename:
            return {"passed": True, "rank": rank}
    return {"passed": False, "rank": None}
