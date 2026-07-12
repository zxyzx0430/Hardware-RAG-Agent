"""
Hardware RAG Agent — ListKbDocsTool (retrieval group).

T3 rag-retrieval-efficiency §1B: lets the Agent discover what documents are
in the knowledge base before searching, so it can pick a precise doc_filter
instead of blind-querying 9 times (e.g. classic ESP32 vs ESP32-S3).

Output shape:
    {"output": "知识库共 N 个文档：\n  - title (category, M chunks)\n  ...",
     "docs": [{doc_id, title, category, file_type, chunk_count, kb_id, kb_name}]}

industrial-tool-runtime Task 2: refactored to ToolSpec.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════

# Cap how many docs we list in the LLM-facing summary line. The full docs
# array stays unbounded; this only guards the one-line text preview so a
# 100-doc KB doesn't blow up the LLM context window.
_SUMMARY_DOC_LIMIT: int = 20

# Empty-KB fallback message shown to the LLM when no docs are indexed.
_EMPTY_KB_MSG: str = "知识库当前没有任何文档，建议直接基于通用知识回答或请用户上传文档。"


# ═══════════════════════════════════════════
# ListKbDocsTool
# ═══════════════════════════════════════════

class ListKbDocsArgs(BaseModel):
    """No args — returns all docs across enabled KBs."""

    pass


class ListKbDocsOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    docs: list[dict] = Field(default_factory=list, description="知识库文档列表")


class ListKbDocsTool(ToolSpec):
    """List documents in enabled knowledge bases.

    Use this BEFORE search_docs when the query mentions a specific document
    series (e.g. "ESP32-S3" vs classic "ESP32") so you can pick a doc_filter.
    """
    name: str = "list_kb_docs"
    description: str = (
        "列出本地知识库中的所有文档（芯片手册 PDF）。"
        "不确定知识库有哪些文档时先调本工具盘点，再调 search_docs 精准搜。"
    )
    args_schema: type = ListKbDocsArgs
    output_schema: type[BaseModel] | None = ListKbDocsOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 10
    max_retries: int = 0

    _kb_ids: list[str] | None = PrivateAttr(default=None)

    def __init__(self, kb_ids: list[str] | None = None):
        super().__init__()
        self._kb_ids = kb_ids

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """List all docs in enabled KBs (sync DB call wrapped in a thread)."""
        from src.rag.kb_manager import get_kb_manager

        kb_manager = get_kb_manager()
        docs = await asyncio.to_thread(kb_manager.list_all_docs, self._kb_ids)
        return _build_output(docs)


def _build_output(docs: list[dict]) -> dict:
    """Build the {output, docs} dict returned by ListKbDocsTool."""
    if not docs:
        return {"output": _EMPTY_KB_MSG, "docs": []}
    summary = _render_docs_summary(docs)
    return {"output": summary, "docs": docs}


def _render_docs_summary(docs: list[dict]) -> str:
    """Render a short text summary the LLM reads to pick a doc_filter."""
    lines = [f"知识库共 {len(docs)} 个文档："]
    for d in docs[:_SUMMARY_DOC_LIMIT]:
        lines.append(_format_doc_line(d))
    if len(docs) > _SUMMARY_DOC_LIMIT:
        lines.append(f"  ...共 {len(docs)} 个，当前显示前 {_SUMMARY_DOC_LIMIT} 个。")
    return "\n".join(lines)


def _format_doc_line(d: dict) -> str:
    """Format one doc as '  - title (category, N chunks) [doc_id]'."""
    title = (d.get("title") or d.get("doc_id") or "未知文档").strip()
    category = (d.get("category") or "").strip()
    chunk_count = d.get("chunk_count", 0) or 0
    doc_id = (d.get("doc_id") or "").strip()
    category_part = f"{category}, " if category else ""
    doc_id_part = f" [{doc_id}]" if doc_id else ""
    return f"  - {title} ({category_part}{chunk_count} chunks){doc_id_part}"
