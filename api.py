"""FastAPI HTTP entry point. Run: python api.py --port 8080."""

import argparse
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from tempfile import SpooledTemporaryFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from auth_api import MAX_LOGIN_BODY_BYTES, authorize_bearer, login
from rag_query import (
    ask, create_collection_for_user, delete_collection_for_user,
    delete_file_for_user, retrieve, source_label, stream_answer,
)
from user_files import (
    MAX_UPLOAD_BYTES, list_collection_files, list_user_collections, save_upload,
    validate_collection_name, validate_filename, validate_userid,
    UploadConflict, ensure_upload_names_available,
)


app = FastAPI(title="RAG 知识问答")
PAGE_DIR = Path(__file__).resolve().parent
DEMO_USER_ID = "1"
DEMO_COOKIE = "rag_demo_session"
DEMO_SESSION_SECONDS = 12 * 60 * 60
_DEMO_SECRET = secrets.token_bytes(32)


@app.exception_handler(HTTPException)
async def http_error(_request, exc):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


def fail(message, status=400):
    raise HTTPException(status_code=status, detail=message)


def user_id(value):
    try:
        return validate_userid(value)
    except ValueError as exc:
        fail(str(exc))


def collection(value):
    try:
        return validate_collection_name(value)
    except ValueError as exc:
        fail(str(exc))


def ask_parameters(data):
    if not isinstance(data, dict):
        fail("请求体必须是 JSON 对象")
    userid = user_id(data.get("userid"))
    names = data.get("collection_names")
    if names is None and "collection_name" in data:
        names = [data["collection_name"]]  # Existing single-collection clients.
    if not isinstance(names, list) or not names:
        fail("请至少选择一个集合")
    names = [collection(name) for name in names]
    if len(names) != len(set(names)):
        fail("集合名称不能重复")
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        fail("question 不能为空")
    top_k = data.get("top_k", 3)
    if type(top_k) is not int or not 1 <= top_k <= 10:
        fail("top_k 必须是 1 到 10 的整数")
    return userid, names, question.strip(), top_k



def upload_conflict_response(userid, name, conflict):
    """Return an ordinary response for an upload rejected due to existing files."""
    return JSONResponse({
        "userid": userid, "collection_name": name,
        "uploaded": False, "indexed": False, "code": "FILE_EXISTS",
        "message": str(conflict), "existing_files": conflict.filenames,
    }, status_code=200)


def content_length(request, required=False):
    raw = request.headers.get("content-length")
    if raw is None:
        if required:
            fail("Content-Length 无效")
        return None
    try:
        length = int(raw)
    except ValueError:
        fail("Content-Length 无效")
    if length < 0:
        fail("Content-Length 无效")
    return length


async def bounded_body(request, limit):
    declared = content_length(request)
    if declared is not None and declared > limit:
        fail("请求体过大", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            fail("请求体过大", 413)
    return bytes(body)


async def json_request(request):
    try:
        return json.loads(await request.body())
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("请求体必须是 JSON")


def demo_session_user(request):
    value = request.cookies.get(DEMO_COOKIE, "")
    parts = value.split(".")
    if len(parts) != 2:
        return None
    if not parts[0].isascii() or not parts[0].isdigit():
        return None
    try:
        issued = int(parts[0])
    except ValueError:
        return None
    if not 0 <= time.time() - issued < DEMO_SESSION_SECONDS:
        return None
    signature = hmac.new(_DEMO_SECRET, parts[0].encode("ascii"), hashlib.sha256).hexdigest()
    return DEMO_USER_ID if hmac.compare_digest(signature, parts[1]) else None


def authorized_scope(request, userid, collection_name):
    userid = user_id(userid)
    name = collection(collection_name)
    if userid == DEMO_USER_ID and demo_session_user(request) == userid:
        return userid, name
    status = authorize_bearer(request.headers.get("authorization"), userid)
    if status != 200:
        fail("认证服务暂不可用" if status == 503 else "无权操作该用户的集合", status)
    return userid, name


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
@app.get("/login.html", include_in_schema=False)
def login_page():
    return FileResponse(PAGE_DIR / "login.html", media_type="text/html")


@app.get("/rag_web.html", include_in_schema=False)
def rag_page():
    return FileResponse(PAGE_DIR / "rag_web.html", media_type="text/html")


@app.post("/api/v1/auth/demo-login")
def demo_login(request: Request):
    issued = str(int(time.time()))
    signature = hmac.new(_DEMO_SECRET, issued.encode("ascii"), hashlib.sha256).hexdigest()
    response = JSONResponse({"authenticated": True, "userid": DEMO_USER_ID})
    response.set_cookie(
        DEMO_COOKIE, f"{issued}.{signature}", max_age=DEMO_SESSION_SECONDS,
        httponly=True, samesite="lax", secure=request.url.scheme == "https",
    )
    return response


@app.post("/api/v1/auth/login")
async def authenticate(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        fail("Content-Type 必须是 application/json", 415)
    declared = content_length(request, required=True)
    if declared > MAX_LOGIN_BODY_BYTES:
        fail("请求体过大", 413)
    body = await bounded_body(request, MAX_LOGIN_BODY_BYTES)
    if len(body) != declared:
        fail("Content-Length 无效")
    status, payload = await run_in_threadpool(login, body)
    return JSONResponse(payload, status_code=status)


@app.get("/api/v1/users/{userid}/collections")
def list_collections(userid: str):
    userid = user_id(userid)
    try:
        names = list_user_collections(userid)
    except ValueError as exc:
        fail(str(exc))
    return {"userid": userid, "collections": names}


@app.post("/api/v1/users/{userid}/collections/{collection_name}")
async def create_collection(userid: str, collection_name: str, request: Request):
    userid, name = authorized_scope(request, userid, collection_name)
    try:
        created = await run_in_threadpool(create_collection_for_user, userid, name)
    except Exception as exc:
        raise HTTPException(500, "创建集合失败；请重试") from exc
    return JSONResponse(
        {"userid": userid, "collection_name": name, "created": created},
        status_code=201 if created else 200,
    )


@app.delete("/api/v1/users/{userid}/collections/{collection_name}")
async def delete_collection(userid: str, collection_name: str, request: Request):
    userid, name = authorized_scope(request, userid, collection_name)
    try:
        await run_in_threadpool(delete_collection_for_user, userid, name)
    except Exception as exc:
        raise HTTPException(500, "删除集合文件或向量失败；请重试") from exc
    return {"userid": userid, "collection_name": name, "deleted": True, "files_preserved": False, "files_deleted": True}


@app.get("/api/v1/users/{userid}/collections/{collection_name}/files")
def files_for_collection(userid: str, collection_name: str):
    userid = user_id(userid)
    name = collection(collection_name)
    try:
        filenames = list_collection_files(userid, name)
    except ValueError as exc:
        fail(str(exc))
    except OSError as exc:
        raise HTTPException(500, "读取文件列表失败") from exc
    return {"userid": userid, "collection_name": name, "files": filenames}


@app.delete("/api/v1/users/{userid}/collections/{collection_name}/files/{filename}")
async def delete_file(userid: str, collection_name: str, filename: str, request: Request):
    userid, name = authorized_scope(request, userid, collection_name)
    try:
        validate_filename(filename)
        await run_in_threadpool(delete_file_for_user, userid, name, filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, "文件不存在") from exc
    except ValueError as exc:
        fail(str(exc))
    except Exception as exc:
        raise HTTPException(500, "删除文件失败；请重试") from exc
    return {"userid": userid, "collection_name": name, "filename": filename, "deleted": True}

from rag_builder import index_files
@app.put("/api/v1/users/{userid}/collections/{collection_name}/files/{filename}")
async def upload_file(userid: str, collection_name: str, filename: str, request: Request):
    userid = user_id(userid)
    name = collection(collection_name)
    try:
        validate_filename(filename)
        ensure_upload_names_available(userid, name, [filename])
    except UploadConflict as exc:
        return upload_conflict_response(userid, name, exc)
    except ValueError as exc:
        fail(str(exc))
    declared = content_length(request, required=True)
    if declared > MAX_UPLOAD_BYTES:
        fail("文件超过 20 MiB 限制", 413)
    with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as source:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                fail("文件超过 20 MiB 限制", 413)
            source.write(chunk)
        if size != declared:
            fail("Content-Length 无效")
        source.seek(0)
        try:
            target = await run_in_threadpool(save_upload, userid, name, filename, source, size)
            chunks = await run_in_threadpool(index_files, userid, name, [filename])
        except UploadConflict as exc:
            return upload_conflict_response(userid, name, exc)
        except ValueError as exc:
            fail(str(exc))
        except Exception as exc:
            raise HTTPException(500, "上传或向量化失败；请检查文件状态，删除已保存文件后重试") from exc
    return JSONResponse({
        "userid": userid, "collection_name": name, "filename": target.name,
        "path": f"{userid}/{name}/{target.name}", "size": size, "uploaded": True, "indexed": True, "chunks": chunks,
    }, status_code=201)


@app.post("/api/v1/users/{userid}/collections/{collection_name}/files/batch")
async def upload_files_batch(userid: str, collection_name: str, request: Request):
    """Save all selected files and finish one embedding batch before success."""
    userid = user_id(userid)
    name = collection(collection_name)
    saved_paths = []
    try:
        form = await request.form(max_files=20, max_fields=0)
        uploads = form.getlist("files")
        if not uploads or len(uploads) > 20:
            fail("请选择 1 到 20 个文件")
        names = []
        for upload in uploads:
            if not hasattr(upload, "file") or not upload.filename:
                fail("files 必须是文件")
            names.append(validate_filename(upload.filename))
            upload.file.seek(0, 2)
            size = upload.file.tell()
            upload.file.seek(0)
            if size > MAX_UPLOAD_BYTES:
                fail("文件超过 20 MiB 限制", 413)
        if len(names) != len(set(names)):
            fail("文件名不能重复")
        ensure_upload_names_available(userid, name, names)
        saved = []
        for upload, filename in zip(uploads, names):
            upload.file.seek(0, 2)
            size = upload.file.tell()
            upload.file.seek(0)
            target = await run_in_threadpool(save_upload, userid, name, filename, upload.file, size)
            saved_paths.append(target)
            saved.append({"filename": filename, "size": size})
        chunks = await run_in_threadpool(index_files, userid, name, names)
    except UploadConflict as exc:
        # A concurrent writer may create a filename after the preflight check.
        for path in saved_paths:
            path.unlink(missing_ok=True)
        return upload_conflict_response(userid, name, exc)
    except HTTPException:
        raise
    except ValueError as exc:
        fail(str(exc))
    except Exception as exc:
        raise HTTPException(500, "上传或向量化失败；请检查文件状态，删除已保存文件后重试") from exc
    finally:
        if "form" in locals():
            await form.close()
    return JSONResponse({
        "userid": userid, "collection_name": name, "files": saved,
        "uploaded": True, "indexed": True, "chunks": chunks,
    }, status_code=201)


@app.post("/ask")
async def ask_question(request: Request):
    userid, names, question, top_k = ask_parameters(await json_request(request))
    print(f"收到用户 {userid} 的集合 {names} 的问题：{question}")
    try:
        return await run_in_threadpool(ask, question, userid, names, top_k)
    except ValueError as exc:
        fail(str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/ask_stream")
async def ask_question_stream(request: Request):
    userid, names, question, top_k = ask_parameters(await json_request(request))
    print(f"收到用户 {userid} 的集合 {names} 的流式问题：{question}")
    try:
        retrieved = await run_in_threadpool(retrieve, question, userid, names, top_k)
        print('retrieved', retrieved)
    except ValueError as exc:
        fail(str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    sources = [(source_label(item), item["page"]) for item in retrieved]
    print('sources', sources)
    def events():
        try:
            for token in stream_answer(retrieved, question):
                yield "data: " + json.dumps({"token": token}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"done": True, "sources": sources}, ensure_ascii=False) + "\n\n"
        except Exception as exc:
            yield "data: " + json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动 RAG FastAPI 服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    options = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=options.host, port=options.port)

