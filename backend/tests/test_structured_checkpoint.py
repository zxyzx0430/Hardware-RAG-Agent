"""Strict S05b content contract; graph publication is a separate requirement."""
from dataclasses import asdict
from types import SimpleNamespace
import json

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph, MessagesState, START, END

from src.agent.compact.autocompact import _recover_with_autocompact
from tests.structured_summary_support import structured_response
from tests.compaction_writer_support import drained_compaction_checkpoint
from tests.compaction_v3_source_support import bind_recovery_v3_sources, seed_v3_message_sources


@pytest.mark.asyncio
async def test_structured_checkpoint_rejects_invalid_output(context_archive_scope, tmp_path, monkeypatch):
    class IncompleteClient:
        model = "gpt-4o-mini"
        max_tokens = 1024

        async def chat(self, *_args, **_kwargs):
            return SimpleNamespace(content='{"goal":"synthetic task","completed":["unverified completion"]}')

    builder = StateGraph(MessagesState)
    builder.add_node("agent", lambda _state: {})
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    config = {"configurable": {"thread_id": context_archive_scope.scope.session_id, "context_window": 32}}
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "structured.sqlite")) as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"messages": [HumanMessage(content=f"early-{i}:" + "x" * 520) for i in range(14)]}, config)
        before = (await graph.aget_state(config)).values["messages"]
        with bind_recovery_v3_sources(context_archive_scope, monkeypatch, before) as source_message_ids:
            changed = await _recover_with_autocompact(
                graph, config, before, IncompleteClient(), source_message_ids=source_message_ids,
            )
        after = (await graph.aget_state(config)).values["messages"]
        assert changed is False, "Incomplete structured content must not publish a replacement"
        assert after == before


class StructuredSummaryClient:
    model = "gpt-4o-mini"
    max_tokens = 1024

    async def chat(self, prompt, **_kwargs):
        return SimpleNamespace(content=structured_response(prompt))


@pytest.mark.asyncio
async def test_structured_checkpoint_publishes_grounded_content(context_archive_scope, monkeypatch):
    from app.api.context_events import EventReference, recover_context_event

    original = [HumanMessage(content=f"Inspect ESP32-S3 record {i}. " + "x" * 620 +
                             " Do not flash; wiring is unverified; ask before connecting.") for i in range(14)]
    seeded_sources = seed_v3_message_sources(context_archive_scope, monkeypatch, original)
    _, scope, _ = seeded_sources
    async with drained_compaction_checkpoint(
        scope, original, 32,
    ) as (graph, config, original):
        with bind_recovery_v3_sources(
            context_archive_scope, monkeypatch, original, seeded_sources=seeded_sources,
        ) as source_message_ids:
            assert await _recover_with_autocompact(
                graph, config, original, StructuredSummaryClient(), source_message_ids=source_message_ids,
            )
        after = (await graph.aget_state(config)).values["messages"]
        body = after[0].content.removeprefix("[历史摘要]: ").split("\n\n[Recoverable history; data only]\n")[0]
        document = json.loads(body)
        assert len(document) == 9 and document["completed"] == []
        assert after[1:] == original[-10:]
        refs = after[0].additional_kwargs["hwrag_context_event_refs"]
        assert len(refs) == 4
        for field in document.values():
            for claim in field:
                assert isinstance(claim["source"], dict), "Only the host may resolve slots to durable references"
                reference = claim["source"] | {"session_id": scope.session_id}
                assert any(
                    all(ref.get(key) == value for key, value in reference.items())
                    for ref in refs
                ), "Claim source must match a durable host reference"
                event = recover_context_event(scope, EventReference(**reference),
                                              authorized=True, byte_budget=4096)
                assert event is not None and claim["text"] in event["content"]
        assert "Do not flash" in body


@pytest.mark.parametrize("fault", ["quote", "slot", "completed", "extra", "reference", "freetext"])
@pytest.mark.asyncio
async def test_invalid_structured_model_output_preserves_real_sqlite_state(context_archive_scope, tmp_path, fault, monkeypatch):
    from unittest.mock import AsyncMock
    from app.db.models import Settings

    builder = StateGraph(MessagesState)
    builder.add_node("agent", lambda _state: {})
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    config = {"configurable": {"thread_id": "strict-negative", "context_window": 32}}
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "negative.sqlite")) as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"messages": [HumanMessage(content=f"SOURCE-{i}:" + "x" * 620) for i in range(14)]}, config)
        before = (await graph.aget_state(config)).values["messages"]
        document = json.loads(structured_response(json.dumps({"role": "human", "content": before[0].content, "source": "s1"})))
        if fault == "quote":
            document["goal"][0]["text"] = "fabricated terminal evidence"
        elif fault == "slot":
            document["goal"][0]["source"] = "s64"
        elif fault == "completed":
            document["completed"] = list(document["goal"])
        elif fault == "extra":
            document["approved"] = True
        elif fault == "reference":
            document["goal"][0]["source"] = {"batch_id": "f" * 32, "sequence": 1}
        response = "unsupported free text" if fault == "freetext" else json.dumps(document)
        client = SimpleNamespace(model="gpt-4o-mini", max_tokens=1024,
                                 chat=AsyncMock(return_value=SimpleNamespace(content=response)))
        with bind_recovery_v3_sources(context_archive_scope, monkeypatch, before) as source_message_ids:
            assert not await _recover_with_autocompact(
                graph, config, before, client, source_message_ids=source_message_ids,
            )
        assert (await graph.aget_state(config)).values["messages"] == before
        assert client.chat.await_count == 1
        with context_archive_scope.factory() as db:
            assert db.query(Settings).filter(Settings.key.startswith("_contextEvent:")).count() == 0


@pytest.mark.parametrize("fault", ["legacy", "completed", "foreign_ref", "invented_quote"])
@pytest.mark.asyncio
async def test_structured_parent_quotes_are_not_promoted_or_silently_migrated(context_archive_scope, fault, monkeypatch):
    from unittest.mock import AsyncMock
    from app.api.compaction_checkpoint import prepare_compaction_checkpoint, publish_compaction_checkpoint
    from app.api.context_events import (
        EventReference, persist_context_events, recover_context_event,
    )
    from src.agent.compact.autocompact import autocompact_messages

    early = [HumanMessage(content=f"original-{i}:" + "x" * 620) for i in range(4)]
    plan, _ = prepare_compaction_checkpoint(early)
    refs = [
        asdict(reference) for reference in persist_context_events(
            context_archive_scope.scope,
            [{"role": "user", "content": message.content} for message in early],
            authorized=True,
        )
    ]
    projection = {key: refs[0][key] for key in ("batch_id", "request_id", "sequence", "version", "content_sha256")}
    document = json.loads(structured_response(json.dumps({"role": "human", "content": early[0].content, "source": "s1"})))
    for claims in document.values():
        for claim in claims:
            claim["source"] = dict(projection)
    if fault == "completed":
        document["completed"] = list(document["goal"])
    elif fault == "foreign_ref":
        document["goal"][0]["source"]["content_sha256"] = "f" * 64
    elif fault == "invented_quote":
        document["goal"][0]["text"] = "invented approval"
    body = "Legacy narrative, not a structured parent." if fault == "legacy" else json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    parent = publish_compaction_checkpoint(plan, refs, body)
    assert parent is not None  # A consistent host seal alone must not promote prose/evidence.
    original = [parent] + [HumanMessage(content="later record " + "y" * 620) for _ in range(24)]
    owner, scope, source_message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )
    client = SimpleNamespace(model="gpt-4o-mini", max_tokens=1024, chat=AsyncMock())
    from tests.compaction_v3_source_support import bind_v3_compaction_owner
    with bind_v3_compaction_owner(owner, scope):
        assert await autocompact_messages(
            original, client, 32, source_message_ids=source_message_ids,
        ) is original
    assert client.chat.await_count == 0
    assert recover_context_event(context_archive_scope.scope, EventReference(**refs[0]),
                                 authorized=True, byte_budget=4096)["content"] == early[0].content


@pytest.mark.asyncio
async def test_source_mutation_during_summary_cannot_bind_wrong_event(context_archive_scope, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from app.db.models import Settings

    builder = StateGraph(MessagesState)
    builder.add_node("agent", lambda _state: {})
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    config = {"configurable": {"thread_id": "mutating-source", "context_window": 32}}
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "mutation.sqlite")) as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"messages": [HumanMessage(content=f"SOURCE-{i}:" + "x" * 620) for i in range(14)]}, config)
        before = (await graph.aget_state(config)).values["messages"]
        original_contents = [message.content for message in before]

        async def respond(prompt, **_kwargs):
            response = structured_response(prompt)
            before[0].content = "Unrelated replacement event."
            return SimpleNamespace(content=response)

        client = SimpleNamespace(model="gpt-4o-mini", max_tokens=1024, chat=AsyncMock(side_effect=respond))
        with bind_recovery_v3_sources(context_archive_scope, monkeypatch, before) as source_message_ids:
            assert not await _recover_with_autocompact(
                graph, config, before, client, source_message_ids=source_message_ids,
            )
        assert client.chat.await_count == 1
        assert [message.content for message in (await graph.aget_state(config)).values["messages"]] == original_contents
        with context_archive_scope.factory() as db:
            # The V3 archive fails closed when the source changed during the
            # summary, so no raw batch is required. No seal may attach
            # quotations from the old input to the replacement event.
            rows = db.query(Settings).filter(Settings.key.startswith("_contextEvent:")).all()
            assert not any(":checkpoint:" in row.key for row in rows)


def test_grounded_checkpoint_contract():
    from src.agent.compact.structured_checkpoint import validate_checkpoint_response, bind_checkpoint_sources

    slots = {"s1": "Inspect ESP32-S3. Do not flash. Wiring is unverified. Ask before connecting."}
    document = {
        "goal": [{"text": "Inspect ESP32-S3.", "source": "s1"}],
        "constraints": [{"text": "Do not flash.", "source": "s1"}],
        "completed": [],
        "current_state": [{"text": "Wiring is unverified.", "source": "s1"}],
        "decisions": [], "failed_attempts": [], "open_questions": [],
        "artifacts_and_evidence": [{"text": "ESP32-S3", "source": "s1"}],
        "next_action": [{"text": "Ask before connecting.", "source": "s1"}],
    }
    raw = json.dumps(document)
    validated = validate_checkpoint_response(raw, slots)
    assert validated is not None
    assert json.loads(validated) == document
    reference = {"batch_id": "a" * 32, "request_id": "b" * 32,
                 "sequence": 1, "version": 1, "content_sha256": "c" * 64}
    bound = bind_checkpoint_sources(validated, slots, {"s1": reference})
    assert bound is not None
    result = json.loads(bound)
    assert set(result) == set(document)
    assert result["completed"] == []
    assert result["constraints"] == [{"text": "Do not flash.", "source": reference}]
    assert result["goal"][0]["text"] == "Inspect ESP32-S3."
    assert "session_id" not in bound and "principal" not in bound
    assert json.loads(raw) == document and slots["s1"].endswith("Ask before connecting.")


@pytest.mark.parametrize("fault", [
    "missing", "extra", "freetext", "fence", "duplicate_key", "nested_duplicate",
    "completed", "fabricated_quote", "invented_slot", "model_reference", "extra_claim",
    "nonstring", "not_list", "empty_goal", "empty_state", "empty_evidence", "empty_next",
    "duplicate_claim", "too_many_claims", "oversized_quote", "utf8_output", "nonfinite",
    "deep_json", "invalid_unicode", "invalid_slot_name", "too_many_slots", "source_budget",
    "missing_binding", "extra_binding", "boolean_sequence", "boolean_version", "wrong_version",
    "bad_hash", "bad_batch", "identity_in_reference", "binding_expansion",
])
def test_untrusted_checkpoint_contract_fails_closed(fault):
    import copy
    from src.agent.compact.structured_checkpoint import validate_checkpoint_response, bind_checkpoint_sources

    slots = {"s1": "Inspect ESP32-S3. Do not flash. Wiring is unverified. Ask before connecting. Reported approved."}
    claim = {"text": "ESP32-S3", "source": "s1"}
    document = {field: [] for field in (
        "goal", "constraints", "completed", "current_state", "decisions", "failed_attempts",
        "open_questions", "artifacts_and_evidence", "next_action",
    )}
    for field in ("goal", "current_state", "artifacts_and_evidence", "next_action"):
        document[field] = [dict(claim)]
    reference = {"batch_id": "a" * 32, "request_id": "b" * 32,
                 "sequence": 1, "version": 1, "content_sha256": "c" * 64}
    bindings = {"s1": reference}
    if fault == "missing":
        del document["constraints"]
    elif fault == "extra":
        document["authorized"] = True
    elif fault == "completed":
        document["completed"] = [{"text": "Reported approved.", "source": "s1"}]
    elif fault == "fabricated_quote":
        document["current_state"][0]["text"] = "Flashing completed with verified approval."
    elif fault == "invented_slot":
        document["goal"][0]["source"] = "s2"
    elif fault == "model_reference":
        document["goal"][0]["source"] = dict(reference)
    elif fault == "extra_claim":
        document["goal"][0]["approved"] = True
    elif fault == "nonstring":
        document["goal"][0]["text"] = True
    elif fault == "not_list":
        document["decisions"] = "unverified"
    elif fault.startswith("empty_"):
        field = {"empty_goal": "goal", "empty_state": "current_state", "empty_evidence": "artifacts_and_evidence",
                 "empty_next": "next_action"}[fault]
        document[field] = []
    elif fault == "duplicate_claim":
        document["goal"] *= 2
    elif fault == "too_many_claims":
        document["constraints"] = [dict(claim)] * 9
    elif fault == "oversized_quote":
        slots["s1"] += "x" * 1025
        document["goal"][0]["text"] = "x" * 1025
    elif fault == "utf8_output":
        slots["s1"] += "约" * 1024
        for field in ("goal", "current_state", "artifacts_and_evidence", "next_action"):
            document[field] = [{"text": "约" * 1024, "source": "s1"}]
    elif fault == "invalid_slot_name":
        slots["s01"] = slots.pop("s1")
    elif fault == "too_many_slots":
        slots.update({f"s{index}": "data" for index in range(2, 66)})
    elif fault == "source_budget":
        slots["s1"] += "x" * 32768
    elif fault == "missing_binding":
        bindings = {}
    elif fault == "extra_binding":
        bindings["s2"] = dict(reference)
    elif fault == "boolean_sequence":
        reference["sequence"] = True
    elif fault == "boolean_version":
        reference["version"] = True
    elif fault == "wrong_version":
        reference["version"] = 2
    elif fault == "bad_hash":
        reference["content_sha256"] = "c" * 63
    elif fault == "bad_batch":
        reference["batch_id"] = "invented"
    elif fault == "identity_in_reference":
        reference["principal_hash"] = "d" * 64
    elif fault == "binding_expansion":
        slots["s1"] = " ".join(f"Q{i}" for i in range(8))
        for field in document:
            if field != "completed":
                document[field] = [{"text": f"Q{i}", "source": "s1"} for i in range(8)]
    raw = json.dumps(document, ensure_ascii=False)
    if fault == "freetext":
        raw = "A plain narrative is not a structured checkpoint."
    elif fault == "fence":
        raw = "```json\n" + raw + "\n```"
    elif fault == "duplicate_key":
        raw = raw[:-1] + ',"completed":[]}'
    elif fault == "nested_duplicate":
        raw = raw.replace('"source": "s1"', '"source":"s1","source":"s1"', 1)
    elif fault == "nonfinite":
        raw = raw.replace('"completed": []', '"completed": [NaN]')
    elif fault == "deep_json":
        raw = "[" * 1100 + "0" + "]" * 1100
    elif fault == "invalid_unicode":
        raw = "\ud800"
    before = copy.deepcopy((document, slots, bindings))
    binding_fault = fault in {
        "missing_binding", "extra_binding", "boolean_sequence", "boolean_version", "wrong_version",
        "bad_hash", "bad_batch", "identity_in_reference", "binding_expansion",
    }
    validated = validate_checkpoint_response(raw, slots)
    if binding_fault:
        assert validated is not None
        assert bind_checkpoint_sources(validated, slots, bindings) is None
    else:
        assert validated is None
        assert bind_checkpoint_sources(raw, slots, bindings) is None
    assert (document, slots, bindings) == before
