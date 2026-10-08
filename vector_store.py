"""Vector storage boundary used by indexing and question answering.

Callers work with records containing content, filename, page, userid,
collection_name, doc_id and vector. To switch backends, replace the implementation returned
by get_vector_store(); keep these methods and search result fields stable.
"""

import json
import os
import re
from functools import lru_cache

from user_files import validate_userid, validate_collection_name


class MilvusVectorStore:
    def __init__(self, client=None):
        self.name = os.environ.get("VECTOR_COLLECTION", "knowledgebase_DST")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", self.name):
            raise ValueError("Milvus 物理集合名只支持字母、数字、下划线，且须以字母或下划线开头")
        if client is None:
            from pymilvus import MilvusClient
            client = MilvusClient(uri=os.environ.get("MILVUS_URI", "http://localhost:19530"))
        self.client = client

    def exists(self):
        return self.client.has_collection(self.name)

    def prepare(self, dimension, rebuild=False):
        """Ensure an index of the given dimension exists; return whether it was created."""
        if rebuild and self.exists():
            self.client.drop_collection(self.name)
        if self.exists():
            return False
        self.client.create_collection(
            collection_name=self.name,
            dimension=dimension,
            metric_type="IP",
            auto_id=True,
            enable_dynamic_field=True,
        )
        return True

    def add(self, records):
        """Insert records with vector, content, filename, page, userid, collection_name and doc_id."""
        for record in records:
            validate_userid(record["userid"])
            validate_collection_name(record["collection_name"])
        if records:
            self.client.insert(collection_name=self.name, data=records)

    def delete_document(self, doc_id):
        self.client.delete(
            collection_name=self.name,
            filter="doc_id == {}".format(json.dumps(doc_id, ensure_ascii=False)),
        )

    def delete_scope(self, userid, collection_name):
        """Delete only vectors belonging to one user's logical collection."""
        userid = validate_userid(userid)
        collection_name = validate_collection_name(collection_name)
        if not self.exists():
            return
        self.client.delete(
            collection_name=self.name,
            filter="userid == {} and collection_name == {}".format(
                json.dumps(userid, ensure_ascii=False),
                json.dumps(collection_name, ensure_ascii=False),
            ),
        )
        self.commit()

    def search(self, userid, collection_name, vector, limit):
        """Return matching records as backend-independent dictionaries."""
        userid = validate_userid(userid)
        collection_name = validate_collection_name(collection_name)
        if not self.exists():
            return []
        results = self.client.search(
            collection_name=self.name,
            data=[vector],
            filter="userid == {} and collection_name == {}".format(
                json.dumps(userid, ensure_ascii=False),
                json.dumps(collection_name, ensure_ascii=False),
            ),
            limit=limit,
            output_fields=["content", "filename", "page"],
            search_params={"metric_type": "IP", "params": {"nprobe": 10}},
            consistency_level="Strong",
        )
        return [
            {
                "content": hit.get("entity", {}).get("content", ""),
                "filename": hit.get("entity", {}).get("filename", ""),
                "page": hit.get("entity", {}).get("page", 0),
                "score": hit.get("distance", 0),
            }
            for hits in results for hit in hits
        ]

    def count(self):
        return self.client.get_collection_stats(self.name).get("row_count", "N/A")

    def commit(self):
        """Make a batch of writes visible; other backends may not need this."""
        self.client.flush(collection_name=self.name)


@lru_cache(maxsize=1)
def get_vector_store():
    return MilvusVectorStore()
