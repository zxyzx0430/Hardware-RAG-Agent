"""FTS5-backed session history search (P0 Task 4).

Stores user/assistant message pairs in a SQLite FTS5 virtual table so the
Agent can answer "上次你帮我生成的代码呢" by full-text search.

DB file: <project_root>/data/agent_sessions.db (data dir auto-created).
SQLite FTS5 is part of the Python stdlib sqlite3 module (Python 3.7+), so
there are no external dependencies.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from src.agent.path_guard import PROJECT_ROOT

logger = logging.getLogger(__name__)

# DB path resolved via the shared PROJECT_ROOT constant (path_guard) so cwd and
# module relocation do not matter. PROJECT_ROOT = repo root (agent/), matching
# the previous Path(__file__).resolve().parents[3] resolution.
_DB_PATH: Path = PROJECT_ROOT / "data" / "agent_sessions.db"

# Default result cap for search_session_history.
_DEFAULT_SEARCH_LIMIT: int = 5


def _get_conn() -> sqlite3.Connection:
    """Open a connection to the session DB (creates file + dir if missing)."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_session_db() -> None:
    """Create the FTS5 virtual table if not exists. Idempotent."""
    with _get_conn() as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5("
            "session_id, user_msg, assistant_msg, timestamp"
            ")"
        )


def index_session_message(session_id: str, user_msg: str, assistant_msg: str) -> None:
    """Insert a user/assistant message pair into FTS5 + Store API (dual-write).

    FTS5 is the primary index (persistent, lexical). Store API is the semantic
    fallback (in-memory, embedding-based). Store API write is isolated — its
    failure never affects FTS5.
    """
    ts = datetime.now(datetime.UTC).isoformat()
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions_fts(session_id, user_msg, assistant_msg, timestamp) "
                "VALUES(?, ?, ?, ?)",
                (session_id, user_msg, assistant_msg, ts),
            )
    except sqlite3.Error as exc:
        logger.error("index_session_message failed session=%s err=%s", session_id, exc)
    _index_to_store(session_id, user_msg, assistant_msg)


def _index_to_store(session_id: str, user_msg: str, assistant_msg: str) -> None:
    """Dual-write to Store API for semantic search. Isolated from FTS5 path.

    Lazy import keeps module load cheap when Store API is unused. No-op when
    embedding unconfigured (store_adapter handles that gracefully).
    """
    try:
        from src.agent.store_adapter import index_session_message_to_store
        index_session_message_to_store(session_id, user_msg, assistant_msg)
    except Exception as exc:
        logger.debug("Store API dual-write skipped: %s", exc)


def search_session_history(query: str, limit: int = _DEFAULT_SEARCH_LIMIT) -> list[dict]:
    """FTS5 MATCH search across user_msg + assistant_msg. Returns newest-first."""
    if not query.strip():
        return []
    try:
        return _run_match_query(query, limit)
    except sqlite3.Error as exc:
        logger.error("search_session_history failed query=%s err=%s", query, exc)
        return []


def _run_match_query(query: str, limit: int) -> list[dict]:
    """Execute the FTS5 MATCH query and return rows as dicts."""
    with _get_conn() as conn:
        cur = conn.execute(
            "SELECT session_id, user_msg, assistant_msg, timestamp "
            "FROM sessions_fts WHERE sessions_fts MATCH ? "
            "ORDER BY rowid DESC LIMIT ?",
            (query, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def search_session_history_via_store(query: str, limit: int = _DEFAULT_SEARCH_LIMIT) -> list[dict]:
    """Semantic search via Store API (fallback when FTS5 returns no results).

    Re-exported here so callers can import both FTS5 + Store API search from
    a single module (session_search). Returns [] when Store API unavailable
    or embedding unconfigured — caller falls back to FTS5 order gracefully.
    """
    from src.agent.store_adapter import search_session_history_via_store as _store_search
    return _store_search(query, limit)
