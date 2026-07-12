"""Cross-encoder reranker for RAG retrieval results.

Uses BAAI/bge-reranker-base to re-score query-chunk pairs and re-rank
the fused retrieval results. This fixes context_precision issues where
BM25+RRF surfaces irrelevant chunks (e.g. HardFault_Handler chunk for
a GPIO clock question).

Singleton pattern: model loads once per process (~5s, ~280MB RAM).
Falls back gracefully if model unavailable — caller keeps RRF order.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from src.config.settings import settings

logger = logging.getLogger(__name__)

# Use HF mirror for faster downloads in China (set before importing transformers)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# Model name / thresholds are configurable via settings (env vars).
_RERANKER_MODEL = settings.reranker_model
_RERANKER: Optional[object] = None  # lazy singleton; None=not loaded, False=unavailable
# M1: predict 失败后基于时间戳退避，避免永久禁用。_failed_at 记录失败时间，
# 在 _RETRY_INTERVAL_S 内跳过 reranker，过后自动重试。间隔从 settings 读取。
_failed_at: float | None = None
_RETRY_INTERVAL_S: float = float(settings.reranker_retry_interval_sec)
# P1: 多线程并行搜索时保护单例加载 + predict 失败标记
_RERANKER_LOCK = threading.Lock()


def get_reranker():
    """Lazy-load cross-encoder model (singleton).

    Returns:
        CrossEncoder instance if available, None if load failed.
        Once marked unavailable (False), subsequent calls return None without retry.
    """
    global _RERANKER
    if _RERANKER is not None:
        return _RERANKER if _RERANKER is not False else None
    try:
        from sentence_transformers import CrossEncoder
        logger.info(f"[Reranker] Loading {_RERANKER_MODEL} (base model, fast load)...")
        _RERANKER = CrossEncoder(_RERANKER_MODEL, max_length=512)
        logger.info("[Reranker] Loaded base model (FP32, ~280MB)")
        try:
            n_params = sum(p.numel() for p in _RERANKER.model.parameters())
            logger.info(f"[Reranker] Loaded OK, params={n_params / 1e6:.1f}M")
        except Exception:
            logger.info("[Reranker] Loaded OK")
        return _RERANKER
    except Exception as e:
        logger.error(f"[Reranker] Base model load failed: {e}")
        _RERANKER = False  # mark as unavailable (don't retry on every search)
        return None


def _is_reranker_in_cooldown() -> bool:
    """True if reranker recently failed and is within the retry backoff window."""
    if _failed_at is None:
        return False
    return time.time() - _failed_at < _RETRY_INTERVAL_S


def _safe_float_score(s) -> float:
    """Coerce a model score (scalar or 1-element array) to float."""
    try:
        return float(s)
    except (TypeError, ValueError):
        return float(s[0]) if hasattr(s, "__iter__") else 0.0


def _ranked_scores(raw_scores, top_k: int) -> list[tuple[int, float]]:
    """Build (index, score) pairs sorted desc, optionally truncated to top_k."""
    indexed = [(i, _safe_float_score(s)) for i, s in enumerate(raw_scores)]
    indexed.sort(key=lambda x: x[1], reverse=True)
    return indexed[:top_k] if top_k > 0 else indexed


def rerank(query: str, chunks: list[str], top_k: int = 0) -> list[tuple[int, float]]:
    """Rerank chunks by query relevance using cross-encoder.

    Args:
        query: User query text
        chunks: List of chunk content strings
        top_k: If >0, only return top_k results; if 0, return all (reranked)

    Returns:
        List of (original_index, rerank_score) sorted by score descending.
        If reranker unavailable or in cooldown, returns indices in original
        order with score=0.0 so caller can fall back to RRF order gracefully.
    """
    if not chunks:
        return []
    global _failed_at
    if _is_reranker_in_cooldown():
        return [(i, 0.0) for i in range(len(chunks))]
    with _RERANKER_LOCK:
        if _is_reranker_in_cooldown():
            return [(i, 0.0) for i in range(len(chunks))]
        model = get_reranker()
        if not model:
            return [(i, 0.0) for i in range(len(chunks))]
        try:
            pairs = [(query, c) for c in chunks]
            raw_scores = model.predict(pairs)
            _failed_at = None  # success — clear cooldown
            return _ranked_scores(raw_scores, top_k)
        except Exception as e:
            logger.warning(
                f"[Reranker] predict failed, backing off {_RETRY_INTERVAL_S}s: {e}"
            )
            _failed_at = time.time()
            return [(i, 0.0) for i in range(len(chunks))]
