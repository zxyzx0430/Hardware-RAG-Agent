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
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, delete, or_, select, update
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
    {
        RUN_STATUS_COMPLETED,
        RUN_STATUS_FAILED,
        RUN_STATUS_CANCELLED,
        # Runtime treats interrupted as finished (no live writer remains).
        RUN_STATUS_INTERRUPTED,
    }
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
    "_keys",
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


def _isoformat(value: _dt.datetime | None) -> str | None:
    aware = _to_utc_aware(value)
    return aware.isoformat() if aware is not None else None


def _parse_datetime(value: Any) -> _dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value
    if not isinstance(value, str):
        raise TypeError(
            f"expected an ISO-8601 datetime string, got {type(value).__name__}"
        )
    try:
        return _dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 datetime: {value!r}") from exc


def _decode_json_argument(value: Any) -> Any:
    """Accept either a JSON string (runtime rows) or an already-decoded value."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


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
        self,
        *,
        error_code: str = PROCESS_RESTART_ERROR_CODE,
        error_message: str | None = None,
    ) -> int:
        """Startup recovery: running/awaiting_confirmation runs become interrupted.

        Pending approval rows are deliberately left untouched. ``finished_at``
        is stamped because no writer survives this process.
        """
        with self._session() as session:
            result = session.execute(
                update(Run)
                .where(
                    Run.status.in_(
                        (RUN_STATUS_RUNNING, RUN_STATUS_AWAITING_CONFIRMATION)
                    )
                )
                .values(
                    status=RUN_STATUS_INTERRUPTED,
                    error_code=error_code,
                    error_message=error_message,
                    finished_at=_utc_now_naive(),
                )
                .execution_options(synchronize_session=False)
            )
            changed = result.rowcount
            session.commit()
        return changed

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        statuses: tuple[str, ...] | list[str] | None = None,
        limit: int | None = None,
    ) -> list[RunRecord]:
        """Return runs newest first, optionally scoped by session and status."""
        statement = select(Run)
        if session_id is not None:
            statement = statement.where(Run.session_id == session_id)
        if statuses is not None:
            wanted = tuple(statuses)
            for status in wanted:
                _require_run_status(status)
            if not wanted:
                return []
            statement = statement.where(Run.status.in_(wanted))
        statement = statement.order_by(
            Run.started_at.desc(), Run.id.desc()
        )
        if limit is not None:
            _require_non_negative_int(limit, "limit")
            statement = statement.limit(limit)
        with self._session() as session:
            rows = session.execute(statement).scalars().all()
            return [_run_record(row) for row in rows]

    def claim_run_status(
        self,
        run_id: str,
        *,
        expected_status: tuple[str, ...] | list[str] | str,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: _dt.datetime | None = None,
    ) -> RunRecord | None:
        """Atomically move a run from an expected status (resume/claim guard).

        Returns ``None`` when the run does not exist or is no longer in one of
        the expected statuses, so two concurrent resumes cannot both win.
        """
        _require_run_status(status)
        expected = (
            (expected_status,) if isinstance(expected_status, str) else tuple(expected_status)
        )
        if not expected:
            return None
        for item in expected:
            _require_run_status(item)
        values: dict[str, Any] = {
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
        }
        if finished_at is not None:
            values["finished_at"] = _to_utc_naive(finished_at)
        elif status in TERMINAL_RUN_STATUSES:
            values["finished_at"] = _utc_now_naive()
        elif status == RUN_STATUS_RUNNING:
            values["finished_at"] = None
        with self._session() as session:
            result = session.execute(
                update(Run)
                .where(Run.id == run_id, Run.status.in_(expected))
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            changed = result.rowcount
            session.commit()
        if changed != 1:
            return None
        return self.get_run(run_id)

    def update_run_checkpoint(
        self, run_id: str, checkpoint_id: str | None
    ) -> bool:
        """Record the checkpoint a paused run can resume from."""
        with self._session() as session:
            result = session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(checkpoint_id=checkpoint_id)
                .execution_options(synchronize_session=False)
            )
            changed = result.rowcount
            session.commit()
        return changed == 1

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

    def consume_pending_approvals(
        self,
        run_id: str,
        *,
        status: str,
        call_id: str | None = None,
        decided_at: _dt.datetime | None = None,
    ) -> list[PendingApprovalRecord]:
        """Atomically consume pending approvals for one run.

        With ``call_id`` only that call is targeted; without it every pending
        row of the run is consumed (cancel semantics). Overdue rows become
        ``expired`` and are not returned. A concurrent consumer that loses the
        race gets an empty list.
        """
        _require_decision_status(status)
        moment = _to_utc_naive(decided_at) or _utc_now_naive()
        conditions = [
            PendingApproval.run_id == run_id,
            PendingApproval.status == APPROVAL_STATUS_PENDING,
        ]
        if call_id is not None:
            conditions.append(PendingApproval.call_id == call_id)
        next_status = case(
            (
                and_(
                    PendingApproval.expires_at.is_not(None),
                    PendingApproval.expires_at <= moment,
                ),
                APPROVAL_STATUS_EXPIRED,
            ),
            else_=status,
        )
        statement = (
            update(PendingApproval)
            .where(*conditions)
            .values(status=next_status, decided_at=moment)
            .returning(PendingApproval.id, PendingApproval.status)
            .execution_options(synchronize_session=False)
        )
        with self._session() as session:
            updated = session.execute(statement).all()
            session.commit()
        consumed_ids = [
            row.id for row in updated if row.status != APPROVAL_STATUS_EXPIRED
        ]
        records: list[PendingApprovalRecord] = []
        for approval_id in consumed_ids:
            record = self.get_pending_approval(approval_id)
            if record is not None:
                records.append(record)
        return records

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


_RUNTIME_DECISION_STATUS = {
    "allow": APPROVAL_STATUS_ALLOWED,
    "deny": APPROVAL_STATUS_DENIED,
    "stop": APPROVAL_STATUS_STOPPED,
}


class RuntimeRunStore:
    """Dict-row adapter expected by ``src.agent.run_events.get_run_store()``.

    The runtime layer was written against plain dicts keyed by SQLite column
    names and ISO-8601 timestamp strings. ``RunStore`` itself exposes typed
    records; this adapter converts both directions and keeps JSON columns as
    strings at rest as the runtime expects.
    """

    def __init__(self, store: RunStore | None = None):
        self._store = store or RunStore()

    @property
    def store(self) -> RunStore:
        return self._store

    @staticmethod
    def _run_row(record: RunRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "session_id": record.session_id,
            "status": record.status,
            "model": record.model,
            "permission_mode": record.permission_mode,
            "config_json": (
                _json_dumps(record.config) if record.config is not None else None
            ),
            "thread_id": record.thread_id,
            "checkpoint_id": record.checkpoint_id,
            "started_at": _isoformat(record.started_at),
            "finished_at": _isoformat(record.finished_at),
            "last_event_seq": record.last_event_seq,
            "error_code": record.error_code,
            "error_message": record.error_message,
        }

    @staticmethod
    def _event_row(record: RunEventRecord) -> dict[str, Any]:
        return {
            "run_id": record.run_id,
            "seq": record.seq,
            "type": record.type,
            "payload": record.payload,
            "created_at": _isoformat(record.created_at),
        }

    @staticmethod
    def _approval_row(record: PendingApprovalRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "run_id": record.run_id,
            "call_id": record.call_id,
            "tool_name": record.tool_name,
            "args_json": _json_dumps(record.args),
            "risk_level": record.risk_level,
            "status": record.status,
            "created_at": _isoformat(record.created_at),
            "expires_at": _isoformat(record.expires_at),
            "decided_at": _isoformat(record.decided_at),
            "principal_id": record.principal_id,
        }

    @staticmethod
    def _snapshot_row(record: RequestSnapshotRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "run_id": record.run_id,
            "session_id": record.session_id,
            "payload_json": _json_dumps(record.payload),
            "created_at": _isoformat(record.created_at),
            "expires_at": _isoformat(record.expires_at),
        }

    def create_run(self, run: Mapping[str, Any]) -> dict[str, Any]:
        record = self._store.create_run(
            run["session_id"],
            run_id=run.get("id"),
            status=run.get("status") or RUN_STATUS_RUNNING,
            model=run.get("model"),
            permission_mode=run.get("permission_mode"),
            config=_decode_json_argument(run.get("config_json")),
            thread_id=run.get("thread_id"),
            checkpoint_id=run.get("checkpoint_id"),
            started_at=_parse_datetime(run.get("started_at")),
        )
        return self._run_row(record)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        record = self._store.get_run(run_id)
        return self._run_row(record) if record is not None else None

    def append_run_event(
        self, run_id: str, event_type: str, payload: Mapping[str, Any]
    ) -> int:
        return self._store.append_event(run_id, event_type, payload).seq

    def list_run_events(
        self, run_id: str, after_seq: int = 0
    ) -> list[dict[str, Any]]:
        return [
            self._event_row(record)
            for record in self._store.list_events(run_id, after=after_seq)
        ]

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: str | None = None,
    ) -> bool:
        kwargs: dict[str, Any] = {
            "error_code": error_code,
            "error_message": error_message,
        }
        if finished_at is not None:
            kwargs["finished_at"] = _parse_datetime(finished_at)
        return self._store.update_run_status(run_id, status, **kwargs) is not None

    def mark_interrupted_runs(
        self,
        *,
        error_code: str = PROCESS_RESTART_ERROR_CODE,
        error_message: str | None = None,
    ) -> int:
        return self._store.mark_running_interrupted(
            error_code=error_code, error_message=error_message
        )

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        statuses: Any = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        records = self._store.list_runs(
            session_id=session_id,
            statuses=(
                (statuses,) if isinstance(statuses, str)
                else tuple(statuses) if statuses is not None else None
            ),
            limit=limit,
        )
        return [self._run_row(record) for record in records]

    def claim_run_status(
        self,
        run_id: str,
        *,
        expected_status: Any,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any] | None:
        record = self._store.claim_run_status(
            run_id,
            expected_status=expected_status,
            status=status,
            error_code=error_code,
            error_message=error_message,
            finished_at=_parse_datetime(finished_at),
        )
        return self._run_row(record) if record is not None else None

    def update_run_checkpoint(
        self, run_id: str, checkpoint_id: str | None
    ) -> bool:
        return self._store.update_run_checkpoint(run_id, checkpoint_id)

    def expire_pending_approvals(self, now: Any = None) -> int:
        moment = _parse_datetime(now) if isinstance(now, str) else now
        return self._store.expire_pending_approvals(moment)

    def create_pending_approval(
        self, approval: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        run_id = approval["run_id"]
        call_id = approval["call_id"]
        existing = self._store.get_pending_approval_by_call(
            run_id, call_id, status=APPROVAL_STATUS_PENDING
        )
        if existing is not None:
            return self._approval_row(existing)
        try:
            record = self._store.create_pending_approval(
                run_id,
                call_id,
                approval["tool_name"],
                _decode_json_argument(approval.get("args_json")),
                approval_id=approval.get("id"),
                risk_level=approval.get("risk_level"),
                status=approval.get("status") or APPROVAL_STATUS_PENDING,
                created_at=_parse_datetime(approval.get("created_at")),
                expires_at=_parse_datetime(approval.get("expires_at")),
                principal_id=approval.get("principal_id"),
            )
        except ValueError:
            # A concurrent writer created the pending row first; SQLite's
            # partial unique index guarantees only one survives.
            existing = self._store.get_pending_approval_by_call(
                run_id, call_id, status=APPROVAL_STATUS_PENDING
            )
            if existing is None:
                raise
            return self._approval_row(existing)
        return self._approval_row(record)

    def list_pending_approvals(
        self, run_id: str, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            self._approval_row(record)
            for record in self._store.list_pending_approvals(run_id, status=status)
        ]

    def consume_pending_approvals(
        self,
        run_id: str,
        *,
        call_id: str | None,
        decision: str,
        decided_at: str | None = None,
    ) -> list[dict[str, Any]]:
        target_status = _RUNTIME_DECISION_STATUS.get(decision)
        if target_status is None:
            raise ValueError(f"unsupported approval decision {decision!r}")
        records = self._store.consume_pending_approvals(
            run_id,
            status=target_status,
            call_id=call_id,
            decided_at=_parse_datetime(decided_at),
        )
        return [self._approval_row(record) for record in records]

    def create_request_snapshot(
        self, snapshot: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        record = self._store.save_request_snapshot(
            snapshot["run_id"],
            snapshot.get("session_id") or "",
            _decode_json_argument(snapshot.get("payload_json")),
            snapshot_id=snapshot.get("id"),
            created_at=_parse_datetime(snapshot.get("created_at")),
            expires_at=_parse_datetime(snapshot.get("expires_at")),
        )
        return self._snapshot_row(record)

    def get_latest_request_snapshot(
        self, run_id: str
    ) -> dict[str, Any] | None:
        record = self._store.get_request_snapshot(run_id=run_id)
        return self._snapshot_row(record) if record is not None else None


_RUNTIME_STORE: RuntimeRunStore | None = None
_RUNTIME_STORE_LOCK = threading.Lock()


def get_run_store() -> RuntimeRunStore:
    """Return the process-wide dict-row store used by the runtime layer."""
    global _RUNTIME_STORE
    if _RUNTIME_STORE is None:
        with _RUNTIME_STORE_LOCK:
            if _RUNTIME_STORE is None:
                _RUNTIME_STORE = RuntimeRunStore()
    return _RUNTIME_STORE


def consume_pending_approvals(
    run_id: str,
    *,
    call_id: str | None = None,
    decision: str = "allow",
    approval_id: str | None = None,
    status: str | None = None,
    principal_id: str | None = None,
    now: object | None = None,
    expires_at: object | None = None,
) -> list[dict[str, Any]]:
    """Contract-level one-shot approval consumer for acceptance/recovery callers.

    ``approval_id``/``status``/``principal_id`` are accepted for compatibility
    with callers that address a single row; the store's atomic consume remains
    scoped by ``(run_id, call_id)`` and returns the consumed rows.
    """
    _ = (approval_id, status, principal_id, now, expires_at)
    return get_run_store().consume_pending_approvals(
        run_id,
        call_id=call_id,
        decision=decision,
    )
