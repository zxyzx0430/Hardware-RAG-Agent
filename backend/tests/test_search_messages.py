"""P1: /api/search and search_history share messages_fts by rowid."""

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

from app.api.search_routes import router as search_router
from app.db.database import Base
from app.db.models import Message as MessageModel
from app.db.models import Session as SessionModel
from src.agent.exceptions import ToolContext
from src.agent.session_search import search_messages
from src.agent.tools.groups.retrieval.search_history import SearchHistoryTool

_TS = datetime.datetime(2026, 11, 4, 10, 0, 0)
_FTS_DDL = (
    "CREATE VIRTUAL TABLE messages_fts USING fts5("
    "content, content='messages', content_rowid='rowid')",
    """
    CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
    END
    """,
    """
    CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES('delete', old.rowid, old.content);
    END
    """,
    """
    CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES('delete', old.rowid, old.content);
        INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
    END
    """,
)


@pytest.fixture()
def search_db(monkeypatch) -> Generator[sessionmaker, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for statement in _FTS_DDL:
            conn.execute(text(statement))
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.db.database.SessionLocal", testing_session)
    _seed(testing_session)
    yield testing_session
    engine.dispose()


def _seed(testing_session: sessionmaker) -> None:
    with testing_session() as db:
        db.add_all([
            SessionModel(id="s1", title="ESP32 调试", model="test"),
            SessionModel(id="s2", title="ESP8266 笔记", model="test"),
        ])
        db.add_all([
            MessageModel(id="m1", session_id="s1", role="user", content="ESP32 wiring guide", seq=1, created_at=_TS),
            MessageModel(id="m2", session_id="s1", role="assistant", content="I2C NACK fix", seq=2, created_at=_TS),
            MessageModel(id="m3", session_id="s2", role="user", content="ESP8266 note", seq=1, created_at=_TS - datetime.timedelta(hours=1)),
        ])
        db.commit()


def test_fts_join_matches_by_rowid(search_db) -> None:
    # "ESP*" is an FTS prefix query; LIKE '%ESP*%' never matches, so a hit here
    # proves the join uses m.rowid = f.rowid rather than the old m.id = f.rowid.
    hits = search_messages("ESP*", 10)

    assert {hit["message_id"] for hit in hits} == {"m1", "m3"}
    assert {hit["session_title"] for hit in hits} == {"ESP32 调试", "ESP8266 笔记"}


def test_substring_falls_back_to_like(search_db) -> None:
    hits = search_messages("SP32", 10)

    assert [hit["message_id"] for hit in hits] == ["m1"]
    assert hits[0]["content"] == "ESP32 wiring guide"


def test_empty_query_and_limit(search_db) -> None:
    assert search_messages("   ", 10) == []
    assert len(search_messages("ESP*", 1)) == 1


def test_search_route_returns_sequence_and_utc(search_db) -> None:
    app = FastAPI()
    app.include_router(search_router)
    with TestClient(app) as client:
        response = client.post("/api/search", json={"query": "ESP32", "limit": 10})

    payload = response.json()["data"]
    assert payload["total"] == 1
    hit = payload["results"][0]
    assert hit["message_id"] == "m1"
    assert hit["seq"] == 1
    assert hit["created_at"].endswith("Z")
    assert hit["session_title"] == "ESP32 调试"


async def test_search_history_tool_reads_same_rows(search_db) -> None:
    result = await SearchHistoryTool().execute({"query": "ESP32"}, ToolContext())

    assert [hit["message_id"] for hit in result["results"]] == ["m1"]
    assert "ESP32 wiring guide" in result["output"]
