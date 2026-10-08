"""按用户与集合编号存储文件的路径与写入测试。"""

import io
import tempfile
import unittest
from pathlib import Path

from user_files import (
    iter_user_documents, save_upload, validate_collection_name, validate_userid,
    list_user_collections, list_collection_files, collection_file_path,
    disable_collection, enable_collection, is_collection_disabled,
)


class UserFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_same_filename_is_separate_for_users_and_collections(self):
        alice_one = save_upload("1", "one", "notes.md", io.BytesIO(b"one"), 3, self.root)
        alice_two = save_upload("1", "two", "notes.md", io.BytesIO(b"two"), 3, self.root)
        bob_one = save_upload("2", "one", "notes.md", io.BytesIO(b"bob"), 3, self.root)
        self.assertEqual(alice_one.read_bytes(), b"one")
        self.assertEqual(alice_two.read_bytes(), b"two")
        self.assertEqual(bob_one.read_bytes(), b"bob")
        self.assertEqual(
            [(doc_id, userid, collection_name)
             for doc_id, userid, collection_name, _ in iter_user_documents(self.root)],
            [("1/one/notes.md", "1", "one"),
             ("1/two/notes.md", "1", "two"),
             ("2/one/notes.md", "2", "one")],
        )

    def test_rejects_path_traversal_and_unsupported_files(self):
        for userid, collection_name, filename in [
            ("../bob", "one", "notes.md"),
            ("1", "../bob", "notes.md"),
            ("1", "one", "../notes.md"),
            ("1", "one", "notes.py"),
            ("1", "one", "..\\notes.md"),
        ]:
            with self.subTest(userid=userid, collection_name=collection_name, filename=filename):
                with self.assertRaises(ValueError):
                    save_upload(userid, collection_name, filename, io.BytesIO(b"x"), 1, self.root)

    def test_incomplete_upload_leaves_no_file(self):
        with self.assertRaises(ValueError):
            save_upload("1", "one", "notes.md", io.BytesIO(b"x"), 3, self.root)
        self.assertFalse((self.root / "1" / "one" / "notes.md").exists())
        self.assertEqual(list((self.root / "1" / "one").iterdir()), [])

    def test_legacy_user_files_use_default_collection(self):
        (self.root / "outside.md").write_text("ignored", encoding="utf-8")
        (self.root / "1").mkdir()
        (self.root / "1" / "old.md").write_text("old", encoding="utf-8")
        (self.root / "1" / "one").mkdir()
        (self.root / "1" / "one" / "new.md").write_text("new", encoding="utf-8")
        self.assertEqual(
            [doc_id for doc_id, _, _, _ in iter_user_documents(self.root)],
            ["1/default/old.md", "1/one/new.md"],
        )
        self.assertEqual(list_collection_files("1", "default", self.root), ["old.md"])
        self.assertEqual(
            collection_file_path("1", "default", "old.md", self.root),
            self.root / "1" / "old.md",
        )

    def test_legacy_default_filename_collision_is_rejected(self):
        (self.root / "1").mkdir()
        (self.root / "1" / "notes.md").write_text("old", encoding="utf-8")
        (self.root / "1" / "default").mkdir()
        (self.root / "1" / "default" / "notes.md").write_text("new", encoding="utf-8")
        with self.assertRaises(ValueError):
            list(iter_user_documents(self.root))

    def test_collection_file_listing_and_lookup(self):
        self.assertEqual(list_collection_files("1", "one", self.root), [])
        file_path = save_upload("1", "one", "notes.md", io.BytesIO(b"one"), 3, self.root)
        save_upload("1", "two", "other.md", io.BytesIO(b"two"), 3, self.root)
        self.assertEqual(list_collection_files("1", "one", self.root), ["notes.md"])
        self.assertEqual(collection_file_path("1", "one", "notes.md", self.root), file_path)
        with self.assertRaises(FileNotFoundError):
            collection_file_path("1", "one", "other.md", self.root)
        with self.assertRaises(ValueError):
            collection_file_path("1", "one", "../notes.md", self.root)
        disable_collection("1", "one", self.root)
        with self.assertRaises(ValueError):
            list_collection_files("1", "one", self.root)

    def test_ascii_name_and_disabled_collection(self):
        name = "English_123"
        self.assertEqual(validate_collection_name(name), name)
        for invalid in ("", "中文", "café", "１23", "a-b", "a.b", "a/b", "a" * 65):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_collection_name(invalid)

        save_upload("1", name, "notes.md", io.BytesIO(b"one"), 3, self.root)
        disable_collection("1", name, self.root)
        self.assertTrue(is_collection_disabled("1", name, self.root))
        self.assertEqual(list(iter_user_documents(self.root)), [])
        with self.assertRaises(ValueError):
            save_upload("1", name, "other.md", io.BytesIO(b"x"), 1, self.root)
        enable_collection("1", name, self.root)
        self.assertFalse(is_collection_disabled("1", name, self.root))
        self.assertEqual(
            [doc_id for doc_id, _, _, _ in iter_user_documents(self.root)],
            ["1/{}/notes.md".format(name)],
        )


if __name__ == "__main__":
    unittest.main()
