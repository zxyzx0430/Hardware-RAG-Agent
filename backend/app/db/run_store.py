"""Synchronous run-recovery storage used by the API/runtime layer.

The store wraps the existing ``database.SessionLocal`` (or an injected
``sessionmaker`` for tests). Each call opens its own short transaction, so a
``RunStore`` instance can be shared across threads.

Event sequencing: ``append_event`` increments ``runs.last_event_seq`` and
inserts the event in one transaction. SQLite serializes writers, so two
concurrent appenders cannot read the same starting value; the sequence is
monotonic per run and starts at 1.

Datetimes are stored as naive UTC (SQLite has no timezone type) and returned
as timezone-aware UTC datetimes, matching ``app.db.time_utils.utc_now``.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db import database
from app.db.recovery_models import (
    APPROVAL_STATUSES,
    RUN_STATUSES,
    PendingApproval,
    RequestSnapshot,
    Run,
    RunEvent,
)

RUN_STATUS_RUNNING = "running"
RUN_STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_INTERRUPTED = "interrupted"

TERMINAL_RUN_STATUSES = frozenset(
    {RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_CANCELLED}
)

APPROVAL_STATUS_PENDING = "pending"
APPROVAL_STATUS_ALLOWED = "allowed"
APPROVAL_STATUS_DENIED = "denied"
APPROVAL_STATUS_STOPPED = "stopped"
APPROVAL_STATUS_EXPIRED = "expired"

APPROVAL_DECISION_STATUSES = frozenset(
    {APPROVAL_STATUS_ALLOWED, APPROVAL_STATUS_DENIED, APPROVAL_STATUS_STOPPED}
)

PROCESS_RESTART_ERROR_CODE = "PROCESS_RESTART"

_UNSET = object()

_KEY_SPLIT_PATTERN = re.compile(r"[^a-z0-9]+")
_KEY_CAMEL_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_EXACT_SENSITIVE_KEYS = frozenset(
    {
        "auth",
        "authorization",
        "api_key",
        "apikey",
        "key",
        "keys",
        "token",
        "tokens",
        "secret",
        "secrets",
        "password",
        "passwd",
        "credential",
        "credentials",
        "private_key",
        "cookie",
        "cookies",
    }
)
# Suffixes rather than arbitrary segments: ``max_tokens`` and ``token_limit``
# are ordinary request parameters and must survive snapshot sanitization.
_SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_key",
    "_token",
    "_secret",
    "_password",
    "_passwd",
    "_credential",
    "_credentials",
    "_private_key",
    "_cookie",
)
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "private_key",
    "client_secret",
    "session_token",
    "authorization",
)


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    session_id: str
    status: str
    model: str | None
    permission_mode: str | None
    config: Any
    thread_id: str | None
    checkpoint_id: str | None
    started_at: _dt.datetime
    finished_at: _dt.datetime | None
    last_event_seq: int
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class RunEventRecord:
    run_id: str
    seq: int
    type: str
    payload: Any
    created_at: _dt.datetime


@dataclass(frozen=True, slots=True)
class PendingApprovalRecord:
    id: str
    run_id: str
    call_id: str
    tool_name: str
    args: Any
    risk_level: str | None
    status: str
    created_at: _dt.datetime
    expires_at: _dt.datetime | None
    decided_at: _dt.datetime | None
    principal_id: str | None


@dataclass(frozen=True, slots=True)
class RequestSnapshotRecord:
    id: str
    run_id: str
    session_id: str
    payload: Any
    created_at: _dt.datetime
    expires_at: _dt.datetime | None


def _normalize_key(key: str) -> str:
    key = _KEY_CAMEL_PATTERN.sub("_", key)
    return _KEY_SPLIT_PATTERN.sub("_", key.lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = _normalize_key(key)
    if not normalized:
        return False
    if normalized in _EXACT_SENSITIVE_KEYS:
        return True
    if normalized.endswith(_SENSITIVE_KEY_SUFFIXES):
        return True
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def sanitize_payload(value: Any) -> Any:
    """Return a copy with credential-like mapping entries removed.

    Applied to ``runs.config_json`` and ``request_snapshots.payload_json`` per
    the recovery contract ("sanitized, no API keys"). Lists and nested mappings
    are handled recursively; the input is not mutated.
    """
    if isinstance(value, dict):
        return {
            key: sanitize_payload(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item) for item in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _utc_now_naive() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _to_utc_naive(value: _dt.datetime | None) -> _dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(_dt.timezone.utc).replace(tzinfo=None)


def _to_utc_aware(value: _dt.datetime | None) -> _dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc)


def _require_non_negative_int(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_run_status(status: Any) -> None:
    if status not in RUN_STATUSES:
        raise ValueError(f"invalid run status: {status!r}")


def _require_approval_status(status: Any) -> None:
    if status not in APPROVAL_STATUSES:
        raise ValueError(f"invalid approval status: {status!r}")


def _require_decision_status(status: Any) -> None:
    if status not in APPROVAL_DECISION_STATUSES:
        raise ValueError(f"invalid approval decision: {status!r}")


def _run_record(row: Run) -> RunRecord:
    return RunRecord(
        id=row.id,
        session_id=row.session_id,
        status=row.status,
        model=row.model,
        permission_mode=row.permission_mode,
        config=_json_loads(row.config_json),
        thread_id=row.thread_id,
        checkpoint_id=row.checkpoint_id,
        started_at=_to_utc_aware(row.started_at),
        finished_at=_to_utc_aware(row.finished_at),
        last_event_seq=row.last_event_seq,
        error_code=row.error_code,
        error_message=row.error_message,
    )


def _event_record(row: RunEvent) -> RunEventRecord:
    return RunEventRecord(
        run_id=row.run_id,
        seq=row.seq,
        type=row.type,
        payload=_json_loads(row.payload_json),
        created_at=_to_utc_aware(row.created_at),
    )


def _approval_record(row: PendingApproval) -> PendingApprovalRecord:
    return PendingApprovalRecord(
        id=row.id,
        run_id=row.run_id,
        call_id=row.call_id,
        tool_name=row.tool_name,
        args=_json_loads(row.args_json),
        risk_level=row.risk_level,
        status=row.status,
        created_at=_to_utc_aware(row.created_at),
        expires_at=_to_utc_aware(row.expires_at),
        decided_at=_to_utc_aware(row.decided_at),
        principal_id=row.principal_id,
    )


def _snapshot_record(row: RequestSnapshot) -> RequestSnapshotRecord:
    return RequestSnapshotRecord(
        id=row.id,
        run_id=row.run_id,
        session_id=row.session_id,
        payload=_json_loads(row.payload_json),
        created_at=_to_utc_aware(row.created_at),
        expires_at=_to_utc_aware(row.expires_at),
    )


class RunStore:
    """Persistence facade for runs, events, approvals and request snapshots."""

    def __init__(self, session_factory=None):
        # Resolve SessionLocal lazily so tests can patch database.SessionLocal.
        self._session_factory = session_factory

    @property
    def session_factory(self):
        return self._session_factory or database.SessionLocal

    def _session(self):
        return self.session_factory()

    # ---------------------------------------------------------------- runs

    def create_run(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        status: str = RUN_STATUS_RUNNING,
        model: str | None = None,
        permission_mode: str | None = None,
        config: Any = None,
        thread_id: str | None = None,
        checkpoint_id: str | None = None,
        started_at: _dt.datetime | None = None,
    ) -> RunRecord:
        _require_run_status(status)
        run_id = run_id or f"r_{uuid.uuid4().hex}"
        row = Run(
            id=run_id,
            session_id=session_id,
            status=status,
            model=model,
            permission_mode=permission_mode,
            config_json=_json_dumps(sanitize_payload(config)) if config is not None else None,
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            started_at=_to_utc_naive(started_at) or _utc_now_naive(),
            finished_at=None,
            last_event_seq=0,
        )
        with self._session() as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError(f"run already exists: {run_id}") from exc
            record = _run_record(row)
        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._session() as session:
            row = session.get(Run, run_id)
            return _run_record(row) if row is not None else None

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        finished_at: _dt.datetime | None | object = _UNSET,
        error_code: str | None | object = _UNSET,
        error_message: str | None | object = _UNSET,
    ) -> RunRecord | None:
        """Update status; terminal statuses get ``finished_at=now`` by default."""
        _require_run_status(status)
        values: dict[str, Any] = {"status": status}
        if finished_at is _UNSET:
            if status in TERMINAL_RUN_STATUSES:
                values["finished_at"] = _utc_now_naive()
        else:
            values["finished_at"] = _to_utc_naive(finished_at)
        if error_code is not _UNSET:
            values["error_code"] = error_code
        if error_message is not _UNSET:
            values["error_message"] = error_message
        with self._session() as session:
            result = session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            changed = result.rowcount
            session.commit()
        if changed != 1:
            return None
        return self.get_run(run_id)

    def mark_running_interrupted(
        self, *, error_code: str = PROCESS_RESTART_ERROR_CODE
    ) -> int:
        """Startup recovery: running/awaiting_confirmation runs become interrupted.

        Pending approval rows are deliberately left untouched.
        """
        with self._session() as session:
            result = session.execute(
                update(Run)
                .where(
                    Run.status.in_(
                        (RUN_STATUS_RUNNING, RUN_STATUS_AWAITING_CONFIRMATION)
                    )
                )
                .values(status=RUN_STATUS_INTERRUPTED, error_code=error_code)
                .execution_options(synchronize_session=False)
            )
            changed = result.rowcount
            session.commit()
        return changed

    # -------------------------------------------------------------- events

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Any = None,
        *,
        created_at: _dt.datetime | None = None,
    ) -> RunEventRecord:
        """Append one event and return it with its allocated ``seq``.

        ``seq`` is allocated by incrementing ``runs.last_event_seq`` and the
        insert runs in the same transaction, so concurrent appenders cannot
        reuse a sequence number.
        """
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be a non-empty string")
        payload = {} if payload is None else payload
        payload_json = _json_dumps(payload)
        created = _to_utc_naive(created_at) or _utc_now_naive()
        with self._session() as session:
            result = session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(last_event_seq=Run.last_event_seq + 1)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                session.rollback()
                raise LookupError(f"run not found: {run_id}")
            seq = session.execute(
                select(Run.last_event_seq).where(Run.id == run_id)
            ).scalar_one()
            session.add(
                RunEvent(
                    run_id=run_id,
                    seq=seq,
                    type=event_type,
                    payload_json=payload_json,
                    created_at=created,
                )
            )
            session.commit()
        return RunEventRecord(
            run_id=run_id,
            seq=seq,
            type=event_type,
            payload=_json_loads(payload_json),
            created_at=_to_utc_aware(created),
        )

    def list_events(
        self, run_id: str, after: int = 0, *, limit: int | None = None
    ) -> list[RunEventRecord]:
        """Return events with ``seq > after`` in ascending order."""
        _require_non_negative_int(after, "after")
        if limit is not None:
            _require_non_negative_int(limit, "limit")
        statement = (
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after)
            .order_by(RunEvent.seq.asc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._session() as session:
            rows = session.execute(statement).scalars().all()
            return [_event_record(row) for row in rows]

    # ----------------------------------------------------------- approvals

    def create_pending_approval(
        self,
        run_id: str,
        call_id: str,
        tool_name: str,
        args: Any = None,
        *,
        approval_id: str | None = None,
        risk_level: str | None = None,
        status: str = APPROVAL_STATUS_PENDING,
        created_at: _dt.datetime | None = None,
        expires_at: _dt.datetime | None = None,
        principal_id: str | None = None,
    ) -> PendingApprovalRecord:
        _require_approval_status(status)
        approval_id = approval_id or f"pa_{uuid.uuid4().hex}"
        row = PendingApproval(
            id=approval_id,
            run_id=run_id,
            call_id=call_id,
            tool_name=tool_name,
            args_json=_json_dumps({} if args is None else args),
            risk_level=risk_level,
            status=status,
            created_at=_to_utc_naive(created_at) or _utc_now_naive(),
            expires_at=_to_utc_naive(expires_at),
            decided_at=None,
            principal_id=principal_id,
        )
        with self._session() as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError(f"approval already exists: {approval_id}") from exc
            record = _approval_record(row)
        return record

    def get_pending_approval(self, approval_id: str) -> PendingApprovalRecord | None:
        with self._session() as session:
            row = session.get(PendingApproval, approval_id)
            return _approval_record(row) if row is not None else None

    def get_pending_approval_by_call(
        self, run_id: str, call_id: str, *, status: str | None = None
    ) -> PendingApprovalRecord | None:
        """Return the most recent approval for ``(run_id, call_id)``."""
        statement = select(PendingApproval).where(
            PendingApproval.run_id == run_id,
            PendingApproval.call_id == call_id,
        )
        if status is not None:
            _require_approval_status(status)
            statement = statement.where(PendingApproval.status == status)
        statement = statement.order_by(
            PendingApproval.created_at.desc(), PendingApproval.id.desc()
        ).limit(1)
        with self._session() as session:
            row = session.execute(statement).scalar_one_or_none()
            return _approval_record(row) if row is not None else None

    def list_pending_approvals(
        self, run_id: str | None = None, *, status: str | None = APPROVAL_STATUS_PENDING
    ) -> list[PendingApprovalRecord]:
        statement = select(PendingApproval)
        if run_id is not None:
            statement = statement.where(PendingApproval.run_id == run_id)
        if status is not None:
            _require_approval_status(status)
            statement = statement.where(PendingApproval.status == status)
        statement = statement.order_by(
            PendingApproval.created_at.asc(), PendingApproval.id.asc()
        )
        with self._session() as session:
            rows = session.execute(statement).scalars().all()
            return [_approval_record(row) for row in rows]

    def decide_pending_approval(
        self,
        approval_id: str,
        status: str,
        *,
        decided_at: _dt.datetime | None = None,
        principal_id: str | None | object = _UNSET,
    ) -> PendingApprovalRecord | None:
        """Atomically consume a pending approval.

        Returns ``None`` when the row is missing, was already decided, or has
        expired. An overdue row that is still marked ``pending`` is transitioned
        to ``expired`` here, so a stale approval can never be allowed even if
        the periodic sweep has not run.
        """
        _require_decision_status(status)
        moment = _to_utc_naive(decided_at) or _utc_now_naive()
        values: dict[str, Any] = {
            "status": status,
            "decided_at": moment,
        }
        if principal_id is not _UNSET:
            values["principal_id"] = principal_id
        with self._session() as session:
            result = session.execute(
                update(PendingApproval)
                .where(
                    PendingApproval.id == approval_id,
                    PendingApproval.status == APPROVAL_STATUS_PENDING,
                    or_(
                        PendingApproval.expires_at.is_(None),
                        PendingApproval.expires_at > moment,
                    ),
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            changed = result.rowcount
            if changed != 1:
                session.execute(
                    update(PendingApproval)
                    .where(
                        PendingApproval.id == approval_id,
                        PendingApproval.status == APPROVAL_STATUS_PENDING,
                        PendingApproval.expires_at.is_not(None),
                        PendingApproval.expires_at <= moment,
                    )
                    .values(status=APPROVAL_STATUS_EXPIRED)
                    .execution_options(synchronize_session=False)
                )
            session.commit()
        if changed != 1:
            return None
        return self.get_pending_approval(approval_id)

    def decide_pending_approval_by_call(
        self,
        run_id: str,
        call_id: str,
        status: str,
        *,
        decided_at: _dt.datetime | None = None,
        principal_id: str | None | object = _UNSET,
    ) -> PendingApprovalRecord | None:
        pending = self.get_pending_approval_by_call(
            run_id, call_id, status=APPROVAL_STATUS_PENDING
        )
        if pending is None:
            return None
        return self.decide_pending_approval(
            pending.id,
            status,
            decided_at=decided_at,
            principal_id=principal_id,
        )

    def expire_pending_approvals(
        self, now: _dt.datetime | None = None
    ) -> int:
        """Mark overdue pending approvals as expired; returns the row count."""
        moment = _to_utc_naive(now) or _utc_now_naive()
        with self._session() as session:
            result = session.execute(
                update(PendingApproval)
                .where(
                    PendingApproval.status == APPROVAL_STATUS_PENDING,
                    PendingApproval.expires_at.is_not(None),
                    PendingApproval.expires_at <= moment,
                )
                .values(status=APPROVAL_STATUS_EXPIRED)
                .execution_options(synchronize_session=False)
            )
            changed = result.rowcount
            session.commit()
        return changed

    # ------------------------------------------------------- snapshots

    def save_request_snapshot(
        self,
        run_id: str,
        session_id: str,
        payload: Any,
        *,
        snapshot_id: str | None = None,
        created_at: _dt.datetime | None = None,
        expires_at: _dt.datetime | None = None,
    ) -> RequestSnapshotRecord:
        snapshot_id = snapshot_id or f"rs_{uuid.uuid4().hex}"
        row = RequestSnapshot(
            id=snapshot_id,
            run_id=run_id,
            session_id=session_id,
            payload_json=_json_dumps(sanitize_payload(payload)),
            created_at=_to_utc_naive(created_at) or _utc_now_naive(),
            expires_at=_to_utc_naive(expires_at),
        )
        with self._session() as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError(f"snapshot already exists: {snapshot_id}") from exc
            record = _snapshot_record(row)
        return record

    def get_request_snapshot(
        self, snapshot_id: str | None = None, *, run_id: str | None = None
    ) -> RequestSnapshotRecord | None:
        """Get by id, or the latest snapshot for a run."""
        if (snapshot_id is None) == (run_id is None):
            raise ValueError("pass exactly one of snapshot_id or run_id")
        with self._session() as session:
            if snapshot_id is not None:
                row = session.get(RequestSnapshot, snapshot_id)
            else:
                row = session.execute(
                    select(RequestSnapshot)
                    .where(RequestSnapshot.run_id == run_id)
                    .order_by(
                        RequestSnapshot.created_at.desc(),
                        RequestSnapshot.id.desc(),
                    )
                    .limit(1)
                ).scalar_one_or_none()
            return _snapshot_record(row) if row is not None else None

    def delete_request_snapshot(
        self, snapshot_id: str | None = None, *, run_id: str | None = None
    ) -> int:
        """Delete by id, or every snapshot for a run; returns the row count."""
        if (snapshot_id is None) == (run_id is None):
            raise ValueError("pass exactly one of snapshot_id or run_id")
        statement = delete(RequestSnapshot)
        if snapshot_id is not None:
            statement = statement.where(RequestSnapshot.id == snapshot_id)
        else:
            statement = statement.where(RequestSnapshot.run_id == run_id)
        with self._session() as session:
            result = session.execute(statement)
            changed = result.rowcount
            session.commit()
        return changed
