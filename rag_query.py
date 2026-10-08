#!/usr/bin/env python3
"""
RAG 检索问答脚本
连接已有的 Milvus 知识库，检索相关内容后调用 DeepSeek LLM 生成回答。
提供检索问答逻辑和命令行模式；HTTP 接口位于 api.py。
"""
import re
import sys
from vector_store import get_vector_store
from index_state import forget_collection_index, forget_document_index
from user_files import (
    validate_userid, validate_collection_name, collection_directory,
    is_collection_disabled, disable_collection, enable_collection,
    collection_file_path, remove_collection_files,
)

# ========== 全局缓存 ==========
_model = None


def get_model():
    """按需加载并缓存嵌入模型。"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        from rag_builder import EMBEDDING_MODEL
        print("🔄 加载嵌入模型...")
        _model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
    return _model


# ========== 检索问答 ==========
def selected_collections(collection_names):
    """Normalize one name or a nonempty list of distinct names."""
    if isinstance(collection_names, str):
        collection_names = [collection_names]
    if not isinstance(collection_names, list) or not collection_names:
        raise ValueError("请至少选择一个集合")
    names = [validate_collection_name(name) for name in collection_names]
    if len(names) != len(set(names)):
        raise ValueError("集合名称不能重复")
    return names


def source_label(item):
    name = item.get("collection_name")
    return f"{name}/{item['filename']}" if name else item["filename"]


def retrieve(question, userid, collection_names, top_k):
    """Embed once, search selected collections, then keep the best global hits."""
    userid = validate_userid(userid)
    names = selected_collections(collection_names)
    for name in names:
        if is_collection_disabled(userid, name):
            raise ValueError(f"集合 {name} 已删除，请重新选择")
    vector = get_model().encode([question], normalize_embeddings=True)[0].tolist()
    store = get_vector_store()
    hits = []
    for name in names:
        for record in store.search(userid, name, vector, top_k):
            hits.append({**record, "collection_name": name})
    return sorted(hits, key=lambda item: item["score"], reverse=True)[:top_k]


def ask(question, userid, collection_names, top_k=3):
    """检索所选集合并调用 LLM 回答"""
    retrieved = retrieve(question, userid, collection_names, top_k)

    if not retrieved:
        return {"answer": "未找到相关文档。", "sources": []}

    context = "\n\n---\n\n".join(
        [f"[{source_label(r)} 第{r['page']}页]\n{r['content']}" for r in retrieved]
    )
    print('context', context)
    prompt = f"""根据以下资料回答问题。如果资料中没有相关信息，请明确说无法回答。

资料：
{context}

问题：{question}

回答："""

    # 警告！请勿删除此打印信息！
    print_prompt = '=' * 20 + '提示词start' + '=' * 20 + '\n' + prompt + '\n' + '=' * 20 + '提示词done' + '=' * 20
    print(print_prompt)

    from deepseek_api import chat_by_deepseek
    answer = chat_by_deepseek(prompt)
    print('answer1::', answer)
    answer = clean(answer)
    print('answer2::', answer)
    return {
        "answer": answer,
        "sources": [(source_label(r), r["page"]) for r in retrieved],
    }


def clean(text):
    """
    删除多余换行符：
    - 将连续 2 个或以上的换行符替换为 1 个换行符
    - 也可以选择去掉开头/结尾的空白换行
    """
    # 将连续 2 个及以上的换行符替换为 1 个换行符
    text = re.sub(r'\n{2,}', '\n', text)
    # 可选：去掉首尾空白（包括换行）
    # text = text.strip()
    return text


# ========== 集合信息 ==========
def show_stats():
    """打印当前 Milvus 集合的条目数量。"""
    store = get_vector_store()
    if not store.exists():
        print(f"集合 {store.name} 不存在")
        return
    print(f"集合名称：{store.name}")
    print(f"条目数量：{store.count()}")


def create_collection_for_user(userid, collection_name):
    """Create a collection, clearing prior deletion remnants on reactivation."""
    directory = collection_directory(userid, collection_name)
    existed = directory.is_dir()
    reactivated = is_collection_disabled(userid, collection_name)
    if reactivated:
        # A previous delete may have stopped after writing the disable marker.
        get_vector_store().delete_scope(userid, collection_name)
        remove_collection_files(userid, collection_name)
        forget_collection_index(userid, collection_name)
    enable_collection(userid, collection_name)
    return not existed or reactivated


def delete_collection_for_user(userid, collection_name):
    """Delete vectors, original files and index entries; retain a disabled marker."""
    disable_collection(userid, collection_name)
    get_vector_store().delete_scope(userid, collection_name)
    remove_collection_files(userid, collection_name)
    forget_collection_index(userid, collection_name)


def delete_file_for_user(userid, collection_name, filename):
    """Delete a source file and its indexed vectors in the selected collection."""
    userid = validate_userid(userid)
    collection_name = validate_collection_name(collection_name)
    path = collection_file_path(userid, collection_name, filename)
    doc_id = f"{userid}/{collection_name}/{filename}"
    store = get_vector_store()
    if store.exists():
        store.delete_document(doc_id)
        store.commit()
    path.unlink()
    forget_document_index(doc_id)


def stream_answer(retrieved, question):
    """Yield cleaned LLM text for an already retrieved question."""
    if not retrieved:
        yield "未找到相关文档。"
        return
    context = "\n\n---\n\n".join(
        f"[{source_label(item)} 第{item['page']}页]\n{item['content']}"
        for item in retrieved
    )
    prompt = f"""根据以下资料回答问题。如果资料中没有相关信息，请明确说无法回答。

资料：
{context}

问题：{question}

回答："""

    from deepseek_api import chat_by_deepseek_stream
    previous = ""
    for token in chat_by_deepseek_stream(prompt):
        current = re.sub(r"\n{3,}", "\n\n", previous + token)
        delta = current[len(previous):]
        previous = current
        if delta:
            yield delta


# ========== 主函数 ==========
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG 检索问答")
    parser.add_argument("question", type=str, nargs="?", default=None, help="要提问的问题")
    parser.add_argument("--userid", default=None, help="检索的字符串用户 ID")
    parser.add_argument("--collection-name", type=str, default=None, help="用户下的集合名称")
    parser.add_argument("--top_k", type=int, default=3, help="检索数量（默认 3）")
    parser.add_argument("--stats", action="store_true", help="显示集合状态信息")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        sys.exit(0)

    question = args.question
    if question is None:
        question = input("请输入问题：")

    if not args.userid or not args.collection_name:
        parser.error("命令行问答必须提供 --userid 和 --collection-name")
    result = ask(question, args.userid, args.collection_name, top_k=args.top_k)
    print(f"\n答案：{result['answer']}")
    print(f"\n来源：")
    for filename, page in result["sources"]:
        print(f"  - {filename} (第{page}页)")
