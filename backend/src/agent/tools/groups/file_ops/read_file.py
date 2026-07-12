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
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import validate_path

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

DEFAULT_READ_LIMIT: int = 2000
DEFAULT_READ_OFFSET: int = 0
MAX_OUTPUT_CHARS: int = 5000
TRUNCATE_SUFFIX: str = "...[truncated]"


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
        "\n\n与 run_command cat/type 的区分："
        "\n- read_file：有 path_guard 安全校验 + 分段读取 + 自动截断（>5000 字符）"
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
        return await _do_read(path, offset, limit)


async def _do_read(path: str, offset: int, limit: int) -> dict:
    """Read file content with offset/limit, return truncated output."""
    content = await asyncio.to_thread(_read_file_sync, path, offset, limit)
    return {"output": content, "path": path, "offset": offset, "limit": limit}


def _read_file_sync(path: str, offset: int, limit: int) -> str:
    """Sync file read with line offset/limit + truncation."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except OSError as exc:
        return f"Error reading file: {exc}"
    return _truncate("".join(lines[offset:offset + limit]))


def _truncate(text: str) -> str:
    """Truncate text to MAX_OUTPUT_CHARS."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + TRUNCATE_SUFFIX
