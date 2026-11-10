"""Persisted ChangeSet facts for P2 observation capture.

A ChangeSet belongs to one Run and Session. ChangeItems are the file-level
changes observed around mutating tool calls. They are deliberately separate
from RunEvents: RunEvents are the replayable stream, these rows are the
queryable review surface.
"""
from __future__ import annotations

import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.db.database import Base


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class ChangeSet(Base):
    __tablename__ = "change_sets"
    __table_args__ = (
        Index("idx_change_sets_session", "session_id"),
        Index("uq_change_sets_run", "run_id", unique=True),
        Index("idx_change_sets_created", "created_at"),
    )

    id = Column(String(64), primary_key=True)
    session_id = Column(String, nullable=False)
    run_id = Column(String(64), nullable=False)
    plan_id = Column(String(64), nullable=True)
    plan_version = Column(Integer, nullable=True)
    workspace_root = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="open")
    base_ref = Column(String(128), nullable=True)
    base_sha = Column(String(128), nullable=True)
    summary_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=_utc_now)
    updated_at = Column(DateTime, nullable=False, default=_utc_now, onupdate=_utc_now)


class ChangeItem(Base):
    __tablename__ = "change_items"
    __table_args__ = (
        Index("idx_change_items_set", "change_set_id"),
        Index("idx_change_items_call", "change_set_id", "call_id"),
        Index("idx_change_items_path", "change_set_id", "path"),
    )

    id = Column(String(64), primary_key=True)
    change_set_id = Column(
        String(64), ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False,
    )
    call_id = Column(String(128), nullable=True)
    tool_name = Column(String(128), nullable=False)
    operation = Column(String(32), nullable=False, default="unknown")
    path = Column(Text, nullable=False, default="")
    before_sha256 = Column(String(80), nullable=True)
    after_sha256 = Column(String(80), nullable=True)
    before_blob_ref = Column(String(128), nullable=True)
    after_blob_ref = Column(String(128), nullable=True)
    diff_stats_json = Column(Text, nullable=False, default="{}")
    risk = Column(String(32), nullable=False, default="unknown")
    status = Column(String(32), nullable=False, default="observed")
    review_status = Column(String(32), nullable=False, default="pending")
    review_note = Column(Text, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    plan_step_id = Column(String(160), nullable=True)
    plan_step_index = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utc_now)


class ChangeEvent(Base):
    __tablename__ = "change_events"
    __table_args__ = (
        Index("idx_change_events_set", "change_set_id"),
        Index("idx_change_events_created", "created_at"),
    )

    id = Column(String(64), primary_key=True)
    change_set_id = Column(
        String(64), ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False,
    )
    change_item_id = Column(String(64), nullable=True)
    event_type = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=_utc_now)


class ValidationRun(Base):
    """One explicit validation command and its captured evidence."""

    __tablename__ = "validation_runs"
    __table_args__ = (
        Index("idx_validation_runs_set", "change_set_id"),
        Index("idx_validation_runs_created", "created_at"),
    )

    id = Column(String(64), primary_key=True)
    change_set_id = Column(
        String(64), ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False,
    )
    run_id = Column(String(64), nullable=True)
    command = Column(Text, nullable=False)
    cwd = Column(Text, nullable=False, default="")
    status = Column(String(32), nullable=False, default="running")
    exit_code = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    timed_out = Column(Boolean, nullable=False, default=False)
    stdout = Column(Text, nullable=False, default="")
    stderr = Column(Text, nullable=False, default="")
    report_ref = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utc_now)
    finished_at = Column(DateTime, nullable=True)
