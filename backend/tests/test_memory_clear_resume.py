"""Saved-memory deletion must invalidate old pending requests, not replay them."""

from dataclasses import replace
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.api import chat_routes, common, crud
from app.api.dependencies import current_user_optional
from app.db.database import Base, get_db
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry


@pytest.fixture
def memory_app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(common, "SessionLocal", factory)

    def database():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(crud.db_router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user_optional] = lambda: None
    captured = []

    async def build(_payload, _creds, long_term_memory=None, request_snapshot=None, **_kwargs):
        captured.append(long_term_memory)
        return object()

    async def resume(*_args, **_kwargs):
        from app.api.sse import sse_event
        yield sse_event("text", {"content": "synthetic resumed answer"})

    class _FakeWriterBinding:
        def __init__(self):
            self.scope = SimpleNamespace(owner_key="synthetic-thread")
            self.registry = SimpleNamespace(
                assert_scope=lambda *_a, **_k: None,
                acquire=lambda _scope: object(),
                release=lambda *_a, **_k: None,
                quarantine=lambda *_a, **_k: None,
            )

        def current_scope(self):
            return self.scope

    from src.agent import hitl_handler
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(
        chat_routes, "_build_request_writer_binding",
        lambda *_a, **_k: _FakeWriterBinding(),
    )
    monkeypatch.setattr(
        chat_routes, "_acquire_request_writer_lease", lambda _binding: object(),
    )
    monkeypatch.setattr(hitl_handler, "_detect_tools_interrupt", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", build)
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: {"model": "synthetic"})
    monkeypatch.setattr(chat_routes, "build_agent_config", lambda *_args: {})
    monkeypatch.setattr(hitl_handler, "resume_agent_after_user", resume)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, captured=captured, factory=factory)
    chat_routes._clear_pending_request_snapshot("clear-resume-test")
    engine.dispose()


def _pause(memory_app, memory="synthetic-old-memory"):
    assert memory_app.client.put("/api/settings", json={"longTermMemory": memory}).status_code == 200
    payload = chat_routes.ChatRequest(
        messages=[{"role": "user", "content": "synthetic request"}], session_id="clear-resume-test",
    )
    snapshot = replace(
        chat_routes._request_snapshot_from_payload(payload, memory),
        tool_context=ToolContext(session_id=payload.session_id, rag_source_registry=RagSourceRegistry()),
    )
    chat_routes._store_pending_request_snapshot(payload.session_id, snapshot)
    assert chat_routes._get_pending_request_snapshot(payload.session_id) is not None
    return payload, snapshot


async def _resume(payload):
    req = chat_routes.ResumeRequest(payload=payload, decision="allow")
    request = Request({"type": "http", "method": "POST", "path": "/api/agent-sandbox/resume", "headers": []})
    response = await chat_routes.resume_agent(req, request, user={"anonymous": True})
    return "".join([str(chunk) async for chunk in response.body_iterator])


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_after_clear", [False, True])
async def test_clear_prevents_old_memory_from_reaching_resumed_model(memory_app, replace_after_clear):
    payload, _snapshot = _pause(memory_app)
    assert memory_app.client.put("/api/settings", json={"longTermMemory": ""}).status_code == 200
    if replace_after_clear:
        assert memory_app.client.put("/api/settings", json={"longTermMemory": "synthetic-new"}).status_code == 200
    with pytest.raises(HTTPException) as error:
        await _resume(payload)
    assert error.value.status_code == 409
    assert error.value.detail["error"]["code"] == "HITL_REQUEST_SNAPSHOT_UNAVAILABLE"
    assert memory_app.captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", [None, "synthetic-replacement"])
async def test_ordinary_resume_keeps_original_snapshot(memory_app, edit):
    payload, _snapshot = _pause(memory_app)
    if edit is not None:
        assert memory_app.client.put("/api/settings", json={"longTermMemory": edit}).status_code == 200
    assert '"type": "done"' in await _resume(payload)
    assert memory_app.captured == ["synthetic-old-memory"]


def test_old_inflight_snapshot_cannot_be_stored_again_after_clear(memory_app):
    payload, snapshot = _pause(memory_app)
    assert memory_app.client.put("/api/settings", json={"longTermMemory": ""}).status_code == 200
    chat_routes._store_pending_request_snapshot(payload.session_id, snapshot)
    assert chat_routes._get_pending_request_snapshot(payload.session_id) is None


def test_rejected_clear_does_not_invalidate_pending_request(memory_app):
    payload, _snapshot = _pause(memory_app)
    response = memory_app.client.put("/api/settings", json={"longTermMemory": "", "topK": 0})
    assert response.status_code == 422
    assert chat_routes._get_pending_request_snapshot(payload.session_id) is not None


def test_failed_commit_does_not_publish_a_clear_epoch(memory_app):
    payload, _snapshot = _pause(memory_app)
    with memory_app.factory() as db:
        def fail_commit():
            raise RuntimeError("synthetic commit failure")
        db.commit = fail_commit
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            crud.update_settings({"longTermMemory": ""}, db=db, user=None)
        db.rollback()
    assert chat_routes._get_pending_request_snapshot(payload.session_id) is not None


def test_epoch_is_private_and_cannot_be_written_by_client(memory_app):
    assert memory_app.client.put("/api/settings", json={"longTermMemory": ""}).status_code == 200
    settings = memory_app.client.get("/api/settings").json()["data"]["settings"]
    assert settings["longTermMemory"] == ""
    assert "_longTermMemoryClearEpoch" not in settings
    assert memory_app.client.put("/api/settings", json={"_longTermMemoryClearEpoch": "spoof"}).status_code == 400


def test_database_failure_cannot_allow_old_memory_resume(memory_app, monkeypatch):
    payload, _snapshot = _pause(memory_app)
    def unavailable():
        raise RuntimeError("synthetic database unavailable")
    monkeypatch.setattr(common, "SessionLocal", unavailable)
    assert chat_routes._get_pending_request_snapshot(payload.session_id) is None


@pytest.mark.asyncio
async def test_no_memory_request_remains_resumable_after_clear(memory_app):
    payload, _snapshot = _pause(memory_app, memory="")
    assert memory_app.client.put("/api/settings", json={"longTermMemory": ""}).status_code == 200
    await _resume(payload)
    assert memory_app.captured == [""]


@pytest.mark.parametrize("value", [None, "malformed", "a" * 31, "A" * 32])
def test_corrupt_epoch_fails_closed(memory_app, value):
    from app.db.models import Settings
    from app.api.memory_epoch import MEMORY_CLEAR_EPOCH_KEY
    payload, _snapshot = _pause(memory_app)
    with memory_app.factory.begin() as db:
        db.add(Settings(key=MEMORY_CLEAR_EPOCH_KEY, value=value))
    assert chat_routes._get_pending_request_snapshot(payload.session_id) is None


def test_clear_in_another_database_engine_invalidates_snapshot(memory_app, monkeypatch, tmp_path):
    from app.api.memory_epoch import stage_memory_clear_epoch
    from app.db.models import Settings
    import datetime

    path = tmp_path / "shared-memory.sqlite3"
    first = create_engine(f"sqlite:///{path}")
    second = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(first)
    factory = sessionmaker(bind=first)
    monkeypatch.setattr(common, "SessionLocal", factory)
    payload = chat_routes.ChatRequest(messages=[{"role": "user", "content": "synthetic"}], session_id="clear-resume-test")
    snapshot = replace(chat_routes._request_snapshot_from_payload(payload, "old-memory"),
                       tool_context=ToolContext(session_id=payload.session_id, rag_source_registry=RagSourceRegistry()))
    try:
        chat_routes._store_pending_request_snapshot(payload.session_id, snapshot)
        assert chat_routes._get_pending_request_snapshot(payload.session_id) is not None
        with sessionmaker(bind=second).begin() as db:
            stage_memory_clear_epoch(db, datetime.datetime.utcnow())
            db.add(Settings(key="longTermMemory", value=""))
        assert chat_routes._get_pending_request_snapshot(payload.session_id) is None
    finally:
        first.dispose()
        second.dispose()


@pytest.mark.asyncio
async def test_clear_during_async_preparation_cannot_restamp_old_memory(memory_app, monkeypatch):
    from app.api.sse import sse_event

    payload, old = _pause(memory_app)
    observed = []

    async def prepare(*_args):
        assert memory_app.client.put("/api/settings", json={"longTermMemory": ""}).status_code == 200
        return None, None

    async def agent_stream(_payload, _creds, long_term_memory=None, request_snapshot=None, **_kwargs):
        observed.append(request_snapshot.memory_clear_epoch)
        chat_routes._store_pending_request_snapshot(payload.session_id, replace(
            request_snapshot, tool_context=old.tool_context,
        ))
        yield sse_event("tool_confirm_required", {"calls": [], "count": 0})

    monkeypatch.setattr(chat_routes, "_prepare_chat_skill_install", prepare)
    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: old.long_term_memory)
    monkeypatch.setattr(chat_routes, "resolve_credentials", lambda *_args: {
        "api_key": "synthetic", "base_url": "http://unused.invalid", "model": "synthetic", "provider": "synthetic",
    })
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: True)
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: object())
    monkeypatch.setattr(chat_routes, "_run_agent_stream", agent_stream)
    request = Request({"type": "http", "method": "POST", "path": "/api/chat", "headers": []})
    with pytest.raises(HTTPException) as error:
        await chat_routes.chat_sse(payload, request, user={})
    assert error.value.status_code == 503
    assert observed == []
    assert chat_routes._get_pending_request_snapshot(payload.session_id) is None
