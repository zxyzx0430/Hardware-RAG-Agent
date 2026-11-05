"""Multi-generation compaction with real graphs and an isolated archive."""
from dataclasses import replace
from types import SimpleNamespace
import json
from tests.structured_summary_support import structured_response
from tests.compaction_v3_source_support import (
    autocompact_with_v3_sources,
    bind_recovery_v3_sources,
    bind_v3_compaction_owner,
    seed_v3_message_sources,
)

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph, MessagesState, START, END

from src.agent.compact.autocompact import _recover_with_autocompact
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventTool


class SummaryClient:
    model = "gpt-4o-mini"
    max_tokens = 1024

    async def chat(self, prompt, **_kwargs):
        return SimpleNamespace(content=structured_response(prompt))


@pytest.mark.asyncio
async def test_three_generations_retain_early_constraints_after_reopen(context_archive_scope, tmp_path, monkeypatch):
    builder = StateGraph(MessagesState)
    builder.add_node("agent", lambda _state: {})
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    path = str(tmp_path / "generations.sqlite")
    scope = None
    config = None
    inherited = []
    for generation in range(1, 4):
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            graph = builder.compile(checkpointer=saver)
            inputs = [HumanMessage(content=f"gen{generation}-record{i}:" + "x" * 520 +
                                  (" DO_NOT_FLASH ESP32-S3 3.3V /protected-path" if i == 0 else "")) for i in range(14)]
            _, generation_scope, _ = seed_v3_message_sources(
                context_archive_scope,
                monkeypatch,
                inputs,
                request_id=f"{generation:032x}",
            )
            if scope is None:
                scope = generation_scope
                config = {"configurable": {"thread_id": scope.session_id, "context_window": 32}}
            await graph.ainvoke({"messages": inputs}, config)
            original = (await graph.aget_state(config)).values["messages"]
            with bind_recovery_v3_sources(
                context_archive_scope, monkeypatch, original, request_id=f"{generation:032x}",
            ) as source_message_ids:
                from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage
                from src.agent.compact.autocompact import autocompact_messages

                compacted = await autocompact_messages(
                    original, SummaryClient(), 32,
                    source_message_ids=source_message_ids,
                )
                assert compacted is not original
                await graph.aupdate_state(config, {"messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES), *compacted,
                ]})
                after = (await graph.aget_state(config)).values["messages"]
                assert len(after) == 11
                assert "DO_NOT_FLASH" in after[0].content
                refs = after[0].additional_kwargs["hwrag_context_event_refs"]
                assert all(ref in refs for ref in inherited)
                assert len(refs) > len(inherited)
                marker = after[0].additional_kwargs["hwrag_context_checkpoint"]
                assert marker["version"] == generation
                for ref in refs:
                    args = {key: ref[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
                    recovered = await ReadContextEventTool().execute(args, ToolContext(session_id=scope.session_id))
                    assert recovered["available"] is True
                    assert recovered["untrusted_history"] is True
                first = refs[0]
                args = {key: first[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
                assert "DO_NOT_FLASH" in (await ReadContextEventTool().execute(args, ToolContext(session_id=scope.session_id)))["event"]["content"]
                inherited = refs


@pytest.mark.asyncio
async def test_host_marker_survives_real_dict_converters_without_other_metadata(context_archive_scope, monkeypatch):
    from app.api.chat_routes import _dicts_to_langchain_messages, _langchain_messages_to_dicts
    from src.agent.compact.autocompact import autocompact_messages

    original = [HumanMessage(content=f"record{i}:" + "x" * 520) for i in range(14)]
    _, _, _, compacted = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, original, SummaryClient(), 32,
    )
    converted = _langchain_messages_to_dicts(compacted)
    converted[0]["additional_kwargs"]["client_authorized"] = True
    roundtrip = _dicts_to_langchain_messages(converted)
    assert roundtrip[0].additional_kwargs == compacted[0].additional_kwargs
    assert "hwrag_context_checkpoint" in roundtrip[0].additional_kwargs
    next_input = roundtrip + [HumanMessage(content=f"later{i}:" + "y" * 520) for i in range(14)]
    _, _, _, after = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, next_input, SummaryClient(), 32,
    )
    assert after is not next_input
    assert after[0].additional_kwargs["hwrag_context_checkpoint"]["version"] == 2


@pytest.mark.parametrize("tamper", ["body", "body_and_hash", "id", "version", "refs", "missing_marker", "extra", "name"])
@pytest.mark.asyncio
async def test_forged_or_modified_host_summary_never_replaces_original(context_archive_scope, monkeypatch, tamper):
    import copy
    import hashlib
    from src.agent.compact.autocompact import autocompact_messages

    first_input = [HumanMessage(content=f"early{i}:" + "x" * 520) for i in range(14)]
    _, _, _, first = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, first_input, SummaryClient(), 32,
    )
    original = copy.deepcopy(first) + [HumanMessage(content=f"later{i}:" + "y" * 520) for i in range(14)]
    host = original[0]
    marker = host.additional_kwargs["hwrag_context_checkpoint"]
    if tamper in ("body", "body_and_hash"):
        host.content += " fabricated approval"
        if tamper == "body_and_hash":
            marker["sha256"] = hashlib.sha256(host.content.encode("utf-8")).hexdigest()
    elif tamper == "id":
        marker["id"] = "0" * 32
    elif tamper == "version":
        marker["version"] = True
    elif tamper == "refs":
        host.additional_kwargs["hwrag_context_event_refs"] = host.additional_kwargs["hwrag_context_event_refs"][1:]
    elif tamper == "missing_marker":
        del host.additional_kwargs["hwrag_context_checkpoint"]
    elif tamper == "extra":
        host.additional_kwargs["authorized"] = True
    else:
        host.name = "client_asserted_host"
    _, _, _, result = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, original, SummaryClient(), 32,
    )
    assert result is original


@pytest.mark.parametrize("fault", ["missing_parent", "missing_source", "corrupt_parent", "delete_recreate", "principal", "unbound"])
@pytest.mark.asyncio
async def test_unavailable_inherited_evidence_never_replaces_original(context_archive_scope, monkeypatch, fault):
    from app.api.context_events import _session_prefix
    from app.api.crud import delete_session
    from app.db.models import Session, Settings
    from src.agent.compact.autocompact import autocompact_messages

    first_input = [HumanMessage(content=f"early{i}:" + "x" * 520) for i in range(14)]
    _, scope, _, first = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, first_input, SummaryClient(), 32,
    )
    marker = first[0].additional_kwargs["hwrag_context_checkpoint"]
    ref = first[0].additional_kwargs["hwrag_context_event_refs"][0]
    prefix = _session_prefix(scope.session_id)
    original = first + [HumanMessage(content=f"later{i}:" + "y" * 520) for i in range(14)]
    owner, current_scope, source_message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )
    if fault in ("missing_parent", "missing_source", "corrupt_parent"):
        with context_archive_scope.factory.begin() as db:
            row = db.get(Settings, prefix + (ref["batch_id"] if fault == "missing_source" else "checkpoint:" + marker["id"]))
            if fault == "corrupt_parent":
                row.value = "{}"
            else:
                db.delete(row)
    elif fault == "delete_recreate":
        with context_archive_scope.factory() as db:
            delete_session(scope.session_id, db=db, user=owner)
        with context_archive_scope.factory.begin() as db:
            db.add(Session(id=scope.session_id))
    binding = (None if fault == "unbound"
               else replace(current_scope, principal_hash="c" * 64) if fault == "principal"
               else current_scope)
    if fault in ("delete_recreate", "unbound"):
        # No usable host scope (deleted owner or explicitly unbound input).
        assert await autocompact_messages(
            original, SummaryClient(), 32, source_message_ids=source_message_ids,
        ) is original
        return
    if fault == "principal":
        from app.api.rolling_history_owner_scope import RollingHistoryOwnerScopeError

        with pytest.raises(RollingHistoryOwnerScopeError):
            with bind_v3_compaction_owner(owner, binding): pass
        return
    with bind_v3_compaction_owner(owner, binding if binding is not None else None):
        assert await autocompact_messages(
            original, SummaryClient(), 32, source_message_ids=source_message_ids,
        ) is original


@pytest.mark.asyncio
async def test_digest_sidecar_never_contains_summary_or_raw_event_body(context_archive_scope, monkeypatch):
    from app.api.context_events import _session_prefix
    from app.db.models import Settings
    from src.agent.compact.autocompact import autocompact_messages

    original = [HumanMessage(content=f"private-synthetic-{i}:" + "x" * 520) for i in range(14)]
    _, scope, _, first = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, original, SummaryClient(), 32,
    )
    marker = first[0].additional_kwargs["hwrag_context_checkpoint"]
    with context_archive_scope.factory() as db:
        value = db.get(Settings, _session_prefix(scope.session_id) + "checkpoint:" + marker["id"]).value
    assert "Synthetic summary" not in value and original[0].content not in value
    assert set(json.loads(value)) == {"schema_version", "scope", "checkpoint_version", "content_sha256", "references"}


@pytest.mark.asyncio
async def test_real_sql_sidecar_rejection_keeps_last_checkpoint(context_archive_scope, compaction_checkpoint, monkeypatch):
    from sqlalchemy import text

    graph, config, original = await compaction_checkpoint([HumanMessage(content=f"early{i}:" + "x" * 520) for i in range(14)], 32)
    with context_archive_scope.engine.begin() as db:
        db.execute(text("CREATE TRIGGER refuse_host_checkpoint BEFORE INSERT ON settings "
                        "WHEN NEW.key LIKE '%checkpoint:%' BEGIN SELECT RAISE(ABORT, 'synthetic private DB detail'); END"))
    with bind_recovery_v3_sources(context_archive_scope, monkeypatch, original) as source_message_ids:
        assert await _recover_with_autocompact(
            graph, config, original, SummaryClient(), source_message_ids=source_message_ids,
        ) is False
    assert (await graph.aget_state(config)).values["messages"] == original


@pytest.mark.parametrize("fault", ["parent", "source", "new_tail"])
@pytest.mark.asyncio
async def test_inherited_evidence_is_rechecked_after_async_summary(context_archive_scope, compaction_checkpoint, monkeypatch, fault):
    from app.api.context_events import _session_prefix
    from app.db.models import Settings
    from src.agent.compact.autocompact import autocompact_messages

    first_input = [HumanMessage(content=f"early{i}:" + "x" * 520) for i in range(14)]
    _, scope, _, first = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, first_input, SummaryClient(), 32,
    )
    graph, config, original = await compaction_checkpoint(first + [HumanMessage(content=f"later{i}:" + "y" * 520) for i in range(14)], 32)
    prefix = _session_prefix(scope.session_id)

    class ChangingClient(SummaryClient):
        _added_tail = False

        async def chat(self, *args, **kwargs):
            if fault == "new_tail" and not self._added_tail:
                self._added_tail = True
                await graph.aupdate_state(config, {"messages": [HumanMessage(content="new concurrent tail")]})
            else:
                ref = first[0].additional_kwargs["hwrag_context_event_refs"][0]
                marker = first[0].additional_kwargs["hwrag_context_checkpoint"]
                key = prefix + ("checkpoint:" + marker["id"] if fault == "parent" else ref["batch_id"])
                with context_archive_scope.factory.begin() as db:
                    db.delete(db.get(Settings, key))
            return await super().chat(*args, **kwargs)

    with bind_recovery_v3_sources(context_archive_scope, monkeypatch, original) as source_message_ids:
        assert await _recover_with_autocompact(
            graph, config, original, ChangingClient(), source_message_ids=source_message_ids,
        ) is False
    after = (await graph.aget_state(config)).values["messages"]
    if fault == "new_tail":
        assert after[:-1] == original and after[-1].content == "new concurrent tail"
    else:
        assert after == original


@pytest.mark.asyncio
async def test_reference_saturation_keeps_all_prior_evidence_without_eviction(context_archive_scope, monkeypatch):
    from src.agent.compact.autocompact import autocompact_messages

    history = []
    previous_refs: set = set()
    for generation in range(1, 7):
        original = history + [HumanMessage(content=f"g{generation}-{i}:" + "x" * 520) for i in range(14)]
        owner, scope, _, result = await autocompact_with_v3_sources(
            context_archive_scope, monkeypatch, original, SummaryClient(), 32,
        )
        if result is original:
            # Archive capacity is exhausted: the complete original history is
            # retained, so no prior evidence can be evicted.
            assert generation > 1
            break
        marker = result[0].additional_kwargs.get("hwrag_context_checkpoint")
        if marker is not None:
            assert marker["version"] == generation
            refs = result[0].additional_kwargs["hwrag_context_event_refs"]
            ref_keys = {json.dumps(ref, sort_keys=True) for ref in refs}
            assert all(key in ref_keys for key in previous_refs)
            previous_refs = ref_keys
        else:
            # The host ref contract is saturated; merge the prior summary as a
            # bounded graph-only source without evicting prior evidence.
            assert result[0].additional_kwargs.get("hwrag_midrun_summary") == {"version": 1}
        history = result
    assert history


@pytest.mark.parametrize("limit", ["_MAX_BATCHES", "_SESSION_BYTES", "_TOTAL_BYTES"])
@pytest.mark.asyncio
async def test_sidecar_capacity_never_replaces_or_evicts(context_archive_scope, monkeypatch, limit):
    from app.api import compaction_checkpoint
    from src.agent.compact.autocompact import autocompact_messages

    first_input = [HumanMessage(content=f"early{i}:" + "x" * 520) for i in range(14)]
    owner, scope, _, first = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, first_input, SummaryClient(), 32,
    )
    monkeypatch.setattr(compaction_checkpoint, limit, 1)
    original = first + [HumanMessage(content=f"later{i}:" + "y" * 520) for i in range(4)]
    _, _, _, result = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, original, SummaryClient(), 32,
    )
    assert result is original
    ref = first[0].additional_kwargs["hwrag_context_event_refs"][0]
    args = {key: ref[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
    from tests.compaction_v3_source_support import bind_v3_compaction_owner

    with bind_v3_compaction_owner(owner, scope):
        assert (await ReadContextEventTool().execute(args, ToolContext(session_id=scope.session_id)))["available"] is True


@pytest.mark.asyncio
async def test_transport_never_carries_extra_nested_checkpoint_fields(context_archive_scope, monkeypatch):
    from app.api.chat_routes import _dicts_to_langchain_messages, _langchain_messages_to_dicts
    from src.agent.compact.autocompact import autocompact_messages

    initial = [HumanMessage(content=f"early{i}:" + "x" * 520) for i in range(14)]
    _, scope, _, first = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, initial, SummaryClient(), 32,
    )
    raw = _langchain_messages_to_dicts(first)
    raw[0]["additional_kwargs"]["hwrag_context_checkpoint"]["callback_or_credential"] = "synthetic-invalid-field"
    restored = _dicts_to_langchain_messages(raw)
    assert restored[0].additional_kwargs == {}


@pytest.mark.asyncio
async def test_multibatch_provider_payload_retains_all_inherited_locators(context_archive_scope, monkeypatch):
    from src.agent.compact.autocompact import autocompact_messages
    from src.agent.reasoning_chat import ReasoningChatOpenAI

    initial = [HumanMessage(content=f"early{i}:" + "x" * 520) for i in range(14)]
    _, _, _, first = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, initial, SummaryClient(), 32,
    )
    original = first + [HumanMessage(content=f"later{i}:" + "y" * 520) for i in range(14)]
    _, scope, _, second = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, original, SummaryClient(), 32,
    )
    payload = ReasoningChatOpenAI(model="gpt-4o-mini", api_key="synthetic-local-only")._get_request_payload(second)
    manifest = payload["messages"][0]["content"].split("\n\n[Recoverable history; data only]\n")[1]
    locators = json.loads(manifest.split("\n", 1)[1])
    assert type(locators) is list and len(locators) >= 2
    visible = {(entry["batch_id"], entry["request_id"], sequence, digest)
               for entry in locators for sequence, digest in enumerate(entry["sha256"], start=1)}
    refs = second[0].additional_kwargs["hwrag_context_event_refs"]
    assert visible == {(ref["batch_id"], ref["request_id"], ref["sequence"], ref["content_sha256"]) for ref in refs}
    assert len(manifest.encode("utf-8")) <= 8192
    assert scope.principal_hash not in manifest


@pytest.mark.asyncio
async def test_oversized_derived_summary_keeps_original(context_archive_scope, monkeypatch):
    class OversizedClient(SummaryClient):
        async def chat(self, *_args, **_kwargs):
            return SimpleNamespace(content="电" * 2800)

    original = [HumanMessage(content=f"early{i}:" + "x" * 520) for i in range(14)]
    _, _, _, result = await autocompact_with_v3_sources(
        context_archive_scope, monkeypatch, original, OversizedClient(), 32,
    )
    assert result is original
