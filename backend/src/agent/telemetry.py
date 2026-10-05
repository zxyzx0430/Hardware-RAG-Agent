"""Privacy-safe timing records for one Agent request and its model calls."""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)
AGENT_INSTANCE_ID_ATTR = "_hardware_rag_agent_instance_id"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def emit_agent_timing(
    event: str,
    *,
    request_id: str | None = None,
    agent_instance_id: str | None = None,
    **fields: Any,
) -> None:
    """Write only explicitly selected, non-content timing fields as JSON."""
    record: dict[str, Any] = {"event": event, "at": utc_timestamp()}
    if request_id is not None:
        record["request_id"] = request_id
    if agent_instance_id is not None:
        record["agent_instance_id"] = agent_instance_id
    record.update({key: value for key, value in fields.items() if value is not None})
    try:
        logger.info("agent_timing %s", json.dumps(record, sort_keys=True, separators=(",", ":")))
    except Exception:
        # Instrumentation must not interrupt an Agent request if a log handler fails.
        logger.debug("agent_timing_emit_failed")


def new_agent_instance_id() -> str:
    return uuid4().hex


def get_agent_instance_id(agent: Any) -> str | None:
    value = getattr(agent, AGENT_INSTANCE_ID_ATTR, None)
    return value if isinstance(value, str) and value else None


@dataclass
class _ModelCall:
    call_index: int
    kind: str
    started_ns: int
    started_at: str
    first_output_ns: int | None = None
    first_output_source: str | None = None


@dataclass
class _ToolBatch:
    batch_index: int
    started_ns: int
    started_at: str
    pending_call_ids: set[str]
    completed_calls: int = 0


class AgentRequestTiming:
    """Collect per-request durations without retaining or logging payloads."""

    def __init__(self, agent_instance_id: str | None = None) -> None:
        self.request_id = uuid4().hex
        self.agent_instance_id = agent_instance_id
        self.started_ns = time.perf_counter_ns()
        self.started_at = utc_timestamp()
        self._lock = threading.RLock()
        self._model_calls: dict[Any, _ModelCall] = {}
        self._model_call_count = 0
        self._tool_batches: dict[int, _ToolBatch] = {}
        self._tool_call_batches: dict[str, int] = {}
        self._tool_batch_count = 0
        self._compaction_count = 0
        self._active_compactions: dict[int, tuple[int, str]] = {}
        self._first_visible_ns: int | None = None
        self._first_answer_ns: int | None = None
        self._answer_observed = False
        self._awaiting_confirmation = False
        self._model_observation_available = True
        self._finished = False
        self._emit("request_start", started_at=self.started_at)

    def _emit(self, event: str, **fields: Any) -> None:
        emit_agent_timing(
            event,
            request_id=self.request_id,
            agent_instance_id=self.agent_instance_id,
            **fields,
        )

    @property
    def awaiting_confirmation(self) -> bool:
        return self._awaiting_confirmation

    @property
    def answer_observed(self) -> bool:
        return self._answer_observed

    def completion_outcome(self) -> str:
        if self._awaiting_confirmation:
            return "awaiting_confirmation"
        return "completed" if self._answer_observed else "no_output"

    @staticmethod
    def _elapsed_ms(started_ns: int, ended_ns: int) -> float:
        return round(max(ended_ns - started_ns, 0) / 1_000_000, 3)

    def mark_model_observation_unavailable(self, error_type: str) -> None:
        self._model_observation_available = False
        self._emit("model_observation_unavailable", error_type=error_type)

    def begin_model_call(self, run_id: Any, kind: str = "agent") -> int | None:
        started_ns = time.perf_counter_ns()
        started_at = utc_timestamp()
        with self._lock:
            if self._finished or run_id in self._model_calls:
                return None
            self._model_call_count += 1
            call_index = self._model_call_count
            self._model_calls[run_id] = _ModelCall(call_index, kind, started_ns, started_at)
        self._emit(
            "model_call_start",
            model_call=call_index,
            kind=kind,
            started_at=started_at,
        )
        return call_index

    def first_model_output(self, run_id: Any, source: str = "token") -> None:
        observed_ns = time.perf_counter_ns()
        with self._lock:
            call = self._model_calls.get(run_id)
            if call is None or call.first_output_ns is not None:
                return
            call.first_output_ns = observed_ns
            call.first_output_source = source
            elapsed_ms = self._elapsed_ms(call.started_ns, observed_ns)
            call_index = call.call_index
        self._emit(
            "model_first_output",
            model_call=call_index,
            first_output_ms=elapsed_ms,
            source=source,
        )

    def end_model_call(self, run_id: Any, status: str) -> None:
        ended_ns = time.perf_counter_ns()
        ended_at = utc_timestamp()
        with self._lock:
            call = self._model_calls.pop(run_id, None)
        if call is None:
            return
        fields: dict[str, Any] = {
            "model_call": call.call_index,
            "kind": call.kind,
            "status": status,
            "started_at": call.started_at,
            "ended_at": ended_at,
            "duration_ms": self._elapsed_ms(call.started_ns, ended_ns),
            "first_output_observed": call.first_output_ns is not None,
        }
        if call.first_output_ns is not None:
            fields["first_output_ms"] = self._elapsed_ms(call.started_ns, call.first_output_ns)
            fields["first_output_source"] = call.first_output_source
        self._emit("model_call_end", **fields)

    def begin_tool_batch(self, call_ids: list[str]) -> int | None:
        ids = {
            call_id for call_id in call_ids
            if isinstance(call_id, str) and call_id
        }
        if not ids:
            return None
        started_ns = time.perf_counter_ns()
        started_at = utc_timestamp()
        with self._lock:
            if self._finished:
                return None
            self._tool_batch_count += 1
            batch_index = self._tool_batch_count
            batch = _ToolBatch(batch_index, started_ns, started_at, ids)
            self._tool_batches[batch_index] = batch
            for call_id in ids:
                self._tool_call_batches[call_id] = batch_index
        self._emit(
            "tool_batch_start",
            tool_batch=batch_index,
            tool_calls=len(ids),
            started_at=started_at,
            timing="wall_clock_until_last_result",
        )
        return batch_index

    def finish_tool_call(self, call_id: str | None) -> None:
        if not isinstance(call_id, str) or not call_id:
            return
        ended_ns = time.perf_counter_ns()
        ended_at = utc_timestamp()
        finished_batch: _ToolBatch | None = None
        with self._lock:
            batch_index = self._tool_call_batches.pop(call_id, None)
            if batch_index is None:
                return
            batch = self._tool_batches.get(batch_index)
            if batch is None or call_id not in batch.pending_call_ids:
                return
            batch.pending_call_ids.remove(call_id)
            batch.completed_calls += 1
            if not batch.pending_call_ids:
                finished_batch = self._tool_batches.pop(batch_index)
        if finished_batch is not None:
            self._emit(
                "tool_batch_end",
                tool_batch=finished_batch.batch_index,
                status="complete",
                tool_calls=len(finished_batch.pending_call_ids) + finished_batch.completed_calls,
                completed_calls=finished_batch.completed_calls,
                started_at=finished_batch.started_at,
                ended_at=ended_at,
                wall_clock_ms=self._elapsed_ms(finished_batch.started_ns, ended_ns),
                timing="wall_clock_until_last_result",
            )

    def begin_compaction(self) -> int | None:
        started_ns = time.perf_counter_ns()
        started_at = utc_timestamp()
        with self._lock:
            if self._finished:
                return None
            self._compaction_count += 1
            compaction_index = self._compaction_count
            self._active_compactions[compaction_index] = (started_ns, started_at)
        self._emit("compaction_start", compaction=compaction_index, started_at=started_at)
        return compaction_index

    def end_compaction(self, compaction_index: int | None, status: str) -> None:
        if compaction_index is None:
            return
        ended_ns = time.perf_counter_ns()
        ended_at = utc_timestamp()
        with self._lock:
            started = self._active_compactions.pop(compaction_index, None)
        if started is None:
            return
        started_ns, started_at = started
        self._emit(
            "compaction_end",
            compaction=compaction_index,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=self._elapsed_ms(started_ns, ended_ns),
        )

    def observe_sse(self, event: str) -> None:
        event_type: str | None = None
        nonempty = False
        for line in event.splitlines():
            if not line.startswith("data: "):
                continue
            try:
                payload = json.loads(line[6:])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            raw_type = payload.get("type")
            event_type = raw_type if isinstance(raw_type, str) else None
            content = payload.get("content")
            nonempty = bool(isinstance(content, str) and content.strip())
            if event_type == "tool_confirm_required":
                self._awaiting_confirmation = True
            if event_type == "text" and nonempty:
                with self._lock:
                    self._answer_observed = True
                    if self._first_answer_ns is None:
                        self._first_answer_ns = time.perf_counter_ns()
            break
        if event_type is None or event_type == "heartbeat" or not (nonempty or event_type in {
            "tool_call", "tool_result", "source", "progress", "tool_confirm_required",
        }):
            return
        observed_ns = time.perf_counter_ns()
        with self._lock:
            if self._first_visible_ns is not None:
                return
            self._first_visible_ns = observed_ns
        self._emit(
            "request_first_visible_output",
            first_visible_output_ms=self._elapsed_ms(self.started_ns, observed_ns),
            event_type=event_type,
        )

    def finish(self, outcome: str, error_type: str | None = None) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            pending_model_ids = list(self._model_calls)
            pending_batch_ids = list(self._tool_batches)
        unfinished_status = "cancelled" if outcome == "cancelled" else "incomplete"
        for run_id in pending_model_ids:
            self.end_model_call(run_id, unfinished_status)
        for batch_index in pending_batch_ids:
            self._finish_open_tool_batch(
                batch_index,
                "awaiting_confirmation" if self._awaiting_confirmation else unfinished_status,
            )
        ended_ns = time.perf_counter_ns()
        fields: dict[str, Any] = {
            "outcome": outcome,
            "started_at": self.started_at,
            "ended_at": utc_timestamp(),
            "duration_ms": self._elapsed_ms(self.started_ns, ended_ns),
            "answer_observed": self._answer_observed,
            "model_calls": self._model_call_count,
            "model_observation_available": self._model_observation_available,
            "tool_batches": self._tool_batch_count,
            "compactions": self._compaction_count,
        }
        if error_type is not None:
            fields["error_type"] = error_type
        if self._first_visible_ns is not None:
            fields["first_visible_output_ms"] = self._elapsed_ms(self.started_ns, self._first_visible_ns)
        if self._first_answer_ns is not None:
            fields["first_answer_text_ms"] = self._elapsed_ms(self.started_ns, self._first_answer_ns)
        self._emit("request_end", **fields)

    def _finish_open_tool_batch(self, batch_index: int, status: str) -> None:
        ended_ns = time.perf_counter_ns()
        ended_at = utc_timestamp()
        with self._lock:
            batch = self._tool_batches.pop(batch_index, None)
            if batch is None:
                return
            for call_id in batch.pending_call_ids:
                self._tool_call_batches.pop(call_id, None)
        self._emit(
            "tool_batch_end",
            tool_batch=batch_index,
            status=status,
            tool_calls=len(batch.pending_call_ids) + batch.completed_calls,
            completed_calls=batch.completed_calls,
            started_at=batch.started_at,
            ended_at=ended_at,
            wall_clock_ms=self._elapsed_ms(batch.started_ns, ended_ns),
            timing="wall_clock_until_last_result",
        )


class AgentTimingCallback(BaseCallbackHandler):
    """Observe model callback boundaries without reading message contents."""

    run_inline = True

    def __init__(self, timing: AgentRequestTiming) -> None:
        self.timing = timing

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: Any, **kwargs: Any) -> None:
        self.timing.begin_model_call(run_id)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        self.timing.begin_model_call(run_id)

    def on_llm_new_token(self, token: str, *, run_id: Any, **kwargs: Any) -> None:
        if token:
            self.timing.first_model_output(run_id)

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        self.timing.end_model_call(run_id, "complete")

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self.timing.end_model_call(run_id, "error")


_CURRENT_AGENT_TIMING: ContextVar[AgentRequestTiming | None] = ContextVar(
    "current_agent_request_timing", default=None,
)


@contextmanager
def use_agent_timing(timing: AgentRequestTiming | None) -> Iterator[None]:
    if timing is None:
        yield
        return
    token = _CURRENT_AGENT_TIMING.set(timing)
    try:
        yield
    finally:
        _CURRENT_AGENT_TIMING.reset(token)


def current_agent_timing() -> AgentRequestTiming | None:
    return _CURRENT_AGENT_TIMING.get()
