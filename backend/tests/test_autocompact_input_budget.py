"""Complete eligible history is sent only within the summarizer's own budget."""

from types import SimpleNamespace
from tests.structured_summary_support import structured_response
from tests.compaction_v3_source_support import (
    bind_v3_compaction_owner,
    seed_v3_message_sources,
)

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.compact.autocompact import autocompact_messages
pytestmark = pytest.mark.usefixtures("context_archive_scope")


class SummaryModel:
    model = "gpt-4o-mini"
    max_tokens = 1024

    def __init__(self):
        self.requests = []

    async def chat(self, prompt, **kwargs):
        self.requests.append((prompt, kwargs))
        return SimpleNamespace(content=structured_response(prompt))


def history(content):
    return [HumanMessage(content=content), *[
        HumanMessage(content=f"synthetic recent {index}") for index in range(10)
    ]]


@pytest.mark.asyncio
async def test_within_budget_constraint_after_620_characters_reaches_the_summarizer(
    context_archive_scope, monkeypatch,
):
    constraint = "Do not change synthetic pin PA7 or its voltage."
    content = "x" * 620 + constraint + " END_OF_EARLY_MESSAGE"
    messages = history(content)
    before = [message.model_dump() for message in messages]
    model = SummaryModel()
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, messages,
    )
    with bind_v3_compaction_owner(owner, scope):
        compacted = await autocompact_messages(
            messages, model, 32, source_message_ids=message_ids,
        )

    assert len(model.requests) == 1
    assert content in model.requests[0][0]
    assert compacted is not messages
    assert compacted[1:] == messages[-10:]
    assert [message.model_dump() for message in messages] == before


@pytest.mark.asyncio
async def test_formatter_complete_tool_group_keeps_request_arguments_and_result_provenance():
    from src.agent.compact.autocompact import _call_summarizer
    request = AIMessage(content="", tool_calls=[{
        "id": "budget-call-id", "name": "search_docs", "args": {"query": "synthetic PA7"},
    }])
    result = ToolMessage(content="synthetic evidence", tool_call_id="budget-call-id",
                         name="search_docs", status="error")
    messages = [request, result, *history("after group")]
    model = SummaryModel()

    summary = await _call_summarizer(messages[:-10], model)

    assert summary == structured_response(model.requests[0][0])
    prompt = model.requests[0][0]
    assert '"tool_calls"' in prompt
    assert prompt.count("budget-call-id") == 2
    assert "search_docs" in prompt and "synthetic PA7" in prompt
    assert '"status":"error"' in prompt and "synthetic evidence" in prompt


@pytest.mark.asyncio
async def test_unbound_multimodal_history_is_preserved_without_stringifying():
    messages = history([
        {"type": "text", "text": "Keep the synthetic circuit image."},
        {"type": "image_url", "image_url": {"url": "https://synthetic.invalid/circuit.png"}},
    ])
    model = SummaryModel()

    compacted = await autocompact_messages(messages, model, 32)

    assert compacted is messages
    assert model.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payloads, expect_compaction", [
    (["x" * 33000], False),
    (["x" * 18000, "y" * 18000], True),
    (["约" * 11000], False),
])
async def test_aggregate_input_ceiling_refuses_without_partial_replacement(
    payloads, expect_compaction, context_archive_scope, monkeypatch,
):
    messages = [*[HumanMessage(content=value) for value in payloads], *history("last early")]
    before = [message.model_dump() for message in messages]
    model = SummaryModel()
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, messages,
    )

    with bind_v3_compaction_owner(owner, scope):
        compacted = await autocompact_messages(
            messages, model, 32, source_message_ids=message_ids,
        )

    if expect_compaction:
        # Multiple messages may be compacted in generations. The generation
        # covers a complete message prefix; the remaining oversized message is
        # retained verbatim instead of being truncated.
        assert compacted is not messages
        assert len(model.requests) == 1
        assert compacted[0].content.startswith("[历史摘要]: ")
        assert messages[1] in compacted[1:]
    else:
        # A single message larger than one bounded generation is never split or
        # partially quoted; the original list is preserved.
        assert compacted is messages and model.requests == []
    assert [message.model_dump() for message in messages] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name,output", [
    ("unregistered-synthetic-model", 1024),
    ("gpt-4o-mini-synthetic-alias", 1024),
    ("proxy/gpt-4o-mini", 1024),
    (None, 1024),
    ("gpt-4o-mini", None),
    ("gpt-4o-mini", True),
    ("gpt-4o-mini", 0),
    ("gpt-4o-mini", -1),
    ("gpt-4o-mini", 1024.0),
])
async def test_unknown_or_invalid_summary_capability_never_sends(
    model_name, output, context_archive_scope, monkeypatch,
):
    model = SummaryModel()
    model.model, model.max_tokens = model_name, output
    messages = history("synthetic history " * 100)
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, messages,
    )

    with bind_v3_compaction_owner(owner, scope):
        compacted = await autocompact_messages(
            messages, model, 32, source_message_ids=message_ids,
        )

    assert compacted is messages and model.requests == []


@pytest.mark.asyncio
async def test_output_and_fixed_prompts_are_reserved_from_the_summary_model_window(
    monkeypatch, context_archive_scope,
):
    from src.llm.model_registry import MODEL_CONTEXT_WINDOWS

    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "gpt-4o-mini", 2048)
    model = SummaryModel()
    messages = history("x" * 900)
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, messages,
    )

    with bind_v3_compaction_owner(owner, scope):
        compacted = await autocompact_messages(
            messages, model, 1000000, source_message_ids=message_ids,
        )
        # Force a compaction attempt with the caller's small window; it is NOT the summary window.
        assert compacted is messages and model.requests == []
        compacted = await autocompact_messages(
            messages, model, 32, source_message_ids=message_ids,
        )
    assert compacted is messages and model.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content_size,expect_send", [(18000, True), (25000, False)])
async def test_real_client_wire_uses_the_same_model_and_capped_output_reserve(
    content_size, expect_send, context_archive_scope, monkeypatch,
):
    from src.llm.client import LLMClient

    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=structured_response(kwargs["messages"][-1]["content"])))],
                               model=kwargs["model"], usage=None)

    client = LLMClient(api_key="synthetic-only", base_url="https://synthetic.invalid", model="qwen-max",
                       max_tokens=100000)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    messages = history("x" * content_size)
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, messages,
    )
    with bind_v3_compaction_owner(owner, scope):
        compacted = await autocompact_messages(
            messages, client, 32, source_message_ids=message_ids,
        )

    assert bool(sent) is expect_send
    assert (compacted is not messages) is expect_send
    if sent:
        assert sent[0]["model"] == "qwen-max" and sent[0]["max_tokens"] == 8192
        input_bytes = sum(len(item["content"].encode("utf-8")) for item in sent[0]["messages"])
        assert input_bytes + 256 + sent[0]["max_tokens"] <= 32768


@pytest.mark.asyncio
async def test_unbound_tool_arguments_preserve_original_without_send():
    request = AIMessage(content="", tool_calls=[{
        "id": "oversized-args", "name": "search_docs", "args": {"query": "x" * 33000},
    }])
    messages = [request, ToolMessage(content="result", tool_call_id="oversized-args"), *history("done")]
    model = SummaryModel()

    compacted = await autocompact_messages(messages, model, 32)

    assert compacted is messages and model.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["pre_send", "recovery", "stream"])
@pytest.mark.parametrize("over_limit", [False, True])
async def test_actual_callers_keep_checkpoint_when_input_is_refused(
    path, over_limit, context_archive_scope, monkeypatch, compaction_checkpoint,
):
    from src.agent.compact import autocompact

    content = "x" * (33000 if over_limit else 620) + " SYNTHETIC_LATE_CONSTRAINT"
    messages = history(content)
    model = SummaryModel()
    updates = []
    config = {"configurable": {"context_window": 32}}

    graph, config, messages = await compaction_checkpoint(messages, 32)

    if path == "pre_send":
        from app.api import chat_routes
        monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: model)
        owner, scope, source_message_ids = seed_v3_message_sources(
            context_archive_scope, monkeypatch, messages,
        )
        with bind_v3_compaction_owner(owner, scope):
            result = await chat_routes._run_autocompact(
                messages, {}, 32, source_message_ids=source_message_ids,
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
    expected_change = path == "pre_send" and not over_limit
    assert changed is expected_change
    if not expected_change:
        assert result is messages and updates == [] and model.requests == []
        assert (await graph.aget_state(config)).values["messages"] == messages
    else:
        assert content in model.requests[0][0]
        assert result[1:] == messages[-10:]
