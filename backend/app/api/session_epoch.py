"""Private deletion generations shared by session CRUD and paused requests."""

import hashlib
import logging
import re
import uuid

from sqlalchemy.dialects.sqlite import insert

from app.api.common import get_db_ctx
from app.db.models import Settings


logger = logging.getLogger(__name__)
SESSION_DELETE_EPOCH_PREFIX = "_sessionDeleteEpoch:"


def read_session_delete_epoch(session_id: str | None) -> str | None:
    """Missing legacy generation is valid; unreadable state fails closed."""
    try:
        key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256((session_id or "default").encode("utf-8")).hexdigest()
        with get_db_ctx() as db:
            row = db.query(Settings).filter(Settings.key == key).first()
            if row is None:
                return ""
            value = row.value
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value):
            return value
    except Exception:
        logger.warning("session deletion generation is unavailable")
    return None


def stage_session_delete_epoch(db, session_id: str, now) -> None:
    """Stage invalidation with deletion, without committing or storing content."""
    key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    value = uuid.uuid4().hex
    statement = insert(Settings).values(key=key, value=value, updated_at=now)
    db.execute(statement.on_conflict_do_update(
        index_elements=[Settings.key], set_={"value": value, "updated_at": now},
    ))
