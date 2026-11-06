"""Message persistence tests use an isolated in-memory database only."""

from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.crud as crud_api
import app.api.rolling_history_owner_scope as rolling_history_owner_scope
from app.api.dependencies import current_user_optional
from app.api.crud import db_router
from app.db.database import Base, get_db
from app.db.models import Session as SessionModel


@pytest.fixture()
def client(monkeypatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    from app.db import database

    monkeypatch.setattr(database, "engine", engine)
    database._ensure_message_source_generation_schema()

    with TestingSession() as db:
        db.add(SessionModel(id="session-1", title="新对话", model="test"))
        db.add(SessionModel(id="session-2", title="另一个会话", model="test"))
        db.commit()

    app = FastAPI()
    app.include_router(db_router)
    app.state.testing_engine = engine

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


def test_plan_approval_message_never_overwrites_session_title(
    client: TestClient,
) -> None:
    original = "帮我在这块 ESP32-S3 上把 UART 日志跑通"
    client.post(
        "/api/sessions/session-1/messages",
        json={"id": "task-1", "role": "user", "content": original},
    )
    title_after_task = client.get("/api/sessions/session-1").json()["data"]["title"]
    assert title_after_task == original

    client.post(
        "/api/sessions/session-1/messages",
        json={
            "id": "approval-1",
            "role": "user",
            "content": "按照已批准的计划开始执行。",
        },
    )
    assert client.get("/api/sessions/session-1").json()["data"]["title"] == original

    # Later real user messages must not rename an already meaningful session.
    client.post(
        "/api/sessions/session-1/messages",
        json={"id": "task-2", "role": "user", "content": "补充：顺便检查引脚冲突"},
    )
    assert client.get("/api/sessions/session-1").json()["data"]["title"] == original


def test_plan_approval_as_first_message_keeps_placeholder_title(
    client: TestClient,
) -> None:
    client.patch("/api/sessions/session-2", json={"title": "新对话"})

    client.post(
        "/api/sessions/session-2/messages",
        json={
            "id": "approval-only",
            "role": "user",
            "content": "按照已批准的计划开始执行。",
        },
    )

    assert client.get("/api/sessions/session-2").json()["data"]["title"] == "新对话"


def test_first_message_names_a_frontend_default_title(client: TestClient) -> None:
    # The frontend creates new sessions as "新对话 · HH:MM"; that is still a
    # placeholder, so the first real task must become the session title.
    client.patch("/api/sessions/session-2", json={"title": "新对话 · 02:29"})

    client.post(
        "/api/sessions/session-2/messages",
        json={"id": "first-task", "role": "user", "content": "帮我检查串口日志"},
    )

    assert client.get("/api/sessions/session-2").json()["data"]["title"] == "帮我检查串口日志"


def test_delete_session_never_creates_the_async_checkpointer(
    client: TestClient, monkeypatch,
) -> None:
    import src.agent.agent_factory as agent_factory

    deleted: list[str] = []
    monkeypatch.setattr(
        agent_factory, "delete_thread_checkpoint_direct",
        lambda thread_id: deleted.append(thread_id) or True,
    )
    monkeypatch.setattr(agent_factory, "_global_checkpointer", None)

    assert client.delete("/api/sessions/session-1").status_code == 200

    assert deleted == ["session-1"]
    assert agent_factory._global_checkpointer is None


def test_message_post_without_client_id_keeps_legacy_behavior(client: TestClient) -> None:
    response = client.post(
        "/api/sessions/session-1/messages",
        json={"role": "user", "content": "旧客户端请求"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["id"].startswith("m")


def _set_authenticated_user(client: TestClient, user: dict) -> None:
    client.app.dependency_overrides[current_user_optional] = lambda: user


def _prepare_owner_history_test(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        rolling_history_owner_scope,
        "engine",
        client.app.state.testing_engine,
    )
    # The route helper and owner-scope engine share the in-memory test DB; this
    # replaces only the ancillary delete-epoch reader's production session.
    monkeypatch.setattr(crud_api, "read_session_delete_epoch", lambda _session_id: "")


def _create_owner_session(client: TestClient, session_id: str, owner: dict) -> None:
    _set_authenticated_user(client, owner)
    response = client.post("/api/sessions", json={"id": session_id})
    assert response.status_code == 200, response.text


def test_history_mode_bootstraps_empty_owner_history_and_reads_back_pointer(
    client: TestClient,
    monkeypatch,
) -> None:
    _prepare_owner_history_test(client, monkeypatch)
    owner = {"anonymous": False, "provider": "test", "api_key": "owner-1"}
    _create_owner_session(client, "owned-empty", owner)

    create_pointer = crud_api.create_rolling_history_v2_active_branch_pointer

    def commit_pointer_but_lose_response(**kwargs):
        create_pointer(**kwargs)
        return SimpleNamespace(
            outcome="outcome_unknown",
            pointer=None,
            branch=None,
            head=None,
        )

    monkeypatch.setattr(
        crud_api,
        "create_rolling_history_v2_active_branch_pointer",
        commit_pointer_but_lose_response,
    )

    mode = client.get("/api/sessions/owned-empty/history-mode")
    assert mode.status_code == 200
    assert mode.json()["data"]["mode"] == "owner_v1"
    incarnation_id = mode.json()["data"]["incarnation_id"]

    # The first response is accepted only after a separate pointer readback;
    # subsequent calls reuse that verified active branch.
    repeated = client.get("/api/sessions/owned-empty/history-mode")
    assert repeated.status_code == 200
    assert repeated.json()["data"]["mode"] == "owner_v1"
    assert repeated.json()["data"]["incarnation_id"] == incarnation_id

    appended = client.post(
        "/api/sessions/owned-empty/messages/append-v1",
        json={
            "id": "first-owner-message",
            "expected_incarnation_id": incarnation_id,
            "role": "user",
            "content": "首次 owner-bound 追加",
        },
    )
    assert appended.status_code == 200, appended.text
    assert appended.json()["data"]["status"] == "saved"


def test_history_mode_retries_unknown_root_commit_with_stable_identity(
    client: TestClient,
    monkeypatch,
) -> None:
    _prepare_owner_history_test(client, monkeypatch)
    owner = {"anonymous": False, "provider": "test", "api_key": "owner-2"}
    _create_owner_session(client, "owned-root-retry", owner)

    create_root = crud_api.create_rolling_history_v2_root
    calls = 0

    def commit_root_but_lose_first_response(**kwargs):
        nonlocal calls
        result = create_root(**kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated lost root response after commit")
        return result

    monkeypatch.setattr(
        crud_api,
        "create_rolling_history_v2_root",
        commit_root_but_lose_first_response,
    )

    first = client.get("/api/sessions/owned-root-retry/history-mode")
    assert first.status_code == 409
    assert first.json()["detail"]["error"]["code"] == "MESSAGE_CONFLICT"

    retried = client.get("/api/sessions/owned-root-retry/history-mode")
    assert retried.status_code == 200
    assert retried.json()["data"]["mode"] == "owner_v1"
    assert calls == 2

    reused = client.get("/api/sessions/owned-root-retry/history-mode")
    assert reused.status_code == 200
    assert reused.json()["data"]["mode"] == "owner_v1"
    assert calls == 2


def test_unindexed_owner_session_with_messages_stays_legacy_and_saves_via_owner_gate(
    client: TestClient,
    monkeypatch,
) -> None:
    _prepare_owner_history_test(client, monkeypatch)
    owner = {"anonymous": False, "provider": "test", "api_key": "owner-3"}
    _create_owner_session(client, "owned-legacy", owner)
    first = client.post(
        "/api/sessions/owned-legacy/messages",
        json={"id": "legacy-first", "role": "user", "content": "保留的旧消息"},
    )
    assert first.status_code == 200

    root_calls = 0

    def unexpected_root_call(**_kwargs):
        nonlocal root_calls
        root_calls += 1
        raise AssertionError("a populated legacy session must not get an empty root")

    monkeypatch.setattr(crud_api, "create_rolling_history_v2_root", unexpected_root_call)
    mode = client.get("/api/sessions/owned-legacy/history-mode")
    assert mode.status_code == 200
    assert mode.json()["data"] == {
        "session_id": "owned-legacy",
        "mode": "legacy",
        "server_history": True,
    }
    assert root_calls == 0

    followup = client.post(
        "/api/sessions/owned-legacy/messages",
        json={"id": "legacy-second", "role": "assistant", "content": "兼容保存"},
    )
    assert followup.status_code == 200, followup.text


def test_owner_bound_legacy_message_mutations_still_require_the_owner(
    client: TestClient,
    monkeypatch,
) -> None:
    _prepare_owner_history_test(client, monkeypatch)
    owner = {"anonymous": False, "provider": "test", "api_key": "owner-4"}
    _create_owner_session(client, "owned-guarded", owner)
    _set_authenticated_user(
        client,
        {"anonymous": False, "provider": "test", "key": "different-owner"},
    )

    append = client.post(
        "/api/sessions/owned-guarded/messages",
        json={"id": "forbidden-message", "role": "user", "content": "拒绝"},
    )
    truncate = client.delete("/api/sessions/owned-guarded/messages?keep_count=0")

    assert append.status_code == 403
    assert truncate.status_code == 403


def test_damaged_owner_history_pointer_fails_closed_without_legacy_fallback(
    client: TestClient,
    monkeypatch,
) -> None:
    _prepare_owner_history_test(client, monkeypatch)
    owner = {"anonymous": False, "provider": "test", "api_key": "owner-5"}
    _create_owner_session(client, "owned-damaged", owner)

    def damaged_pointer():
        raise ValueError("simulated malformed pointer state")

    monkeypatch.setattr(
        crud_api,
        "read_rolling_history_v2_active_branch_pointer",
        damaged_pointer,
    )
    response = client.get("/api/sessions/owned-damaged/history-mode")

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "MESSAGE_CONFLICT"
