"""Native writer publication protocol acceptance checks."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.constants import PUSH
from langgraph.errors import GraphDrained
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph import runtime as langgraph_runtime
from langgraph.runtime import RunControl, get_runtime
from langgraph.types import Send

from src.agent.compact.checkpoint_publish import (
    _PublicationSaver,
    StaleCompaction,
    _publish_compacted_checkpoint_outcome,
)
from src.agent.request_context import REQUEST_GRAPH_BINDING_KEY, new_request_graph_binding
from src.agent.writer_lifecycle import (
    acquire_writer_lease,
    bind_writer_lease,
    mint_graph_provenance,
)


def _binding_config(session_id: str, request_id: str) -> tuple[object, dict]:
    binding = new_request_graph_binding(session_id, request_id)
    config = {
        "recursion_limit": 20,
        "configurable": {
            "thread_id": binding.graph_thread_id,
            REQUEST_GRAPH_BINDING_KEY: binding.as_config_value(),
        },
    }
    return binding, config


def _graph(
    checkpointer,
    model_calls: list[str],
    tool_runtime_control_matches: list[bool] | None = None,
    run_control_ref: list[RunControl] | None = None,
):
    builder = StateGraph(MessagesState)

    def tools(_state):
        if tool_runtime_control_matches is not None:
            runtime = get_runtime()
            tool_runtime_control_matches.append(
                bool(run_control_ref) and runtime.control is run_control_ref[0]
            )
        return {
            "messages": [
                ToolMessage(
                    content="synthetic tool result",
                    tool_call_id="s05-tools-call",
                )
            ]
        }

    def model(_state):
        model_calls.append("model")
        return {"messages": [AIMessage(content="synthetic model response", id="s05-model-response")]}

    builder.add_node("tools", tools)
    builder.add_node("model", model)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", "model")
    builder.add_edge("model", END)
    return builder.compile(checkpointer=checkpointer)


def _history() -> list[HumanMessage | AIMessage]:
    return [
        HumanMessage(content=f"synthetic source {index}", id=f"s05-source-{index}")
        for index in range(4)
    ] + [
        AIMessage(
            content="",
            id="s05-tools-request",
            tool_calls=[
                {"name": "synthetic_tool", "args": {}, "id": "s05-tools-call"}
            ],
        )
    ]


@pytest.mark.asyncio
async def test_native_graph_drain_publish_readback_and_continuation():
    session_id, request_id = "s05-native-success", "2" * 32
    binding, config = _binding_config(session_id, request_id)
    model_calls: list[str] = []
    tool_runtime_control_matches: list[bool] = []
    run_control_ref: list[RunControl] = []
    saver = InMemorySaver()
    agent = _graph(saver, model_calls, tool_runtime_control_matches, run_control_ref)
    assert mint_graph_provenance(agent)
    lease = acquire_writer_lease(
        agent,
        config,
        business_session_id=session_id,
        request_id=request_id,
        session_epoch="a" * 64,
        memory_epoch=None,
        validate_epochs=lambda: None,
    )
    messages = _history()
    caller_task = asyncio.current_task()

    with bind_writer_lease(lease):
        run = lease.start_run(agent, config)
        assert isinstance(run.control, RunControl)
        run_control_ref.append(run.control)
        iterator = agent.astream(
            {"messages": messages},
            config=config,
            stream_mode=["messages", "updates"],
            control=run.control,
        ).__aiter__()
        acknowledged = False
        saw_tools_update = False
        caught_graph_drained = False
        graph_drained_exception: str | None = None
        model_calls_at_drain_request: list[tuple[str, ...]] = []
        update_keys: list[tuple[str, ...]] = []
        try:
            while True:
                try:
                    mode, chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                if mode == "updates" and isinstance(chunk, dict):
                    update_keys.append(tuple(chunk))
                if mode == "updates" and isinstance(chunk, dict) and "tools" in chunk:
                    saw_tools_update = True
                    model_calls_at_drain_request.append(tuple(model_calls))
                    lease.request_drain(run, "context_compaction")
        except GraphDrained as exc:
            caught_graph_drained = True
            graph_drained_exception = f"{type(exc).__name__}: {exc!r}"
            acknowledged = lease.acknowledge_graph_drained(run, exc)
        finally:
            await iterator.aclose()
            lease.finish_run(run, joined=True)

        model_calls_after_stream = list(model_calls)
        diagnostics = {
            "saw_tools_update": saw_tools_update,
            "update_keys": update_keys,
            "drain_requested": run.control.drain_requested,
            "model_calls_at_drain_request": model_calls_at_drain_request,
            "model_calls_after_stream": model_calls_after_stream,
            "tools_runtime_control_matches_run_control": tool_runtime_control_matches,
            "caught_graph_drained": caught_graph_drained,
            "graph_drained_exception": graph_drained_exception,
            "acknowledge_graph_drained_return": acknowledged,
            "langgraph_runtime_file": langgraph_runtime.__file__,
        }
        diagnostic_context = f"native drain diagnostics: {diagnostics!r}"
        assert asyncio.current_task() is caller_task
        assert saw_tools_update, diagnostic_context
        assert run.control.drain_requested, diagnostic_context
        assert model_calls_at_drain_request == [()], diagnostic_context
        assert model_calls_after_stream == [], diagnostic_context
        assert tool_runtime_control_matches == [True], diagnostic_context
        assert caught_graph_drained, diagnostic_context
        assert acknowledged, diagnostic_context
        assert run.control.drain_requested
        assert run.drain_acknowledged and run.joined
        assert model_calls == []

        drained = await agent.aget_state(config)
        assert tuple(drained.next) == ("model",)
        original = list(drained.values["messages"])
        checkpoint_id = drained.config["configurable"]["checkpoint_id"]
        compacted = [
            HumanMessage(content="synthetic compacted summary", id="s05-summary"),
            *original[-2:],
        ]
        outcome = await _publish_compacted_checkpoint_outcome(
            agent,
            config,
            original,
            compacted,
            writer_lease=lease,
        )

        assert outcome.status == "APPLIED", f"{diagnostic_context}; outcome={outcome!r}"
        assert outcome.execution_config is not None
        published = await agent.aget_state(outcome.execution_config)
        saved = await saver.aget_tuple(outcome.execution_config)
        assert saved is not None
        assert saved.checkpoint["id"] == outcome.execution_config["configurable"]["checkpoint_id"]
        assert (saved.parent_config or {}).get("configurable", {}).get("checkpoint_id") == checkpoint_id
        assert not saved.pending_writes
        assert published.values["messages"] == compacted
        assert tuple(published.next) == ("model",)

        continuation = outcome.execution_config
        resumed_run = lease.start_run(agent, continuation)
        resumed_iterator = agent.astream(
            None,
            config=continuation,
            stream_mode=["messages", "updates"],
            control=resumed_run.control,
        ).__aiter__()
        try:
            async for _mode, _chunk in resumed_iterator:
                pass
        finally:
            await resumed_iterator.aclose()
            lease.finish_run(resumed_run, joined=True)

        assert model_calls == ["model"]
        assert lease.continuation_config is None


@pytest.mark.asyncio
async def test_publisher_refuses_without_lease_and_preserves_checkpoint():
    session_id, request_id = "s05-native-no-lease", "3" * 32
    _binding, config = _binding_config(session_id, request_id)
    model_calls: list[str] = []
    saver = InMemorySaver()
    agent = _graph(saver, model_calls)
    assert mint_graph_provenance(agent)
    await agent.ainvoke({"messages": _history()}, config)
    before = await agent.aget_state(config)
    before_saved = await saver.aget_tuple(config)
    assert before_saved is not None

    original = list(before.values["messages"])
    compacted = [HumanMessage(content="must not publish", id="s05-forbidden-summary")]
    outcome = await _publish_compacted_checkpoint_outcome(
        agent,
        config,
        original,
        compacted,
    )

    after = await agent.aget_state(config)
    after_saved = await saver.aget_tuple(config)
    assert outcome.status == "REFUSED"
    assert outcome.execution_config is None
    assert after.config == before.config
    assert after.values == before.values
    assert after.next == before.next
    assert after_saved is not None
    assert after_saved.checkpoint == before_saved.checkpoint
    assert after_saved.metadata == before_saved.metadata
    assert after_saved.parent_config == before_saved.parent_config
    assert after_saved.pending_writes == before_saved.pending_writes
    assert model_calls == ["model"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "invalid_write"),
    [
        ("other branch channel", ("branch:to:tools", None)),
        ("PUSH Send scheduling", (PUSH, Send("model", {"messages": []}))),
        (
            "nonempty model branch",
            ("branch:to:model", Send("model", {"messages": []})),
        ),
        ("unknown channel", ("unknown:checkpoint:channel", None)),
    ],
    ids=["other-branch", "push-send", "nonempty-model-branch", "unknown-channel"],
)
async def test_publication_saver_rejects_unsupported_writes_without_mutation(
    case_name, invalid_write
):
    session_id = f"s05-native-write-rejection-{case_name.replace(' ', '-')}"
    request_id = "4" * 32
    _binding, config = _binding_config(session_id, request_id)
    saver = InMemorySaver()
    agent = _graph(saver, [])
    assert mint_graph_provenance(agent)
    await agent.ainvoke({"messages": _history()}, config)

    before_state = await agent.aget_state(config)
    before_saved = await saver.aget_tuple(config)
    assert before_saved is not None
    before_checkpoint = deepcopy(before_saved.checkpoint)
    before_metadata = deepcopy(before_saved.metadata)
    before_parent_config = deepcopy(before_saved.parent_config)
    before_pending_writes = deepcopy(before_saved.pending_writes)
    before_values = deepcopy(before_state.values)
    before_next = tuple(before_state.next)
    before_config = deepcopy(before_state.config)

    publication_saver = _PublicationSaver(
        saver,
        before_saved.checkpoint["id"],
    )
    writes = [
        ("messages", [HumanMessage(content="synthetic attempted publication")]),
        invalid_write,
    ]
    with pytest.raises(StaleCompaction, match="Unsupported checkpoint writes"):
        await publication_saver.aput_writes(
            before_state.config,
            writes,
            task_id=f"s05-{case_name.replace(' ', '-')}",
        )

    after_state = await agent.aget_state(config)
    after_saved = await saver.aget_tuple(config)
    assert after_saved is not None
    assert after_saved.checkpoint == before_checkpoint
    assert after_saved.metadata == before_metadata
    assert after_saved.parent_config == before_parent_config
    assert after_saved.pending_writes == before_pending_writes
    assert after_state.values == before_values
    assert tuple(after_state.next) == before_next
    assert after_state.config == before_config


@pytest.mark.asyncio
async def test_native_sqlite_host_archive_publication_and_continuation(
    context_archive_scope, monkeypatch, tmp_path
):
    """Exercise summary, native drain, SQLite save, host receipt, and resume."""
    from app.api.compaction_checkpoint import load_published_context
    from app.api.context_events import EventReference, recover_context_event
    from src.agent.compact.autocompact import autocompact_messages
    from tests.structured_summary_support import structured_response

    class SummaryClient:
        model = "gpt-4o-mini"
        max_tokens = 1024

        def __init__(self):
            self.calls = 0

        async def chat(self, prompt, **_kwargs):
            self.calls += 1
            return SimpleNamespace(content=structured_response(prompt))

    model_calls: list[str] = []
    builder = StateGraph(MessagesState)

    def tools(_state):
        return {"messages": [HumanMessage(content="synthetic local tool update", id="s05-host-tools") ]}

    def model(_state):
        model_calls.append("model")
        return {"messages": [AIMessage(content="synthetic continued response", id="s05-host-model")]}

    builder.add_node("tools", tools)
    builder.add_node("model", model)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", "model")
    builder.add_edge("model", END)
    initial = [
        HumanMessage(content=f"synthetic archive source {index}: " + "x" * 1200, id=f"s05-host-source-{index}")
        for index in range(14)
    ]
    from tests.compaction_v3_source_support import (
        bind_v3_compaction_owner,
        seed_v3_message_sources,
    )
    owner, scope, _initial_source_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, initial,
        request_id=context_archive_scope.scope.request_id,
    )
    _binding, config = _binding_config(scope.session_id, scope.request_id)

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "s05-host-compaction.sqlite")) as saver:
        await saver.setup()
        agent = builder.compile(checkpointer=saver)
        assert mint_graph_provenance(agent)
        lease = acquire_writer_lease(
            agent,
            config,
            business_session_id=scope.session_id,
            request_id=scope.request_id,
            session_epoch=scope.session_epoch,
            memory_epoch=None,
            validate_epochs=lambda: None,
        )

        with bind_writer_lease(lease):
            run = lease.start_run(agent, config)
            iterator = agent.astream(
                {"messages": initial},
                config=config,
                stream_mode=["messages", "updates"],
                control=run.control,
            ).__aiter__()
            saw_tools_update = False
            caught_graph_drained = False
            acknowledged = False
            try:
                while True:
                    try:
                        mode, chunk = await iterator.__anext__()
                    except StopAsyncIteration:
                        break
                    if mode == "updates" and isinstance(chunk, dict) and "tools" in chunk:
                        saw_tools_update = True
                        lease.request_drain(run, "context_compaction")
            except GraphDrained as exc:
                caught_graph_drained = True
                acknowledged = lease.acknowledge_graph_drained(run, exc)
            finally:
                await iterator.aclose()
                lease.finish_run(run, joined=True)

            assert saw_tools_update
            assert run.control.drain_requested
            assert caught_graph_drained and acknowledged
            assert run.drain_acknowledged and run.joined
            assert model_calls == []

            drained = await agent.aget_state(config)
            assert tuple(drained.next) == ("model",)
            checkpoint_id = drained.config["configurable"]["checkpoint_id"]
            original = list(drained.values["messages"])
            original_before_summary = deepcopy(original)
            summary_client = SummaryClient()
            source_owner, source_scope, source_message_ids = seed_v3_message_sources(
                context_archive_scope, monkeypatch, original, transient_tail=True,
                request_id=scope.request_id,
            )
            assert source_owner == owner and source_scope == scope
            with bind_v3_compaction_owner(owner, scope):
                compacted = await autocompact_messages(
                    original, summary_client, context_window=1,
                    source_message_ids=source_message_ids,
                )
            assert compacted is not original
            assert summary_client.calls > 0
            assert original == original_before_summary
            assert len(compacted) == 11
            assert compacted[1:] == original[-10:]
            references = compacted[0].additional_kwargs["hwrag_context_event_refs"]
            assert references
            assert len(references) == len(original) - 10
            for index, reference in enumerate(references):
                recovered = recover_context_event(
                    scope,
                    EventReference(**reference),
                    authorized=True,
                    byte_budget=4096,
                )
                assert recovered is not None
                assert recovered == {"role": "user", "content": original[index].content}

            with bind_v3_compaction_owner(owner, scope):
                outcome = await _publish_compacted_checkpoint_outcome(
                    agent,
                    config,
                    original,
                    compacted,
                    writer_lease=lease,
                    host_context_scope=scope,
                    skills_mode="off",
                )
            assert outcome.status == "APPLIED", f"native SQLite publication outcome: {outcome!r}"
            assert outcome.receipt_status == "CONFIRMED"
            assert outcome.applied_config is not None
            assert outcome.execution_config is not None

            saved = await saver.aget_tuple(outcome.execution_config)
            assert saved is not None
            assert saved.checkpoint["id"] == outcome.execution_config["configurable"]["checkpoint_id"]
            assert (saved.parent_config or {}).get("configurable", {}).get("checkpoint_id") == checkpoint_id
            assert not saved.pending_writes
            published = await agent.aget_state(outcome.execution_config)
            assert published.values["messages"] == compacted
            assert tuple(published.next) == ("model",)

            with bind_v3_compaction_owner(owner, scope):
                replacement = load_published_context(scope, original, skills_mode="off")
            assert replacement is not None
            assert replacement.head.revision == 1
            assert replacement.checkpoint.content == compacted[0].content
            assert replacement.checkpoint.additional_kwargs["hwrag_context_event_refs"] == references
            assert replacement.tail == tuple(original[replacement.prefix_count:])

            continuation = outcome.execution_config
            resumed_run = lease.start_run(agent, continuation)
            resumed_iterator = agent.astream(
                None,
                config=continuation,
                stream_mode=["messages", "updates"],
                control=resumed_run.control,
            ).__aiter__()
            try:
                async for _mode, _chunk in resumed_iterator:
                    pass
            finally:
                await resumed_iterator.aclose()
                lease.finish_run(resumed_run, joined=True)
            assert model_calls == ["model"]
            assert lease.continuation_config is None


@pytest.mark.skip(
    reason="legacy unbound pre-send unit path is superseded by the V3 source-bound "
    "pre-send tests; client success/failure is covered by autocompact_input_budget "
    "and compaction generation tests",
)
@pytest.mark.asyncio
async def test_pre_send_compaction_replaces_only_after_valid_in_budget_summary(
    context_archive_scope, monkeypatch
):
    from app.api import chat_routes
    from src.agent.request_budget import AgentRequestBudgetError
    from tests.structured_summary_support import structured_response

    class SummaryClient:
        model = "gpt-4o-mini"
        max_tokens = 1024

        def __init__(self):
            self.calls = 0

        async def chat(self, prompt, **_kwargs):
            self.calls += 1
            return SimpleNamespace(content=structured_response(prompt))

    class EmptySummaryClient(SummaryClient):
        async def chat(self, _prompt, **_kwargs):
            self.calls += 1
            return SimpleNamespace(content="   ")

    class FailingSummaryClient(SummaryClient):
        async def chat(self, _prompt, **_kwargs):
            self.calls += 1
            raise RuntimeError("synthetic local summary failure")

    def make_input():
        return {
            "messages": [
                {"role": "user", "content": f"synthetic early record {i}: " + "x" * 4200}
                for i in range(4)
            ] + [
                {"role": "user", "content": f"synthetic recent {i}"}
                for i in range(10)
            ]
        }

    async def run_case(client, context_window):
        from langchain_core.messages import convert_to_messages
        from src.agent.compact.autocompact import should_autocompact

        payload = chat_routes.ChatRequest(
            session_id=context_archive_scope.scope.session_id,
            messages=[chat_routes.ChatMessageSchema(role="user", content="synthetic current question")],
        )
        monkeypatch.setattr(chat_routes, "_session_context_window", lambda _session_id, _model=None: context_window)
        monkeypatch.setattr(chat_routes, "make_client", lambda *_args, **_kwargs: client)
        agent_input = make_input()
        original_list = agent_input["messages"]
        original_contents = deepcopy(agent_input)
        assert should_autocompact(
            convert_to_messages(original_list), int(context_window * 0.8)
        )
        event_count = 0
        try:
            async for _event in chat_routes._check_and_compact_history(payload, {}, agent_input):
                event_count += 1
        except AgentRequestBudgetError:
            assert client.calls > 0
            assert agent_input["messages"] is original_list
            assert agent_input == original_contents
            return False, agent_input, original_list, event_count
        assert client.calls > 0
        return True, agent_input, original_list, event_count

    success_client = SummaryClient()
    success, agent_input, original_list, events = await run_case(success_client, 4096)
    assert success and events == 1
    assert success_client.calls > 0
    assert agent_input["messages"] is not original_list
    assert len(agent_input["messages"]) == 11
    from langchain_core.messages import convert_to_messages
    from src.agent.compact.autocompact import should_autocompact
    assert not should_autocompact(convert_to_messages(agent_input["messages"]), int(4096 * 0.8))

    for client, window in (
        (FailingSummaryClient(), 4096),
        (EmptySummaryClient(), 4096),
        (SummaryClient(), 1),
    ):
        success, failed_input, original_list, events = await run_case(client, window)
        assert not success and events == 1
        assert failed_input["messages"] is original_list


def test_quarantined_writer_owner_cannot_be_replaced_by_same_session_graph():
    from src.agent.writer_lifecycle import WriterLifecycleError

    session_id = "s05-quarantined-owner"
    _binding, old_config = _binding_config(session_id, "5" * 32)
    old_graph = _graph(InMemorySaver(), [])
    assert mint_graph_provenance(old_graph)
    old_lease = acquire_writer_lease(
        old_graph,
        old_config,
        business_session_id=session_id,
        request_id="5" * 32,
        session_epoch="a" * 64,
        memory_epoch=None,
        validate_epochs=lambda: None,
    )
    old_lease.quarantine()

    _binding, new_config = _binding_config(session_id, "6" * 32)
    new_graph = _graph(InMemorySaver(), [])
    assert mint_graph_provenance(new_graph)
    with pytest.raises(WriterLifecycleError, match="Quarantined graph owner"):
        acquire_writer_lease(
            new_graph,
            new_config,
            business_session_id=session_id,
            request_id="6" * 32,
            session_epoch="a" * 64,
            memory_epoch=None,
            validate_epochs=lambda: None,
        )


@pytest.mark.asyncio
async def test_ordinary_context_limit_is_terminal_without_last_question_fallback(
    context_archive_scope, monkeypatch
):
    import json
    from starlette.requests import Request
    from app.api import chat_routes
    from src.agent.exceptions import ContextLimitError

    fallback_calls: list[str] = []

    class NoFallbackClient:
        async def chat_stream(self, **_kwargs):
            fallback_calls.append("called")
            yield SimpleNamespace(type="text", content="must not fallback")

    async def failing_agent_stream(*_args, **_kwargs):
        raise ContextLimitError(cumulative=100, limit=80)
        yield

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
    monkeypatch.setattr(
        chat_routes,
        "resolve_credentials",
        lambda *_args: {"api_key": "", "base_url": "", "model": "synthetic", "provider": ""},
    )
    monkeypatch.setattr(chat_routes, "_process_attachments", lambda _payload: _empty_attachments())
    monkeypatch.setattr(chat_routes, "_build_chat_history", lambda _messages: ([], "synthetic question"))
    monkeypatch.setattr(chat_routes, "_record_token_usage", lambda **_kwargs: None)
    monkeypatch.setattr(chat_routes, "make_client", lambda *_args, **_kwargs: NoFallbackClient())
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: True)
    monkeypatch.setattr(chat_routes, "_run_agent_stream", failing_agent_stream)
    payload = chat_routes.ChatRequest(
        session_id=context_archive_scope.scope.session_id,
        messages=[chat_routes.ChatMessageSchema(role="user", content="synthetic question")],
        use_agent=True,
    )
    request = Request({
        "type": "http", "method": "POST", "path": "/api/chat",
        "headers": [], "query_string": b"",
    })

    response = await chat_routes.chat_sse(payload, request, user={})
    frames = [frame async for frame in response.body_iterator]
    events = [
        json.loads(line[6:])
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]
    assert any(event.get("type") == "error" and event.get("code") == "CONTEXT_LIMIT" for event in events)
    done = [event for event in events if event.get("type") == "done"]
    assert done and done[-1].get("success") is False
    assert fallback_calls == []


async def _empty_attachments():
    return [], []


@pytest.mark.asyncio
async def test_multi_pending_pause_receipt_and_single_call_confirmation(tmp_path):
    import json
    from collections import Counter
    from src.agent.exceptions import ToolContext
    from src.agent.mcp_authorization import MCPRequestAuthorizations
    from src.agent.request_context import activate_tool_context

    session_id, request_id = "s05-multi-pending", "7" * 32
    _binding, config = _binding_config(session_id, request_id)
    calls = [
        {"call_id": "s05-call-one", "name": "synthetic_one", "args": {"value": 1}},
        {"call_id": "s05-call-two", "name": "synthetic_two", "args": {"value": 2}},
    ]
    builder = StateGraph(MessagesState)
    builder.add_node("tools", lambda _state: {})
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)

    async with AsyncSqliteSaver.from_conn_string(
        str(tmp_path / "s05-multi-pending.sqlite")
    ) as saver:
        await saver.setup()
        agent = builder.compile(checkpointer=saver, interrupt_before=["tools"])
        assert mint_graph_provenance(agent)
        lease = acquire_writer_lease(
            agent,
            config,
            business_session_id=session_id,
            request_id=request_id,
            session_epoch="a" * 64,
            memory_epoch=None,
            validate_epochs=lambda: None,
        )
        with bind_writer_lease(lease):
            run = lease.start_run(agent, config)
            iterator = agent.astream(
                {"messages": [AIMessage(content="", tool_calls=[
                    {"name": call["name"], "args": call["args"], "id": call["call_id"]}
                    for call in calls
                ])]},
                config=config,
                stream_mode=["updates"],
                control=run.control,
            ).__aiter__()
            try:
                async for _mode, _chunk in iterator:
                    pass
            finally:
                await iterator.aclose()
                lease.finish_run(run, joined=True)

            from src.agent import hitl_handler

            pending = await hitl_handler._detect_tools_interrupt(agent, config)
            assert [call["call_id"] for call in pending] == [call["call_id"] for call in calls]

            class Spec:
                def __init__(self, name):
                    self.name = name
                    self.mcp_info = {"server_name": "synthetic", "tool_name": name}
                    self.risk_level = SimpleNamespace(value="high")

            specs = [Spec(call["name"]) for call in calls]
            spec_map = {spec.name: spec for spec in specs}
            authorizations = MCPRequestAuthorizations(specs)
            ctx = ToolContext(session_id=session_id, permission_mode="default")
            ctx.request_tools = spec_map
            ctx.mcp_authorization = authorizations
            with activate_tool_context(ctx):
                confirmation_events = [
                    event async for event in hitl_handler.handle_auto_resume(
                        agent, config, "default", Counter(), {}, {}
                    )
                ]

            assert lease.pause_receipt is not None
            assert tuple(item[0] for item in lease.pause_receipt.pending_call_fingerprints) == tuple(
                call["call_id"] for call in calls
            )
            assert len(confirmation_events) == 1
            frame = confirmation_events[0]
            event = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
            assert event["count"] == 1
            assert [item["call_id"] for item in event["calls"]] == [calls[0]["call_id"]]
            with activate_tool_context(ctx):
                assert authorizations.validate_confirmation(calls[0]["call_id"], pending)
                assert not authorizations.validate_confirmation(calls[1]["call_id"], pending)
                authorizations.resolve_confirmation(calls[0]["call_id"], "allow")
                assert authorizations.dispatch_decision(
                    calls[0]["call_id"], spec_map[calls[0]["name"]], calls[0]["args"]
                ) == (True, "user_allow")
                assert authorizations.dispatch_decision(
                    calls[1]["call_id"], spec_map[calls[1]["name"]], calls[1]["args"]
                ) == (False, "mcp_confirmation_required")
                from src.agent.core.toolkit.permission_classifier import PermissionClassifier

                classifier = PermissionClassifier()
                with pytest.raises(ValueError, match="already executed"):
                    authorizations.next_confirmation(pending, spec_map, classifier, ctx)
                next_call = authorizations.next_confirmation(
                    pending[1:], spec_map, classifier, ctx
                )
                assert next_call == calls[1]


@pytest.mark.asyncio
async def test_resume_context_limit_maps_to_failed_terminal_event(
    context_archive_scope, monkeypatch
):
    import json
    from collections import Counter
    from dataclasses import replace
    from app.api import chat_routes
    from src.agent.exceptions import ContextLimitError
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry

    session_id = context_archive_scope.scope.session_id
    request_id = context_archive_scope.scope.request_id
    _binding, config = _binding_config(session_id, request_id)
    request = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            session_id=session_id,
            messages=[chat_routes.ChatMessageSchema(role="user", content="synthetic question")]
        ),
        decision="allow",
    )
    call_id = "s05-resume-context-limit-call"
    pending = [{"call_id": call_id, "name": "synthetic_tool", "args": {"value": 1}}]
    tool_context = ToolContext(
        session_id=session_id,
        rag_source_registry=RagSourceRegistry(),
    )
    snapshot = replace(
        chat_routes._request_snapshot_from_payload(request.payload, ""),
        business_session_id=session_id,
        principal_hash="d" * 64,
        request_id=request_id,
        graph_config=config,
        tool_context=tool_context,
        mcp_snapshot_bound=True,
    )
    stored = chat_routes._store_pending_request_snapshot(session_id, snapshot, pending)
    assert stored is not None
    claim = chat_routes._claim_pending_request_snapshot("d" * 64, session_id, call_id)
    assert claim is not None

    class PausedAgent:
        async def aget_state(self, _config):
            return SimpleNamespace(
                next=("tools",),
                values={
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[{
                                "name": "synthetic_tool",
                                "args": {"value": 1},
                                "id": call_id,
                            }],
                        )
                    ]
                },
                config=config,
            )

    async def fail_resume(*_args, **_kwargs):
        raise ContextLimitError(cumulative=100, limit=80)
        yield  # Make this an async generator with a failure before any event.

    monkeypatch.setattr(chat_routes, "_resume_in_context", fail_resume)
    ctx = chat_routes.ResumeContext(
        agent=PausedAgent(),
        config=config,
        req=request,
        call_counter=Counter(),
        model="synthetic-local",
        request_snapshot=stored,
        claim=claim,
    )
    frames = [frame async for frame in chat_routes._resume_event_generator_from_ctx(ctx)]
    events = [
        json.loads(line[6:])
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]
    assert any(event.get("type") == "error" and event.get("code") == "CONTEXT_LIMIT" for event in events)
    assert [event for event in events if event.get("type") == "done"] == [
        {"type": "done", "success": False, "completed": False}
    ]


@pytest.mark.asyncio
async def test_sse_adapter_native_compaction_and_sqlite_continuation(
    context_archive_scope, tmp_path, monkeypatch
):
    import json
    from collections import Counter

    from app.api.compaction_checkpoint import load_published_context
    from app.api.context_events import EventReference, recover_context_event
    from app.db.models import Session as DbSession
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from src.agent.compact import autocompact as autocompact_module
    from src.agent.context_guard import accumulate_tokens, reset_token_counter
    from src.agent.sse_adapter import _iter_agent_sse, _new_compaction_source_sidecar
    from src.agent.sse_helpers import init_stream_state
    from tests.structured_summary_support import structured_response
    from tests.compaction_v3_source_support import bind_v3_compaction_owner, seed_v3_message_sources

    class SummaryClient:
        model = "gpt-4o-mini"
        max_tokens = 1024

        def __init__(self):
            self.calls = 0

        async def chat(self, prompt, **_kwargs):
            self.calls += 1
            return SimpleNamespace(content=structured_response(prompt))

    archive_scope = context_archive_scope.scope
    call_id = "s05-adapter-tool-call"
    human_history = [
        HumanMessage(
            content=(f"synthetic archived source {index}: " + "x" * 2600)
            if index < 6 else f"synthetic recent tail {index}",
            id=f"s05-adapter-source-{index}",
        )
        for index in range(14)
    ]
    initial = [
        *human_history,
        AIMessage(
            content="",
            id="s05-adapter-tool-request",
            tool_calls=[{"name": "synthetic_tool", "args": {"value": 1}, "id": call_id}],
        ),
    ]
    source_owner, source_scope, persisted_source_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, human_history, request_id=archive_scope.request_id,
    )
    assert source_scope.session_id != archive_scope.session_id
    scope = source_scope
    with context_archive_scope.factory.begin() as db:
        session = db.get(DbSession, scope.session_id)
        assert session is not None
        session.context_window = 8192

    _binding, config = _binding_config(scope.session_id, scope.request_id)
    # The initial tool-call message and the later tool result are transient tail
    # entries; persisted user messages retain their real owner-bound V3 IDs.
    source_message_ids = (*persisted_source_ids, None)
    tools_seen: list[str] = []
    model_calls: list[str] = []
    builder = StateGraph(MessagesState)

    def tools_node(state):
        tool_calls = state["messages"][-1].tool_calls
        assert len(tool_calls) == 1 and tool_calls[0]["id"] == call_id
        tools_seen.append(tool_calls[0]["id"])
        return {
            "messages": [
                ToolMessage(
                    content="synthetic paired tool result",
                    tool_call_id=call_id,
                    id="s05-adapter-tool-result",
                )
            ]
        }

    def model_node(_state):
        model_calls.append("model")
        return {"messages": [AIMessage(content="synthetic local model answer", id="s05-adapter-answer")]}

    builder.add_node("tools", tools_node)
    builder.add_node("model", model_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", "model")
    builder.add_edge("model", END)

    summary_client = SummaryClient()
    monkeypatch.setattr(autocompact_module, "_make_summary_client", lambda: summary_client)
    async with AsyncSqliteSaver.from_conn_string(
        str(tmp_path / "s05-adapter-compaction.sqlite")
    ) as saver:
        await saver.setup()
        agent = builder.compile(checkpointer=saver)
        assert mint_graph_provenance(agent)
        lease = acquire_writer_lease(
            agent,
            config,
            business_session_id=scope.session_id,
            request_id=scope.request_id,
            session_epoch=scope.session_epoch,
            memory_epoch=None,
            validate_epochs=lambda: None,
        )
        state = init_stream_state(config, "gpt-4o-mini")
        assert state["context_window"] == 8192
        assert state["token_limit"] > 0
        state.update({
            "agent": agent,
            "config": config,
            "permission_mode": "bypassPermissions",
            "compaction_source_sidecar": _new_compaction_source_sidecar(
                source_message_ids, initial,
            ),
        })
        reset_token_counter()
        accumulate_tokens("\n".join(message.content for message in human_history), state)
        caller_task = asyncio.current_task()

        with bind_writer_lease(lease), bind_v3_compaction_owner(source_owner, source_scope):
            events = [
                event async for event in _iter_agent_sse(
                    agent,
                    {"messages": initial},
                    config,
                    Counter(),
                    {},
                    state,
                )
            ]

        assert asyncio.current_task() is caller_task
        assert state.get("compaction_status") == "APPLIED"
        assert state.get("needs_compact") is False
        assert summary_client.calls == 1
        assert tools_seen == [call_id]
        assert model_calls == ["model"]

        payloads = [
            json.loads(line[6:])
            for event in events
            for line in event.splitlines()
            if line.startswith("data: ")
        ]
        assert any(item.get("type") == "context_compressing" for item in payloads)

        compaction_config = state.get("config")
        assert isinstance(compaction_config, dict)
        compacted = await agent.aget_state(compaction_config)
        assert tuple(compacted.next) == ("model",)
        compacted_tuple = await saver.aget_tuple(compacted.config)
        assert compacted_tuple is not None
        assert compacted_tuple.checkpoint["id"] == compaction_config["configurable"]["checkpoint_id"]
        assert compacted_tuple.parent_config is not None
        drained = await agent.aget_state(compacted_tuple.parent_config)
        drained_messages = list(drained.values["messages"])
        assert tuple(drained.next) == ("model",)
        assert drained_messages == [*initial, ToolMessage(
            content="synthetic paired tool result",
            tool_call_id=call_id,
            id="s05-adapter-tool-result",
        )]
        assert compacted_tuple.parent_config["configurable"]["checkpoint_id"] == drained.config[
            "configurable"
        ]["checkpoint_id"]

        compacted_messages = list(compacted.values["messages"])
        assert len(compacted_messages) == 11
        assert compacted_messages[1:] == drained_messages[-10:]
        references = compacted_messages[0].additional_kwargs["hwrag_context_event_refs"]
        assert len(references) == 6
        for index, reference in enumerate(references):
            recovered = recover_context_event(
                scope,
                EventReference(**reference),
                authorized=True,
                byte_budget=4096,
            )
            assert recovered == {"role": "user", "content": human_history[index].content}

        with bind_v3_compaction_owner(source_owner, scope):
            replacement = load_published_context(scope, human_history, skills_mode="off")
        assert replacement is not None
        assert replacement.head.revision == 1
        assert replacement.prefix_count == 6
        assert replacement.checkpoint.content == compacted_messages[0].content
        assert replacement.checkpoint.additional_kwargs["hwrag_context_event_refs"] == references
        assert replacement.tail == tuple(human_history[replacement.prefix_count:])

        final = await agent.aget_state(config)
        assert final.values["messages"][-1].content == "synthetic local model answer"
        assert not final.next
        final_tuple = await saver.aget_tuple(final.config)
        assert final_tuple is not None
        assert (final_tuple.parent_config or {}).get("configurable", {}).get("checkpoint_id") == compacted.config[
            "configurable"
        ]["checkpoint_id"]
