"""
Hardware RAG Agent — ReadFileTool (file_ops group).

Reads a local file with optional line offset/limit.
Migrated from tools/file_ops.py (Task 1, SubTask 1.10).

industrial-tool-runtime Task 2: refactored to ToolSpec. Permission gating
removed (PermissionClassifier handles it pre-ToolNode); _ctx PrivateAttr
removed (inherited from ToolSpec, injected by agent_factory).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import resolve_agent_path, validate_path

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

DEFAULT_READ_LIMIT: int = 2000
DEFAULT_READ_OFFSET: int = 0
MAX_READ_LINES: int = 5000
MAX_READ_BYTES: int = 512 * 1024
MAX_OUTPUT_CHARS: int = 5000
PLAN_MAX_OUTPUT_CHARS: int = 3000
TRUNCATE_SUFFIX: str = "...[truncated; use offset/limit to continue]"


# ═══════════════════════════════════════════
# ReadFileTool
# ═══════════════════════════════════════════

class ReadFileArgs(BaseModel):
    path: str = Field(description="要读取的文件路径（绝对路径或相对项目根）")
    offset: int = Field(default=DEFAULT_READ_OFFSET, description="起始行号（0-based）")
    limit: int = Field(default=DEFAULT_READ_LIMIT, description="最多读取行数")


class ReadFileOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    path: str = Field("", description="读取的文件路径")
    offset: int = Field(0, description="起始行偏移")
    limit: int = Field(0, description="读取行数")


class ReadFileTool(ToolSpec):
    """Read a local file with optional line offset/limit."""
    name: str = "read_file"
    description: str = (
        "读取本地文件内容，支持分段读取（offset/limit）。"
        "路径必须在项目目录内（path_guard 安全校验）。"
        "单次最多 5000 字符（计划模式 3000 字符）、5000 行、512KB；"
        "超出时返回截断标记，请用 offset/limit 继续读取，不要一次读完整份大文件。"
        "\n\n与 run_command cat/type 的区分："
        "\n- read_file：有 path_guard 安全校验 + 分段读取 + 自动截断"
        "\n- run_command cat：无安全校验，但支持管道（如 cat | grep）"
        "\n需要读单个文件用 read_file；需要管道处理用 run_command。"
    )
    args_schema: type = ReadFileArgs
    output_schema: type[BaseModel] | None = ReadFileOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Read file content with offset/limit, return truncated output."""
        path = args.get("path", "")
        offset = args.get("offset", DEFAULT_READ_OFFSET)
        limit = args.get("limit", DEFAULT_READ_LIMIT)
        ok, reason = validate_path(path, is_write=False)
        if not ok:
            return {"output": f"Error: denied path: {reason}", "path": path, "offset": offset, "limit": limit}
        resolved = resolve_agent_path(path)
        if not resolved:
            return {"output": "Error: path could not be resolved", "path": path, "offset": offset, "limit": limit}
        limit = max(0, min(int(limit), MAX_READ_LINES))
        max_chars = (
            PLAN_MAX_OUTPUT_CHARS
            if getattr(ctx, "permission_mode", "") == "plan"
            else MAX_OUTPUT_CHARS
        )
        return await _do_read(resolved, offset, limit, max_chars)


async def _do_read(path: str, offset: int, limit: int, max_chars: int) -> dict:
    """Read file content with offset/limit, return truncated output."""
    content = await asyncio.to_thread(
        _read_file_sync, path, offset, limit, max_chars,
    )
    return {"output": content, "path": path, "offset": offset, "limit": limit}


def _read_file_sync(path: str, offset: int, limit: int, max_chars: int) -> str:
    """Sync file read with line offset/limit, byte cap and output truncation.

    Reads in binary mode with bounded line reads so a multi-megabyte single
    line cannot be loaded whole; binary files are rejected with a clear error.
    """
    try:
        with open(path, "rb") as handle:
            if _looks_binary(handle):
                return (
                    f"Error: binary file: {path} ({_file_size(path)} bytes); "
                    "read_file only supports text files"
                )
            for _ in range(max(int(offset), 0)):
                if not _skip_one_line(handle):
                    return ""
            chunks: list[bytes] = []
            bytes_read = 0
            byte_truncated = False
            for _ in range(max(int(limit), 0)):
                line = handle.readline(MAX_READ_BYTES + 1)
                if not line:
                    break
                line_too_long = (
                    len(line) > MAX_READ_BYTES and not line.endswith(b"\n")
                )
                if bytes_read + len(line) > MAX_READ_BYTES or line_too_long:
                    remaining = max(MAX_READ_BYTES - bytes_read, 0)
                    if remaining:
                        chunks.append(line[:remaining])
                    byte_truncated = True
                    break
                chunks.append(line)
                bytes_read += len(line)
                if line_too_long:
                    break
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except PermissionError:
        return f"Error: permission denied: {path}"
    except OSError as exc:
        return f"Error reading file: {exc}"
    text = b"".join(chunks).decode("utf-8", errors="replace")
    # Match the previous text-mode universal-newline behavior.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if byte_truncated:
        text += TRUNCATE_SUFFIX
    return _truncate(text, max_chars)


def _looks_binary(handle: Any) -> bool:
    """Detect a binary file from its first 8 KiB (seek back afterwards)."""
    sample = handle.read(8192)
    try:
        handle.seek(0)
    except OSError:
        pass
    return b"\x00" in sample


def _skip_one_line(handle: Any) -> bool:
    """Skip one line of arbitrary length; return False at EOF."""
    while True:
        chunk = handle.readline(MAX_READ_BYTES + 1)
        if not chunk:
            return False
        if chunk.endswith(b"\n"):
            return True


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars with an explicit continuation marker."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + TRUNCATE_SUFFIX


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
