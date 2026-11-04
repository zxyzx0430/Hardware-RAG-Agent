"""Full-text search over the single ``messages_fts`` index.

The legacy ``sessions_fts`` database and its in-memory store fallback are
retired. Both ``POST /api/search`` and the Agent ``search_history`` tool read
the same rows by joining ``messages_fts.rowid`` to ``messages.rowid``; a LIKE
query is used only when FTS returns no rows or the FTS query is invalid.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from sqlalchemy import text

from app.db.time_utils import to_utc_iso_z

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 5

_FTS_SQL = text(
    """
    SELECT m.id, m.session_id, m.role, m.content, m.created_at, m.seq, s.title
    FROM messages_fts f
    JOIN messages m ON m.rowid = f.rowid
    JOIN sessions s ON s.id = m.session_id
    WHERE messages_fts MATCH :query
    ORDER BY rank, m.created_at DESC, m.seq DESC
    LIMIT :limit
    """
)

_LIKE_SQL = text(
    """
    SELECT m.id, m.session_id, m.role, m.content, m.created_at, m.seq, s.title
    FROM messages m
    JOIN sessions s ON s.id = m.session_id
    WHERE m.content LIKE :pattern ESCAPE '\\'
    ORDER BY m.created_at DESC, m.seq DESC
    LIMIT :limit
    """
)


def search_messages(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[dict[str, Any]]:
    """Return message hits ordered by FTS rank (newest first on ties)."""
    normalized = query.strip() if isinstance(query, str) else ""
    if not normalized or limit <= 0:
        return []
    rows = _try_fts(normalized, limit)
    if not rows:
        rows = _try_like(normalized, limit)
    return [_row_to_result(row) for row in rows]


def _try_fts(query: str, limit: int) -> list:
    try:
        return _fetch(_FTS_SQL, {"query": query, "limit": limit})
    except Exception as exc:
        logger.warning("messages_fts search failed; falling back to LIKE: %s", exc)
        return []


def _try_like(query: str, limit: int) -> list:
    try:
        return _fetch(_LIKE_SQL, {"pattern": _like_pattern(query), "limit": limit})
    except Exception as exc:
        logger.error("message LIKE search failed: %s", exc)
        return []


def _fetch(statement, params: dict[str, Any]) -> list:
    from app.db.database import SessionLocal

    with SessionLocal() as db:
        return db.execute(statement, params).fetchall()


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _row_to_result(row) -> dict[str, Any]:
    return {
        "message_id": row[0],
        "session_id": row[1],
        "role": row[2],
        "content": row[3] or "",
        "created_at": _iso(row[4]),
        "seq": row[5],
        "session_title": row[6],
    }


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return to_utc_iso_z(value)
    try:
        return to_utc_iso_z(datetime.datetime.fromisoformat(str(value)))
    except ValueError:
        return str(value)
