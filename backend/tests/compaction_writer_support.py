from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.api.compaction_archive import bind_compaction_archive
from app.api.context_events import ContextEventScope
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphDrained
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.runtime import RunControl, get_runtime
from src.agent.request_context import REQUEST_GRAPH_BINDING_KEY, new_request_graph_binding
from src.agent.writer_lifecycle import (
    acquire_writer_lease,
    bind_writer_lease,
    mint_graph_provenance,
)


def _message_signature(messages: list) -> list[tuple[type, Any]]:
    return [(type(message), message.content) for message in messages]


@asynccontextmanager
async def drained_compaction_checkpoint(
    scope: ContextEventScope,
    messages: list,
    context_window: int,
    *,
    checkpointer: Any | None = None,
) -> AsyncIterator[tuple[Any, dict[str, Any], list]]:
    if type(scope) is not ContextEventScope:
        raise TypeError("A host-created ContextEventScope is required")

    binding = new_request_graph_binding(scope.session_id, scope.request_id)
    config = {
        "configurable": {
            "thread_id": binding.graph_thread_id,
            REQUEST_GRAPH_BINDING_KEY: binding.as_config_value(),
            "context_window": context_window,
        },
    }

    model_calls: list[str] = []
    tool_runtime_control_matches: list[bool] = []
    run_control_ref: list[RunControl] = []

    def tools_node(_state):
        runtime = get_runtime()
        tool_runtime_control_matches.append(
            bool(run_control_ref) and runtime.control is run_control_ref[0]
        )
        return {}

    def model_node(_state):
        model_calls.append("model")
        return {}

    builder = StateGraph(MessagesState)
    builder.add_node("tools", tools_node)
    builder.add_node("model", model_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", "model")
    builder.add_edge("model", END)
    saver = checkpointer if checkpointer is not None else InMemorySaver()
    graph = builder.compile(checkpointer=saver)
    if not mint_graph_provenance(graph):
        raise RuntimeError("Native graph provenance could not be minted")

    scope_snapshot = (
        scope.session_id,
        scope.request_id,
        scope.principal_hash,
        scope.session_epoch,
    )

    def validate_epochs() -> None:
        if type(scope) is not ContextEventScope or (
            scope.session_id,
            scope.request_id,
            scope.principal_hash,
            scope.session_epoch,
        ) != scope_snapshot:
            raise RuntimeError("Synthetic request scope changed during graph lifetime")

    lease = acquire_writer_lease(
        graph,
        config,
        business_session_id=scope.session_id,
        request_id=scope.request_id,
        session_epoch=scope.session_epoch,
        memory_epoch=None,
        validate_epochs=validate_epochs,
    )

    caller_task = asyncio.current_task()
    with bind_compaction_archive(scope), bind_writer_lease(lease):
        run = lease.start_run(graph, config)
        assert isinstance(run.control, RunControl)
        run_control_ref.append(run.control)
        iterator = graph.astream(
            {"messages": messages},
            config=config,
            stream_mode=["messages", "updates"],
            control=run.control,
        ).__aiter__()
        acknowledged = False
        saw_tools_update = False
        caught_graph_drained = False
        model_calls_at_drain_request: list[tuple[str, ...]] = []
        try:
            while True:
                try:
                    mode, chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                if mode == "updates" and isinstance(chunk, dict) and "tools" in chunk:
                    saw_tools_update = True
                    model_calls_at_drain_request.append(tuple(model_calls))
                    lease.request_drain(run, "context_compaction")
        except GraphDrained as exc:
            caught_graph_drained = True
            acknowledged = lease.acknowledge_graph_drained(run, exc)
        finally:
            await iterator.aclose()
            lease.finish_run(run, joined=True)

        assert asyncio.current_task() is caller_task
        assert saw_tools_update
        assert run.control.drain_requested
        assert model_calls_at_drain_request == [()]
        assert model_calls == []
        assert tool_runtime_control_matches == [True]
        assert caught_graph_drained
        assert acknowledged
        assert run.drain_acknowledged and run.joined
        lease.assert_compaction_safe(graph, config)
        drained = await graph.aget_state(config)
        if tuple(drained.next or ()) != ("model",):
            raise AssertionError("The drained checkpoint is not paused at the model node")
        original_messages = list(drained.values.get("messages", []))
        if len(original_messages) != len(messages) or _message_signature(original_messages) != _message_signature(messages):
            raise AssertionError("The tools node changed the original message sequence")

        yield graph, config, original_messages
