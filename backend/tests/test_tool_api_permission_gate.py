"""Direct tool API permission checks and conservative command grading."""

from __future__ import annotations

from pydantic import BaseModel
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import tool_routes
from src.agent import hitl_permission
from src.agent.core.toolkit import tool_router
from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.risk_classifier import LOW, MEDIUM, _classify_command


class _EmptyArgs(BaseModel):
    pass


class _PathArgs(BaseModel):
    path: str = "sample.txt"


class _CommandArgs(BaseModel):
    command: str


class _StubTool(ToolSpec):
    name: str = "stub_tool"
    description: str = "test tool"
    args_schema: type[BaseModel] = _EmptyArgs

    async def execute(self, args, ctx):
        raise AssertionError("the fake router owns execution in this test")


class _FakeRouter:
    def __init__(self):
        self.calls = []

    async def dispatch(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"success": True, "output": "dispatched", "data": None}


@pytest.mark.parametrize(
    "command",
    [
        "echo safe; Remove-Item important.txt",
        "git status && Remove-Item important.txt",
        "echo safe | Out-File important.txt",
        "echo safe > important.txt",
        "git diff`nRemove-Item important.txt",
        "echo $(Remove-Item important.txt)",
    ],
)
def test_compound_or_shell_control_commands_are_not_low_risk(command):
    assert _classify_command(command) == MEDIUM


def test_simple_allowlisted_command_remains_low_risk():
    assert _classify_command("git status --short") == LOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "risk_level", "args", "path_result"),
    [
        ("write_file", RiskLevel.MEDIUM, {"path": "sample.txt"}, (True, "")),
        ("flash_firmware", RiskLevel.HIGH, {}, None),
    ],
)
async def test_direct_api_refuses_tools_requiring_confirmation(
    monkeypatch, name, risk_level, args, path_result
):
    args_schema = _PathArgs if name == "write_file" else _EmptyArgs
    spec = _StubTool(name=name, risk_level=risk_level, args_schema=args_schema)
    fake_router = _FakeRouter()
    monkeypatch.setattr(tool_routes, "_ensure_tools", lambda: None)
    monkeypatch.setattr(tool_routes, "list_registered_tools", lambda: [spec])
    monkeypatch.setattr(tool_routes.ToolRouter, "get_default", lambda: fake_router)
    if path_result is not None:
        from src.agent import path_guard

        monkeypatch.setattr(path_guard, "validate_path", lambda *_args, **_kwargs: path_result)

    result = await tool_routes.call_tool(
        tool_routes.ToolRequest(tool=name, args=args), user={}
    )

    assert result["success"] is False
    assert result["error"]["error_type"] == "PERMISSION_CONFIRMATION_REQUIRED"
    assert fake_router.calls == []


@pytest.mark.asyncio
async def test_direct_api_denies_path_guard_rejection_without_dispatch(monkeypatch):
    spec = _StubTool(name="write_file", risk_level=RiskLevel.MEDIUM, args_schema=_PathArgs)
    fake_router = _FakeRouter()
    monkeypatch.setattr(tool_routes, "_ensure_tools", lambda: None)
    monkeypatch.setattr(tool_routes, "list_registered_tools", lambda: [spec])
    monkeypatch.setattr(tool_routes.ToolRouter, "get_default", lambda: fake_router)
    from src.agent import path_guard

    monkeypatch.setattr(path_guard, "validate_path", lambda *_args, **_kwargs: (False, "blocked"))

    result = await tool_routes.call_tool(
        tool_routes.ToolRequest(tool="write_file", args={"path": ".env"}), user={}
    )

    assert result["success"] is False
    assert result["error"]["error_type"] == "PERMISSION_DENIED"
    assert fake_router.calls == []


@pytest.mark.asyncio
async def test_direct_api_still_dispatches_low_risk_tool(monkeypatch):
    spec = _StubTool(name="safe_lookup", risk_level=RiskLevel.LOW)
    fake_router = _FakeRouter()
    monkeypatch.setattr(tool_routes, "_ensure_tools", lambda: None)
    monkeypatch.setattr(tool_routes, "list_registered_tools", lambda: [spec])
    monkeypatch.setattr(tool_routes.ToolRouter, "get_default", lambda: fake_router)

    result = await tool_routes.call_tool(
        tool_routes.ToolRequest(tool="safe_lookup", args={}), user={}
    )

    assert result["success"] is True
    assert len(fake_router.calls) == 1


@pytest.mark.asyncio
async def test_direct_api_fails_closed_when_tool_spec_is_missing(monkeypatch):
    fake_router = _FakeRouter()
    monkeypatch.setattr(tool_routes, "_ensure_tools", lambda: None)
    monkeypatch.setattr(tool_routes, "list_registered_tools", lambda: [])
    monkeypatch.setattr(tool_routes.ToolRouter, "get_default", lambda: fake_router)

    result = await tool_routes.call_tool(
        tool_routes.ToolRequest(tool="unknown_side_effect_tool", args={}), user={}
    )

    assert result["success"] is False
    assert result["error"]["error_type"] == "TOOL_NOT_FOUND"
    assert fake_router.calls == []


@pytest.mark.asyncio
async def test_direct_api_refuses_compound_command_that_has_safe_prefix(monkeypatch):
    spec = _StubTool(
        name="run_command",
        risk_level=RiskLevel.HIGH,
        args_schema=_CommandArgs,
    )
    fake_router = _FakeRouter()
    monkeypatch.setattr(tool_routes, "_ensure_tools", lambda: None)
    monkeypatch.setattr(tool_routes, "list_registered_tools", lambda: [spec])
    monkeypatch.setattr(tool_routes.ToolRouter, "get_default", lambda: fake_router)

    result = await tool_routes.call_tool(
        tool_routes.ToolRequest(
            tool="run_command",
            args={"command": "git status; Remove-Item important.txt"},
        ),
        user={},
    )

    assert result["success"] is False
    assert result["error"]["error_type"] == "PERMISSION_CONFIRMATION_REQUIRED"
    assert fake_router.calls == []


def test_http_tool_route_returns_confirmation_required_without_dispatch(monkeypatch):
    spec = _StubTool(name="flash_firmware", risk_level=RiskLevel.HIGH)
    fake_router = _FakeRouter()
    monkeypatch.setattr(tool_routes, "_ensure_tools", lambda: None)
    monkeypatch.setattr(tool_routes, "list_registered_tools", lambda: [spec])
    monkeypatch.setattr(tool_routes.ToolRouter, "get_default", lambda: fake_router)

    app = FastAPI()
    app.include_router(tool_routes.router)
    app.dependency_overrides[tool_routes.current_user] = lambda: {"anonymous": False}

    with TestClient(app) as client:
        response = client.post(
            "/api/tool",
            json={"tool": "flash_firmware", "args": {}},
        )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["error"]["error_type"] == "PERMISSION_CONFIRMATION_REQUIRED"
    assert fake_router.calls == []


def test_chat_pending_compound_command_stays_at_hitl_gate(monkeypatch):
    spec = _StubTool(
        name="run_command",
        risk_level=RiskLevel.HIGH,
        args_schema=_CommandArgs,
    )
    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", {spec.name: spec})
    pending = [
        {
            "name": "run_command",
            "args": {"command": "git status; Remove-Item important.txt"},
            "call_id": "chat-confirm-call",
        }
    ]

    decision = hitl_permission.evaluate_pending(pending, "default")

    assert decision == "ask"
    assert spec._ctx is None
