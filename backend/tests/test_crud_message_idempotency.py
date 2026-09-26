"""Message persistence tests use an isolated in-memory database only."""

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.crud import db_router
from app.db.database import Base, get_db
from app.db.models import Session as SessionModel


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with TestingSession() as db:
        db.add(SessionModel(id="session-1", title="新对话", model="test"))
        db.add(SessionModel(id="session-2", title="另一个会话", model="test"))
        db.commit()

    app = FastAPI()
    app.include_router(db_router)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


def test_message_post_with_client_id_is_idempotent(client: TestClient) -> None:
    user = client.post(
        "/api/sessions/session-1/messages",
        json={"id": "user-1", "role": "user", "content": "问题"},
    )
    assistant = client.post(
        "/api/sessions/session-1/messages",
        json={
            "id": "assistant-1",
            "role": "assistant",
            "content": "回答的第一版",
            "sources": [{"id": "src-1", "title": "数据手册"}],
            "activity": {"status": "running", "steps": []},
        },
    )
    # The database commit may succeed even when the client misses the response.
    # Retrying the same message ID must update that row, not append a duplicate.
    retried = client.post(
        "/api/sessions/session-1/messages",
        json={
            "id": "assistant-1",
            "role": "assistant",
            "content": "回答完成",
            "sources": [{"id": "src-1", "title": "数据手册"}],
            "activity": {"status": "done", "steps": []},
        },
    )

    assert user.status_code == assistant.status_code == retried.status_code == 200
    assert assistant.json()["data"]["id"] == retried.json()["data"]["id"] == "assistant-1"

    response = client.get("/api/sessions/session-1/messages")
    messages = response.json()["data"]["messages"]
    assert [message["id"] for message in messages] == ["user-1", "assistant-1"]
    assert messages[1]["content"] == "回答完成"
    assert messages[1]["sources"] == [{"id": "src-1", "title": "数据手册"}]
    assert messages[1]["activity"]["status"] == "done"
    session = client.get("/api/sessions/session-1").json()["data"]
    assert session["msg_count"] == 2


def test_client_message_id_cannot_move_between_sessions(client: TestClient) -> None:
    first = client.post(
        "/api/sessions/session-1/messages",
        json={"id": "stable-id", "role": "user", "content": "原问题"},
    )
    conflict = client.post(
        "/api/sessions/session-2/messages",
        json={"id": "stable-id", "role": "user", "content": "其他会话"},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    messages = client.get("/api/sessions/session-1/messages").json()["data"]["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "原问题"


def test_message_post_without_client_id_keeps_legacy_behavior(client: TestClient) -> None:
    response = client.post(
        "/api/sessions/session-1/messages",
        json={"role": "user", "content": "旧客户端请求"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["id"].startswith("m")
