import unittest

from supabase.run_hybrid_regression import (
    V2_FUNCTION,
    V3_FUNCTION,
    build_rpc_payload,
    evaluate_result,
    has_duplicate_ids,
    has_regressed,
)


class EvaluateResultTests(unittest.TestCase):
    def test_passes_when_expected_text_and_filename_are_found(self):
        rows = [{
            "content": "교직원 내부 인원에 대한 식사비는 복리후생비로 처리한다.",
            "metadata": {"filename": "특례규칙 해설서.md"},
        }]

        result = evaluate_result(
            rows,
            expected_text="내부 인원에 대한 식사비",
            expected_filename="특례규칙",
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["rank"], 1)

    def test_fails_when_expected_evidence_is_missing(self):
        result = evaluate_result(
            [{"content": "무관한 내용", "metadata": {}}],
            expected_text="내부 인원에 대한 식사비",
            expected_filename="특례규칙",
        )

        self.assertFalse(result["passed"])
        self.assertIsNone(result["rank"])

    def test_build_rpc_payload_uses_same_question_for_fts(self):
        case = {"question": "내부인원 식사비", "match_count": 20}

        payload = build_rpc_payload([0.1, 0.2], case, V2_FUNCTION)

        self.assertEqual(payload["query_embedding"], [0.1, 0.2])
        self.assertEqual(payload["query_text"], "내부인원 식사비")
        self.assertEqual(payload["match_count"], 20)
        self.assertEqual(payload["filter"], {})

    def test_build_v3_payload_keeps_question_and_keywords(self):
        case = {"question": "q", "core_keywords": ["a"], "optional_keywords": ["b"]}
        payload = build_rpc_payload([0.1], case, V3_FUNCTION)
        self.assertEqual(payload["query_text"], "q")
        self.assertEqual(payload["core_keywords"], ["a"])
        self.assertEqual(payload["optional_keywords"], ["b"])

    def test_detects_duplicate_ids(self):
        self.assertTrue(has_duplicate_ids([{"id": 7}, {"id": 7}]))
        self.assertFalse(has_duplicate_ids([{"id": 7}, {"id": 8}]))

    def test_detects_regression_when_baseline_passes_and_candidate_fails(self):
        self.assertTrue(
            has_regressed(
                {"passed": True, "rank": 3},
                {"passed": False, "rank": None},
            )
        )

    def test_does_not_flag_regression_when_both_versions_pass(self):
        self.assertFalse(
            has_regressed(
                {"passed": True, "rank": 3},
                {"passed": True, "rank": 2},
            )
        )


if __name__ == "__main__":
    unittest.main()
