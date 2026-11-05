"""Memory v2 tool behavior: tiers, status defaults, secrets, forget scope."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import memory_models  # noqa: F401  registers memory tables on Base
from app.db.database import Base
from app.db.memory_store import MemoryStore
from src.agent import memory_project
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.memory import (
    MemoryForgetTool,
    MemoryRememberTool,
    MemorySearchTool,
)


@pytest.fixture()
def memory_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def store(memory_db):
    return MemoryStore(session_factory=memory_db)


@pytest.fixture()
def project_env(tmp_path, monkeypatch):
    project_root = tmp_path / "synthetic-project"
    project_root.mkdir()
    monkeypatch.setenv("HARDWARE_AGENT_PROJECT_PATH", str(project_root))
    memory_project.clear_cache()
    try:
        yield project_root
    finally:
        memory_project.clear_cache()


async def test_search_returns_only_active_visible_entries(store, project_env):
    store.create(
        tier="project",
        project_id=memory_project.current_project_id(),
        kind="fact",
        content="ACTIVE project note.",
    )
    store.upsert_proposed(
        {
            "tier": "project",
            "project_id": memory_project.current_project_id(),
            "kind": "fact",
            "content": "PROPOSED note.",
        }
    )
    store.create(tier="working", scope_key="sess-1", kind="fact", content="WORKING note.")
    store.create(tier="working", scope_key="sess-2", kind="fact", content="OTHER session note.")
    store.create(
        tier="project",
        project_id="other-project",
        kind="fact",
        content="OTHER project note.",
    )

    result = await MemorySearchTool(store=store).execute(
        {"query": "note"}, ToolContext(session_id="sess-1")
    )
    contents = [entry["content"] for entry in result["results"]]
    assert "ACTIVE project note." in contents
    assert "WORKING note." in contents
    assert "PROPOSED note." not in contents
    assert "OTHER session note." not in contents
    assert "OTHER project note." not in contents


async def test_search_tier_filter(store, project_env):
    store.create(tier="user", kind="preference", content="USER preference.")
    store.create(
        tier="project",
        project_id=memory_project.current_project_id(),
        kind="fact",
        content="PROJECT fact.",
    )
    result = await MemorySearchTool(store=store).execute(
        {"query": "preference", "tier": "user"}, ToolContext(session_id="sess-1")
    )
    assert result["total"] == 1
    assert result["results"][0]["tier"] == "user"

    invalid = await MemorySearchTool(store=store).execute(
        {"query": "preference", "tier": "bogus"}, ToolContext(session_id="sess-1")
    )
    assert invalid["total"] == 0


async def test_remember_with_tier_writes_active(store, project_env):
    result = await MemoryRememberTool(store=store).execute(
        {
            "content": "User prefers metric units.",
            "kind": "preference",
            "tier": "user",
        },
        ToolContext(session_id="sess-1"),
    )
    assert result["saved"] is True
    assert result["entry"]["tier"] == "user"
    assert result["entry"]["status"] == "active"
    assert result["entry"]["scope"] == "global"

    project = await MemoryRememberTool(store=store).execute(
        {"content": "Use PlatformIO.", "kind": "decision", "tier": "project"},
        ToolContext(session_id="sess-1"),
    )
    assert project["entry"]["project_id"] == memory_project.current_project_id()

    working = await MemoryRememberTool(store=store).execute(
        {"content": "Current session note.", "tier": "working"},
        ToolContext(session_id="sess-1"),
    )
    assert working["entry"]["tier"] == "working"
    assert working["entry"]["scope_key"] == "sess-1"


async def test_remember_without_session_rejects_working_tier(store):
    result = await MemoryRememberTool(store=store).execute(
        {"content": "No session.", "tier": "working"}, ToolContext()
    )
    assert result["saved"] is False
    assert store.list(limit=10) == []


async def test_remember_refuses_secrets_and_hardware(store):
    secret = await MemoryRememberTool(store=store).execute(
        {"content": "api_key = abc12345678"}, ToolContext(session_id="sess-1")
    )
    assert secret["saved"] is False
    assert "拒绝" in secret["output"]

    hardware = await MemoryRememberTool(store=store).execute(
        {
            "content": "ESP32-S3 pin map.",
            "structured": {"type": "hardware_profile"},
        },
        ToolContext(session_id="sess-1"),
    )
    assert hardware["saved"] is False
    assert store.list(limit=10) == []


async def test_legacy_scope_remember_still_works(store, project_env):
    result = await MemoryRememberTool(store=store).execute(
        {"content": "Synthetic project fact.", "scope": "project"},
        ToolContext(session_id="sess-1"),
    )
    assert result["saved"] is True
    assert result["entry"]["scope"] == "project"
    assert result["entry"]["scope_key"] == str(project_env)


async def test_forget_soft_deletes_only_the_target(store):
    first = store.create(tier="user", kind="fact", content="First entry.")
    second = store.create(tier="user", kind="fact", content="Second entry.")
    result = await MemoryForgetTool(store=store).execute(
        {"id": first["id"]}, ToolContext()
    )
    assert result["deleted"] is True
    assert store.get(first["id"]) is None
    assert store.get(second["id"]) is not None
