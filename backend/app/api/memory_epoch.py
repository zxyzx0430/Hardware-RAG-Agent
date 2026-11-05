"""Private, persistent deletion epochs for request-scoped manual memory."""

import logging
import re
import uuid

from sqlalchemy.dialects.sqlite import insert

from app.api.common import get_db_ctx
from app.db.models import Settings


logger = logging.getLogger(__name__)
MEMORY_CLEAR_EPOCH_KEY = "_longTermMemoryClearEpoch"


def read_memory_clear_epoch() -> str | None:
    """Missing legacy marker is valid; unreadable or corrupt state is not."""
    try:
        with get_db_ctx() as db:
            row = db.query(Settings).filter(Settings.key == MEMORY_CLEAR_EPOCH_KEY).first()
            value = row.value if row is not None else ""
        if value == "" or (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)):
            return value
    except Exception:
        logger.warning("manual memory deletion epoch is unavailable")
    return None


def stage_memory_clear_epoch(db, now) -> None:
    """Stage marker atomically with the caller's clear; never commit here."""
    value = uuid.uuid4().hex
    statement = insert(Settings).values(key=MEMORY_CLEAR_EPOCH_KEY, value=value, updated_at=now)
    db.execute(statement.on_conflict_do_update(
        index_elements=[Settings.key], set_={"value": value, "updated_at": now},
    ))
