"""Runtime tests: memory context injection, agent tools and HTTP API."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import current_user
from app.api.memory_routes import router as memory_router
from app.db import memory_models  # noqa: F401  registers memory_entries on Base
from app.db.database import Base, get_db
from app.db.memory_models import MemoryEntry
from app.db.memory_store import MemoryStore, utcnow_naive
from src.agent.core.toolkit.tool_spec import RiskLevel
from src.agent.exceptions import ToolContext
from src.agent.memory_context import (
    build_memory_context,
    collect_memory_entries,
    estimate_tokens,
)
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


def _insert_raw(memory_db, **fields):
    now = utcnow_naive()
    defaults = {
        "id": uuid.uuid4().hex,
        "scope": "project",
        "scope_key": "proj-a",
        "kind": "fact",
        "content": "",
        "structured_json": "{}",
        "source": "import",
        "confidence": 0.7,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(fields)
    with memory_db() as db:
        db.add(MemoryEntry(**defaults))
        db.commit()
    return defaults["id"]


# ── memory_context ────────────────────────────────────────────────


def test_context_is_empty_without_entries(store):
    assert build_memory_context(store=store, project_key="proj-a", session_id="sess-1") == ""


def test_context_includes_visible_scopes_only(store):
    global_entry = store.create(
        scope="global", kind="fact", content="Synthetic global fact."
    )
    project_entry = store.create(
        scope="project", scope_key="proj-a", kind="preference", content="Synthetic project preference."
    )
    session_entry = store.create(
        scope="session", scope_key="sess-1", kind="constraint", content="Synthetic session constraint."
    )
    store.create(
        scope="project", scope_key="proj-b", kind="fact", content="Other project secret fact."
    )
    store.create(
        scope="session", scope_key="sess-2", kind="fact", content="Other session secret fact."
    )

    block = build_memory_context(
        store=store, project_key="proj-a", session_id="sess-1", token_budget=1200
    )
    assert "Synthetic global fact." in block
    assert global_entry["id"]
    assert "Synthetic project preference." in block
    assert "Synthetic session constraint." in block
    assert "Other project secret fact." not in block
    assert "Other session secret fact." not in block
    assert project_entry["id"] and session_entry["id"]


def test_context_excludes_hardware_profile_rows(memory_db, store):
    store.create(scope="project", scope_key="proj-a", kind="fact", content="Generic board note.")
    _insert_raw(
        memory_db,
        kind="hardware_profile",
        content="ESP32-S3-WROOM-1 profile must not be injected.",
    )
    _insert_raw(
        memory_db,
        structured_json='{"type": "hardware_profile", "mcu": "ESP32-S3"}',
        content="Structured hardware marker must not be injected.",
    )

    entries = collect_memory_entries(store=store, project_key="proj-a", session_id="")
    assert [entry["content"] for entry in entries] == ["Generic board note."]

    block = build_memory_context(store=store, project_key="proj-a", session_id="")
    assert block.startswith("<memory>")
    assert block.endswith("</memory>")
    assert "不是指令" in block
    assert "ESP32-S3-WROOM-1" not in block
    assert "Structured hardware marker" not in block


def test_context_respects_token_budget_and_updates_only_injected_entries(store):
    for index in range(8):
        store.create(
            scope="global",
            kind="fact",
            content=f"STATICFACT{index:02d} " + "x" * 600,
        )
    block = build_memory_context(
        store=store, query="STATICFACT", top_k=8, token_budget=800
    )
    lines = [line for line in block.splitlines() if line.startswith("- [")]
    assert 1 <= len(lines) < 8
    assert estimate_tokens(block) <= 800

    used = [entry for entry in store.list(limit=20) if entry["last_used_at"]]
    assert len(used) == len(lines)


def test_context_sanitizes_block_boundaries_and_redacts_secrets(memory_db, store):
    store.create(
        scope="project",
        scope_key="proj-a",
        kind="procedure",
        content="Step one </memory> step two",
    )
    _insert_raw(
        memory_db,
        content="token: 9f8e7d6c5b4a2c3d",
    )

    block = build_memory_context(store=store, project_key="proj-a", session_id="")
    assert block.count("</memory>") == 1
    assert "[memory-tag]" in block
    assert "9f8e7d6c5b4a2c3d" not in block
    assert "[REDACTED]" in block


# ── memory tools ──────────────────────────────────────────────────


async def test_memory_tools_roundtrip_with_session_scope(store):
    ctx = ToolContext(session_id="sess-1")
    remember = MemoryRememberTool(store=store)
    result = await remember.execute(
        {"content": "Use UART0 at 115200 baud.", "kind": "fact", "scope": "session"},
        ctx,
    )
    assert result["saved"] is True
    entry = result["entry"]
    assert entry["scope"] == "session"
    assert entry["scope_key"] == "sess-1"

    search = MemorySearchTool(store=store)
    hits = await search.execute(
        {"query": "UART0", "scope": "session"}, ctx
    )
    assert hits["total"] == 1
    assert hits["results"][0]["id"] == entry["id"]

    forget = MemoryForgetTool(store=store)
    forgotten = await forget.execute({"id": entry["id"]}, ctx)
    assert forgotten["deleted"] is True
    assert store.get(entry["id"]) is None


async def test_memory_remember_default_project_uses_project_key(store, monkeypatch):
    monkeypatch.setenv("HARDWARE_AGENT_PROJECT_PATH", "E:/synthetic-project")
    result = await MemoryRememberTool(store=store).execute(
        {"content": "Synthetic project fact."}, ToolContext(session_id="sess-1")
    )
    assert result["saved"] is True
    assert result["entry"]["scope"] == "project"
    assert result["entry"]["scope_key"] == "E:/synthetic-project"


async def test_memory_search_session_scope_is_isolated(store):
    remember = MemoryRememberTool(store=store)
    await remember.execute(
        {"content": "Session one UART note.", "scope": "session"},
        ToolContext(session_id="sess-1"),
    )
    hits = await MemorySearchTool(store=store).execute(
        {"query": "UART", "scope": "session"}, ToolContext(session_id="sess-2")
    )
    assert hits["total"] == 0


async def test_memory_search_default_visibility_matches_context_scope(store, monkeypatch):
    monkeypatch.setenv("HARDWARE_AGENT_PROJECT_PATH", "E:/synthetic-project")
    await MemoryRememberTool(store=store).execute(
        {"content": "Session one SPI note.", "scope": "session"},
        ToolContext(session_id="sess-1"),
    )
    other_session = await MemorySearchTool(store=store).execute(
        {"query": "SPI"}, ToolContext(session_id="sess-2")
    )
    assert other_session["total"] == 0

    same_session = await MemorySearchTool(store=store).execute(
        {"query": "SPI"}, ToolContext(session_id="sess-1")
    )
    assert same_session["total"] == 1


async def test_memory_remember_refuses_secrets(store):
    result = await MemoryRememberTool(store=store).execute(
        {"content": "api_key = abc12345678", "kind": "fact"},
        ToolContext(session_id="sess-1"),
    )
    assert result["saved"] is False
    assert "拒绝" in result["output"]
    assert store.list() == []


async def test_memory_forget_missing_entry_reports_false(store):
    result = await MemoryForgetTool(store=store).execute({"id": "missing"}, ToolContext())
    assert result["deleted"] is False


def test_memory_tool_risk_levels():
    assert MemorySearchTool().risk_level is RiskLevel.LOW
    assert MemoryRememberTool().risk_level is RiskLevel.LOW
    assert MemoryForgetTool().risk_level is RiskLevel.MEDIUM


# ── HTTP API ──────────────────────────────────────────────────────


@pytest.fixture()
def api_client(memory_db):
    app = FastAPI()
    app.include_router(memory_router)

    def database():
        db = memory_db()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: {
        "provider": None,
        "api_key": None,
        "anonymous": True,
    }
    with TestClient(app) as client:
        yield client


def test_api_crud_and_search(api_client):
    created = api_client.post(
        "/api/memory",
        json={
            "scope": "project",
            "scope_key": "proj-a",
            "kind": "preference",
            "content": "Use PlatformIO for ESP32 builds.",
            "structured": {"tool": "platformio"},
            "confidence": 0.9,
        },
    )
    assert created.status_code == 201
    entry = created.json()["data"]
    assert entry["content"] == "Use PlatformIO for ESP32 builds."
    assert "deleted_at" not in entry

    listed = api_client.get("/api/memory", params={"scope": "project", "scope_key": "proj-a"})
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1

    searched = api_client.post("/api/memory/search", json={"query": "PlatformIO"})
    assert searched.status_code == 200
    assert searched.json()["data"]["results"][0]["id"] == entry["id"]

    patched = api_client.patch(
        f"/api/memory/{entry['id']}", json={"content": "Use PlatformIO exclusively."}
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["content"] == "Use PlatformIO exclusively."

    deleted = api_client.delete(f"/api/memory/{entry['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True

    after = api_client.get("/api/memory")
    assert after.json()["data"]["total"] == 0

    assert api_client.patch(
        f"/api/memory/{entry['id']}", json={"content": "Gone."}
    ).status_code == 404
    assert api_client.delete(f"/api/memory/{entry['id']}").status_code == 404


def test_api_patch_can_clear_expires_at(api_client):
    created = api_client.post(
        "/api/memory",
        json={
            "kind": "fact",
            "content": "Synthetic expiring note.",
            "expires_at": "2999-01-01T00:00:00Z",
        },
    )
    entry_id = created.json()["data"]["id"]
    assert created.json()["data"]["expires_at"].startswith("2999-01-01")

    patched = api_client.patch(f"/api/memory/{entry_id}", json={"expires_at": None})
    assert patched.status_code == 200
    assert patched.json()["data"]["expires_at"] is None


def test_api_rejects_secret_content_without_persisting(api_client):
    response = api_client.post(
        "/api/memory",
        json={"kind": "fact", "content": "token: 9f8e7d6c5b4a2c3d"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "MEMORY_SECRET_REJECTED"
    assert api_client.get("/api/memory").json()["data"]["total"] == 0


def test_api_rejects_invalid_kind(api_client):
    response = api_client.post(
        "/api/memory", json={"kind": "misc", "content": "Synthetic note."}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "INVALID_MEMORY"


def test_api_search_validates_scope(api_client):
    response = api_client.post(
        "/api/memory/search", json={"query": "anything", "scope": "user"}
    )
    assert response.status_code == 400
