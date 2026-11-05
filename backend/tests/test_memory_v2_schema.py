"""Memory v2 schema migration tests (legacy SQLite databases)."""

import json

import pytest
from sqlalchemy import create_engine, text

from app.db.memory_v2_schema import apply_memory_v2_schema
from app.db.memory_models import MEMORY_SCHEMA_VERSION
from app.db.project_identity import current_project_id
from sqlalchemy.pool import StaticPool


V1_ENTRY_COLUMNS = """
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    scope_key TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    structured_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'agent',
    confidence FLOAT NOT NULL DEFAULT 0.7,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    last_used_at DATETIME,
    expires_at DATETIME,
    deleted_at DATETIME,
    supersedes_id TEXT
"""


@pytest.fixture()
def legacy_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _create_v1_schema(conn):
    conn.execute(text(f"CREATE TABLE memory_entries ({V1_ENTRY_COLUMNS})"))
    conn.execute(text("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at DATETIME)"))


def _insert_entry(conn, entry_id, scope, scope_key="", content=None):
    conn.execute(
        text(
            "INSERT INTO memory_entries "
            "(id, scope, scope_key, kind, content, created_at, updated_at) "
            "VALUES (:id, :scope, :scope_key, 'fact', :content, :now, :now)"
        ),
        {
            "id": entry_id,
            "scope": scope,
            "scope_key": scope_key,
            "content": content or f"{entry_id} note",
            "now": "2026-01-01 00:00:00",
        },
    )


def _entry_row(conn, entry_id):
    return conn.execute(
        text(
            "SELECT tier, status, project_id, principal_id, evidence_json, schema_version "
            "FROM memory_entries WHERE id = :id"
        ),
        {"id": entry_id},
    ).one()


def test_migration_is_idempotent_on_empty_legacy_table(legacy_engine):
    with legacy_engine.begin() as conn:
        _create_v1_schema(conn)
        apply_memory_v2_schema(conn)
        apply_memory_v2_schema(conn)
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(memory_entries)")}
        assert {
            "tier",
            "status",
            "project_id",
            "principal_id",
            "evidence_json",
            "schema_version",
        } <= columns
        assert (
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_snapshots'")
            ).scalar()
            == "memory_snapshots"
        )
        indexes = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index'")
            ).all()
        }
        assert "idx_memory_entries_tier_status" in indexes
        assert "idx_memory_entries_project_status" in indexes
        assert "idx_memory_snapshots_session_created" in indexes


def test_migration_maps_scopes_and_backfills_once(legacy_engine, tmp_path):
    project_root = tmp_path / "legacy-project"
    project_root.mkdir()
    with legacy_engine.begin() as conn:
        _create_v1_schema(conn)
        _insert_entry(conn, "g1", "global")
        _insert_entry(conn, "p1", "project", str(project_root))
        _insert_entry(conn, "p2", "project", "named-project-key")
        _insert_entry(conn, "s1", "session", "sess-1")

        apply_memory_v2_schema(conn)
        apply_memory_v2_schema(conn)

        assert _entry_row(conn, "g1")[:4] == ("user", "active", "", "local")
        p1 = _entry_row(conn, "p1")
        assert p1[:2] == ("project", "active")
        assert p1[2] == current_project_id(str(project_root))
        assert p1[5] == MEMORY_SCHEMA_VERSION
        # A non-path scope_key is not guessed into a project id.
        assert _entry_row(conn, "p2")[2] == ""
        assert _entry_row(conn, "s1")[:2] == ("working", "active")
        # A second run must not rewrite tier/status/project_id.
        assert conn.execute(text("SELECT COUNT(*) FROM memory_entries")).scalar() == 4


def test_migration_ignores_v2_rows(legacy_engine):
    with legacy_engine.begin() as conn:
        _create_v1_schema(conn)
        apply_memory_v2_schema(conn)
        conn.execute(
            text(
                "INSERT INTO memory_entries "
                "(id, scope, scope_key, kind, content, created_at, updated_at, "
                "tier, status, project_id, principal_id, evidence_json, schema_version) "
                "VALUES ('e1', 'global', '', 'fact', 'episodic note', :now, :now, "
                "'episodic', 'active', 'proj', 'local', '{}', :version)"
            ),
            {"now": "2026-01-01", "version": MEMORY_SCHEMA_VERSION},
        )
        apply_memory_v2_schema(conn)
        assert _entry_row(conn, "e1")[:3] == ("episodic", "active", "proj")


def test_migration_imports_long_term_memory_once(legacy_engine):
    with legacy_engine.begin() as conn:
        _create_v1_schema(conn)
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('longTermMemory', :value)"),
            {"value": "Prefer 3.3V logic levels."},
        )
        apply_memory_v2_schema(conn)
        apply_memory_v2_schema(conn)

        rows = conn.execute(
            text(
                "SELECT tier, status, kind, source, content, evidence_json "
                "FROM memory_entries WHERE source = 'import'"
            )
        ).all()
        assert len(rows) == 1
        tier, status, kind, source, content, evidence_json = rows[0]
        assert (tier, status, kind, source) == ("user", "proposed", "preference", "import")
        assert content == "Prefer 3.3V logic levels."
        assert json.loads(evidence_json)["migrated_from"] == "longTermMemory"
        # Compatibility: the legacy setting itself is not deleted.
        assert (
            conn.execute(
                text("SELECT value FROM settings WHERE key='longTermMemory'")
            ).scalar()
            == "Prefer 3.3V logic levels."
        )


def test_migration_skips_secret_long_term_memory(legacy_engine):
    with legacy_engine.begin() as conn:
        _create_v1_schema(conn)
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('longTermMemory', :value)"),
            {"value": "api_key = abc12345678"},
        )
        apply_memory_v2_schema(conn)
        assert (
            conn.execute(text("SELECT COUNT(*) FROM memory_entries")).scalar() == 0
        )


def test_legacy_import_is_not_repeated_after_review(legacy_engine):
    with legacy_engine.begin() as conn:
        _create_v1_schema(conn)
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('longTermMemory', :value)"),
            {"value": "Prefer 3.3V logic levels."},
        )
        apply_memory_v2_schema(conn)
        conn.execute(text("UPDATE memory_entries SET status = 'active'"))
        apply_memory_v2_schema(conn)
        assert conn.execute(text("SELECT COUNT(*) FROM memory_entries")).scalar() == 1


def test_migration_creates_snapshot_table_without_entries_table(legacy_engine):
    with legacy_engine.begin() as conn:
        apply_memory_v2_schema(conn)
        apply_memory_v2_schema(conn)
        assert (
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_snapshots'")
            ).scalar()
            == "memory_snapshots"
        )


def test_init_db_installs_v2_columns():
    from app.db.database import engine

    with engine.connect() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(memory_entries)")}
        assert {"tier", "status", "project_id", "principal_id", "evidence_json"} <= columns
        assert (
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_snapshots'")
            ).scalar()
            == "memory_snapshots"
        )
