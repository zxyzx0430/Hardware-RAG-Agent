"""SearchHistoryTool (retrieval group) — messages_fts history search.

Lets the Agent answer "上次你帮我生成的代码呢" by searching past messages in
the same ``messages_fts`` index used by ``POST /api/search``.

Read-only (risk_level LOW). Delegates to src.agent.session_search.
ToolSpec pattern mirrors search_docs.py.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.session_search import search_messages

logger = logging.getLogger(__name__)

# Default result cap for the search_history tool.
_DEFAULT_LIMIT: int = 5


class SearchHistoryArgs(BaseModel):
    query: str = Field(description="搜索关键词，例如 'LED 接线' 或 'STM32 代码'")


class SearchHistoryOutput(BaseModel):
    """Describes envelope.data shape (results list of matching messages)."""
    results: list[dict] = Field(default_factory=list, description="匹配的历史会话消息列表")


class SearchHistoryTool(ToolSpec):
    """Search past session messages via SQLite FTS5 full-text search."""
    name: str = "search_history"
    description: str = (
        "搜索历史会话记录，用户问'上次'相关问题时使用。"
        "例如'上次你帮我生成的代码呢'、'之前我们聊过的 ESP32 接线'。"
    )
    args_schema: type = SearchHistoryArgs
    output_schema: type[BaseModel] | None = SearchHistoryOutput
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 30
    max_retries: int = 1

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Search messages_fts and render a bounded summary for the model."""
        query = args.get("query", "")
        results = search_messages(query, _DEFAULT_LIMIT)
        return _build_history_result(results, query)


def _build_history_result(results: list[dict], query: str) -> dict:
    """Build the dict returned by SearchHistoryTool: output summary + results."""
    if not results:
        return {"output": f"未找到与「{query}」相关的历史会话。", "results": []}
    summary = _format_history_summary(results, query)
    return {"output": summary, "results": results}


def _format_history_summary(results: list[dict], query: str) -> str:
    """Render a one-line-per-match summary for the LLM."""
    lines = [f"找到 {len(results)} 条与「{query}」相关的历史会话："]
    for i, r in enumerate(results, 1):
        preview = (r.get("content") or "")[:80]
        role = r.get("role") or "?"
        lines.append(f"  [{i}] session={r.get('session_id', '?')} {role}：{preview}")
    return "\n".join(lines)
