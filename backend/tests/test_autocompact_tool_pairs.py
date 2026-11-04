"""Successful compaction must preserve request/result protocol groups."""

from types import SimpleNamespace
from tests.structured_summary_support import structured_response

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agent.compact.autocompact import AUTOCOMPACT_KEEP_RECENT_MSGS, autocompact_messages
pytestmark = pytest.mark.usefixtures("context_archive_scope")


class RecordingModel:
    model = "gpt-4o-mini"
    max_tokens = 1024

    def __init__(self):
        self.prompts = []

    async def chat(self, prompt, **_kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(content=structured_response(prompt))


def call(*ids):
    return AIMessage(content="", tool_calls=[
        {"id": value, "name": "search_docs", "args": {"query": "synthetic"}} for value in ids
    ])


def tail(count=9):
    return [HumanMessage(content=f"synthetic tail {index}") for index in range(count)]


def early(count=2):
    return [HumanMessage(content="synthetic context " * 100) for _ in range(count)]


def assert_balanced(messages):
    pending = set()
    for message in messages:
        if isinstance(message, AIMessage):
            pending.update(item["id"] for item in message.tool_calls)
        elif isinstance(message, ToolMessage):
            assert message.tool_call_id in pending, "compaction created an orphan tool result"
            pending.remove(message.tool_call_id)
    return pending


@pytest.mark.asyncio
async def test_unbound_tool_history_preserves_call_and_result_verbatim():
    request = call("pair-a")
    result = ToolMessage(content="synthetic evidence", tool_call_id="pair-a")
    messages = [*early(), request, result, *tail()]
    before = [item.model_dump() for item in messages]
    model = RecordingModel()

    compacted = await autocompact_messages(messages, model, 32)

    assert compacted is messages
    assert assert_balanced(compacted) == set()
    assert request in compacted and result in compacted
    assert compacted[-9:] == messages[-9:]
    assert [item.model_dump() for item in messages] == before


@pytest.mark.asyncio
async def test_pending_call_at_start_leaves_no_safe_history_to_summarize():
    messages = [call("still-pending"), *tail(12)]
    model = RecordingModel()

    compacted = await autocompact_messages(messages, model, 32)

    assert compacted is messages
    assert assert_balanced(compacted) == {"still-pending"}
    assert model.prompts == []


@pytest.mark.asyncio
async def test_unbound_parallel_tool_history_preserves_the_whole_request_group():
    request = call("parallel-a", "parallel-b")
    results = [ToolMessage(content="evidence B", tool_call_id="parallel-b"),
               ToolMessage(content="evidence A", tool_call_id="parallel-a")]
    messages = [*early(), request, *results, *tail()]

    compacted = await autocompact_messages(messages, RecordingModel(), 32)

    assert compacted is messages
    assert assert_balanced(compacted) == set()
    assert request in compacted and all(result in compacted for result in results)


@pytest.mark.asyncio
async def test_completed_tool_group_before_the_cut_is_preserved_without_safe_archive():
    messages = [*early(1), call("complete-a", "complete-b"),
                ToolMessage(content="complete evidence A", tool_call_id="complete-a"),
                ToolMessage(content="complete evidence B", tool_call_id="complete-b"),
                HumanMessage(content="group is complete"), *tail(10)]
    model = RecordingModel()

    compacted = await autocompact_messages(messages, model, 32)

    assert compacted is messages
    assert assert_balanced(compacted) == set()
    assert model.prompts == []
    from src.agent.compact.autocompact import _format_early_for_summary
    formatted = _format_early_for_summary(messages[:-10], 32768)
    assert "complete evidence A" in formatted and "complete evidence B" in formatted


@pytest.mark.asyncio
async def test_unbound_pending_group_after_eligible_history_is_retained_verbatim():
    request = call("pending-with-history")
    messages = [*early(1), request, *tail(12)]
    before = request.model_dump()

    compacted = await autocompact_messages(messages, RecordingModel(), 32)

    assert compacted is messages
    assert assert_balanced(compacted) == {"pending-with-history"}
    assert request.model_dump() == before


@pytest.mark.asyncio
async def test_unparsed_tool_request_is_not_summarized_as_completed_history():
    request = AIMessage(content="", invalid_tool_calls=[{
        "id": "unparsed-call", "name": "search_docs", "args": "{invalid", "error": "synthetic parse error",
    }])
    messages = [request, *tail(12)]
    model = RecordingModel()

    compacted = await autocompact_messages(messages, model, 32)

    assert compacted is messages
    assert model.prompts == []


@pytest.mark.asyncio
async def test_ambiguous_duplicate_call_ids_are_not_partially_consumed():
    request = call("duplicate", "duplicate")
    results = [ToolMessage(content=f"duplicate evidence {index}", tool_call_id="duplicate") for index in range(2)]
    messages = [*early(), request, *results, *tail()]

    compacted = await autocompact_messages(messages, RecordingModel(), 32)

    assert compacted is messages
    assert request in compacted
    assert all(result in compacted for result in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["pre_send", "recovery", "stream"])
@pytest.mark.parametrize("no_safe_prefix", [False, True])
async def test_actual_compaction_callers_fail_closed_without_source_ids(path, no_safe_prefix, monkeypatch, compaction_checkpoint):
    from src.agent.compact import autocompact

    model = RecordingModel()
    request = call("caller-pair")
    messages = [request, *tail(12)] if no_safe_prefix else [
        *early(), request, ToolMessage(content="caller evidence", tool_call_id="caller-pair"), *tail(),
    ]
    config = {"configurable": {"context_window": 32}}
    updates = []

    graph, config, messages = await compaction_checkpoint(messages, 32)

    if path == "pre_send":
        from app.api import chat_routes
        monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: model)
        result = await chat_routes._run_autocompact(
            messages, {}, 32, source_message_ids=(None,) * len(messages),
        )
        changed = result is not messages
    elif path == "recovery":
        changed = await autocompact._recover_with_autocompact(graph, config, messages, model)
        result = (await graph.aget_state(config)).values["messages"] if changed else messages
    else:
        from src.agent import sse_adapter
        monkeypatch.setattr(autocompact, "_make_summary_client", lambda: model)
        changed = await sse_adapter._compact_and_apply({"agent": graph, "config": config}, messages, 32)
        result = (await graph.aget_state(config)).values["messages"] if changed else messages
    assert changed is False
    assert result is messages and updates == []
    assert assert_balanced(result) == assert_balanced(messages)
    assert request in result
    assert (await graph.aget_state(config)).values["messages"] == messages


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted_count", [12, 13])
async def test_owner_bound_compaction_keeps_transient_tool_and_system_suffix_verbatim(
    context_archive_scope, monkeypatch, persisted_count,
):
    from tests.compaction_v3_source_support import (
        bind_v3_compaction_owner,
        seed_v3_message_sources,
    )

    persisted_history = [
        (HumanMessage(content=f"persisted user {index}: " + "x" * 520)
         if index % 2 == 0 else AIMessage(content=f"persisted assistant {index}: " + "y" * 520))
        for index in range(persisted_count)
    ]
    owner, scope, persisted_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, persisted_history,
    )
    assert all(type(message_id) is str and message_id for message_id in persisted_ids)
    assert len(set(persisted_ids)) == len(persisted_ids)
    call_id = "transient-runtime-tool-pair"
    tool_call = AIMessage(
        content="",
        id="transient-runtime-tool-call",
        tool_calls=[{
            "id": call_id,
            "name": "search_docs",
            "args": {"query": "DO_NOT_SUMMARIZE_TOOL_CALL_7C9F"},
        }],
    )
    tool_result = ToolMessage(
        content="DO_NOT_SUMMARIZE_TOOL_RESULT_2A61",
        tool_call_id=call_id,
        id="transient-runtime-tool-result",
    )
    quoted_context = SystemMessage(content="DO_NOT_SUMMARIZE_QUOTED_CONTEXT_9D14")
    runtime_tail = [
        HumanMessage(content="DO_NOT_SUMMARIZE_RUNTIME_TAIL_51D3"),
        quoted_context,
        HumanMessage(content="latest transient user turn"),
    ]
    messages = [*persisted_history, tool_call, tool_result, *runtime_tail]
    source_message_ids = persisted_ids + (None,) * (len(messages) - len(persisted_ids))
    from src.agent.sse_adapter import _new_compaction_source_sidecar
    sidecar = _new_compaction_source_sidecar(source_message_ids, messages)
    assert sidecar.message_ids == source_message_ids
    before_call = tool_call.model_dump()
    before_result = tool_result.model_dump()
    model = RecordingModel()
    transient_count = len(messages) - persisted_count
    early_count = persisted_count - max(
        0, AUTOCOMPACT_KEEP_RECENT_MSGS - transient_count,
    )

    with bind_v3_compaction_owner(owner, scope):
        compacted = await autocompact_messages(
            messages, model, 32, source_message_ids=source_message_ids,
        )

    assert compacted is not messages
    assert len(model.prompts) == 1
    assert persisted_history[early_count - 1].content in model.prompts[0]
    assert persisted_history[early_count].content not in model.prompts[0]
    assert all(marker not in model.prompts[0] for marker in (
        "DO_NOT_SUMMARIZE_TOOL_CALL_7C9F",
        "DO_NOT_SUMMARIZE_TOOL_RESULT_2A61",
        "DO_NOT_SUMMARIZE_RUNTIME_TAIL_51D3",
        "DO_NOT_SUMMARIZE_QUOTED_CONTEXT_9D14",
    ))
    assert compacted[1:] == messages[early_count:]
    assert all(actual is expected for actual, expected in zip(
        compacted[1:], messages[early_count:], strict=True,
    ))
    assert compacted[-2] is quoted_context
    assert compacted[-1] is runtime_tail[-1]
    assert tool_call.model_dump() == before_call
    assert tool_result.model_dump() == before_result
    assert tool_call in compacted and tool_result in compacted
    assert assert_balanced(compacted) == set()
