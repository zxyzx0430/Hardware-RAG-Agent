"""Isolated request-side tests for manually supplied long-term memory."""

from __future__ import annotations

import asyncio
import pytest
from contextlib import contextmanager
from types import SimpleNamespace
from pydantic import ValidationError
from starlette.requests import Request

from app.api import chat_helpers
from app.api.chat_routes import ChatRequest


def _messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "test request"}]


def test_chat_request_rejects_manual_memory_over_4000_characters() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(messages=_messages(), long_term_memory="x" * 4001)


@pytest.mark.parametrize("value", [None, 7, True, [], {}])
def test_chat_request_rejects_non_string_manual_memory(value: object) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(messages=_messages(), long_term_memory=value)


def test_explicit_empty_memory_does_not_fall_back_to_saved_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = ChatRequest(messages=_messages(), long_term_memory="")
    monkeypatch.setattr(
        chat_helpers,
        "_read_saved_long_term_memory",
        lambda: pytest.fail("explicit empty memory must not read the saved value"),
        raising=False,
    )

    assert chat_helpers.resolve_long_term_memory(payload) == ""


def test_omitted_memory_reads_the_saved_setting_through_the_database_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class _Query:
        def filter(self, criterion):
            observed["criterion_key"] = criterion.right.value
            return self

        def first(self):
            return SimpleNamespace(value="saved memory")

    class _Database:
        def query(self, model):
            observed["model"] = model
            return _Query()

    @contextmanager
    def fake_db_context():
        yield _Database()

    monkeypatch.setattr(chat_helpers, "get_db_ctx", fake_db_context)
    payload = ChatRequest(messages=_messages())

    assert chat_helpers.resolve_long_term_memory(payload) == "saved memory"
    assert observed["model"].__name__ == "Settings"
    assert observed["criterion_key"] == "longTermMemory"


def test_manual_memory_is_added_once_to_fallback_system_prompt() -> None:
    marker = "User prefers concise register tables."
    payload = ChatRequest(messages=_messages(), long_term_memory=marker)

    prompt = chat_helpers._build_system_prompt(payload, [], "")

    assert prompt.count(marker) == 1
    assert "本轮用户请求或工具权限" in prompt


@pytest.mark.asyncio
async def test_agent_factory_adds_manual_memory_once_without_calling_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.agent import agent_factory
    import langchain.agents

    marker = "Uses 3.3V logic for this board."
    captured: dict[str, object] = {}

    monkeypatch.setattr(agent_factory, "_build_llm", lambda *_args: object())

    async def fake_checkpointer():
        return object()

    monkeypatch.setattr(agent_factory, "_get_checkpointer", fake_checkpointer)

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)
    config = agent_factory.AgentConfig(
        model="mock-model",
        api_key="",
        base_url="",
        tools=[],
        long_term_memory=marker,
    )

    await agent_factory.create_hardware_agent_from_config(config)

    prompt = captured["system_prompt"]
    assert isinstance(prompt, str)
    assert prompt.count(marker) == 1


@pytest.mark.asyncio
async def test_chat_agent_builder_forwards_the_resolved_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import chat_routes

    marker = "Use the board's default I2C pins."
    captured: dict[str, object] = {}

    monkeypatch.setattr(chat_routes, "build_tools", lambda _payload: [])

    async def fake_create_hardware_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(chat_routes, "create_hardware_agent", fake_create_hardware_agent)
    payload = ChatRequest(messages=_messages(), long_term_memory=marker)

    await chat_routes._build_agent_for_payload(
        payload,
        {"model": "mock", "api_key": "", "base_url": ""},
        long_term_memory=marker,
    )

    assert captured["long_term_memory"] == marker


@pytest.mark.asyncio
@pytest.mark.parametrize("use_agent", [False, True])
async def test_chat_route_uses_one_resolved_memory_on_each_model_path(
    monkeypatch: pytest.MonkeyPatch,
    use_agent: bool,
) -> None:
    from app.api import chat_routes
    from app.api.sse import sse_event
    from src.llm.client import StreamChunk

    marker = "Keep answers tied to the saved board background."
    observed: dict[str, object] = {}

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: marker)
    monkeypatch.setattr(
        chat_routes,
        "resolve_credentials",
        lambda *_args: {"api_key": "", "base_url": "", "model": "mock", "provider": ""},
    )

    async def fake_process_attachments(_payload):
        return [], []

    monkeypatch.setattr(chat_routes, "_process_attachments", fake_process_attachments)
    monkeypatch.setattr(chat_routes, "_build_chat_history", lambda _messages: ([], "question"))
    monkeypatch.setattr(chat_routes, "_record_token_usage", lambda **_kwargs: None)

    class _FakeClient:
        async def chat_stream(self, **kwargs):
            observed["fallback_prompt"] = kwargs["system_prompt"]
            yield StreamChunk(type="text", content="reply")

    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: _FakeClient())
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", use_agent)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: use_agent)

    async def fake_agent_stream(_payload, _creds, long_term_memory=None, request_snapshot=None):
        observed["agent_memory"] = long_term_memory
        yield sse_event("text", {"content": "reply"})

    monkeypatch.setattr(chat_routes, "_run_agent_stream", fake_agent_stream)
    payload = ChatRequest(messages=_messages(), use_agent=use_agent)
    request = Request({
        "type": "http", "method": "POST", "path": "/api/chat",
        "headers": [], "query_string": b"",
    })

    response = await chat_routes.chat_sse(payload, request, user={})
    async for _chunk in response.body_iterator:
        pass

    if use_agent:
        assert observed["agent_memory"] == marker
    else:
        prompt = observed["fallback_prompt"]
        assert isinstance(prompt, str)
        assert prompt.count(marker) == 1


@pytest.mark.asyncio
async def test_hitl_resume_reuses_the_memory_snapshot_from_the_paused_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import chat_routes
    from app.api.sse import sse_event
    from src.agent import agent_factory, hitl_handler

    marker = "Initial request memory snapshot."
    agent_memories: list[str | None] = []
    agent_configs: list[tuple[str, list[str] | None, int | None, float | None]] = []

    async def fake_reset_thread_checkpoint(_session_id):
        return None

    monkeypatch.setattr(agent_factory, "reset_thread_checkpoint", fake_reset_thread_checkpoint)

    async def fake_build_agent(_payload, _creds, long_term_memory=None, request_snapshot=None):
        agent_memories.append(long_term_memory)
        agent_configs.append((
            _payload.permission_mode, _payload.kb_ids,
            _payload.top_k, _payload.relevance_threshold,
        ))
        return object()

    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", fake_build_agent)
    monkeypatch.setattr(chat_routes, "_session_context_window", lambda _session_id: None)

    async def fake_agent_stream(*_args, **_kwargs):
        yield sse_event("tool_confirm_required", {"calls": [], "count": 0})

    monkeypatch.setattr(chat_routes, "stream_agent_to_sse", fake_agent_stream)
    payload = ChatRequest(
        messages=_messages(), session_id="memory-session",
        permission_mode="default", kb_ids=["kb-original"], top_k=7,
        relevance_threshold=0.42,
    )
    creds = {"model": "mock", "api_key": "", "base_url": ""}
    _paused_events = [
        event
        async for event in chat_routes._run_agent_stream(
            payload, creds, long_term_memory=marker,
        )
    ]
    assert len(_paused_events) == 1

    monkeypatch.setattr(
        chat_routes,
        "resolve_long_term_memory",
        lambda _payload: pytest.fail("resume must not reload changed saved memory"),
    )
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: creds)
    monkeypatch.setattr(chat_routes, "build_agent_config", lambda _session_id: {})

    async def fake_resume_agent_after_user(*_args):
        yield sse_event("done", {"success": True})

    monkeypatch.setattr(hitl_handler, "resume_agent_after_user", fake_resume_agent_after_user)
    resume_request = chat_routes.ResumeRequest(
        payload=ChatRequest(
            messages=_messages(), session_id="memory-session",
            long_term_memory="Tampered replacement memory.",
            permission_mode="bypassPermissions", kb_ids=["kb-tampered"],
            top_k=1, relevance_threshold=0.99,
        ),
        decision="allow",
    )
    request = Request({
        "type": "http", "method": "POST", "path": "/api/agent-sandbox/resume",
        "headers": [], "query_string": b"",
    })

    response = await chat_routes.resume_agent(resume_request, request, user={})
    async for _chunk in response.body_iterator:
        pass

    assert agent_memories == [marker, marker]
    assert agent_configs == [
        ("default", ["kb-original"], 7, 0.42),
        ("default", ["kb-original"], 7, 0.42),
    ]


@pytest.mark.asyncio
async def test_interleaved_agent_sessions_keep_distinct_memory_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import chat_routes
    from app.api.sse import sse_event
    from src.agent import agent_factory

    async def fake_reset_thread_checkpoint(_session_id):
        return None

    async def fake_build_agent(_payload, _creds, long_term_memory=None, request_snapshot=None):
        return object()

    monkeypatch.setattr(agent_factory, "reset_thread_checkpoint", fake_reset_thread_checkpoint)
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", fake_build_agent)
    monkeypatch.setattr(chat_routes, "_session_context_window", lambda _session_id: None)

    async def fake_agent_stream(_agent, _input, config, *_args, **_kwargs):
        await asyncio.sleep(0)
        yield sse_event("tool_confirm_required", {"calls": [], "count": 0})

    monkeypatch.setattr(chat_routes, "stream_agent_to_sse", fake_agent_stream)

    payload_a = ChatRequest(messages=_messages(), session_id="memory-session-a")
    payload_b = ChatRequest(messages=_messages(), session_id="memory-session-b")
    creds = {"model": "mock", "api_key": "", "base_url": ""}

    await asyncio.gather(
        _consume(chat_routes._run_agent_stream(payload_a, creds, "memory A")),
        _consume(chat_routes._run_agent_stream(payload_b, creds, "memory B")),
    )

    assert chat_routes._get_pending_memory_snapshot("memory-session-a") == "memory A"
    assert chat_routes._get_pending_memory_snapshot("memory-session-b") == "memory B"
    chat_routes._clear_pending_memory_snapshot("memory-session-a")
    chat_routes._clear_pending_memory_snapshot("memory-session-b")


async def _consume(iterator) -> list[str]:
    return [item async for item in iterator]


@pytest.mark.asyncio
async def test_resume_fails_closed_when_request_memory_snapshot_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException
    from app.api import chat_routes

    session_id = "memory-session-without-snapshot"
    chat_routes._clear_pending_memory_snapshot(session_id)
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    builder_calls: list[object] = []
    monkeypatch.setattr(
        chat_routes, "_build_agent_for_payload",
        lambda *args, **kwargs: builder_calls.append((args, kwargs)),
    )
    resume_request = chat_routes.ResumeRequest(
        payload=ChatRequest(
            messages=_messages(), session_id=session_id,
            long_term_memory="Tampered explicit memory.",
        ),
        decision="allow",
    )
    request = Request({
        "type": "http", "method": "POST", "path": "/api/agent-sandbox/resume",
        "headers": [], "query_string": b"",
    })

    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.resume_agent(resume_request, request, user={})

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "HITL_REQUEST_SNAPSHOT_UNAVAILABLE"
    assert builder_calls == []
