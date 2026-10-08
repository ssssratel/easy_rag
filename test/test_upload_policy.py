"""No-overwrite upload and full collection cleanup behavior."""
import io
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import api
import index_state
import rag_query
import user_files
from user_files import UploadConflict, save_upload


class UploadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"KNOWLEDGE_DIR": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        state = patch.object(index_state, "INDEX_FILE", str(self.root / "index.json"))
        state.start()
        self.addCleanup(state.stop)
        self.index = patch.object(api, "index_files", return_value=1)
        self.index_mock = self.index.start()
        self.addCleanup(self.index.stop)
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def test_duplicate_single_returns_normal_response_without_indexing(self):
        base = "/api/v1/users/1/collections/notes/files/a.md"
        self.assertEqual(self.client.put(base, content=b"original").status_code, 201)
        response = self.client.put(base, content=b"replacement")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["code"], "FILE_EXISTS")
        self.assertFalse(response.json()["uploaded"])
        self.assertFalse(response.json()["indexed"])
        self.assertEqual(response.json()["existing_files"], ["a.md"])
        self.assertEqual((self.root / "1" / "notes" / "a.md").read_bytes(), b"original")
        self.assertEqual(self.index_mock.call_count, 1)

    def test_batch_conflict_rejects_whole_batch_before_saving(self):
        old = save_upload("1", "notes", "old.md", io.BytesIO(b"original"), 8)
        response = self.client.post(
            "/api/v1/users/1/collections/notes/files/batch",
            files=[("files", ("new.md", b"new", "text/markdown")),
                   ("files", ("old.md", b"replacement", "text/markdown"))],
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["existing_files"], ["old.md"])
        self.assertFalse((old.parent / "new.md").exists())
        self.assertEqual(old.read_bytes(), b"original")
        self.index_mock.assert_not_called()

    def test_late_batch_conflict_rolls_back_new_files(self):
        real_save = api.save_upload

        def save_with_competing_writer(userid, name, filename, source, length):
            if filename == "b.md":
                target = user_files.collection_directory(userid, name) / filename
                target.write_bytes(b"competing writer")
            return real_save(userid, name, filename, source, length)

        with patch.object(api, "save_upload", side_effect=save_with_competing_writer):
            response = self.client.post(
                "/api/v1/users/1/collections/notes/files/batch",
                files=[("files", ("a.md", b"first", "text/markdown")),
                       ("files", ("b.md", b"second", "text/markdown"))],
            )
        self.assertEqual(response.status_code, 200, response.text)
        directory = self.root / "1" / "notes"
        self.assertFalse((directory / "a.md").exists())
        self.assertEqual((directory / "b.md").read_bytes(), b"competing writer")
        self.index_mock.assert_not_called()

    def test_legacy_default_filename_is_also_rejected(self):
        (self.root / "1").mkdir()
        old = self.root / "1" / "old.md"
        old.write_bytes(b"legacy")
        response = self.client.put(
            "/api/v1/users/1/collections/default/files/old.md", content=b"replacement"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(old.read_bytes(), b"legacy")
        self.assertFalse((old.parent / "default" / "old.md").exists())
        self.index_mock.assert_not_called()

    def test_concurrent_saves_cannot_replace_each_other(self):
        barrier = threading.Barrier(2)
        real_link = os.link

        def simultaneous_link(source, target):
            barrier.wait(timeout=5)
            return real_link(source, target)

        def attempt(content):
            try:
                save_upload("1", "notes", "a.md", io.BytesIO(content), len(content))
                return "saved"
            except UploadConflict:
                return "conflict"

        with patch.object(user_files.os, "link", side_effect=simultaneous_link):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(attempt, [b"first", b"second"]))
        self.assertCountEqual(results, ["saved", "conflict"])
        directory = self.root / "1" / "notes"
        self.assertIn((directory / "a.md").read_bytes(), [b"first", b"second"])
        self.assertEqual([p.name for p in directory.iterdir()], ["a.md"])

    def test_delete_collection_removes_files_vectors_and_index(self):
        old = save_upload("1", "notes", "a.md", io.BytesIO(b"source"), 6)
        nested = old.parent / "nested"
        nested.mkdir()
        (nested / "extra.bin").write_bytes(b"extra")
        other_collection = save_upload("1", "other", "a.md", io.BytesIO(b"other"), 5)
        other_user = save_upload("2", "notes", "a.md", io.BytesIO(b"user2"), 5)
        index_state.save_index({
            "1/notes/a.md": {"sha256": "old"},
            "1/other/a.md": {"sha256": "other"},
            "2/notes/a.md": {"sha256": "user2"},
        })
        store = Mock()
        self.client.post("/api/v1/auth/demo-login")
        with patch.object(rag_query, "get_vector_store", return_value=store):
            response = self.client.delete("/api/v1/users/1/collections/notes")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["files_deleted"])
        self.assertFalse(response.json()["files_preserved"])
        self.assertFalse(old.exists())
        self.assertFalse(nested.exists())
        self.assertTrue(other_collection.exists())
        self.assertTrue(other_user.exists())
        self.assertEqual(set(index_state.load_index()),
                         {"1/other/a.md", "2/notes/a.md"})
        store.delete_scope.assert_called_once_with("1", "notes")
        self.assertTrue(user_files.is_collection_disabled("1", "notes"))
        self.assertEqual([p.name for p in old.parent.iterdir()], [user_files.INACTIVE_MARKER])
        with patch.object(rag_query, "get_vector_store", return_value=store):
            self.assertEqual(self.client.post("/api/v1/users/1/collections/notes").status_code, 201)
        self.assertEqual(user_files.list_collection_files("1", "notes"), [])

    def test_vector_failure_preserves_sources_for_retry(self):
        old = save_upload("1", "notes", "a.md", io.BytesIO(b"source"), 6)
        store = Mock()
        store.delete_scope.side_effect = RuntimeError("Milvus unavailable")
        self.client.post("/api/v1/auth/demo-login")
        with patch.object(rag_query, "get_vector_store", return_value=store):
            response = self.client.delete("/api/v1/users/1/collections/notes")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(old.read_bytes(), b"source")
        self.assertTrue(user_files.is_collection_disabled("1", "notes"))

    def test_delete_collection_does_not_follow_directory_symlinks(self):
        old = save_upload("1", "notes", "a.md", io.BytesIO(b"source"), 6)
        outside = self.root / "outside"
        outside.mkdir()
        keep = outside / "keep.txt"
        keep.write_bytes(b"keep")
        link = old.parent / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are unavailable on this host")
        with patch.object(rag_query, "get_vector_store", return_value=Mock()):
            rag_query.delete_collection_for_user("1", "notes")
        self.assertFalse(link.exists())
        self.assertEqual(keep.read_bytes(), b"keep")

    def test_deleting_default_also_removes_legacy_files(self):
        user = self.root / "1"
        user.mkdir()
        legacy = user / "legacy.md"
        legacy.write_bytes(b"legacy")
        sibling = save_upload("1", "other", "keep.md", io.BytesIO(b"keep"), 4)
        store = Mock()
        with patch.object(rag_query, "get_vector_store", return_value=store):
            rag_query.delete_collection_for_user("1", "default")
        self.assertFalse(legacy.exists())
        self.assertTrue(sibling.exists())


if __name__ == "__main__":
    unittest.main()
