"""Undo the latest Agent file snapshot without changing user Git state."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.file_ops._git_lock import get_git_lock
from src.agent.tools.groups.file_ops._git_snapshot import undo_latest_snapshot


class UndoEditArgs(BaseModel):
    """Undo takes no arguments — it targets the latest Agent snapshot."""


class UndoEditOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""

    success: bool = Field(False, description="是否撤销成功")
    reverted_files: list[str] = Field(default_factory=list, description="回滚的文件列表")


class UndoEditTool(ToolSpec):
    """Undo the latest Agent edit from a private snapshot ref."""

    name: str = "undo_edit"
    description: str = "撤销最近一次 Agent 文件编辑，只恢复 Agent 修改过且未被后续用户改动的文件。"
    args_schema: type = UndoEditArgs
    output_schema: type[BaseModel] | None = UndoEditOutput

    risk_level: RiskLevel = RiskLevel.HIGH
    timeout_seconds: int = 12
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Run Git inspection and worktree-only restore under the shared lock."""
        return await _do_undo()


async def _do_undo() -> dict:
    """Serialize the undo with Agent snapshots, then run Git in a worker thread."""
    async with get_git_lock():
        result = await asyncio.to_thread(_undo_sync)
    if isinstance(result, str):
        return _fail(result)
    files, warning = result
    return _ok(files, warning)


def _undo_sync() -> str | tuple[list[str], str | None]:
    """Restore only the latest private snapshot's paths; never reset/stash."""
    return undo_latest_snapshot()


def _fail(msg: str) -> dict:
    return {"output": msg, "success": False, "reverted_files": []}


def _ok(files: list[str], warning: str | None = None) -> dict:
    listing = ", ".join(files) if files else "(no files)"
    msg = f"reverted: {listing}"
    if warning:
        msg += f" | {warning}"
    return {
        "output": msg,
        "success": True,
        "reverted_files": files,
    }
