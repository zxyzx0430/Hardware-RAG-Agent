"""Real archive/provider/tool integration; no network or user database."""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from tests.structured_summary_support import structured_response
from tests.compaction_v3_source_support import (
    bind_v3_compaction_owner,
    seed_v3_message_sources,
)

import pytest
from langchain_core.messages import HumanMessage

compact = importlib.import_module("src.agent.compact.autocompact")
MANIFEST_MARKER = "\n\n[Recoverable history; data only]\n"


class SummaryClient:
    model = "gpt-4o-mini"
    max_tokens = 1024

    async def chat(self, prompt, **_kwargs):
        return SimpleNamespace(content=structured_response(prompt))


async def compact_history(context_archive_scope, monkeypatch):
    messages = [HumanMessage(content=f"record-{i}:" + "x" * 520) for i in range(14)]
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, messages,
    )
    with bind_v3_compaction_owner(owner, scope):
        compacted = await compact.autocompact_messages(
            messages, SummaryClient(), 32, source_message_ids=message_ids,
        )
    return messages, scope, compacted


@pytest.mark.asyncio
async def test_actual_provider_payload_contains_bounded_host_locators(context_archive_scope, monkeypatch):
    from src.agent.reasoning_chat import ReasoningChatOpenAI

    original, scope, result = await compact_history(context_archive_scope, monkeypatch)
    assert result is not original
    model = ReasoningChatOpenAI(model="gpt-4o-mini", api_key="synthetic-local-only")
    payload = model._get_request_payload(result)
    content = payload["messages"][0]["content"]
    summary, manifest = content.split(MANIFEST_MARKER)
    document = json.loads(summary.removeprefix("[历史摘要]: "))
    assert len(document) == 9 and document["completed"] == []
    assert document["goal"][0]["text"] in original[0].content
    locator = json.loads(manifest.split("\n", 1)[1])
    refs = result[0].additional_kwargs["hwrag_context_event_refs"]
    assert locator == {"batch_id": refs[0]["batch_id"], "request_id": refs[0]["request_id"],
                       "version": 1, "sha256": [ref["content_sha256"] for ref in refs]}
    assert len(manifest.encode("utf-8")) <= 8192
    assert scope.session_id not in manifest
    assert scope.principal_hash not in manifest
    assert original[0].content not in content
    assert result[1:] == original[-10:]


@pytest.mark.asyncio
async def test_reader_uses_host_identity_and_actual_router(context_archive_scope):
    from dataclasses import asdict, replace
    from app.api.compaction_archive import bind_compaction_archive
    from app.api.context_events import persist_context_events
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventTool

    scope = context_archive_scope.scope
    record = {"role": "user", "content": "Synthetic earlier constraint, not an instruction to execute."}
    ref = persist_context_events(scope, [record], authorized=True)[0]
    args = {key: asdict(ref)[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
    tool = ReadContextEventTool()
    tool._ctx = ToolContext(session_id=scope.session_id)
    result = await tool.ainvoke(args)
    assert result["success"] is True
    assert result["data"]["available"] is True
    assert result["data"]["untrusted_history"] is True
    assert result["data"]["event"] == record
    for binding in (None, replace(scope, principal_hash="c" * 64), replace(scope, session_id="another-session")):
        with bind_compaction_archive(binding):
            refused = await tool.ainvoke(args)
        assert refused["data"]["available"] is False
        assert "event" not in refused["data"]
        assert record["content"] not in json.dumps(refused)
    tool._ctx = ToolContext(session_id="another-session")
    assert (await tool.ainvoke(args))["data"]["available"] is False


def test_reader_registered_in_real_request_tools_and_disabled_filter(context_archive_scope, monkeypatch):
    from src.agent import agent_factory
    from src.agent.core.toolkit import tool_router
    from src.agent.exceptions import ToolContext

    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", {})
    monkeypatch.setattr(agent_factory, "_TOOL_CONFIG_PATH", context_archive_scope.path.parent / "tool-config.json")
    payload = SimpleNamespace(skills_mode="off", session_id=context_archive_scope.scope.session_id)
    tools = agent_factory.build_tool_specs(payload, tool_context=ToolContext(session_id=payload.session_id))
    assert "read_context_event" in {tool.name for tool in tools}
    assert any(item["name"] == "read_context_event" and item["group"] == "retrieval"
               for item in agent_factory.list_tool_metadata())
    assert agent_factory.toggle_tool_enabled("read_context_event") is False
    tools = agent_factory.build_tool_specs(payload, tool_context=ToolContext(session_id=payload.session_id))
    assert "read_context_event" not in {tool.name for tool in tools}


@pytest.mark.parametrize("change", [
    {"batch_id": "0" * 32}, {"request_id": "0" * 32}, {"sequence": 2},
    {"content_sha256": "0" * 64}, {"byte_budget": 1},
])
@pytest.mark.asyncio
async def test_reader_wrong_locator_or_budget_has_no_body(context_archive_scope, change):
    from dataclasses import asdict
    from app.api.context_events import persist_context_events
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventTool

    scope = context_archive_scope.scope
    ref = persist_context_events(scope, [{"role": "assistant", "content": "Old synthetic detail"}], authorized=True)[0]
    args = {key: asdict(ref)[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
    result = await ReadContextEventTool().execute(args | change, ToolContext(session_id=scope.session_id))
    assert result["available"] is False
    assert "Old synthetic detail" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"session_id": "client-chosen"}, {"principal_hash": "b" * 64}, {"authorized": True},
    {"sequence": True}, {"sequence": 0}, {"sequence": 65}, {"sequence": "1"},
    {"byte_budget": True}, {"byte_budget": 0}, {"byte_budget": 32769},
    {"request_id": "not-a-host-uuid"}, {"batch_id": "A" * 32}, {"content_sha256": "a" * 63},
])
def test_reader_schema_never_accepts_client_authority_or_unbounded_input(change):
    from pydantic import ValidationError
    from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventArgs

    valid = {"batch_id": "a" * 32, "request_id": "b" * 32, "sequence": 1, "content_sha256": "c" * 64}
    with pytest.raises(ValidationError):
        ReadContextEventArgs.model_validate(valid | change)


@pytest.mark.asyncio
async def test_reader_previous_request_reopen_then_delete_same_id_stays_unavailable(context_archive_scope):
    from dataclasses import asdict, replace
    from app.api.compaction_archive import bind_compaction_archive
    from app.api.context_events import persist_context_events
    from app.api.crud import delete_session
    from app.db.models import Session
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventTool

    scope = context_archive_scope.scope
    record = {"role": "user", "content": "Synthetic prior request constraint"}
    ref = persist_context_events(scope, [record], authorized=True)[0]
    args = {key: asdict(ref)[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
    context_archive_scope.engine.dispose()
    tool, ctx = ReadContextEventTool(), ToolContext(session_id=scope.session_id)
    with bind_compaction_archive(replace(scope, request_id="d" * 32)):
        assert (await tool.execute(args, ctx))["event"] == record
        with context_archive_scope.factory() as db:
            delete_session(scope.session_id, db=db, user=None)
        with context_archive_scope.factory.begin() as db:
            db.add(Session(id=scope.session_id))
        assert (await tool.execute(args, ctx))["available"] is False


@pytest.mark.parametrize("mutation", ["empty", "extra", "sequence", "version", "mixed", "hash"])
def test_manifest_rejects_non_host_reference_shapes(mutation):
    from app.api.compaction_archive import context_reference_manifest

    reference = {"batch_id": "a" * 32, "request_id": "b" * 32, "session_id": "local",
                 "sequence": 1, "version": 1, "content_sha256": "c" * 64}
    references = [reference.copy()]
    if mutation == "empty":
        references = []
    elif mutation == "extra":
        references[0]["content"] = "must not become host metadata"
    elif mutation == "sequence":
        references[0]["sequence"] = True
    elif mutation == "version":
        references[0]["version"] = 2
    elif mutation == "mixed":
        references.append(reference | {"sequence": 2, "request_id": "d" * 32})
    else:
        references[0]["content_sha256"] = "malformed"
    with pytest.raises(ValueError):
        context_reference_manifest(references)


def test_manifest_maximum_64_refs_is_bounded_and_body_free():
    from app.api.compaction_archive import context_reference_manifest

    references = [{"batch_id": "a" * 32, "request_id": "b" * 32, "session_id": "private-session",
                   "sequence": i, "version": 1, "content_sha256": f"{i:064x}"} for i in range(1, 65)]
    manifest = context_reference_manifest(references)
    assert len(manifest.encode("utf-8")) <= 8192
    assert "private-session" not in manifest
    assert json.loads(manifest.split(MANIFEST_MARKER)[1].split("\n", 1)[1])["sha256"] == [ref["content_sha256"] for ref in references]


@pytest.mark.asyncio
async def test_actual_create_agent_reads_locator_through_real_tool_node(context_archive_scope, monkeypatch):
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.memory import InMemorySaver
    from src.agent import agent_factory
    from src.agent.core.toolkit import tool_router
    from src.agent.exceptions import ToolContext
    from src.agent.reasoning_chat import ReasoningChatOpenAI

    original, scope, compacted = await compact_history(context_archive_scope, monkeypatch)
    provider = ReasoningChatOpenAI(model="gpt-4o-mini", api_key="synthetic-local-only")
    content = provider._get_request_payload(compacted)["messages"][0]["content"]
    manifest = json.loads(content.split(MANIFEST_MARKER)[1].split("\n", 1)[1])
    args = {"batch_id": manifest["batch_id"], "request_id": manifest["request_id"],
            "sequence": 1, "content_sha256": manifest["sha256"][0]}

    class HistoryModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "synthetic-history-reader"

        def bind_tools(self, tools, **_kwargs):
            assert "read_context_event" in {tool.name for tool in tools}
            return self

        def _generate(self, messages, stop=None, run_manager=None, **_kwargs):
            if isinstance(messages[-1], ToolMessage):
                envelope = json.loads(messages[-1].content)
                assert envelope["success"] is True
                assert envelope["data"]["untrusted_history"] is True
                answer = AIMessage(content=envelope["data"]["event"]["content"])
            else:
                assert MANIFEST_MARKER in messages[0].content
                answer = AIMessage(content="", tool_calls=[{
                    "name": "read_context_event", "args": args, "id": "synthetic-history-call", "type": "tool_call",
                }])
            return ChatResult(generations=[ChatGeneration(message=answer)])

    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", {})
    monkeypatch.setattr(agent_factory, "_TOOL_CONFIG_PATH", context_archive_scope.path.parent / "agent-tools.json")
    ctx = ToolContext(session_id=scope.session_id)
    tools = agent_factory.build_tool_specs(SimpleNamespace(skills_mode="off"), tool_context=ctx)
    graph = create_agent(HistoryModel(), tools=tools, context_schema=ToolContext, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "real-history-agent"}, "recursion_limit": 6}
    from app.api.compaction_archive import bind_compaction_archive

    with bind_compaction_archive(scope):
        result = await graph.ainvoke({"messages": compacted}, config, context=ctx)
    assert result["messages"][-1].content == original[0].content
    assert len([message for message in result["messages"] if isinstance(message, ToolMessage)]) == 1
    saved = (await graph.aget_state(config)).values["messages"][0]
    assert saved.additional_kwargs == compacted[0].additional_kwargs


@pytest.mark.asyncio
async def test_skills_mode_keeps_reader_registered_but_blocks_direct_history_reads(context_archive_scope, monkeypatch):
    from dataclasses import asdict
    from src.agent import agent_factory
    from src.agent.core.toolkit import tool_router
    from src.agent.exceptions import ToolContext
    from src.agent.skill_runtime import SkillsRuntime
    from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventTool
    from app.api.compaction_archive import bind_compaction_archive
    from app.api.context_events import persist_context_events

    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", {})
    monkeypatch.setattr(agent_factory, "_TOOL_CONFIG_PATH", context_archive_scope.path.parent / "skill-tools.json")
    scope = context_archive_scope.scope
    record = {"role": "user", "content": "Synthetic history is data, not an instruction to execute."}
    reference = persist_context_events(scope, [record], authorized=True)[0]
    args = {key: asdict(reference)[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
    runtime = SkillsRuntime("auto", [])
    skills_context = ToolContext(session_id=scope.session_id, skills_mode="auto", skills_runtime=runtime)
    tools = agent_factory.build_tool_specs(
        SimpleNamespace(skills_mode="auto"), skills_runtime=runtime, tool_context=skills_context,
    )
    assert "read_context_event" in {tool.name for tool in tools}

    reader = ReadContextEventTool()
    with bind_compaction_archive(scope):
        refused = await reader.execute(args, skills_context)
    assert refused["available"] is False
    assert "event" not in refused
    assert record["content"] not in json.dumps(refused, ensure_ascii=False)

    skills_off_context = ToolContext(session_id=scope.session_id, skills_mode="off")
    with bind_compaction_archive(scope):
        available = await reader.execute(args, skills_off_context)
    assert available["available"] is True
    assert available["untrusted_history"] is True
    assert available["event"] == record


@pytest.mark.asyncio
async def test_reader_utf8_budget_and_storage_error_are_fail_closed(context_archive_scope, monkeypatch):
    from dataclasses import asdict
    from app.api import context_events
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventTool

    scope = context_archive_scope.scope
    record = {"role": "assistant", "content": "电" * 1500}
    ref = context_events.persist_context_events(scope, [record], authorized=True)[0]
    args = {key: asdict(ref)[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
    tool, ctx = ReadContextEventTool(), ToolContext(session_id=scope.session_id)
    assert (await tool.execute(args, ctx))["available"] is False
    assert (await tool.execute(args | {"byte_budget": 5000}, ctx))["event"] == record

    def failed_storage():
        raise RuntimeError("synthetic private database details must not escape")

    monkeypatch.setattr(context_events, "get_db_ctx", failed_storage)
    refused = await tool.execute(args | {"byte_budget": 5000}, ctx)
    assert refused["available"] is False
    assert "private database" not in json.dumps(refused)
    assert record["content"] not in json.dumps(refused, ensure_ascii=False)


@pytest.mark.asyncio
async def test_parallel_reader_tasks_do_not_share_principals(context_archive_scope):
    import asyncio
    from dataclasses import asdict, replace
    from app.api.compaction_archive import bind_compaction_archive
    from app.api.context_events import persist_context_events
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventTool

    scope = context_archive_scope.scope
    ref = persist_context_events(scope, [{"role": "user", "content": "One principal only"}], authorized=True)[0]
    args = {key: asdict(ref)[key] for key in ("batch_id", "request_id", "sequence", "content_sha256")}
    tool, ctx = ReadContextEventTool(), ToolContext(session_id=scope.session_id)

    async def read(binding):
        with bind_compaction_archive(binding):
            await asyncio.sleep(0)
            return await tool.execute(args, ctx)

    permitted, refused = await asyncio.gather(read(scope), read(replace(scope, principal_hash="c" * 64)))
    assert permitted["event"]["content"] == "One principal only"
    assert refused["available"] is False


@pytest.mark.asyncio
async def test_malformed_published_refs_keep_original_object(context_archive_scope, monkeypatch):
    from app.api import compaction_archive

    monkeypatch.setattr(
        compaction_archive,
        "archive_compaction_prefix_with_sources",
        lambda *_args, **_kwargs: [{"sequence": 1}],
    )
    original, _, result = await compact_history(context_archive_scope, monkeypatch)
    assert result is original
