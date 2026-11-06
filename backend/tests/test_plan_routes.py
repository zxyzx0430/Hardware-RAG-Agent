"""Plan HTTP routes: latest/history, approve/reject and session ownership."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.compaction_archive import compaction_principal
from app.api.crud import db_router
from app.api.dependencies import current_user_optional
from app.api.plan_routes import router
from app.db import database
from app.db.database import Base, get_db
from app.db.models import Session as SessionModel
from app.db.models import SessionIncarnation, SessionOwnerBinding
from app.db import plan_store

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
    with factory() as db:
        db.add(SessionModel(id="s1", title="A", model="test"))
        db.add(SessionModel(id="s2", title="B", model="test"))
        db.commit()

    app = FastAPI()
    app.include_router(router)
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


def test_latest_returns_null_when_absent(client):
    response = client.client.get("/api/sessions/s1/plans/latest")

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"plan": None}}


def test_latest_and_list_return_plan(client):
    plan = plan_store.create_version("s1", PAYLOAD, "r1")

    latest = client.client.get("/api/sessions/s1/plans/latest").json()
    listed = client.client.get("/api/sessions/s1/plans").json()

    assert latest["data"]["plan"]["id"] == plan["id"]
    assert latest["data"]["plan"]["steps"][0]["title"] == "Inspect"
    assert listed["data"]["plans"][0]["id"] == plan["id"]
    assert "steps" not in listed["data"]["plans"][0]


def test_approve_and_reject(client):
    approved = plan_store.create_version("s1", PAYLOAD, "r1")
    approve_response = client.client.post(f"/api/sessions/s1/plans/{approved['id']}/approve")

    rejected = plan_store.create_version("s1", {**PAYLOAD, "title": "Plan B"}, "r2")
    reject_response = client.client.post(f"/api/sessions/s1/plans/{rejected['id']}/reject")

    assert approve_response.json()["data"]["plan"]["status"] == "approved"
    assert reject_response.json()["data"]["plan"]["status"] == "rejected"


def test_stale_plan_approval_returns_409(client):
    first = plan_store.create_version("s1", PAYLOAD, "r1")
    plan_store.create_version("s1", {**PAYLOAD, "title": "Plan B"}, "r2")

    response = client.client.post(f"/api/sessions/s1/plans/{first['id']}/approve")

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "PLAN_NOT_PENDING"


def test_missing_session_returns_404(client):
    response = client.client.get("/api/sessions/missing/plans/latest")

    assert response.status_code == 404


def test_owner_mismatch_returns_403(client):
    with client.factory() as db:
        db.add(SessionModel(id="owned", title="owned", model="test"))
        db.add(SessionIncarnation(id="a" * 32))
        db.flush()
        db.add(SessionOwnerBinding(
            session_id="owned",
            owner_principal_hash=compaction_principal(
                {"provider": "p", "api_key": "k", "anonymous": False}
            ),
            incarnation_id="a" * 32,
        ))
        db.commit()

    response = client.client.get("/api/sessions/owned/plans/latest")

    assert response.status_code == 403
