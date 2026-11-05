"""SQLAlchemy models for run recovery (Recovery R1).

Tables defined by the shared recovery contract:

- ``runs``: one chat request, its lifecycle status and checkpoint pointers.
- ``run_events``: append-only persisted stream events, keyed ``(run_id, seq)``.
- ``pending_approvals``: HITL approvals that must survive a restart.
- ``request_snapshots``: sanitized request payloads for recovery/replay.
"""

import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, Text, text

from app.db.database import Base

RUN_STATUSES = (
    "running",
    "awaiting_confirmation",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)

APPROVAL_STATUSES = ("pending", "allowed", "denied", "stopped", "expired")


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class Run(Base):
    """A single chat request that can be recovered after disconnect/restart."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("idx_runs_session", "session_id"),
        Index("idx_runs_status", "status"),
        Index("idx_runs_started", "started_at"),
        CheckConstraint(
            "status IN ('running', 'awaiting_confirmation', 'completed', "
            "'failed', 'cancelled', 'interrupted')",
            name="ck_runs_status",
        ),
    )

    id = Column(String(64), primary_key=True)
    session_id = Column(String, nullable=False)
    status = Column(String(32), nullable=False, default="running")
    model = Column(String, nullable=True)
    permission_mode = Column(String(32), nullable=True)
    config_json = Column(Text, nullable=True)
    thread_id = Column(String, nullable=True)
    checkpoint_id = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=False, default=_utc_now)
    finished_at = Column(DateTime, nullable=True)
    last_event_seq = Column(Integer, nullable=False, default=0, server_default="0")
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)


class RunEvent(Base):
    """Persisted SSE event; ``seq`` is allocated per run inside the write txn."""

    __tablename__ = "run_events"

    run_id = Column(String(64), primary_key=True)
    seq = Column(Integer, primary_key=True)
    type = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utc_now)


class PendingApproval(Base):
    """HITL approval persisted before the user decides; consumed once."""

    __tablename__ = "pending_approvals"
    __table_args__ = (
        Index("idx_pending_approvals_run", "run_id"),
        Index("idx_pending_approvals_call", "run_id", "call_id"),
        Index("idx_pending_approvals_status", "status"),
        # At most one live approval per tool call; decided/expired rows stay
        # append-only so history is preserved.
        Index(
            "uq_pending_approvals_pending_call",
            "run_id",
            "call_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
        CheckConstraint(
            "status IN ('pending', 'allowed', 'denied', 'stopped', 'expired')",
            name="ck_pending_approvals_status",
        ),
    )

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), nullable=False)
    call_id = Column(String(64), nullable=False)
    tool_name = Column(String(255), nullable=False)
    args_json = Column(Text, nullable=False)
    risk_level = Column(String(32), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, default=_utc_now)
    expires_at = Column(DateTime, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    principal_id = Column(String, nullable=True)


class RequestSnapshot(Base):
    """Sanitized request payload kept for re-attaching after a refresh."""

    __tablename__ = "request_snapshots"
    __table_args__ = (
        Index("idx_request_snapshots_run", "run_id"),
        Index("idx_request_snapshots_session", "session_id"),
    )

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), nullable=False)
    session_id = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utc_now)
    expires_at = Column(DateTime, nullable=True)
