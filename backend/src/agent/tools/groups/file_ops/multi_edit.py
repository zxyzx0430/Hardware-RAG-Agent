"""
Hardware RAG Agent — MultiEditTool (file_ops group).

Single-file multi-place string replacement, applied atomically:
all edits must succeed in memory before any disk write happens.
Spec: add-grep-glob-todo-tools §Requirement: multi_edit.
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
# MultiEditTool
# ═══════════════════════════════════════════

class EditOp(BaseModel):
    old_string: str = Field(description="要替换的原文本")
    new_string: str = Field(description="替换为的新文本")
    replace_all: bool = Field(default=False, description="是否替换该 old_string 的全部匹配")


class MultiEditArgs(BaseModel):
    file_path: str = Field(description="要编辑的文件路径")
    edits: list[EditOp] = Field(description="按顺序应用的替换列表，任一失败则全部回滚")


class MultiEditOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    file_path: str = Field("", description="编辑的文件路径")
    success: bool = Field(False, description="是否全部成功")
    applied: int = Field(0, description="已应用的替换数")


class MultiEditTool(ToolSpec):
    """Single-file multi-place string replacement, atomic."""
    name: str = "multi_edit"
    description: str = (
        "对同一文件做多处字符串替换，一次调用原子完成（全成功才写盘，任一失败则回滚不写盘）。"
        "敏感文件如 .env/*.key 会被拦截。"
    )
    args_schema: type = MultiEditArgs
    output_schema: type[BaseModel] | None = MultiEditOutput

    risk_level: RiskLevel = RiskLevel.MEDIUM
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Validate path, then apply all edits atomically in memory."""
        path = args.get("file_path", "")
        ok, reason = validate_path(path, is_write=True)
        if not ok:
            return _fail(path, f"Error: {reason}")
        edits = args.get("edits", [])
        return await _do_multi_edit(path, edits)


async def _do_multi_edit(path: str, edits: list[Any]) -> dict:
    """Run atomic edit in a worker thread; translate str error to failure."""
    result = await asyncio.to_thread(_apply_edits_sync, path, edits)
    if isinstance(result, str):
        return _fail(path, result)
    await asyncio.to_thread(_git_snapshot, path, "multi_edit")
    return _ok(path, result)


def _apply_edits_sync(path: str, edits: list[Any]) -> str | int:
    """Read file, apply all edits in memory, write back only if all succeed."""
    content, err = _read_content(path)
    if err:
        return err
    new_content, applied, err = _apply_all_edits(content, edits)
    if err:
        return err
    return _write_back(path, new_content, applied)


def _apply_all_edits(content: str, edits: list[Any]) -> tuple[str, int, str | None]:
    """Sequentially apply edits. Stop and signal on first failure."""
    applied = 0
    for idx, edit in enumerate(edits):
        op = _coerce_edit(edit)
        missing = op["old_string"] not in content
        if missing:
            return content, applied, _err_not_found(idx)
        content, n = _apply_one(content, op)
        applied += n
    return content, applied, None


def _coerce_edit(edit: Any) -> dict[str, Any]:
    """Accept both dict and pydantic EditOp for robustness."""
    if isinstance(edit, dict):
        return edit
    return edit.model_dump()


def _apply_one(content: str, op: dict[str, Any]) -> tuple[str, int]:
    """Apply a single replacement. Returns (new_content, count)."""
    if op.get("replace_all"):
        n = content.count(op["old_string"])
        return content.replace(op["old_string"], op["new_string"]), n
    return content.replace(op["old_string"], op["new_string"], 1), 1


def _err_not_found(idx: int) -> str:
    return f"edit {idx + 1} failed: old_string not found"


def _read_content(path: str) -> tuple[str, str | None]:
    """Read file content as text. Returns (content, error)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), None
    except FileNotFoundError:
        return "", f"file not found: {path}"
    except OSError as exc:
        return "", str(exc)


def _write_back(path: str, content: str, count: int) -> str | int:
    """Write content back to disk. Returns count or error string."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return count
    except OSError as exc:
        return str(exc)


def _fail(path: str, msg: str) -> dict:
    return {"output": msg, "file_path": path, "success": False, "applied": 0}


def _ok(path: str, applied: int) -> dict:
    return {
        "output": f"已应用 {applied} 处替换",
        "file_path": path,
        "success": True,
        "applied": applied,
    }
