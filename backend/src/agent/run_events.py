"""Run-scoped event persistence, replay, and live fan-out for Recovery R1.

The runtime talks to a small store protocol instead of the ORM directly so the
storage layer (``app.db.run_store``) owns persistence and tests can inject a
fake. When that module is absent this file falls back to a process-local store;
recovery guarantees are only real with the persistent implementation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
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
    """Track the asyncio task driving each active run so cancel can reach it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, asyncio.Task] = {}

    def register(self, run_id: str, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks[run_id] = task

    def unregister(self, run_id: str, task: asyncio.Task | None = None) -> None:
        with self._lock:
            if task is None or self._tasks.get(run_id) is task:
                self._tasks.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
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
    body = json.dumps(payload, ensure_ascii=False)
    return f"id: {run_id}:{seq}\ndata: {body}\n\n"


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
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.bus = bus
        self.principal_id = principal_id
        self.session_id = session_id
        self.request_snapshot = request_snapshot
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
        try:
            seq = self.store.append_run_event(self.run_id, event_type, payload)
        except Exception:
            logger.warning(
                "Run event persistence failed run=%s type=%s",
                self.run_id, event_type, exc_info=True,
            )
            return None
        self.last_seq = int(seq)
        if self.bus is not None:
            self.bus.publish(self.run_id, {
                "seq": self.last_seq,
                "type": event_type,
                "payload": dict(payload),
            })
        if event_type == "tool_confirm_required":
            self._persist_pending_approvals(payload)
            self._persist_request_snapshot()
        return self.last_seq, build_run_event_frame(self.run_id, self.last_seq, payload)

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
        try:
            payload_json = json.dumps(
                self.request_snapshot, ensure_ascii=False, default=str,
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
# Stream wrapper
# ---------------------------------------------------------------------------

async def stream_with_persistence(
    inner: AsyncIterator[str],
    *,
    store: RunStore | None,
    run_id: str,
    principal_id: str | None = None,
    session_id: str | None = None,
    request_snapshot: Mapping[str, Any] | None = None,
    emit_started: bool = True,
) -> AsyncIterator[str]:
    """Wrap an SSE generator: persist semantic events before forwarding them.

    The wrapper owns the run lifecycle for the stream: ``run_started`` first,
    a terminal ``done`` for every exit path, and the final run status.
    """
    writer = RunEventWriter(
        store, run_id,
        bus=get_run_bus(),
        principal_id=principal_id,
        session_id=session_id,
        request_snapshot=request_snapshot,
    )
    control = get_run_control()
    task = asyncio.current_task()
    if task is not None:
        control.register(run_id, task)

    saw_done = False
    done_payload: dict[str, Any] = {}
    last_error: dict[str, Any] | None = None
    cancelled = False
    iterator = inner.__aiter__()
    exhausted = False

    try:
        if emit_started:
            started = {
                "type": "run_started",
                "run_id": run_id,
                "session_id": session_id,
                "seq": 1,
            }
            persisted = writer.append("run_started", started) if store is not None else None
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
                if event_type == "error":
                    last_error = payload
                if event_type == "done":
                    saw_done = True
                    done_payload = payload
                persisted = writer.append(event_type, payload) if store is not None else None
                yield persisted[1] if persisted is not None else build_unpersisted_frame(payload)
                if event_type == "done":
                    break
            if saw_done:
                break
    except asyncio.CancelledError:
        cancelled = True
        if not saw_done:
            terminal = {"type": "done", "success": False, "completed": False, "cancelled": True}
            writer.append("done", terminal)
            done_payload = terminal
            saw_done = True
        raise
    except GeneratorExit:
        cancelled = True
        if not saw_done:
            terminal = {"type": "done", "success": False, "completed": False, "cancelled": True}
            writer.append("done", terminal)
            done_payload = terminal
            saw_done = True
        raise
    except Exception:
        logger.warning("Run stream failed run=%s", run_id, exc_info=True)
        if not saw_done:
            error_payload = {
                "type": "error",
                "code": "INTERNAL_ERROR",
                "message": "运行意外结束，请重试。",
            }
            last_error = error_payload
            persisted_error = writer.append("error", error_payload) if store is not None else None
            if persisted_error is not None:
                yield persisted_error[1]
            else:
                yield build_unpersisted_frame(error_payload)
            terminal = {"type": "done", "success": False, "completed": False}
            persisted_done = writer.append("done", terminal) if store is not None else None
            if persisted_done is not None:
                yield persisted_done[1]
            else:
                yield build_unpersisted_frame(terminal)
            done_payload = terminal
            saw_done = True
    finally:
        if not exhausted:
            close = getattr(inner, "aclose", None)
            if close is not None:
                try:
                    await close()
                except BaseException:
                    logger.debug("Inner SSE generator cleanup failed", exc_info=True)
        if store is not None:
            status = _final_status(cancelled, saw_done, done_payload)
            error_code = None
            error_message = None
            if status in (RUN_STATUS_FAILED, RUN_STATUS_CANCELLED):
                error_code = (
                    last_error.get("code") if isinstance(last_error, Mapping) else None
                ) or ("CANCELLED" if status == RUN_STATUS_CANCELLED else "RUN_FAILED")
                if isinstance(last_error, Mapping):
                    message = last_error.get("message")
                    error_message = message if isinstance(message, str) else None
            try:
                store.update_run_status(
                    run_id, status,
                    error_code=error_code, error_message=error_message,
                )
            except Exception:
                logger.warning("Run status update failed run=%s", run_id, exc_info=True)
        get_run_bus().close(run_id)
        if task is not None:
            control.unregister(run_id, task)


def _final_status(
    cancelled: bool, saw_done: bool, done_payload: Mapping[str, Any],
) -> str:
    # A disconnect after the terminal event must not rewrite a finished run;
    # only the synthetic terminal written by the cancellation path is cancelled.
    if cancelled and done_payload.get("cancelled"):
        return RUN_STATUS_CANCELLED
    if saw_done:
        if done_payload.get("awaiting_confirmation"):
            return RUN_STATUS_AWAITING_CONFIRMATION
        if done_payload.get("success") is False:
            return RUN_STATUS_FAILED
        return RUN_STATUS_COMPLETED
    if cancelled:
        return RUN_STATUS_CANCELLED
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
            payload = event["payload"]
            last_seq = seq
            last_type = event.get("type")
            yield build_run_event_frame(run_id, seq, payload)
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
                yield build_run_event_frame(run_id, seq, event["payload"])
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
                    yield build_run_event_frame(run_id, seq, event["payload"])
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
            yield build_run_event_frame(run_id, seq, item["payload"])
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
