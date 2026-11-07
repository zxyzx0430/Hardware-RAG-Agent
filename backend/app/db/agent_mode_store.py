"""Session-scoped marker for an Agent-initiated downgrade to plan mode.

The marker is authoritative across requests: while it exists, /api/chat and
HITL resumes clamp the effective permission mode to ``plan``. Only the user can
clear it (the explicit clear endpoint or approving a submitted plan).
"""

from __future__ import annotations

import datetime
import logging

from app.db import database
from app.db.models import Session as SessionModel
from app.db.models import Settings as SettingsModel

logger = logging.getLogger(__name__)

PLAN_REQUEST_PREFIX = "agent_plan_requested:"
PLAN_REQUESTED_VALUE = "1"
MAX_SESSION_ID_CHARS = 128


def _require_session_id(session_id: object) -> str:
    if (
        type(session_id) is not str
        or not session_id
        or len(session_id) > MAX_SESSION_ID_CHARS
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
    ):
        raise ValueError("a real session id is required")
    return session_id


def _key(session_id: str) -> str:
    return PLAN_REQUEST_PREFIX + session_id


def session_exists(session_id: str) -> bool:
    """Return True only when the session row exists."""
    session_id = _require_session_id(session_id)
    with database.SessionLocal() as db:
        row = db.query(SessionModel.id).filter(SessionModel.id == session_id).first()
    return row is not None


def request_plan_mode(session_id: str) -> None:
    """Persist the marker; the caller owns any earlier permission decision."""
    session_id = _require_session_id(session_id)
    with database.SessionLocal() as db:
        request_plan_mode_in_db(db, session_id)
        db.commit()
    logger.info("agent_plan_mode_requested session=%s", session_id)


def request_plan_mode_in_db(db, session_id: str) -> None:
    """Upsert the marker inside a caller-owned transaction."""
    session_id = _require_session_id(session_id)
    now = datetime.datetime.now(datetime.timezone.utc)
    row = db.get(SettingsModel, _key(session_id))
    if row is None:
        db.add(SettingsModel(
            key=_key(session_id), value=PLAN_REQUESTED_VALUE, updated_at=now,
        ))
    else:
        row.value = PLAN_REQUESTED_VALUE
        row.updated_at = now


def plan_mode_requested(session_id: str | None) -> bool:
    """Return whether the Agent asked for plan mode; DB errors propagate."""
    if type(session_id) is not str or not session_id:
        return False
    with database.SessionLocal() as db:
        return plan_mode_requested_in_db(db, session_id)


def plan_mode_requested_in_db(db, session_id: str | None) -> bool:
    """Read the marker through a caller-owned session (same transaction)."""
    if type(session_id) is not str or not session_id:
        return False
    row = db.query(SettingsModel.value).filter(
        SettingsModel.key == _key(session_id),
    ).first()
    return row is not None and row[0] == PLAN_REQUESTED_VALUE


def plan_mode_requested_flags(
    session_ids: list[str], db=None,
) -> dict[str, bool]:
    """Read flags for a session listing in one query; errors propagate."""
    keys = {
        _key(session_id): session_id
        for session_id in session_ids
        if type(session_id) is str and session_id
    }
    if not keys:
        return {}
    if db is None:
        with database.SessionLocal() as owned:
            return _read_flags(owned, keys)
    return _read_flags(db, keys)


def _read_flags(db, keys: dict[str, str]) -> dict[str, bool]:
    rows = db.query(SettingsModel.key).filter(
        SettingsModel.key.in_(list(keys)),
    ).all()
    return {keys[row[0]]: True for row in rows if row[0] in keys}


def clear_plan_mode_requested(session_id: str) -> bool:
    """Delete the marker. Returns True when a row was removed."""
    session_id = _require_session_id(session_id)
    with database.SessionLocal() as db:
        removed = clear_plan_mode_requested_in_db(db, session_id)
        db.commit()
    if removed:
        logger.info("agent_plan_mode_cleared session=%s", session_id)
    return removed


def clear_plan_mode_requested_in_db(db, session_id: str) -> bool:
    """Delete the marker inside a caller-owned transaction."""
    if type(session_id) is not str or not session_id:
        return False
    removed = db.query(SettingsModel).filter(
        SettingsModel.key == _key(session_id),
    ).delete(synchronize_session=False)
    return bool(removed)
