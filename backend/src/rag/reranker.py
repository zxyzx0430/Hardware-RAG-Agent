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
# A broken mirror must not hold a request thread forever. This is a download
# timeout only; the model load itself runs in the background warmup thread.
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

# Model name / thresholds are configurable via settings (env vars).
_RERANKER_MODEL = settings.reranker_model
_RERANKER: Optional[object] = None  # lazy singleton; None=not loaded, False=unavailable
# M1: predict 失败后基于时间戳退避，避免永久禁用。_failed_at 记录失败时间，
# 在 _RETRY_INTERVAL_S 内跳过 reranker，过后自动重试。间隔从 settings 读取。
_failed_at: float | None = None
_RETRY_INTERVAL_S: float = float(settings.reranker_retry_interval_sec)
# Loading is serialized separately from prediction. Requests must never wait
# for a cold model load: they fall back to RRF while the warmup thread works.
_LOAD_LOCK = threading.Lock()
_PREDICT_LOCK = threading.Lock()

# ── Load status (read by /api/rag/reranker and search_docs metadata) ──
STATE_NOT_LOADED: str = "not_loaded"
STATE_LOADING: str = "loading"
STATE_LOADED: str = "loaded"
STATE_UNAVAILABLE: str = "unavailable"

# _CONTROL_LOCK guards the status fields below. It is never held while the
# model loads, so status reads and reload requests stay non-blocking.
_CONTROL_LOCK = threading.Lock()
# Number of in-flight load attempts (a reload reservation counts as one).
_ACTIVE_LOADS: int = 0
_LAST_REASON: str | None = None
_LAST_AVAILABLE_MB: int | None = None
_LAST_REQUIRED_MB: int = int(settings.reranker_min_available_mb)
_LOADED_AT: float | None = None


def get_reranker():
    """Load the cross-encoder model (singleton).

    This is intentionally a blocking function for the background warmup thread
    only. Request handlers must use :func:`rerank`, which never waits for a
    cold load and falls back to the fused RRF order.
    """
    global _RERANKER
    if _RERANKER is not None:
        return _RERANKER if _RERANKER is not False else None
    _enter_load()
    try:
        with _LOAD_LOCK:
            if _RERANKER is not None:
                return _RERANKER if _RERANKER is not False else None
            return _load_model()
    finally:
        _end_load()


def _load_model() -> Optional[object]:
    """Blocking load body. Caller holds _LOAD_LOCK."""
    available_mb = _available_memory_mb()
    required_mb = int(settings.reranker_min_available_mb)
    _record_memory(available_mb, required_mb)
    if available_mb is not None and available_mb < required_mb:
        reason = (
            f"insufficient memory: {available_mb} MB available, "
            f"{required_mb} MB required"
        )
        logger.warning(
            "[Reranker] Skipping load: only %d MB available, need >= %d MB; "
            "retrieval will use RRF fallback",
            available_mb, required_mb,
        )
        _mark_unavailable(reason)
        return None
    try:
        model = _load_cross_encoder()
    except Exception as exc:  # noqa: BLE001 — degraded mode is a feature
        logger.error("[Reranker] Base model load failed: %s", exc)
        _mark_unavailable(f"model load failed: {exc}")
        return None
    _mark_loaded(model)
    _log_loaded(model)
    return model


def _load_cross_encoder() -> object:
    """Import and construct the cross-encoder; raises on failure."""
    from sentence_transformers import CrossEncoder
    _configure_torch_threads()
    logger.info("[Reranker] Loading %s (base model, fast load)...", _RERANKER_MODEL)
    return CrossEncoder(_RERANKER_MODEL, max_length=512)


def _configure_torch_threads() -> None:
    """Pin torch to one thread; the app must stay single-threaded natively."""
    import torch
    try:
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    except (RuntimeError, ValueError):
        # Interop threads can only be set before parallel work starts;
        # a late call is harmless if another module already initialized.
        pass


def _log_loaded(model: object) -> None:
    """Log load completion with a parameter count when the model exposes it."""
    logger.info("[Reranker] Loaded base model (FP32, ~280MB)")
    try:
        n_params = sum(p.numel() for p in model.model.parameters())
        logger.info("[Reranker] Loaded OK, params=%.1fM", n_params / 1e6)
    except Exception:
        logger.info("[Reranker] Loaded OK")


def _available_memory_mb() -> int | None:
    """Read available system RAM; None when psutil cannot answer."""
    try:
        import psutil
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        return None


def _record_memory(available_mb: int | None, required_mb: int) -> None:
    """Store the latest memory readings exposed by the status object."""
    global _LAST_AVAILABLE_MB, _LAST_REQUIRED_MB
    with _CONTROL_LOCK:
        _LAST_AVAILABLE_MB = available_mb
        _LAST_REQUIRED_MB = required_mb


def _mark_loaded(model: object) -> None:
    """Publish a successfully loaded model and its load timestamp."""
    global _RERANKER, _LAST_REASON, _LOADED_AT
    with _CONTROL_LOCK:
        _RERANKER = model
        _LAST_REASON = None
        _LOADED_AT = time.time()


def _mark_unavailable(reason: str) -> None:
    """Publish the unavailable sentinel together with a human-readable reason."""
    global _RERANKER, _LAST_REASON
    with _CONTROL_LOCK:
        _RERANKER = False
        _LAST_REASON = reason


def _enter_load() -> None:
    """Mark a load attempt as in flight; status reads report 'loading'."""
    global _ACTIVE_LOADS
    with _CONTROL_LOCK:
        _ACTIVE_LOADS += 1


def _end_load() -> None:
    """Release one load slot (never below zero)."""
    global _ACTIVE_LOADS
    with _CONTROL_LOCK:
        _ACTIVE_LOADS = max(0, _ACTIVE_LOADS - 1)


def get_reranker_status() -> dict:
    """Return the current load status without touching the blocking loader.

    Never waits for a cold load, so API routes and tool results stay fast.
    """
    with _CONTROL_LOCK:
        return _status_locked()


def _status_locked() -> dict:
    """Build the status object. Caller must hold _CONTROL_LOCK."""
    return {
        "state": _state_locked(),
        "model": _RERANKER_MODEL,
        "available_mb": _LAST_AVAILABLE_MB,
        "required_mb": _LAST_REQUIRED_MB,
        "reason": _LAST_REASON,
        "loaded_at": _LOADED_AT,
        "retry_interval_sec": _RETRY_INTERVAL_S,
    }


def _state_locked() -> str:
    """Resolve the status enum from singleton state. Caller holds the lock."""
    if _ACTIVE_LOADS > 0:
        return STATE_LOADING
    if _RERANKER is None:
        return STATE_NOT_LOADED
    return STATE_UNAVAILABLE if _RERANKER is False else STATE_LOADED


def reload_reranker() -> dict:
    """Reset the singleton and load in a daemon thread; return immediately.

    Returns ``{"started": bool, "status": <status object>}``. While any load
    is already in flight the call is a no-op (``started=False``), so two
    threads never load the model concurrently.
    """
    global _RERANKER, _failed_at, _LAST_REASON, _LOADED_AT, _ACTIVE_LOADS
    with _CONTROL_LOCK:
        if _ACTIVE_LOADS > 0:
            return {"started": False, "status": _status_locked()}
        _ACTIVE_LOADS = 1
        _RERANKER = None
        _failed_at = None
        _LAST_REASON = None
        _LOADED_AT = None
        try:
            threading.Thread(
                target=_reload_worker, daemon=True, name="reranker-reload",
            ).start()
        except Exception as exc:  # noqa: BLE001 — surface as a failed start
            _ACTIVE_LOADS = 0
            _LAST_REASON = f"reload failed: {exc}"
            return {"started": False, "status": _status_locked()}
        return {"started": True, "status": _status_locked()}


def _reload_worker() -> None:
    """Daemon-thread body for reload_reranker()."""
    try:
        get_reranker()
    except Exception as exc:  # noqa: BLE001 — status must report the failure
        logger.error("[Reranker] Reload failed: %s", exc)
        _mark_unavailable(f"model load failed: {exc}")
    finally:
        _end_load()


def _is_reranker_in_cooldown() -> bool:
    """True if reranker recently failed and is within the retry backoff window."""
    return _failed_at is not None and time.time() - _failed_at < _RETRY_INTERVAL_S


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
    """Rerank chunks by query relevance; returns (index, score) desc.

    If the reranker is unavailable or in cooldown, returns the original
    order with score=0.0 so the caller can fall back to RRF gracefully.
    """
    if not chunks:
        return []
    global _failed_at
    if _is_reranker_in_cooldown():
        return [(i, 0.0) for i in range(len(chunks))]
    # Never call get_reranker() here: a cold load may be downloading hundreds
    # of MB. The warmup thread owns loading; requests fall back to RRF.
    model = _RERANKER
    if model is None or model is False:
        return [(i, 0.0) for i in range(len(chunks))]
    try:
        with _PREDICT_LOCK:
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
