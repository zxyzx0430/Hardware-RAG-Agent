"""
Hardware RAG Agent — WriteFileTool (file_ops group).

Writes content to a local file (overwrite).
Migrated from tools/file_ops.py (Task 1, SubTask 1.10).

industrial-tool-runtime Task 2: refactored to ToolSpec. Permission gating
removed (PermissionClassifier handles it pre-ToolNode); _ctx PrivateAttr
removed (inherited from ToolSpec, injected by agent_factory).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import validate_path
from src.agent.tools.groups.file_ops._git_snapshot import _git_snapshot


# ═══════════════════════════════════════════
# WriteFileTool
# ═══════════════════════════════════════════

class WriteFileArgs(BaseModel):
    path: str = Field(description="要写入的文件路径")
    content: str = Field(description="文件完整内容")


class WriteFileOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    path: str = Field("", description="写入的文件路径")
    success: bool = Field(False, description="是否写入成功")


class WriteFileTool(ToolSpec):
    """Write content to a local file (overwrite)."""
    name: str = "write_file"
    description: str = (
        "写入本地文件（覆盖已有内容），自动创建父目录。"
        "路径必须在项目目录内（path_guard 安全校验）。"
        "\n\n与 run_command echo > 的区分："
        "\n- write_file：有 path_guard 安全校验 + 自动创建父目录"
        "\n- run_command echo >：无安全校验，但支持 shell 重定向"
        "\n写入代码/配置文件用 write_file；需要 shell 重定向（如 echo | sort >）用 run_command。"
    )
    args_schema: type = WriteFileArgs
    output_schema: type[BaseModel] | None = WriteFileOutput

    risk_level: RiskLevel = RiskLevel.MEDIUM
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Write content to file, creating parent dirs if needed."""
        path = args.get("path", "")
        content = args.get("content", "")
        ok, reason = validate_path(path, is_write=True)
        if not ok:
            return {"output": f"Error: denied path: {reason}", "path": path, "success": False}
        return await _do_write(path, content)


async def _do_write(path: str, content: str) -> dict:
    """Write content to file, creating parent dirs if needed."""
    err = await asyncio.to_thread(_write_file_sync, path, content)
    if err:
        return {"output": f"Error writing file: {err}", "path": path, "success": False}
    await asyncio.to_thread(_git_snapshot, path, "write_file")
    return {"output": f"已写入 {len(content)} 字符到 {path}", "path": path, "success": True}


def _write_file_sync(path: str, content: str) -> str | None:
    """Sync file write with parent dir creation. Returns error string or None."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return None
    except OSError as exc:
        return str(exc)
