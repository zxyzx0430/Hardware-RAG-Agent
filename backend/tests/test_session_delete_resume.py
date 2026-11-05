"""Deleting a persisted session invalidates its old paused Agent request."""

from dataclasses import replace
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
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
def session_app(monkeypatch):
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

    async def build(*_args, **_kwargs):
        captured.append("synthetic Agent constructed")
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
    yield SimpleNamespace(app=app, captured=captured, factory=factory)
    for key in ("delete-resume-a", "delete-resume-b"):
        chat_routes._clear_pending_request_snapshot(key)
    engine.dispose()


def paused_request(session_id="delete-resume-a"):
    payload = chat_routes.ChatRequest(messages=[{"role": "user", "content": "synthetic pending request"}],
                                      session_id=session_id)
    snapshot = replace(chat_routes._request_snapshot_from_payload(payload, ""), tool_context=ToolContext(
        session_id=session_id, rag_source_registry=RagSourceRegistry(),
    ))
    chat_routes._store_pending_request_snapshot(session_id, snapshot)
    assert chat_routes._get_pending_request_snapshot(session_id) is not None
    return payload, snapshot


async def resume_request(payload):
    req = chat_routes.ResumeRequest(payload=payload, decision="allow")
    request = Request({"type": "http", "method": "POST", "path": "/api/agent-sandbox/resume", "headers": []})
    response = await chat_routes.resume_agent(req, request, user={"anonymous": True})
    return "".join([str(chunk) async for chunk in response.body_iterator])


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_first", [False, True])
async def test_session_delete_prevents_old_request_resume(session_app, delete_first):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=session_app.app), base_url="http://synthetic") as client:
        assert (await client.post("/api/sessions", json={"id": "delete-resume-a"})).status_code == 200
        payload, _snapshot = paused_request()
        if delete_first:
            assert (await client.delete("/api/sessions/delete-resume-a")).status_code == 200
            assert (await client.get("/api/sessions/delete-resume-a")).status_code == 404
            with pytest.raises(HTTPException) as error:
                await resume_request(payload)
            assert error.value.status_code == 409
            assert error.value.detail["error"]["code"] == "HITL_REQUEST_SNAPSHOT_UNAVAILABLE"
            assert session_app.captured == []
        else:
            assert '"type": "done"' in await resume_request(payload)
            assert len(session_app.captured) == 1


@pytest.mark.asyncio
async def test_deleted_request_cannot_be_restored_by_reinsert_or_same_id_recreation(session_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=session_app.app), base_url="http://synthetic") as client:
        await client.post("/api/sessions", json={"id": "delete-resume-a"})
        old_payload, old_snapshot = paused_request()
        await client.delete("/api/sessions/delete-resume-a")
        chat_routes._store_pending_request_snapshot(old_payload.session_id, old_snapshot)
        assert chat_routes._get_pending_request_snapshot(old_payload.session_id) is None
        assert (await client.post("/api/sessions", json={"id": "delete-resume-a"})).status_code == 410
        chat_routes._store_pending_request_snapshot(old_payload.session_id, old_snapshot)
        with pytest.raises(HTTPException) as error:
            await resume_request(old_payload)
        assert error.value.status_code == 409 and session_app.captured == []
        fresh_payload, _fresh_snapshot = paused_request("delete-resume-b")
        assert '"type": "done"' in await resume_request(fresh_payload)
        assert len(session_app.captured) == 1


@pytest.mark.asyncio
async def test_deletion_does_not_invalidate_another_session(session_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=session_app.app), base_url="http://synthetic") as client:
        for session_id in ("delete-resume-a", "delete-resume-b"):
            await client.post("/api/sessions", json={"id": session_id})
        _payload_a, _snapshot_a = paused_request()
        payload_b, _snapshot_b = paused_request("delete-resume-b")
        assert (await client.delete("/api/sessions/delete-resume-a")).status_code == 200
        assert '"type": "done"' in await resume_request(payload_b)
        assert len(session_app.captured) == 1


@pytest.mark.asyncio
async def test_failed_delete_commit_leaves_request_and_session_valid(session_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=session_app.app), base_url="http://synthetic") as client:
        await client.post("/api/sessions", json={"id": "delete-resume-a"})
        payload, _snapshot = paused_request()
        with session_app.factory() as db:
            def fail_commit():
                raise RuntimeError("synthetic delete commit failure")
            db.commit = fail_commit
            with pytest.raises(RuntimeError, match="synthetic delete commit failure"):
                crud.delete_session(payload.session_id, db=db, user=None)
            db.rollback()
        assert (await client.get("/api/sessions/delete-resume-a")).status_code == 200
        assert '"type": "done"' in await resume_request(payload)


@pytest.mark.asyncio
async def test_delete_marker_is_private_and_client_cannot_spoof_it(session_app):
    import hashlib
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=session_app.app), base_url="http://synthetic") as client:
        await client.post("/api/sessions", json={"id": "delete-resume-a"})
        await client.delete("/api/sessions/delete-resume-a")
        key = "_sessionDeleteEpoch:" + hashlib.sha256(b"delete-resume-a").hexdigest()
        exposed = (await client.get("/api/settings")).json()["data"]["settings"]
        assert not any(name.startswith("_sessionDeleteEpoch:") for name in exposed)
        assert (await client.put("/api/settings", json={key: "spoof"})).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", None, "malformed", "A" * 32])
async def test_corrupt_persisted_deletion_marker_fails_closed(session_app, value):
    import hashlib
    from app.db.models import Settings

    payload, _snapshot = paused_request()
    key = "_sessionDeleteEpoch:" + hashlib.sha256(payload.session_id.encode("utf-8")).hexdigest()
    with session_app.factory.begin() as db:
        db.add(Settings(key=key, value=value))
    with pytest.raises(HTTPException) as error:
        await resume_request(payload)
    assert error.value.status_code == 409 and session_app.captured == []


@pytest.mark.asyncio
async def test_unavailable_generation_database_rejects_resume(session_app, monkeypatch):
    payload, _snapshot = paused_request()

    def unavailable():
        raise RuntimeError("synthetic generation database unavailable")

    monkeypatch.setattr(common, "SessionLocal", unavailable)
    with pytest.raises(HTTPException) as error:
        await resume_request(payload)
    assert error.value.status_code == 409 and session_app.captured == []


@pytest.mark.asyncio
async def test_unreadable_generation_at_capture_cannot_be_late_stamped(session_app, monkeypatch):
    with monkeypatch.context() as scoped:
        scoped.setattr(common, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("synthetic unavailable")))
        payload = chat_routes.ChatRequest(messages=[{"role": "user", "content": "synthetic"}],
                                          session_id="delete-resume-a")
        snapshot = replace(chat_routes._request_snapshot_from_payload(payload, ""), tool_context=ToolContext(
            session_id=payload.session_id, rag_source_registry=RagSourceRegistry(),
        ))
    chat_routes._store_pending_request_snapshot(payload.session_id, snapshot)
    with pytest.raises(HTTPException) as error:
        await resume_request(payload)
    assert error.value.status_code == 409 and session_app.captured == []


@pytest.mark.asyncio
async def test_missing_session_delete_does_not_invalidate_unpersisted_request(session_app):
    payload, _snapshot = paused_request()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=session_app.app), base_url="http://synthetic") as client:
        assert (await client.delete("/api/sessions/delete-resume-a")).status_code == 404
    assert '"type": "done"' in await resume_request(payload)


@pytest.mark.asyncio
async def test_delete_during_async_preparation_does_not_restamp_old_request(session_app, monkeypatch):
    from app.api.sse import sse_event

    payload, old_snapshot = paused_request()
    observed = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=session_app.app), base_url="http://synthetic") as client:
        await client.post("/api/sessions", json={"id": payload.session_id})

        async def prepare(*_args):
            assert (await client.delete(f"/api/sessions/{payload.session_id}")).status_code == 200
            return None, None

        async def agent_stream(_payload, _creds, long_term_memory=None, request_snapshot=None, **_kwargs):
            observed.append(request_snapshot.session_delete_epoch)
            chat_routes._store_pending_request_snapshot(payload.session_id, replace(
                request_snapshot, tool_context=old_snapshot.tool_context,
            ))
            yield sse_event("tool_confirm_required", {"calls": [], "count": 0})

        monkeypatch.setattr(chat_routes, "_prepare_chat_skill_install", prepare)
        monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
        monkeypatch.setattr(chat_routes, "resolve_credentials", lambda *_args: {
            "api_key": "synthetic", "base_url": "http://synthetic.invalid", "model": "synthetic", "provider": "synthetic",
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


@pytest.mark.asyncio
async def test_delete_generation_is_observed_by_another_file_database_engine(session_app, monkeypatch, tmp_path):
    path = tmp_path / "session-generation.sqlite3"
    engine_a = create_engine(f"sqlite:///{path}")
    engine_b = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine_a)
    factory_a, factory_b = sessionmaker(bind=engine_a), sessionmaker(bind=engine_b)
    monkeypatch.setattr(common, "SessionLocal", factory_a)
    try:
        with factory_a() as db:
            crud.create_session(crud.SessionCreate(id="delete-resume-a"), db=db, user=None)
        payload, old_snapshot = paused_request()
        with factory_a() as db:
            crud.delete_session(payload.session_id, db=db, user=None)
        monkeypatch.setattr(common, "SessionLocal", factory_b)
        chat_routes._store_pending_request_snapshot(payload.session_id, old_snapshot)
        with pytest.raises(HTTPException) as error:
            await resume_request(payload)
        assert error.value.status_code == 409 and session_app.captured == []
        # Tombstones survive the engine boundary and keep the ID retired.
        with factory_b() as db, pytest.raises(HTTPException) as recreate_error:
            crud.create_session(crud.SessionCreate(id=payload.session_id), db=db, user=None)
        assert recreate_error.value.status_code == 410
        fresh_payload, _fresh_snapshot = paused_request("delete-resume-b")
        assert '"type": "done"' in await resume_request(fresh_payload)
    finally:
        engine_a.dispose()
        engine_b.dispose()
