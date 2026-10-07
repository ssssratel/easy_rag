"""按用户与集合编号存储文件的路径与写入测试。"""

import io
import tempfile
import unittest
from pathlib import Path

from user_files import (
    iter_user_documents, save_upload, validate_collection_name,
    disable_collection, enable_collection, is_collection_disabled,
)


class UserFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_same_filename_is_separate_for_users_and_collections(self):
        alice_one = save_upload("alice", "one", "notes.md", io.BytesIO(b"one"), 3, self.root)
        alice_two = save_upload("alice", "two", "notes.md", io.BytesIO(b"two"), 3, self.root)
        bob_one = save_upload("bob", "one", "notes.md", io.BytesIO(b"bob"), 3, self.root)
        self.assertEqual(alice_one.read_bytes(), b"one")
        self.assertEqual(alice_two.read_bytes(), b"two")
        self.assertEqual(bob_one.read_bytes(), b"bob")
        self.assertEqual(
            [(doc_id, userid, collection_name)
             for doc_id, userid, collection_name, _ in iter_user_documents(self.root)],
            [("alice/one/notes.md", "alice", "one"),
             ("alice/two/notes.md", "alice", "two"),
             ("bob/one/notes.md", "bob", "one")],
        )

    def test_rejects_path_traversal_and_unsupported_files(self):
        for userid, collection_name, filename in [
            ("../bob", "one", "notes.md"),
            ("alice", "../bob", "notes.md"),
            ("alice", "one", "../notes.md"),
            ("alice", "one", "notes.py"),
            ("alice", "one", "..\\notes.md"),
        ]:
            with self.subTest(userid=userid, collection_name=collection_name, filename=filename):
                with self.assertRaises(ValueError):
                    save_upload(userid, collection_name, filename, io.BytesIO(b"x"), 1, self.root)

    def test_incomplete_upload_does_not_replace_existing_file(self):
        target = save_upload("alice", "one", "notes.md", io.BytesIO(b"old"), 3, self.root)
        with self.assertRaises(ValueError):
            save_upload("alice", "one", "notes.md", io.BytesIO(b"x"), 3, self.root)
        self.assertEqual(target.read_bytes(), b"old")

    def test_legacy_user_files_use_default_collection(self):
        (self.root / "outside.md").write_text("ignored", encoding="utf-8")
        (self.root / "alice").mkdir()
        (self.root / "alice" / "old.md").write_text("old", encoding="utf-8")
        (self.root / "alice" / "one").mkdir()
        (self.root / "alice" / "one" / "new.md").write_text("new", encoding="utf-8")
        self.assertEqual(
            [doc_id for doc_id, _, _, _ in iter_user_documents(self.root)],
            ["alice/default/old.md", "alice/one/new.md"],
        )

    def test_legacy_default_filename_collision_is_rejected(self):
        (self.root / "alice").mkdir()
        (self.root / "alice" / "notes.md").write_text("old", encoding="utf-8")
        (self.root / "alice" / "default").mkdir()
        (self.root / "alice" / "default" / "notes.md").write_text("new", encoding="utf-8")
        with self.assertRaises(ValueError):
            list(iter_user_documents(self.root))

    def test_ascii_name_and_disabled_collection(self):
        name = "English_123"
        self.assertEqual(validate_collection_name(name), name)
        for invalid in ("", "中文", "café", "１23", "a-b", "a.b", "a/b", "a" * 65):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_collection_name(invalid)

        save_upload("alice", name, "notes.md", io.BytesIO(b"one"), 3, self.root)
        disable_collection("alice", name, self.root)
        self.assertTrue(is_collection_disabled("alice", name, self.root))
        self.assertEqual(list(iter_user_documents(self.root)), [])
        with self.assertRaises(ValueError):
            save_upload("alice", name, "other.md", io.BytesIO(b"x"), 1, self.root)
        enable_collection("alice", name, self.root)
        self.assertFalse(is_collection_disabled("alice", name, self.root))
        self.assertEqual(
            [doc_id for doc_id, _, _, _ in iter_user_documents(self.root)],
            ["alice/{}/notes.md".format(name)],
        )


if __name__ == "__main__":
    unittest.main()
