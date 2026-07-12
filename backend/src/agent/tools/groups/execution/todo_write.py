"""
Hardware RAG Agent — TodoWriteTool (execution group).

Maintains a session-scoped TODO list. Agent self-uses this on long tasks
(3+ steps): list first, then check off step-by-step. Inspired by
Claude Code's TodoWrite tool.

Session isolation: each session_id has its own list stored in a
module-level dict (not persisted — closing the session clears it).

SSE contract: this tool DOES NOT push SSE directly (avoids conflict
with Task 0 / Task 14 changes in sse_adapter.py). Instead it tags the
return value with `_todo_update: True`; Task 14 will add a unified
`todo_update` SSE emitter in sse_adapter that consumes this marker.

Spec: add-grep-glob-todo-tools §Requirement: todo_write tool (Task 11).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

_STATUS_PENDING: str = "pending"
_STATUS_IN_PROGRESS: str = "in_progress"
_STATUS_COMPLETED: str = "completed"
_PRIORITY_HIGH: str = "high"
_PRIORITY_MEDIUM: str = "medium"
_PRIORITY_LOW: str = "low"
_VALID_STATUSES: frozenset[str] = frozenset({
    _STATUS_PENDING, _STATUS_IN_PROGRESS, _STATUS_COMPLETED,
})
_VALID_PRIORITIES: frozenset[str] = frozenset({
    _PRIORITY_HIGH, _PRIORITY_MEDIUM, _PRIORITY_LOW,
})
_DEFAULT_PRIORITY: str = _PRIORITY_MEDIUM
_DEFAULT_SESSION_ID: str = "default"
_UPDATE_MARKER_KEY: str = "_todo_update"
_MAX_TODO_ITEMS: int = 50


# ═══════════════════════════════════════════
# Session state (module-level, not persisted)
# ═══════════════════════════════════════════

# Maps session_id -> list of todo dicts (in submission order).
# Chosen over a dataclass because the spec requires "module-level dict
# 按 session_id 存 TODO 清单". Cleared on process restart (not persisted).
_SESSION_TODOS: dict[str, list[dict]] = {}


# ═══════════════════════════════════════════
# Args schema
# ═══════════════════════════════════════════

class TodoOp(BaseModel):
    """Single TODO entry. Status transitions are Agent-managed."""
    content: str = Field(description="TODO 内容（简短一句话）")
    status: str = Field(
        default=_STATUS_PENDING,
        description="状态：pending / in_progress / completed",
    )
    priority: str = Field(
        default=_DEFAULT_PRIORITY,
        description="优先级：high / medium / low",
    )


class TodoWriteArgs(BaseModel):
    """Args for TodoWriteTool. Replaces the entire list each call."""
    todos: list[TodoOp] = Field(description="TODO 清单（整体替换当前会话的清单）")


# ═══════════════════════════════════════════
# TodoWriteTool
# ═══════════════════════════════════════════

class TodoWriteTool(ToolSpec):
    """Maintain a session-scoped TODO list for long tasks."""
    name: str = "todo_write"
    description: str = (
        "维护会话级 TODO 清单。长任务（3+ 步骤）应先列清单再逐步执行："
        "pending=灰圈、in_progress=蓝转圈（同时只能有 1 项）、"
        "completed=绿勾划线。每次调用整体替换当前会话的清单。"
        "短任务（1-2 步）不要使用此工具。清单仅存内存，关闭会话即清空。"
    )
    args_schema: type = TodoWriteArgs

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 5
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Replace session list; return marker for SSE downstream."""
        raw_todos = args.get("todos", []) or []
        session_id = _resolve_session_id(ctx)
        normalized = _normalize_todos(raw_todos)
        if normalized is None:
            return _invalid_result()
        if len(normalized) > _MAX_TODO_ITEMS:
            return _too_many_result(len(normalized))
        _SESSION_TODOS[session_id] = normalized
        logger.info("todo_write session=%s count=%d", session_id, len(normalized))
        return _build_result(session_id, normalized)


# ═══════════════════════════════════════════
# Helpers (pure, testable)
# ═══════════════════════════════════════════

def _resolve_session_id(ctx: ToolContext) -> str:
    """Read session_id from ctx; fall back to 'default'."""
    sid = getattr(ctx, "session_id", None)
    if sid and isinstance(sid, str):
        return sid
    return _DEFAULT_SESSION_ID


def _normalize_todos(raw_todos: list[Any]) -> list[dict] | None:
    """Convert raw TodoOp-like dicts to plain dicts with validated fields."""
    out: list[dict] = []
    for item in raw_todos:
        rec = _normalize_one(item)
        if rec is None:
            return None
        out.append(rec)
    return out


def _normalize_one(item: Any) -> dict | None:
    """Validate one todo entry; return dict or None on invalid."""
    if not isinstance(item, dict):
        # Pydantic model passed — convert via .model_dump()
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        else:
            return None
    content = str(item.get("content", "")).strip()
    if not content:
        return None
    status = str(item.get("status", _STATUS_PENDING))
    priority = str(item.get("priority", _DEFAULT_PRIORITY))
    if status not in _VALID_STATUSES or priority not in _VALID_PRIORITIES:
        return None
    return {"content": content, "status": status, "priority": priority}


def _build_result(session_id: str, todos: list[dict]) -> dict:
    """Build success result; marker triggers SSE in Task 14."""
    return {
        "output": f"updated {len(todos)} todos",
        "todos": todos,
        "session_id": session_id,
        _UPDATE_MARKER_KEY: True,
    }


def _invalid_result() -> dict:
    """Build invalid-input result (no marker, no SSE)."""
    return {"output": "invalid todos: each item needs content + valid status/priority",
            "todos": [], _UPDATE_MARKER_KEY: False}


def _too_many_result(count: int) -> dict:
    """Build over-capacity result."""
    return {"output": f"too many todos: {count} > {_MAX_TODO_ITEMS}",
            "todos": [], _UPDATE_MARKER_KEY: False}


# ═══════════════════════════════════════════
# Public accessor (used by sse_adapter / Task 14)
# ═══════════════════════════════════════════

def get_session_todos(session_id: str) -> list[dict]:
    """Return the current TODO list for a session (empty if absent)."""
    return list(_SESSION_TODOS.get(session_id, []))


def clear_session_todos(session_id: str) -> None:
    """Clear the TODO list for a session (called on session close)."""
    _SESSION_TODOS.pop(session_id, None)
