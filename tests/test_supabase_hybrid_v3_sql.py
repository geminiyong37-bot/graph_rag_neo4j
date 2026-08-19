import unittest
from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "sql"
    / "002_match_univ_documents_hybrid_v3.sql"
)


class SupabaseHybridV3SqlTests(unittest.TestCase):
    def read_sql(self):
        return SQL_PATH.read_text(encoding="utf-8")

    def test_v3_accepts_keyword_arrays_without_replacing_v1_or_v2(self):
        sql = self.read_sql()

        self.assertIn("match_univ_documents_hybrid_v3", sql)
        self.assertIn("core_keywords text[] default '{}'", sql)
        self.assertIn("optional_keywords text[] default '{}'", sql)
        self.assertNotIn("drop function", sql.lower())

    def test_v3_builds_vector_strict_and_expanded_routes(self):
        sql = self.read_sql()

        self.assertIn("vector_search as", sql)
        self.assertIn("strict_search as", sql)
        self.assertIn("expanded_search as", sql)
        self.assertIn("union all", sql)
        self.assertIn("group by d.id, d.content, d.metadata", sql)
        self.assertIn("0.70::float8", sql)
        self.assertIn("1.00::float8", sql)
        self.assertIn("0.40::float8", sql)

    def test_v3_uses_safe_tokenization_not_raw_ai_tsquery(self):
        sql = self.read_sql()

        self.assertIn("to_tsvector('simple', btrim(source.keyword))", sql)
        self.assertIn("tsvector_to_array", sql)
        self.assertIn("quote_literal(tokens.lexeme)", sql)
        self.assertNotIn("to_tsquery('simple', query_text)", sql)

    def test_v3_qualifies_candidate_ids_to_avoid_plpgsql_ambiguity(self):
        sql = self.read_sql()

        self.assertIn("select v.id", sql)
        self.assertIn("select s.id", sql)
        self.assertIn("select e.id", sql)


if __name__ == "__main__":
    unittest.main()
