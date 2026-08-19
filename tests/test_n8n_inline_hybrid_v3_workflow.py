import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "n8n" / "univ-inline-hybrid-v3.workflow.json"


class InlineHybridV3WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}
        cls.connections = cls.workflow["connections"]

    def test_search_pipeline_nodes_exist(self):
        expected = {
            "Prepare Hybrid Search",
            "OpenAI Query Embedding",
            "Build Supabase V3 Payload",
            "Supabase V3 RPC",
            "Prepare Cohere Rerank",
            "Cohere Rerank HTTP",
            "Attach Reranked Chunks",
        }
        self.assertTrue(expected.issubset(self.nodes))

    def test_embedding_uses_question_text_only(self):
        body = self.nodes["OpenAI Query Embedding"]["parameters"]["jsonBody"]
        self.assertIn("question_text", body)
        self.assertNotIn("core_keywords", body)
        self.assertNotIn("optional_keywords", body)

    def test_rpc_receives_all_v3_arguments(self):
        body = self.nodes["Supabase V3 RPC"]["parameters"]["jsonBody"]
        for key in (
            "query_embedding",
            "query_text",
            "core_keywords",
            "optional_keywords",
            "match_count",
            "filter",
        ):
            self.assertIn(key, body)
        self.assertIn("match_univ_documents_hybrid_v3", self.nodes["Supabase V3 RPC"]["parameters"]["url"])

    def test_main_connection_runs_inline_search_before_draft(self):
        self.assertEqual(
            "Prepare Hybrid Search",
            self.connections["Key Words Extract AI"]["main"][0][0]["node"],
        )
        self.assertEqual(
            "Draft Answer Generator",
            self.connections["Attach Reranked Chunks"]["main"][0][0]["node"],
        )
        self.assertNotIn("ai_tool", self.connections.get("Supabase as AI Agent", {}))

    def test_draft_prompt_uses_retrieved_chunks(self):
        prompt = self.nodes["Draft Answer Generator"]["parameters"]["text"]
        self.assertIn("$json.chunks", prompt)
        self.assertIn("검색된 근거", prompt)
        self.assertNotIn("core_keywords만 공백으로 연결", prompt)

    def test_http_nodes_reuse_existing_credentials(self):
        self.assertIn("openAiApi", self.nodes["OpenAI Query Embedding"]["credentials"])
        self.assertIn("supabaseApi", self.nodes["Supabase V3 RPC"]["credentials"])
        self.assertIn("cohereApi", self.nodes["Cohere Rerank HTTP"]["credentials"])

    def test_new_main_nodes_do_not_overlap(self):
        names = [
            "Prepare Hybrid Search",
            "OpenAI Query Embedding",
            "Build Supabase V3 Payload",
            "Supabase V3 RPC",
            "Prepare Cohere Rerank",
            "Cohere Rerank HTTP",
            "Attach Reranked Chunks",
            "Draft Answer Generator",
            "Wait1",
        ]
        positions = [tuple(self.nodes[name]["position"]) for name in names]
        self.assertEqual(len(positions), len(set(positions)))


if __name__ == "__main__":
    unittest.main()
