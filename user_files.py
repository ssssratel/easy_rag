"""Per-user knowledge document paths and bounded file uploads."""

import os
import re
import tempfile
from pathlib import Path


DEFAULT_KNOWLEDGE_DIR = "/mnt/f/Workspace/wsl_py/knowledge_base"
SUPPORTED_SUFFIXES = {".txt", ".md", ".html", ".htm"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_COLLECTION_NAME = "default"
INACTIVE_MARKER = ".vector-disabled"
USERID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}\Z")


def knowledge_root():
    """返回当前配置的知识库根目录。"""
    return Path(os.environ.get("KNOWLEDGE_DIR", DEFAULT_KNOWLEDGE_DIR))


def validate_userid(userid):
    """校验可安全用作目录名的 userid。"""
    if not isinstance(userid, str) or not USERID_PATTERN.fullmatch(userid):
        raise ValueError("userid 格式无效；仅支持字母、数字、点、下划线、@ 和连字符")
    return userid


def validate_collection_name(collection_name):
    """校验用户下的逻辑集合名称。"""
    if not isinstance(collection_name, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", collection_name):
        raise ValueError("collection_name 格式无效；仅支持英文字母、数字和下划线，最多 64 字符")
    return collection_name


def collection_directory(userid, collection_name, root=None, create=False):
    """Return a validated user collection directory, creating it if requested."""
    userid = validate_userid(userid)
    collection_name = validate_collection_name(collection_name)
    root = Path(root) if root is not None else knowledge_root()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    user_dir = root / userid
    if create:
        user_dir.mkdir(exist_ok=True)
    if user_dir.is_symlink() or user_dir.resolve().parent != root.resolve():
        raise ValueError("用户目录无效")
    collection_dir = user_dir / collection_name
    if create:
        collection_dir.mkdir(exist_ok=True)
    if collection_dir.is_symlink() or collection_dir.resolve().parent != user_dir.resolve():
        raise ValueError("集合目录无效")
    return collection_dir


def is_collection_disabled(userid, collection_name, root=None):
    marker = collection_directory(userid, collection_name, root) / INACTIVE_MARKER
    return marker.exists() or marker.is_symlink()


def disable_collection(userid, collection_name, root=None):
    collection_dir = collection_directory(userid, collection_name, root, create=True)
    marker = collection_dir / INACTIVE_MARKER
    if marker.is_symlink():
        raise ValueError("集合状态标记无效")
    marker.touch(exist_ok=True)


def enable_collection(userid, collection_name, root=None):
    collection_dir = collection_directory(userid, collection_name, root, create=True)
    marker = collection_dir / INACTIVE_MARKER
    if marker.is_symlink():
        raise ValueError("集合状态标记无效")
    if marker.exists():
        marker.unlink()
    return collection_dir


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
    """Yield (doc_id, userid, collection_name, path) for user collection files."""
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

        seen = set()
        # Legacy files directly under a user are assigned to the default collection.
        legacy_disabled = is_collection_disabled(userid, DEFAULT_COLLECTION_NAME, root)
        for path in sorted(user_dir.iterdir()):
            if legacy_disabled:
                break
            if not path.is_file() or path.is_symlink():
                continue
            try:
                validate_filename(path.name)
            except ValueError:
                continue
            doc_id = "{}/{}/{}".format(userid, DEFAULT_COLLECTION_NAME, path.name)
            seen.add(doc_id)
            yield doc_id, userid, DEFAULT_COLLECTION_NAME, path

        for collection_dir in sorted(user_dir.iterdir()):
            if not collection_dir.is_dir() or collection_dir.is_symlink():
                continue
            try:
                collection_name = validate_collection_name(collection_dir.name)
            except ValueError:
                continue
            if is_collection_disabled(userid, collection_name, root):
                continue
            for path in sorted(collection_dir.iterdir()):
                if not path.is_file() or path.is_symlink():
                    continue
                try:
                    validate_filename(path.name)
                except ValueError:
                    continue
                doc_id = "{}/{}/{}".format(userid, collection_name, path.name)
                if doc_id in seen:
                    raise ValueError("旧目录与集合目录存在同名文件：{}".format(doc_id))
                seen.add(doc_id)
                yield doc_id, userid, collection_name, path


def save_upload(userid, collection_name, filename, source, length, root=None):
    """Atomically replace a user's document from a bounded input stream."""
    userid = validate_userid(userid)
    collection_name = validate_collection_name(collection_name)
    filename = validate_filename(filename)
    if not isinstance(length, int) or length < 0:
        raise ValueError("Content-Length 无效")
    if length > MAX_UPLOAD_BYTES:
        raise ValueError("文件超过 20 MiB 限制")

    collection_dir = collection_directory(userid, collection_name, root, create=True)
    if is_collection_disabled(userid, collection_name, root):
        raise ValueError("集合已删除，请先重新创建")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".upload-", dir=str(collection_dir), delete=False) as output:
            temp_path = Path(output.name)
            remaining = length
            while remaining:
                block = source.read(min(65536, remaining))
                if not block:
                    raise ValueError("上传内容不完整")
                output.write(block)
                remaining -= len(block)
        target = collection_dir / filename
        os.replace(str(temp_path), str(target))
        return target
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
