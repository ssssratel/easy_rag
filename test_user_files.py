"""按用户存储文件的路径与写入测试。"""

import io
import tempfile
import unittest
from pathlib import Path

from user_files import iter_user_documents, save_upload


class UserFileTests(unittest.TestCase):
    """覆盖用户目录隔离与上传文件校验。"""

    def setUp(self):
        """为每个测试创建独立的临时知识库。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_same_filename_is_separate_for_each_user(self):
        """同名文件按用户分别保存。"""
        alice = save_upload("alice", "notes.md", io.BytesIO(b"alice"), 5, self.root)
        bob = save_upload("bob", "notes.md", io.BytesIO(b"bob"), 3, self.root)
        self.assertEqual(alice.read_bytes(), b"alice")
        self.assertEqual(bob.read_bytes(), b"bob")
        self.assertEqual(
            [(doc_id, userid) for doc_id, userid, _ in iter_user_documents(self.root)],
            [("alice/notes.md", "alice"), ("bob/notes.md", "bob")],
        )

    def test_rejects_path_traversal_and_unsupported_files(self):
        """拒绝路径穿越与不支持的文件类型。"""
        for userid, filename in [
            ("../bob", "notes.md"),
            ("alice", "../notes.md"),
            ("alice", "notes.py"),
            ("alice", "..\\notes.md"),
        ]:
            with self.subTest(userid=userid, filename=filename):
                with self.assertRaises(ValueError):
                    save_upload(userid, filename, io.BytesIO(b"x"), 1, self.root)

    def test_incomplete_upload_does_not_replace_existing_file(self):
        """上传中断时保留原文件。"""
        target = save_upload("alice", "notes.md", io.BytesIO(b"old"), 3, self.root)
        with self.assertRaises(ValueError):
            save_upload("alice", "notes.md", io.BytesIO(b"x"), 3, self.root)
        self.assertEqual(target.read_bytes(), b"old")

    def test_scan_ignores_files_outside_user_folder(self):
        """构建扫描只接收用户目录下的直接文件。"""
        (self.root / "legacy.md").write_text("old", encoding="utf-8")
        (self.root / "alice").mkdir()
        (self.root / "alice" / "allowed.md").write_text("new", encoding="utf-8")
        (self.root / "alice" / "nested").mkdir()
        (self.root / "alice" / "nested" / "ignored.md").write_text("nested", encoding="utf-8")
        self.assertEqual(
            [doc_id for doc_id, _, _ in iter_user_documents(self.root)],
            ["alice/allowed.md"],
        )


if __name__ == "__main__":
    unittest.main()
