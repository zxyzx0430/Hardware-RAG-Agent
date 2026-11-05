"""Memory tools (Memory v1): search, remember, forget.

These tools operate on explicit ``memory_entries`` only. They never grant
permissions and never touch hardware profiles (which use their own store).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from app.db.memory_store import (
    DEFAULT_CONFIDENCE,
    MemorySecretError,
    MemoryStore,
    MemoryValidationError,
)
from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.memory_context import current_project_key

logger = logging.getLogger(__name__)

_DEFAULT_SEARCH_LIMIT = 5


def _scope_key_for(scope: str, explicit: str, ctx: ToolContext) -> str:
    if explicit:
        return explicit
    if scope == "global":
        return ""
    if scope == "session":
        return ctx.session_id or ""
    return current_project_key()


class MemorySearchArgs(BaseModel):
    query: str = Field(description="检索关键词，例如 '用户偏好的开发板' 或 '串口波特率'")
    scope: str = Field(default="", description="可选过滤：global / project / session；留空搜索全部")
    scope_key: str = Field(default="", description="可选 scope 键；留空按 session_id 或当前项目自动填充")
    kind: str = Field(default="", description="可选过滤：preference / fact / constraint / decision / procedure")
    limit: int = Field(default=_DEFAULT_SEARCH_LIMIT, description="最多返回条数（默认 5）")


class MemorySearchOutput(BaseModel):
    results: list[dict] = Field(default_factory=list, description="命中的记忆条目")
    total: int = Field(default=0, description="命中条数")


class MemorySearchTool(ToolSpec):
    """Search explicit memory entries (read-only)."""

    name: str = "memory_search"
    description: str = (
        "搜索之前明确记住的用户偏好、事实、约束或决定。用户提到'之前'、'上次说过的偏好'、"
        "项目约定时先调用。只返回已保存的记忆，不检索知识库或历史消息。"
    )
    args_schema: type = MemorySearchArgs
    output_schema: type[BaseModel] | None = MemorySearchOutput
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 30
    max_retries: int = 1

    _store: MemoryStore | None = PrivateAttr(default=None)

    def __init__(self, store: MemoryStore | None = None):
        super().__init__()
        self._store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        store = self._store or MemoryStore()
        query = args.get("query", "")
        scope = (args.get("scope") or "").strip()
        kind = (args.get("kind") or "").strip()
        try:
            if scope:
                results = store.search(
                    query,
                    scope=scope,
                    scope_key=(
                        _scope_key_for(scope, args.get("scope_key", ""), ctx)
                        if scope != "global"
                        else None
                    ),
                    kind=kind or None,
                    limit=args.get("limit") or _DEFAULT_SEARCH_LIMIT,
                )
            else:
                # Default visibility matches context injection: global plus the
                # current project and session, never unrelated sessions.
                results = store.search_visible(
                    query,
                    project_key=current_project_key(),
                    session_id=ctx.session_id or None,
                    kind=kind or None,
                    limit=args.get("limit") or _DEFAULT_SEARCH_LIMIT,
                )
        except MemoryValidationError as exc:
            return {"output": f"记忆检索参数无效：{exc}", "results": [], "total": 0}
        if not results:
            return {"output": f"没有找到与「{query}」相关的记忆。", "results": [], "total": 0}
        lines = [f"找到 {len(results)} 条相关记忆："]
        for index, entry in enumerate(results, 1):
            lines.append(
                f"  [{index}] id={entry['id']} kind={entry['kind']} "
                f"confidence={entry['confidence']:.2f}：{entry['content']}"
            )
        return {"output": "\n".join(lines), "results": results, "total": len(results)}


class MemoryRememberArgs(BaseModel):
    content: str = Field(description="要记住的内容，简洁陈述；不要写入密钥、令牌或密码")
    kind: str = Field(
        default="fact",
        description="记忆类型：preference / fact / constraint / decision / procedure",
    )
    scope: str = Field(default="project", description="作用域：global / project / session")
    scope_key: str = Field(default="", description="可选 scope 键；留空自动使用当前会话或项目")
    structured: dict | None = Field(default=None, description="可选结构化字段（JSON 对象）")
    confidence: float = Field(default=DEFAULT_CONFIDENCE, description="置信度 0-1")
    source: str = Field(default="agent", description="来源：agent / skill / user / import")


class MemoryRememberOutput(BaseModel):
    saved: bool = Field(default=False, description="是否保存成功")
    entry: dict | None = Field(default=None, description="保存后的记忆条目")


class MemoryRememberTool(ToolSpec):
    """Persist one explicit memory entry."""

    name: str = "memory_remember"
    description: str = (
        "明确记住一条对后续对话有用的信息（用户偏好、项目事实、约束、决定或流程）。"
        "仅当用户明确要求记住或确认某信息应长期保留时调用；不要每轮自动保存。"
        "禁止保存密钥、令牌、密码等敏感信息。"
    )
    args_schema: type = MemoryRememberArgs
    output_schema: type[BaseModel] | None = MemoryRememberOutput
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 30
    max_retries: int = 1

    _store: MemoryStore | None = PrivateAttr(default=None)

    def __init__(self, store: MemoryStore | None = None):
        super().__init__()
        self._store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        store = self._store or MemoryStore()
        scope = (args.get("scope") or "project").strip()
        try:
            entry = store.create(
                scope=scope,
                scope_key=_scope_key_for(scope, args.get("scope_key", ""), ctx),
                kind=args.get("kind") or "fact",
                content=args.get("content", ""),
                structured=args.get("structured"),
                source=args.get("source") or "agent",
                confidence=args.get("confidence", DEFAULT_CONFIDENCE),
            )
        except MemorySecretError as exc:
            return {"output": f"拒绝保存：{exc}", "saved": False, "entry": None}
        except MemoryValidationError as exc:
            return {"output": f"记忆参数无效：{exc}", "saved": False, "entry": None}
        return {
            "output": f"已记住（id={entry['id']}，scope={entry['scope']}，kind={entry['kind']}）：{entry['content']}",
            "saved": True,
            "entry": entry,
        }


class MemoryForgetArgs(BaseModel):
    id: str = Field(description="要遗忘的记忆条目 id（来自 memory_search 结果）")


class MemoryForgetOutput(BaseModel):
    deleted: bool = Field(default=False, description="是否已软删除")
    id: str = Field(default="", description="目标条目 id")


class MemoryForgetTool(ToolSpec):
    """Soft-delete one memory entry."""

    name: str = "memory_forget"
    description: str = (
        "遗忘一条记忆（软删除）。用户要求删除某条记住的信息时使用，需要先从 memory_search 获取 id。"
    )
    args_schema: type = MemoryForgetArgs
    output_schema: type[BaseModel] | None = MemoryForgetOutput
    risk_level: RiskLevel = RiskLevel.MEDIUM
    timeout_seconds: int = 30
    max_retries: int = 1

    _store: MemoryStore | None = PrivateAttr(default=None)

    def __init__(self, store: MemoryStore | None = None):
        super().__init__()
        self._store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        store = self._store or MemoryStore()
        entry_id = args.get("id", "")
        if store.soft_delete(entry_id):
            return {"output": f"已遗忘记忆 {entry_id}。", "deleted": True, "id": entry_id}
        return {
            "output": f"未找到可删除的记忆 {entry_id}（可能已删除）。",
            "deleted": False,
            "id": entry_id,
        }


__all__ = [
    "MemorySearchArgs",
    "MemorySearchTool",
    "MemoryRememberArgs",
    "MemoryRememberTool",
    "MemoryForgetArgs",
    "MemoryForgetTool",
]
