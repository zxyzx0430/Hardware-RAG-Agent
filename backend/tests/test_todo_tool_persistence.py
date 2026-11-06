"""TodoWriteTool persistence, fallback and session isolation tests."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import database
from app.db.database import Base
from app.db.models import Session as SessionModel
from app.db.todo_store import list_todos
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.execution import todo_write
from src.agent.tools.groups.execution.todo_write import (
    TodoWriteTool,
    get_session_todos,
)

TODOS = [
    {"content": "先看手册", "status": "completed", "priority": "high"},
    {"content": "再写代码", "status": "in_progress", "priority": "medium"},
]


@pytest.fixture()
def tool_db(monkeypatch):
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
    with factory() as db:
        db.add(SessionModel(id="session-1", title="A", model="test"))
        db.add(SessionModel(id="session-2", title="B", model="test"))
        db.commit()
    todo_write._SESSION_TODOS.clear()
    try:
        yield factory
    finally:
        todo_write._SESSION_TODOS.clear()
        engine.dispose()


def _ctx(session_id: str) -> ToolContext:
    return ToolContext(session_id=session_id)


@pytest.mark.asyncio
async def test_tool_write_is_persisted_to_the_database(tool_db):
    result = await TodoWriteTool().execute({"todos": TODOS}, _ctx("session-1"))

    assert result["persisted"] is True
    assert result["_todo_update"] is True
    assert result["todos"] == TODOS
    assert [
        {key: row[key] for key in ("content", "status", "priority")}
        for row in list_todos("session-1")
    ] == TODOS


@pytest.mark.asyncio
async def test_empty_list_clears_the_persisted_list(tool_db):
    tool = TodoWriteTool()
    await tool.execute({"todos": TODOS}, _ctx("session-1"))

    result = await tool.execute({"todos": []}, _ctx("session-1"))

    assert result["persisted"] is True
    assert result["todos"] == []
    assert list_todos("session-1") == []


@pytest.mark.asyncio
async def test_default_session_stays_memory_only(tool_db):
    result = await TodoWriteTool().execute({"todos": TODOS}, _ctx("default"))

    assert result["persisted"] is False
    assert list_todos("default") == []
    assert get_session_todos("default") == TODOS


@pytest.mark.asyncio
async def test_missing_session_falls_back_to_memory(tool_db):
    result = await TodoWriteTool().execute({"todos": TODOS}, _ctx("missing-session"))

    assert result["persisted"] is False
    assert list_todos("missing-session") == []
    assert get_session_todos("missing-session") == TODOS


@pytest.mark.asyncio
async def test_database_failure_keeps_tool_success_and_memory_fallback(
    tool_db, monkeypatch,
):
    def _fail(*_args, **_kwargs):
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr("app.db.todo_store.replace_todos", _fail)

    result = await TodoWriteTool().execute({"todos": TODOS}, _ctx("session-1"))

    assert result["persisted"] is False
    assert result["_todo_update"] is True
    assert result["output"] == "updated 2 todos"
    assert get_session_todos("session-1") == TODOS
    assert list_todos("session-1") == []


@pytest.mark.asyncio
async def test_cross_session_isolation(tool_db):
    tool = TodoWriteTool()
    await tool.execute({"todos": [TODOS[0]]}, _ctx("session-1"))
    await tool.execute({"todos": [TODOS[1]]}, _ctx("session-2"))

    assert [row["content"] for row in list_todos("session-1")] == ["先看手册"]
    assert [row["content"] for row in list_todos("session-2")] == ["再写代码"]


def test_agent_config_empty_session_matches_tool_default(tool_db):
    from src.agent.agent_factory import AgentConfig
    from src.agent.todo_context import normalize_session_id

    config = AgentConfig(
        model="test-model", api_key="test-key", base_url="http://localhost",
        tools=[], session_id="",
    )

    assert normalize_session_id(config.session_id) == "default"
    assert ToolContext().session_id == "default"
    assert todo_write._resolve_session_id(ToolContext(session_id="")) == "default"
