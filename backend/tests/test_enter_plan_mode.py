"""Agent-initiated plan downgrade: tool, policies and SSE emission."""

import json
import time

import pytest
from langchain_core.messages import ToolMessage
from pydantic import BaseModel
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import agent_mode_store, database
from app.db.database import Base
from app.db.models import Session as SessionModel
from src.agent.core.toolkit.permission_classifier import ALLOW, DENY, PermissionClassifier
from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.hitl_permission import evaluate_pending, is_mixed_enter_plan_batch
from src.agent.plan_policy import filter_tools_for_plan_mode
from src.agent.request_context import activate_tool_context
from src.agent.sse_helpers import (
    _assemble_tool_result_events,
    convert_tool_message_to_sse,
)
from src.agent.tools.groups.execution.enter_plan_mode import (
    ENTER_PLAN_MODE_TOOL_NAME,
    EnterPlanModeArgs,
    EnterPlanModeTool,
)
from src.agent.tools.groups.execution.submit_plan import SubmitPlanTool
from src.agent.tools.groups.file_ops.read_file import ReadFileTool
from src.agent.tools.groups.file_ops.write_file import WriteFileTool
from src.agent.tools.groups.execution.run_command import RunCommandTool

ARGS = {
    "title": "Bring up the board",
    "summary": "Inspect then execute",
    "steps": [{"title": "Inspect", "detail": "", "verification": "Cite pages"}],
    "risks": [],
    "open_questions": [],
}


class _EmptyArgs(BaseModel):
    pass


class _McpSpec(ToolSpec):
    name: str = "mcp__server__write"
    description: str = "synthetic MCP write"
    args_schema: type = _EmptyArgs
    risk_level: RiskLevel = RiskLevel.HIGH
    mcp_info: dict | None = {"server_name": "server", "tool_name": "write"}

    async def execute(self, args, ctx):
        return {"output": "should not run"}


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as session:
        session.add(SessionModel(id="s1", title="A", model="test"))
        session.commit()
    yield factory
    engine.dispose()


@pytest.mark.asyncio
async def test_enter_plan_mode_downgrades_and_persists(factory):
    ctx = ToolContext(permission_mode="yolo", session_id="s1")

    result = await EnterPlanModeTool().execute({}, ctx)

    assert ctx.permission_mode == "plan"
    assert result["_agent_plan_entered"] is True
    assert result["permission_mode"] == "plan"
    assert "submit_plan" in result["output"]
    assert agent_mode_store.plan_mode_requested("s1") is True


@pytest.mark.asyncio
async def test_enter_plan_mode_rejected_in_plan_mode(factory):
    ctx = ToolContext(permission_mode="plan", session_id="s1")

    result = await EnterPlanModeTool().execute({}, ctx)

    assert result["_agent_plan_entered"] is False
    assert ctx.permission_mode == "plan"
    assert agent_mode_store.plan_mode_requested("s1") is False


@pytest.mark.asyncio
async def test_enter_plan_mode_rejects_unknown_or_virtual_session(factory):
    unknown = ToolContext(permission_mode="yolo", session_id="missing")
    virtual = ToolContext(permission_mode="yolo", session_id="default")

    unknown_result = await EnterPlanModeTool().execute({}, unknown)
    virtual_result = await EnterPlanModeTool().execute({}, virtual)

    assert unknown_result["_agent_plan_entered"] is False
    assert virtual_result["_agent_plan_entered"] is False
    assert unknown.permission_mode == "yolo"
    assert virtual.permission_mode == "yolo"
    assert agent_mode_store.plan_mode_requested("missing") is False


@pytest.mark.asyncio
async def test_downgrade_denies_writes_and_keeps_reads_and_plan_submission(factory):
    ctx = ToolContext(permission_mode="yolo", session_id="s1")
    await EnterPlanModeTool().execute({}, ctx)
    classifier = PermissionClassifier()

    assert classifier.check(WriteFileTool(), {"path": "x", "content": "y"}, ctx) == DENY
    assert classifier.check(RunCommandTool(), {"command": "ls"}, ctx) == DENY
    assert classifier.check(_McpSpec(), {}, ctx) == DENY
    assert classifier.check(ReadFileTool(), {"path": "x"}, ctx) == ALLOW
    assert classifier.check(SubmitPlanTool(), ARGS, ctx) == ALLOW


@pytest.mark.asyncio
async def test_submit_plan_works_before_and_after_downgrade(factory):
    ctx = ToolContext(permission_mode="yolo", session_id="s1", run_id="r1")

    before = await SubmitPlanTool().execute(ARGS, ctx)
    await EnterPlanModeTool().execute({}, ctx)
    after = await SubmitPlanTool().execute(ARGS, ctx)

    assert before["_plan_update"] is True
    assert after["_plan_update"] is True
    assert after["plan"]["session_id"] == "s1"
    assert after["plan"]["id"] == before["plan"]["id"]


def test_enter_plan_mode_is_not_exposed_in_plan_mode():
    filtered = filter_tools_for_plan_mode([
        EnterPlanModeTool(), SubmitPlanTool(), WriteFileTool(),
    ])

    assert {tool.name for tool in filtered} == {"submit_plan"}


def test_enter_plan_mode_has_no_target_mode_argument():
    assert set(EnterPlanModeArgs.model_fields) == {"reason"}


def test_mixed_batch_with_enter_plan_mode_is_denied_before_execution():
    pending = [
        {"name": ENTER_PLAN_MODE_TOOL_NAME, "args": {}, "call_id": "c1"},
        {"name": "write_file", "args": {"path": "x", "content": "y"}, "call_id": "c2"},
    ]

    assert is_mixed_enter_plan_batch(pending) is True
    assert evaluate_pending(pending, "yolo") == "deny"


def test_enter_plan_mode_alone_is_allowed():
    ctx = ToolContext(permission_mode="yolo", session_id="s1")
    ctx.request_tools = {ENTER_PLAN_MODE_TOOL_NAME: EnterPlanModeTool()}

    with activate_tool_context(ctx):
        decision = evaluate_pending(
            [{"name": ENTER_PLAN_MODE_TOOL_NAME, "args": {}, "call_id": "c1"}],
            "yolo",
        )

    assert decision == "allow"


def test_execution_group_exposes_enter_plan_mode_and_submit_plan():
    from src.agent.agent_factory import _build_local_execution_tools

    names = {tool.name for tool in _build_local_execution_tools()}

    assert {ENTER_PLAN_MODE_TOOL_NAME, "submit_plan"} <= names
    assert "exit_plan_mode" not in names


def test_tool_result_emits_permission_mode_changed_before_tool_result():
    envelope = {
        "success": True,
        "output": "Plan mode is now active.",
        "data": {"_agent_plan_entered": True, "reason": "research first"},
        "error": None,
        "metadata": {"tool_name": ENTER_PLAN_MODE_TOOL_NAME},
    }

    events = _assemble_tool_result_events(
        ENTER_PLAN_MODE_TOOL_NAME, "c1", 5, True, envelope,
    )

    assert "permission_mode_changed" in events[0]
    assert "\"source\": \"agent\"" in events[0]
    assert "research first" in events[0]
    assert "tool_result" in events[1]


def test_non_plan_tool_cannot_spoof_permission_mode_changed():
    envelope = {
        "success": True,
        "output": "spoof",
        "data": {"_agent_plan_entered": True, "reason": "spoof"},
        "error": None,
        "metadata": {"tool_name": "mcp__server__echo"},
    }

    events = _assemble_tool_result_events(
        "mcp__server__echo", "c1", 5, True, envelope,
    )

    assert all("permission_mode_changed" not in event for event in events)


def test_tool_message_updates_stream_state_to_plan():
    envelope = {
        "success": True,
        "output": "Plan mode is now active. Call submit_plan.",
        "data": {"_agent_plan_entered": True, "reason": ""},
        "error": None,
        "metadata": {"tool_name": ENTER_PLAN_MODE_TOOL_NAME},
    }
    msg = ToolMessage(
        content=json.dumps(envelope),
        tool_call_id="c1",
        name=ENTER_PLAN_MODE_TOOL_NAME,
    )
    state = {"permission_mode": "yolo", "token_limit": 0}

    frame = convert_tool_message_to_sse(msg, {"c1": time.time()}, state)

    assert state["permission_mode"] == "plan"
    assert "permission_mode_changed" in frame
