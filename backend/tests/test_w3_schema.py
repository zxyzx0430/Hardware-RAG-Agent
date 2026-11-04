"""W3 P0: schema upgrade, message sequence/version, and UTC serialization."""

from __future__ import annotations

import datetime
from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.crud import db_router
from app.db.database import Base, get_db
from app.db.models import Message as MessageModel
from app.db.models import Session as SessionModel
from app.db.schema_w3 import SCHEMA_VERSION, apply_w3_schema
from app.db.time_utils import to_utc_iso_z


def _create_legacy_db(tmp_path) -> Engine:
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE sessions (id VARCHAR PRIMARY KEY, title VARCHAR, "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE messages (id VARCHAR PRIMARY KEY, session_id VARCHAR, "
            "role VARCHAR, content TEXT, created_at DATETIME)"
        ))
        conn.execute(text("INSERT INTO sessions (id) VALUES ('s1')"))
        conn.execute(text("INSERT INTO sessions (id) VALUES ('s2')"))
        conn.execute(text(
            "INSERT INTO messages VALUES "
            "('m1','s1','user','a','2026-11-04 10:00:00'),"
            "('m2','s1','assistant','b','2026-11-04 10:00:00'),"
            "('m3','s1','user','c','2026-11-04 10:00:01'),"
            "('m4','s2','user','d','2026-11-04 09:00:00')"
        ))
    return engine


def test_legacy_schema_upgrade_backfills_sequence(tmp_path) -> None:
    engine = _create_legacy_db(tmp_path)
    with engine.begin() as conn:
        apply_w3_schema(conn)
    with engine.connect() as conn:
        version = conn.execute(text("PRAGMA user_version")).scalar()
        rows = conn.execute(text(
            "SELECT id, seq FROM messages ORDER BY session_id, seq"
        )).all()
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(messages)"))}
    engine.dispose()

    assert version == SCHEMA_VERSION
    assert [(row[0], row[1]) for row in rows] == [
        ("m1", 1), ("m2", 2), ("m3", 3), ("m4", 1),
    ]
    assert {"seq", "updated_at", "status", "client_key", "tool_call_id", "tool_name"} <= columns


def test_schema_upgrade_is_idempotent(tmp_path) -> None:
    engine = _create_legacy_db(tmp_path)
    with engine.begin() as conn:
        apply_w3_schema(conn)
        conn.execute(text("UPDATE messages SET seq = 99 WHERE id = 'm1'"))
        apply_w3_schema(conn)
    with engine.connect() as conn:
        seq = conn.execute(text("SELECT seq FROM messages WHERE id = 'm1'")).scalar()
    engine.dispose()

    assert seq == 99


def test_to_utc_iso_z_marks_naive_and_converts_aware() -> None:
    assert to_utc_iso_z(datetime.datetime(2026, 11, 4, 12, 0, 0)) == "2026-11-04T12:00:00Z"
    china = datetime.timezone(datetime.timedelta(hours=8))
    aware = datetime.datetime(2026, 11, 4, 20, 0, 0, tzinfo=china)
    assert to_utc_iso_z(aware) == "2026-11-04T12:00:00Z"
    assert to_utc_iso_z(None) == ""


@pytest.fixture()
def db_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def client(db_engine: Engine) -> Generator[TestClient, None, None]:
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    with testing_session() as db:
        db.add(SessionModel(id="s1", title="新对话", model="test"))
        db.commit()

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(db_router)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_message_sequence_and_version(client: TestClient) -> None:
    first = client.post(
        "/api/sessions/s1/messages",
        json={"id": "m1", "role": "user", "content": "问题"},
    ).json()["data"]
    second = client.post(
        "/api/sessions/s1/messages",
        json={"id": "m2", "role": "assistant", "content": "回答"},
    ).json()["data"]
    retried = client.post(
        "/api/sessions/s1/messages",
        json={"id": "m2", "role": "assistant", "content": "回答更新"},
    ).json()["data"]

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert retried["seq"] == 2
    assert first["created_at"].endswith("Z")

    messages = client.get("/api/sessions/s1/messages").json()["data"]
    assert [message["seq"] for message in messages["messages"]] == [1, 2]
    assert messages["messages"][1]["content"] == "回答更新"
    assert messages["messages"][1]["status"] == "complete"
    assert messages["version"] == 3
    assert client.get("/api/sessions/s1").json()["data"]["version"] == 3


def test_same_timestamp_orders_by_sequence(db_engine: Engine) -> None:
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    timestamp = datetime.datetime(2026, 11, 4, 12, 0, 0)
    with testing_session() as db:
        db.add(SessionModel(id="s1", title="新对话", model="test"))
        db.add_all([
            MessageModel(id="b", session_id="s1", role="user", content="second", seq=2, created_at=timestamp),
            MessageModel(id="a", session_id="s1", role="user", content="first", seq=1, created_at=timestamp),
        ])
        db.commit()

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(db_router)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        messages = test_client.get("/api/sessions/s1/messages").json()["data"]["messages"]

    assert [message["id"] for message in messages] == ["a", "b"]
