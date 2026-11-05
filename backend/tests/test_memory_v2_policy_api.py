"""Memory v2 HTTP API: filters, review workflow, episodes, status, auth."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import current_user
from app.api.memory_routes import router as memory_router
from app.db import memory_models  # noqa: F401  registers memory tables on Base
from app.db import models  # noqa: F401  registers Settings and sessions
from app.db.database import Base, get_db
from app.db.memory_store import MemoryStore


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


def _database_override(memory_db):
    def database():
        db = memory_db()
        try:
            yield db
        finally:
            db.close()

    return database


@pytest.fixture()
def client(memory_db):
    app = FastAPI()
    app.include_router(memory_router)
    app.dependency_overrides[get_db] = _database_override(memory_db)
    app.dependency_overrides[current_user] = lambda: {
        "provider": None,
        "api_key": None,
        "anonymous": True,
    }
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def unauthenticated_client(memory_db):
    app = FastAPI()
    app.include_router(memory_router)
    app.dependency_overrides[get_db] = _database_override(memory_db)
    with TestClient(app) as test_client:
        yield test_client


def _proposal(store, **overrides):
    payload = {
        "tier": "project",
        "project_id": "proj-a",
        "kind": "decision",
        "content": "Use PlatformIO for builds.",
        "evidence": {"seal_id": "seal-1", "field": "decisions"},
    }
    payload.update(overrides)
    return store.upsert_proposed(payload)


def test_list_defaults_to_active_and_supports_filters(store, client):
    active = store.create(
        tier="project", project_id="proj-a", kind="fact", content="ACTIVE fact."
    )
    store.create(tier="user", kind="preference", content="USER preference.")
    proposed = _proposal(store)
    rejected = _proposal(store, content="Rejected candidate.", kind="fact")
    store.update_status(rejected["id"], "rejected")

    default = client.get("/api/memory").json()["data"]
    ids = {entry["id"] for entry in default["entries"]}
    assert active["id"] in ids
    assert proposed["id"] not in ids
    assert rejected["id"] not in ids

    proposed_list = client.get("/api/memory", params={"status": "proposed"}).json()["data"]
    assert [entry["id"] for entry in proposed_list["entries"]] == [proposed["id"]]

    user_list = client.get("/api/memory", params={"tier": "user"}).json()["data"]
    assert [entry["tier"] for entry in user_list["entries"]] == ["user"]

    project_list = client.get(
        "/api/memory", params={"tier": "project", "project_id": "proj-a"}
    ).json()["data"]
    assert [entry["project_id"] for entry in project_list["entries"]] == ["proj-a"]

    assert client.get("/api/memory", params={"status": "bogus"}).status_code == 400


def test_response_contains_v2_fields(store, client):
    entry = store.create(
        tier="project",
        project_id="proj-a",
        kind="decision",
        content="Field contract entry.",
        evidence={"seal_id": "seal-9"},
    )
    response = client.get("/api/memory", params={"project_id": "proj-a"})
    row = response.json()["data"]["entries"][0]
    assert row["id"] == entry["id"]
    assert row["tier"] == "project"
    assert row["status"] == "active"
    assert row["project_id"] == "proj-a"
    assert row["evidence"] == {"seal_id": "seal-9"}


def test_proposals_approve_and_reject(store, client):
    first = _proposal(store, content="Approve me.")
    second = _proposal(store, content="Reject me.")

    listed = client.get("/api/memory/proposals").json()["data"]
    assert listed["total"] == 2

    approved = client.post(f"/api/memory/proposals/{first['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "active"

    rejected = client.post(f"/api/memory/proposals/{second['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["data"]["status"] == "rejected"

    assert client.get("/api/memory/proposals").json()["data"]["total"] == 0

    illegal = client.post(f"/api/memory/proposals/{first['id']}/approve")
    assert illegal.status_code == 409
    assert illegal.json()["detail"]["error"]["code"] == "MEMORY_INVALID_TRANSITION"

    missing = client.post("/api/memory/proposals/missing/approve")
    assert missing.status_code == 404


def test_rejected_proposals_never_enter_context(store, client):
    from src.agent.memory_context import build_memory_context

    proposal = _proposal(store, content="PENDINGCANDIDATE should stay out.")
    block = build_memory_context(
        store=store, project_id="proj-a", project_key="", session_id=None
    )
    assert "PENDINGCANDIDATE" not in block
    client.post(f"/api/memory/proposals/{proposal['id']}/approve")
    block = build_memory_context(
        store=store, project_id="proj-a", project_key="", session_id=None
    )
    assert "PENDINGCANDIDATE" in block


def test_episodes_are_scoped_to_the_requested_session(store, client):
    store.create_snapshot(
        session_id="session-1",
        seal_id="seal-1",
        body={"goal": [{"text": "Session one goal."}], "completed": []},
        memory_epoch="",
        delete_epoch="",
    )
    store.create_snapshot(
        session_id="session-1",
        seal_id="seal-2",
        body={"goal": [{"text": "Stale session goal."}]},
        memory_epoch="old-epoch",
        delete_epoch="",
    )
    store.create_snapshot(
        session_id="session-2",
        seal_id="seal-3",
        body={"goal": [{"text": "Other session goal."}]},
    )

    response = client.get("/api/memory/episodes", params={"session_id": "session-1"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    goals = [episode["fields"].get("goal") for episode in data["episodes"]]
    assert "Other session goal." not in goals
    assert "Session one goal." in goals
    validity = {episode["seal_id"]: episode["valid"] for episode in data["episodes"]}
    assert validity["seal-1"] is True
    assert validity["seal-2"] is False

    assert client.get("/api/memory/episodes").status_code == 422


def test_status_reports_schema_and_working_snapshot(store, client):
    store.create(tier="user", kind="fact", content="Status fact.")
    _proposal(store)
    store.create_snapshot(
        session_id="session-1",
        seal_id="seal-1",
        body={"goal": [{"text": "Status goal."}], "completed": []},
    )
    response = client.get("/api/memory/status", params={"session_id": "session-1"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema_version"] == 2
    assert data["tables"] == {"memory_entries": True, "memory_snapshots": True}
    assert all(data["columns"].values())
    assert data["counts"]["active"] == 1
    assert data["counts"]["proposed"] == 1
    assert data["counts"]["snapshots"] == 1
    assert data["working_snapshot"]["fields"]["goal"] == "Status goal."
    assert data["working_snapshot"]["valid"] is True
    assert "home" in data["export"]


def test_create_with_tier_derives_project_id_and_stays_active(client):
    response = client.post(
        "/api/memory",
        json={"tier": "project", "kind": "fact", "content": "Tiered create."},
    )
    assert response.status_code == 201
    entry = response.json()["data"]
    assert entry["tier"] == "project"
    assert entry["status"] == "active"
    assert entry["project_id"]
    assert entry["scope"] == "project"


def test_tier_is_authoritative_over_legacy_scope(client):
    response = client.post(
        "/api/memory",
        json={
            "tier": "user",
            "scope": "project",
            "kind": "fact",
            "content": "Tier wins over legacy scope.",
        },
    )
    assert response.status_code == 201
    entry = response.json()["data"]
    assert entry["tier"] == "user"
    assert entry["scope"] == "global"
    assert entry["scope_key"] == ""


def test_create_rejects_hardware_profiles(client):
    response = client.post(
        "/api/memory",
        json={
            "kind": "fact",
            "content": "Structured hardware note.",
            "structured": {"type": "hardware_profile"},
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "MEMORY_HARDWARE_PROFILE"
    assert client.get("/api/memory").json()["data"]["total"] == 0


def test_review_routes_require_authentication(unauthenticated_client):
    configured = {"providers": {"openai": {"key": "x"}}, "sessions": {}}
    with patch("app.api.auth._load_store", return_value=configured):
        response = unauthenticated_client.get("/api/memory/proposals")
        assert response.status_code == 401
        assert response.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"
