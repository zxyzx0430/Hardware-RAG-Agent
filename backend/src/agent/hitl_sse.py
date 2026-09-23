"""SSE event builders for HITL deny/stop paths.

Extracted from hitl_handler.py to keep that file under the 300-line cap
and to reuse the deny-result envelope shape elsewhere.
"""
from __future__ import annotations

import time
from typing import Any

from app.api.sse import sse_event

# Result envelope field keywords that mark a ToolMessage.content as structured.
_ENVELOPE_MARKERS: tuple[str, ...] = ("target_pane", "results", "success", "metadata")


def emit_deny_tool_results(pending: list[dict], call_start_time: dict[str, float], deny_message: str) -> list[str]:
    """Build tool_result SSE events for denied tool calls.

    create_agent's astream does not emit ToolMessages injected via
    aupdate_state in updates stream_mode, so we must send the frontend
    tool_result events explicitly to close pending tool cards.
    """
    events: list[str] = []
    now = time.time()
    for tc in pending:
        call_id = tc.get("call_id", "")
        start = call_start_time.pop(call_id, now)
        duration_ms = int((now - start) * 1000)
        envelope: dict[str, Any] = {
            "success": False,
            "output": deny_message,
            "data": {"denied": True, "args": tc.get("args", {})},
            "error": deny_message,
            "metadata": {
                "tool_name": tc.get("name", ""),
                "duration_ms": duration_ms,
                "call_id": call_id,
                "timestamp": now,
            },
        }
        events.append(sse_event("tool_result", {
            "call_id": call_id,
            "tool": tc.get("name", ""),
            "result": envelope,
            "duration": duration_ms,
            "success": False,
            "step_index": 0,
            "end_timestamp": now,
        }))
    return events


def emit_skipped_tool_results(skipped: list[dict], skip_message: str) -> list[str]:
    """Build tool_result SSE events for unchecked calls (Task 13).

    Same shape as deny events but carry status='skipped' so the frontend can
    render them distinctly from hard denials.
    """
    events: list[str] = []
    now = time.time()
    for tc in skipped:
        call_id = tc.get("call_id", "")
        envelope: dict[str, Any] = {
            "success": False,
            "output": skip_message,
            "data": {"skipped": True, "args": tc.get("args", {})},
            "error": skip_message,
            "metadata": {
                "tool_name": tc.get("name", ""),
                "duration_ms": 0,
                "call_id": call_id,
                "timestamp": now,
            },
        }
        events.append(sse_event("tool_result", {
            "call_id": call_id,
            "tool": tc.get("name", ""),
            "result": envelope,
            "duration": 0,
            "success": False,
            "status": "skipped",
            "step_index": 0,
            "end_timestamp": now,
        }))
    return events
