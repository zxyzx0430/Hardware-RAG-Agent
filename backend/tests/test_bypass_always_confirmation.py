"""Bypass mode still pauses for tools that require explicit confirmation."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, PrivateAttr

from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import ConfirmationRule, RiskLevel, ToolSpec


class _ProbeArgs(BaseModel):
    value: str


class _SideEffectProbe(ToolSpec):
    name: str = "always_probe"
    description: str = "Synthetic confirmation probe"
    args_schema: type[BaseModel] = _ProbeArgs
    risk_level: RiskLevel = RiskLevel.HIGH
    requires_confirmation: ConfirmationRule = ConfirmationRule.ALWAYS
    _calls: list[str] = PrivateAttr(default_factory=list)

    async def execute(self, args, ctx):
        self._calls.append(args["value"])
        return {"output": args["value"]}


class _OrdinaryProbe(_SideEffectProbe):
    name: str = "ordinary_probe"
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: ConfirmationRule = ConfirmationRule.NEVER


class _Audit:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


class _NoConnectedMcp:
    def connected_tool_specs_snapshot(self):
        return ()


class _HistoryAwareModel(BaseChatModel):
    tool_name: str

    def bind_tools(self, tools, **kwargs):
        return self

    def _response_for(self, messages):
        if any(
            isinstance(message, ToolMessage)
            and message.tool_call_id == "synthetic-call"
            for message in messages
        ):
            return AIMessage(content="Synthetic answer after tool.")
        return AIMessage(content="", tool_calls=[{
            "id": "synthetic-call",
            "name": self.tool_name,
            "args": {"value": "synthetic-side-effect"},
            "type": "tool_call",
        }])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._response_for(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        response = self._response_for(messages)
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=response.content, tool_calls=response.tool_calls),
        )

    @property
    def _llm_type(self):
        return "history-aware-confirmation-test-model"


def _decode(frames):
    return [
        json.loads(line[6:])
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]


async def _collect(stream):
    return _decode([frame async for frame in stream])


def _execution_count(instances) -> int:
    unique_instances = {id(instance): instance for instance in instances}
    return sum(len(instance._calls) for instance in unique_instances.values())


def _fake_model(tool_name: str) -> _HistoryAwareModel:
    return _HistoryAwareModel(tool_name=tool_name)


def _install_agent_fakes(monkeypatch, tool_type, model, saver):
    from app.api import chat_routes
    from src.agent import agent_factory, audit_logger, sse_helpers
    from src.mcp import manager as manager_module

    audit = _Audit()
    instances = []

    def assemble(_payload):
        tool = tool_type()
        instances.append(tool)
        return [tool]

    monkeypatch.setattr(agent_factory, "_global_checkpointer", saver)
    monkeypatch.setattr(agent_factory, "_global_checkpointer_cm", None)
    monkeypatch.setattr(agent_factory, "_writer_lifecycle_saver", None)
    monkeypatch.setattr(agent_factory, "_writer_lifecycle_registry", None)
    monkeypatch.setattr(agent_factory, "_factory_graph_savers", {})
    monkeypatch.setattr(agent_factory, "_assemble_all_tools", assemble)
    monkeypatch.setattr(agent_factory, "_build_llm", lambda *_args: model)
    monkeypatch.setattr(sse_helpers, "_load_session_context_window", lambda _sid: 128000)
    monkeypatch.setattr(manager_module, "get_mcp_manager", lambda: _NoConnectedMcp())
    monkeypatch.setattr(
        ToolRouter, "get_default",
        classmethod(lambda _cls: ToolRouter(audit)),
    )
    monkeypatch.setattr(audit_logger, "log_tool_call", lambda **_kwargs: None)
    creds = {"model": "synthetic", "api_key": "synthetic", "base_url": "http://127.0.0.1"}
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: creds)
    return audit, instances, creds


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["allow", "deny", "stop"])
async def test_bypass_always_tool_waits_for_user_decision(monkeypatch, decision, writer_bound_saver):
    from app.api import chat_routes
    from app.api.dependencies import current_user

    tool = _SideEffectProbe()
    model = _fake_model(tool.name)
    _audit, instances, creds = _install_agent_fakes(monkeypatch, _SideEffectProbe, model, writer_bound_saver)
    payload = chat_routes.ChatRequest(
        messages=[{"role": "user", "content": "Synthetic request"}],
        use_agent=True,
        permission_mode="bypassPermissions",
        session_id=f"bypass-always-{decision}",
    )
    chat_routes._clear_pending_request_snapshot(payload.session_id)
    app = FastAPI()
    app.include_router(chat_routes.router)
    app.dependency_overrides[current_user] = lambda: {}

    try:
        initial_events = await _collect(chat_routes._run_agent_stream(payload, creds))
        initial_tool_calls = [
            event for event in initial_events if event["type"] == "tool_call"
        ]
        confirmations = [
            event for event in initial_events
            if event["type"] == "tool_confirm_required"
        ]
        assert len(confirmations) == 1
        confirmation = confirmations[0]
        assert [event["call_id"] for event in initial_tool_calls] == ["synthetic-call"]
        assert confirmation["calls"][0]["name"] == tool.name
        assert confirmation["calls"][0]["call_id"] == "synthetic-call"
        assert initial_events.index(initial_tool_calls[0]) < initial_events.index(confirmation)
        assert _execution_count(instances) == 0

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/agent-sandbox/resume",
                json={
                    "payload": payload.model_dump(exclude_none=True),
                    "decision": decision,
                    **({"call_id": "synthetic-call"} if decision == "allow" else {}),
                },
            )
        assert response.status_code == 200
        resumed_events = _decode([response.text])
        assert _execution_count(instances) == (1 if decision == "allow" else 0)
        resumed_results = [
            event for event in resumed_events if event["type"] == "tool_result"
        ]
        assert [event["call_id"] for event in resumed_results] == ["synthetic-call"]
        assert not any(event["type"] == "tool_call" for event in resumed_events)
        if decision == "stop":
            assert any(
                event["type"] == "done" and event.get("success") is False
                for event in resumed_events
            )
        else:
            assert any(event["type"] == "done" for event in resumed_events)
    finally:
        chat_routes._clear_pending_request_snapshot(payload.session_id)


@pytest.mark.asyncio
async def test_bypass_without_always_tool_keeps_running_without_confirmation(monkeypatch, writer_bound_saver):
    from app.api import chat_routes

    tool = _OrdinaryProbe()
    model = _fake_model(tool.name)
    _audit, instances, creds = _install_agent_fakes(monkeypatch, _OrdinaryProbe, model, writer_bound_saver)
    payload = chat_routes.ChatRequest(
        messages=[{"role": "user", "content": "Synthetic request"}],
        use_agent=True,
        permission_mode="bypassPermissions",
        session_id="bypass-ordinary-tool",
    )
    chat_routes._clear_pending_request_snapshot(payload.session_id)
    try:
        events = await _collect(chat_routes._run_agent_stream(payload, creds))
        assert not any(event["type"] == "tool_confirm_required" for event in events)
        assert _execution_count(instances) == 1
        assert any(event["type"] == "text" for event in events)
    finally:
        chat_routes._clear_pending_request_snapshot(payload.session_id)
