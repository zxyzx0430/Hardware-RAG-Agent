"""
Hardware RAG Agent — EditFileTool (file_ops group).

Edits a local file by string replacement.
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
from src.agent.tools.groups.file_ops._git_snapshot import _git_snapshot


# ═══════════════════════════════════════════
# EditFileTool
# ═══════════════════════════════════════════

class EditFileArgs(BaseModel):
    path: str = Field(description="要编辑的文件路径")
    old_string: str = Field(description="要替换的原文本")
    new_string: str = Field(description="替换为的新文本")
    replace_all: bool = Field(default=False, description="是否替换全部匹配")


class EditFileOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter).
    replacements is absent on failure paths — default keeps soft-check quiet."""
    path: str = Field("", description="编辑的文件路径")
    success: bool = Field(False, description="是否编辑成功")
    replacements: int = Field(0, description="替换处数")


class EditFileTool(ToolSpec):
    """Edit a local file by string replacement."""
    name: str = "edit_file"
    description: str = (
        "编辑本地文件（字符串替换，精确匹配 old_string 后替换为 new_string）。"
        "路径必须在项目目录内（path_guard 安全校验）。"
        "\n\n与 run_command sed 的区分："
        "\n- edit_file：有 path_guard 安全校验 + 精确字符串匹配（比 sed 正则更安全）"
        "\n- run_command sed：无安全校验，但支持正则替换和管道"
        "\n精确替换代码片段用 edit_file；需要正则替换或管道处理用 run_command。"
        "\n多处替换同一文件用 multi_edit（原子完成）；大段重构用 apply_patch。"
    )
    args_schema: type = EditFileArgs
    output_schema: type[BaseModel] | None = EditFileOutput

    risk_level: RiskLevel = RiskLevel.MEDIUM
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Edit file by string replacement."""
        path = args.get("path", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        replace_all = args.get("replace_all", False)
        ok, reason = validate_path(path, is_write=True)
        if not ok:
            return {"output": f"Error: denied path: {reason}", "path": path, "success": False}
        return await _do_edit(path, old_string, new_string, replace_all)


async def _do_edit(path: str, old_string: str, new_string: str, replace_all: bool) -> dict:
    """Edit file by string replacement."""
    result = await asyncio.to_thread(_edit_file_sync, path, old_string, new_string, replace_all)
    if isinstance(result, str):
        return {"output": f"Error: {result}", "path": path, "success": False}
    await asyncio.to_thread(_git_snapshot, path, "edit_file")
    return {"output": f"已替换 {result} 处", "path": path, "success": True, "replacements": result}


def _edit_file_sync(path: str, old_string: str, new_string: str, replace_all: bool) -> int | str:
    """Sync edit. Returns replacement count (int) or error string."""
    content, err = _read_content(path)
    if err:
        return err
    if old_string not in content:
        return f"old_string not found in {path}"
    new_content, count = _apply_replace(content, old_string, new_string, replace_all)
    return _write_back(path, new_content, count)


def _read_content(path: str) -> tuple[str, str | None]:
    """Read file content. Returns (content, error)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), None
    except FileNotFoundError:
        return "", f"file not found: {path}"
    except OSError as exc:
        return "", str(exc)


def _apply_replace(content: str, old: str, new: str, replace_all: bool) -> tuple[str, int]:
    """Replace old with new. Returns (new_content, count)."""
    count = content.count(old) if replace_all else 1
    new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
    return new_content, count


def _write_back(path: str, content: str, count: int) -> int | str:
    """Write content back. Returns count or error string."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return count
    except OSError as exc:
        return str(exc)
