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
from typing import Optional

logger = logging.getLogger(__name__)

# Use HF mirror for faster downloads in China (set before importing transformers)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

_RERANKER_MODEL = "BAAI/bge-reranker-base"
_RERANKER: Optional[object] = None  # lazy singleton; None=not loaded, False=unavailable
# P1: predict 失败后标记不可用，避免每个 KB 每次搜索都重复尝试 predict
_RERANKER_PREDICT_FAILED: bool = False
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


def rerank(query: str, chunks: list[str], top_k: int = 0) -> list[tuple[int, float]]:
    """Rerank chunks by query relevance using cross-encoder.

    Args:
        query: User query text
        chunks: List of chunk content strings
        top_k: If >0, only return top_k results; if 0, return all (reranked)

    Returns:
        List of (original_index, rerank_score) sorted by score descending.
        If reranker unavailable, returns indices in original order with score=0.0
        so caller can fall back to RRF order gracefully.
    """
    if not chunks:
        return []
    # P1: predict 之前失败过（如 "Modality 'audio' is not supported"）— 不重试
    global _RERANKER_PREDICT_FAILED
    if _RERANKER_PREDICT_FAILED:
        return [(i, 0.0) for i in range(len(chunks))]
    # P1: 加锁保护模型加载 + predict，避免多线程并行搜索时重复加载
    with _RERANKER_LOCK:
        if _RERANKER_PREDICT_FAILED:
            return [(i, 0.0) for i in range(len(chunks))]
        model = get_reranker()
        if not model:
            return [(i, 0.0) for i in range(len(chunks))]

        try:
            pairs = [(query, c) for c in chunks]
            raw_scores = model.predict(pairs)
            indexed = []
            for i, s in enumerate(raw_scores):
                try:
                    val = float(s)
                except (TypeError, ValueError):
                    val = float(s[0]) if hasattr(s, '__iter__') else 0.0
                indexed.append((i, val))
            indexed.sort(key=lambda x: x[1], reverse=True)
            if top_k > 0:
                indexed = indexed[:top_k]
            return indexed
        except Exception as e:
            logger.warning(f"[Reranker] predict failed, disabling reranker for this session: {e}")
            _RERANKER_PREDICT_FAILED = True  # 标记永久不可用，避免重复尝试
            return [(i, 0.0) for i in range(len(chunks))]
