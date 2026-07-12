"""Internal helpers for sse_adapter: parsing, state init, ToolMessage → SSE
conversion. Extracted from sse_adapter.py to keep that file under the
300-line cap.

Two groups live here:
- Pure helpers (no `state` side effects): parse_tool_content, build_source_events, ...
- State-aware internals: init_stream_state, convert_tool_message_to_sse, ...
  — these take the per-request `state` dict explicitly and mutate it;
  sse_adapter calls them by name.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import ToolMessage

from app.api.sse import sse_event
from src.agent.context_guard import accumulate_tokens, check_tool_timeout, compute_token_limit
from src.agent.tools.groups.retrieval.search_docs import build_source_event_from_dict

logger = logging.getLogger(__name__)

# Placeholder step_index for tool_result events (v1 does not track precisely).
RESULT_STEP_INDEX: int = 0
# Structured-field keywords that mark a ToolMessage.content as JSON worth recovering.
_ENVELOPE_MARKERS: tuple[str, ...] = ("target_pane", "results", "success", "metadata")


# ═══════════════════════════════════════════
# Pure helpers (no state side effects)
# ═══════════════════════════════════════════

def parse_tool_content(msg: ToolMessage) -> tuple[dict, bool]:
    """Parse ToolMessage content (str | dict | other) into (result_dict, success)."""
    content = msg.content
    if isinstance(content, str):
        parsed = try_parse_structured_content(content)
        if parsed is not None:
            return parsed, bool(parsed.get("success", True))
        return {"output": content}, not (msg.status or "").startswith("error")
    if isinstance(content, dict):
        return content, bool(content.get("success", True))
    return {"output": str(content)}, True


def try_parse_structured_content(content: str) -> dict | None:
    """Recover a structured dict from JSON-serialized ToolMessage content.

    Recognises ToolResultEnvelope shape (success/output/error/metadata),
    workbench shape (target_pane) and search_docs shape (results). Returns
    None when content is plain text so callers wrap it as {"output": str}.
    """
    if not _has_structured_marker(content):
        return None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or not _has_structured_marker_dict(parsed):
        return None
    return parsed


def _has_structured_marker(content: str) -> bool:
    """True if the content string mentions any structured-field keyword."""
    return any(marker in content for marker in _ENVELOPE_MARKERS)


def _has_structured_marker_dict(parsed: dict) -> bool:
    """True if the parsed dict carries any structured-field key."""
    return any(marker in parsed for marker in _ENVELOPE_MARKERS)


def extract_output_text(result: dict) -> str:
    """Flatten tool result dict to a single string for hashing."""
    if not isinstance(result, dict):
        return str(result)
    return str(result.get("output") or result.get("error") or "")


def build_source_events(result: dict) -> list[str]:
    """Build source SSE events from a search_docs tool result.

    Prefers ToolResultEnvelope shape (result["data"]["results"]); falls back
    to legacy shape (result["results"]) for tools not yet migrated.
    Source IDs are taken from the result dict (assigned globally by
    SearchDocsTool via ToolContext.source_counter) so multiple search_docs
    calls in one request do not collide.
    """
    if not isinstance(result, dict):
        return []
    results = _extract_search_results(result)
    if not isinstance(results, list) or not results:
        return []
    events: list[str] = []
    for res in results:
        if not isinstance(res, dict):
            continue
        # result dict already carries the globally-allocated srcN id.
        idx = int(str(res.get("id", "src1")).replace("src", "") or "1") - 1
        events.append(sse_event("source", build_source_event_from_dict(res, idx)))
    return events


def _extract_search_results(result: dict) -> list[Any]:
    """Pull the results array out of either envelope or legacy shape."""
    data = result.get("data")
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    legacy = result.get("results")
    return legacy if isinstance(legacy, list) else []


def is_envelope_shape(result: dict) -> bool:
    """True when result already follows ToolResultEnvelope shape."""
    return isinstance(result, dict) and "success" in result and "metadata" in result


def wrap_legacy_result_as_envelope(
    result: dict, tool_name: str, duration_ms: int, call_id: str,
) -> dict:
    """Wrap a legacy tool result dict into ToolResultEnvelope shape.

    Used when the tool has not been migrated to ToolSpec yet. The original
    dict is preserved inside `data` so the frontend keeps working.
    """
    output = result.get("output") if isinstance(result, dict) else None
    if not output:
        output = str(result)
    return {
        "success": True,
        "output": str(output),
        "data": result,
        "error": None,
        "metadata": {
            "tool_name": tool_name,
            "duration_ms": duration_ms,
            "call_id": call_id,
            "timestamp": time.time(),
        },
    }


# ═══════════════════════════════════════════
# State-aware internals (take per-request `state` dict)
# ═══════════════════════════════════════════

def init_stream_state(config: dict, model: str) -> dict:
    """Build the per-request state dict shared by all SSE handlers."""
    session_id = config.get("configurable", {}).get("thread_id", "?")
    ctx_window = _load_session_context_window(session_id)
    return {
        "step_index": 0, "call_history": [], "session_id": session_id,
        "token_limit": compute_token_limit(model, ctx_window),
        "context_window": ctx_window, "text_buffer": "",
    }


def _load_session_context_window(session_id: str) -> int | None:
    """Read context_window from the session row, if the column exists."""
    if not session_id or session_id == "?":
        return None
    from app.db.database import SessionLocal
    from app.db.models import Session as SessionModel
    with SessionLocal() as db:
        row = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    return getattr(row, "context_window", None) if row else None


def check_active_tool_timeouts(call_start_time: dict[str, float]) -> None:
    """Wall-clock fallback: check all active tool calls for timeout.

    Spec agent-reliability-batch 实现风险 #1: if a tool hangs and never
    returns a ToolMessage, the per-call check in convert_tool_message_to_sse
    won't fire. This runs on every chunk to catch hung calls.
    """
    for call_id in list(call_start_time.keys()):
        check_tool_timeout(call_start_time, call_id)


def convert_tool_message_to_sse(
    msg: ToolMessage, call_start_time: dict[str, float], state: dict | None = None,
) -> str:
    """Convert a ToolMessage into a tool_result SSE event.

    - tool_result.result field carries ToolResultEnvelope shape (legacy
      results are wrapped on the fly by wrap_legacy_result_as_envelope).
    - Large binary payloads (e.g. image_generation base64) are kept in the
      SSE event sent to the frontend but stripped from msg.content so the
      LLM does not blow its context window on the next reasoning step.
    - When state is provided, accumulate token cost. state may be None for
      legacy callers — guard every access.
    """
    call_id = msg.tool_call_id or ""
    check_tool_timeout(call_start_time, call_id)
    start_time = call_start_time.pop(call_id, time.time())
    duration_ms = int((time.time() - start_time) * 1000)
    result, success = parse_tool_content(msg)
    tool_name = msg.name or ""
    logger.info("tool_result call_id=%s tool=%s duration=%sms", call_id, tool_name, duration_ms)
    if state is not None:
        accumulate_tokens(result, state)
        state.get("pending_tool_calls", set()).discard(call_id)
    envelope = _ensure_envelope_shape(result, tool_name, duration_ms, call_id)
    events = _assemble_tool_result_events(tool_name, call_id, duration_ms, success, envelope)
    # Compact the in-graph ToolMessage so subsequent LLM calls don't ingest
    # megabyte-sized base64 strings.
    _compact_tool_message_for_llm(msg, result, tool_name)
    return "\n".join(events)


def _ensure_envelope_shape(
    result: dict, tool_name: str, duration_ms: int, call_id: str,
) -> dict:
    """Return a ToolResultEnvelope-shaped dict, wrapping legacy results on the fly."""
    if is_envelope_shape(result):
        return result
    return wrap_legacy_result_as_envelope(result, tool_name, duration_ms, call_id)


# Threshold above which a base64 payload is replaced by a length marker in the
# ToolMessage content passed back to the LLM. The full payload is still streamed
# to the frontend via the tool_result SSE event.
_IMAGE_B64_COMPACT_THRESHOLD: int = 1000


def _compact_tool_message_for_llm(msg: ToolMessage, result: dict, tool_name: str) -> None:
    """Mutate msg.content to hide large binary payloads from the LLM context.

    LangGraph's ToolNode serialises the tool return value into ToolMessage.content
    and feeds it back to the model. For image_generation this can be >1MB of
    base64, causing context_length_exceeded on the next reasoning step. We keep
    the full payload in the SSE event (frontend already rendered the image) but
    replace it with a short marker inside the in-graph message.
    """
    if tool_name != "image_generation":
        return
    data = result.get("data") if isinstance(result.get("data"), dict) else None
    if not data:
        return
    b64 = data.get("image_base64")
    if not isinstance(b64, str) or len(b64) <= _IMAGE_B64_COMPACT_THRESHOLD:
        return
    compact = dict(result)
    compact_data = dict(data)
    compact_data["image_base64"] = f"<base64 image, {len(b64)} chars>"
    compact["data"] = compact_data
    try:
        msg.content = json.dumps(compact)
    except (TypeError, ValueError):
        logger.warning("image_generation tool_result compaction failed")


def _assemble_tool_result_events(
    tool_name: str, call_id: str, duration_ms: int, success: bool, envelope: dict,
) -> list[str]:
    """Emit source events (search_docs / web_search), optional todo_update, then tool_result."""
    events: list[str] = []
    if tool_name in ("search_docs", "web_search"):
        events.extend(build_source_events(envelope))
    _maybe_emit_todo_update(events, envelope)
    events.append(sse_event("tool_result", {
        "call_id": call_id,
        "tool": tool_name,
        "result": envelope,
        "duration": duration_ms,  # field name aligns with frontend (value in ms)
        "success": success,
        "step_index": RESULT_STEP_INDEX,
        "end_timestamp": time.time(),
    }))
    return events


def _maybe_emit_todo_update(events: list[str], envelope: dict) -> None:
    """If the tool result is from todo_write, emit a todo_update SSE event."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
    if data is None:
        data = envelope if "_todo_update" in envelope else {}
    if data.get("_todo_update"):
        todos = data.get("todos")
        if isinstance(todos, list):
            events.append(sse_event("todo_update", {"todos": todos}))
