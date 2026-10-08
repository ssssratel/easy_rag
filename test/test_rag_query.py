"""Selected-collection retrieval and document deletion behavior."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import rag_query
import index_state
from user_files import is_collection_disabled, list_user_collections, save_upload


class Vector:
    def tolist(self):
        return [0.1, 0.2]


class RagQueryTests(unittest.TestCase):
    def test_retrieve_merges_selected_collections_by_score(self):
        model = Mock()
        model.encode.return_value = [Vector()]
        store = Mock()
        store.search.side_effect = [
            [{"filename": "a.md", "page": 1, "content": "a", "score": 0.5}],
            [{"filename": "b.md", "page": 1, "content": "b", "score": 0.9}],
        ]
        with patch.object(rag_query, "get_model", return_value=model), \
             patch.object(rag_query, "get_vector_store", return_value=store), \
             patch.object(rag_query, "is_collection_disabled", return_value=False):
            hits = rag_query.retrieve("question", "1", ["one", "two"], 2)
        self.assertEqual([item["collection_name"] for item in hits], ["two", "one"])
        model.encode.assert_called_once_with(["question"], normalize_embeddings=True)
        self.assertEqual(store.search.call_args_list, [
            call("1", "one", [0.1, 0.2], 2),
            call("1", "two", [0.1, 0.2], 2),
        ])
        self.assertEqual(rag_query.source_label(hits[0]), "two/b.md")

    def test_rejects_empty_or_duplicate_collections(self):
        for names in ([], ["one", "one"], ["bad-name"]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                rag_query.selected_collections(names)

    def test_stream_with_no_hits_does_not_call_llm(self):
        self.assertEqual(list(rag_query.stream_answer([], "question")),
                         ["未找到相关文档。"])

    def test_create_and_delete_collection_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            index_file = str(Path(directory) / "chunk_index.json")
            with patch.dict(os.environ, {"KNOWLEDGE_DIR": directory}), \
                 patch.object(index_state, "INDEX_FILE", index_file):
                store = Mock()
                with patch.object(rag_query, "get_vector_store", return_value=store):
                    self.assertTrue(rag_query.create_collection_for_user("1", "one"))
                    self.assertEqual(list_user_collections("1"), ["one"])
                    rag_query.delete_collection_for_user("1", "one")
                    self.assertTrue(is_collection_disabled("1", "one"))
                    self.assertEqual(list_user_collections("1"), [])
                    index_state.save_index({"1/one/notes.md": {"sha256": "x"},
                                            "2/two/other.md": {"sha256": "y"}})
                    self.assertTrue(rag_query.create_collection_for_user("1", "one"))
                self.assertFalse(is_collection_disabled("1", "one"))
                self.assertEqual(list(index_state.load_index()), ["2/two/other.md"])
                self.assertEqual(store.delete_scope.call_count, 2)

    def test_delete_file_removes_vector_then_source(self):
        with tempfile.TemporaryDirectory() as directory:
            index_file = str(Path(directory) / "chunk_index.json")
            with patch.dict(os.environ, {"KNOWLEDGE_DIR": directory}), \
                 patch.object(index_state, "INDEX_FILE", index_file):
                path = save_upload("1", "one", "notes.md", io.BytesIO(b"one"), 3)
                index_state.save_index({"1/one/notes.md": {"sha256": "same"}})
                store = Mock()
                store.exists.return_value = True
                with patch.object(rag_query, "get_vector_store", return_value=store):
                    rag_query.delete_file_for_user("1", "one", "notes.md")
                self.assertFalse(path.exists())
                self.assertEqual(index_state.load_index(), {})
                self.assertTrue(save_upload("1", "one", "notes.md", io.BytesIO(b"one"), 3).exists())
                store.delete_document.assert_called_once_with("1/one/notes.md")
                store.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
