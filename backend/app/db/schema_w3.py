"""W3 schema upgrade: message sequence, session version, tombstones, todos, MCP.

The project runs ``init_db`` (create_all + idempotent DDL) at startup rather
than ``alembic upgrade head`` because the legacy revision chain is incomplete.
This module keeps that upgrade in one place and guards it with
``PRAGMA user_version`` so repeated starts are cheap.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

_MESSAGE_COLUMNS = (
    "seq INTEGER",
    "updated_at DATETIME",
    "status TEXT NOT NULL DEFAULT 'complete'",
    "client_key TEXT",
    "tool_call_id TEXT",
    "tool_name TEXT",
)

_SESSION_COLUMNS = (
    "version INTEGER NOT NULL DEFAULT 0",
    "messages_imported INTEGER NOT NULL DEFAULT 0",
)

_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS session_tombstones (
        session_id TEXT PRIMARY KEY,
        deleted_at DATETIME NOT NULL,
        epoch TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS todos (
        session_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        priority TEXT NOT NULL DEFAULT 'medium',
        updated_at DATETIME,
        PRIMARY KEY (session_id, seq),
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_servers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        command TEXT NOT NULL,
        args JSON,
        env JSON,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        updated_at DATETIME
    )
    """,
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_messages_session_order "
    "ON messages(session_id, created_at, seq)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_session_seq "
    "ON messages(session_id, seq)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_session_client_key "
    "ON messages(session_id, client_key)",
    "CREATE INDEX IF NOT EXISTS idx_todos_session ON todos(session_id)",
)


def apply_w3_schema(conn) -> None:
    """Upgrade a connection to SCHEMA_VERSION; no-op when already current."""
    if _schema_version(conn) >= SCHEMA_VERSION:
        return
    _add_columns(conn, "messages", _MESSAGE_COLUMNS)
    _add_columns(conn, "sessions", _SESSION_COLUMNS)
    _run_statements(conn, _TABLES)
    _backfill_messages(conn)
    _run_statements(conn, _INDEXES)
    _rebuild_messages_fts(conn)
    conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))


def _schema_version(conn) -> int:
    value = conn.execute(text("PRAGMA user_version")).scalar()
    return int(value or 0)


def _add_columns(conn, table: str, definitions: tuple[str, ...]) -> None:
    for definition in definitions:
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {definition}"))
        except Exception as exc:
            logger.debug("W3 column already present on %s: %s", table, exc)


def _run_statements(conn, statements: tuple[str, ...]) -> None:
    for statement in statements:
        conn.execute(text(statement))


def _backfill_messages(conn) -> None:
    conn.execute(text(
        """
        WITH ranked AS (
            SELECT rowid AS rid,
                   ROW_NUMBER() OVER (
                       PARTITION BY session_id ORDER BY created_at, rowid
                   ) AS rn
            FROM messages
        )
        UPDATE messages
        SET seq = (SELECT rn FROM ranked WHERE ranked.rid = messages.rowid)
        """
    ))
    conn.execute(text(
        "UPDATE messages SET updated_at = created_at WHERE updated_at IS NULL"
    ))


def _rebuild_messages_fts(conn) -> None:
    try:
        conn.execute(text(
            "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"
        ))
    except Exception as exc:
        logger.warning("messages_fts rebuild skipped: %s", exc)
