"""Exercise both HTTP answer paths through retrieval and answer generation."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import api
import rag_query


class Vector:
    def tolist(self):
        return [0.1, 0.2]


class QueryFlowTests(unittest.TestCase):
    def test_query_to_json_and_sse(self):
        model = Mock()
        model.encode.return_value = [Vector()]
        store = Mock()
        store.search.side_effect = lambda userid, name, vector, limit: [{
            "filename": name + ".md", "page": 1, "content": name + " content",
            "score": 0.8 if name == "one" else 0.9,
        }]
        llm = types.ModuleType("deepseek_api")
        llm.chat_by_deepseek = lambda prompt: "final answer"
        llm.chat_by_deepseek_stream = lambda prompt: iter(["final ", "answer"])
        payload = {
            "userid": "1", "collection_names": ["one", "two"],
            "question": "where?", "top_k": 2,
        }
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"KNOWLEDGE_DIR": directory}), \
             patch.object(rag_query, "get_model", return_value=model), \
             patch.object(rag_query, "get_vector_store", return_value=store), \
             patch.dict(sys.modules, {"deepseek_api": llm}):
            with TestClient(api.app) as client:
                normal = client.post("/ask", json=payload)
                stream = client.post("/ask_stream", json=payload)
        self.assertEqual(normal.status_code, 200, normal.text)
        self.assertEqual(normal.json()["answer"], "final answer")
        self.assertEqual(normal.json()["sources"],
                         [["two/two.md", 1], ["one/one.md", 1]])
        self.assertEqual(stream.status_code, 200, stream.text)
        self.assertIn('"token": "final "', stream.text)
        self.assertIn('"token": "answer"', stream.text)
        self.assertIn('"done": true', stream.text)
        self.assertIn('"two/two.md"', stream.text)
        self.assertEqual(model.encode.call_count, 2)
        self.assertEqual(store.search.call_count, 4)

    def test_stream_no_hits_returns_message_without_llm(self):
        model = Mock()
        model.encode.return_value = [Vector()]
        store = Mock()
        store.search.return_value = []
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"KNOWLEDGE_DIR": directory}), \
             patch.object(rag_query, "get_model", return_value=model), \
             patch.object(rag_query, "get_vector_store", return_value=store):
            with TestClient(api.app) as client:
                response = client.post("/ask_stream", json={
                    "userid": "1", "collection_names": ["one"],
                    "question": "where?", "top_k": 2,
                })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('"token": "未找到相关文档。"', response.text)
        self.assertIn('"done": true', response.text)


if __name__ == "__main__":
    unittest.main()
