"""Per-user knowledge document paths and bounded file uploads."""

import os
import re
import shutil
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
    """Validate a string user ID before using it in paths and vector filters."""
    if not isinstance(userid, str) or not USERID_PATTERN.fullmatch(userid):
        raise ValueError("userid 格式无效")
    if userid in (".", ".."):
        raise ValueError("userid 格式无效")
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
    user_dir = root / str(userid)
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


def list_user_collections(userid, root=None):
    """列出用户已启用的逻辑集合，不创建任何目录。"""
    userid = validate_userid(userid)
    root = Path(root) if root is not None else knowledge_root()
    user_dir = root / str(userid)
    if not user_dir.is_symlink() and user_dir.is_dir():
        collections = []
        for path in sorted(user_dir.iterdir()):
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                name = validate_collection_name(path.name)
            except ValueError:
                continue
            if not is_collection_disabled(userid, name, root):
                collections.append(name)
        if DEFAULT_COLLECTION_NAME not in collections and not is_collection_disabled(
                userid, DEFAULT_COLLECTION_NAME, root):
            for path in user_dir.iterdir():
                if path.is_file() and not path.is_symlink():
                    try:
                        validate_filename(path.name)
                    except ValueError:
                        continue
                    collections.append(DEFAULT_COLLECTION_NAME)
                    collections.sort()
                    break
        return collections
    return []


def list_collection_files(userid, collection_name, root=None):
    """Return supported filenames in one active collection."""
    userid = validate_userid(userid)
    collection_name = validate_collection_name(collection_name)
    root = Path(root) if root is not None else knowledge_root()
    directory = collection_directory(userid, collection_name, root)
    if is_collection_disabled(userid, collection_name, root):
        raise ValueError("集合已删除，请先重新创建")
    directories = [directory]
    if collection_name == DEFAULT_COLLECTION_NAME:
        directories.append(directory.parent)  # Legacy files under the user directory.
    names = set()
    for folder in directories:
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            try:
                names.add(validate_filename(path.name))
            except ValueError:
                continue
    return sorted(names)


def collection_file_path(userid, collection_name, filename, root=None):
    """Resolve a supported file inside one active collection without creating it."""
    userid = validate_userid(userid)
    collection_name = validate_collection_name(collection_name)
    filename = validate_filename(filename)
    root = Path(root) if root is not None else knowledge_root()
    directory = collection_directory(userid, collection_name, root)
    if is_collection_disabled(userid, collection_name, root):
        raise ValueError("集合已删除，请先重新创建")
    candidates = [directory / filename]
    if collection_name == DEFAULT_COLLECTION_NAME:
        candidates.append(directory.parent / filename)
    for path in candidates:
        if path.is_symlink():
            raise ValueError("文件路径无效")
        if path.is_file():
            return path
    raise FileNotFoundError(filename)


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

def iter_user_documents(userid=None, collection_name=None, root=None):
    """Yield (doc_id, userid, collection_name, path) from active collections."""
    if isinstance(userid, Path) and root is None and collection_name is None:
        root, userid = userid, None
    root = Path(root) if root is not None else knowledge_root()
    if not root.is_dir():
        return
    users = [root / validate_userid(userid)] if userid is not None else sorted(root.iterdir())
    for user_dir in users:
        if not user_dir.is_dir() or user_dir.is_symlink():
            continue
        try:
            user = validate_userid(user_dir.name)
        except ValueError:
            continue
        names = ([validate_collection_name(collection_name)] if collection_name is not None
                 else list_user_collections(user, root))
        for name in names:
            if is_collection_disabled(user, name, root):
                continue
            directory = collection_directory(user, name, root)
            folders = [directory]
            if name == DEFAULT_COLLECTION_NAME:
                folders.append(user_dir)
            seen = set()
            for folder in folders:
                if not folder.is_dir():
                    continue
                for path in sorted(folder.iterdir()):
                    if not path.is_file() or path.is_symlink():
                        continue
                    try:
                        validate_filename(path.name)
                    except ValueError:
                        continue
                    if path.name in seen:
                        raise ValueError(f"旧目录与集合目录存在同名文件：{user}/{name}/{path.name}")
                    seen.add(path.name)
                    yield f"{user}/{name}/{path.name}", user, name, path



class UploadConflict(FileExistsError):
    """A filename already exists in the requested user collection."""
    def __init__(self, filenames):
        self.filenames = list(filenames)
        super().__init__("文件已存在，拒绝上传：" + "、".join(self.filenames))


def ensure_upload_names_available(userid, collection_name, filenames, root=None):
    """Check the whole batch before writing, including legacy default files."""
    directory = collection_directory(userid, collection_name, root)
    if is_collection_disabled(userid, collection_name, root):
        raise ValueError("集合已删除，请先重新创建")
    conflicts = []
    for filename in filenames:
        filename = validate_filename(filename)
        candidates = [directory / filename]
        if collection_name == DEFAULT_COLLECTION_NAME:
            candidates.append(directory.parent / filename)
        if any(path.exists() or path.is_symlink() for path in candidates):
            conflicts.append(filename)
    if conflicts:
        raise UploadConflict(conflicts)


def remove_collection_files(userid, collection_name, root=None):
    """Remove collection contents without following symlinks; retain its tombstone."""
    directory = collection_directory(userid, collection_name, root)
    # collection_directory checks that this exact directory is under root/user.
    for path in directory.iterdir():
        if path.name == INACTIVE_MARKER:
            continue
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path)
    if collection_name == DEFAULT_COLLECTION_NAME:
        for path in directory.parent.iterdir():
            if path.is_dir() and not path.is_symlink():
                continue
            try:
                validate_filename(path.name)
            except ValueError:
                continue
            path.unlink()


def save_upload(userid, collection_name, filename, source, length, root=None):
    """Publish a complete document atomically without replacing an existing file."""
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

    ensure_upload_names_available(userid, collection_name, [filename], root)
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
        ensure_upload_names_available(userid, collection_name, [filename], root)
        target = collection_dir / filename
        try:
            os.link(str(temp_path), str(target))
        except FileExistsError as exc:
            raise UploadConflict([filename]) from exc
        return target
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
