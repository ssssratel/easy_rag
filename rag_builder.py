#!/usr/bin/env python3
"""
RAG 知识库构建脚本
从 KNOWLEDGE_DIR/<userid>/ 读取文档，自动分块后向量化存入 Milvus。
默认增量构建（只处理变化的文件），--rebuild 全量重建。
"""
import os
import sys
import json
import hashlib
from sentence_transformers import SentenceTransformer
from pymilvus import MilvusClient
from chunk_docs import read_document, split_by_paragraph
from user_files import knowledge_root, iter_user_documents

# ========== 配置 ==========
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
COLLECTION_NAME = "knowledgebase_DST"
KNOWLEDGE_DIR = str(knowledge_root())
# COLLECTION_NAME = "my_knowledge_base"
# KNOWLEDGE_DIR = "/home/ratel/projects/knowledge_base"

EMBEDDING_MODEL = "/home/ratel/models/bge-m3"
INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "milvus_docs", "chunk_index.json")
CHUNKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "milvus_docs", "chunks.json")


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


def file_sha256(filepath):
    """计算文件 SHA256"""
    with open(filepath, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


# ========== Milvus 客户端 ==========
def get_client():
    """建立到 Milvus 的客户端连接。"""
    return MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")


# ========== 构建知识库 ==========
def build(chunks, rebuild=False):
    """全量构建：清空集合后向量化入库"""
    print(f"\n📊 全量构建：加载 {len(chunks)} 个文本块")
    if not chunks:
        print("❌ 没有可处理的文档，退出")
        return

    if any(not c.get("userid") or not c.get("doc_id") for c in chunks):
        raise ValueError("每个 chunk 必须包含 userid 和 doc_id；旧格式数据需重新分块")

    # 1. 向量化
    print("🔄 生成向量中...")
    model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
    texts = [c["content"] for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True)
    dim = embeddings.shape[1]
    print(f"✅ 生成 {len(embeddings)} 个 {dim} 维向量")

    client = get_client()

    # 2. 重建集合
    if rebuild and client.has_collection(COLLECTION_NAME):
        client.drop_collection(COLLECTION_NAME)
        print("🗑️  已清空旧集合")

    if not client.has_collection(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            dimension=dim,
            metric_type="IP",
            auto_id=True,
            enable_dynamic_field=True,
        )
        print(f"📦 创建集合 {COLLECTION_NAME}")

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
            "doc_id": c["doc_id"],
        })
    client.insert(collection_name=COLLECTION_NAME, data=data)
    client.flush(collection_name=COLLECTION_NAME)

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
    if any("/" not in doc_id for doc_id in old_index):
        raise RuntimeError("检测到旧版文件索引；请先按 userid 迁移文件，再运行 --rebuild")

    # 2. 扫描当前文件
    doc_files = list(iter_user_documents())
    if not doc_files and not old_index:
        print(f"⚠️  在 {KNOWLEDGE_DIR}/<userid>/ 中未找到支持的文档文件")
        return

    current_files = {}
    for doc_id, userid, fp in doc_files:
        current_files[doc_id] = {
            "path": str(fp),
            "sha256": file_sha256(fp),
            "userid": userid,
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
    client = get_client()
    if not client.has_collection(COLLECTION_NAME):
        # 首次运行，走全量
        print("📦 首次运行，进行全量构建")
        chunks = _chunk_files(list(current_files.values()))
        build(chunks, rebuild=False)
        _update_index(current_files)
        return

    model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)

    # 6. 删除已移除文件的条目
    for doc_id in removed_files:
        _delete_by_doc_id(client, doc_id)
        print(f"  🗑️  已删除：{doc_id}")

    # 7. 处理修改过的文件（先删旧再插新）
    for doc_id in changed_files:
        _delete_by_doc_id(client, doc_id)
        info = current_files[doc_id]
        _insert_file(client, model, info)
        print(f"  ✏️  已更新：{doc_id}")

    # 8. 处理新文件
    for doc_id in new_files:
        info = current_files[doc_id]
        _insert_file(client, model, info)
        print(f"  ➕ 已新增：{doc_id}")

    client.flush(collection_name=COLLECTION_NAME)

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
                "doc_id": info["doc_id"],
            })
    return all_chunks


def _delete_by_doc_id(client, doc_id):
    """从集合中删除指定用户文档的所有条目；失败时不更新本地索引。"""
    client.delete(
        collection_name=COLLECTION_NAME,
        filter='doc_id == {}'.format(json.dumps(doc_id, ensure_ascii=False)),
    )


def _insert_file(client, model, info):
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
            "doc_id": info["doc_id"],
        })
    client.insert(collection_name=COLLECTION_NAME, data=data)


def _update_index(current_files):
    """保存当前文件索引（去除 path 字段）"""
    clean = {fname: {"sha256": info["sha256"]} for fname, info in current_files.items()}
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
                "userid": userid, "filename": path.name, "doc_id": doc_id,
            }
            for doc_id, userid, path in iter_user_documents()
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
