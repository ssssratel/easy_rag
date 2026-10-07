#!/usr/bin/env python3
"""
RAG 检索问答脚本
连接已有的 Milvus 知识库，检索相关内容后调用 DeepSeek LLM 生成回答。
支持命令行模式和 Web 服务模式（--serve）。
"""
import json
import os
import re
import sys
from urllib.parse import unquote, urlsplit
from sentence_transformers import SentenceTransformer
from pymilvus import MilvusClient
from deepseek_api import chat_by_deepseek, chat_by_deepseek_stream
from rag_builder import COLLECTION_NAME, EMBEDDING_MODEL
from auth_api import MAX_LOGIN_BODY_BYTES, login
from user_files import MAX_UPLOAD_BYTES, save_upload, validate_userid

# ========== 配置 ==========
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"

# ========== 全局缓存 ==========
_model = None
_client = None


def get_model():
    """按需加载并缓存嵌入模型。"""
    global _model
    if _model is None:
        print("🔄 加载嵌入模型...")
        _model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
    return _model


def get_client():
    """按需建立并缓存 Milvus 客户端。"""
    global _client
    if _client is None:
        _client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
    return _client


# ========== 检索问答 ==========
def ask(question, userid, top_k=3):
    """检索知识库并调用 LLM 回答"""
    userid = validate_userid(userid)
    client = get_client()
    model = get_model()

    query_embedding = model.encode([question], normalize_embeddings=True)

    results = client.search(
        collection_name=COLLECTION_NAME,
        data=query_embedding.tolist(),
        filter='userid == "{}"'.format(userid),
        limit=top_k,
        output_fields=["content", "filename", "page"],
        search_params={"metric_type": "IP", "params": {"nprobe": 10}},
    )

    retrieved = []
    for hits in results:
        for hit in hits:
            entity = hit.get("entity", {})
            retrieved.append({
                "content": entity.get("content", ""),
                "filename": entity.get("filename", ""),
                "page": entity.get("page", 0),
                "score": hit.get("distance", 0),
            })

    if not retrieved:
        return {"answer": "未找到相关文档。", "sources": []}

    context = "\n\n---\n\n".join(
        [f"[{r['filename']} 第{r['page']}页]\n{r['content']}" for r in retrieved]
    )

    prompt = f"""根据以下资料回答问题。如果资料中没有相关信息，请明确说无法回答。

资料：
{context}

问题：{question}

回答："""

    # 警告！请勿删除此打印信息！
    print_prompt = '=' * 20 + '提示词start' + '=' * 20 + '\n' + prompt + '\n' + '=' * 20 + '提示词done' + '=' * 20
    print(print_prompt)

    answer = chat_by_deepseek(prompt)
    answer = clean(answer)
    return {
        "answer": answer,
        "sources": [(r["filename"], r["page"]) for r in retrieved],
    }


def parse_ask_request(data):
    """Validate the user scope before any Milvus search."""
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    userid = validate_userid(data.get("userid"))
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question 不能为空")
    top_k = data.get("top_k", 3)
    if type(top_k) is not int or not 1 <= top_k <= 10:
        raise ValueError("top_k 必须是 1 到 10 的整数")
    return userid, question.strip(), top_k


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
    client = get_client()
    if not client.has_collection(COLLECTION_NAME):
        print(f"集合 {COLLECTION_NAME} 不存在")
        return
    stats = client.get_collection_stats(COLLECTION_NAME)
    print(f"集合名称：{COLLECTION_NAME}")
    print(f"条目数量：{stats.get('row_count', 'N/A')}")


# ========== Web 服务 ==========
# HTML 模板在 rag_web.html 中，启动时自动加载


def serve(port=8080):
    """启动问答、登录和文件上传 HTTP 服务。"""
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

    # 读取 HTML 模板文件
    html_file = os.path.join(os.path.dirname(__file__), "rag_web.html")
    with open(html_file, 'r', encoding='utf-8') as f:
        html_content = f.read()

    class Handler(BaseHTTPRequestHandler):
        """处理 Web 页面及 REST 请求。"""
        def do_GET(self):
            """返回问答页面。"""
            if self.path in ('/', '/index.html'):
                self._respond_html(html_content)
            else:
                self.send_error(404)

        def do_POST(self):
            """分发登录、普通问答和流式问答请求。"""
            if self.path == '/api/v1/auth/login':
                if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                    self._respond_json({"error": "Content-Type 必须是 application/json"}, 415)
                    return
                try:
                    length = int(self.headers.get('Content-Length', ''))
                except ValueError:
                    self._respond_json({"error": "Content-Length 无效"}, 400)
                    return
                if length < 0:
                    self._respond_json({"error": "Content-Length 无效"}, 400)
                    return
                if length > MAX_LOGIN_BODY_BYTES:
                    self._respond_json({"error": "请求体过大"}, 413)
                    return
                status, payload = login(self.rfile.read(length))
                self._respond_json(payload, status)
            elif self.path == '/ask':
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    body = self.rfile.read(length)
                    data = json.loads(body)
                    userid, question, top_k = parse_ask_request(data)
                    print(f"收到用户 {userid} 的问题：{question}")
                    result = ask(question, userid, top_k=top_k)
                    self._respond_json(result)
                except ValueError as e:
                    self._respond_json({"error": str(e)}, 400)
                except Exception as e:
                    self._respond_json({"error": str(e)}, 500)
            elif self.path == '/ask_stream':
                self._handle_ask_stream()
            else:
                self.send_error(404)

        def do_PUT(self):
            """接收文件并写入 userid 对应目录。"""
            # PUT /api/v1/users/{userid}/files/{filename}
            parts = urlsplit(self.path).path.split('/')
            if (len(parts) != 7 or parts[1:4] != ['api', 'v1', 'users']
                    or parts[5] != 'files'):
                self.send_error(404)
                return
            userid, filename = unquote(parts[4]), unquote(parts[6])
            try:
                length = int(self.headers.get('Content-Length', ''))
            except ValueError:
                self._respond_json({"error": "Content-Length 无效"}, 400)
                return
            if length > MAX_UPLOAD_BYTES:
                self._respond_json({"error": "文件超过 20 MiB 限制"}, 413)
                return
            try:
                target = save_upload(userid, filename, self.rfile, length)
            except ValueError as exc:
                self._respond_json({"error": str(exc)}, 400)
                return
            except OSError:
                self._respond_json({"error": "文件保存失败"}, 500)
                return
            self._respond_json({
                "userid": userid,
                "filename": target.name,
                "path": "{}/{}".format(userid, target.name),
                "size": length,
            }, 201)

        def _respond_html(self, html, status=200):
            """发送 HTML 响应。"""
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode())

        def _respond_json(self, data, status=200):
            """发送 JSON 响应及状态码。"""
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

        def _handle_ask_stream(self):
            """SSE 流式端点：先检索，再逐 token 推送 LLM 输出"""
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                data = json.loads(body)
                userid, question, top_k = parse_ask_request(data)
                print(f"收到用户 {userid} 的流式问题：{question}")

                # 检索
                client = get_client()
                model = get_model()
                query_embedding = model.encode([question], normalize_embeddings=True)
                results = client.search(
                    collection_name=COLLECTION_NAME,
                    data=query_embedding.tolist(),
                    filter='userid == "{}"'.format(userid),
                    limit=top_k,
                    output_fields=["content", "filename", "page"],
                    search_params={"metric_type": "IP", "params": {"nprobe": 10}},
                )
                retrieved = []
                for hits in results:
                    for hit in hits:
                        entity = hit.get("entity", {})
                        retrieved.append({
                            "content": entity.get("content", ""),
                            "filename": entity.get("filename", ""),
                            "page": entity.get("page", 0),
                        })
                sources = [(r["filename"], r["page"]) for r in retrieved]

                # 拼接 prompt
                if retrieved:
                    context = "\n\n---\n\n".join(
                        [f"[{r['filename']} 第{r['page']}页]\n{r['content']}"
                         for r in retrieved]
                    )
                else:
                    context = "暂无相关资料"
                prompt = f"""根据以下资料回答问题。如果资料中没有相关信息，请明确说无法回答。

资料：
{context}

问题：{question}

回答："""

                # SSE 响应头
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('X-Accel-Buffering', 'no')
                self.end_headers()

                def _write_sse(obj):
                    """向客户端写入一条 SSE 数据消息。"""
                    line = json.dumps(obj, ensure_ascii=False)
                    self.wfile.write(f"data: {line}\n\n".encode())
                    self.wfile.flush()

                # 流式调用（缓冲区压缩多余换行符）
                prev_text = ""
                for token in chat_by_deepseek_stream(prompt):
                    current = prev_text + token
                    current = re.sub(r'\n{3,}', '\n\n', current)
                    delta = current[len(prev_text):]
                    prev_text = current
                    if delta:
                        _write_sse({"token": delta})

                _write_sse({"done": True, "sources": sources})
            except ValueError as e:
                self._respond_json({"error": str(e)}, 400)
            except Exception as e:
                try:
                    _write_sse({"error": str(e)})
                except Exception:
                    pass

        def log_message(self, format, *args):
            """将 HTTP 访问记录输出到控制台。"""
            print(f"  {args[0]}")

    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f"\nWeb 服务已启动：http://localhost:{port}")
    print("按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()


# ========== 主函数 ==========
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG 检索问答")
    parser.add_argument("question", type=str, nargs="?", default=None, help="要提问的问题")
    parser.add_argument("--userid", type=str, default=None, help="检索的用户 ID")
    parser.add_argument("--top_k", type=int, default=3, help="检索数量（默认 3）")
    parser.add_argument("--serve", action="store_true", help="启动 Web 服务")
    parser.add_argument("--port", type=int, default=8080, help="Web 端口（默认 8080）")
    parser.add_argument("--stats", action="store_true", help="显示集合状态信息")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        sys.exit(0)

    if args.serve:
        serve(port=args.port)
        sys.exit(0)

    question = args.question
    if question is None:
        question = input("请输入问题：")

    if not args.userid:
        parser.error("命令行问答必须提供 --userid")
    result = ask(question, args.userid, top_k=args.top_k)
    print(f"\n答案：{result['answer']}")
    print(f"\n来源：")
    for filename, page in result["sources"]:
        print(f"  - {filename} (第{page}页)")
