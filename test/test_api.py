"""HTTP contract checks for the FastAPI adapter without loading model dependencies."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import api
import index_state
import rag_query


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"KNOWLEDGE_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.index_file = patch.object(
            index_state, "INDEX_FILE", str(Path(self.temp.name) / "chunk_index.json")
        )
        self.index_file.start()
        self.addCleanup(self.index_file.stop)
        self.mock_index = patch.object(api, "index_files", return_value=1)
        self.mock_index.start()
        self.addCleanup(self.mock_index.stop)
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def test_pages_and_collection_list(self):
        (Path(self.temp.name) / "1" / "demo_1").mkdir(parents=True)
        self.assertIn("login_btn", self.client.get("/").text)
        page = self.client.get("/rag_web.html").text
        self.assertIn("current_user", page)
        self.assertIn('id="ask_collections"', page)
        self.assertIn('id="delete_file_btn"', page)
        self.assertNotIn("身份 Token", page)
        response = self.client.get("/api/v1/users/1/collections")
        self.assertEqual(response.json(), {"userid": "1", "collections": ["demo_1"]})
        self.assertEqual(self.client.get("/api/v1/users/alice/collections").status_code, 200)

    def test_login_preserves_auth_response(self):
        with patch.object(api, "login", return_value=(200, {"authenticated": True, "userid": "1"})) as check:
            response = self.client.post("/api/v1/auth/login", json={"userid": "1", "token": "test"})
        self.assertEqual(response.json(), {"authenticated": True, "userid": "1"})
        self.assertEqual(json.loads(check.call_args.args[0])["userid"], "1")

    def test_collection_auth_and_mutations(self):
        path = "/api/v1/users/1/collections/demo_1"
        self.assertEqual(self.client.post(path).status_code, 401)
        with patch.object(api, "authorize_bearer", return_value=200), \
             patch.object(api, "create_collection_for_user", return_value=True), \
             patch.object(api, "delete_collection_for_user"):
            created = self.client.post(path, headers={"Authorization": "Bearer test"})
            deleted = self.client.delete(path, headers={"Authorization": "Bearer test"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["userid"], "1")
        self.assertTrue(deleted.json()["deleted"])

    def test_demo_login_allows_only_user_one_collection_changes(self):
        login = self.client.post("/api/v1/auth/demo-login")
        self.assertEqual(login.json(), {"authenticated": True, "userid": "1"})
        self.assertIn("HttpOnly", login.headers["set-cookie"])
        with patch.object(api, "create_collection_for_user", return_value=True) as create:
            one = self.client.post("/api/v1/users/1/collections/demo_1")
            two = self.client.post("/api/v1/users/2/collections/demo_1")
        self.assertEqual(one.status_code, 201)
        self.assertEqual(two.status_code, 401)
        create.assert_called_once_with("1", "demo_1")
        self.client.cookies.set(api.DEMO_COOKIE, "tampered")
        self.assertEqual(
            self.client.post("/api/v1/users/1/collections/demo_1").status_code, 401
        )

    def test_raw_upload_and_ask_validation(self):
        path = "/api/v1/users/1/collections/demo_1/files/notes.md"
        response = self.client.put(path, content=b"hello", headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(response.status_code, 201)
        file_path = Path(self.temp.name) / "1" / "demo_1" / "notes.md"
        self.assertEqual(file_path.read_bytes(), b"hello")
        listed = self.client.get("/api/v1/users/1/collections/demo_1/files")
        self.assertEqual(listed.json()["files"], ["notes.md"])
        bad = self.client.post("/ask", json={"userid": 1, "collection_names": ["demo_1"], "question": "x"})
        self.assertEqual(bad.status_code, 400)
        empty = self.client.post("/ask", json={"userid": "1", "collection_names": [], "question": "x"})
        self.assertEqual(empty.status_code, 400)
        with patch.object(api, "ask", return_value={"answer": "ok", "sources": []}) as answer:
            good = self.client.post("/ask", json={
                "userid": "1", "collection_names": ["demo_1", "other"], "question": "x"
            })
        self.assertEqual(good.json()["answer"], "ok")
        answer.assert_called_once_with("x", "1", ["demo_1", "other"], 3)
        self.assertEqual(
            self.client.delete("/api/v1/users/1/collections/demo_1/files/notes.md").status_code, 401
        )
        self.client.post("/api/v1/auth/demo-login")
        with patch.object(rag_query, "get_vector_store") as store:
            store.return_value.exists.return_value = False
            deleted = self.client.delete("/api/v1/users/1/collections/demo_1/files/notes.md")
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])
        self.assertFalse(file_path.exists())
        self.assertEqual(self.client.get("/api/v1/users/1/collections/demo_1/files").json()["files"], [])

    def test_demo_collection_and_file_flow(self):
        self.client.post("/api/v1/auth/demo-login")
        base = "/api/v1/users/1/collections/notes"
        store = Mock()
        store.exists.return_value = False
        with patch.object(rag_query, "get_vector_store", return_value=store):
            self.assertEqual(self.client.post(base).status_code, 201)
            self.assertEqual(
                self.client.get("/api/v1/users/1/collections").json()["collections"],
                ["notes"],
            )
            self.assertEqual(
                self.client.put(base + "/files/a.md", content=b"example").status_code,
                201,
            )
            self.assertEqual(self.client.get(base + "/files").json()["files"], ["a.md"])
            self.assertEqual(self.client.delete(base + "/files/a.md").status_code, 200)
            self.assertEqual(self.client.get(base + "/files").json()["files"], [])
            self.assertEqual(self.client.delete(base).status_code, 200)
            self.assertEqual(
                self.client.get("/api/v1/users/1/collections").json()["collections"],
                [],
            )
        store.delete_scope.assert_called_once_with("1", "notes")

    def test_stream_response_contract(self):
        docs = [{"filename": "notes.md", "page": 1, "content": "text",
                 "collection_name": "demo_1"}]
        with patch.object(api, "retrieve", return_value=docs), \
             patch.object(api, "stream_answer", return_value=iter(["hello"])):
            response = self.client.post("/ask_stream", json={
                "userid": "1", "collection_names": ["demo_1"], "question": "x"
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn('"token": "hello"', response.text)
        self.assertIn('"done": true', response.text)
        self.assertIn("demo_1/notes.md", response.text)


if __name__ == "__main__":
    unittest.main()

