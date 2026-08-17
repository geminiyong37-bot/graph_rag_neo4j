import ast
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "3_ask_graph.py"


def load_pure_functions(*names):
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(MODULE_PATH), "exec"), namespace)
    return namespace


class RerankContextTests(unittest.TestCase):
    def test_rerank_passage_uses_child_text_not_parent_body(self):
        build_passage = load_pure_functions("build_rerank_passage").get("build_rerank_passage")
        self.assertIsNotNone(build_passage, "build_rerank_passage 함수가 필요해")

        passage = build_passage({
            "file_name": "특례규칙 해설서.md",
            "parent_title": "운영비",
            "child_text": "내부 인원 식사비는 복리후생비로 처리한다.",
            "parent_text": "부모 전체에만 있는 무관한 건축물관리비 내용",
        })

        self.assertIn("특례규칙 해설서.md", passage)
        self.assertIn("운영비", passage)
        self.assertIn("내부 인원 식사비는 복리후생비", passage)
        self.assertNotIn("무관한 건축물관리비", passage)

    def test_grouping_keeps_multiple_child_evidence_for_same_parent(self):
        group_chunks = load_pure_functions("group_chunks_by_parent").get("group_chunks_by_parent")
        self.assertIsNotNone(group_chunks, "group_chunks_by_parent 함수가 필요해")

        rows = [
            {
                "parent_text": "부모 전체 문맥",
                "parent_title": "운영비",
                "child_text": "내부 인원 식사비 기준",
                "file_name": "특례규칙 해설서.md",
                "year": 2023,
                "rerank_score": 0.9,
                "direct_entities": [],
                "hop1_relations": [],
            },
            {
                "parent_text": "부모 전체 문맥",
                "parent_title": "운영비",
                "child_text": "외부 인원 식사비 기준",
                "file_name": "특례규칙 해설서.md",
                "year": 2023,
                "rerank_score": 0.8,
                "direct_entities": [],
                "hop1_relations": [],
            },
        ]

        parent_map, ordered_parents = group_chunks(rows)

        self.assertEqual(["부모 전체 문맥"], ordered_parents)
        self.assertEqual(
            ["내부 인원 식사비 기준", "외부 인원 식사비 기준"],
            parent_map["부모 전체 문맥"]["child_evidence"],
        )


if __name__ == "__main__":
    unittest.main()
