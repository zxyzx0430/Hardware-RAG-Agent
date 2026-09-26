"""File read/write helpers for the explorer."""

from __future__ import annotations

import base64
import codecs
import hashlib
import logging
import mimetypes
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.explorer.security import ExplorerSecurityError, validate_path

logger = logging.getLogger(__name__)

TEXT_SAMPLE_BYTES = 8192
MAX_TEXT_SIZE = 5 * 1024 * 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024
_PATH_WRITE_LOCKS: dict[str, tuple[threading.Lock, int]] = {}
_PATH_WRITE_LOCKS_GUARD = threading.Lock()


class FileVersionConflictError(Exception):
    """Raised when a file changed since Explorer last read it."""


def read_file_response(path: Path) -> dict[str, Any]:
    """Build the response payload for a read request."""
    if _is_text_file(path):
        return _text_response(path)
    return _binary_response(path)


def write_text_file(path: Path, content: str, expected_version: str | None = None) -> str:
    """Atomically write UTF-8 text if the on-disk version still matches.

    A ``None`` expected version means the caller expects the target not to exist.
    The returned digest is the version for the newly-written bytes.
    """
    with _path_write_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if _file_version(path) != expected_version:
            raise FileVersionConflictError(f"file changed since it was read: {path}")

        encoded = content.encode("utf-8")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            # Check again before replacement; the per-path lock serializes Explorer saves.
            if _file_version(path) != expected_version:
                raise FileVersionConflictError(f"file changed while save was pending: {path}")
            os.replace(temp_path, path)
            temp_path = None
            return hashlib.sha256(encoded).hexdigest()
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("could not remove temporary Explorer write file %s", temp_path)


@contextmanager
def _path_write_lock(path: Path) -> Iterator[None]:
    """Serialize Explorer compare-and-replace saves for the same destination."""
    lock_key = os.path.normcase(str(path.resolve(strict=False)))
    with _PATH_WRITE_LOCKS_GUARD:
        entry = _PATH_WRITE_LOCKS.get(lock_key)
        if entry is None:
            lock, users = threading.Lock(), 0
        else:
            lock, users = entry
        _PATH_WRITE_LOCKS[lock_key] = (lock, users + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _PATH_WRITE_LOCKS_GUARD:
            current_lock, users = _PATH_WRITE_LOCKS[lock_key]
            if users == 1:
                del _PATH_WRITE_LOCKS[lock_key]
            else:
                _PATH_WRITE_LOCKS[lock_key] = (current_lock, users - 1)


def _file_version(path: Path) -> str | None:
    """Hash current file bytes, or return None when the path does not exist."""
    try:
        with path.open("rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except FileNotFoundError:
        return None


def create_node(path: Path, node_type: str, *, session_id: str) -> None:
    """Create a new file or directory at *path*."""
    validate_path(str(path), must_exist=False, session_id=session_id)
    if path.exists():
        raise FileExistsError(f"already exists: {path}")
    if node_type == "directory":
        path.mkdir(parents=True, exist_ok=True)
    else:
        write_text_file(path, "")


def rename_node(path: Path, new_name: str, *, session_id: str) -> Path:
    """Rename *path* to *new_name* in the same parent directory."""
    new_path = path.with_name(new_name)
    validate_path(str(new_path), must_exist=False, session_id=session_id)
    path.rename(new_path)
    return new_path


def delete_node(path: Path) -> None:
    """Delete a file or directory."""
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def move_node(path: Path, target_dir: Path, *, session_id: str) -> Path:
    """Move *path* into *target_dir*; raise FileExistsError on conflict."""
    target = target_dir / path.name
    validate_path(str(target), must_exist=False, session_id=session_id)
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")
    shutil.move(str(path), str(target))
    return target


def copy_node(path: Path, target_dir: Path, *, session_id: str) -> Path:
    """Copy *path* into *target_dir*; raise FileExistsError on conflict."""
    target = target_dir / path.name
    validate_path(str(target), must_exist=False, session_id=session_id)
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")
    if path.is_dir():
        shutil.copytree(str(path), str(target))
    else:
        shutil.copy2(str(path), str(target))
    return target


def _text_response(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > MAX_TEXT_SIZE:
        return _error_dict("file too large", f"max {MAX_TEXT_SIZE} bytes", 413)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        # The file may have changed between the sample check and the full read.
        return _binary_response(path, raw)
    return {
        "name": path.name,
        "path": str(path),
        "content": content,
        "is_text": True,
        "version": hashlib.sha256(raw).hexdigest(),
    }


def _binary_response(path: Path, raw: bytes | None = None) -> dict[str, Any]:
    size = len(raw) if raw is not None else path.stat().st_size
    data_url = _image_data_url(path, raw) if size <= IMAGE_MAX_BYTES else None
    result: dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "is_text": False,
        "size": size,
    }
    if data_url:
        result["data_url"] = data_url
    return result


def _error_dict(message: str, detail: str | None, status: int) -> dict[str, Any]:
    return {"error": message, "detail": detail, "_status": status}


def _is_text_file(path: Path) -> bool:
    if path.stat().st_size == 0:
        return True
    sample = _read_sample(path)
    if b"\x00" in sample:
        return False
    return _is_valid_utf8(sample)


def _read_sample(path: Path) -> bytes:
    try:
        with path.open("rb") as f:
            return f.read(TEXT_SAMPLE_BYTES)
    except OSError:
        return b""


def _is_valid_utf8(data: bytes) -> bool:
    # 使用增量解码器：采样末尾可能截断多字节 UTF-8 字符（如中文），
    # 增量解码器在 final=False 时会保留未完成序列而不报错。
    # 真正的非 UTF-8 数据仍会触发 UnicodeDecodeError。
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(data, final=False)
        return True
    except UnicodeDecodeError:
        return False


def _read_text_content(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def _image_data_url(path: Path, raw: bytes | None = None) -> str | None:
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None or not mime.startswith("image/"):
        return None
    data = raw if raw is not None else path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"
