"""
LangGraph ReAct Agent stream → SSE event adapter.

Converts `agent.astream(stream_mode=["messages", "updates"])` output into the
project's SSE event protocol (text / tool_call / tool_result / error).

Internal helpers (parsing / state init / ToolMessage conversion) live in
src/agent/sse_helpers.py — this file owns the high-level stream loop and chunk
dispatch only.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.api.sse import sse_event
from src.agent.context_guard import _CUMULATIVE_TOKENS, accumulate_tokens, reset_token_counter
from src.agent.exceptions import AgentTimeoutError, ContextLimitError
from src.agent.sse_helpers import (
    check_active_tool_timeouts,
    convert_tool_message_to_sse,
    init_stream_state,
)
from src.agent.streaming_event_bus import register_queue, unregister_queue
from src.agent.core.toolkit.tool_router import list_registered_tools

logger = logging.getLogger(__name__)

# Cap how many tool_calls from a single chunk are surfaced as SSE events.
MAX_TOOL_CALLS_DISPLAY: int = 3


# ═══════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════

async def stream_agent_to_sse(
    agent: Any,
    events: Any,
    config: dict,
    call_counter: Counter,
    permission_mode: str = "bypassPermissions",
    model: str = "",
) -> AsyncIterator[str]:
    """Run the agent stream and yield SSE event strings.

    Hands off to hitl_handler after the stream drains for interrupt evaluation
    + auto-resume.
    """
    session_id = config.get("configurable", {}).get("thread_id", "?")
    logger.info(
        "stream_agent_to_sse start session=%s",
        session_id,
    )
    call_start_time: dict[str, float] = {}
    state: dict = init_stream_state(config, model)
    state["tool_event_queue"] = asyncio.Queue()
    state["permission_mode"] = permission_mode
    state["agent"] = agent
    state["config"] = config
    register_queue(session_id, state["tool_event_queue"])
    reset_token_counter()
    try:
        async for sse in _iter_agent_sse(agent, events, config, call_counter, call_start_time, state):
            yield sse
        from src.agent.hitl_handler import handle_auto_resume
        async for sse in handle_auto_resume(
            agent, config, permission_mode, call_counter, call_start_time, state,
        ):
            yield sse
    finally:
        unregister_queue(session_id)
    logger.info("stream_agent_to_sse done total_steps=%s", state.get("step_index", 0))


async def _iter_agent_sse(
    agent: Any, events: Any, config: dict,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
) -> AsyncIterator[str]:
    """Iterate agent.astream and yield converted SSE events.

    When a tool_event_queue is present in state (set by stream_agent_to_sse),
    tool intermediate events (compile_log / progress / thinking / heartbeat)
    are merged into the stream in real time.
    """
    tool_event_queue: asyncio.Queue | None = state.get("tool_event_queue")
    try:
        if tool_event_queue is not None:
            merged = _merge_agent_and_tool_events(agent, events, config, tool_event_queue)
            async for item in merged:
                check_active_tool_timeouts(call_start_time)
                if item["source"] == "agent":
                    sse = await _convert_chunk_to_sse(
                        item["mode"], item["chunk"], call_counter, call_start_time, state,
                    )
                else:
                    sse = _convert_tool_event_to_sse(item["event"])
                if sse:
                    yield sse
        else:
            async for mode, chunk in agent.astream(
                events, config=config, stream_mode=["messages", "updates"],
            ):
                check_active_tool_timeouts(call_start_time)
                sse = await _convert_chunk_to_sse(mode, chunk, call_counter, call_start_time, state)
                if sse:
                    yield sse
        final_text = state.pop("text_buffer", "")
        if final_text:
            yield sse_event("text", {"content": final_text})
    except ContextLimitError as exc:
        logger.warning("context_limit_exceeded tokens=%s limit=%s", exc.cumulative, exc.limit)
        raise
    except AgentTimeoutError as exc:
        logger.warning("agent_timeout elapsed=%.1fs limit=%ss", exc.elapsed, exc.limit)
        raise


# ═══════════════════════════════════════════
# Chunk dispatch
# ═══════════════════════════════════════════

async def _convert_chunk_to_sse(
    mode: str, chunk: Any,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
) -> str | None:
    """Dispatch a single stream chunk to the right handler by mode."""
    if mode == "messages":
        return await _handle_message_chunk(chunk, call_counter, call_start_time, state)
    if mode == "updates":
        return await _handle_update_chunk(chunk, call_start_time, call_counter, state)
    return None


# ═══════════════════════════════════════════
# stream_mode="messages" → AIMessageChunk
# ═══════════════════════════════════════════

async def _handle_message_chunk(
    chunk: Any, call_counter: Counter,
    call_start_time: dict[str, float], state: dict,
) -> str | None:
    """Handle a messages-mode chunk: (AIMessageChunk, metadata) tuple.

    Streams both reasoning_content (→ thinking event) and text content.
    tool_calls are intentionally ignored in messages mode (incremental chunks
    produce empty args); complete tool_calls are extracted in updates mode.
    """
    msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk
    if not isinstance(msg_chunk, AIMessageChunk):
        return None
    parts: list[str] = []
    _append_reasoning_event(parts, msg_chunk, state)
    _append_text_event(parts, msg_chunk, state)
    accumulate_tokens(msg_chunk.content, state)
    return "\n".join(parts) if parts else None


def _append_reasoning_event(parts: list[str], msg_chunk: AIMessageChunk, state: dict) -> None:
    """Append a thinking SSE event for incremental reasoning_content, if any."""
    reasoning = msg_chunk.additional_kwargs.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        parts.append(sse_event("thinking", {"content": reasoning, "source": "reasoning"}))


def _append_text_event(parts: list[str], msg_chunk: AIMessageChunk, state: dict) -> None:
    """Stream incremental text content as it arrives.

    ReAct Agent emits "guidance text" (e.g. "我来查询...") before tool calls.
    While a tool call is pending, that guidance is surfaced as a thinking event
    so the user can still see the model's explanation; once all pending tool
    calls finish, the final answer text is streamed live as text events.
    """
    content = msg_chunk.content
    if not isinstance(content, str) or not content:
        return
    if state.get("pending_tool_calls"):
        # Guidance text before/around tool calls — show as thinking, not text.
        state["text_buffer"] = ""
        parts.append(sse_event("thinking", {"content": content, "source": "reasoning"}))
        return
    parts.append(sse_event("text", {"content": content}))


# ═══════════════════════════════════════════
# stream_mode="updates" → node output (ToolMessage)
# ═══════════════════════════════════════════

async def _handle_update_chunk(
    chunk: Any, call_start_time: dict[str, float],
    call_counter: Counter, state: dict,
) -> str | None:
    """Handle an updates-mode chunk: {"node_name": {"messages": [Message]}}.

    Extracts both tool_call events (from complete AIMessage) and tool_result
    events (from ToolMessage). Updates mode gives us complete messages, so
    tool_calls have full args — unlike messages mode's incremental chunks.
    """
    if not isinstance(chunk, dict):
        return None
    parts: list[str] = []
    for node_output in chunk.values():
        _collect_node_events(parts, node_output, call_start_time, call_counter, state)
    await _maybe_compact(parts, state)
    return "\n".join(parts) if parts else None


def _collect_node_events(
    parts: list[str], node_output: Any,
    call_start_time: dict[str, float], call_counter: Counter, state: dict,
) -> None:
    """Extract tool_call (AIMessage) and tool_result (ToolMessage) events."""
    messages = node_output.get("messages", []) if isinstance(node_output, dict) else []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            _emit_tool_calls_from_message(parts, msg, call_counter, call_start_time, state)
        elif isinstance(msg, ToolMessage):
            _handle_tool_message(parts, msg, call_start_time, state)


def _handle_tool_message(
    parts: list[str], msg: ToolMessage,
    call_start_time: dict[str, float], state: dict,
) -> None:
    """Convert ToolMessage to SSE and flag compaction if over token limit."""
    sse = convert_tool_message_to_sse(msg, call_start_time, state)
    if sse:
        parts.append(sse)
    if _should_compact(state):
        state["needs_compact"] = True


def _emit_tool_calls_from_message(
    parts: list[str], msg: AIMessage,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
) -> None:
    """Emit tool_call SSE events from a complete AIMessage's tool_calls."""
    for tc in msg.tool_calls[:MAX_TOOL_CALLS_DISPLAY]:
        _emit_tool_call(parts, tc, call_counter, call_start_time, state)


def _emit_tool_call(
    parts: list[str], tc: dict,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
) -> None:
    """Emit one tool_call SSE event and record its start time + counter."""
    state["step_index"] += 1
    name = tc.get("name") or "unknown"
    call_id = tc.get("id") or f"call_{state['step_index']}"
    call_start_time[call_id] = time.time()
    call_counter[name] += 1
    call_entry = {"tool": name, "args": tc.get("args", {})}
    state.setdefault("call_history", []).append(call_entry)
    state.setdefault("pending_call_args", {})[call_id] = call_entry
    state.setdefault("pending_tool_calls", set()).add(call_id)
    logger.info("tool_call step=%s tool=%s call_id=%s", state['step_index'], name, call_id)
    risk_level = next(
        (s.risk_level.value for s in list_registered_tools() if s.name == name),
        None,
    )
    perm_mode = state.get("permission_mode", "bypassPermissions")
    decision_source = "mode_bypass" if perm_mode == "bypassPermissions" else "auto_allow"
    parts.append(sse_event("tool_call", {
        "tool": tc.get("name", ""),
        "args": tc.get("args", {}),
        "call_id": call_id,
        "step_index": state['step_index'],
        "timestamp": time.time(),
        "risk_level": risk_level,
        "decision_source": decision_source,
    }))


# ═══════════════════════════════════════════
# Context compaction checkpoint
# ═══════════════════════════════════════════

def _should_compact(state: dict) -> bool:
    """True when cumulative tokens reach the per-request limit."""
    token_limit = state.get("token_limit", 0)
    if token_limit <= 0:
        return False
    return _CUMULATIVE_TOKENS.get() >= token_limit


async def _maybe_compact(parts: list[str], state: dict) -> None:
    """Append context_compressing event and compact if flag is set.

    On compaction success the flag is cleared and the stream continues.
    On failure ContextLimitError is raised so the caller can degrade.
    """
    if not state.get("needs_compact"):
        return
    parts.append(sse_event("context_compressing", {"message": "正在压缩上下文..."}))
    if await _do_compact(state):
        state["needs_compact"] = False
        return
    raise ContextLimitError(
        cumulative=_CUMULATIVE_TOKENS.get(), limit=state.get("token_limit", 0),
    )


async def _do_compact(state: dict) -> bool:
    """Compact agent messages via autocompact. Returns True on success."""
    context_window = state.get("context_window")
    if not context_window:
        return False
    messages = _read_agent_messages(state)
    if not messages:
        return False
    return await _compact_and_apply(state, messages, context_window)


def _read_agent_messages(state: dict) -> list:
    """Read current messages from the agent's checkpointed state."""
    agent = state.get("agent")
    config = state.get("config")
    if agent is None or config is None:
        return []
    snapshot = agent.get_state(config)
    return list(snapshot.values.get("messages", []))


async def _compact_and_apply(
    state: dict, messages: list, context_window: int,
) -> bool:
    """Run autocompact, update agent state, reset token counter."""
    from src.agent.compact.autocompact import _make_summary_client, autocompact_messages
    llm_client = _make_summary_client()
    compacted = await autocompact_messages(messages, llm_client, context_window)
    # autocompact_messages returns the same list object when no compaction
    # happened (should_autocompact False or too few messages). A new list
    # means compaction ran — apply it even if message count is unchanged
    # (e.g., 11 msgs → 1 summary + 10 recent = 11, but tokens decreased).
    if compacted is messages:
        return False
    await _apply_compacted(state, compacted)
    return True


async def _apply_compacted(state: dict, compacted: list) -> None:
    """Update agent state with compacted messages and reset token counter."""
    agent = state.get("agent")
    config = state.get("config")
    await agent.update_state(config, {"messages": compacted})
    _reset_tokens_for_compacted(compacted)


def _reset_tokens_for_compacted(compacted: list) -> None:
    """Reset cumulative token counter to estimated size of compacted messages."""
    from src.llm.client import LLMClient
    total = sum(
        LLMClient._estimate_tokens(str(getattr(m, "content", "") or ""))
        for m in compacted
    )
    _CUMULATIVE_TOKENS.set(total)


# ═══════════════════════════════════════════
# Real-time tool event streaming
# ═══════════════════════════════════════════

async def _merge_agent_and_tool_events(
    agent: Any, events: Any, config: dict, queue: asyncio.Queue,
) -> AsyncIterator[dict[str, Any]]:
    """Merge LangGraph agent stream with intermediate tool events from queue.

    Runs two consumers concurrently and yields tagged dicts:
      {"source": "agent", "mode": mode, "chunk": chunk}
      {"source": "tool_event", "event": event}
    """
    outgoing: asyncio.Queue = asyncio.Queue()
    agent_iter = agent.astream(
        events, config=config, stream_mode=["messages", "updates"],
    ).__aiter__()
    tasks: list[asyncio.Task] = []

    async def _consume_agent() -> None:
        try:
            async for mode, chunk in agent_iter:
                await outgoing.put({"source": "agent", "mode": mode, "chunk": chunk})
        finally:
            # Sentinel tells the queue consumer that no more tool events will arrive.
            try:
                queue.put_nowait(None)
            except Exception:
                pass
            await outgoing.put({"source": "agent_done"})

    async def _consume_queue() -> None:
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                await outgoing.put({"source": "tool_event", "event": event})
        except asyncio.CancelledError:
            pass
        finally:
            await outgoing.put({"source": "queue_done"})

    try:
        tasks = [
            asyncio.create_task(_consume_agent()),
            asyncio.create_task(_consume_queue()),
        ]
        agent_done = False
        queue_done = False
        while not (agent_done and queue_done):
            item = await outgoing.get()
            source = item.get("source")
            if source == "agent_done":
                agent_done = True
            elif source == "queue_done":
                queue_done = True
            else:
                yield item
    finally:
        for task in tasks:
            if task.cancelled():
                # Cancelled tasks must skip the normal cleanup path — calling
                # task.exception() on a cancelled task raises CancelledError.
                continue
            if not task.done():
                task.cancel()
            else:
                # Propagate exceptions from background tasks so the caller
                # can surface an SSE error event instead of silently returning
                # a successful "done".
                exc = task.exception()
                if exc is not None:
                    raise exc


def _convert_tool_event_to_sse(event: dict[str, Any]) -> str | None:
    """Convert a pio-style tool event dict into an SSE event string."""
    etype = event.get("type")
    if etype == "compile_log":
        return sse_event("compile_log", {
            "line": event.get("line", ""),
            "stream": event.get("stream", "stdout"),
        })
    if etype == "progress":
        return sse_event("progress", {
            "percent": event.get("percent"),
            "message": event.get("message"),
        })
    if etype == "thinking":
        return sse_event("thinking", {
            "content": event.get("content", ""),
            "source": event.get("source", "build"),
        })
    if etype == "heartbeat":
        return sse_event("heartbeat", {
            "elapsed": event.get("elapsed"),
            "message": event.get("message"),
        })
    return None


# ═══════════════════════════════════════════
# Stage 3 preparation: stream_events v3 custom event consumer
# ═══════════════════════════════════════════

async def _consume_custom_events(
    agent: Any, events: Any, config: dict,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
) -> AsyncIterator[str]:
    """Consume stream_events v3 output (stage 3 path, NOT used in stage 2).

    Stage 2 keeps the active path as _iter_agent_sse (astream stream_mode
    queue merge). Stage 3 migrates to stream_events v3, which surfaces
    custom events (writer-pushed compile_log/progress) via on_custom_event.

    Prepared here so stage 3 Task 12 can swap _iter_agent_sse → this
    function without re-architecting the file. Dispatch logic for v3 event
    kinds (on_chat_model_stream / on_tool_start / on_tool_end / on_custom_event)
    will be filled in Task 12.
    """
    # Stage 3 will use: agent.stream_events(events, version="v3", config=config)
    # For now, raise NotImplementedError to guard against accidental use.
    raise NotImplementedError("stream_events v3 path is stage 3 Task 12 work")


# Backward-compat alias: original module-level name kept for legacy importers
# and the industrial-tool-runtime §Task 3 verification snippet.
_convert_tool_message_to_sse = convert_tool_message_to_sse
