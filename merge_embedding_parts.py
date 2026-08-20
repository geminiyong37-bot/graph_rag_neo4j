from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


PART_RE = re.compile(r"^(?P<base>.+)\((?P<part>\d+)\)\.md$")


def _body(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
        except ValueError:
            return text.strip()
        return "\n".join(lines[end + 1 :]).strip()
    return text.strip()


def _metrics(text: str) -> dict[str, int]:
    lines = text.splitlines()
    lengths = [len(line) for line in lines]
    return {
        "characters": len(text),
        "lines": len(lines),
        "headings": sum(1 for line in lines if line.startswith("#")),
        "table_rows": sum(1 for line in lines if line.lstrip().startswith("|")),
        "very_long_lines": sum(1 for length in lengths if length > 1200),
        "max_line_length": max(lengths, default=0),
    }


def merge_split_files(output_dir: Path) -> dict[str, int]:
    groups: dict[str, list[tuple[int, Path]]] = {}
    for path in output_dir.glob("*.md"):
        match = PART_RE.match(path.name)
        if match:
            groups.setdefault(match.group("base"), []).append(
                (int(match.group("part")), path)
            )

    manifest_path = output_dir / "_embedding_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records_by_name = {record["filename"]: record for record in manifest["files"]}
    merged_names: set[str] = set()

    for base, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        members.sort(key=lambda item: item[0])
        logical_name = f"{base}.md"
        first_text = members[0][1].read_text(encoding="utf-8")
        first_lines = first_text.splitlines()
        year_line = next((line for line in first_lines if line.startswith("year:")), "year: null")
        bodies = [_body(path.read_text(encoding="utf-8")) for _, path in members]
        combined = (
            "---\n"
            + f"filename: {json.dumps(logical_name, ensure_ascii=False)}\n"
            + year_line
            + "\n---\n\n"
            + "\n\n".join(bodies)
            + "\n"
        )
        (output_dir / logical_name).write_text(combined, encoding="utf-8", newline="\n")

        part_records = [records_by_name[path.name] for _, path in members]
        merged_record = dict(part_records[0])
        merged_record["filename"] = logical_name
        merged_record["source_files"] = [record["filename"] for record in part_records]
        merged_record["source_parts"] = part_records
        merged_record["output_sha256"] = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        merged_record["output_metrics"] = _metrics(combined)
        records_by_name[logical_name] = merged_record
        merged_names.update(path.name for _, path in members)

        for _, path in members:
            path.unlink()

    final_records = [
        record for name, record in records_by_name.items() if name not in merged_names
    ]
    final_records.sort(key=lambda record: record["filename"].casefold())
    manifest["source_file_count"] = manifest.get("file_count", len(manifest["files"]))
    manifest["logical_file_count"] = len(final_records)
    manifest["merged_group_count"] = len(groups)
    manifest.pop("file_count", None)
    manifest["files"] = final_records
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = {
        "source_file_count": manifest["source_file_count"],
        "logical_file_count": len(final_records),
        "merged_group_count": len(groups),
        "output_md_count": len(list(output_dir.glob("*.md"))),
        "files_with_long_lines": [
            record["filename"]
            for record in final_records
            if record["output_metrics"]["very_long_lines"] > 0
        ],
        "total_long_lines": sum(
            record["output_metrics"]["very_long_lines"] for record in final_records
        ),
        "total_table_rows": sum(
            record["output_metrics"]["table_rows"] for record in final_records
        ),
    }
    (output_dir / "_quality_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(merge_split_files(Path("data_embedding_ready")), ensure_ascii=False, indent=2))
