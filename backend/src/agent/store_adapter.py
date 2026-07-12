"""Store API adapter — InMemoryStore + embedding for semantic session search.

Provides a Store API fallback when FTS5 lexical search returns no results.
FTS5 is precise (keyword match, persistent across restarts), Store API is
semantic (meaning similarity, in-memory per-process).

Data flow:
- index_session_message_to_store(): called alongside FTS5 indexing to keep
  Store in sync (dual-write). No-op when embedding unconfigured.
- search_session_history_via_store(): semantic search via Store API. Returns
  [] when Store unavailable or embedding unconfigured — caller falls back to
  FTS5 order gracefully.

Spec: .trae/specs/migrate-langchain-1x-new-api/ Task 6 (Store API 与 FTS5 并存).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from src.config.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_SEARCH_LIMIT: int = 5
# text-embedding-3-small default dims. Actual dims probed lazily on first put
# when embedding is configured — mismatched dims will raise at index time.
_EMBEDDING_DIMS: int = 1536

_store: Optional[Any] = None
_store_init_attempted: bool = False


def _get_store() -> Optional[Any]:
    """Lazy-init InMemoryStore with embedding index. None if embedding unconfigured.

    Singleton: once init fails (no api_key / import error), subsequent calls
    return None without retry to avoid repeated logging on every search.
    """
    global _store, _store_init_attempted
    if _store is not None:
        return _store
    if _store_init_attempted:
        return None
    _store_init_attempted = True
    try:
        from langgraph.store.memory import InMemoryStore
        from langchain_openai import OpenAIEmbeddings
    except ImportError as exc:
        logger.error("Store API unavailable (import failed): %s", exc)
        return None
    api_key = settings.embedding_api_key
    if not api_key:
        logger.debug("Store API disabled: embedding_api_key not configured")
        return None
    try:
        embeddings = OpenAIEmbeddings(
            model=settings.embedding_model,
            openai_api_key=api_key,
            openai_api_base=settings.embedding_base_url,
            tiktoken_enabled=False,
            check_embedding_ctx_length=False,
            chunk_size=10,
        )
        _store = InMemoryStore(index={"dims": _EMBEDDING_DIMS, "embed": embeddings})
        logger.info(
            "Store API initialized (InMemoryStore + %s dims=%s)",
            settings.embedding_model, _EMBEDDING_DIMS,
        )
        return _store
    except Exception as exc:
        logger.error("Store API init failed: %s", exc)
        return None


def index_session_message_to_store(
    session_id: str, user_msg: str, assistant_msg: str,
) -> None:
    """Index a session message pair into the Store for semantic search.

    Called alongside FTS5 indexing (dual-write). No-op when Store unavailable.
    Combines user + assistant into one text for richer semantic signal.
    """
    store = _get_store()
    if store is None:
        return
    try:
        key = f"{session_id}#{uuid.uuid4().hex[:8]}"
        store.put(
            ("sessions", session_id),
            key=key,
            value={
                "session_id": session_id,
                "user_msg": user_msg,
                "assistant_msg": assistant_msg,
                "timestamp": datetime.now(datetime.UTC).isoformat(),
            },
        )
    except Exception as exc:
        logger.error("index_session_message_to_store failed session=%s: %s", session_id, exc)


def search_session_history_via_store(
    query: str, limit: int = _DEFAULT_SEARCH_LIMIT,
) -> list[dict]:
    """Semantic search via Store API. Returns [] if Store unavailable or query empty.

    Caller (SearchHistoryTool) should try FTS5 first, then fall back to this
    when FTS5 returns no results — combines lexical precision with semantic
    coverage.
    """
    store = _get_store()
    if store is None or not query.strip():
        return []
    try:
        items = store.search(("sessions",), query=query, limit=limit)
        return [_extract_item(item) for item in items]
    except Exception as exc:
        logger.error("search_session_history_via_store failed query=%s: %s", query, exc)
        return []


def _extract_item(item: Any) -> dict:
    """Extract a result dict from a Store Item (handles attr/dict access).

    langgraph InMemoryStore.search returns Item objects (not dicts), so we
    try attribute access first, then dict access as fallback. The original
    one-liner had an operator-precedence bug (or vs if/else) that always
    returned {} for Item objects — fixed by explicit branching.
    """
    if isinstance(item, dict):
        value = item.get("value", {}) or {}
    else:
        value = getattr(item, "value", None) or {}
    return {
        "session_id": value.get("session_id", "") if isinstance(value, dict) else "",
        "user_msg": value.get("user_msg", "") if isinstance(value, dict) else "",
        "assistant_msg": value.get("assistant_msg", "") if isinstance(value, dict) else "",
        "timestamp": value.get("timestamp", "") if isinstance(value, dict) else "",
    }
