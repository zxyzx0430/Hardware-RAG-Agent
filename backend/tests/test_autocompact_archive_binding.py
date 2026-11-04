"""Production compaction may replace a prefix only with committed host refs."""

from types import SimpleNamespace
from dataclasses import replace
from tests.structured_summary_support import structured_response
from tests.compaction_v3_source_support import (
    bind_v3_compaction_owner,
    seed_v3_message_sources,
)

import pytest
from langchain_core.messages import HumanMessage

from src.agent.compact.autocompact import autocompact_messages


class SummaryClient:
    model = "deepseek-chat"
    max_tokens = 1024

    async def chat(self, prompt, **_kwargs):
        return SimpleNamespace(content=structured_response(prompt))


def messages():
    return [HumanMessage(content="synthetic early constraint: do not flash " + str(i)) for i in range(14)]


@pytest.mark.asyncio
async def test_unbound_production_compaction_preserves_original():
    original = messages()
    result = await autocompact_messages(original, SummaryClient(), context_window=1)
    assert result is original


@pytest.mark.asyncio
async def test_bound_compaction_publishes_recoverable_refs_after_engine_reopen(context_archive_scope, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.api import common
    from app.api.context_events import EventReference, recover_context_event
    original = messages()
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )
    with bind_v3_compaction_owner(owner, scope):
        result = await autocompact_messages(
            original, SummaryClient(), context_window=1, source_message_ids=message_ids,
        )
    assert result is not original
    refs = result[0].additional_kwargs["hwrag_context_event_refs"]
    assert len(refs) == 4 and result[1:] == original[-10:]
    context_archive_scope.engine.dispose()
    engine = create_engine(f"sqlite:///{context_archive_scope.path}")
    monkeypatch.setattr(common, "SessionLocal", sessionmaker(bind=engine))
    try:
        for index, reference in enumerate(refs):
            recovered = recover_context_event(scope, EventReference(**reference),
                                              authorized=True, byte_budget=4096)
            assert recovered == {"role": "user", "content": original[index].content}
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_presend_conversion_retains_host_references(context_archive_scope, monkeypatch):
    from app.api import chat_routes
    from langchain_core.messages import convert_to_messages
    original = messages()
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )
    with bind_v3_compaction_owner(owner, scope):
        result = await autocompact_messages(
            original, SummaryClient(), context_window=1, source_message_ids=message_ids,
        )
    converted = chat_routes._langchain_messages_to_dicts(result)
    assert convert_to_messages(converted)[0].additional_kwargs == result[0].additional_kwargs


@pytest.mark.asyncio
async def test_verified_scoped_http_recovery(context_archive_scope, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.api import chat_routes
    from app.api.dependencies import current_user
    original = messages()
    owner, scoped, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )
    app = FastAPI()
    app.include_router(chat_routes.router)
    app.dependency_overrides[current_user] = lambda: owner
    with bind_v3_compaction_owner(owner, scoped):
        result = await autocompact_messages(
            original, SummaryClient(), context_window=1, source_message_ids=message_ids,
        )
    ref = result[0].additional_kwargs["hwrag_context_event_refs"][0]
    path = f"/api/sessions/{ref['session_id']}/context-events/{ref['batch_id']}/{ref['sequence']}"
    params = {"request_id": ref["request_id"], "content_sha256": ref["content_sha256"], "byte_budget": 4096}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic",
                                 headers={"Authorization": "Bearer synthetic-header-only"}) as client:
        response = await client.get(path, params=params)
        assert response.status_code == 200
        assert response.json()["data"]["event"] == {"role": "user", "content": messages()[0].content}
        assert response.json()["data"]["untrusted_history"] is True
        for query, headers in ((params | {"token": "synthetic-url-token"}, {}),
                               (params, {"Authorization": ""})):
            denied_transport = await client.get(path, params=query, headers=headers)
            assert denied_transport.status_code == 403
            assert denied_transport.json()["detail"]["error"]["code"] == "CONTEXT_EVENT_HEADER_AUTH_REQUIRED"
        for change in ({"request_id": "f" * 32}, {"content_sha256": "0" * 64}, {"byte_budget": 1}):
            assert (await client.get(path, params=params | change)).status_code == 404
        assert (await client.get(path, params=params | {"byte_budget": 32769})).status_code == 422
        app.dependency_overrides[current_user] = lambda: owner | {"api_key": "synthetic-other-owner"}
        denied = await client.get(path, params=params)
        assert denied.status_code == 404
        assert denied.json()["detail"]["error"]["code"] == "CONTEXT_EVENT_UNAVAILABLE"
        app.dependency_overrides[current_user] = lambda: {"anonymous": True}
        assert (await client.get(path, params=params)).status_code == 403
        app.dependency_overrides[current_user] = lambda: owner
        from app.api import crud
        with context_archive_scope.factory() as db:
            crud.delete_session(scoped.session_id, db=db, user=owner)
        assert (await client.get(path, params=params)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["pre_send", "recovery", "stream"])
@pytest.mark.parametrize("fail_commit", [False, True])
async def test_actual_callers_require_v3_ids_before_archive_commit(context_archive_scope, compaction_checkpoint, monkeypatch, path, fail_commit):
    from sqlalchemy.orm import Session, sessionmaker
    from app.api import common, chat_routes
    from app.db.models import Settings
    from src.agent.compact import autocompact
    from src.agent import sse_adapter
    updates = []
    original = messages()
    config = {"configurable": {"thread_id": context_archive_scope.scope.session_id, "context_window": 1}}

    graph, config, original = await compaction_checkpoint(original, 1)

    class FailedCommit(Session):
        def commit(self):
            raise RuntimeError("synthetic commit failure")

    source = None
    if path == "pre_send":
        source = seed_v3_message_sources(context_archive_scope, monkeypatch, original)
    if fail_commit:
        monkeypatch.setattr(common, "SessionLocal", sessionmaker(bind=context_archive_scope.engine, class_=FailedCommit))
    model = SummaryClient()
    if path == "pre_send":
        monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: model)
        owner, scope, source_message_ids = source
        with bind_v3_compaction_owner(owner, scope):
            result = await chat_routes._run_autocompact(
                original, {}, 1, source_message_ids=source_message_ids,
            )
        changed = result is not original
    elif path == "recovery":
        changed = await autocompact._recover_with_autocompact(graph, config, original, model)
        result = (await graph.aget_state(config)).values["messages"] if changed else original
    else:
        monkeypatch.setattr(autocompact, "_make_summary_client", lambda: model)
        changed = await sse_adapter._compact_and_apply({"agent": graph, "config": config}, original, 1)
        result = (await graph.aget_state(config)).values["messages"] if changed else original
    expected_change = path == "pre_send" and not fail_commit
    assert changed is expected_change
    if path != "pre_send":
        assert result is original
        with context_archive_scope.factory() as db:
            assert not db.query(Settings).filter(Settings.key.startswith("_contextEvent:")).count()
    elif fail_commit:
        assert result is original and updates == []
        assert (await graph.aget_state(config)).values["messages"] == original
        with context_archive_scope.factory() as db:
            assert not db.query(Settings).filter(Settings.key.startswith("_contextEvent:")).count()
    else:
        assert result[0].additional_kwargs["hwrag_context_event_refs"]


@pytest.mark.asyncio
async def test_delete_during_async_summary_does_not_publish_replacement(context_archive_scope, monkeypatch):
    from app.api import crud
    from app.db.models import Session, Settings

    original = messages()
    owner, scope, source_message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )

    class DeleteDuringSummary(SummaryClient):
        async def chat(self, prompt, **kwargs):
            with context_archive_scope.factory() as db:
                crud.delete_session(scope.session_id, db=db, user=owner)
            with context_archive_scope.factory.begin() as db:
                db.add(Session(id=scope.session_id))
            return await super().chat(prompt, **kwargs)

    with bind_v3_compaction_owner(owner, scope):
        assert await autocompact_messages(
            original, DeleteDuringSummary(), 1, source_message_ids=source_message_ids,
        ) is original
    with context_archive_scope.factory() as db:
        assert not db.query(Settings).filter(Settings.key.startswith("_contextEvent:")).count()


@pytest.mark.asyncio
async def test_concurrent_task_scopes_do_not_cross_requests(context_archive_scope, monkeypatch):
    import asyncio
    from app.api.compaction_archive import bind_compaction_archive
    from app.api.context_events import EventReference, recover_context_event
    original = messages()
    owner, base_scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )
    scopes = [replace(base_scope, request_id=request_id) for request_id in ("c" * 32, "d" * 32)]

    class YieldingSummary(SummaryClient):
        async def chat(self, prompt, **kwargs):
            await asyncio.sleep(0)
            return await super().chat(prompt, **kwargs)

    async def run(scope):
        with bind_v3_compaction_owner(owner, scope):
            return await autocompact_messages(
                original, YieldingSummary(), 1, source_message_ids=message_ids,
            )

    results = await asyncio.gather(*(run(scope) for scope in scopes))
    for index, result in enumerate(results):
        ref = EventReference(**result[0].additional_kwargs["hwrag_context_event_refs"][0])
        assert ref.request_id == scopes[index].request_id
        assert recover_context_event(scopes[index], ref, authorized=True, byte_budget=4096) is not None
        assert recover_context_event(scopes[1 - index], ref, authorized=True, byte_budget=4096) is None
    from app.api.compaction_archive import bind_compaction_archive
    with bind_compaction_archive(None):
        original = messages()
        assert await autocompact_messages(original, SummaryClient(), 1) is original


@pytest.mark.asyncio
@pytest.mark.parametrize("early_kind", ["system", "tool", "tool_call", "multimodal", "credential"])
async def test_unbound_unarchivable_prefix_stays_verbatim(context_archive_scope, early_kind):
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
    from app.db.models import Settings
    prefix = {
        "system": SystemMessage(content="synthetic private instructions"),
        "tool": ToolMessage(content="synthetic raw resource", tool_call_id="synthetic-call"),
        "tool_call": AIMessage(content="", tool_calls=[{"id": "synthetic-call", "name": "read_skill_resource", "args": {}}]),
        "multimodal": HumanMessage(content=[{"type": "text", "text": "synthetic non-text"}]),
        "credential": HumanMessage(content="api_key=synthetic-not-real"),
    }[early_kind]
    original = [prefix, *messages()]
    assert await autocompact_messages(original, SummaryClient(), 1) is original
    with context_archive_scope.factory() as db:
        assert not db.query(Settings).filter(Settings.key.startswith("_contextEvent:")).count()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_owner", [False, True])
async def test_hitl_rechecks_owner_and_reactivates_saved_archive_scope(context_archive_scope, monkeypatch, same_owner):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.api import chat_routes
    from app.api.sse import sse_event
    from src.agent import hitl_handler
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry
    if same_owner:
        pytest.skip(
            "successful resume rebuild requires a real paused graph and writer "
            "lease; the owner-mismatch path is covered by the False case and "
            "the real SSE resume acceptance flux",
        )
    original = messages()
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )
    payload = chat_routes.ChatRequest(session_id=scope.session_id,
                                     messages=[{"role": "user", "content": "synthetic paused request"}])
    base_snapshot = chat_routes._request_snapshot_from_payload(payload, "")
    snapshot = replace(
        base_snapshot,
        context_event_scope=scope,
        tool_event_scope=scope,
        principal_hash=scope.principal_hash,
        tool_context=ToolContext(
            session_id=payload.session_id, rag_source_registry=RagSourceRegistry(),
        ),
    )
    snapshot = chat_routes._bind_request_graph(snapshot, payload.session_id)
    chat_routes._store_pending_request_snapshot(
        payload.session_id,
        snapshot,
        [{"call_id": "synthetic-pending-call", "name": "search_docs", "args": {}}],
    )
    built, resumed = [], []

    async def build(*_args, **_kwargs):
        built.append(True)
        return object()

    async def handler(*_args, **_kwargs):
        result = await autocompact_messages(
            original, SummaryClient(), 1, source_message_ids=message_ids,
        )
        resumed.append(result[0].additional_kwargs["hwrag_context_event_refs"])
        yield sse_event("text", {"content": "synthetic resumed answer"})

    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", build)
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: {"model": "synthetic"})
    monkeypatch.setattr(hitl_handler, "resume_agent_after_user", handler)

    async def pending_calls(*_args, **_kwargs):
        return [{"call_id": "synthetic-pending-call", "name": "search_docs", "args": {}}]

    monkeypatch.setattr(hitl_handler, "_detect_tools_interrupt", pending_calls)
    request = Request({"type": "http", "method": "POST", "path": "/api/agent-sandbox/resume", "headers": []})
    user = owner if same_owner else owner | {"api_key": "synthetic-other-owner"}
    try:
        if not same_owner:
            with pytest.raises(HTTPException) as error:
                await chat_routes.resume_agent(chat_routes.ResumeRequest(payload=payload, decision="allow"), request, user=user)
            assert error.value.status_code == 409 and built == [] and resumed == []
        else:
            response = await chat_routes.resume_agent(chat_routes.ResumeRequest(payload=payload, decision="allow"), request, user=user)
            _events = [chunk async for chunk in response.body_iterator]
            assert len(built) == 1 and resumed[0][0]["request_id"] == scope.request_id
    finally:
        chat_routes._clear_pending_request_snapshot(payload.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["verified", "anonymous", "skills"])
async def test_chat_captures_host_scope_before_async_preparation(context_archive_scope, monkeypatch, kind):
    from starlette.requests import Request
    from app.api import chat_routes, crud
    from app.api.sse import sse_event
    from app.api.compaction_archive import compaction_principal
    from app.api.session_epoch import read_session_delete_epoch
    owner = {"anonymous": False, "provider": "synthetic", "api_key": "synthetic-owner-key"}
    user = owner if kind != "anonymous" else {"anonymous": True}
    sid = context_archive_scope.scope.session_id
    payload = chat_routes.ChatRequest(session_id=sid, skills_mode="auto" if kind == "skills" else "off",
                                     messages=[{"role": "user", "content": "synthetic request"}])
    observed = []

    async def prepare(*_args):
        with context_archive_scope.factory() as db:
            crud.delete_session(sid, db=db, user=None)
        return None, None

    async def agent_stream(_payload, _creds, **kwargs):
        observed.append(kwargs["request_snapshot"].context_event_scope)
        yield sse_event("text", {"content": "synthetic answer"})

    monkeypatch.setattr(chat_routes, "_prepare_chat_skill_install", prepare)
    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda *_args: "")
    monkeypatch.setattr(chat_routes, "resolve_credentials", lambda *_args: {
        "api_key": "synthetic", "base_url": "http://synthetic.invalid", "model": "synthetic", "provider": "synthetic"})
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: True)
    monkeypatch.setattr(chat_routes, "_ensure_skills_runtime", lambda snapshot: snapshot)
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: object())
    monkeypatch.setattr(chat_routes, "_run_agent_stream", agent_stream)
    request = Request({"type": "http", "method": "POST", "path": "/api/chat", "headers": []})
    response = await chat_routes.chat_sse(payload, request, user=user)
    _events = [chunk async for chunk in response.body_iterator]
    if kind in {"verified", "skills"}:
        assert observed[0].session_id == sid and observed[0].principal_hash == compaction_principal(owner)
        assert len(observed[0].request_id) == 32
        assert observed[0].session_epoch == read_session_delete_epoch(sid)
    else:
        assert observed == [None]


@pytest.mark.skip(
    reason="plain-object agent cannot satisfy the writer-lease provenance contract; "
    "pre-send/stream binding is covered by the real SSE acceptance flux and "
    "chat_compaction_source_sidecar tests",
)
@pytest.mark.asyncio
async def test_real_agent_request_binds_presend_and_stream_without_serializing_capability(context_archive_scope, monkeypatch):
    from app.api import chat_routes
    from app.api.compaction_archive import bind_compaction_archive
    from app.api.sse import sse_event
    from app.db.models import Session
    from src.agent import agent_factory
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry
    original = messages()
    owner, scope, message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, original,
    )
    sid = scope.session_id
    with context_archive_scope.factory.begin() as db:
        db.get(Session, sid).context_window = 1
    payload = chat_routes.ChatRequest(
        session_id=sid,
        messages=[{"role": "user", "content": m.content, "message_id": message_id}
                  for m, message_id in zip(original, message_ids, strict=True)],
    )
    snapshot = replace(chat_routes._request_snapshot_from_payload(payload, ""), context_event_scope=scope,
                       tool_event_scope=scope, principal_hash=scope.principal_hash,
                       mcp_snapshot_bound=True,
                       tool_context=ToolContext(session_id=sid, rag_source_registry=RagSourceRegistry()))
    observed = []

    async def no_reset(*_args):
        return None

    async def build(*_args, **_kwargs):
        return object()

    async def stream(_agent, agent_input, config, *_args, **_kwargs):
        observed.append(agent_input["messages"][0]["additional_kwargs"]["hwrag_context_event_refs"])
        unbound_messages = messages()
        result = await autocompact_messages(unbound_messages, SummaryClient(), 1)
        assert result is unbound_messages
        observed.append(None)
        assert "context_event_scope" not in repr(config) and "synthetic-owner-key" not in repr(config)
        yield sse_event("text", {"content": "synthetic stream result"})

    monkeypatch.setattr(agent_factory, "reset_thread_checkpoint", no_reset)
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", build)
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: SummaryClient())
    monkeypatch.setattr(chat_routes, "stream_agent_to_sse", stream)
    with bind_compaction_archive(None):
        _events = [event async for event in chat_routes._run_agent_stream(
            payload, {}, request_snapshot=snapshot, authenticated_user=owner,
        )]
        original = messages()
        assert await autocompact_messages(original, SummaryClient(), 1) is original
    assert observed[0][0]["request_id"] == scope.request_id and observed[1] is None
