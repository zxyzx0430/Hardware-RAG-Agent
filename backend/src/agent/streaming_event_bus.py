"""Per-request streaming event bus for real-time tool feedback.

Allows long-running tools (e.g. build_firmware / flash_firmware) to publish
intermediate events (compile_log, progress, heartbeat) while they execute.
sse_adapter registers a queue per session_id and forwards those events as SSE.

This avoids coupling ToolSpec.execute signatures to the SSE layer while still
letting intermediate output escape LangGraph's ToolNode result boundary.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from typing import Any, Iterator

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class _QueueRegistration:
    queue: asyncio.Queue
    business_session_id: str | None


@dataclass(frozen=True)
class _RequestEventChannel:
    channel_id: str
    business_session_id: str
    queue: asyncio.Queue


_queues: dict[str, list[_QueueRegistration]] = {}
_queue_lock = RLock()
_ACTIVE_REQUEST_EVENT_CHANNEL: ContextVar[_RequestEventChannel | None] = ContextVar(
    "active_request_event_channel", default=None,
)


@contextmanager
def bind_event_channel(config: Any, queue: asyncio.Queue) -> Iterator[None]:
    """Pin producers to the exact owned queue, never a replacement stream."""
    from src.agent.request_context import request_graph_binding_from_config

    binding = request_graph_binding_from_config(config)
    channel = None
    if binding is not None:
        with _queue_lock:
            if not any(
                item.queue is queue and item.business_session_id == binding.business_session_id
                for item in _queues.get(binding.graph_thread_id, ())
            ):
                raise ValueError("Request event queue is not registered")
        channel = _RequestEventChannel(binding.graph_thread_id, binding.business_session_id, queue)
    token = _ACTIVE_REQUEST_EVENT_CHANNEL.set(channel)
    try:
        yield
    finally:
        _ACTIVE_REQUEST_EVENT_CHANNEL.reset(token)


def register_queue(
    session_id: str, queue: asyncio.Queue, *, business_session_id: str | None = None,
) -> None:
    """Register a legacy queue or one owned request stream, without takeover."""
    with _queue_lock:
        entries = _queues.setdefault(session_id, [])
        if any(item.business_session_id != business_session_id for item in entries):
            raise ValueError("Streaming channel business binding changed")
        if not any(item.queue is queue for item in entries):
            entries.append(_QueueRegistration(queue, business_session_id))


def get_queue(session_id: str) -> asyncio.Queue | None:
    """Return the registered queue for session_id, or None if absent."""
    with _queue_lock:
        entries = _queues.get(session_id, ())
        return entries[-1].queue if entries else None


def unregister_queue(session_id: str, queue: asyncio.Queue | None = None) -> None:
    """Remove only the owned queue; legacy calls cannot clear request queues."""
    with _queue_lock:
        entries = _queues.get(session_id, ())
        remaining = [
            item for item in entries
            if (item.queue is not queue if queue is not None else item.business_session_id is not None)
        ]
        if remaining:
            _queues[session_id] = remaining
        else:
            _queues.pop(session_id, None)


def emit_tool_event(session_id: str, event: dict[str, Any]) -> None:
    """Emit a tool event to the session's queue if one is registered.

    Uses put_nowait so tool execution never blocks waiting for the SSE layer.
    Drops the event silently when no queue is registered (e.g. standalone
    /api/tool calls or HITL resume paths that have not set up streaming).
    """
    channel = _ACTIVE_REQUEST_EVENT_CHANNEL.get()
    with _queue_lock:
        if channel is not None:
            if session_id != channel.business_session_id:
                return
            entries = _queues.get(channel.channel_id, ())
            queue = next((item.queue for item in entries if item.queue is channel.queue
                          and item.business_session_id == session_id), None)
        else:
            # Missing task context must not leak into any active request queue.
            if any(item.business_session_id == session_id for entries in _queues.values() for item in entries):
                return
            entries = _queues.get(session_id, ())
            queue = entries[-1].queue if entries and entries[-1].business_session_id is None else None
        if queue is None:
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("streaming event queue full session_id=%s", session_id)


def emit_build_log_via_stream_writer(writer: Any, event: dict[str, Any]) -> None:
    """Emit a tool event via StreamWriter (stage 3 stream_events v3 path).

    Stage 2 (Task 8): defined but NOT called by build_tool — which still
    uses emit_tool_event queue mode. Stage 3 migrates sse_adapter to
    stream_events v3, which surfaces these custom events via on_custom_event.

    Args:
        writer: StreamWriter instance (LangGraph injects when stream_mode
                includes "custom"). None → no-op (caller falls back to queue).
        event: tool event dict (type=compile_log/progress/thinking/heartbeat).
    """
    if writer is None:
        return
    try:
        # StreamWriter is callable: writer(value) pushes a custom event
        # that stream_events v3 surfaces as on_custom_event.
        writer(event)
    except Exception as exc:
        logger.debug("emit_build_log_via_stream_writer skipped: %s", exc)
