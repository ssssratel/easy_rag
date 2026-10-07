#!/usr/bin/env python3
"""
RAG 知识库构建脚本
从 KNOWLEDGE_DIR/<userid>/<collection_name>/ 读取文档并向量化入库。
默认增量构建（只处理变化的文件），--rebuild 全量重建。
"""
import os
import sys
import json
import hashlib
from sentence_transformers import SentenceTransformer
from vector_store import get_vector_store
from chunk_docs import read_document, split_by_paragraph
from user_files import knowledge_root, iter_user_documents, validate_userid, validate_collection_name

# ========== 配置 ==========
KNOWLEDGE_DIR = str(knowledge_root())
# KNOWLEDGE_DIR = "/home/ratel/projects/knowledge_base"

EMBEDDING_MODEL = "/home/ratel/models/bge-m3"
INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "milvus_docs", "chunk_index.json")
CHUNKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "milvus_docs", "chunks.json")
INDEX_SCHEMA = "collection_name_v1"


# ========== 索引管理 ==========
def load_index():
    """加载文件索引 {filename: {"sha256": ..., "mtime": ...}}"""
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_index(index):
    """将文件哈希索引写入本地 JSON。"""
    os.makedirs(os.path.dirname(INDEX_FILE), exist_ok=True)
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def forget_collection_index(userid, collection_name):
    """Make retained files eligible for indexing after a deleted collection is restored."""
    userid = validate_userid(userid)
    collection_name = validate_collection_name(collection_name)
    prefix = "{}/{}/".format(userid, collection_name)
    index = load_index()
    retained = {doc_id: item for doc_id, item in index.items()
                if not doc_id.startswith(prefix)}
    if len(retained) != len(index):
        save_index(retained)


def file_sha256(filepath):
    """计算文件 SHA256"""
    with open(filepath, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


# ========== 构建知识库 ==========
def build(chunks, rebuild=False):
    """全量构建：清空集合后向量化入库"""
    print(f"\n📊 全量构建：加载 {len(chunks)} 个文本块")
    if not chunks:
        print("❌ 没有可处理的文档，退出")
        return

    if any(not c.get("doc_id") or "collection_name" not in c for c in chunks):
        raise ValueError("每个 chunk 必须包含 userid、collection_name 和 doc_id；旧格式数据需重新分块")
    for chunk in chunks:
        validate_userid(chunk.get("userid"))
        validate_collection_name(chunk["collection_name"])

    # 1. 向量化
    print("🔄 生成向量中...")
    model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
    texts = [c["content"] for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True)
    dim = embeddings.shape[1]
    print(f"✅ 生成 {len(embeddings)} 个 {dim} 维向量")

    store = get_vector_store()

    # 2. 准备向量索引
    if store.prepare(dim, rebuild=rebuild):
        print(f"📦 创建集合 {store.name}")

    # 3. 插入数据
    data = []
    for i, c in enumerate(chunks):
        data.append({
            # "id": i,
            "content": c["content"],
            "vector": embeddings[i].tolist(),
            "filename": c.get("filename", ""),
            "page": c.get("page", 0),
            "userid": c["userid"],
            "collection_name": c["collection_name"],
            "doc_id": c["doc_id"],
        })
    store.add(data)
    store.commit()

    # 保存 chunks 到 JSON 文件
    os.makedirs(os.path.dirname(CHUNKS_FILE), exist_ok=True)
    with open(CHUNKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"📄 chunks 已导出到 {CHUNKS_FILE}")

    print(f"✅ 全量构建完成！")


def incremental_build():
    """
    增量构建：
    - 新文件 → 分块 + 插入
    - 修改过的文件 → 删旧 + 分块 + 插入
    - 删除的文件 → 从集合移除
    - 未变化的文件 → 跳过
    """
    print(f"\n📂 知识库路径：{KNOWLEDGE_DIR}")

    # 1. 加载历史索引
    old_index = load_index()
    if any(len(doc_id.split("/")) != 3 for doc_id in old_index):
        raise RuntimeError("检测到缺少集合名称的旧索引；请备份数据后运行 --rebuild")
    if any(not isinstance(info, dict) or info.get("schema") != INDEX_SCHEMA
           for info in old_index.values()):
        raise RuntimeError("检测到旧 collection_id 索引；请备份数据后运行 --rebuild")

    # 2. 扫描当前文件
    doc_files = list(iter_user_documents())
    if not doc_files and not old_index:
        print(f"⚠️  在 {KNOWLEDGE_DIR}/<userid>/<collection_name>/ 中未找到支持的文档文件")
        return

    current_files = {}
    for doc_id, userid, collection_name, fp in doc_files:
        current_files[doc_id] = {
            "path": str(fp),
            "sha256": file_sha256(fp),
            "userid": userid,
            "collection_name": collection_name,
            "filename": fp.name,
            "doc_id": doc_id,
        }

    # 3. 对比差异
    new_files = []       # 新文件
    changed_files = []   # 修改过的
    unchanged = []       # 未变化
    removed_files = []   # 已删除

    for fname, info in current_files.items():
        if fname not in old_index:
            new_files.append(fname)
        elif old_index[fname]["sha256"] != info["sha256"]:
            changed_files.append(fname)
        else:
            unchanged.append(fname)

    for fname in old_index:
        if fname not in current_files:
            removed_files.append(fname)

    # 4. 如果没有变化，直接退出
    if not new_files and not changed_files and not removed_files:
        print("✅ 所有文件均未变化，无需更新")
        return

    print(f"📋 变更摘要：新增 {len(new_files)} | 修改 {len(changed_files)} | "
          f"删除 {len(removed_files)} | 未变 {len(unchanged)}")

    if new_files:
        print(f"\n  🆕 新增文件 ({len(new_files)} 个)：")
        for f in new_files:
            print(f"     - {f}")
    if changed_files:
        print(f"\n  ✏️  修改文件 ({len(changed_files)} 个)：")
        for f in changed_files:
            print(f"     - {f}")
    if removed_files:
        print(f"\n  🗑️  删除文件 ({len(removed_files)} 个)：")
        for f in removed_files:
            print(f"     - {f}")
    print()

    # 5. 确保集合存在
    store = get_vector_store()
    if not store.exists():
        # 首次运行，走全量
        print("📦 首次运行，进行全量构建")
        chunks = _chunk_files(list(current_files.values()))
        build(chunks, rebuild=False)
        _update_index(current_files)
        return

    model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)

    # 6. 删除已移除文件的条目
    for doc_id in removed_files:
        store.delete_document(doc_id)
        print(f"  🗑️  已删除：{doc_id}")

    # 7. 处理修改过的文件（先删旧再插新）
    for doc_id in changed_files:
        store.delete_document(doc_id)
        info = current_files[doc_id]
        _insert_file(store, model, info)
        print(f"  ✏️  已更新：{doc_id}")

    # 8. 处理新文件
    for doc_id in new_files:
        info = current_files[doc_id]
        _insert_file(store, model, info)
        print(f"  ➕ 已新增：{doc_id}")

    store.commit()

    # 9. 更新索引
    _update_index(current_files)

    print(f"✅ 增量构建完成！")


# ========== 辅助函数 ==========
def _chunk_files(file_infos):
    """对文件列表分块，返回 chunk 列表"""
    all_chunks = []
    for info in file_infos:
        text = read_document(info["path"])
        if not text.strip():
            continue
        chunks = split_by_paragraph(text)
        for i, chunk in enumerate(chunks):
            all_chunks.append({
                "content": chunk,
                "filename": info["filename"],
                "page": i + 1,
                "userid": info["userid"],
                "collection_name": info["collection_name"],
                "doc_id": info["doc_id"],
            })
    return all_chunks


def _insert_file(store, model, info):
    """分块并插入单个文件"""
    text = read_document(info["path"])
    if not text.strip():
        return

    chunks = split_by_paragraph(text)
    texts = [c for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True)

    data = []
    for i, chunk in enumerate(chunks):
        data.append({
            "content": chunk,
            "vector": embeddings[i].tolist(),
            "filename": info["filename"],
            "page": i + 1,
            "userid": info["userid"],
            "collection_name": info["collection_name"],
            "doc_id": info["doc_id"],
        })
    store.add(data)


def _update_index(current_files):
    """保存当前文件索引（去除 path 字段）"""
    clean = {fname: {"sha256": info["sha256"], "schema": INDEX_SCHEMA}
             for fname, info in current_files.items()}
    save_index(clean)


# ========== 兼容旧接口 ==========
def load_chunks(data_path):
    """从 JSON/parquet 文件加载已有的 chunks（备用）"""
    if data_path.endswith('.json'):
        with open(data_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    elif data_path.endswith('.parquet'):
        import pandas as pd
        df = pd.read_parquet(data_path)
        return df.to_dict('records')
    else:
        return data_path


# ========== 主函数 ==========
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="RAG 知识库构建（默认增量，--rebuild 全量）"
    )
    parser.add_argument("--build", action="store_true",
                        help="增量构建（默认行为）")
    parser.add_argument("--rebuild", action="store_true",
                        help="全量重建（清空集合后重新入库）")
    parser.add_argument("--data", type=str, default=None,
                        help="从已有的 chunks JSON 文件全量构建")
    args = parser.parse_args()

    # 无参数 或 --build → 增量
    if (len(sys.argv) == 1 or args.build) and not args.rebuild and not args.data:
        incremental_build()
    elif args.rebuild:
        print(f"📂 知识库路径：{KNOWLEDGE_DIR}")
        current_files = {
            doc_id: {
                "path": str(path), "sha256": file_sha256(path),
                "userid": userid, "collection_name": collection_name,
                "filename": path.name, "doc_id": doc_id,
            }
            for doc_id, userid, collection_name, path in iter_user_documents()
        }
        chunks = _chunk_files(list(current_files.values()))
        build(chunks, rebuild=True)
        if chunks:
            _update_index(current_files)
    elif args.data:
        chunks = load_chunks(args.data)
        build(chunks, rebuild=True)
    else:
        print("用法：")
        print("  python rag_builder.py            # 增量构建（默认）")
        print("  python rag_builder.py --build    # 同上")
        print("  python rag_builder.py --rebuild  # 全量重建")
        print("  python rag_builder.py --data chunks.json  # 从 JSON 全量构建")
