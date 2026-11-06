"""Approved-plan admission, todo seeding and prompt guards."""

import pytest
from fastapi import HTTPException
from types import SimpleNamespace
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import chat_routes
from app.db import database
from app.db.database import Base
from app.db.models import Session as SessionModel
from app.db import plan_store
from app.db.todo_store import list_todos
from src.agent.exceptions import ToolContext
from src.agent.plan_prompt import build_approved_plan_prompt, build_plan_mode_prompt

PAYLOAD = {
    "title": "Bring up the board",
    "summary": "Inspect then execute",
    "steps": [
        {"title": "Inspect", "detail": "Read the manual", "verification": "Cite pages"},
        {"title": "Patch code", "detail": "", "verification": "Run tests"},
    ],
    "risks": [],
    "open_questions": [],
}


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
        session.add(SessionModel(id="s2", title="B", model="test"))
        session.commit()
    yield factory
    engine.dispose()


@pytest.fixture(autouse=True)
def _agent_available(monkeypatch):
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda payload, model="": True)


def _payload(**updates):
    return chat_routes.ChatRequest(messages=[{"role": "user", "content": "go"}], **updates)


def _approved_plan(session_id="s1"):
    plan = plan_store.create_version(session_id, PAYLOAD, "r1")
    return plan_store.approve(session_id, plan["id"])


def test_chat_request_accepts_plan_mode_and_approved_id():
    request = _payload(permission_mode="plan", approved_plan_id="pl_1")

    assert request.permission_mode == "plan"
    assert request.approved_plan_id == "pl_1"


def test_resolve_returns_none_without_id(factory):
    assert chat_routes._resolve_approved_plan(_payload(), "s1", "test") is None


def test_resolve_rejects_plan_mode_with_id(factory):
    with pytest.raises(HTTPException) as exc:
        chat_routes._resolve_approved_plan(
            _payload(permission_mode="plan", approved_plan_id="pl_1"), "s1", "test",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "PLAN_MODE_CONFLICT"


def test_resolve_rejects_pending_plan(factory):
    plan = plan_store.create_version("s1", PAYLOAD, "r1")

    with pytest.raises(HTTPException) as exc:
        chat_routes._resolve_approved_plan(
            _payload(approved_plan_id=plan["id"]), "s1", "test",
        )

    assert exc.value.detail["error"]["code"] == "PLAN_NOT_APPROVED"


def test_resolve_rejects_superseded_plan(factory):
    first = _approved_plan()
    plan_store.create_version("s1", {**PAYLOAD, "title": "Plan B"}, "r2")

    with pytest.raises(HTTPException) as exc:
        chat_routes._resolve_approved_plan(
            _payload(approved_plan_id=first["id"]), "s1", "test",
        )

    assert exc.value.detail["error"]["code"] == "PLAN_SUPERSEDED"


def test_resolve_rejects_wrong_session(factory):
    plan = _approved_plan("s1")

    with pytest.raises(HTTPException) as exc:
        chat_routes._resolve_approved_plan(
            _payload(approved_plan_id=plan["id"]), "s2", "test",
        )

    assert exc.value.status_code == 404


def test_resolve_accepts_latest_approved(factory):
    plan = _approved_plan()

    resolved = chat_routes._resolve_approved_plan(
        _payload(approved_plan_id=plan["id"]), "s1", "test",
    )

    assert resolved["id"] == plan["id"]
    assert resolved["status"] == "approved"


def test_seed_plan_todos_replaces_once(factory):
    plan = _approved_plan()

    seeded = chat_routes._seed_plan_todos("s1", plan)
    chat_routes._seed_plan_todos("s1", plan)

    assert [item["content"] for item in seeded] == ["Inspect", "Patch code"]
    assert [item["content"] for item in list_todos("s1")] == ["Inspect", "Patch code"]


def test_approved_prompt_is_not_a_permission_grant(factory):
    plan = _approved_plan()

    prompt = build_approved_plan_prompt(plan)

    assert "User-approved plan" in prompt
    assert "NOT a permission grant" in prompt
    assert "Bring up the board" in prompt
    assert len(prompt) < 5000


def test_plan_mode_prompt_is_read_only():
    prompt = build_plan_mode_prompt()

    assert "read-only" in prompt.lower()
    assert "submit_plan" in prompt
    assert "not a permission grant" in prompt.lower()


def test_final_filter_keeps_submit_and_drops_writes():
    from src.agent.tools.groups.file_ops.read_file import ReadFileTool
    from src.agent.tools.groups.file_ops.write_file import WriteFileTool

    filtered = chat_routes._filter_plan_mode_tools([ReadFileTool(), WriteFileTool()])
    names = {tool.name for tool in filtered}

    assert "read_file" in names
    assert "submit_plan" in names
    assert "write_file" not in names


def test_apply_plan_mode_rebuilds_request_lookup():
    from src.agent.tools.groups.file_ops.read_file import ReadFileTool
    from src.agent.tools.groups.file_ops.write_file import WriteFileTool

    ctx = ToolContext(permission_mode="plan", session_id="s1")
    snapshot = SimpleNamespace(permission_mode="plan", tool_context=ctx)

    plan_mode, tools = chat_routes._apply_plan_mode_tools(
        snapshot, [ReadFileTool(), WriteFileTool()],
    )
    names = {tool.name for tool in tools}

    assert plan_mode is True
    assert names == {"read_file", "submit_plan"}
    assert set(ctx.request_tools) == names
    assert ctx.skills_allowed_tools == frozenset(names)


def test_apply_plan_mode_is_a_noop_outside_plan_mode():
    from src.agent.tools.groups.file_ops.read_file import ReadFileTool

    ctx = ToolContext(permission_mode="default", session_id="s1")
    snapshot = SimpleNamespace(permission_mode="default", tool_context=ctx)
    tools = [ReadFileTool()]

    plan_mode, returned = chat_routes._apply_plan_mode_tools(snapshot, tools)

    assert plan_mode is False
    assert returned is tools
