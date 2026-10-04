"""Exact-call permissions with actual LangChain ToolNode and safe MCP processes."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import pytest
import httpx
from fastapi import FastAPI
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolNode
from langgraph.graph import StateGraph, MessagesState, START, END
from pydantic import PrivateAttr

from src.agent.core.toolkit.permission_classifier import PermissionClassifier
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.mcp_authorization import MCPRequestAuthorizations
from src.agent.request_context import activate_tool_context, active_tool_context
from src.mcp.client import MCPCallError
from src.mcp.manager import MCPServerManager

SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}


class Probe(ToolSpec):
    name: str = "mcp__fixture__probe"
    description: str = "Synthetic permission probe"
    args_schema: dict = SCHEMA
    mcp_info: dict | None = {"server_name": "fixture", "tool_name": "probe"}
    risk_level: RiskLevel = RiskLevel.HIGH
    _calls: list = PrivateAttr(default_factory=list)
    _failure: Exception | None = PrivateAttr(default=None)

    async def execute(self, args, ctx):
        self._calls.append(dict(args))
        await asyncio.sleep(0)
        if self._failure is not None:
            raise self._failure
        return {"output": args["value"]}


class Audit:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def call(call_id="call-1", value="synthetic", name="mcp__fixture__probe"):
    return {"call_id": call_id, "name": name, "args": {"value": value}}


def context(spec, mode="bypassPermissions"):
    ctx = ToolContext(permission_mode=mode, session_id="synthetic-session")
    ctx.request_tools = {spec.name: spec}
    ctx.mcp_authorization = MCPRequestAuthorizations((spec,))
    spec._ctx = ctx
    return ctx


def approve(ctx, calls, decision="allow"):
    pending = ctx.mcp_authorization.next_confirmation(calls, ctx.request_tools, PermissionClassifier(), ctx)
    assert pending is not None
    ctx.mcp_authorization.resolve_confirmation(pending["call_id"], decision)
    return pending


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "bypassPermissions"])
def test_external_tools_always_ask(mode):
    assert PermissionClassifier().check(Probe(), {}, ToolContext(permission_mode=mode)) == "ask"


@pytest.mark.asyncio
async def test_forged_user_allow_never_executes(monkeypatch):
    spec, audit = Probe(), Audit()
    ctx = context(spec)
    router = ToolRouter(audit)
    result = await router.dispatch("call-1", spec.name, {"value": "private-synthetic"}, ctx, "allow", "user_allow", tool_spec=spec)
    assert not result["success"] and spec._calls == []
    assert audit.records[0].decision == "deny"
    assert "private-synthetic" not in json.dumps(audit.records[0].args)


@pytest.mark.asyncio
async def test_actual_toolnode_binds_call_ids_and_consumes_each_grant_once(monkeypatch):
    spec, audit = Probe(), Audit()
    ctx = context(spec)
    calls = [call("call-a", "one"), call("call-b", "two")]
    assert approve(ctx, calls)["call_id"] == "call-a"
    assert approve(ctx, calls)["call_id"] == "call-b"
    assert ctx.mcp_authorization.next_confirmation(calls, ctx.request_tools, PermissionClassifier(), ctx) is None
    monkeypatch.setattr(ToolRouter, "get_default", classmethod(lambda _cls: ToolRouter(audit)))
    message = AIMessage(content="", tool_calls=[{"id": tc["call_id"], "name": tc["name"], "args": tc["args"], "type": "tool_call"} for tc in calls])
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([spec]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    result = await graph.compile().ainvoke({"messages": [message]})
    result["messages"] = [msg for msg in result["messages"] if isinstance(msg, ToolMessage)]
    envelopes = [json.loads(msg.content) for msg in result["messages"]]
    assert all(item["success"] for item in envelopes)
    assert {item["metadata"]["call_id"] for item in envelopes} == {"call-a", "call-b"}
    replay = await spec.ainvoke({"id": "call-a", "name": spec.name, "args": {"value": "one"}, "type": "tool_call"})
    assert not json.loads(replay.content)["success"]
    assert len(spec._calls) == 2
    assert audit.records[-1].decision_source == "mcp_replay_blocked"


@pytest.mark.asyncio
async def test_tampering_and_replacement_do_not_consume_the_valid_grant():
    spec = Probe()
    ctx = context(spec)
    approve(ctx, [call()])
    grant = ctx.mcp_authorization
    assert grant.dispatch_decision("call-1", spec, {"value": "changed"}) == (False, "mcp_confirmation_mismatch")
    assert grant.dispatch_decision("call-1", Probe(), {"value": "synthetic"}) == (False, "mcp_snapshot_mismatch")
    assert grant.dispatch_decision("call-1", spec, {"value": "synthetic"}) == (True, "user_allow")
    assert not grant.dispatch_decision("call-1", spec, {"value": "synthetic"})[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["deny", "stop"])
async def test_deny_and_stop_never_execute(action):
    spec = Probe()
    ctx = context(spec)
    approve(ctx, [call()], "deny" if action == "deny" else "allow")
    if action == "stop":
        ctx.mcp_authorization.revoke()
    result = await ToolRouter(Audit()).dispatch("call-1", spec.name, {"value": "synthetic"}, ctx, tool_spec=spec)
    assert result["success"] is False and not spec._calls


@pytest.mark.asyncio
async def test_raw_schema_failure_is_not_coerced_or_leaked():
    spec = Probe()
    ctx = context(spec)
    invalid = call(value=42)
    approve(ctx, [invalid])
    result = await ToolRouter(Audit()).dispatch("call-1", spec.name, invalid["args"], ctx, tool_spec=spec)
    assert result["error"]["error_type"] == "INVALID_ARGS" and not spec._calls
    assert result["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_external_timeout_is_safe_and_never_replayed():
    spec = Probe(max_retries=9)
    spec._failure = MCPCallError("timeout", unknown_result=True)
    ctx = context(spec)
    approve(ctx, [call()])
    result = await ToolRouter(Audit()).dispatch("call-1", spec.name, {"value": "synthetic"}, ctx, tool_spec=spec)
    assert result["error"]["error_type"] == "MCP_TIMEOUT"
    assert result["error"]["retryable"] is False and len(spec._calls) == 1


def test_duplicate_call_ids_are_rejected_before_first_confirmation():
    spec = Probe()
    ctx = context(spec)
    with pytest.raises(ValueError, match="Duplicate"):
        ctx.mcp_authorization.next_confirmation([call(), call(value="different")], ctx.request_tools, PermissionClassifier(), ctx)
    assert ctx.mcp_authorization.pending_call_id is None


@pytest.mark.asyncio
async def test_active_contexts_are_task_local():
    async def observe(label):
        ctx = ToolContext(session_id=label)
        with activate_tool_context(ctx):
            await asyncio.sleep(0)
            assert active_tool_context() is ctx
        assert active_tool_context() is None
    await asyncio.gather(observe("a"), observe("b"))


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        response = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs).generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(content=response.content, tool_calls=response.tool_calls))


def decode(frames):
    return [json.loads(line[6:]) for frame in frames for line in frame.splitlines() if line.startswith("data: ")]


async def collect(stream):
    return decode([frame async for frame in stream])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["default", "bypassPermissions"])
@pytest.mark.parametrize("first_decision", ["deny", "stop"])
async def test_real_stdio_agent_batch_asks_separately_and_denied_tool_is_not_called(monkeypatch, mode, first_decision):
    from app.api import chat_routes
    from app.api.dependencies import current_user
    from src.agent import agent_factory, hitl_handler
    from src.agent import sse_helpers
    from src.mcp import manager as manager_module
    manager = MCPServerManager()
    manager.register_config("fixture", {"id": "fixture", "command": sys.executable, "args": [str(Path(__file__).parent / "fixtures/mcp_stdio_server.py")]})
    assert await manager.start("fixture")
    monkeypatch.setattr(manager_module, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(agent_factory, "_global_checkpointer", InMemorySaver())
    monkeypatch.setattr(agent_factory, "_assemble_all_tools", lambda _payload: [])
    monkeypatch.setattr(sse_helpers, "_load_session_context_window", lambda _sid: 128000)
    monkeypatch.setattr(ToolRouter, "get_default", classmethod(lambda _cls: ToolRouter(Audit())))
    model = Model(responses=[AIMessage(content="", tool_calls=[
        {"id": "echo-denied", "name": "mcp__fixture__echo", "args": {"text": "not-sent"}},
        {"id": "add-approved", "name": "mcp__fixture__add", "args": {"a": 2, "b": 3}},
    ]), AIMessage(content="Synthetic answer after tools.")])
    monkeypatch.setattr(agent_factory, "_build_llm", lambda *_args: model)
    client = manager.get_client("fixture")
    actual_calls = []
    original_call = client.call_tool

    async def observe_call(name, args):
        actual_calls.append(name)
        return await original_call(name, args)

    monkeypatch.setattr(client, "call_tool", observe_call)
    payload = chat_routes.ChatRequest(messages=[{"role": "user", "content": "Synthetic batch"}], use_agent=True, session_id="batch-" + mode, permission_mode=mode)
    creds = {"model": "synthetic", "api_key": "synthetic", "base_url": "http://127.0.0.1"}
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: creds)
    app = FastAPI()
    app.include_router(chat_routes.router)
    app.dependency_overrides[current_user] = lambda: {}
    try:
        events = await collect(chat_routes._run_agent_stream(payload, creds))
        first = next(item for item in events if item["type"] == "tool_confirm_required")
        assert [item["call_id"] for item in first["calls"]] == ["echo-denied"]
        assert first["calls"][0]["risk_level"] == "high" and not actual_calls
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
            async def resume(decision, call_id=None):
                body = {"payload": payload.model_dump(exclude_none=True), "decision": decision}
                if call_id is not None:
                    body["call_id"] = call_id
                return await api.post("/api/agent-sandbox/resume", json=body)
            assert (await resume("allow")).status_code == 409
            assert (await resume("allow", "unrelated-call")).status_code == 409
            response = await resume(first_decision, "echo-denied")
            assert response.status_code == 200
            events = decode([response.text])
            assert not actual_calls
            if first_decision == "stop":
                assert any(item["type"] == "done" and item["success"] is False for item in events)
                assert chat_routes._get_pending_request_snapshot(payload.session_id) is None
                return
            second = next(item for item in events if item["type"] == "tool_confirm_required")
            assert second["calls"][0]["call_id"] == "add-approved"
            assert (await resume("allow", "echo-denied")).status_code == 409
            response = await resume("allow", "add-approved")
            assert response.status_code == 200
            events = decode([response.text])
            assert (await resume("allow", "add-approved")).status_code == 409
        results = [item for item in events if item["type"] == "tool_result"]
        assert actual_calls == ["add"]
        assert any(item["success"] is False for item in results)
        assert any(item["success"] is True for item in results)
        assert any(item["type"] == "text" and "Synthetic answer" in item["content"] for item in events)
        assert chat_routes._get_pending_request_snapshot(payload.session_id) is None
    finally:
        chat_routes._clear_pending_request_snapshot(payload.session_id)
        await manager.shutdown()


@pytest.mark.asyncio
async def test_connected_mcp_is_excluded_from_skills_mode(monkeypatch):
    from app.api import chat_routes
    from src.mcp import manager
    monkeypatch.setattr(manager, "get_mcp_manager", lambda: pytest.fail("Skills must not discover external tools"))
    snapshot = chat_routes.AgentRequestSnapshot(skills_mode="auto")
    assert chat_routes._ensure_external_tool_snapshot(snapshot, "test") is snapshot
