"""Agent tool-call audit logger — SQLite persistence (v3-T1).

Records every permission decision and tool execution outcome to the tool_audit
table. Logs are auto-cleaned after RETENTION_DAYS.
"""

import datetime
import json
import logging
from typing import Any

from app.db.database import SessionLocal
from app.db.models import ToolAudit

logger = logging.getLogger(__name__)

RETENTION_DAYS: int = 30
ARGS_SUMMARY_MAX: int = 500
ERROR_MAX: int = 1000
DEFAULT_LIMIT: int = 100


def _truncate(text: Any, limit: int) -> str:
    raw = str(text) if text is not None else ""
    return raw if len(raw) <= limit else raw[:limit] + "…"


def _persist_audit_record(record: ToolAudit) -> None:
    """Add and commit a ToolAudit record; always close the session."""
    db = SessionLocal()
    try:
        db.add(record)
        db.commit()
    finally:
        db.close()


def log_tool_call(
    session_id: str | None,
    tool_name: str,
    args: dict | None,
    decision: str,
    decision_source: str = "",
    risk_level: str = "",
    exit_code: int | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Persist a single tool-call audit record. Silently fails on DB errors."""
    try:
        args_summary = _truncate(json.dumps(args or {}, ensure_ascii=False), ARGS_SUMMARY_MAX)
        record = ToolAudit(
            session_id=session_id,
            tool_name=tool_name,
            args_summary=args_summary,
            decision=decision,
            decision_source=decision_source,
            risk_level=risk_level,
            exit_code=exit_code,
            duration_ms=duration_ms,
            error=_truncate(error, ERROR_MAX),
        )
        _persist_audit_record(record)
    except Exception as exc:
        logger.warning("audit_log_write_failed tool=%s error=%s", tool_name, exc)


def cleanup_old_logs(days: int = RETENTION_DAYS) -> int:
    """Delete audit logs older than `days`. Returns deleted count."""
    logger.info("audit_log_cleanup start")
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        db = SessionLocal()
        try:
            deleted = db.query(ToolAudit).filter(ToolAudit.timestamp < cutoff).delete(
                synchronize_session=False
            )
            db.commit()
            logger.info("audit_log_cleanup done deleted=%s", deleted)
            return deleted
        finally:
            db.close()
    except Exception as exc:
        logger.warning("audit cleanup failed: %s", exc)
        return 0


def query_logs(
    session_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
    tool_name: str | None = None,
    decision: str | None = None,
    risk_level: str | None = None,
) -> list[dict]:
    """Query audit logs with optional filters. Returns list of dicts."""
    logger.info("audit_log_query session=%s tool=%s limit=%s", session_id, tool_name, limit)
    try:
        db = SessionLocal()
        try:
            q = db.query(ToolAudit)
            if session_id:
                q = q.filter(ToolAudit.session_id == session_id)
            if tool_name:
                q = q.filter(ToolAudit.tool_name == tool_name)
            if decision:
                q = q.filter(ToolAudit.decision == decision)
            if risk_level:
                q = q.filter(ToolAudit.risk_level == risk_level)
            rows = q.order_by(ToolAudit.timestamp.desc()).limit(limit).all()
            return [_row_to_dict(r) for r in rows]
        finally:
            db.close()
    except Exception as exc:
        logger.warning("audit query failed: %s", exc)
        return []


def _row_to_dict(row: ToolAudit) -> dict:
    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "session_id": row.session_id,
        "tool_name": row.tool_name,
        "args_summary": row.args_summary,
        "decision": row.decision,
        "decision_source": row.decision_source,
        "risk_level": row.risk_level,
        "exit_code": row.exit_code,
        "duration_ms": row.duration_ms,
        "error": row.error,
    }
