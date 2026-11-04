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
import json
import logging
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.runnables.config import merge_configs

from app.api.sse import sse_event
from src.agent.context_guard import (
    _CUMULATIVE_TOKENS,
    ContextBudget,
    accumulate_tokens,
    estimate_context_tokens,
    reset_token_counter,
)
from src.agent.exceptions import (
    AgentStreamIdleTimeoutError,
    AgentTimeoutError,
    ContextLimitError,
)
from src.agent.sse_helpers import (
    check_active_tool_timeouts,
    convert_tool_message_to_sse,
    init_stream_state,
)
from src.agent.streaming_event_bus import register_queue, unregister_queue
from src.agent.core.toolkit.tool_router import list_registered_tools
from src.agent.telemetry import (
    AgentRequestTiming,
    AgentTimingCallback,
    get_agent_instance_id,
    use_agent_timing,
)

logger = logging.getLogger(__name__)

# Cap how many tool_calls from a single chunk are surfaced as SSE events.
MAX_TOOL_CALLS_DISPLAY: int = 3
AGENT_HEARTBEAT_INTERVAL = 15
AGENT_STREAM_IDLE_TIMEOUT_S = 300


@dataclass(frozen=True)
class _ActiveWriterBinding:
    binding: Any
    lease: Any
    model: str = ""


@dataclass
class _NativeExitJoinState:
    """Track LangGraph exit tasks proven at native iterator boundaries."""

    joined: bool = True
    tasks: set[asyncio.Task] = field(default_factory=set)


_active_writer_binding: ContextVar[_ActiveWriterBinding | None] = ContextVar(
    "sse_active_writer_binding", default=None,
)


@contextmanager
def activate_writer_binding(binding: Any, lease: Any, model: str = ""):
    """Expose the route's authoritative scope provider to every adapter run."""
    token = _active_writer_binding.set(_ActiveWriterBinding(binding, lease, model))
    try:
        yield
    finally:
        _active_writer_binding.reset(token)


def _revalidate_active_writer(
    active: _ActiveWriterBinding | None = None,
) -> Any | None:
    """Re-read the route's epoch authority and authenticate its lease."""
    active = active if active is not None else _active_writer_binding.get()
    if active is None:
        return None
    scope = active.binding.current_scope()
    active.binding.registry.assert_scope(active.lease, scope)
    return scope


def _quarantine_active_writer(
    active: _ActiveWriterBinding | None,
    reason: Any = None,
) -> None:
    """Fail closed when a native run or scope can no longer be proven safe."""
    if active is None:
        return
    from src.agent.compact.writer_lifecycle import QuarantineReason

    try:
        active.binding.registry.quarantine(
            active.lease,
            active.binding.scope,
            reason=reason or QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
    except BaseException:
        # The owner may already be quarantined or have a mismatching scope.
        pass


def _add_exception_note(error: BaseException, note: str) -> None:
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        try:
            add_note(note)
        except Exception:
            pass


def _mark_join_unconfirmed(
    join_state: _NativeExitJoinState,
    active: _ActiveWriterBinding | None,
    message: str,
    error: BaseException | None = None,
    primary: BaseException | None = None,
) -> None:
    join_state.joined = False
    from src.agent.compact.writer_lifecycle import QuarantineReason

    _quarantine_active_writer(active, QuarantineReason.JOIN_UNCONFIRMED)
    if error is None:
        logger.error(message)
    else:
        logger.error(
            message,
            exc_info=(type(error), error, error.__traceback__),
        )
    if primary is not None and primary is not error:
        _add_exception_note(primary, message)


async def _await_tasks_to_completion(
    tasks: list[asyncio.Task],
) -> tuple[list[Any] | None, asyncio.CancelledError | None]:
    """Join every task even if this owner receives another cancellation."""
    if not tasks:
        return [], None
    waiter = asyncio.gather(*tasks, return_exceptions=True)
    interrupted: asyncio.CancelledError | None = None
    while not waiter.done():
        try:
            await asyncio.shield(waiter)
        except asyncio.CancelledError as exc:
            if interrupted is None:
                interrupted = exc
        except BaseException:
            break
    if not waiter.done():
        return None, interrupted
    try:
        return waiter.result(), interrupted
    except BaseException as exc:
        return [exc], interrupted


async def _join_langgraph_exit_task(
    cancellation: asyncio.CancelledError,
    join_state: _NativeExitJoinState,
    active: _ActiveWriterBinding | None,
    *,
    source: str,
) -> None:
    """Join only the exit task appended by LangGraph's native __aexit__ shape.

    This is called only when CancelledError came directly from the native
    iterator's __anext__ or aclose. LangGraph appends its exit_task as the
    final exception argument; earlier arguments remain arbitrary cancellation
    messages and are deliberately ignored.
    """
    args = cancellation.args
    candidate = args[-1] if args else None
    current = asyncio.current_task()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if (
        not isinstance(candidate, asyncio.Task)
        or candidate is current
        or loop is None
        or candidate.get_loop() is not loop
    ):
        _mark_join_unconfirmed(
            join_state,
            active,
            f"Could not verify LangGraph exit task from native {source} cancellation",
            cancellation,
            cancellation,
        )
        return

    join_state.tasks.add(candidate)
    results, interrupted = await _await_tasks_to_completion([candidate])
    if interrupted is not None:
        _add_exception_note(
            cancellation,
            f"Owner received another cancellation while joining LangGraph exit task from {source}",
        )
    if results is None or not candidate.done():
        _mark_join_unconfirmed(
            join_state,
            active,
            f"LangGraph exit task from native {source} did not finish",
            cancellation,
            cancellation,
        )
        return
    exit_result = results[0] if results else None
    if isinstance(exit_result, BaseException):
        _mark_join_unconfirmed(
            join_state,
            active,
            f"LangGraph exit task from native {source} ended with an error",
            exit_result,
            cancellation,
        )


def _check_unverified_task_argument(
    cancellation: asyncio.CancelledError,
    join_state: _NativeExitJoinState,
    active: _ActiveWriterBinding | None,
    *,
    source: str,
    primary: BaseException | None = None,
) -> None:
    args = cancellation.args
    candidate = args[-1] if args else None
    if isinstance(candidate, asyncio.Task) and candidate not in join_state.tasks:
        _mark_join_unconfirmed(
            join_state,
            active,
            f"Cancellation from {source} carried a Task that was not proven as a LangGraph exit task",
            cancellation,
            primary,
        )


def _is_graph_drained(error: BaseException) -> bool:
    """Recognize only LangGraph's native cooperative-drain terminal error."""
    try:
        from langgraph.errors import GraphDrained
    except Exception:
        return False
    return isinstance(error, GraphDrained)


def _raise_context_limit(state: dict) -> None:
    raise ContextLimitError(
        cumulative=_CUMULATIVE_TOKENS.get(), limit=state.get("token_limit", 0),
    )


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
    requested_max_tokens: int | None = None,
    context_budget: ContextBudget | None = None,
) -> AsyncIterator[str]:
    """Run the agent stream and yield SSE event strings.

    Hands off to hitl_handler after the stream drains for interrupt evaluation
    + auto-resume.
    """
    session_id = config.get("configurable", {}).get("thread_id", "?")
    timing = AgentRequestTiming(get_agent_instance_id(agent))
    try:
        stream_config = merge_configs(config, {"callbacks": [AgentTimingCallback(timing)]})
    except Exception as exc:
        timing.mark_model_observation_unavailable(type(exc).__name__)
        stream_config = dict(config)
    call_start_time: dict[str, float] = {}
    state: dict = {}
    queue_registered = False
    outcome = "error"
    error_type: str | None = None
    try:
        state = init_stream_state(
            stream_config, model, requested_max_tokens, context_budget,
        )
        state["agent_timing"] = timing
        state["tool_event_queue"] = asyncio.Queue()
        state["permission_mode"] = permission_mode
        state["agent"] = agent
        state["config"] = config
        state["stream_config"] = stream_config
        register_queue(session_id, state["tool_event_queue"])
        queue_registered = True
        initial_tokens = estimate_context_tokens(events)
        if initial_tokens > state["token_limit"]:
            raise ContextLimitError(
                cumulative=initial_tokens, limit=state["token_limit"],
            )
        reset_token_counter(initial_tokens)
        agent_stream = _iter_agent_sse(
            agent, events, stream_config, call_counter, call_start_time, state,
        )
        try:
            async for sse in agent_stream:
                _revalidate_active_writer()
                timing.observe_sse(sse)
                yield sse
                _revalidate_active_writer()
        finally:
            await agent_stream.aclose()
            _revalidate_active_writer()
        from src.agent.hitl_handler import handle_auto_resume
        resume_stream = handle_auto_resume(
            agent, stream_config, permission_mode, call_counter, call_start_time, state,
        )
        try:
            async for sse in resume_stream:
                _revalidate_active_writer()
                timing.observe_sse(sse)
                yield sse
                _revalidate_active_writer()
        finally:
            await resume_stream.aclose()
            _revalidate_active_writer()
        outcome = timing.completion_outcome()
    except (asyncio.CancelledError, GeneratorExit):
        outcome = "cancelled"
        raise
    except BaseException as exc:
        outcome = "error"
        error_type = type(exc).__name__
        raise
    finally:
        if queue_registered:
            unregister_queue(session_id)
        timing.finish(outcome, error_type=error_type)
    logger.info("stream_agent_to_sse done total_steps=%s", state.get("step_index", 0))


async def _iter_agent_sse(
    agent: Any, events: Any, config: dict,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
) -> AsyncIterator[str]:
    """Iterate one native run, compact only after a confirmed graph drain.

    The continuation after checkpoint publication uses its exact execution
    config and one ``input=None`` call. HITL callers reach this wrapper too,
    because they invoke it directly for each Command continuation.
    """
    state.setdefault("agent", agent)
    state.setdefault("config", config)
    state.setdefault("stream_config", config)
    if (
        not isinstance(state.get("context_budget"), ContextBudget)
        or state.get("token_limit", 0) <= 0
    ):
        _raise_context_limit(state)
    run_events = events
    run_config = config
    while True:
        try:
            async for sse in _iterate_agent_sse_once(
                agent, run_events, run_config, call_counter, call_start_time, state,
            ):
                _revalidate_active_writer()
                yield sse
                _revalidate_active_writer()
            return
        except BaseException as exc:
            if not _is_graph_drained(exc):
                raise
            _revalidate_active_writer()
            handle = state.get("last_writer_run_handle")
            if not state.get("needs_compact") or not state.get("compaction_drain_requested"):
                raise
            if (
                handle is None
                or not handle.joined
                or not handle.drain_acknowledged
                or handle.drain_reason != "context_compaction"
            ):
                _raise_context_limit(state)
            if not await _do_compact(state):
                _raise_context_limit(state)
            _revalidate_active_writer()
            execution_config = state.pop("continuation_execution_config", None)
            if not _is_exact_execution_config(execution_config, agent):
                _raise_context_limit(state)
            state["needs_compact"] = False
            state["compaction_drain_requested"] = False
            run_events = None
            # Keep this exact publisher object through begin_run and astream;
            # the lifecycle validates its checkpoint tuple on the new run.
            run_config = execution_config


async def _iterate_agent_sse_once(
    agent: Any, events: Any, config: dict,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
) -> AsyncIterator[str]:
    """Run one LangGraph iterator and confirm its join before lifecycle ack."""
    from langgraph.runtime import RunControl
    from src.agent.compact.writer_lifecycle import RunOutcome

    active = _active_writer_binding.get()
    scope = _revalidate_active_writer(active)
    native_control = RunControl()
    run_handle = None
    if active is not None:
        run_handle = active.binding.registry.begin_run(
            active.lease, scope, native_control, execution_config=config,
        )
    state["active_writer_run_handle"] = run_handle
    state["last_writer_run_handle"] = None

    tool_event_queue: asyncio.Queue | None = state.get("tool_event_queue")
    native_exit_join_state = _NativeExitJoinState()

    async def _direct_agent_items():
        _revalidate_active_writer(active)
        native_stream = agent.astream(
            events, config=config, stream_mode=["messages", "updates"],
            control=native_control,
        )
        native_iterator = native_stream.__aiter__()
        primary_error: BaseException | None = None
        try:
            while True:
                try:
                    mode, chunk = await native_iterator.__anext__()
                except StopAsyncIteration:
                    break
                except asyncio.CancelledError as exc:
                    await _join_langgraph_exit_task(
                        exc, native_exit_join_state, active, source="__anext__",
                    )
                    raise
                _revalidate_active_writer(active)
                yield {"source": "agent", "mode": mode, "chunk": chunk}
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            closer = getattr(native_iterator, "aclose", None)
            if closer is None:
                closer = getattr(native_stream, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except asyncio.CancelledError as exc:
                    await _join_langgraph_exit_task(
                        exc, native_exit_join_state, active, source="aclose",
                    )
                    _mark_join_unconfirmed(
                        native_exit_join_state,
                        active,
                        "Native agent stream close raised cancellation",
                        exc,
                        primary_error,
                    )
                    if primary_error is None or not isinstance(
                        primary_error, (asyncio.CancelledError, GeneratorExit),
                    ):
                        raise
                except BaseException as exc:
                    _mark_join_unconfirmed(
                        native_exit_join_state,
                        active,
                        "Native agent stream close failed",
                        exc,
                        primary_error,
                    )
                    if primary_error is None:
                        raise
                else:
                    _revalidate_active_writer(active)

    source = (
        _merge_agent_and_tool_events(
            agent,
            events,
            config,
            tool_event_queue,
            control=native_control,
            exit_join_state=native_exit_join_state,
            active_writer=active,
        )
        if tool_event_queue is not None
        else _direct_agent_items()
    )
    iterator = source.__aiter__()
    last_progress_at = time.monotonic()
    next_item_task: asyncio.Task | None = None
    had_error = False
    run_error: BaseException | None = None
    graph_terminal_error: BaseException | None = None
    outcome_unknown = False
    joined = True
    cleanup_error: BaseException | None = None
    try:
        while True:
            if next_item_task is None:
                next_item_task = asyncio.create_task(iterator.__anext__())

            active_tool_call = bool(call_start_time)
            if active_tool_call:
                wait_seconds = AGENT_HEARTBEAT_INTERVAL
            else:
                elapsed = time.monotonic() - last_progress_at
                remaining = AGENT_STREAM_IDLE_TIMEOUT_S - elapsed
                if remaining <= 0:
                    raise AgentStreamIdleTimeoutError(elapsed, AGENT_STREAM_IDLE_TIMEOUT_S)
                wait_seconds = min(AGENT_HEARTBEAT_INTERVAL, remaining)

            ready, _ = await asyncio.wait({next_item_task}, timeout=wait_seconds)
            _revalidate_active_writer(active)
            if not ready:
                check_active_tool_timeouts(call_start_time)
                if not call_start_time:
                    elapsed = time.monotonic() - last_progress_at
                    if elapsed >= AGENT_STREAM_IDLE_TIMEOUT_S:
                        raise AgentStreamIdleTimeoutError(elapsed, AGENT_STREAM_IDLE_TIMEOUT_S)
                continue

            try:
                item = next_item_task.result()
            except StopAsyncIteration:
                break
            except BaseException as exc:
                graph_terminal_error = exc
                raise
            next_item_task = None
            check_active_tool_timeouts(call_start_time)

            if item["source"] == "agent":
                sse = await _convert_chunk_to_sse(
                    item["mode"], item["chunk"], call_counter, call_start_time, state,
                )
                _revalidate_active_writer(active)
            else:
                sse = _convert_tool_event_to_sse(item["event"])
            if sse:
                if _sse_indicates_progress(sse):
                    last_progress_at = time.monotonic()
                yield sse
                _revalidate_active_writer(active)

        final_text = state.pop("text_buffer", "")
        if final_text:
            yield sse_event("text", {"content": final_text})
            _revalidate_active_writer(active)
    except ContextLimitError as exc:
        had_error = True
        run_error = exc
        outcome_unknown = True
        logger.warning("context_limit_exceeded tokens=%s limit=%s", exc.cumulative, exc.limit)
        # Let the chat route emit the stable CONTEXT_LIMIT error and failed
        # terminal event; do not continue or retry the Agent stream here.
        raise
    except AgentTimeoutError as exc:
        had_error = True
        run_error = exc
        outcome_unknown = True
        logger.warning("agent_timeout elapsed=%.1fs limit=%ss", exc.elapsed, exc.limit)
        raise
    except BaseException as exc:
        had_error = True
        run_error = exc
        outcome_unknown = (
            graph_terminal_error is not exc
            or isinstance(exc, (asyncio.CancelledError, GeneratorExit))
        )
        raise
    finally:
        if next_item_task is not None:
            try:
                if not next_item_task.done():
                    next_item_task.cancel()
                results, interrupted = await _await_tasks_to_completion(
                    [next_item_task],
                )
                if results is None or not next_item_task.done():
                    joined = False
                    outcome_unknown = True
                    _mark_join_unconfirmed(
                        native_exit_join_state,
                        active,
                        "Adapter next-item task did not finish during shutdown",
                    )
                else:
                    task_result = results[0] if results else None
                    if (
                        isinstance(task_result, BaseException)
                        and not isinstance(
                            task_result,
                            (asyncio.CancelledError, StopAsyncIteration),
                        )
                        and task_result is not run_error
                    ):
                        joined = False
                        outcome_unknown = True
                        _mark_join_unconfirmed(
                            native_exit_join_state,
                            active,
                            "Adapter next-item task ended with a cleanup error",
                            task_result,
                            run_error,
                        )
                        if run_error is None:
                            cleanup_error = task_result
                            run_error = task_result
                    elif isinstance(task_result, asyncio.CancelledError):
                        _check_unverified_task_argument(
                            task_result,
                            native_exit_join_state,
                            active,
                            source="adapter next-item task",
                            primary=run_error,
                        )
                    if interrupted is not None:
                        _check_unverified_task_argument(
                            interrupted,
                            native_exit_join_state,
                            active,
                            source="adapter task join",
                            primary=run_error,
                        )
                        outcome_unknown = True
                        if run_error is None or not isinstance(
                            run_error, (asyncio.CancelledError, GeneratorExit),
                        ):
                            cleanup_error = interrupted
                            run_error = interrupted
                        else:
                            _add_exception_note(
                                run_error,
                                "Owner received another cancellation while joining adapter next-item task",
                            )
                _revalidate_active_writer(active)
            except BaseException as exc:
                joined = False
                outcome_unknown = True
                _mark_join_unconfirmed(
                    native_exit_join_state,
                    active,
                    "Adapter next-item task join failed",
                    exc,
                    run_error,
                )
                if run_error is None:
                    cleanup_error = exc
                    run_error = exc
        closer = getattr(iterator, "aclose", None)
        if closer is not None:
            try:
                await closer()
                _revalidate_active_writer(active)
            except BaseException as exc:
                joined = False
                outcome_unknown = True
                _mark_join_unconfirmed(
                    native_exit_join_state,
                    active,
                    "Adapter source iterator close failed",
                    exc,
                    run_error,
                )
                if run_error is None or (
                    isinstance(exc, asyncio.CancelledError)
                    and not isinstance(run_error, (asyncio.CancelledError, GeneratorExit))
                ):
                    cleanup_error = exc
                    run_error = exc
        unfinished_exit_tasks = [
            task for task in native_exit_join_state.tasks if not task.done()
        ]
        if unfinished_exit_tasks:
            joined = False
            outcome_unknown = True
            _mark_join_unconfirmed(
                native_exit_join_state,
                active,
                "A proven LangGraph exit task remained unfinished after iterator shutdown",
            )
        if not native_exit_join_state.joined:
            joined = False
            outcome_unknown = True
        if active is not None and run_handle is not None:
            try:
                scope = _revalidate_active_writer(active)
                if not joined:
                    from src.agent.compact.writer_lifecycle import QuarantineReason

                    _quarantine_active_writer(active, QuarantineReason.JOIN_UNCONFIRMED)
                    outcome_unknown = True
                elif outcome_unknown:
                    from src.agent.compact.writer_lifecycle import QuarantineReason

                    _quarantine_active_writer(active, QuarantineReason.RUN_OUTCOME_UNKNOWN)
                else:
                    if (
                        run_error is not None
                        and _is_graph_drained(run_error)
                        and state.get("compaction_drain_requested")
                        and run_handle.drain_reason == "context_compaction"
                    ):
                        active.binding.registry.acknowledge_graph_drained(
                            active.lease, scope, run_handle, run_error,
                        )
                        _revalidate_active_writer(active)
                    active.binding.registry.finish_run(
                        active.lease, scope, run_handle,
                        joined=True, outcome=RunOutcome.KNOWN_TERMINAL,
                    )
                    _revalidate_active_writer(active)
                state["last_writer_run_handle"] = run_handle
            except BaseException:
                _quarantine_active_writer(active)
                if run_error is None:
                    raise
        state["active_writer_run_handle"] = None
        if cleanup_error is not None and (
            not had_error
            or isinstance(cleanup_error, (asyncio.CancelledError, GeneratorExit))
        ):
            raise cleanup_error


def _is_exact_execution_config(config: Any, agent: Any) -> bool:
    """Require the publisher's exact nonempty resumable config for continuation."""
    if type(config) is not dict or not config:
        return False
    configurable = config.get("configurable")
    if type(configurable) is not dict:
        return False
    active = _active_writer_binding.get()
    if active is None:
        return False
    scope = _revalidate_active_writer(active)
    return (
        configurable.get("thread_id") == scope.owner_key
        and configurable.get("checkpoint_ns", "") == ""
        and type(configurable.get("checkpoint_id")) is str
        and bool(configurable.get("checkpoint_id"))
        and scope.graph is agent
    )
# ═══════════════════════════════════════════
# Chunk dispatch
# ═══════════════════════════════════════════

def _sse_indicates_progress(serialized_events: str) -> bool:
    """Only visible answer/reasoning or useful tool/status events renew idle time."""
    for line in serialized_events.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        event_type = payload.get("type")
        if event_type in {"text", "thinking", "compile_log"}:
            value = payload.get("content") if event_type != "compile_log" else payload.get("line")
            if isinstance(value, str) and value.strip():
                return True
        elif event_type == "progress":
            if payload.get("percent") is not None or (isinstance(payload.get("message"), str) and payload["message"].strip()):
                return True
        elif event_type in {"tool_call", "tool_result"}:
            if payload.get("call_id") or payload.get("tool"):
                return True
        elif event_type == "source":
            if any(isinstance(payload.get(key), str) and payload[key].strip() for key in ("id", "title", "doc", "excerpt")):
                return True
        elif event_type == "todo_update":
            if payload.get("todos"):
                return True
        elif event_type in {"context_compressing", "tool_confirm_required"}:
            if payload.get("message") or payload.get("calls") or payload.get("count"):
                return True
    return False

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
    timing = state.get("agent_timing")
    if timing is not None:
        timing.finish_tool_call(msg.tool_call_id)
    if _should_compact(state):
        state["needs_compact"] = True


def _emit_tool_calls_from_message(
    parts: list[str], msg: AIMessage,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
) -> None:
    """Emit tool_call SSE events from a complete AIMessage's tool_calls."""
    if state.get("token_limit", 0) > 0:
        # messages-mode carries incremental tool-call chunks too; count only
        # this complete normalized representation from updates mode.
        tool_call_tokens = estimate_context_tokens([{
            "content": "",
            "tool_calls": msg.tool_calls,
        }])
        _CUMULATIVE_TOKENS.set(_CUMULATIVE_TOKENS.get() + tool_call_tokens)
    timing = state.get("agent_timing")
    if timing is not None:
        timing.begin_tool_batch([tc.get("id") for tc in msg.tool_calls])
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
    from src.agent.request_context import active_tool_context
    ctx = active_tool_context()
    specs = ctx.request_tools.values() if ctx is not None else list_registered_tools()
    spec = next((item for item in specs if item.name == name), None)
    risk_level = spec.risk_level.value if spec is not None else None
    perm_mode = state.get("permission_mode", "bypassPermissions")
    decision_source = "mode_bypass" if perm_mode == "bypassPermissions" else "auto_allow"
    if spec is not None and spec.mcp_info is not None:
        decision_source = "confirmation_required"
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
    """Request a native superstep drain before checkpoint compaction."""
    if not state.get("needs_compact"):
        return
    active = _active_writer_binding.get()
    run_handle = state.get("active_writer_run_handle")
    if active is None or run_handle is None:
        _raise_context_limit(state)
    scope = _revalidate_active_writer(active)
    try:
        requested = active.binding.registry.request_drain(
            active.lease, scope, run_handle, "context_compaction",
        )
    except Exception:
        _quarantine_active_writer(active)
        _raise_context_limit(state)
    if requested is not True:
        _raise_context_limit(state)
    state["compaction_drain_requested"] = True
    parts.append(sse_event("context_compressing", {
        "message": "正在压缩上下文...",
        "reason": "history_exceeds_threshold",
        "threshold_tokens": state.get("token_limit", 0),
        "context_window": state.get("context_window"),
    }))


async def _do_compact(state: dict) -> bool:
    """Compact agent messages via autocompact. Returns True on success."""
    active = _active_writer_binding.get()
    if active is None:
        _raise_context_limit(state)
    scope = _revalidate_active_writer(active)
    active.binding.registry.assert_compaction_safe(active.lease, scope)
    compaction_window = state.get("compaction_window")
    if type(compaction_window) is not int or compaction_window <= 0:
        return False
    messages = _read_agent_messages(state)
    _revalidate_active_writer(active)
    if not messages:
        return False
    return await _compact_and_apply(state, messages, compaction_window)


def _read_agent_messages(state: dict) -> list:
    """Read current messages from the agent's checkpointed state."""
    agent = state.get("agent")
    config = state.get("config")
    if agent is None or config is None:
        return []
    _revalidate_active_writer()
    snapshot = agent.get_state(config)
    _revalidate_active_writer()
    return list(snapshot.values.get("messages", []))


async def _compact_and_apply(
    state: dict, messages: list, context_window: int,
) -> bool:
    """Summarize only after a joined drain and publish through the saver guard."""
    active = _active_writer_binding.get()
    if active is None:
        _raise_context_limit(state)
    scope = _revalidate_active_writer(active)
    active.binding.registry.assert_compaction_safe(active.lease, scope)
    from src.agent.compact.autocompact import _make_summary_client, autocompact_messages
    timing = state.get("agent_timing")
    compaction = timing.begin_compaction() if timing is not None else None
    status = "error"
    try:
        with use_agent_timing(timing):
            llm_client = _make_summary_client()
            compacted = await autocompact_messages(messages, llm_client, context_window)
            _revalidate_active_writer(active)
            # The same list means no work was needed. A new list can have the
            # same length while replacing older messages with a shorter summary.
            if compacted is messages:
                status = "no_change"
                return False
            await _apply_compacted(state, messages, compacted)
            _revalidate_active_writer(active)
        status = "applied"
        return True
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except BaseException:
        status = "error"
        raise
    finally:
        if timing is not None:
            timing.end_compaction(compaction, status)


async def _apply_compacted(state: dict, original: list, compacted: list) -> None:
    """Publish only a confirmed, resumable checkpoint replacement."""
    agent = state.get("agent")
    # Keep non-copyable stream callbacks out of the checkpoint publisher's
    # deepcopy path. The publisher only needs the host's checkpoint config;
    # timing callbacks are reattached to its exact authorized continuation
    # object after the publication receipt has been confirmed.
    config = state.get("config")
    stream_config = state.get("stream_config", config)
    active = _active_writer_binding.get()
    if active is None or agent is None or config is None or stream_config is None:
        _raise_context_limit(state)
    scope = _revalidate_active_writer(active)
    active.binding.registry.assert_compaction_safe(active.lease, scope)

    def confirm_receipt(applied_config: dict, observed: object) -> bool:
        current_scope = _revalidate_active_writer(active)
        if type(applied_config) is not dict:
            return False
        applied_conf = applied_config.get("configurable")
        observed_config = getattr(observed, "config", None)
        observed_conf = (
            observed_config.get("configurable")
            if type(observed_config) is dict
            else None
        )
        observed_values = getattr(observed, "values", None)
        if (
            type(applied_conf) is not dict
            or type(observed_conf) is not dict
            or type(observed_values) is not dict
        ):
            return False
        tasks = tuple(getattr(observed, "tasks", ()) or ())
        checkpoint_id = applied_conf.get("checkpoint_id")
        return (
            applied_conf.get("thread_id") == current_scope.owner_key
            and applied_conf.get("checkpoint_ns", "") == ""
            and type(checkpoint_id) is str
            and bool(checkpoint_id)
            and observed_conf.get("thread_id") == current_scope.owner_key
            and observed_conf.get("checkpoint_ns", "") == ""
            and observed_conf.get("checkpoint_id") == checkpoint_id
            and observed_values.get("messages", []) == compacted
            and tuple(getattr(observed, "next", ()) or ()) == ("model",)
            and len(tasks) == 1
            and getattr(tasks[0], "name", None) == "model"
            and getattr(tasks[0], "error", None) is None
            and not getattr(tasks[0], "interrupts", ())
            and not getattr(observed, "interrupts", ())
        )

    from src.agent.compact.checkpoint_publish import publish_compacted_checkpoint

    outcome = await publish_compacted_checkpoint(
        agent,
        config,
        original,
        compacted,
        registry=active.binding.registry,
        lease=active.lease,
        scope=scope,
        current_scope=active.binding.current_scope,
        receipt_callback=confirm_receipt,
    )
    _revalidate_active_writer(active)
    execution_config = outcome.execution_config
    if (
        outcome.status != "APPLIED"
        or outcome.receipt_status != "CONFIRMED"
        or not _is_exact_execution_config(execution_config, agent)
    ):
        _raise_context_limit(state)
    callbacks = (
        stream_config.get("callbacks")
        if type(stream_config) is dict
        else None
    )
    if callbacks is not None:
        # Preserve callback behavior without replacing or copying the exact
        # object authorized by the lifecycle registry. Its checkpoint tuple is
        # intentionally untouched.
        execution_config["callbacks"] = callbacks
    # Preserve the exact publisher object; do not merge, copy, or rebuild it.
    state["continuation_execution_config"] = execution_config
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
    *,
    control: Any = None,
    exit_join_state: _NativeExitJoinState | None = None,
    active_writer: _ActiveWriterBinding | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Merge LangGraph agent stream with intermediate tool events from queue.

    Runs two consumers concurrently and yields tagged dicts:
      {"source": "agent", "mode": mode, "chunk": chunk}
      {"source": "tool_event", "event": event}
    """
    outgoing: asyncio.Queue = asyncio.Queue()
    tasks: list[asyncio.Task] = []
    join_state = exit_join_state or _NativeExitJoinState()
    active = active_writer if active_writer is not None else _active_writer_binding.get()

    async def _consume_agent() -> None:
        agent_iter = None
        primary_error: BaseException | None = None
        try:
            # This synchronous check is adjacent to the first native iterator
            # creation, with no await where the pinned epoch could change.
            _revalidate_active_writer(active)
            native_stream = agent.astream(
                events, config=config, stream_mode=["messages", "updates"],
                control=control,
            )
            agent_iter = native_stream.__aiter__()
            while True:
                try:
                    mode, chunk = await agent_iter.__anext__()
                except StopAsyncIteration:
                    break
                except asyncio.CancelledError as exc:
                    await _join_langgraph_exit_task(
                        exc, join_state, active, source="__anext__",
                    )
                    raise
                _revalidate_active_writer(active)
                await outgoing.put({"source": "agent", "mode": mode, "chunk": chunk})
                _revalidate_active_writer(active)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                closer = getattr(agent_iter, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except asyncio.CancelledError as exc:
                        await _join_langgraph_exit_task(
                            exc, join_state, active, source="aclose",
                        )
                        _mark_join_unconfirmed(
                            join_state,
                            active,
                            "Native agent iterator close raised cancellation",
                            exc,
                            primary_error,
                        )
                        if primary_error is None or not isinstance(
                            primary_error, (asyncio.CancelledError, GeneratorExit),
                        ):
                            raise
                    except BaseException as exc:
                        _mark_join_unconfirmed(
                            join_state,
                            active,
                            "Native agent iterator close failed",
                            exc,
                            primary_error,
                        )
                        if primary_error is None:
                            raise
                    else:
                        _revalidate_active_writer(active)
            finally:
                # Sentinels must be delivered even if native iterator close fails.
                try:
                    queue.put_nowait(None)
                except Exception:
                    pass
                try:
                    await outgoing.put({"source": "agent_done"})
                    _revalidate_active_writer(active)
                except BaseException as exc:
                    _mark_join_unconfirmed(
                        join_state,
                        active,
                        "Agent consumer could not publish its completion sentinel",
                        exc,
                        primary_error,
                    )
                    if primary_error is None:
                        raise

    async def _consume_queue() -> None:
        try:
            while True:
                event = await queue.get()
                _revalidate_active_writer(active)
                if event is None:
                    break
                await outgoing.put({"source": "tool_event", "event": event})
                _revalidate_active_writer(active)
        except asyncio.CancelledError:
            raise
        finally:
            await outgoing.put({"source": "queue_done"})
            _revalidate_active_writer(active)

    try:
        tasks = [
            asyncio.create_task(_consume_agent()),
            asyncio.create_task(_consume_queue()),
        ]
        agent_done = False
        queue_done = False
        while not (agent_done and queue_done):
            try:
                item = await asyncio.wait_for(
                    outgoing.get(), timeout=AGENT_HEARTBEAT_INTERVAL
                )
                _revalidate_active_writer(active)
            except asyncio.TimeoutError:
                _revalidate_active_writer(active)
                yield {"source": "tool_event", "event": {"type": "heartbeat"}}
                _revalidate_active_writer(active)
                continue
            source = item.get("source")
            if source == "agent_done":
                agent_done = True
            elif source == "queue_done":
                queue_done = True
            else:
                yield item
                _revalidate_active_writer(active)
        results, interrupted = await _await_tasks_to_completion(tasks)
        if results is None or any(not task.done() for task in tasks):
            _mark_join_unconfirmed(
                join_state,
                active,
                "Merge consumers did not finish after both completion sentinels",
            )
            raise RuntimeError("agent stream merge consumers did not finish")
        _revalidate_active_writer(active)
        if interrupted is not None:
            raise interrupted
        for result in results:
            if isinstance(result, BaseException):
                raise result
    except BaseException as primary_error:
        if isinstance(primary_error, asyncio.CancelledError):
            _check_unverified_task_argument(
                primary_error,
                join_state,
                active,
                source="merge owner",
            )
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            results, interrupted = await _await_tasks_to_completion(tasks)
            if results is None or any(not task.done() for task in tasks):
                _mark_join_unconfirmed(
                    join_state,
                    active,
                    "Merge consumers remained active after cancellation",
                    primary=primary_error,
                )
            else:
                for index, result in enumerate(results):
                    if isinstance(result, asyncio.CancelledError):
                        _check_unverified_task_argument(
                            result,
                            join_state,
                            active,
                            source=f"merge consumer {index}",
                            primary=primary_error,
                        )
                    elif isinstance(result, BaseException):
                        _mark_join_unconfirmed(
                            join_state,
                            active,
                            f"Merge consumer {index} failed during shutdown",
                            result,
                            primary_error,
                        )
            try:
                _revalidate_active_writer(active)
            except BaseException as exc:
                _mark_join_unconfirmed(
                    join_state,
                    active,
                    "Writer revalidation failed after merge consumer shutdown",
                    exc,
                    primary_error,
                )
                if not isinstance(primary_error, (asyncio.CancelledError, GeneratorExit)):
                    raise
            if interrupted is not None:
                _check_unverified_task_argument(
                    interrupted,
                    join_state,
                    active,
                    source="merge consumer join",
                    primary=primary_error,
                )
            if interrupted is not None and not isinstance(
                primary_error, (asyncio.CancelledError, GeneratorExit),
            ):
                raise interrupted
        raise


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
