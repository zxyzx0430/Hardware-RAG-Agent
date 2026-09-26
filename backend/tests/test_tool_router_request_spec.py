"""ToolRouter request-instance dispatch and compatibility regressions."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, PrivateAttr
import pytest

from src.agent.core.toolkit import tool_router
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import ToolSpec
from src.agent.exceptions import ToolContext


class _ProbeArgs(BaseModel):
    value: str


class _RequestTool(ToolSpec):
    name: str = "request_config_probe"
    description: str = "request scoped test tool"
    args_schema: type[BaseModel] = _ProbeArgs
    request_config: str = "default"
    _calls: int = PrivateAttr(default=0)
    _barrier: asyncio.Event | None = PrivateAttr(default=None)
    _started: list[str] | None = PrivateAttr(default=None)

    async def execute(self, args, ctx):
        self._calls += 1
        if self._barrier is not None and self._started is not None:
            self._started.append(self.request_config)
            if len(self._started) == 2:
                self._barrier.set()
            await asyncio.wait_for(self._barrier.wait(), timeout=2)
        return {
            "output": self.request_config,
            "request_config": self.request_config,
            "session_id": ctx.session_id,
        }


class _AuditRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


@pytest.mark.asyncio
async def test_concurrent_tool_instances_keep_config_context_and_audit_identity(monkeypatch):
    registry = {}
    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", registry)
    router = ToolRouter(_AuditRecorder())
    monkeypatch.setattr(ToolRouter, "get_default", classmethod(lambda _cls: router))

    barrier = asyncio.Event()
    started: list[str] = []
    default_spec = _RequestTool(request_config="registry-default")
    request_a = _RequestTool(request_config="request-a")
    request_b = _RequestTool(request_config="request-b")
    registry[default_spec.name] = default_spec
    for spec, session_id, call_id in (
        (default_spec, "default-session", "default-call"),
        (request_a, "session-a", "call-a"),
        (request_b, "session-b", "call-b"),
    ):
        spec._barrier = barrier
        spec._started = started
        spec._ctx = ToolContext(session_id=session_id)
        spec._current_call_id = call_id

    result_a, result_b = await asyncio.wait_for(
        asyncio.gather(
            request_a.ainvoke({"value": "a"}),
            request_b.ainvoke({"value": "b"}),
        ),
        timeout=3,
    )

    assert started in (
        ["request-a", "request-b"],
        ["request-b", "request-a"],
    )
    assert result_a["data"]["request_config"] == "request-a"
    assert result_a["data"]["session_id"] == "session-a"
    assert result_b["data"]["request_config"] == "request-b"
    assert result_b["data"]["session_id"] == "session-b"
    assert default_spec._calls == 0
    assert {id(record.spec) for record in router.audit_recorder.records} == {
        id(request_a),
        id(request_b),
    }


@pytest.mark.asyncio
async def test_dispatch_without_request_spec_still_uses_registry(monkeypatch):
    spec = _RequestTool(request_config="legacy-registry")
    spec._barrier = asyncio.Event()
    spec._barrier.set()
    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", {spec.name: spec})
    router = ToolRouter(_AuditRecorder())

    result = await router.dispatch("legacy-call", spec.name, {"value": "ok"}, ToolContext())

    assert result["success"] is True
    assert result["data"]["request_config"] == "legacy-registry"
    assert router.audit_recorder.records[0].spec is spec


@pytest.mark.asyncio
async def test_dispatch_refuses_mismatched_request_spec_without_fallback(monkeypatch):
    registered = _RequestTool(request_config="registered")
    mismatched = _RequestTool(name="different-name", request_config="request")
    monkeypatch.setattr(
        tool_router,
        "_TOOL_REGISTRY",
        {registered.name: registered, mismatched.name: mismatched},
    )
    router = ToolRouter(_AuditRecorder())

    result = await router.dispatch(
        "mismatch-call",
        registered.name,
        {"value": "must not run"},
        ToolContext(),
        tool_spec=mismatched,
    )

    assert result["success"] is False
    assert result["error"]["error_type"] == "TOOL_NOT_FOUND"
    assert mismatched._calls == 0
    assert registered._calls == 0
    assert router.audit_recorder.records == []
