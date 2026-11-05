"""Memory v2 schema upgrade.

``Base.metadata.create_all`` only creates missing tables; it never adds columns
to an existing ``memory_entries`` table. This module performs the idempotent
``ALTER TABLE``/``CREATE TABLE IF NOT EXISTS`` upgrade that ``init_db`` runs
after ``apply_w3_schema`` and before its commit.

Legacy rows are identified by ``schema_version < 2``. The column is added to
old databases with ``DEFAULT 0`` solely as that marker; after the row backfill
every legacy row is stamped with ``MEMORY_SCHEMA_VERSION``. Fresh tables get
``DEFAULT 2`` from the ORM model, so a second startup performs no row work.
The legacy ``longTermMemory`` setting is imported once as a proposed user
preference; the setting itself is kept for compatibility.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid

from sqlalchemy import text

from app.db.memory_models import MEMORY_SCHEMA_VERSION, SCOPE_TO_TIER
from app.db.memory_store import MAX_CONTENT_CHARS, contains_secret
from app.db.project_identity import current_project_id, is_path_like

logger = logging.getLogger(__name__)

SCHEMA_VERSION = MEMORY_SCHEMA_VERSION

_ENTRY_COLUMNS = (
    ("tier", "TEXT NOT NULL DEFAULT 'project'"),
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("project_id", "TEXT NOT NULL DEFAULT ''"),
    ("principal_id", "TEXT NOT NULL DEFAULT 'local'"),
    ("evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
    # Marker for the legacy-row backfill; overwritten with SCHEMA_VERSION.
    ("schema_version", "INTEGER NOT NULL DEFAULT 0"),
)

_SNAPSHOT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_snapshots (
    id TEXT NOT NULL PRIMARY KEY,
    session_id TEXT NOT NULL,
    principal_hash TEXT NOT NULL DEFAULT '',
    memory_epoch TEXT NOT NULL DEFAULT '',
    delete_epoch TEXT NOT NULL DEFAULT '',
    seal_id TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    body_json TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_memory_snapshots_session_seal UNIQUE (session_id, seal_id)
)
"""

_SNAPSHOT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memory_snapshots_session_created "
    "ON memory_snapshots(session_id, created_at DESC)"
)

_ENTRY_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memory_entries_tier_status "
    "ON memory_entries(tier, status)",
    "CREATE INDEX IF NOT EXISTS idx_memory_entries_project_status "
    "ON memory_entries(project_id, status)",
)


def apply_memory_v2_schema(conn) -> None:
    """Upgrade a connection in place; safe to call on every startup."""
    _create_snapshot_storage(conn)
    if not _table_exists(conn, "memory_entries"):
        return
    _add_missing_columns(conn, "memory_entries", _ENTRY_COLUMNS)
    _migrate_legacy_entries(conn)
    _migrate_legacy_long_term_memory(conn)
    for statement in _ENTRY_INDEX_SQL:
        conn.execute(text(statement))


def _create_snapshot_storage(conn) -> None:
    conn.execute(text(_SNAPSHOT_TABLE_SQL))
    conn.execute(text(_SNAPSHOT_INDEX_SQL))


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": table_name},
    ).first()
    return row is not None


def _column_names(conn, table_name: str) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})")}


def _add_missing_columns(conn, table_name: str, columns) -> None:
    existing = _column_names(conn, table_name)
    for name, definition in columns:
        if name in existing:
            continue
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"))


def _migrate_legacy_entries(conn) -> None:
    rows = conn.execute(
        text(
            "SELECT id, scope, scope_key FROM memory_entries "
            "WHERE schema_version IS NULL OR schema_version < :version"
        ),
        {"version": SCHEMA_VERSION},
    ).all()
    if not rows:
        return
    project_ids: dict[str, str] = {}
    now = _utcnow_naive()
    for entry_id, scope, scope_key in rows:
        tier = SCOPE_TO_TIER.get(scope, "project")
        project_id = ""
        if tier == "project" and isinstance(scope_key, str) and scope_key.strip():
            candidate = scope_key.strip()
            if is_path_like(candidate):
                if candidate not in project_ids:
                    project_ids[candidate] = current_project_id(candidate)
                project_id = project_ids[candidate]
        conn.execute(
            text(
                "UPDATE memory_entries SET "
                "tier = :tier, "
                "status = CASE WHEN status IS NULL OR status = '' THEN 'active' ELSE status END, "
                "project_id = CASE WHEN project_id IS NULL OR project_id = '' "
                "THEN :project_id ELSE project_id END, "
                "principal_id = CASE WHEN principal_id IS NULL OR principal_id = '' "
                "THEN 'local' ELSE principal_id END, "
                "evidence_json = CASE WHEN evidence_json IS NULL OR evidence_json = '' "
                "THEN '{}' ELSE evidence_json END, "
                "schema_version = :version, "
                "updated_at = COALESCE(updated_at, :now) "
                "WHERE id = :id"
            ),
            {
                "tier": tier,
                "project_id": project_id,
                "version": SCHEMA_VERSION,
                "now": now,
                "id": entry_id,
            },
        )
    logger.info("Memory v2 migrated %d legacy memory entries", len(rows))


def _migrate_legacy_long_term_memory(conn) -> None:
    if not _table_exists(conn, "settings"):
        return
    row = conn.execute(
        text("SELECT value FROM settings WHERE key = 'longTermMemory' LIMIT 1")
    ).first()
    if row is None:
        return
    value = row[0]
    if not isinstance(value, str) or not value.strip():
        return
    if contains_secret(value):
        logger.warning("legacy longTermMemory was not imported: secret pattern detected")
        return
    if len(value) > MAX_CONTENT_CHARS:
        logger.warning(
            "legacy longTermMemory was not imported: exceeds %d characters",
            MAX_CONTENT_CHARS,
        )
        return
    if _legacy_import_exists(conn):
        return
    now = _utcnow_naive()
    evidence_json = json.dumps(
        {"migrated_from": "longTermMemory", "setting": "longTermMemory"},
        sort_keys=True,
        separators=(",", ":"),
    )
    conn.execute(
        text(
            "INSERT INTO memory_entries ("
            "id, scope, scope_key, kind, content, structured_json, source, confidence, "
            "created_at, updated_at, last_used_at, expires_at, deleted_at, supersedes_id, "
            "tier, status, project_id, principal_id, evidence_json, schema_version"
            ") VALUES ("
            ":id, 'global', '', 'preference', :content, '{}', 'import', :confidence, "
            ":now, :now, NULL, NULL, NULL, NULL, "
            "'user', 'proposed', '', 'local', :evidence_json, :version"
            ")"
        ),
        {
            "id": uuid.uuid4().hex,
            "content": value,
            "confidence": 0.7,
            "now": now,
            "evidence_json": evidence_json,
            "version": SCHEMA_VERSION,
        },
    )
    logger.info("Memory v2 imported legacy longTermMemory as a proposed preference")


def _legacy_import_exists(conn) -> bool:
    rows = conn.execute(
        text(
            "SELECT evidence_json FROM memory_entries "
            "WHERE source = 'import' AND kind = 'preference' "
            "AND evidence_json LIKE '%longTermMemory%' LIMIT 5"
        )
    ).all()
    for (raw,) in rows:
        try:
            marker = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(marker, dict) and marker.get("migrated_from") == "longTermMemory":
            return True
    return False


def _utcnow_naive() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


__all__ = ["SCHEMA_VERSION", "apply_memory_v2_schema"]
