"""P2: session deletion cascades and doc/KB big_chunks ownership."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.crud import db_router
from app.api.session_epoch import _session_prefix
from app.db.database import Base, get_db
from app.db.models import (
    BigChunk,
    Feedback,
    KnowledgeBase,
    KnowledgeDoc,
    Message as MessageModel,
    Session as SessionModel,
    SessionTombstone,
    Settings,
    TokenUsage,
    ToolAudit,
)
from src.agent import agent_factory
from src.rag.kb_manager import KnowledgeBaseManager


@pytest.fixture()
def testing_session() -> Generator[sessionmaker, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(testing_session, monkeypatch) -> Generator[TestClient, None, None]:
    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    async def fake_delete_thread(_thread_id: str) -> bool:
        return True

    monkeypatch.setattr(agent_factory, "delete_thread_checkpoint", fake_delete_thread)
    app = FastAPI()
    app.include_router(db_router)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _seed_session_rows(factory: sessionmaker) -> None:
    session_digest = hashlib.sha256(b"s1").hexdigest()
    ref_digest = "b" * 64
    with factory() as db:
        db.add(SessionModel(id="s1", title="待删除", model="test"))
        db.add(SessionModel(id="s2", title="保留", model="test"))
        db.add(MessageModel(id="m1", session_id="s1", role="user", content="a", seq=1))
        db.add(Feedback(session_id="s1", message_id="m1", rating=1))
        db.add(TokenUsage(session_id="s1", model="test", total_tokens=10))
        db.add(ToolAudit(session_id="s1", tool_name="read_file"))
        db.add(Settings(key=f"{_session_prefix('s1')}event", value="1"))
        db.add(Settings(
            key=f"_toolObservation:v1:meta:{'a' * 64}:{session_digest}:{ref_digest}",
            value=json.dumps({"reference": "r", "body_bytes": 1}),
        ))
        db.add(Settings(key=f"_toolObservation:v1:body:{ref_digest}", value='{"x":1}'))
        db.add(Settings(
            key=f"_toolObservation:v1:meta:{'a' * 64}:{hashlib.sha256(b's2').hexdigest()}:{'c' * 64}",
            value=json.dumps({"reference": "r2", "body_bytes": 1}),
        ))
        db.commit()


def _count(factory: sessionmaker, model) -> int:
    with factory() as db:
        return db.query(func.count()).select_from(model).scalar()


def test_session_delete_cascades_all_derived_rows(client, testing_session) -> None:
    _seed_session_rows(testing_session)

    response = client.delete("/api/sessions/s1")

    assert response.status_code == 200
    assert _count(testing_session, MessageModel) == 0
    assert _count(testing_session, Feedback) == 0
    assert _count(testing_session, TokenUsage) == 0
    assert _count(testing_session, ToolAudit) == 0
    assert _count(testing_session, SessionTombstone) == 1
    with testing_session() as db:
        keys = {key for (key,) in db.query(Settings.key).all()}
    assert not any(key.startswith(_session_prefix("s1")) for key in keys)
    assert not any(":body:" in key for key in keys if hashlib.sha256(b"s1").hexdigest() in key)
    assert f"_toolObservation:v1:meta:{'a' * 64}:{hashlib.sha256(b's2').hexdigest()}:{'c' * 64}" in keys


def test_tombstone_blocks_recreation_and_message_writes(client, testing_session) -> None:
    _seed_session_rows(testing_session)
    client.delete("/api/sessions/s1")

    recreated = client.post("/api/sessions", json={"id": "s1", "title": "幽灵"})
    written = client.post("/api/sessions/s1/messages", json={"id": "m2", "role": "user", "content": "x"})

    assert recreated.status_code == 410
    assert written.status_code == 410
    assert _count(testing_session, SessionModel) == 1  # only s2


def test_repeated_delete_is_idempotent(client, testing_session) -> None:
    _seed_session_rows(testing_session)

    assert client.delete("/api/sessions/s1").status_code == 200
    assert client.delete("/api/sessions/s1").status_code == 200


def test_delete_thread_checkpoint_uses_saver_api(monkeypatch) -> None:
    deleted: list[str] = []

    class FakeSaver:
        async def adelete_thread(self, thread_id: str) -> None:
            deleted.append(thread_id)

    async def fake_checkpointer():
        return FakeSaver()

    monkeypatch.setattr(agent_factory, "_get_checkpointer", fake_checkpointer)

    assert asyncio.run(agent_factory.delete_thread_checkpoint("s1")) is True
    assert deleted == ["s1"]


class _FakeStore:
    def __init__(self, delete_result: bool = True) -> None:
        self.delete_result = delete_result
        self.deleted_docs: list[str] = []

    def delete_document(self, doc_id: str) -> int:
        self.deleted_docs.append(doc_id)
        return 1

    def delete_collection(self):
        return self.delete_result


class _FakeManager:
    def __init__(self, store: _FakeStore | None) -> None:
        self.store = store
        self._bm25_stale: set[str] = set()

    def get_kb(self, _kb_id: str):
        return object()

    def _get_store(self, _kb) -> _FakeStore | None:
        return self.store

    def _rebuild_bm25(self, _kb_id: str) -> None:
        return None


def _seed_docs(factory: sessionmaker) -> None:
    with factory() as db:
        db.add(KnowledgeBase(id="kb1", name="KB", collection_name="kb_w3_unique"))
        db.add(KnowledgeDoc(doc_id="doc1", kb_id="kb1", title="a.md"))
        db.add(KnowledgeDoc(doc_id="doc2", kb_id="kb1", title="b.md"))
        db.add(BigChunk(big_chunk_id="doc1#b0", doc_id="doc1", kb_id="kb1", text="a"))
        db.add(BigChunk(big_chunk_id="doc2#b0", doc_id="doc2", kb_id="kb1", text="b"))
        db.commit()


def test_doc_delete_removes_big_chunks_even_without_store(testing_session, monkeypatch) -> None:
    from contextlib import contextmanager

    import app.api.kb_routes as kb_routes

    _seed_docs(testing_session)
    manager = _FakeManager(store=None)

    @contextmanager
    def fake_db_ctx():
        db = testing_session()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    monkeypatch.setattr(kb_routes, "get_db_ctx", fake_db_ctx)
    monkeypatch.setattr(kb_routes, "_get_kb_manager", lambda: manager)

    result = kb_routes._delete_document_sync("doc1")

    assert result["success"] is True
    with testing_session() as db:
        assert db.query(BigChunk).filter(BigChunk.doc_id == "doc1").count() == 0
        assert db.query(BigChunk).filter(BigChunk.doc_id == "doc2").count() == 1


def test_kb_delete_keeps_rows_when_chroma_fails(testing_session) -> None:
    _seed_docs(testing_session)
    manager = KnowledgeBaseManager(db_session_factory=testing_session)
    manager._get_store = lambda _kb: _FakeStore(delete_result=False)

    assert manager.delete_kb("kb1") is False
    with testing_session() as db:
        assert db.query(KnowledgeBase).filter(KnowledgeBase.id == "kb1").count() == 1
        assert db.query(BigChunk).filter(BigChunk.kb_id == "kb1").count() == 2


def test_kb_delete_removes_docs_and_chunks_in_one_transaction(testing_session) -> None:
    _seed_docs(testing_session)
    manager = KnowledgeBaseManager(db_session_factory=testing_session)
    manager._get_store = lambda _kb: _FakeStore(delete_result=True)

    assert manager.delete_kb("kb1") is True
    with testing_session() as db:
        assert db.query(KnowledgeBase).filter(KnowledgeBase.id == "kb1").count() == 0
        assert db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == "kb1").count() == 0
        assert db.query(BigChunk).filter(BigChunk.kb_id == "kb1").count() == 0
