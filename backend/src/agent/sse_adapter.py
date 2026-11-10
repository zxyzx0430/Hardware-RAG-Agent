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
import copy
import json
import logging
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
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
    project_skill_tool_args_for_sse,
)
from src.agent.streaming_event_bus import bind_event_channel, register_queue, unregister_queue
from src.agent.writer_bridge import (
    assert_compaction_safe,
    assert_current,
    current_request_binding,
    quarantine as quarantine_writer,
)
from src.agent.async_join import join_native_exit_tasks
from src.agent.request_context import request_graph_binding_from_config
import anyio
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


@dataclass
class _CompactionSourceSidecar:
    """Private positional source IDs and the exact message prefix they bind."""

    message_ids: tuple[str | None, ...] | None
    message_shapes: tuple[tuple[Any, ...], ...] | None
    messages: tuple[Any, ...] | None = None


_COMPACTION_SOURCE_SIDECAR: ContextVar[_CompactionSourceSidecar | None] = ContextVar(
    "agent_compaction_source_sidecar", default=None,
)


def _compaction_source_message_shape(message: Any) -> tuple[Any, ...] | None:
    if type(message) is dict:
        role = message.get("role")
        content = message.get("content")
        additional_kwargs = message.get("additional_kwargs", {})
        name = message.get("name")
        tool_calls = message.get("tool_calls", [])
        invalid_tool_calls = message.get("invalid_tool_calls", [])
    else:
        role = {
            "human": "user",
            "ai": "assistant",
            "system": "system",
            "tool": "tool",
        }.get(getattr(message, "type", None))
        content = getattr(message, "content", None)
        additional_kwargs = getattr(message, "additional_kwargs", {})
        name = getattr(message, "name", None)
        tool_calls = getattr(message, "tool_calls", [])
        invalid_tool_calls = getattr(message, "invalid_tool_calls", [])
    if (type(role) is not str or role not in {"user", "assistant", "system", "tool"}
            or type(additional_kwargs) is not dict):
        return None
    try:
        return copy.deepcopy((
            role, content, additional_kwargs, name, tool_calls, invalid_tool_calls,
        ))
    except Exception:
        return None


def _is_midrun_summary_shape(shape: tuple[Any, ...]) -> bool:
    """A graph-only summary emitted after generic tool observation archival."""
    return (
        type(shape) is tuple
        and len(shape) == 6
        and shape[0] == "system"
        and type(shape[1]) is str
        and shape[1].startswith("[历史摘要]: ")
        and type(shape[2]) is dict
        and shape[2].get("hwrag_midrun_summary") == {"version": 1}
    )


def _new_compaction_source_sidecar(
    message_ids: tuple[str | None, ...] | None,
    messages: tuple[Any, ...] | list[Any] | None,
) -> _CompactionSourceSidecar:
    unavailable = _CompactionSourceSidecar(None, None)
    if (type(message_ids) is not tuple or type(messages) not in (tuple, list)
            or not message_ids or len(message_ids) != len(messages)):
        return unavailable
    shapes = tuple(_compaction_source_message_shape(message) for message in messages)
    if any(shape is None for shape in shapes):
        return unavailable
    ids = [message_id for message_id in message_ids if message_id is not None]
    if (any(type(message_id) is not str or not message_id for message_id in ids)
            or len(set(ids)) != len(ids)):
        return unavailable
    transient_start = None
    for index, message_id in enumerate(message_ids):
        if message_id is not None:
            continue
        shape = shapes[index]
        if index == 0 and shape[0] == "system":
            try:
                from app.api.compaction_checkpoint import checkpoint_metadata_for_transport

                if checkpoint_metadata_for_transport(shape[2]):
                    continue
            except Exception:
                return unavailable
            if _is_midrun_summary_shape(shape):
                continue
        transient_start = index
        break
    if transient_start is not None:
        if (any(message_id is not None for message_id in message_ids[transient_start:])
                or any(shape[0] not in {"user", "assistant", "system", "tool"}
                       for shape in shapes[transient_start:])):
            return unavailable
    return _CompactionSourceSidecar(message_ids, shapes, tuple(messages))


@contextmanager
def bind_compaction_source_sidecar(
    message_ids: tuple[str | None, ...] | None,
    messages: tuple[Any, ...] | list[Any] | None,
):
    """Bind verified request history for a separately-created HITL stream state."""
    sidecar = _new_compaction_source_sidecar(message_ids, messages)
    token = _COMPACTION_SOURCE_SIDECAR.set(sidecar)
    try:
        yield sidecar
    finally:
        _COMPACTION_SOURCE_SIDECAR.reset(token)


def _source_ids_for_current_messages(
    sidecar: _CompactionSourceSidecar | None,
    messages: list[Any],
) -> tuple[str | None, ...] | None:
    if (type(sidecar) is not _CompactionSourceSidecar
            or sidecar.message_ids is None or sidecar.message_shapes is None):
        return None
    if len(messages) < len(sidecar.message_shapes):
        return None
    current_shapes = tuple(
        _compaction_source_message_shape(message)
        for message in messages[:len(sidecar.message_shapes)]
    )
    if (any(shape is None for shape in current_shapes)
            or current_shapes != sidecar.message_shapes):
        return None
    return sidecar.message_ids + (None,) * (len(messages) - len(sidecar.message_ids))


def _compacted_source_sidecar(
    messages: list[Any],
    message_ids: tuple[str | None, ...],
    compacted: list[Any],
) -> _CompactionSourceSidecar | None:
    if type(compacted) is not list or len(compacted) < 2:
        return None
    if any(compacted[0] is original for original in messages):
        return None
    checkpoint_shape = _compaction_source_message_shape(compacted[0])
    if checkpoint_shape is None or checkpoint_shape[0] != "system":
        return None
    is_midrun_summary = _is_midrun_summary_shape(checkpoint_shape)
    if not is_midrun_summary:
        try:
            from app.api.compaction_checkpoint import checkpoint_metadata_for_transport

            if not checkpoint_metadata_for_transport(checkpoint_shape[2]):
                return None
        except Exception:
            return None
    retained = compacted[1:]
    retained_count = len(retained)
    retained_start = len(messages) - retained_count
    if (retained_start < 0
            or any(new is not old for new, old in zip(
                retained, messages[retained_start:], strict=True,
            ))):
        return None
    retained_ids = message_ids[retained_start:]
    if len(retained_ids) != retained_count:
        return None
    return _new_compaction_source_sidecar((None, *retained_ids), tuple(compacted))


def _with_skill_proof_callback(config: dict) -> dict:
    """Add a request-local proof observer to one graph run when host-bound."""
    agent_config = dict(config)
    callbacks = config.get("callbacks")
    if isinstance(callbacks, (list, tuple)):
        agent_config["callbacks"] = list(callbacks)
    try:
        from src.agent.skill_proof import (
            SkillProofCallback,
            current_skill_proof_collector,
        )

        if current_skill_proof_collector() is None:
            return agent_config

        handlers = []
        if isinstance(callbacks, (list, tuple)):
            handlers.extend(callbacks)
        else:
            for attribute in ("handlers", "inheritable_handlers"):
                values = getattr(callbacks, attribute, None)
                if isinstance(values, (list, tuple)):
                    handlers.extend(values)
        if any(isinstance(handler, SkillProofCallback) for handler in handlers):
            return agent_config

        class _BestEffortSkillProofCallback(SkillProofCallback):
            """Keep observation faults from changing model execution."""

            def on_chat_model_start(
                self,
                serialized: dict[str, Any],
                messages: list[list[Any]],
                *,
                run_id: Any,
                **kwargs: Any,
            ) -> None:
                try:
                    super().on_chat_model_start(
                        serialized, messages, run_id=run_id, **kwargs,
                    )
                except Exception:
                    # Evidence is optional; never make a model invocation retry.
                    return

        return merge_configs(
            agent_config, {"callbacks": [_BestEffortSkillProofCallback()]},
        )
    except Exception:
        return agent_config


# ═══════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════

async def stream_agent_to_sse(
    agent: Any,
    events: Any,
    config: dict,
    call_counter: Counter,
    permission_mode: str = "default",
    model: str = "",
    requested_max_tokens: int | None = None,
    context_budget: ContextBudget | None = None,
    *,
    initial_execution_config: dict | None = None,
    before_initial_iteration=None,
    compaction_source_message_ids: tuple[str | None, ...] | None = None,
    compaction_source_messages: tuple[Any, ...] | list[Any] | None = None,
) -> AsyncIterator[str]:
    """Run the agent stream and yield SSE event strings.

    Hands off to hitl_handler after the stream drains for interrupt evaluation
    + auto-resume.
    """
    if initial_execution_config is None:
        if before_initial_iteration is not None:
            raise ValueError("Initial startup validator requires a pinned execution config")
    elif (
        type(initial_execution_config) is not dict
        or type(initial_execution_config.get("configurable")) is not dict
        or events is not None
        or not callable(before_initial_iteration)
    ):
        raise ValueError("Initial startup requires None input, a host config and a validator")
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
    event_context = ExitStack()
    outcome = "error"
    error_type: str | None = None
    try:
        state = init_stream_state(
            stream_config, model, requested_max_tokens, context_budget,
        )
        if compaction_source_message_ids is None and compaction_source_messages is None:
            source_sidecar = _COMPACTION_SOURCE_SIDECAR.get()
        else:
            source_sidecar = _new_compaction_source_sidecar(
                compaction_source_message_ids, compaction_source_messages,
            )
        state["compaction_source_sidecar"] = source_sidecar
        state["agent_timing"] = timing
        state["tool_event_queue"] = asyncio.Queue()
        state["permission_mode"] = permission_mode
        state["agent"] = agent
        state["config"] = stream_config
        binding = request_graph_binding_from_config(stream_config)
        register_queue(session_id, state["tool_event_queue"], business_session_id=(
            binding.business_session_id if binding is not None else None
        ))
        queue_registered = True
        event_context.enter_context(bind_event_channel(stream_config, state["tool_event_queue"]))
        try:
            initial_tokens = estimate_context_tokens(events)
        except ContextLimitError:
            if events not in (None, {}):
                raise
            initial_tokens = 0
        token_limit = state.get("token_limit", 0)
        if type(token_limit) is int and token_limit > 0 and initial_tokens > token_limit:
            raise ContextLimitError(
                cumulative=initial_tokens, limit=token_limit,
            )
        reset_token_counter(initial_tokens)
        # The initial request history already occupies the window. Seed the
        # same estimator used by compaction triggers so streaming compaction
        # reacts to history + new output, not only new output.
        initial_messages = compaction_source_messages
        if (initial_messages is None
                and type(source_sidecar) is _CompactionSourceSidecar):
            initial_messages = source_sidecar.messages
        if initial_messages:
            from src.agent.request_budget import estimated_messages_tokens

            seeded_tokens = estimated_messages_tokens(initial_messages)
            if seeded_tokens > _CUMULATIVE_TOKENS.get():
                _CUMULATIVE_TOKENS.set(seeded_tokens)
        if initial_execution_config is None:
            agent_stream = _iter_agent_sse(
                agent, events, stream_config, call_counter, call_start_time, state,
            )
        else:
            initial_stream_config = merge_configs(
                initial_execution_config,
                {"callbacks": [AgentTimingCallback(timing)]},
            )
            agent_stream = _iter_agent_sse(
                agent, None, initial_stream_config, call_counter, call_start_time, state,
                before_initial_iteration=before_initial_iteration,
            )
        try:
            async for sse in agent_stream:
                timing.observe_sse(sse)
                yield sse
        finally:
            await agent_stream.aclose()
        from src.agent.hitl_handler import handle_auto_resume
        resume_stream = handle_auto_resume(
            agent, stream_config, permission_mode, call_counter, call_start_time, state,
        )
        try:
            async for sse in resume_stream:
                timing.observe_sse(sse)
                yield sse
        finally:
            await resume_stream.aclose()
        outcome = timing.completion_outcome()
    except (asyncio.CancelledError, GeneratorExit):
        outcome = "cancelled"
        raise
    except BaseException as exc:
        outcome = "error"
        error_type = type(exc).__name__
        raise
    finally:
        try:
            event_context.close()
        finally:
            if queue_registered:
                unregister_queue(session_id, state["tool_event_queue"])
            timing.finish(outcome, error_type=error_type)
    usage = state.get("usage")
    if isinstance(usage, dict) and any(usage.get(key, 0) for key in (
        "prompt_tokens", "completion_tokens", "total_tokens",
    )):
        yield sse_event("usage", {"usage": usage})
    logger.info("stream_agent_to_sse done total_steps=%s", state.get("step_index", 0))


async def _iter_agent_sse(
    agent: Any, events: Any, config: dict,
    call_counter: Counter, call_start_time: dict[str, float], state: dict,
    *, before_initial_iteration=None,
) -> AsyncIterator[str]:
    """Iterate agent.astream and yield converted SSE events.

    When a tool_event_queue is present in state (set by stream_agent_to_sse),
    tool intermediate events (compile_log / progress / thinking / heartbeat)
    are merged into the stream in real time.
    """
    token_limit = state.get("token_limit", 0)
    if (
        state.get("needs_compact")
        and type(token_limit) is int and token_limit > 0
        and _CUMULATIVE_TOKENS.get() >= token_limit
    ):
        raise ContextLimitError(cumulative=_CUMULATIVE_TOKENS.get(), limit=token_limit)
    agent_config = _with_skill_proof_callback(config)
    tool_event_queue: asyncio.Queue | None = state.get("tool_event_queue")
    from src.agent.writer_bridge import (
        acknowledge_drained,
        assert_current,
        begin_native_run,
        current_request_binding,
        finish_native_run,
        quarantine as quarantine_writer,
        request_drain,
    )
    from src.agent.async_join import join_native_exit_tasks

    binding = current_request_binding()
    run = begin_native_run(agent, config)
    join_failed = {"value": False}

    async def _direct_agent_items():
        outgoing: asyncio.Queue = asyncio.Queue()

        async def _produce_agent_items() -> None:
            native_iterator = None
            try:
                if before_initial_iteration is not None:
                    before_initial_iteration()
                assert_current(agent, config)
                native_iterator = agent.astream(
                    events, config=agent_config, stream_mode=["messages", "updates"],
                    control=run.native_control,
                ).__aiter__()
                while True:
                    assert_current(agent, config)
                    try:
                        mode, chunk = await native_iterator.__anext__()
                    except asyncio.CancelledError as exc:
                        try:
                            await join_native_exit_tasks(exc)
                        except BaseException:
                            join_failed["value"] = True
                            raise
                        raise
                    except StopAsyncIteration:
                        break
                    assert_current(agent, config)
                    ack = asyncio.get_running_loop().create_future()
                    await outgoing.put({
                        "source": "agent", "mode": mode, "chunk": chunk, "_ack": ack,
                    })
                    await ack
            finally:
                try:
                    closer = getattr(native_iterator, "aclose", None)
                    if closer is not None:
                        try:
                            await closer()
                        except asyncio.CancelledError as exc:
                            try:
                                await join_native_exit_tasks(exc)
                            except BaseException:
                                join_failed["value"] = True
                                raise
                            raise
                finally:
                    # End-of-stream signals termination only; it is not a save acknowledgement.
                    await outgoing.put({"source": "agent_done"})

        producer_task = asyncio.create_task(_produce_agent_items())
        try:
            while True:
                item = await outgoing.get()
                if item["source"] == "agent_done":
                    await producer_task
                    break
                yield item
        finally:
            if not producer_task.done():
                producer_task.cancel()
            await asyncio.gather(producer_task, return_exceptions=True)

    if tool_event_queue is None:
        source = _direct_agent_items()
    elif before_initial_iteration is None:
        source = _merge_agent_and_tool_events(
            agent, events, config, tool_event_queue, agent_config=agent_config,
            run=run,
            join_failed=join_failed,
        )
    else:
        source = _merge_agent_and_tool_events(
            agent, events, config, tool_event_queue,
            agent_config=agent_config,
            before_initial_iteration=before_initial_iteration,
            run=run, join_failed=join_failed,
        )
    iterator = source.__aiter__()
    last_progress_at = time.monotonic()
    next_item_task: asyncio.Task | None = None
    had_error = False
    user_stopped = False
    graph_drained = False
    cleanup_joined = False
    try:
        while True:
            if next_item_task is None:
                assert_current(agent, config)
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
            assert_current(agent, config)
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
                if join_failed["value"]:
                    had_error = True
                    raise
                if acknowledge_drained(run, exc):
                    graph_drained = True
                    break
                user_stopped = isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                had_error = True
                raise
            next_item_task = None
            check_active_tool_timeouts(call_start_time)

            if item["source"] == "agent":
                sse = await _convert_chunk_to_sse(
                    item["mode"], item["chunk"], call_counter, call_start_time, state,
                )
            else:
                sse = _convert_tool_event_to_sse(item["event"])
            assert_current(agent, config)
            if state.get("needs_compact") and not run.native_control.drain_requested:
                request_drain(run, "context_compaction")
            ack = item.get("_ack")
            if isinstance(ack, asyncio.Future) and not ack.done():
                ack.set_result(None)
            if sse:
                if _sse_indicates_progress(sse):
                    last_progress_at = time.monotonic()
                yield sse

        final_text = state.pop("text_buffer", "")
        if final_text:
            yield sse_event("text", {"content": final_text})
    except ContextLimitError as exc:
        had_error = True
        logger.warning("context_limit_exceeded tokens=%s limit=%s", exc.cumulative, exc.limit)
        raise
    except AgentTimeoutError as exc:
        had_error = True
        logger.warning("agent_timeout elapsed=%.1fs limit=%ss", exc.elapsed, exc.limit)
        raise
    except BaseException as exc:
        user_stopped = isinstance(exc, (asyncio.CancelledError, GeneratorExit))
        had_error = True
        raise
    finally:
        # Disconnect cancellation must not skip joining our producer/consumer.
        try:
            with anyio.CancelScope(shield=True):
                if next_item_task is not None and not next_item_task.done():
                    next_item_task.cancel()
                    joined = await asyncio.gather(next_item_task, return_exceptions=True)
                    for result in joined:
                        if isinstance(result, asyncio.CancelledError) and tool_event_queue is not None:
                            try:
                                await join_native_exit_tasks(result)
                            except BaseException:
                                join_failed["value"] = True
                                raise
                closer = getattr(iterator, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except asyncio.CancelledError as exc:
                        if tool_event_queue is not None:
                            try:
                                await join_native_exit_tasks(exc)
                            except BaseException:
                                join_failed["value"] = True
                                raise
                        raise
            cleanup_joined = True
        finally:
            # A user stop is not an uncertain native outcome: teardown already
            # cancelled and joined this run, so the session may be reused.
            finish_native_run(
                run,
                joined=cleanup_joined and (not had_error or user_stopped),
                had_error=had_error and not user_stopped,
            )

    if state.get("needs_compact"):
        if not graph_drained:
            raise ContextLimitError(
                cumulative=_CUMULATIVE_TOKENS.get(), limit=state.get("token_limit", 0),
            )
        yield sse_event("context_compressing", {"message": "正在压缩上下文..."})
        if not await _do_compact(state):
            if state.pop("compaction_status", None) == "OUTCOME_UNKNOWN":
                from src.agent.compact.writer_lifecycle import WriterLifecycleError

                raise WriterLifecycleError("Compaction outcome is unknown; request stopped without replay")
            raise ContextLimitError(
                cumulative=_CUMULATIVE_TOKENS.get(), limit=state.get("token_limit", 0),
            )
        continuation_config = state.pop("compaction_execution_config", None)
        if type(continuation_config) is not dict:
            quarantine_writer()
            from src.agent.compact.writer_lifecycle import WriterLifecycleError

            raise WriterLifecycleError(
                "Compaction was applied, but its pending model continuation was not verified"
            )
        state["config"] = continuation_config
        async for sse in _iter_agent_sse(
            agent, None, continuation_config, call_counter, call_start_time, state,
        ):
            yield sse
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
            if isinstance(payload.get("todos"), list):
                return True
        elif event_type == "plan_update":
            if isinstance(payload.get("plan"), dict):
                return True
        elif event_type == "permission_mode_changed":
            if isinstance(payload.get("permission_mode"), str) and payload["permission_mode"]:
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
    _capture_usage(msg_chunk, state)
    accumulate_tokens(msg_chunk.content, state)
    return "\n".join(parts) if parts else None


def _capture_usage(msg_chunk: AIMessageChunk, state: dict) -> None:
    """Accumulate provider-reported usage exactly once per model response."""
    raw = getattr(msg_chunk, "usage_metadata", None)
    if callable(getattr(raw, "model_dump", None)):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        return
    input_tokens = _as_int(raw.get("input_tokens"))
    output_tokens = _as_int(raw.get("output_tokens"))
    total_tokens = _as_int(raw.get("total_tokens")) or input_tokens + output_tokens
    input_details = raw.get("input_token_details")
    cached_tokens = 0
    if isinstance(input_details, dict):
        cached_tokens = _as_int(input_details.get("cache_read"))
        if not cached_tokens:
            cached_tokens = _as_int(input_details.get("cached_tokens"))
    if not (input_tokens or output_tokens or total_tokens):
        return
    usage = state.setdefault("usage", {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
    })
    usage["prompt_tokens"] += input_tokens
    usage["completion_tokens"] += output_tokens
    usage["total_tokens"] += total_tokens
    usage["cached_tokens"] += cached_tokens


def _as_int(value: Any) -> int:
    if type(value) is int and value >= 0:
        return value
    if type(value) is float and value >= 0 and value.is_integer():
        return int(value)
    return 0


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
    raw_args = tc.get("args", {})
    call_entry = {"tool": name, "args": raw_args}
    trace_entry = {
        "tool": name,
        "args": project_skill_tool_args_for_sse(name, raw_args),
    }
    state.setdefault("call_history", []).append(trace_entry)
    state.setdefault("pending_call_args", {})[call_id] = call_entry
    state.setdefault("pending_tool_calls", set()).add(call_id)
    logger.info("tool_call step=%s tool=%s call_id=%s", state['step_index'], name, call_id)
    from src.agent.request_context import active_tool_context
    ctx = active_tool_context()
    specs = ctx.request_tools.values() if ctx is not None else list_registered_tools()
    spec = next((item for item in specs if item.name == name), None)
    risk_level = spec.risk_level.value if spec is not None else None
    # The live context is authoritative: enter_plan_mode mutates it mid-run,
    # while state["permission_mode"] holds the mode captured at stream start.
    perm_mode = (
        getattr(ctx, "permission_mode", None)
        if ctx is not None
        else state.get("permission_mode", "bypassPermissions")
    )
    if perm_mode == "bypassPermissions":
        decision_source = "mode_bypass"
    elif perm_mode == "plan":
        decision_source = "plan_mode"
    else:
        decision_source = "auto_allow"
    if spec is not None and spec.mcp_info is not None:
        decision_source = "confirmation_required"
    parts.append(sse_event("tool_call", {
        "tool": tc.get("name", ""),
        "args": trace_entry["args"],
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
    """True when cumulative tokens reach the shared message-body budget.

    ``token_limit`` is produced by ``context_guard.compute_token_limit``, which
    takes the smaller of the session override and the registered model window
    and applies the same safety ratio as pre-send ``should_autocompact``.
    """
    token_limit = state.get("token_limit", 0)
    if token_limit <= 0 and state.get("context_window"):
        from src.agent.context_guard import compute_token_limit

        token_limit = compute_token_limit(None, state.get("context_window"))
        state["token_limit"] = token_limit
    if token_limit <= 0:
        return False
    return _CUMULATIVE_TOKENS.get() >= token_limit


async def _maybe_compact(parts: list[str], state: dict) -> None:
    """Leave publication to the outer consumer after its native run drains."""
    return


def _effective_compaction_window(state: dict) -> int | None:
    """Use the same min(session, model) window that produced token_limit.

    ``init_stream_state`` computes ``token_limit`` via the shared budget
    helper, but the raw session ``context_window`` may be larger than the
    registered model window. Recover the effective window from the already
    safety-scaled limit so the summarizer trigger cannot be bypassed.
    """
    from src.agent.prompts import MAX_TOKEN_RATIO

    token_limit = state.get("token_limit", 0)
    context_window = state.get("context_window")
    if (type(token_limit) is int and token_limit > 0
            and type(MAX_TOKEN_RATIO) is float and MAX_TOKEN_RATIO > 0):
        effective = int(token_limit / MAX_TOKEN_RATIO)
        if type(context_window) is int and context_window > 0:
            return min(context_window, effective)
        return effective
    return context_window if type(context_window) is int and context_window > 0 else None


async def _do_compact(state: dict) -> bool:
    """Compact agent messages via autocompact. Returns True on success.

    One sealed generation is bounded to 64 source slots, so a long run may
    need several generations before the next model call fits. The graph is
    paused after the drain, which makes repeated apply/readback safe.
    """
    context_window = _effective_compaction_window(state)
    if not context_window:
        return False
    from src.agent.writer_bridge import current_request_binding

    binding = current_request_binding()
    assert_compaction_safe(state.get("agent"), state.get("config"))
    applied_any = False
    rounds = 0
    max_rounds: int | None = None
    while True:
        if not _should_compact(state):
            break
        messages = await _read_agent_messages(state)
        assert_current(state.get("agent"), state.get("config"))
        if not messages:
            break
        if max_rounds is None:
            max_rounds = len(messages) + 1
        if rounds >= max_rounds:
            break
        if not await _compact_and_apply(state, messages, context_window):
            break
        applied_any = True
        rounds += 1
    if applied_any and _should_compact(state):
        # Generations stopped making progress while the run is still over budget.
        # Stop rather than sending a request that is still over budget.
        return False
    if applied_any:
        state["needs_compact"] = False
    return applied_any


async def _read_agent_messages(state: dict) -> list:
    """Read current messages from the agent's checkpointed state."""
    agent = state.get("agent")
    config = state.get("config")
    if agent is None or config is None:
        return []
    from src.agent.writer_bridge import current_request_binding

    binding = current_request_binding()
    assert_current(agent, config)
    snapshot = await agent.aget_state(config)
    assert_current(agent, config)
    return list(snapshot.values.get("messages", []))


async def _compact_and_apply(
    state: dict, messages: list, context_window: int,
) -> bool:
    """Run autocompact, update agent state, reset token counter."""
    from src.agent.compact.autocompact import _make_summary_client, autocompact_messages
    source_sidecar = state.get("compaction_source_sidecar")
    if type(source_sidecar) is not _CompactionSourceSidecar:
        source_sidecar = _COMPACTION_SOURCE_SIDECAR.get()
        if source_sidecar is not None:
            state["compaction_source_sidecar"] = source_sidecar
    source_message_ids = _source_ids_for_current_messages(source_sidecar, messages)
    if source_message_ids is None:
        if type(source_sidecar) is _CompactionSourceSidecar:
            source_sidecar.message_ids = None
            source_sidecar.message_shapes = None
        logger.warning("Streaming compaction source bindings unavailable; refusing unbound compaction")
        return False
    timing = state.get("agent_timing")
    compaction = timing.begin_compaction() if timing is not None else None
    status = "error"
    try:
        with use_agent_timing(timing):
            llm_client = _make_summary_client()
            compacted = await autocompact_messages(
                messages,
                llm_client,
                context_window,
                source_message_ids=source_message_ids,
                token_threshold=state.get("token_limit"),
            )
            from src.agent.writer_bridge import current_request_binding

            binding = current_request_binding()
            assert_current(state.get("agent"), state.get("config"))
            # The same list means no work was needed. A new list can have the
            # same length while replacing older messages with a shorter summary.
            if compacted is messages:
                status = "no_change"
                return False
            if not await _apply_compacted(state, messages, compacted):
                if type(source_sidecar) is _CompactionSourceSidecar:
                    source_sidecar.message_ids = None
                    source_sidecar.message_shapes = None
                status = "stale"
                return False
        if type(source_sidecar) is _CompactionSourceSidecar:
            updated_sidecar = _compacted_source_sidecar(
                messages, source_message_ids, compacted,
            )
            if updated_sidecar is None:
                source_sidecar.message_ids = None
                source_sidecar.message_shapes = None
            else:
                source_sidecar.message_ids = updated_sidecar.message_ids
                source_sidecar.message_shapes = updated_sidecar.message_shapes
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


async def _apply_compacted(state: dict, original: list, compacted: list) -> bool:
    """Update agent state with compacted messages and reset token counter."""
    agent = state.get("agent")
    config = state.get("config")
    from src.agent.compact.checkpoint_publish import _publish_compacted_checkpoint_outcome
    from src.agent.compact.autocompact import (
        _host_publication_scope_for_compacted,
        _is_midrun_summary_message,
    )
    from src.agent.writer_bridge import current_request_binding

    binding = current_request_binding()
    host_scope = _host_publication_scope_for_compacted(compacted)
    registration = ({"host_context_scope": host_scope, "skills_mode": "off"}
                    if host_scope is not None else {})
    outcome = await _publish_compacted_checkpoint_outcome(
        agent, config, original, compacted, **registration,
    )
    state["compaction_status"] = outcome.status
    if outcome.status != "APPLIED":
        return False
    state["compaction_execution_config"] = outcome.execution_config
    # Later rounds in the same drain must read the newly applied checkpoint.
    if type(outcome.execution_config) is dict:
        state["config"] = outcome.execution_config
    if compacted and _is_midrun_summary_message(compacted[0]):
        # Best-effort request-scoped persistence; never blocks a verified
        # graph-only compaction, but the outcome is recorded for callers.
        _store_midrun_summary(state, compacted[0])
    _reset_tokens_for_compacted(compacted)
    if host_scope is not None and outcome.receipt_status == "CONFIRMED":
        await _project_confirmed_memory(state, host_scope)
    return True


async def _project_confirmed_memory(state: dict, host_scope: Any) -> None:
    """Project a confirmed checkpoint into Memory v2 working memory.

    The compaction result is already applied and read back; projection runs in
    its own short transaction and any fault is recorded only in request state.
    """
    try:
        from src.agent.compact.checkpoint_publish import _run_host_record_transaction
        from src.agent.memory_projection import project_confirmed_checkpoint

        result = await _run_host_record_transaction(
            project_confirmed_checkpoint, host_scope,
        )
        state["memory_projection_status"] = (
            "projected" if result is not None else "unavailable"
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        state["memory_projection_status"] = "unavailable"
        logger.warning("Confirmed checkpoint memory projection unavailable")


def _store_midrun_summary(state: dict, summary_message: Any) -> None:
    """Persist the graph-only summary and its archived observation refs."""
    try:
        from src.agent.compact.autocompact import _parse_observation_locator_block
        from app.api.tool_events import get_tool_event_scope
        from app.api.tool_observations import (
            make_tool_observation_scope,
            store_midrun_compaction_summary,
        )

        content = summary_message.content
        body, refs = _parse_observation_locator_block(content)
        event_scope = get_tool_event_scope()
        if event_scope is None:
            state["midrun_summary_status"] = "unavailable"
            return
        scope = make_tool_observation_scope(
            event_scope.principal_hash,
            event_scope.session_id,
            event_scope.request_id,
            event_scope.session_epoch,
        )
        if scope is None:
            state["midrun_summary_status"] = "unavailable"
            return
        import hashlib

        idempotency_key = "midrun:" + hashlib.sha256(
            content.encode("utf-8"),
        ).hexdigest()
        reference = store_midrun_compaction_summary(
            scope,
            summary_text=body,
            observation_refs=tuple(refs),
            idempotency_key=idempotency_key,
        )
        state["midrun_summary_ref"] = reference
        state["midrun_summary_status"] = "stored" if reference else "unavailable"
    except Exception:
        state["midrun_summary_status"] = "unavailable"


def _reset_tokens_for_compacted(compacted: list) -> None:
    """Reset cumulative token counter to estimated size of compacted messages."""
    from src.agent.request_budget import estimated_messages_tokens

    _CUMULATIVE_TOKENS.set(estimated_messages_tokens(compacted))


# ═══════════════════════════════════════════
# Real-time tool event streaming
# ═══════════════════════════════════════════

async def _merge_agent_and_tool_events(
    agent: Any, events: Any, config: dict, queue: asyncio.Queue,
    *, agent_config: dict, before_initial_iteration=None, run,
    join_failed: dict[str, bool],
) -> AsyncIterator[dict[str, Any]]:
    """Merge LangGraph agent stream with intermediate tool events from queue.

    Runs two consumers concurrently and yields tagged dicts:
      {"source": "agent", "mode": mode, "chunk": chunk}
      {"source": "tool_event", "event": event}
    """
    outgoing: asyncio.Queue = asyncio.Queue()
    agent_iter = None
    tasks: list[asyncio.Task] = []

    async def _put_with_ack(item: dict[str, Any]) -> None:
        ack = asyncio.get_running_loop().create_future()
        await outgoing.put({**item, "_ack": ack})
        await ack

    async def _consume_agent() -> None:
        nonlocal agent_iter
        try:
            if before_initial_iteration is not None:
                before_initial_iteration()
            assert_current(agent, config)
            agent_iter = agent.astream(
                events, config=agent_config, stream_mode=["messages", "updates"],
                control=run.native_control,
            ).__aiter__()
            while True:
                assert_current(agent, config)
                try:
                    mode, chunk = await agent_iter.__anext__()
                except asyncio.CancelledError as exc:
                    from src.agent.async_join import join_native_exit_tasks

                    try:
                        await join_native_exit_tasks(exc)
                    except BaseException:
                        join_failed["value"] = True
                        raise
                    raise
                except StopAsyncIteration:
                    break
                assert_current(agent, config)
                await _put_with_ack({"source": "agent", "mode": mode, "chunk": chunk})
        finally:
            try:
                # Close native state in the task that entered its context.
                closer = getattr(agent_iter, "aclose", None) if agent_iter is not None else None
                if closer is not None:
                    try:
                        await closer()
                    except asyncio.CancelledError as exc:
                        from src.agent.async_join import join_native_exit_tasks

                        try:
                            await join_native_exit_tasks(exc)
                        except BaseException:
                            join_failed["value"] = True
                            raise
                        raise
            finally:
                # Sentinels signal termination, never graph-save success.
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
                await _put_with_ack({"source": "tool_event", "event": event})
        except asyncio.CancelledError:
            raise
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
            try:
                item = await asyncio.wait_for(
                    outgoing.get(), timeout=AGENT_HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                assert_current(agent, config)
                yield {"source": "tool_event", "event": {"type": "heartbeat"}}
                continue
            assert_current(agent, config)
            source = item.get("source")
            if source == "agent_done":
                agent_done = True
            elif source == "queue_done":
                queue_done = True
            else:
                yield item
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
    except BaseException:
        with anyio.CancelScope(shield=True):
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                joined = await asyncio.gather(*tasks, return_exceptions=True)
                from src.agent.async_join import join_native_exit_tasks

                for result in joined:
                    if isinstance(result, asyncio.CancelledError):
                        try:
                            await join_native_exit_tasks(result)
                        except BaseException:
                            join_failed["value"] = True
                            raise
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
