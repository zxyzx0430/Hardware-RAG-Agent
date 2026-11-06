"""Todo HTTP routes: response contract, session existence and ownership."""

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
from app.api.todo_routes import router
from app.db import database
from app.db.database import Base, get_db
from app.db.models import Session as SessionModel
from app.db.models import SessionIncarnation, SessionOwnerBinding, Todo
from app.db.todo_store import list_todos, replace_todos

TODOS = [
    {"content": "第一步", "status": "completed", "priority": "high"},
    {"content": "第二步", "status": "pending", "priority": "low"},
]


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
        db.add(SessionModel(id="session-1", title="A", model="test"))
        db.add(SessionModel(id="session-2", title="B", model="test"))
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
        yield SimpleNamespace(client=test_client, app=app, factory=factory)
    engine.dispose()


def _owner(db, session_id: str, provider: str, key: str) -> None:
    db.add(SessionModel(id=session_id, title="owned", model="test"))
    db.add(SessionIncarnation(id="a" * 32))
    db.flush()
    db.add(SessionOwnerBinding(
        session_id=session_id,
        owner_principal_hash=compaction_principal(
            {"provider": provider, "api_key": key, "anonymous": False}
        ),
        incarnation_id="a" * 32,
    ))
    db.commit()


def test_get_returns_persisted_snapshot(client):
    replace_todos("session-1", TODOS)

    response = client.client.get("/api/sessions/session-1/todos")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["todos"] == TODOS
    assert body["data"]["updated_at"].endswith("Z")


def test_get_empty_session_returns_empty_list(client):
    response = client.client.get("/api/sessions/session-1/todos")

    assert response.status_code == 200
    assert response.json()["data"] == {"todos": [], "updated_at": None}


def test_get_unknown_session_returns_404(client):
    response = client.client.get("/api/sessions/missing/todos")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "NOT_FOUND"


def test_delete_clears_only_the_target_session(client):
    replace_todos("session-1", TODOS)
    replace_todos("session-2", [TODOS[0]])

    response = client.client.delete("/api/sessions/session-1/todos")

    assert response.status_code == 200
    assert response.json()["data"] == {"todos": [], "cleared": True}
    assert list_todos("session-1") == []
    assert [row["content"] for row in list_todos("session-2")] == ["第一步"]


def test_delete_is_idempotent_for_an_empty_list(client):
    response = client.client.delete("/api/sessions/session-1/todos")

    assert response.status_code == 200
    assert response.json()["data"] == {"todos": [], "cleared": True}


def test_delete_unknown_session_returns_404(client):
    response = client.client.delete("/api/sessions/missing/todos")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "NOT_FOUND"


def test_deleting_the_session_removes_its_todo_rows(client):
    replace_todos("session-1", TODOS)
    replace_todos("session-2", [TODOS[0]])

    response = client.client.delete("/api/sessions/session-1")

    assert response.status_code == 200
    assert list_todos("session-1") == []
    with client.factory() as db:
        assert db.query(Todo).filter(Todo.session_id == "session-1").count() == 0
    assert [row["content"] for row in list_todos("session-2")] == ["第一步"]


def test_owner_bound_session_is_readable_by_its_owner(client):
    with client.factory() as db:
        _owner(db, "owned-session", "provider", "key-1")
    replace_todos("owned-session", TODOS)
    client.app.dependency_overrides[current_user_optional] = lambda: {
        "provider": "provider", "api_key": "key-1", "anonymous": False,
    }

    response = client.client.get("/api/sessions/owned-session/todos")

    assert response.status_code == 200
    assert response.json()["data"]["todos"] == TODOS


def test_owner_bound_session_rejects_another_principal(client):
    with client.factory() as db:
        _owner(db, "owned-session", "provider", "key-1")
    replace_todos("owned-session", TODOS)
    client.app.dependency_overrides[current_user_optional] = lambda: {
        "provider": "provider", "api_key": "key-2", "anonymous": False,
    }

    get_response = client.client.get("/api/sessions/owned-session/todos")
    delete_response = client.client.delete("/api/sessions/owned-session/todos")

    assert get_response.status_code == 403
    assert delete_response.status_code == 403
    assert get_response.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert [row["content"] for row in list_todos("owned-session")] == ["第一步", "第二步"]


def test_owner_bound_session_rejects_anonymous_access(client):
    with client.factory() as db:
        _owner(db, "owned-session", "provider", "key-1")
    client.app.dependency_overrides[current_user_optional] = lambda: {
        "provider": None, "api_key": None, "anonymous": True,
    }

    response = client.client.get("/api/sessions/owned-session/todos")

    assert response.status_code == 403
