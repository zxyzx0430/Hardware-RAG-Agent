"""Private, persistent deletion generations for session-scoped requests."""

import hashlib
import logging
import re
import uuid

from sqlalchemy.dialects.sqlite import insert

from app.api.common import get_db_ctx
from app.db.models import Settings


logger = logging.getLogger(__name__)
SESSION_DELETE_EPOCH_PREFIX = "_sessionDeleteEpoch:"
CONTEXT_EVENT_PREFIX = "_contextEvent:"


def _session_epoch_key(session_id: str | None) -> str:
    value = session_id or "default"
    return SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _session_prefix(session_id: str) -> str:
    """Return the context-event Settings prefix without storing the session ID."""
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return CONTEXT_EVENT_PREFIX + digest + ":"


def read_session_delete_epoch(session_id: str | None) -> str | None:
    """Return the opaque generation, empty for legacy state, or None if unavailable."""
    try:
        with get_db_ctx() as db:
            row = db.query(Settings).filter(Settings.key == _session_epoch_key(session_id)).first()
            if row is None:
                return ""
            value = row.value
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value):
            return value
    except Exception:
        logger.warning("session deletion generation is unavailable")
    return None


def is_session_tombstoned(session_id: str | None) -> bool:
    """True only when the persisted tombstone proves the session was deleted."""
    try:
        with get_db_ctx() as db:
            from app.db.models import SessionTombstone

            return db.query(SessionTombstone.session_id).filter(
                SessionTombstone.session_id == session_id,
            ).first() is not None
    except Exception:
        return True


def stage_session_delete_epoch(db, session_id: str, now) -> str:
    """Stage invalidation with deletion; the caller owns the transaction."""
    key = _session_epoch_key(session_id)
    value = uuid.uuid4().hex
    statement = insert(Settings).values(key=key, value=value, updated_at=now)
    db.execute(statement.on_conflict_do_update(
        index_elements=[Settings.key], set_={"value": value, "updated_at": now},
    ))
    return value
