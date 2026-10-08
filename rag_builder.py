#!/usr/bin/env python3
"""Build and update the shared vector collection from user documents."""
import argparse
import hashlib
import os
from pathlib import Path
from functools import lru_cache

from chunk_docs import read_document, split_by_paragraph
from index_state import load_index, save_index
from user_files import (
    collection_file_path, iter_user_documents, validate_collection_name,
    validate_filename, validate_userid,
)
from vector_store import get_vector_store

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "/home/ratel/models/bge-m3")
INDEX_SCHEMA = "string_userid_v3"


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)


def _chunks(files):
    chunks = []
    for doc_id, userid, collection_name, path in files:
        text = read_document(str(path))
        parts = split_by_paragraph(text) if text.strip() else []
        if not parts:
            raise ValueError(f"文件没有可向量化的文本：{path.name}")
        for page, content in enumerate(parts, 1):
            chunks.append({
                "content": content, "filename": path.name, "page": page,
                "userid": userid, "collection_name": collection_name, "doc_id": doc_id,
            })
    return chunks


def _write_vectors(files, *, rebuild=False):
    """Encode all selected files in one model call, then replace their vectors."""
    if not files:
        return 0
    chunks = _chunks(files)
    embeddings = _model().encode(
        [chunk["content"] for chunk in chunks], normalize_embeddings=True
    )
    store = get_vector_store()
    store.prepare(len(embeddings[0]), rebuild=rebuild)
    if not rebuild:
        for doc_id, _, _, _ in files:
            store.delete_document(doc_id)
    records = [
        {**chunk, "vector": embedding.tolist()}
        for chunk, embedding in zip(chunks, embeddings)
    ]
    store.add(records)
    store.commit()
    return len(records)


def index_files(userid, collection_name, filenames):
    """Synchronously vectorize a batch of uploaded files in one collection."""
    userid = validate_userid(userid)
    collection_name = validate_collection_name(collection_name)
    if not filenames or len(filenames) != len(set(filenames)):
        raise ValueError("文件列表不能为空或包含重复文件名")
    files = []
    for filename in filenames:
        filename = validate_filename(filename)
        path = collection_file_path(userid, collection_name, filename)
        files.append((f"{userid}/{collection_name}/{filename}", userid, collection_name, path))
    count = _write_vectors(files)
    index = load_index()
    for doc_id, _, _, path in files:
        index[doc_id] = {"sha256": file_sha256(path), "schema": INDEX_SCHEMA}
    save_index(index)
    return count


def incremental_build():
    """Index changed documents and remove vectors of deleted documents."""
    current = {doc_id: (doc_id, userid, name, path)
               for doc_id, userid, name, path in iter_user_documents()}
    old = load_index()
    if any(not isinstance(info, dict) or info.get("schema") != INDEX_SCHEMA
           for info in old.values()):
        raise RuntimeError("检测到旧索引，请备份后运行 --rebuild")
    changed = [item for doc_id, item in current.items()
               if doc_id not in old or old[doc_id]["sha256"] != file_sha256(item[3])]
    removed = set(old) - set(current)
    if changed:
        _write_vectors(changed)
    if removed:
        store = get_vector_store()
        if store.exists():
            for doc_id in removed:
                store.delete_document(doc_id)
            store.commit()
    if changed or removed:
        index = {doc_id: info for doc_id, info in old.items() if doc_id not in removed}
        for doc_id, _, _, path in changed:
            index[doc_id] = {"sha256": file_sha256(path), "schema": INDEX_SCHEMA}
        save_index(index)
    return len(changed)


def rebuild():
    """Recreate the physical vector collection and index every current document."""
    files = list(iter_user_documents())
    if not files:
        raise ValueError("没有可向量化的文档")
    _write_vectors(files, rebuild=True)
    save_index({doc_id: {"sha256": file_sha256(path), "schema": INDEX_SCHEMA}
                for doc_id, _, _, path in files})
    return len(files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 知识库构建")
    parser.add_argument("--rebuild", action="store_true", help="清空共享向量集合并重建")
    args = parser.parse_args()
    print(f"已处理 {rebuild() if args.rebuild else incremental_build()} 个文件")
