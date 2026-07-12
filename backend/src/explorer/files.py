"""File read/write helpers for the explorer."""

from __future__ import annotations

import base64
import codecs
import logging
import mimetypes
import shutil
from pathlib import Path
from typing import Any

from src.explorer.security import ExplorerSecurityError, validate_path

logger = logging.getLogger(__name__)

TEXT_SAMPLE_BYTES = 8192
MAX_TEXT_SIZE = 5 * 1024 * 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024


def read_file_response(path: Path) -> dict[str, Any]:
    """Build the response payload for a read request."""
    if _is_text_file(path):
        return _text_response(path)
    return _binary_response(path)


def write_text_file(path: Path, content: str) -> None:
    """Write *content* as UTF-8 text to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_node(path: Path, node_type: str) -> None:
    """Create a new file or directory at *path*."""
    validate_path(str(path), must_exist=False)
    if path.exists():
        raise FileExistsError(f"already exists: {path}")
    if node_type == "directory":
        path.mkdir(parents=True, exist_ok=True)
    else:
        write_text_file(path, "")


def rename_node(path: Path, new_name: str) -> Path:
    """Rename *path* to *new_name* in the same parent directory."""
    new_path = path.with_name(new_name)
    validate_path(str(new_path), must_exist=False)
    path.rename(new_path)
    return new_path


def delete_node(path: Path) -> None:
    """Delete a file or directory."""
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def move_node(path: Path, target_dir: Path) -> Path:
    """Move *path* into *target_dir*; raise FileExistsError on conflict."""
    target = target_dir / path.name
    validate_path(str(target), must_exist=False)
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")
    shutil.move(str(path), str(target))
    return target


def copy_node(path: Path, target_dir: Path) -> Path:
    """Copy *path* into *target_dir*; raise FileExistsError on conflict."""
    target = target_dir / path.name
    validate_path(str(target), must_exist=False)
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")
    if path.is_dir():
        shutil.copytree(str(path), str(target))
    else:
        shutil.copy2(str(path), str(target))
    return target


def _text_response(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_TEXT_SIZE:
        return _error_dict("file too large", f"max {MAX_TEXT_SIZE} bytes", 413)
    return {
        "name": path.name,
        "path": str(path),
        "content": _read_text_content(path),
        "is_text": True,
    }


def _binary_response(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    data_url = _image_data_url(path) if size <= IMAGE_MAX_BYTES else None
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


def _image_data_url(path: Path) -> str | None:
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None or not mime.startswith("image/"):
        return None
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"
