"""Agent plan-mode marker: session API, approve/reject, deletion and clamps."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import chat_routes, crud
from app.api.chat_routes import (
    AgentRequestSnapshot,
    ChatRequest,
    _clamp_payload_to_agent_plan_mode,
    _clamp_snapshot_to_agent_plan_mode,
)
from app.api.crud import db_router
from app.api.plan_routes import router as plan_router
from app.db import agent_mode_store, database, plan_store
from app.db.database import Base, get_db
from app.db.models import Session as SessionModel
from src.agent import agent_factory
from src.agent.exceptions import ToolContext

PAYLOAD = {
    "title": "Bring up the board",
    "summary": "Inspect then execute",
    "steps": [{"title": "Inspect", "detail": "", "verification": "Cite pages"}],
    "risks": [],
    "open_questions": [],
}


@pytest.fixture()
def client(monkeypatch):
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
    monkeypatch.setattr(
        agent_factory, "delete_thread_checkpoint_direct", lambda _thread_id: True,
    )
    with factory() as db:
        db.add(SessionModel(id="s1", title="A", model="test"))
        db.add(SessionModel(id="s2", title="B", model="test"))
        db.commit()

    app = FastAPI()
    app.include_router(plan_router)
    app.include_router(db_router)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield SimpleNamespace(client=test_client, factory=factory)
    engine.dispose()


def test_session_payload_reports_the_marker_in_list_get_and_patch(client):
    agent_mode_store.request_plan_mode("s1")

    listed = client.client.get("/api/sessions").json()["data"]["sessions"]
    single = client.client.get("/api/sessions/s1").json()["data"]
    patched = client.client.patch("/api/sessions/s1", json={"title": "A2"}).json()["data"]

    by_id = {row["id"]: row for row in listed}
    assert by_id["s1"]["plan_mode_requested"] is True
    assert by_id["s2"]["plan_mode_requested"] is False
    assert single["plan_mode_requested"] is True
    assert patched["plan_mode_requested"] is True


def test_user_clear_and_set_endpoint(client):
    agent_mode_store.request_plan_mode("s1")

    cleared = client.client.post(
        "/api/sessions/s1/agent-plan-mode", json={"enabled": False},
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["plan_mode_requested"] is False
    assert agent_mode_store.plan_mode_requested("s1") is False

    enabled = client.client.post(
        "/api/sessions/s1/agent-plan-mode", json={"enabled": True},
    )

    assert enabled.json()["data"]["plan_mode_requested"] is True


def test_clear_endpoint_requires_an_existing_session(client):
    response = client.client.post(
        "/api/sessions/missing/agent-plan-mode", json={"enabled": False},
    )

    assert response.status_code == 404


def test_approve_clears_marker_and_reject_keeps_it(client):
    agent_mode_store.request_plan_mode("s1")
    rejected = plan_store.create_version("s1", PAYLOAD, "r1")
    client.client.post(f"/api/sessions/s1/plans/{rejected['id']}/reject")
    assert agent_mode_store.plan_mode_requested("s1") is True

    approved = plan_store.create_version("s1", {**PAYLOAD, "title": "Plan B"}, "r2")
    response = client.client.post(f"/api/sessions/s1/plans/{approved['id']}/approve")

    assert response.status_code == 200
    assert agent_mode_store.plan_mode_requested("s1") is False


def test_delete_session_purges_marker(client):
    agent_mode_store.request_plan_mode("s1")

    with client.factory() as db:
        crud.delete_session("s1", db=db, user=None)

    assert agent_mode_store.plan_mode_requested("s1") is False
    assert agent_mode_store.plan_mode_requested("s2") is False


def test_chat_payload_is_clamped_while_marker_exists(client):
    agent_mode_store.request_plan_mode("s1")
    payload = ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        session_id="s1",
        permission_mode="yolo",
    )

    clamped = _clamp_payload_to_agent_plan_mode(payload)

    assert clamped.permission_mode == "plan"
    agent_mode_store.clear_plan_mode_requested("s1")
    assert _clamp_payload_to_agent_plan_mode(payload).permission_mode == "yolo"


def test_resume_snapshot_is_clamped_with_its_tool_context(client):
    agent_mode_store.request_plan_mode("s1")
    snapshot = AgentRequestSnapshot(
        permission_mode="bypassPermissions",
        tool_context=ToolContext(permission_mode="bypassPermissions", session_id="s1"),
    )

    clamped = _clamp_snapshot_to_agent_plan_mode(snapshot, "s1")

    assert clamped.permission_mode == "plan"
    assert clamped.tool_context.permission_mode == "plan"
    assert clamped.tool_context is not snapshot.tool_context
    assert snapshot.tool_context.permission_mode == "bypassPermissions"


class _StopResume(Exception):
    pass


def test_resume_api_clamps_payload_and_agent_context_to_plan(client, monkeypatch):
    agent_mode_store.request_plan_mode("s1")
    snapshot = AgentRequestSnapshot(
        session_delete_epoch="session-epoch",
        memory_clear_epoch="memory-epoch",
        permission_mode="bypassPermissions",
        tool_context=ToolContext(
            permission_mode="bypassPermissions", session_id="s1",
        ),
        pending_call_ids=("call-1",),
    )
    captured: dict = {}

    async def fake_build(payload, creds, memory, request_snapshot=None, context_window_override=None):
        captured["payload_mode"] = payload.permission_mode
        captured["snapshot_mode"] = request_snapshot.permission_mode
        captured["ctx_mode"] = request_snapshot.tool_context.permission_mode
        raise _StopResume()

    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(
        chat_routes, "_capture_request_snapshot_epochs",
        lambda _session_id: ("session-epoch", "memory-epoch"),
    )
    monkeypatch.setattr(chat_routes, "_get_pending_request_snapshot", lambda _key: snapshot)
    monkeypatch.setattr(chat_routes, "_verified_principal_id", lambda _user: None)
    monkeypatch.setattr(chat_routes, "_require_resume_snapshot_current", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: {
        "api_key": "k", "base_url": "http://local", "model": "gpt-4o",
    })
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", fake_build)
    request = chat_routes.ResumeRequest(
        payload=ChatRequest(
            messages=[{"role": "user", "content": "confirm"}],
            session_id="s1",
            permission_mode="bypassPermissions",
        ),
        decision="deny",
    )

    with pytest.raises(_StopResume):
        asyncio.run(chat_routes.resume_agent(request, Mock(), user={"anonymous": True}))

    assert captured == {
        "payload_mode": "plan",
        "snapshot_mode": "plan",
        "ctx_mode": "plan",
    }
