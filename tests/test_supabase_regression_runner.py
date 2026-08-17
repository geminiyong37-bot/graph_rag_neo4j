import unittest

from supabase.run_hybrid_regression import evaluate_result


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


if __name__ == "__main__":
    unittest.main()
