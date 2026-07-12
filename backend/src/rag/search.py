"""
Hardware RAG Agent — 文档检索核心逻辑。

从 app/api/chat_routes.py 的 RAG 检索段抽出，
供路由层和 agent 工具层复用，消除 SearchDocsTool stub 与真实检索的割裂。
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# LRU cache for search results (spec: 5 min TTL, 256 entries)
# ═══════════════════════════════════════════
# Key = (query, sorted kb_ids, top_k, threshold). OrderedDict implements LRU
# via move_to_end on hit + popitem(last=False) on eviction.
_SEARCH_CACHE: "OrderedDict[tuple, tuple[list, float]]" = OrderedDict()
_CACHE_TTL_SECONDS: int = 300
_CACHE_MAX_SIZE: int = 256

# Timing/logging constants
_MS_PER_SECOND: int = 1000
_LOG_QUERY_TRUNCATE: int = 50


def _observe_rag_retrieval(elapsed_seconds: float) -> None:
    """Observe RAG retrieval latency on the rag_retrieval_seconds histogram.

    Lazy import avoids a circular dependency between app.main and src.rag.search.
    Silently skips if metrics are unavailable.
    """
    try:
        from app.main import _RAG_RETRIEVAL_SECONDS
        _RAG_RETRIEVAL_SECONDS.observe(elapsed_seconds)
    except Exception:
        pass


def _cache_key(query: str, kb_ids: list[str] | None, top_k: int, threshold: float, doc_filter: str = "") -> tuple:
    """Build a hashable cache key; kb_ids sorted for order-insensitive match."""
    return (query, tuple(sorted(kb_ids)) if kb_ids else (), top_k, threshold, doc_filter)


def _get_cached(key: tuple) -> list | None:
    """Return cached results if within TTL, else None. Updates LRU order on hit."""
    if key in _SEARCH_CACHE:
        results, ts = _SEARCH_CACHE[key]
        if time.time() - ts < _CACHE_TTL_SECONDS:
            _SEARCH_CACHE.move_to_end(key)
            return results
        del _SEARCH_CACHE[key]
    return None


def _set_cached(key: tuple, results: list) -> None:
    """Store results in cache; evict oldest entries beyond _CACHE_MAX_SIZE."""
    _SEARCH_CACHE[key] = (results, time.time())
    _SEARCH_CACHE.move_to_end(key)
    while len(_SEARCH_CACHE) > _CACHE_MAX_SIZE:
        _SEARCH_CACHE.popitem(last=False)


async def search_docs_core(
    query: str,
    top_k: int = 5,
    kb_ids: list[str] | None = None,
    threshold: float = 0.0,
    doc_filter: str = "",
) -> list[Any]:
    """
    检索知识库，返回匹配的文档片段。

    Args:
        query: 检索查询文本
        top_k: 返回结果数上限
        kb_ids: 指定知识库 ID 列表，None/空=所有启用的
        threshold: 相关性阈值，低于此值的结果被过滤
        doc_filter: 文档名过滤关键词（子串匹配，不区分大小写）。
                    例如 "esp32-s3" 只在文件名含 esp32-s3 的文档里搜。

    Returns:
        检索结果列表，元素结构见 kb_manager.search_all_enabled 返回值。
        失败时返回空列表（不抛异常，调用方需检查长度）。
    """
    if not query or not query.strip():
        logger.debug("[search_docs_core] empty query, skip")
        return []

    # Record start time at entry, before any cache work
    start_time = time.perf_counter()

    # LRU cache: short-circuit if this query was seen recently
    key = _cache_key(query, kb_ids, top_k, threshold, doc_filter)
    cached = _get_cached(key)
    if cached is not None:
        elapsed_ms = int((time.perf_counter() - start_time) * _MS_PER_SECOND)
        elapsed_seconds = elapsed_ms / _MS_PER_SECOND
        logger.info(
            f"[search_docs_core] cache hit query='{query[:60]}' top_k={top_k} "
            f"threshold={threshold:.2f} doc_filter='{doc_filter}'"
        )
        logger.info(
            "search_docs_core cache_hit=true total_ms=%d result_count=%d query=%r",
            elapsed_ms, len(cached), query[:_LOG_QUERY_TRUNCATE],
        )
        _observe_rag_retrieval(elapsed_seconds)
        return cached

    try:
        from src.rag.kb_manager import get_kb_manager
        kb_manager = get_kb_manager()
        results = await kb_manager.search_all_enabled(
            query, k=top_k, kb_ids=kb_ids, score_threshold=threshold,
            doc_filter=doc_filter,
        )
        logger.info(
            f"[search_docs_core] query='{query[:60]}' top_k={top_k} "
            f"threshold={threshold:.2f} doc_filter='{doc_filter}' "
            f"found={len(results)}"
        )
        _set_cached(key, results)
        elapsed_ms = int((time.perf_counter() - start_time) * _MS_PER_SECOND)
        elapsed_seconds = elapsed_ms / _MS_PER_SECOND
        logger.info(
            "search_docs_core cache_hit=false total_ms=%d result_count=%d query=%r",
            elapsed_ms, len(results), query[:_LOG_QUERY_TRUNCATE],
        )
        _observe_rag_retrieval(elapsed_seconds)
        return results
    except Exception as e:
        logger.warning(f"[search_docs_core] 检索失败: {e}")
        return []
