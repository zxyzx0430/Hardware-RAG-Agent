"""Offline data-integrity reconciliation for message and checkpoint stores.

The CLI wrapper is ``backend/scripts/gc_data_integrity.py``. Dry-run is the
default; ``--apply`` performs the deletions listed in the report. SQLite has
no per-thread timestamp in the LangGraph checkpoint tables, so orphan threads
are removed without an age filter; run the apply mode with the backend
stopped.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from src.config.settings import settings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MAIN_DB = Path(settings.sqlite_db_path)
DEFAULT_CHECKPOINT_DB = Path(settings.agent_checkpoint_path)
DEFAULT_GRACE_HOURS = 24
SAMPLE_LIMIT = 20
_OBS_META_PREFIX = "_toolObservation:v1:meta:"
_OBS_BODY_PREFIX = "_toolObservation:v1:body:"


def build_gc_report(
    main_db: str | Path = DEFAULT_MAIN_DB,
    checkpoint_db: str | Path = DEFAULT_CHECKPOINT_DB,
    grace_hours: int = DEFAULT_GRACE_HOURS,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Collect orphan counts without deleting anything."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = _cutoff(now, grace_hours)
    report = {
        "generated_at": _iso(now),
        "mode": "dry-run",
        "main_db": str(main_db),
        "checkpoint_db": str(checkpoint_db),
        "grace_hours": grace_hours,
        "cutoff": cutoff,
        "main": {},
        "checkpoints": {},
    }
    with _connect(main_db, readonly=True) as conn:
        report["main"] = _collect_main(conn, cutoff)
        session_ids = _session_ids(conn)
    with _connect(checkpoint_db, readonly=True) as conn:
        report["checkpoints"] = _collect_checkpoints(conn, session_ids)
    return report


def apply_gc_report(report: dict[str, Any], vacuum: bool = False) -> dict[str, Any]:
    """Apply all deletions matching the report's cutoff."""
    applied: dict[str, int] = {}
    with _connect(report["main_db"], readonly=False) as conn:
        session_ids = _session_ids(conn)
        applied.update(_apply_main(conn, report["cutoff"]))
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        applied["fts_rebuilt"] = 1
        if vacuum:
            _vacuum(conn)
    with _connect(report["checkpoint_db"], readonly=False) as conn:
        applied.update(_apply_checkpoints(conn, session_ids))
        if vacuum:
            _vacuum(conn)
    report["mode"] = "apply"
    report["applied"] = applied
    return report


def write_report(report: dict[str, Any], path: str | Path | None = None) -> Path:
    target = Path(path) if path else DEFAULT_MAIN_DB.parent / f"gc-report-{_stamp()}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def log_startup_reconciliation() -> None:
    """Log orphan counts at startup; apply only when explicitly enabled."""
    if not DEFAULT_MAIN_DB.exists() or not DEFAULT_CHECKPOINT_DB.exists():
        return
    try:
        report = build_gc_report()
    except Exception as exc:
        logger.warning("data integrity reconciliation unavailable: %s", exc)
        return
    main = report["main"]
    orphan_threads = report["checkpoints"]["orphan_threads"]["count"]
    orphan_chunks = main["orphan_big_chunks"]["count"]
    orphan_messages = main["orphan_messages"]["count"]
    if orphan_threads or orphan_chunks or orphan_messages:
        logger.warning(
            "data integrity: orphan_threads=%d orphan_big_chunks=%d orphan_messages=%d",
            orphan_threads, orphan_chunks, orphan_messages,
        )
    if os.getenv("HW_RAG_GC_ON_START") == "1":
        applied = apply_gc_report(report)
        logger.warning("data integrity startup GC applied: %s", applied["applied"])


def _collect_main(conn: sqlite3.Connection, cutoff: str) -> dict[str, Any]:
    return {
        "orphan_messages": _sample_orphans(conn, "messages", "created_at", cutoff),
        "orphan_feedback": _sample_orphans(conn, "feedback", "created_at", cutoff),
        "orphan_token_usage": _sample_orphans(conn, "token_usage", "created_at", cutoff),
        "orphan_tool_audit": _sample_orphans(conn, "tool_audit", "timestamp", cutoff),
        "orphan_big_chunks": _sample_chunks(conn, cutoff),
        "orphan_observation_bodies": _sample_observations(conn),
        "fts": {"messages": conn.execute("SELECT count(*) FROM messages").fetchone()[0]},
    }


def _orphan_where(table: str, ts_column: str) -> str:
    return (
        f"{table}.session_id IS NOT NULL "
        f"AND {table}.session_id NOT IN (SELECT id FROM sessions) "
        f"AND ({table}.{ts_column} IS NULL OR {table}.{ts_column} < ?)"
    )


def _sample_orphans(conn, table: str, ts_column: str, cutoff: str) -> dict[str, Any]:
    where = _orphan_where(table, ts_column)
    count = conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", (cutoff,)).fetchone()[0]
    rows = conn.execute(
        f"SELECT rowid FROM {table} WHERE {where} LIMIT ?", (cutoff, SAMPLE_LIMIT),
    ).fetchall()
    return {"count": count, "rowids": [row[0] for row in rows]}


def _chunk_where() -> str:
    return (
        "(big_chunks.created_at IS NULL OR big_chunks.created_at < ?) "
        "AND (big_chunks.doc_id NOT IN (SELECT doc_id FROM knowledge_docs) "
        "OR big_chunks.kb_id NOT IN (SELECT id FROM knowledge_bases))"
    )


def _sample_chunks(conn, cutoff: str) -> dict[str, Any]:
    where = _chunk_where()
    count = conn.execute(f"SELECT count(*) FROM big_chunks WHERE {where}", (cutoff,)).fetchone()[0]
    rows = conn.execute(
        f"SELECT rowid FROM big_chunks WHERE {where} LIMIT ?", (cutoff, SAMPLE_LIMIT),
    ).fetchall()
    return {"count": count, "rowids": [row[0] for row in rows]}


def _all_orphan_observations(conn) -> list[str]:
    body_keys = [
        row[0] for row in conn.execute(
            "SELECT key FROM settings WHERE key LIKE ?", (f"{_OBS_BODY_PREFIX}%",),
        )
    ]
    meta_keys = {
        row[0] for row in conn.execute(
            "SELECT key FROM settings WHERE key LIKE ?", (f"{_OBS_META_PREFIX}%",),
        )
    }
    return [key for key in body_keys if not _has_meta(meta_keys, key)]


def _has_meta(meta_keys: set[str], body_key: str) -> bool:
    digest = body_key[len(_OBS_BODY_PREFIX):]
    suffix = f":{digest}"
    return any(key.endswith(suffix) for key in meta_keys)


def _sample_observations(conn) -> dict[str, Any]:
    orphan = _all_orphan_observations(conn)
    return {"count": len(orphan), "keys": orphan[:SAMPLE_LIMIT]}


def _session_ids(conn) -> list[str]:
    return [row[0] for row in conn.execute("SELECT id FROM sessions")]


def _collect_checkpoints(conn, session_ids: list[str]) -> dict[str, Any]:
    threads = _all_orphan_threads(conn, session_ids)
    return {"orphan_threads": {"count": len(threads), "thread_ids": threads[:SAMPLE_LIMIT]}}


def _all_orphan_threads(conn, session_ids: list[str]) -> list[str]:
    if not session_ids:
        return [row[0] for row in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")]
    marks = ",".join("?" for _ in session_ids)
    return [
        row[0] for row in conn.execute(
            f"SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id NOT IN ({marks})",
            session_ids,
        )
    ]


def _apply_main(conn, cutoff: str) -> dict[str, int]:
    applied = {
        "messages": _delete_orphans(conn, "messages", "created_at", cutoff),
        "feedback": _delete_orphans(conn, "feedback", "created_at", cutoff),
        "token_usage": _delete_orphans(conn, "token_usage", "created_at", cutoff),
        "tool_audit": _delete_orphans(conn, "tool_audit", "timestamp", cutoff),
        "big_chunks": conn.execute(
            f"DELETE FROM big_chunks WHERE {_chunk_where()}", (cutoff,),
        ).rowcount,
        "observation_bodies": _delete_settings(conn, _all_orphan_observations(conn)),
    }
    return applied


def _delete_orphans(conn, table: str, ts_column: str, cutoff: str) -> int:
    where = _orphan_where(table, ts_column)
    return conn.execute(f"DELETE FROM {table} WHERE {where}", (cutoff,)).rowcount


def _delete_settings(conn, keys: list[str]) -> int:
    if not keys:
        return 0
    marks = ",".join("?" for _ in keys)
    return conn.execute(f"DELETE FROM settings WHERE key IN ({marks})", keys).rowcount


def _apply_checkpoints(conn, session_ids: list[str]) -> dict[str, int]:
    deleted = 0
    for thread_id in _all_orphan_threads(conn, session_ids):
        deleted += conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,)).rowcount
        deleted += conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,)).rowcount
    return {"checkpoint_rows": deleted}


def _vacuum(conn) -> None:
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM")


def _connect(path: str | Path, readonly: bool) -> sqlite3.Connection:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(f"file:{target.as_posix()}?mode={mode}", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.isolation_level = None
        conn.execute("BEGIN")
    return conn


def _cutoff(now: datetime.datetime, grace_hours: int) -> str:
    delta = datetime.timedelta(hours=max(0, grace_hours))
    return (now - delta).strftime("%Y-%m-%d %H:%M:%S")


def _iso(value: datetime.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
