"""Lightweight document index state shared by collection management and ingestion."""

import json
import os

from user_files import validate_collection_name, validate_userid


INDEX_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "milvus_docs", "chunk_index.json"
)


def load_index():
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as source:
            return json.load(source)
    return {}


def save_index(index):
    os.makedirs(os.path.dirname(INDEX_FILE), exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as output:
        json.dump(index, output, ensure_ascii=False, indent=2)


def forget_collection_index(userid, collection_name):
    """Remove index entries for a deleted or reset logical collection."""
    userid = validate_userid(userid)
    collection_name = validate_collection_name(collection_name)
    prefix = f"{userid}/{collection_name}/"
    index = load_index()
    retained = {
        doc_id: item for doc_id, item in index.items()
        if not doc_id.startswith(prefix)
    }
    if len(retained) != len(index):
        save_index(retained)




def forget_document_index(doc_id):
    """Ensure a deleted file is reindexed if the same content is uploaded again."""
    index = load_index()
    if doc_id in index:
        del index[doc_id]
        save_index(index)
