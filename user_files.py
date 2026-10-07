"""Per-user knowledge document paths and bounded file uploads."""

import os
import re
import tempfile
from pathlib import Path


DEFAULT_KNOWLEDGE_DIR = "/mnt/f/Workspace/wsl_py/knowledge_base"
SUPPORTED_SUFFIXES = {".txt", ".md", ".html", ".htm"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
USERID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}\Z")


def knowledge_root():
    """返回当前配置的知识库根目录。"""
    return Path(os.environ.get("KNOWLEDGE_DIR", DEFAULT_KNOWLEDGE_DIR))


def validate_userid(userid):
    """校验可安全用作目录名的 userid。"""
    if not isinstance(userid, str) or not USERID_PATTERN.fullmatch(userid):
        raise ValueError("userid 格式无效；仅支持字母、数字、点、下划线、@ 和连字符")
    return userid


def validate_filename(filename):
    """校验上传文件名及支持的文档格式。"""
    if (not isinstance(filename, str) or not filename or filename in (".", "..")
            or "/" in filename or "\\" in filename or "\x00" in filename
            or any(ch in filename for ch in '<>:"|?*')
            or any(ord(ch) < 32 for ch in filename)
            or len(filename.encode("utf-8")) > 240
            or filename.startswith(".") or filename.endswith((".", " "))):
        raise ValueError("文件名无效")
    if Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("只支持 .txt、.md、.html、.htm 文件")
    return filename


def iter_user_documents(root=None):
    """Yield (doc_id, userid, path) for files directly under each user folder."""
    root = Path(root) if root is not None else knowledge_root()
    if not root.is_dir():
        return
    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir() or user_dir.is_symlink():
            continue
        try:
            userid = validate_userid(user_dir.name)
        except ValueError:
            continue
        for path in sorted(user_dir.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                validate_filename(path.name)
            except ValueError:
                continue
            yield "{}/{}".format(userid, path.name), userid, path


def save_upload(userid, filename, source, length, root=None):
    """Atomically replace a user's document from a bounded input stream."""
    userid = validate_userid(userid)
    filename = validate_filename(filename)
    if not isinstance(length, int) or length < 0:
        raise ValueError("Content-Length 无效")
    if length > MAX_UPLOAD_BYTES:
        raise ValueError("文件超过 20 MiB 限制")

    root = Path(root) if root is not None else knowledge_root()
    root.mkdir(parents=True, exist_ok=True)
    user_dir = root / userid
    user_dir.mkdir(exist_ok=True)
    if user_dir.is_symlink() or user_dir.resolve().parent != root.resolve():
        raise ValueError("用户目录无效")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".upload-", dir=str(user_dir), delete=False) as output:
            temp_path = Path(output.name)
            remaining = length
            while remaining:
                block = source.read(min(65536, remaining))
                if not block:
                    raise ValueError("上传内容不完整")
                output.write(block)
                remaining -= len(block)
        target = user_dir / filename
        os.replace(str(temp_path), str(target))
        return target
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
