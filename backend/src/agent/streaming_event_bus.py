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
from typing import Any

logger = logging.getLogger(__name__)

_queues: dict[str, asyncio.Queue] = {}


def register_queue(session_id: str, queue: asyncio.Queue) -> None:
    """Register an asyncio.Queue for the given session_id."""
    _queues[session_id] = queue


def get_queue(session_id: str) -> asyncio.Queue | None:
    """Return the registered queue for session_id, or None if absent."""
    return _queues.get(session_id)


def unregister_queue(session_id: str) -> None:
    """Remove the queue for session_id (idempotent)."""
    _queues.pop(session_id, None)


def emit_tool_event(session_id: str, event: dict[str, Any]) -> None:
    """Emit a tool event to the session's queue if one is registered.

    Uses put_nowait so tool execution never blocks waiting for the SSE layer.
    Drops the event silently when no queue is registered (e.g. standalone
    /api/tool calls or HITL resume paths that have not set up streaming).
    """
    queue = _queues.get(session_id)
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
