"""Batch upload, vector insertion and deletion contract."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import api
import index_state
import rag_builder
import rag_query


class Embedding:
    def tolist(self):
        return [0.1, 0.2]

    def __len__(self):
        return 2


class MemoryStore:
    def __init__(self):
        self.records = []

    def exists(self):
        return True

    def prepare(self, _dimension, rebuild=False):
        if rebuild:
            self.records.clear()
        return False

    def delete_document(self, doc_id):
        self.records = [r for r in self.records if r["doc_id"] != doc_id]

    def add(self, records):
        self.records.extend(records)

    def commit(self):
        pass

    def search(self, userid, collection_name, _vector, limit):
        return [{**r, "score": 0.9} for r in self.records
                if r["userid"] == userid and r["collection_name"] == collection_name][:limit]


class BatchIndexTests(unittest.TestCase):
    def test_upload_two_files_encodes_together_then_delete_one(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Mock()
            store.exists.return_value = True
            model = Mock()
            model.encode.return_value = [Embedding(), Embedding()]
            with patch.dict(os.environ, {"KNOWLEDGE_DIR": directory}), \
                 patch.object(index_state, "INDEX_FILE", str(Path(directory) / "index.json")), \
                 patch.object(rag_builder, "_model", return_value=model), \
                 patch.object(rag_builder, "get_vector_store", return_value=store), \
                 patch.object(rag_query, "get_vector_store", return_value=store):
                with TestClient(api.app) as client:
                    response = client.post(
                        "/api/v1/users/1/collections/notes/files/batch",
                        files=[
                            ("files", ("a.md", b"Alpha text", "text/markdown")),
                            ("files", ("b.txt", b"Beta text", "text/plain")),
                        ],
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    self.assertEqual(response.json()["indexed"], True)
                    self.assertEqual(response.json()["chunks"], 2)
                    self.assertEqual(len(response.json()["files"]), 2)
                    model.encode.assert_called_once()
                    records = store.add.call_args.args[0]
                    self.assertEqual([r["doc_id"] for r in records],
                                     ["1/notes/a.md", "1/notes/b.txt"])
                    self.assertEqual([r["userid"] for r in records], ["1", "1"])
                    self.assertEqual(set(index_state.load_index()),
                                     {"1/notes/a.md", "1/notes/b.txt"})
                    self.assertTrue((Path(directory) / "1" / "notes" / "a.md").exists())
                    client.post("/api/v1/auth/demo-login")
                    deleted = client.delete(
                        "/api/v1/users/1/collections/notes/files/a.md"
                    )
                    self.assertEqual(deleted.status_code, 200, deleted.text)
                    self.assertFalse((Path(directory) / "1" / "notes" / "a.md").exists())
                    self.assertEqual(set(index_state.load_index()), {"1/notes/b.txt"})
                    self.assertEqual(store.delete_document.call_args.args[0],
                                     "1/notes/a.md")

    def test_retrieval_reflects_upload_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore()
            model = Mock()
            model.encode.side_effect = [
                [Embedding(), Embedding()],
                [Embedding()],
                [Embedding()],
            ]
            with patch.dict(os.environ, {"KNOWLEDGE_DIR": directory}), \
                 patch.object(index_state, "INDEX_FILE", str(Path(directory) / "index.json")), \
                 patch.object(rag_builder, "_model", return_value=model), \
                 patch.object(rag_builder, "get_vector_store", return_value=store), \
                 patch.object(rag_query, "get_vector_store", return_value=store), \
                 patch.object(rag_query, "get_model", return_value=model):
                with TestClient(api.app) as client:
                    response = client.post(
                        "/api/v1/users/1/collections/notes/files/batch",
                        files=[("files", ("a.md", b"Alpha text", "text/markdown")),
                               ("files", ("b.md", b"Beta text", "text/markdown"))],
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    before = rag_query.retrieve("Alpha", "1", ["notes"], 5)
                    self.assertEqual({r["filename"] for r in before}, {"a.md", "b.md"})
                    client.post("/api/v1/auth/demo-login")
                    self.assertEqual(client.delete(
                        "/api/v1/users/1/collections/notes/files/a.md"
                    ).status_code, 200)
                    after = rag_query.retrieve("Alpha", "1", ["notes"], 5)
                    self.assertEqual([r["filename"] for r in after], ["b.md"])

    def test_batch_failure_does_not_claim_indexed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"KNOWLEDGE_DIR": directory}), \
                 patch.object(api, "index_files", side_effect=RuntimeError("model unavailable")):
                with TestClient(api.app) as client:
                    response = client.post(
                        "/api/v1/users/1/collections/notes/files/batch",
                        files=[("files", ("a.md", b"Alpha", "text/markdown"))],
                    )
            self.assertEqual(response.status_code, 500)
            self.assertNotIn("indexed", response.json())


if __name__ == "__main__":
    unittest.main()
