"""Run-scoped event persistence, replay, and live fan-out for Recovery R1.

The runtime talks to a small store protocol instead of the ORM directly so the
storage layer (``app.db.run_store``) owns persistence and tests can inject a
fake. When that module is absent this file falls back to a process-local store;
recovery guarantees are only real with the persistent implementation.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Mapping, Protocol

logger = logging.getLogger(__name__)

RUN_STATUS_RUNNING = "running"
RUN_STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_INTERRUPTED = "interrupted"

ACTIVE_RUN_STATUSES = frozenset({RUN_STATUS_RUNNING, RUN_STATUS_AWAITING_CONFIRMATION})
FINISHED_RUN_STATUSES = frozenset({
    RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_CANCELLED, RUN_STATUS_INTERRUPTED,
})

APPROVAL_STATUS_PENDING = "pending"
APPROVAL_STATUS_EXPIRED = "expired"
APPROVAL_DECISION_STATUS = {
    "allow": "allowed",
    "deny": "denied",
    "stop": "stopped",
}

# Heartbeats keep proxies alive but carry no semantic state; persisting them
# would inflate the replay log and give clients ids they must dedupe.
NON_PERSISTED_EVENT_TYPES = frozenset({"heartbeat"})
PENDING_APPROVAL_TTL_SECONDS = 30 * 60
HEARTBEAT_INTERVAL_SECONDS = 15.0
INTERRUPTED_ERROR_CODE = "PROCESS_RESTART"
INTERRUPTED_EVENT_CODE = "INTERRUPTED"
CANCELLED_EVENT_CODE = "CANCELLED"
UNRESOLVED_SIDE_EFFECT_CODE = "UNRESOLVED_SIDE_EFFECT"

RUN_STARTED_EVENT = "run_started"
TOOL_CALL_EVENT = "tool_call"
TOOL_RESULT_EVENT = "tool_result"
TOOL_CONFIRM_EVENT = "tool_confirm_required"
APPROVAL_DECISION_EVENT = "approval_decision"
ERROR_EVENT = "error"
DONE_EVENT = "done"

# One RunEvent ledger carries every fact the frontend replays: model text,
# tool call/result pairs, approvals, errors, compaction and terminal state.
# The persisted payload is the SSE payload unchanged, so call_id, tool status,
# error classification, side-effect hints and evidence locators survive. The
# ToolAudit row remains the dispatch-time audit; it is joined by run_id and
# call_id instead of duplicating a second event stream.


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def new_run_id() -> str:
    return f"r_{uuid.uuid4().hex}"


def new_approval_id() -> str:
    return f"pa_{uuid.uuid4().hex}"


def new_snapshot_id() -> str:
    return f"rs_{uuid.uuid4().hex}"


class RunStore(Protocol):
    """Persistence contract used by the runtime.

    Rows are plain dicts keyed like the SQLite columns; the storage layer is
    responsible for timestamps and JSON serialization.
    """

    def create_run(self, run: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def append_run_event(
        self, run_id: str, event_type: str, payload: Mapping[str, Any],
    ) -> int:
        """Append one event, allocate the next seq, and update last_event_seq."""

    def list_run_events(
        self, run_id: str, after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        """Return events ordered by seq, each with seq/type/payload keys."""

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: str | None = None,
    ) -> bool: ...

    def mark_interrupted_runs(
        self,
        *,
        error_code: str = INTERRUPTED_ERROR_CODE,
        error_message: str | None = None,
    ) -> int: ...

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        statuses: Any = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return run rows (newest first), optionally scoped by session/status."""

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
        """Atomically move a run from one of ``expected_status`` to ``status``."""

    def expire_pending_approvals(self, now: datetime | None = None) -> int: ...

    def create_pending_approval(
        self, approval: Mapping[str, Any],
    ) -> dict[str, Any] | None: ...

    def list_pending_approvals(
        self, run_id: str, *, status: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def consume_pending_approvals(
        self,
        run_id: str,
        *,
        call_id: str | None,
        decision: str,
        decided_at: str | None = None,
    ) -> list[dict[str, Any]]:
        """One-shot consume of pending rows for a run.

        Returns the consumed rows, or an empty list when ``call_id`` is not
        pending anymore (already decided or expired).
        """

    def create_request_snapshot(
        self, snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None: ...

    def get_latest_request_snapshot(self, run_id: str) -> dict[str, Any] | None: ...


class InMemoryRunStore:
    """Process-local fallback used until ``app.db.run_store`` is available.

    It implements enough of the protocol to keep chat working and to let tests
    exercise the runtime end to end. It does not survive a restart.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._approvals: list[dict[str, Any]] = []
        self._snapshots: dict[str, list[dict[str, Any]]] = {}

    def create_run(self, run: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(run)
        row.setdefault("last_event_seq", 0)
        row.setdefault("status", RUN_STATUS_RUNNING)
        with self._lock:
            self._runs[row["id"]] = row
            self._events.setdefault(row["id"], [])
        return dict(row)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._runs.get(run_id)
            return dict(row) if row is not None else None

    def append_run_event(
        self, run_id: str, event_type: str, payload: Mapping[str, Any],
    ) -> int:
        with self._lock:
            row = self._runs.get(run_id)
            if row is None:
                raise KeyError(f"unknown run {run_id}")
            seq = int(row.get("last_event_seq") or 0) + 1
            row["last_event_seq"] = seq
            self._events.setdefault(run_id, []).append({
                "run_id": run_id,
                "seq": seq,
                "type": event_type,
                "payload": dict(payload),
                "created_at": utc_now_iso(),
            })
            return seq

    def list_run_events(
        self, run_id: str, after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(event) for event in self._events.get(run_id, ())
                if int(event["seq"]) > after_seq
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
        with self._lock:
            row = self._runs.get(run_id)
            if row is None:
                return False
            row["status"] = status
            row["error_code"] = error_code
            row["error_message"] = error_message
            if status in FINISHED_RUN_STATUSES:
                row["finished_at"] = finished_at or utc_now_iso()
            return True

    def mark_interrupted_runs(
        self,
        *,
        error_code: str = INTERRUPTED_ERROR_CODE,
        error_message: str | None = None,
    ) -> int:
        changed = 0
        with self._lock:
            for row in self._runs.values():
                if row.get("status") not in ACTIVE_RUN_STATUSES:
                    continue
                row["status"] = RUN_STATUS_INTERRUPTED
                row["error_code"] = error_code
                row["error_message"] = error_message
                row["finished_at"] = utc_now_iso()
                changed += 1
        return changed

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        statuses: Any = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        wanted = (
            None if statuses is None
            else {statuses} if isinstance(statuses, str)
            else set(statuses)
        )
        with self._lock:
            rows = [
                dict(row) for row in self._runs.values()
                if (session_id is None or row.get("session_id") == session_id)
                and (wanted is None or row.get("status") in wanted)
            ]
        rows.sort(
            key=lambda row: (str(row.get("started_at") or ""), str(row.get("id") or "")),
            reverse=True,
        )
        if limit is not None:
            rows = rows[: int(limit)]
        return rows

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
        expected = (
            {expected_status} if isinstance(expected_status, str) else set(expected_status)
        )
        with self._lock:
            row = self._runs.get(run_id)
            if row is None or row.get("status") not in expected:
                return None
            row["status"] = status
            row["error_code"] = error_code
            row["error_message"] = error_message
            if status in FINISHED_RUN_STATUSES:
                row["finished_at"] = finished_at or utc_now_iso()
            elif status in ACTIVE_RUN_STATUSES:
                row["finished_at"] = None
            return dict(row)

    def expire_pending_approvals(self, now: datetime | None = None) -> int:
        moment = now if isinstance(now, datetime) else utc_now()
        changed = 0
        with self._lock:
            for row in self._approvals:
                if (
                    row.get("status") == APPROVAL_STATUS_PENDING
                    and _expired(row.get("expires_at"), moment)
                ):
                    row["status"] = "expired"
                    changed += 1
        return changed

    def update_run_checkpoint(
        self, run_id: str, checkpoint_id: str | None,
    ) -> bool:
        with self._lock:
            row = self._runs.get(run_id)
            if row is None:
                return False
            row["checkpoint_id"] = checkpoint_id
            return True

    def create_pending_approval(
        self, approval: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        row = dict(approval)
        with self._lock:
            for existing in self._approvals:
                if (
                    existing["run_id"] == row["run_id"]
                    and existing["call_id"] == row["call_id"]
                    and existing["status"] == APPROVAL_STATUS_PENDING
                ):
                    return dict(existing)
            self._approvals.append(row)
        return dict(row)

    def list_pending_approvals(
        self, run_id: str, *, status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row) for row in self._approvals
                if row["run_id"] == run_id and (status is None or row["status"] == status)
            ]

    def consume_pending_approvals(
        self,
        run_id: str,
        *,
        call_id: str | None,
        decision: str,
        decided_at: str | None = None,
    ) -> list[dict[str, Any]]:
        target_status = APPROVAL_DECISION_STATUS.get(decision)
        if target_status is None:
            raise ValueError(f"unsupported approval decision {decision!r}")
        now = utc_now()
        consumed: list[dict[str, Any]] = []
        with self._lock:
            pending = [
                row for row in self._approvals
                if row["run_id"] == run_id
                and row["status"] == APPROVAL_STATUS_PENDING
                and (call_id is None or row["call_id"] == call_id)
            ]
            if call_id is not None and not pending:
                return []
            for row in pending:
                expires_at = row.get("expires_at")
                if _expired(expires_at, now):
                    row["status"] = "expired"
                    row["decided_at"] = decided_at or now.isoformat()
                    continue
                row["status"] = target_status
                row["decided_at"] = decided_at or now.isoformat()
                consumed.append(dict(row))
        return consumed

    def create_request_snapshot(
        self, snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        row = dict(snapshot)
        with self._lock:
            self._snapshots.setdefault(row["run_id"], []).append(row)
        return dict(row)

    def get_latest_request_snapshot(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            rows = self._snapshots.get(run_id) or []
            return dict(rows[-1]) if rows else None


def _expired(expires_at: Any, now: datetime) -> bool:
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now


def approval_is_expired(
    approval: Mapping[str, Any], now: datetime | None = None,
) -> bool:
    """True when a persisted approval or snapshot row is past its expires_at."""
    return _expired(approval.get("expires_at"), now or utc_now())


# ---------------------------------------------------------------------------
# Store resolution
# ---------------------------------------------------------------------------

_STORE_LOCK = threading.Lock()
_STORE: RunStore | None = None


def get_run_store() -> RunStore:
    """Return the persistent store, falling back to the in-memory one."""
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            return _STORE
        try:
            from app.db.run_store import get_run_store as factory
        except ImportError:
            logger.warning(
                "Persistent run store is unavailable; recovery falls back to memory"
            )
            _STORE = InMemoryRunStore()
        else:
            _STORE = factory()
    return _STORE


def set_run_store(store: RunStore | None) -> None:
    """Override the store (tests or explicit wiring); None restores lazy lookup."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store


# ---------------------------------------------------------------------------
# Live event bus and cancellation registry
# ---------------------------------------------------------------------------

_CLOSED = object()


class RunBus:
    """Fan out persisted live events to subscribers of one run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, run_id: str) -> tuple[asyncio.Queue, Any]:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.setdefault(run_id, set()).add(queue)

        def unsubscribe() -> None:
            with self._lock:
                subscribers = self._subscribers.get(run_id)
                if subscribers is None:
                    return
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(run_id, None)

        return queue, unsubscribe

    def publish(self, run_id: str, item: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(run_id, ()))
        for queue in subscribers:
            queue.put_nowait(item)

    def close(self, run_id: str) -> None:
        with self._lock:
            subscribers = list(self._subscribers.pop(run_id, ()))
        for queue in subscribers:
            queue.put_nowait(_CLOSED)


class RunControlRegistry:
    """Track the asyncio task driving each active run so cancel can reach it.

    ``cancel`` marks the run as user-cancelled before cancelling the task: the
    driver needs that flag to tell an explicit cancel apart from an event-loop
    shutdown, where the run must become ``interrupted`` (and resumable) rather
    than a user-visible cancellation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_requested: set[str] = set()
        self._resuming: dict[str, float] = {}

    def register(self, run_id: str, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks[run_id] = task

    def unregister(self, run_id: str, task: asyncio.Task | None = None) -> None:
        with self._lock:
            if task is None or self._tasks.get(run_id) is task:
                self._tasks.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            self._cancel_requested.add(run_id)
            task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def is_cancel_requested(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancel_requested

    def clear_cancel_requested(self, run_id: str) -> None:
        with self._lock:
            self._cancel_requested.discard(run_id)

    def has_live_task(self, run_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def begin_resume(self, run_id: str, *, ttl_seconds: float = 120.0) -> bool:
        """Claim the single resume slot for a run; False when already claimed.

        This is a burst guard for concurrent duplicate resumes; the
        authoritative one-shot protection is the atomic pending-approval
        consume plus the store-level status claim. The TTL only bounds a guard
        leaked by an early validation error.
        """
        now = time.monotonic()
        with self._lock:
            deadline = self._resuming.get(run_id)
            if deadline is not None and deadline > now:
                return False
            self._resuming[run_id] = now + max(float(ttl_seconds), 0.0)
            return True

    def end_resume(self, run_id: str) -> None:
        with self._lock:
            self._resuming.pop(run_id, None)

    def is_resuming(self, run_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            deadline = self._resuming.get(run_id)
            if deadline is None:
                return False
            if deadline <= now:
                self._resuming.pop(run_id, None)
                return False
            return True



_BUS: RunBus | None = None
_CONTROL: RunControlRegistry | None = None


def get_run_bus() -> RunBus:
    global _BUS
    if _BUS is None:
        _BUS = RunBus()
    return _BUS


def get_run_control() -> RunControlRegistry:
    global _CONTROL
    if _CONTROL is None:
        _CONTROL = RunControlRegistry()
    return _CONTROL


def reset_runtime_state() -> None:
    """Drop the bus/control singletons; intended for tests."""
    global _BUS, _CONTROL
    _BUS = None
    _CONTROL = None


# ---------------------------------------------------------------------------
# SSE framing
# ---------------------------------------------------------------------------

def build_run_event_frame(run_id: str, seq: int, payload: Mapping[str, Any]) -> str:
    # The wire id and the payload must agree. A legacy producer may already
    # carry its own seq/run_id; overwrite them with the persisted sequence.
    normalized = dict(payload)
    normalized["run_id"] = run_id
    normalized["seq"] = int(seq)
    body = json.dumps(normalized, ensure_ascii=False)
    return f"id: {run_id}:{seq}\ndata: {body}\n\n"


def _payload_with_type(payload: Mapping[str, Any], event_type: str | None) -> dict[str, Any]:
    normalized = dict(payload or {})
    if event_type and "type" not in normalized:
        normalized["type"] = event_type
    return normalized


def build_unpersisted_frame(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False)
    return f"data: {body}\n\n"


def split_sse_frames(chunk: str) -> list[str]:
    return [
        frame.strip()
        for frame in chunk.replace("\r\n", "\n").split("\n\n")
        if frame.strip()
    ]


def decode_sse_frame(frame: str) -> dict[str, Any] | None:
    data_lines = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
    if not data_lines:
        return None
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_last_event_id(value: str | None) -> tuple[str, int] | None:
    if not isinstance(value, str):
        return None
    run_id, separator, seq_text = value.rpartition(":")
    if not separator or not run_id:
        return None
    try:
        seq = int(seq_text)
    except ValueError:
        return None
    if seq < 0:
        return None
    return run_id, seq


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class RunEventWriter:
    """Persist semantic events and publish the same frames to live subscribers."""

    def __init__(
        self,
        store: RunStore | None,
        run_id: str,
        *,
        bus: RunBus | None = None,
        principal_id: str | None = None,
        session_id: str | None = None,
        request_snapshot: Mapping[str, Any] | None = None,
        snapshot_provider: Any = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.bus = bus
        self.principal_id = principal_id
        self.session_id = session_id
        self.request_snapshot = request_snapshot
        # Lazy provider for request-local evidence that only exists live (for
        # example the source registry). It is merged into the persisted request
        # snapshot so a restart can rebuild the registry without guessing.
        self.snapshot_provider = snapshot_provider
        self.last_seq = 0
        self._snapshot_written = False
        if store is not None:
            try:
                run = store.get_run(run_id)
            except Exception:
                logger.warning("Run state could not be read for %s", run_id, exc_info=True)
                run = None
            if run is not None:
                self.last_seq = int(run.get("last_event_seq") or 0)

    def append(
        self, event_type: str, payload: Mapping[str, Any],
    ) -> tuple[int, str] | None:
        """Persist one semantic event, then return its seq and SSE frame."""
        if self.store is None:
            return None
        event_payload = _payload_with_type(payload, event_type)
        try:
            seq = self.store.append_run_event(self.run_id, event_type, event_payload)
        except Exception:
            logger.warning(
                "Run event persistence failed run=%s type=%s",
                self.run_id, event_type, exc_info=True,
            )
            return None
        self.last_seq = int(seq)
        # Persist the approval cards / request snapshot before publishing, so a
        # client reacting to the confirmation event already sees the durable
        # rows it needs for a resume.
        if event_type == "tool_confirm_required":
            self._persist_pending_approvals(payload)
            self._persist_request_snapshot()
        if self.bus is not None:
            self.bus.publish(self.run_id, {
                "seq": self.last_seq,
                "type": event_type,
                "payload": dict(event_payload),
            })
        return self.last_seq, build_run_event_frame(self.run_id, self.last_seq, event_payload)

    def _persist_pending_approvals(self, payload: Mapping[str, Any]) -> None:
        if self.store is None:
            return
        calls = payload.get("calls")
        if not isinstance(calls, list):
            return
        expires_at = (utc_now() + timedelta(seconds=PENDING_APPROVAL_TTL_SECONDS)).isoformat()
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            call_id = call.get("call_id")
            tool_name = call.get("name")
            if not isinstance(call_id, str) or not call_id:
                logger.warning("HITL confirmation without call_id; approval not persisted")
                continue
            if not isinstance(tool_name, str) or not tool_name:
                continue
            try:
                args_json = json.dumps(
                    call.get("args") or {}, ensure_ascii=False, default=str,
                )
            except (TypeError, ValueError):
                args_json = "{}"
            approval = {
                "id": new_approval_id(),
                "run_id": self.run_id,
                "call_id": call_id,
                "tool_name": tool_name,
                "args_json": args_json,
                "risk_level": call.get("risk_level"),
                "status": APPROVAL_STATUS_PENDING,
                "created_at": utc_now_iso(),
                "expires_at": expires_at,
                "decided_at": None,
                "principal_id": self.principal_id,
            }
            try:
                self.store.create_pending_approval(approval)
            except Exception:
                logger.warning(
                    "Pending approval persistence failed run=%s", self.run_id, exc_info=True,
                )

    def _persist_request_snapshot(self) -> None:
        if self.store is None or self._snapshot_written or self.request_snapshot is None:
            return
        self._snapshot_written = True
        snapshot_payload: dict[str, Any] = dict(self.request_snapshot)
        if self.snapshot_provider is not None:
            try:
                extra = self.snapshot_provider()
            except Exception:
                logger.warning(
                    "Request snapshot provider failed run=%s", self.run_id, exc_info=True,
                )
                extra = None
            if isinstance(extra, Mapping):
                for key, value in extra.items():
                    snapshot_payload.setdefault(str(key), value)
        try:
            payload_json = json.dumps(
                snapshot_payload, ensure_ascii=False, default=str,
            )
        except (TypeError, ValueError):
            logger.warning("Request snapshot is not serializable; skipped", exc_info=True)
            return
        snapshot = {
            "id": new_snapshot_id(),
            "run_id": self.run_id,
            "session_id": self.session_id or "",
            "payload_json": payload_json,
            "created_at": utc_now_iso(),
            "expires_at": (
                utc_now() + timedelta(seconds=PENDING_APPROVAL_TTL_SECONDS)
            ).isoformat(),
        }
        try:
            self.store.create_request_snapshot(snapshot)
        except Exception:
            logger.warning(
                "Request snapshot persistence failed run=%s", self.run_id, exc_info=True,
            )


# ---------------------------------------------------------------------------
# Stream pump and server-owned runner
# ---------------------------------------------------------------------------

class RunEventPump:
    """Persist semantic events from one inner SSE iterator and publish frames.

    Two callers share this pump: the legacy inline wrapper used when no
    persistent store is available, and the server-owned task started by
    :func:`start_run_task`, which is the production path. In the second case an
    HTTP response is only a subscriber, so a client disconnect never stops the
    run.
    """

    def __init__(
        self,
        inner: AsyncIterator[str],
        *,
        store: RunStore | None,
        run_id: str,
        principal_id: str | None = None,
        session_id: str | None = None,
        request_snapshot: Mapping[str, Any] | None = None,
        snapshot_provider: Any = None,
        emit_started: bool = True,
    ) -> None:
        self.inner = inner
        self.store = store
        self.run_id = run_id
        self.emit_started = emit_started
        self.writer = RunEventWriter(
            store, run_id,
            bus=get_run_bus(),
            principal_id=principal_id,
            session_id=session_id,
            request_snapshot=request_snapshot,
            snapshot_provider=snapshot_provider,
        )
        self.control = get_run_control()
        self.open_tool_calls: dict[str, dict[str, Any]] = {}

    def _track(self, event_type: str | None, payload: Mapping[str, Any]) -> None:
        if event_type == TOOL_CALL_EVENT:
            call_id = payload.get("call_id")
            key = (
                call_id if isinstance(call_id, str)
                else f"__anonymous_{len(self.open_tool_calls)}"
            )
            self.open_tool_calls[key] = {
                "call_id": call_id if isinstance(call_id, str) else None,
                "tool": payload.get("tool"),
                "risk_level": payload.get("risk_level"),
                "decision_source": payload.get("decision_source"),
            }
        elif event_type == TOOL_RESULT_EVENT:
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                self.open_tool_calls.pop(call_id, None)

    def unresolved_tool_calls(self) -> list[dict[str, Any]]:
        return [dict(call) for call in self.open_tool_calls.values()]

    def _ledger_unresolved_tool_calls(self) -> list[dict[str, Any]] | None:
        """Pair calls across the whole run, including previous resume segments."""
        if self.store is None:
            return None
        try:
            events = self.store.list_run_events(self.run_id)
        except Exception:
            logger.warning(
                "Run ledger could not be read for the success guard run=%s",
                self.run_id, exc_info=True,
            )
            return None
        return summarize_run_events(events)["open_tool_calls"]

    def _guard_success_terminal(
        self, payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Never mark a run successful while a tool call has no result.

        A dispatch whose outcome is unknown (crash, broken upstream, or a tool
        that never returned a tool_result) must stay visibly unresolved. The
        returned error payload is persisted/yielded before the done event so
        sequence numbers stay contiguous.
        """
        if self.store is None:
            return dict(payload), None
        if payload.get("success") is not True or payload.get("awaiting_confirmation"):
            return dict(payload), None
        unresolved = self._ledger_unresolved_tool_calls()
        if unresolved is None:
            unresolved = self.unresolved_tool_calls()
        if not unresolved:
            return dict(payload), None
        error_payload = {
            "type": "error",
            "code": UNRESOLVED_SIDE_EFFECT_CODE,
            "message": "部分工具调用没有返回结果，不能把本次运行标记为成功；请人工核对副作用。",
            "unresolved_tool_calls": unresolved,
        }
        guarded = dict(payload)
        guarded["success"] = False
        guarded["completed"] = False
        guarded["unresolved_side_effects"] = unresolved
        return guarded, error_payload

    async def frames(self) -> AsyncIterator[str]:
        control = self.control
        task = asyncio.current_task()
        if task is not None:
            control.register(self.run_id, task)

        saw_done = False
        done_payload: dict[str, Any] = {}
        last_error: dict[str, Any] | None = None
        cancelled = False
        iterator = self.inner.__aiter__()
        exhausted = False
        terminal_written_in_exception = False

        try:
            if self.emit_started:
                started = {
                    "type": RUN_STARTED_EVENT,
                    "run_id": self.run_id,
                    "session_id": self.writer.session_id,
                    "seq": 1,
                }
                persisted = (
                    self.writer.append(RUN_STARTED_EVENT, started)
                    if self.store is not None else None
                )
                yield persisted[1] if persisted is not None else build_unpersisted_frame(started)

            while True:
                try:
                    chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    exhausted = True
                    break
                for frame_text in split_sse_frames(chunk):
                    payload = decode_sse_frame(frame_text)
                    if payload is None:
                        yield f"{frame_text}\n\n"
                        continue
                    event_type = payload.get("type")
                    if event_type in NON_PERSISTED_EVENT_TYPES:
                        yield build_unpersisted_frame(payload)
                        continue
                    if not isinstance(event_type, str) or not event_type:
                        # Untyped frames cannot be sequenced; pass them through.
                        yield build_unpersisted_frame(payload)
                        continue
                    self._track(event_type, payload)
                    if event_type == ERROR_EVENT:
                        last_error = payload
                    if event_type == DONE_EVENT:
                        payload, guard_error = self._guard_success_terminal(payload)
                        if guard_error is not None:
                            last_error = guard_error
                            persisted_guard = (
                                self.writer.append(ERROR_EVENT, guard_error)
                                if self.store is not None else None
                            )
                            if persisted_guard is not None:
                                yield persisted_guard[1]
                            else:
                                yield build_unpersisted_frame(guard_error)
                        saw_done = True
                        done_payload = payload
                    persisted = (
                        self.writer.append(event_type, payload)
                        if self.store is not None else None
                    )
                    yield persisted[1] if persisted is not None else build_unpersisted_frame(payload)
                    if event_type == DONE_EVENT:
                        break
                if saw_done:
                    break
        except asyncio.CancelledError:
            cancelled = True
            terminal_written_in_exception = True
            if not saw_done:
                if control.is_cancel_requested(self.run_id):
                    terminal = {
                        "type": DONE_EVENT, "success": False,
                        "completed": False, "cancelled": True,
                    }
                else:
                    # Event-loop shutdown or an unexpected cancel: the process
                    # owning this run is going away. Leave a resumable
                    # interrupted terminal instead of claiming a user cancel.
                    error_payload = {
                        "type": "error",
                        "code": INTERRUPTED_EVENT_CODE,
                        "message": "运行被意外中止，请重新连接或重试待确认操作。",
                    }
                    last_error = error_payload
                    if self.store is not None:
                        self.writer.append(ERROR_EVENT, error_payload)
                    terminal = {
                        "type": DONE_EVENT, "success": False,
                        "completed": False, "interrupted": True,
                    }
                if self.store is not None:
                    self.writer.append(DONE_EVENT, terminal)
                done_payload = terminal
                saw_done = True
            raise
        except GeneratorExit:
            cancelled = True
            terminal_written_in_exception = True
            if not saw_done:
                terminal = {
                    "type": DONE_EVENT, "success": False,
                    "completed": False, "cancelled": True,
                }
                if self.store is not None:
                    self.writer.append(DONE_EVENT, terminal)
                done_payload = terminal
                saw_done = True
            raise
        except Exception:
            logger.warning("Run stream failed run=%s", self.run_id, exc_info=True)
            terminal_written_in_exception = True
            if not saw_done:
                error_payload = {
                    "type": "error",
                    "code": "INTERNAL_ERROR",
                    "message": "运行意外结束，请重试。",
                }
                last_error = error_payload
                persisted_error = (
                    self.writer.append(ERROR_EVENT, error_payload)
                    if self.store is not None else None
                )
                if persisted_error is not None:
                    yield persisted_error[1]
                else:
                    yield build_unpersisted_frame(error_payload)
                terminal = {"type": DONE_EVENT, "success": False, "completed": False}
                persisted_done = (
                    self.writer.append(DONE_EVENT, terminal)
                    if self.store is not None else None
                )
                if persisted_done is not None:
                    yield persisted_done[1]
                else:
                    yield build_unpersisted_frame(terminal)
                done_payload = terminal
                saw_done = True
        finally:
            if not exhausted:
                close = getattr(self.inner, "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except BaseException:
                        logger.debug("Inner SSE generator cleanup failed", exc_info=True)
            status = _final_status(
                cancelled=cancelled,
                user_cancel=control.is_cancel_requested(self.run_id),
                saw_done=saw_done,
                done_payload=done_payload,
            )
            if self.store is not None:
                if not saw_done and not terminal_written_in_exception:
                    terminal = {"type": DONE_EVENT, "success": False, "completed": False}
                    try:
                        self.writer.append(DONE_EVENT, terminal)
                    except Exception:
                        logger.warning(
                            "Run terminal event could not be persisted run=%s",
                            self.run_id, exc_info=True,
                        )
                    done_payload = terminal
                    saw_done = True
                    status = _final_status(
                        cancelled=cancelled, user_cancel=False,
                        saw_done=True, done_payload=done_payload,
                    )
                error_code = None
                error_message = None
                if status == RUN_STATUS_INTERRUPTED:
                    error_code = INTERRUPTED_EVENT_CODE
                    if isinstance(last_error, Mapping):
                        error_message = last_error.get("message")
                elif status == RUN_STATUS_CANCELLED:
                    error_code = CANCELLED_EVENT_CODE
                elif status == RUN_STATUS_FAILED:
                    error_code = (
                        last_error.get("code") if isinstance(last_error, Mapping) else None
                    ) or "RUN_FAILED"
                    if isinstance(last_error, Mapping):
                        message = last_error.get("message")
                        error_message = message if isinstance(message, str) else None
                try:
                    self.store.update_run_status(
                        self.run_id, status,
                        error_code=error_code, error_message=error_message,
                    )
                except Exception:
                    logger.warning(
                        "Run status update failed run=%s", self.run_id, exc_info=True,
                    )
            get_run_bus().close(self.run_id)
            if task is not None:
                control.unregister(self.run_id, task)
            control.clear_cancel_requested(self.run_id)


async def stream_with_persistence(
    inner: AsyncIterator[str],
    *,
    store: RunStore | None,
    run_id: str,
    principal_id: str | None = None,
    session_id: str | None = None,
    request_snapshot: Mapping[str, Any] | None = None,
    snapshot_provider: Any = None,
    emit_started: bool = True,
) -> AsyncIterator[str]:
    """Inline wrapper kept for the degraded (no store) and legacy call sites."""
    pump = RunEventPump(
        inner,
        store=store,
        run_id=run_id,
        principal_id=principal_id,
        session_id=session_id,
        request_snapshot=request_snapshot,
        snapshot_provider=snapshot_provider,
        emit_started=emit_started,
    )
    async for frame in pump.frames():
        yield frame


async def _invoke_finished(on_finished: Any) -> None:
    if on_finished is None:
        return
    try:
        result = on_finished()
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.warning("Run completion callback failed", exc_info=True)


def _finalize_unstarted_run(
    store: RunStore | None,
    run_id: str,
    control: RunControlRegistry,
) -> None:
    """Finalize a run whose task was cancelled before its coroutine ran."""
    if store is None:
        get_run_bus().close(run_id)
        return
    user_cancel = control.is_cancel_requested(run_id)
    control.clear_cancel_requested(run_id)
    try:
        run = store.get_run(run_id)
        if run is not None and run.get("status") in ACTIVE_RUN_STATUSES:
            writer = RunEventWriter(store, run_id, bus=get_run_bus())
            if user_cancel:
                terminal = {
                    "type": DONE_EVENT, "success": False,
                    "completed": False, "cancelled": True,
                }
                writer.append(DONE_EVENT, terminal)
                store.update_run_status(
                    run_id, RUN_STATUS_CANCELLED, error_code=CANCELLED_EVENT_CODE,
                )
            else:
                error_payload = {
                    "type": "error",
                    "code": INTERRUPTED_EVENT_CODE,
                    "message": "运行被意外中止，请重新连接或重试待确认操作。",
                }
                writer.append(ERROR_EVENT, error_payload)
                terminal = {
                    "type": DONE_EVENT, "success": False,
                    "completed": False, "interrupted": True,
                }
                writer.append(DONE_EVENT, terminal)
                store.update_run_status(
                    run_id, RUN_STATUS_INTERRUPTED,
                    error_code=INTERRUPTED_EVENT_CODE,
                    error_message="run task stopped before start",
                )
    except Exception:
        logger.warning("Unstarted run finalization failed run=%s", run_id, exc_info=True)
    finally:
        get_run_bus().close(run_id)


def start_run_task(
    inner: AsyncIterator[str],
    *,
    store: RunStore | None,
    run_id: str,
    principal_id: str | None = None,
    session_id: str | None = None,
    request_snapshot: Mapping[str, Any] | None = None,
    snapshot_provider: Any = None,
    emit_started: bool = True,
    on_finished: Any = None,
) -> asyncio.Task:
    """Run the agent stream on a server-owned task, independent of HTTP.

    The returned task keeps consuming the inner iterator after the response
    that created it disconnects. Callers should serve the client from
    :func:`iterate_run_events`; the writer persists each event before
    publishing it, so replay and live subscription cannot diverge.
    """
    loop = asyncio.get_running_loop()
    control = get_run_control()
    control.clear_cancel_requested(run_id)
    pump = RunEventPump(
        inner,
        store=store,
        run_id=run_id,
        principal_id=principal_id,
        session_id=session_id,
        request_snapshot=request_snapshot,
        snapshot_provider=snapshot_provider,
        emit_started=emit_started,
    )
    state = {"started": False}

    async def driver() -> None:
        state["started"] = True
        try:
            async for _frame in pump.frames():
                pass
        except asyncio.CancelledError:
            raise
        except BaseException:
            logger.warning("Run task failed run=%s", run_id, exc_info=True)
        finally:
            await _invoke_finished(on_finished)

    task = loop.create_task(driver(), name=f"run:{run_id}")
    control.register(run_id, task)

    def _done(finished: asyncio.Task) -> None:
        control.unregister(run_id, finished)
        try:
            finished.exception()
        except BaseException:
            pass
        if state["started"]:
            return
        # Cancelled before the coroutine body ran, so the pump never finalized.
        _finalize_unstarted_run(store, run_id, control)
        if on_finished is not None:
            try:
                loop.create_task(_invoke_finished(on_finished))
            except RuntimeError:
                logger.warning(
                    "Run cleanup could not be scheduled run=%s", run_id, exc_info=True,
                )

    task.add_done_callback(_done)
    return task


def summarize_run_events(events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    """Summarize tool-call pairing and terminal state from a persisted ledger."""
    open_calls: dict[str, dict[str, Any]] = {}
    last_type: str | None = None
    for event in events:
        event_type = event.get("type")
        raw_payload = event.get("payload")
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        last_type = event_type if isinstance(event_type, str) else None
        if event_type == TOOL_CALL_EVENT:
            call_id = payload.get("call_id")
            key = (
                call_id if isinstance(call_id, str)
                else f"__anonymous_{len(open_calls)}"
            )
            open_calls[key] = {
                "call_id": call_id if isinstance(call_id, str) else None,
                "tool": payload.get("tool"),
                "risk_level": payload.get("risk_level"),
                "decision_source": payload.get("decision_source"),
            }
        elif event_type == TOOL_RESULT_EVENT:
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                open_calls.pop(call_id, None)
    return {
        "last_type": last_type,
        "has_terminal": last_type == DONE_EVENT,
        "open_tool_calls": list(open_calls.values()),
    }


def append_terminal_events(
    store: RunStore | None,
    run_id: str,
    *,
    interrupted: bool = False,
    cancelled: bool = False,
    error_message: str | None = None,
) -> bool:
    """Append a terminal event pair unless the ledger already ends with one."""
    if store is None:
        return False
    run = store.get_run(run_id)
    if run is None:
        return False
    last_seq = int(run.get("last_event_seq") or 0)
    tail = store.list_run_events(run_id, after_seq=max(last_seq - 1, 0))
    if tail and tail[-1].get("type") == DONE_EVENT and int(tail[-1].get("seq", -1)) == last_seq:
        return False
    writer = RunEventWriter(store, run_id, bus=get_run_bus())
    if not tail and int(run.get("last_event_seq") or 0) == 0:
        # Keep the "run_started is the first persisted event" invariant even
        # for a process that died before its first event.
        writer.append(RUN_STARTED_EVENT, {
            "type": RUN_STARTED_EVENT,
            "run_id": run_id,
            "session_id": run.get("session_id"),
            "seq": 1,
        })
    if interrupted:
        writer.append(ERROR_EVENT, {
            "type": "error",
            "code": INTERRUPTED_EVENT_CODE,
            "message": error_message or "运行在服务重启时被中断，请重新发送请求或重试待确认操作。",
        })
        writer.append(DONE_EVENT, {
            "type": DONE_EVENT, "success": False,
            "completed": False, "interrupted": True,
        })
    elif cancelled:
        writer.append(DONE_EVENT, {
            "type": DONE_EVENT, "success": False,
            "completed": False, "cancelled": True,
        })
    else:
        writer.append(DONE_EVENT, {
            "type": DONE_EVENT, "success": False, "completed": False,
        })
    return True


def append_approval_decisions(
    store: RunStore | None,
    run_id: str,
    approvals: list[Mapping[str, Any]],
    *,
    decision: str,
) -> None:
    """Record the one-shot approval decision in the run ledger."""
    if store is None or not approvals:
        return
    writer = RunEventWriter(store, run_id, bus=get_run_bus())
    for row in approvals:
        try:
            writer.append(APPROVAL_DECISION_EVENT, {
                "type": APPROVAL_DECISION_EVENT,
                "call_id": row.get("call_id"),
                "tool_name": row.get("tool_name"),
                "decision": decision,
                "status": row.get("status"),
                "decided_at": row.get("decided_at"),
            })
        except Exception:
            logger.warning(
                "Approval decision event failed run=%s", run_id, exc_info=True,
            )


def _final_status(
    *,
    cancelled: bool,
    user_cancel: bool,
    saw_done: bool,
    done_payload: Mapping[str, Any],
) -> str:
    if saw_done:
        if done_payload.get("awaiting_confirmation"):
            return RUN_STATUS_AWAITING_CONFIRMATION
        if done_payload.get("interrupted"):
            return RUN_STATUS_INTERRUPTED
        if done_payload.get("cancelled"):
            return RUN_STATUS_CANCELLED
        if done_payload.get("success") is False:
            return RUN_STATUS_FAILED
        return RUN_STATUS_COMPLETED
    if cancelled:
        return RUN_STATUS_CANCELLED if user_cancel else RUN_STATUS_INTERRUPTED
    return RUN_STATUS_FAILED


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

async def iterate_run_events(
    run_id: str,
    after_seq: int = 0,
    *,
    store: RunStore | None = None,
    bus: RunBus | None = None,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    """Replay persisted events, then follow live ones while the run is active."""
    store = store or get_run_store()
    bus = bus or get_run_bus()
    queue, unsubscribe = bus.subscribe(run_id)
    last_seq = max(int(after_seq), 0)
    last_type: str | None = None
    try:
        for event in store.list_run_events(run_id, after_seq=last_seq):
            seq = int(event["seq"])
            last_seq = seq
            last_type = event.get("type")
            yield build_run_event_frame(
                run_id, seq, _payload_with_type(event["payload"], last_type),
            )
            if last_type == "done":
                return

        run = store.get_run(run_id)
        if run is None:
            return
        status = run.get("status")
        if status not in ACTIVE_RUN_STATUSES:
            # Close the race with an append between the replay read and the
            # status read: a terminal event may have landed in between.
            for event in store.list_run_events(run_id, after_seq=last_seq):
                seq = int(event["seq"])
                last_seq = seq
                last_type = event.get("type")
                yield build_run_event_frame(
                    run_id, seq, _payload_with_type(event["payload"], last_type),
                )
            async for frame in _terminal_replay_frames(run_id, status, last_type):
                yield frame
            return

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                yield build_unpersisted_frame({"type": "heartbeat"})
                continue
            if item is _CLOSED:
                # The writer may have persisted a terminal event between our
                # last read and the bus close; drain it before ending replay.
                for event in store.list_run_events(run_id, after_seq=last_seq):
                    seq = int(event["seq"])
                    last_seq = seq
                    last_type = event.get("type")
                    yield build_run_event_frame(
                        run_id, seq, _payload_with_type(event["payload"], last_type),
                    )
                    if last_type == "done":
                        return
                run = store.get_run(run_id)
                status = run.get("status") if run is not None else None
                if status not in ACTIVE_RUN_STATUSES:
                    async for frame in _terminal_replay_frames(run_id, status, last_type):
                        yield frame
                return
            seq = int(item["seq"])
            if seq <= last_seq:
                continue
            if seq != last_seq + 1:
                logger.warning(
                    "Run event gap run=%s expected=%s got=%s", run_id, last_seq + 1, seq,
                )
                yield build_unpersisted_frame({
                    "type": "error",
                    "code": "EVENT_GAP",
                    "message": "事件流出现缺口，请重新连接。",
                })
                return
            last_seq = seq
            last_type = item.get("type")
            yield build_run_event_frame(
                run_id, seq, _payload_with_type(item["payload"], last_type),
            )
            if last_type == "done":
                return
    finally:
        unsubscribe()


async def _terminal_replay_frames(
    run_id: str, status: str | None, last_type: str | None,
) -> AsyncIterator[str]:
    if status == RUN_STATUS_INTERRUPTED:
        yield build_unpersisted_frame({
            "type": "error",
            "code": "INTERRUPTED",
            "message": "运行在服务重启时被中断，请重新发送请求或重试待确认操作。",
        })
        yield build_unpersisted_frame({
            "type": "done", "success": False, "completed": False, "interrupted": True,
        })
        return
    if last_type == "done":
        return
    if status == RUN_STATUS_COMPLETED:
        yield build_unpersisted_frame({
            "type": "done", "success": True, "completed": True, "run_id": run_id,
        })
    else:
        yield build_unpersisted_frame({
            "type": "done", "success": False, "completed": False,
            "cancelled": status == RUN_STATUS_CANCELLED,
            "run_id": run_id,
        })
