"""
Hardware RAG Agent — SearchDocsTool (retrieval group).

Retrieves relevant chunks from the local chip-manual knowledge base.
Migrated from tools/wrappers.py (Task 1, SubTask 1.2).

industrial-tool-runtime Task 2: refactored to ToolSpec. Coverage hint
counter migrated from PrivateAttr to ToolContext.kb_coverage_counter
(per-request shared state); hint text flows into the result dict's
`kb_coverage_hint` key (ToolRouter lifts it into envelope.metadata).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Truncation constants (spec §6.2)
# ═══════════════════════════════════════════

# Per-chunk character cap for search_docs results.
# Set high enough to show full source context; truncation is still a last-resort
# safety net for pathological chunks.
MAX_CHUNK_CHARS: int = 24000
TRUNCATE_SUFFIX: str = "...[chunk truncated]"

# How many result lines to include in the LLM-facing summary.
SUMMARY_TOP_N: int = 5

# Max chars per field (doc_id / section) in the summary header line.
# Keeps one-line readability; full content stays in the results array.
SUMMARY_HEADER_MAX_CHARS: int = 60

# Relevance level thresholds (mirror chat_helpers._classify_relevance).
_RELEVANCE_HIGH_THRESHOLD: float = 0.8
_RELEVANCE_MEDIUM_THRESHOLD: float = 0.5

# 连续低相关度调用次数达到此阈值时，在 tool result 追加知识库覆盖提示。
_KB_COVERAGE_HINT_TRIGGER: int = 3
# 知识库可能未覆盖内容时附加到 tool result output 末尾的提示文案。
_KB_COVERAGE_HINT: str = (
    "\n\n⚠️ 知识库可能未覆盖该内容：已连续多次搜索未找到高度相关结果（相关度 < 80%）。"
    "建议如实告诉用户\"知识库可能未覆盖该内容\"，基于通用知识回答或建议用户上传相关文档，不要再继续改写查询。"
)

# Key used inside ToolContext.kb_coverage_counter dict.
_COVERAGE_COUNTER_KEY: str = "search_docs"


def _clamp_score(score: Any) -> float:
    """Clamp relevance score to [0.0, 1.0] to avoid >100% percentages."""
    try:
        value = float(score or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return min(1.0, max(0.0, value))


# ═══════════════════════════════════════════
# SearchDocsTool
# ═══════════════════════════════════════════

class SearchDocsArgs(BaseModel):
    query: str = Field(description="检索查询文本，例如 'ESP32-S3 GPIO 配置' 或 'CH340G 引脚定义'")
    doc_filter: str = Field(
        default="",
        description=(
            "可选：文档名过滤关键词（不区分大小写，子串匹配）。"
            "例如 'esp32-s3' 则只在文件名含 esp32-s3 的文档里搜。"
            "知道目标文档名时使用，避免检索到相似但错误的文档。"
        ),
    )


class SearchDocsOutput(BaseModel):
    """Describes envelope.data shape (output & kb_coverage_hint are lifted out
    by ToolRouter._success_envelope, so only results/truncated remain)."""
    results: list[dict] = Field(default_factory=list, description="检索结果列表")
    truncated: bool = Field(False, description="是否有 chunk 内容被截断")


class SearchDocsTool(ToolSpec):
    """Retrieve relevant chunks from the local chip-manual knowledge base.

    Retrieval strategy (top_k / kb_ids / threshold) is injected at construction
    from user frontend config; the LLM only decides the query.
    """
    name: str = "search_docs"
    description: str = (
        "检索本地知识库（芯片手册 PDF）。只需提供查询语句，检索策略"
        "（top_k / 知识库范围 / 相关度阈值）由用户前端配置，无需传入。"
    )
    args_schema: type = SearchDocsArgs
    output_schema: type[BaseModel] | None = SearchDocsOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 180
    max_retries: int = 1

    _top_k: int = PrivateAttr(default=5)
    _kb_ids: list[str] | None = PrivateAttr(default=None)
    _threshold: float = PrivateAttr(default=0.0)

    def __init__(self, top_k: int = 5, kb_ids: list[str] | None = None, threshold: float = 0.0):
        super().__init__()
        self._top_k = top_k
        self._kb_ids = kb_ids
        self._threshold = threshold

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Run KB search; return output + results + kb_coverage_hint."""
        from src.rag.search import search_docs_core

        query = args.get("query", "")
        doc_filter = args.get("doc_filter", "")
        results = await search_docs_core(
            query, self._top_k, self._kb_ids, self._threshold, doc_filter,
        )
        return _build_search_result(results, ctx)


def _build_search_result(results: list, ctx: ToolContext) -> dict:
    """Build result dict + manage coverage counter on ctx (per-request).

    Source IDs are assigned globally across all search_docs calls within the
    same request (ctx.source_counter) so src1/src2/... do not collide when the
    Agent calls search_docs multiple times. The counter advances by the number
    of small-chunk entries produced (>= FusedResult count after big-chunk merge).
    """
    start_idx = ctx.source_counter + 1
    entries, truncated = _build_result_dicts(results, start_idx)
    ctx.source_counter += len(entries)
    output_dict = _format_output_from_entries(entries, truncated)
    max_score = _extract_max_score(results)
    hint = _update_coverage_counter(ctx, max_score)
    output_dict["kb_coverage_hint"] = hint
    return output_dict


def _update_coverage_counter(ctx: ToolContext, max_score: float) -> str | None:
    """Increment/reset per-request coverage counter; return hint when triggered."""
    if max_score >= _RELEVANCE_HIGH_THRESHOLD:
        ctx.kb_coverage_counter[_COVERAGE_COUNTER_KEY] = 0
        return None
    count = ctx.kb_coverage_counter.get(_COVERAGE_COUNTER_KEY, 0) + 1
    ctx.kb_coverage_counter[_COVERAGE_COUNTER_KEY] = count
    if count >= _KB_COVERAGE_HINT_TRIGGER:
        return _KB_COVERAGE_HINT
    return None


def _format_output_from_entries(entries: list[dict], truncated: bool) -> dict:
    """Build the dict returned by SearchDocsTool: summary + full-source entries."""
    if not entries:
        return {"output": "未找到相关文档片段。", "results": [], "truncated": False}
    summary = _build_search_summary(entries, len(entries))
    return {"output": summary, "results": entries, "truncated": truncated}


def _extract_max_score(results: list) -> float:
    """Return the highest clamped score among results; 0.0 when empty."""
    if not results:
        return 0.0
    return max(_clamp_score(getattr(r, "score", 0.0)) for r in results)


def _build_result_dicts(results: list, start_idx: int = 1) -> tuple[list[dict], bool]:
    """Expand FusedResults into small-chunk-granular entries (big-chunk content).

    Each FusedResult yields 1..N entries: one per small chunk when the result
    was merged from a big chunk (content = big-chunk text shared across the
    group), or a single entry for standalone results. [srcN] ids are continuous
    across all small chunks in FusedResult order then small_chunks order.
    """
    out: list[dict] = []
    did_truncate = False
    next_idx = start_idx
    for r in results:
        entries, truncated, next_idx = _expand_result_entries(r, next_idx)
        out.extend(entries)
        if truncated:
            did_truncate = True
    return out, did_truncate


def _expand_result_entries(r: Any, start: int) -> tuple[list[dict], bool, int]:
    """Expand one FusedResult into 1..N small-chunk entries; return (entries, truncated, next_idx)."""
    chunks = _get_small_chunks(r) or [_standalone_chunk_info(r)]
    content, truncated = _truncate_chunk_content(getattr(r, "content", "") or "")
    base = _entry_base(r, content)
    entries = [_overlay_small_chunk(base, sc, start + i) for i, sc in enumerate(chunks)]
    return entries, truncated, start + len(chunks)


def _get_small_chunks(r: Any) -> list[dict]:
    """Return the small_chunks list from metadata, or [] when absent/empty."""
    meta = getattr(r, "metadata", {}) or {}
    chunks = meta.get("small_chunks")
    return list(chunks) if isinstance(chunks, list) and chunks else []


def _standalone_chunk_info(r: Any) -> dict:
    """Synthesize small-chunk info for a standalone (non-merged) FusedResult."""
    meta = getattr(r, "metadata", {}) or {}
    return {
        "id": meta.get("small_chunk_id", "") or "",
        "score": getattr(r, "score", 0.0),
        "chunk_index": meta.get("chunk_index", 0) or 0,
        "text": getattr(r, "content", "") or "",
    }


def _entry_base(r: Any, content: str) -> dict:
    """Build shared fields (from FusedResult + metadata) for all entries in a group."""
    meta = getattr(r, "metadata", {}) or {}
    return {
        "doc": getattr(r, "doc_id", "") or meta.get("doc_id", ""),
        "content": content,
        "title": meta.get("title", "未知来源"),
        "page_start": meta.get("page_start"),
        "page_end": meta.get("page_end"),
        "section_title": meta.get("section_title", ""),
        "source_url": meta.get("source_url", meta.get("source", "")),
        "category": meta.get("category", ""),
        "chunk_method": meta.get("chunk_method", ""),
        "kb_id": getattr(r, "kb_id", "") or "",
        "kb_name": getattr(r, "kb_name", "") or "",
        "big_chunk_id": meta.get("big_chunk_id", "") or "",
    }


def _overlay_small_chunk(base: dict, sc: dict, idx: int) -> dict:
    """Merge small-chunk-specific fields (id/score/chunk_index/text) onto base."""
    entry = {
        **base,
        "id": f"src{idx}",
        "score": _clamp_score(sc.get("score", 0.0)),
        "chunk_index": sc.get("chunk_index", 0) or 0,
        "small_chunk_id": sc.get("id", "") or "",
        "small_chunk_text": sc.get("text", "") or "",
    }
    entry["citation"] = _build_citation(entry)
    return entry


def _truncate_chunk_content(full: str) -> tuple[str, bool]:
    """Truncate chunk content to MAX_CHUNK_CHARS; return (content, did_truncate)."""
    if len(full) <= MAX_CHUNK_CHARS:
        return full, False
    return full[:MAX_CHUNK_CHARS] + TRUNCATE_SUFFIX, True


def _build_search_summary(simplified: list[dict], total: int) -> str:
    """Render summary with doc_id §section pXX so LLM can detect wrong-document fast."""
    lines = [f"找到 {total} 条相关片段："]
    for r in simplified[:SUMMARY_TOP_N]:
        score_pct = int(r["score"] * 100)
        sid = r.get("id") or "src?"
        lines.append(f"  [{sid}] {_format_summary_header(r)} (相关度 {score_pct}%)")
    if total > SUMMARY_TOP_N:
        lines.append(f"  ...共 {total} 条，当前显示前 {SUMMARY_TOP_N} 条。")
    lines.append("回答时必须用 [srcN] 格式引用上述片段，N 对应 src1/src2/...")
    return "\n".join(lines)


def _format_summary_header(r: dict) -> str:
    """Build 'doc_id §section pXX' header; each field truncated for one-line readability."""
    doc_id = (r.get("doc") or r.get("title") or "未知文档").strip()
    parts: list[str] = [doc_id[:SUMMARY_HEADER_MAX_CHARS]]
    section = (r.get("section_title") or "").strip()
    if section:
        parts.append(f"§{section[:SUMMARY_HEADER_MAX_CHARS]}")
    page_start = r.get("page_start")
    if isinstance(page_start, int) and page_start >= 0:
        parts.append(f"p{page_start}")
    return " ".join(parts)


# ═══════════════════════════════════════════
# Source SSE event builder (migrated from chat_helpers._build_source_event)
# ═══════════════════════════════════════════
# sse_adapter imports build_source_event_from_dict to emit `source` SSE events
# when the search_docs tool completes (spec ADDED Requirements). The payload
# format matches the legacy pre-RAG path so the frontend SourceCard keeps
# working unchanged.


def build_source_event_from_dict(res: dict, i: int) -> dict:
    """Build a source SSE event payload from a SearchDocsTool result dict.

    Accepts the dict shape produced by _overlay_small_chunk (one entry per
    small chunk, carrying big_chunk_id + small_chunk_id + small_chunk_text).
    Falls back to index-based id/citation when fields are missing (legacy
    callers like web_search / chat_helpers that don't set big-chunk fields).
    """
    sid = res.get("id") or f"src{i + 1}"
    score = _clamp_score(res.get("score", 0.0))
    return {
        "id": sid,
        "title": res.get("title", "未知来源"),
        "doc": res.get("doc", ""),
        "page": res.get("chunk_index", 0),  # backward compat (chunk index)
        "chunk_index": res.get("chunk_index", 0),
        "page_start": res.get("page_start"),
        "page_end": res.get("page_end"),
        "section_title": res.get("section_title", ""),
        "source_url": res.get("source_url", ""),
        "category": res.get("category", ""),
        "chunk_method": res.get("chunk_method", ""),
        "score": score,
        "score_percentage": round(score * 100, 1),
        "relevance_level": _classify_relevance(score),
        "citation": res.get("citation") or _build_citation(res),
        "excerpt": res.get("small_chunk_text", "") or res.get("content", "") or res.get("excerpt", ""),
        "kb_id": res.get("kb_id", ""),
        "kb_name": res.get("kb_name", ""),
        "small_chunk_id": res.get("small_chunk_id", ""),
        "big_chunk_id": res.get("big_chunk_id", ""),
    }


def _classify_relevance(score: float) -> str:
    """Map a relevance score to high/medium/low label."""
    if score >= _RELEVANCE_HIGH_THRESHOLD:
        return "high"
    if score >= _RELEVANCE_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _build_citation(res: dict) -> str:
    """Build citation string: kb_name / title / section_title / page."""
    parts = [res.get("kb_name") or "知识库", res.get("title", "未知来源")]
    section = res.get("section_title", "")
    if section:
        parts.append(section)
    page_start = res.get("page_start")
    if page_start is not None:
        parts.append(f"p{page_start}")
    return " / ".join(str(p) for p in parts if p)
