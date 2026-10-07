"""Contract checks for the Milvus vector-store adapter."""

import unittest
from unittest.mock import Mock, patch
import os

from vector_store import MilvusVectorStore


class MilvusVectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.store = MilvusVectorStore(client=self.client)

    def test_prepare_creates_or_rebuilds_collection(self):
        self.client.has_collection.side_effect = [True, False]
        self.assertTrue(self.store.prepare(1024, rebuild=True))
        self.client.drop_collection.assert_called_once_with(self.store.name)
        self.client.create_collection.assert_called_once_with(
            collection_name=self.store.name,
            dimension=1024,
            metric_type="IP",
            auto_id=True,
            enable_dynamic_field=True,
        )

    def test_prepare_creates_missing_physical_collection(self):
        self.client.has_collection.return_value = False
        self.assertTrue(self.store.prepare(384))
        self.client.create_collection.assert_called_once()
        self.assertEqual(self.client.create_collection.call_args.kwargs["dimension"], 384)

    def test_physical_name_rejects_chinese(self):
        with patch.dict(os.environ, {"VECTOR_COLLECTION": "中文集合"}):
            with self.assertRaises(ValueError):
                MilvusVectorStore(client=self.client)

    def test_search_on_missing_physical_collection_is_empty(self):
        self.client.has_collection.return_value = False
        self.assertEqual(self.store.search("alice", "group_1", [0.1], 3), [])
        self.client.search.assert_not_called()

    def test_search_returns_backend_independent_records(self):
        self.client.search.return_value = [[{
            "entity": {"content": "text", "filename": "doc.md", "page": 2},
            "distance": 0.8,
        }]]
        result = self.store.search("alice", "one", [0.1, 0.2], 3)
        self.assertEqual(result, [{
            "content": "text", "filename": "doc.md", "page": 2, "score": 0.8,
        }])
        self.client.search.assert_called_once_with(
            collection_name=self.store.name,
            data=[[0.1, 0.2]],
            filter='userid == "alice" and collection_name == "one"',
            limit=3,
            output_fields=["content", "filename", "page"],
            search_params={"metric_type": "IP", "params": {"nprobe": 10}},
        )

    def test_document_write_delete_and_commit(self):
        record = {
            "content": "text", "vector": [0.1], "filename": "doc.md",
            "page": 1, "userid": "alice", "collection_name": "one",
            "doc_id": "alice/one/doc.md",
        }
        self.store.add([record])
        self.store.delete_document("alice/one/doc.md")
        self.store.commit()
        self.client.insert.assert_called_once_with(
            collection_name=self.store.name, data=[record],
        )
        self.client.delete.assert_called_once_with(
            collection_name=self.store.name,
            filter='doc_id == "alice/one/doc.md"',
        )
        self.client.flush.assert_called_once_with(collection_name=self.store.name)

    def test_delete_scope_keeps_other_users_and_collections(self):
        self.client.has_collection.return_value = True
        self.store.delete_scope("alice", "group_1")
        self.client.delete.assert_called_once_with(
            collection_name=self.store.name,
            filter='userid == "alice" and collection_name == "group_1"',
        )
        self.client.flush.assert_called_once_with(collection_name=self.store.name)

    def test_delete_scope_skips_missing_physical_collection(self):
        self.client.has_collection.return_value = False
        self.store.delete_scope("alice", "one")
        self.client.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
