from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path


YEAR_RE = re.compile(r"\[(?P<year>19\d{2}|20\d{2})년\]")
HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s*(?P<text>.*)$")
CHAPTER_RE = re.compile(r"^제\s*\d+\s*장(?:\s|$)")
SECTION_RE = re.compile(r"^제\s*\d+\s*절(?:\s|$)")
ARTICLE_RE = re.compile(r"^제\s*\d+\s*조(?:\s|\(|$)")
PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")
TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
BULLET_RE = re.compile(r"^(?:[-*+]\s+|[▪■□◆◇●○❍∙※☞▷▶◈▣￭]\s*)")
PROSE_END_RE = re.compile(
    r"(?:[.!?。]|다\.?|한다\.?|된다\.?|있다\.?|없다\.?|"
    r"하여야\s*함\.?|해야\s*함\.?|대상임\.?|말함\.?|규정함\.?)$"
)


def infer_year(filename: str) -> int | None:
    match = YEAR_RE.search(filename)
    return int(match.group("year")) if match else None


def infer_document_type(filename: str) -> str:
    name = filename.lower()
    if "판례" in name or "법률자문" in name or "법적 지위" in name:
        return "legal_case"
    if "질의" in name or "faq" in name or "q&a" in name or "qa" in name:
        return "qna_reference"
    if "감리" in name or "실태점검" in name or "지적" in name:
        return "inspection_case"
    if "세무" in name or "세제" in name or "기부금" in name:
        return "tax_guide"
    if "해설" in name:
        return "commentary"
    if "규칙" in name or "법률" in name or "법령" in name:
        return "regulation"
    if "지침" in name or "가이드" in name or "안내" in name or "매뉴얼" in name:
        return "guideline"
    if "회계" in name or "결산" in name or "예산" in name:
        return "accounting_reference"
    return "reference"


def normalize_characters(text: str) -> str:
    text = text.lstrip("\ufeff")
    text = unicodedata.normalize("NFC", text)
    replacements = {
        "\r\n": "\n",
        "\r": "\n",
        "￭": "▪",
        "｢": "「",
        "｣": "」",
        "➜": "→",
        "\u00a0": " ",
        "<br>": " ",
        "<br/>": " ",
        "<br />": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def looks_like_prose(text: str) -> bool:
    # PDF-to-MD exports often prefix prose lines with heading marks.
    if len(text) > 35:
        return True
    if PROSE_END_RE.search(text):
        return True
    return False


def normalize_heading(line: str) -> tuple[str, bool]:
    match = HEADING_RE.match(line.strip())
    if not match:
        return line.strip(), False

    text = match.group("text").strip()
    if not text:
        return "", True

    if BULLET_RE.match(text):
        text = BULLET_RE.sub("", text).strip()
        return f"- {text}", True
    if CHAPTER_RE.match(text):
        return f"# {text}", True
    if SECTION_RE.match(text):
        return f"## {text}", True
    if ARTICLE_RE.match(text):
        return f"### {text}", True
    if text.casefold() in {"contents", "목차"}:
        return "# 목차", True
    if text in {"들어가기에 앞서", "판례체크", "참고", "주의", "유의사항"}:
        return f"## {text}", True
    if looks_like_prose(text):
        return text, True
    return f"{match.group('marks')} {text}", False


def remove_page_number_blocks(lines: list[str]) -> tuple[list[str], int]:
    result: list[str] = []
    removed = 0
    index = 0
    while index < len(lines):
        if not PAGE_NUMBER_RE.match(lines[index]):
            result.append(lines[index])
            index += 1
            continue

        end = index
        while end < len(lines) and PAGE_NUMBER_RE.match(lines[end]):
            end += 1
        block_size = end - index
        if block_size >= 2:
            removed += block_size
        else:
            result.append(lines[index])
        index = end
    return result, removed


def rebuild_paragraphs(lines: list[str]) -> list[str]:
    output: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        segments: list[str] = []
        current = ""

        def split_oversized(part: str, hard_limit: int = 1100) -> list[str]:
            pieces: list[str] = []
            remaining = part.strip()
            while len(remaining) > hard_limit:
                window = remaining[:hard_limit]
                candidates = [
                    window.rfind(". "),
                    window.rfind("다. "),
                    window.rfind("함. "),
                    window.rfind("; "),
                    window.rfind(" "),
                ]
                cut = max(candidates) + 1
                if cut < 400:
                    cut = hard_limit
                pieces.append(remaining[:cut].strip())
                remaining = remaining[cut:].strip()
            if remaining:
                pieces.append(remaining)
            return pieces

        for original_part in paragraph:
            for part in split_oversized(original_part):
                candidate = f"{current} {part}".strip() if current else part
                if current and len(candidate) > 900:
                    segments.append(current)
                    current = part
                else:
                    current = candidate
        if current:
            segments.append(current)
        output.extend(re.sub(r"[ \t]+", " ", segment).strip() for segment in segments)
        paragraph.clear()

    for line in lines:
        stripped = line.strip()
        structural = (
            not stripped
            or stripped.startswith("#")
            or TABLE_RE.match(stripped)
            or BULLET_RE.match(stripped)
        )
        if structural:
            flush_paragraph()
            if stripped:
                output.append(stripped)
            elif output and output[-1] != "":
                output.append("")
            continue
        paragraph.append(stripped)

    flush_paragraph()
    while output and output[-1] == "":
        output.pop()
    return output


def normalize_body(raw_text: str) -> tuple[str, dict[str, int]]:
    text = normalize_characters(raw_text)
    source_lines = [line.rstrip() for line in text.split("\n")]
    normalized_lines: list[str] = []
    changed_headings = 0
    for line in source_lines:
        normalized, changed = normalize_heading(line)
        changed_headings += int(changed)
        normalized_lines.append(normalized)

    normalized_lines, removed_page_numbers = remove_page_number_blocks(normalized_lines)
    rebuilt = rebuild_paragraphs(normalized_lines)
    body = "\n".join(rebuilt).strip() + "\n"
    return body, {
        "source_lines": len(source_lines),
        "output_lines": len(rebuilt),
        "normalized_headings": changed_headings,
        "removed_page_number_lines": removed_page_numbers,
    }


def file_metrics(text: str) -> dict[str, int]:
    lines = text.splitlines()
    lengths = [len(line) for line in lines]
    return {
        "characters": len(text),
        "lines": len(lines),
        "headings": sum(1 for line in lines if line.startswith("#")),
        "table_rows": sum(1 for line in lines if TABLE_RE.match(line)),
        "very_long_lines": sum(1 for length in lengths if length > 1200),
        "max_line_length": max(lengths, default=0),
    }


def prepare_file(source: Path, destination: Path) -> dict[str, object]:
    raw = source.read_text(encoding="utf-8-sig")
    body, changes = normalize_body(raw)
    year = infer_year(source.name)
    frontmatter = ["---", f"filename: {json.dumps(source.name, ensure_ascii=False)}"]
    frontmatter.append(f"year: {year if year is not None else 'null'}")
    frontmatter.append("---")
    output = "\n".join(frontmatter) + "\n\n" + body
    destination.write_text(output, encoding="utf-8", newline="\n")

    source_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    output_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()
    return {
        "filename": source.name,
        "year": year,
        "document_type": infer_document_type(source.name),
        "source_sha256": source_hash,
        "output_sha256": output_hash,
        "changes": changes,
        "source_metrics": file_metrics(raw),
        "output_metrics": file_metrics(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="MD 문서를 임베딩 준비용으로 정규화한다.")
    parser.add_argument("--input", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("data_embedding_ready"))
    args = parser.parse_args()

    sources = sorted(args.input.glob("*.md"), key=lambda path: path.name.casefold())
    if not sources:
        raise SystemExit(f"MD 파일이 없어: {args.input.resolve()}")
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"출력 폴더가 비어 있지 않아: {args.output.resolve()}")
    args.output.mkdir(parents=True, exist_ok=True)

    records = [prepare_file(source, args.output / source.name) for source in sources]
    manifest = {
        "source_directory": str(args.input.resolve()),
        "output_directory": str(args.output.resolve()),
        "file_count": len(records),
        "files": records,
    }
    (args.output / "_embedding_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    summary = {
        "file_count": len(records),
        "output_md_count": len(list(args.output.glob("*.md"))),
        "files_with_long_lines": [
            record["filename"]
            for record in records
            if record["output_metrics"]["very_long_lines"] > 0
        ],
        "total_long_lines": sum(
            record["output_metrics"]["very_long_lines"] for record in records
        ),
        "total_table_rows": sum(record["output_metrics"]["table_rows"] for record in records),
    }
    (args.output / "_quality_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
