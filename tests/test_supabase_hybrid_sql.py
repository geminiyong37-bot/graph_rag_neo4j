import unittest
from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "sql"
    / "001_match_univ_documents_hybrid_v2.sql"
)


class SupabaseHybridSqlTests(unittest.TestCase):
    def test_v2_sql_keeps_v1_and_builds_safe_or_query(self):
        sql = SQL_PATH.read_text(encoding="utf-8")

        self.assertIn("match_univ_documents_hybrid_v2", sql)
        self.assertNotIn("drop function match_univ_documents_hybrid", sql.lower())
        self.assertIn("tsvector_to_array(to_tsvector('simple', query_text))", sql)
        self.assertIn("order by char_length(lexeme) desc", sql)
        self.assertIn("limit 8", sql)
        self.assertIn("' | '", sql)

    def test_v2_sql_limits_match_count_and_handles_empty_query(self):
        sql = SQL_PATH.read_text(encoding="utf-8")

        self.assertIn("greatest(1, least(coalesce(match_count, 20), 50))", sql)
        self.assertIn("nullif(btrim(query_text), '')", sql)
        self.assertIn("fts_query is not null", sql)


if __name__ == "__main__":
    unittest.main()
