"""P3: offline GC reconciles orphans and rebuilds messages_fts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db.gc import apply_gc_report, build_gc_report
from scripts.gc_data_integrity import main as gc_main

_MAIN_SCHEMA = (
    "CREATE TABLE sessions (id TEXT PRIMARY KEY)",
    "CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, created_at DATETIME)",
    "CREATE TABLE feedback (id INTEGER PRIMARY KEY, session_id TEXT, created_at DATETIME)",
    "CREATE TABLE token_usage (id INTEGER PRIMARY KEY, session_id TEXT, created_at DATETIME)",
    "CREATE TABLE tool_audit (id INTEGER PRIMARY KEY, session_id TEXT, timestamp DATETIME)",
    "CREATE TABLE big_chunks (id INTEGER PRIMARY KEY, doc_id TEXT, kb_id TEXT, created_at DATETIME)",
    "CREATE TABLE knowledge_docs (doc_id TEXT)",
    "CREATE TABLE knowledge_bases (id TEXT)",
    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages', content_rowid='rowid')",
)

_CHECKPOINT_SCHEMA = (
    "CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', "
    "checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, "
    "metadata BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))",
    "CREATE TABLE writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', "
    "task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT, value BLOB, "
    "PRIMARY KEY (thread_id, checkpoint_ns, task_id, idx))",
)


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    main_db = tmp_path / "main.db"
    checkpoint_db = tmp_path / "checkpoints.db"
    with sqlite3.connect(main_db) as conn:
        for statement in _MAIN_SCHEMA:
            conn.execute(statement)
        conn.execute("INSERT INTO sessions (id) VALUES ('s1')")
        conn.execute("INSERT INTO messages (id, session_id, role, content, created_at) VALUES "
                     "('m1','s1','user','keep','2000-01-01 00:00:00')")
        conn.execute("INSERT INTO messages (id, session_id, role, content, created_at) VALUES "
                     "('m2','s9','user','orphan','2000-01-01 00:00:00')")
        conn.execute("INSERT INTO feedback (session_id, created_at) VALUES ('s9','2000-01-01 00:00:00')")
        conn.execute("INSERT INTO token_usage (session_id, created_at) VALUES ('s9','2000-01-01 00:00:00')")
        conn.execute("INSERT INTO tool_audit (session_id, timestamp) VALUES ('s9','2000-01-01 00:00:00')")
        conn.execute("INSERT INTO knowledge_docs (doc_id) VALUES ('doc1')")
        conn.execute("INSERT INTO knowledge_bases (id) VALUES ('kb1')")
        conn.execute("INSERT INTO big_chunks (doc_id, kb_id, created_at) VALUES "
                     "('doc1','kb1','2000-01-01 00:00:00')")
        conn.execute("INSERT INTO big_chunks (doc_id, kb_id, created_at) VALUES "
                     "('doc9','kb1','2000-01-01 00:00:00')")
        conn.execute("INSERT INTO settings (key, value) VALUES ('_toolObservation:v1:body:' || ?, 'x')", ("a" * 64,))
        conn.execute("INSERT INTO settings (key, value) VALUES ('_toolObservation:v1:body:' || ?, 'x')", ("b" * 64,))
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('_toolObservation:v1:meta:p' || ? || ?, '{}')",
            (":" + "b" * 64, ":" + "b" * 64),
        )
    with sqlite3.connect(checkpoint_db) as conn:
        for statement in _CHECKPOINT_SCHEMA:
            conn.execute(statement)
        conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id) VALUES ('s1','c1')")
        conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_id) VALUES ('s9','c9')")
        conn.execute("INSERT INTO writes (thread_id, task_id, idx, channel) VALUES ('s9','t',0,'x')")
    return main_db, checkpoint_db


def test_dry_run_reports_without_deleting(tmp_path) -> None:
    main_db, checkpoint_db = _seed(tmp_path)

    report = build_gc_report(main_db, checkpoint_db, grace_hours=0)

    assert report["main"]["orphan_messages"]["count"] == 1
    assert report["main"]["orphan_feedback"]["count"] == 1
    assert report["main"]["orphan_big_chunks"]["count"] == 1
    assert report["main"]["orphan_observation_bodies"]["count"] == 1
    assert report["checkpoints"]["orphan_threads"]["count"] == 1
    with sqlite3.connect(main_db) as conn:
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    with sqlite3.connect(checkpoint_db) as conn:
        assert conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 2


def test_apply_deletes_only_orphans_and_rebuilds_fts(tmp_path) -> None:
    main_db, checkpoint_db = _seed(tmp_path)
    report = build_gc_report(main_db, checkpoint_db, grace_hours=0)

    applied = apply_gc_report(report, vacuum=True)

    assert applied["mode"] == "apply"
    assert applied["applied"]["messages"] == 1
    assert applied["applied"]["big_chunks"] == 1
    assert applied["applied"]["checkpoint_rows"] == 2
    with sqlite3.connect(main_db) as conn:
        assert conn.execute("SELECT id FROM messages").fetchall() == [("m1",)]
        assert conn.execute("SELECT count(*) FROM big_chunks").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0] == 1
    with sqlite3.connect(checkpoint_db) as conn:
        assert conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall() == [("s1",)]
        assert conn.execute("SELECT count(*) FROM writes").fetchone()[0] == 0


def test_cli_dry_run_writes_report(tmp_path) -> None:
    main_db, checkpoint_db = _seed(tmp_path)
    report_path = tmp_path / "report.json"

    code = gc_main([
        "--main-db", str(main_db),
        "--checkpoint-db", str(checkpoint_db),
        "--grace-hours", "0",
        "--report", str(report_path),
    ])

    assert code == 0
    assert '"mode": "dry-run"' in report_path.read_text(encoding="utf-8")
