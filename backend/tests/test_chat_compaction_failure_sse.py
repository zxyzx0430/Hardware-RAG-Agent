"""SSE behavior when context compaction cannot bring a request under budget."""

import asyncio
import json
from collections import Counter
from types import SimpleNamespace

from fastapi import Request
import pytest

from app.api import chat_routes
from app.api.sse import sse_event
from src.agent.exceptions import ContextLimitError


def _request() -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("test", 1),
    })


def _configure_chat_route(monkeypatch, run_agent_stream, counters: dict[str, int]) -> None:
    async def no_attachments(_payload):
        return "", []

    class FallbackClient:
        async def chat_stream(self, **_kwargs):
            counters["fallback"] += 1
            raise AssertionError("context-limit failure must not run the fallback model")
            yield  # Keep this an async generator for the chat client contract.

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
    monkeypatch.setattr(
        chat_routes,
        "resolve_credentials",
        lambda *_args: {
            "api_key": "synthetic-key",
            "base_url": "https://example.invalid/v1",
            "model": "synthetic-model",
            "provider": "synthetic-provider",
        },
    )
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: True)
    monkeypatch.setattr(chat_routes, "_process_attachments", no_attachments)
    monkeypatch.setattr(chat_routes, "_build_chat_history", lambda _messages: ([], "synthetic question"))
    monkeypatch.setattr(chat_routes, "_build_system_prompt", lambda *_args, **_kwargs: "synthetic prompt")
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: FallbackClient())
    monkeypatch.setattr(chat_routes, "_run_agent_stream", run_agent_stream)


async def _collect_events(monkeypatch, run_agent_stream, counters: dict[str, int]) -> list[dict]:
    _configure_chat_route(monkeypatch, run_agent_stream, counters)
    payload = chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(role="user", content="synthetic question", message_id="m-synthetic")],
        use_agent=True,
        session_id="synthetic-context-limit-sse-test",
    )
    response = await chat_routes.chat_sse(
        payload, _request(),
        user={"anonymous": False, "provider": "synthetic", "api_key": "synthetic-key"},
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return [
        json.loads(line[6:])
        for line in "".join(chunks).splitlines()
        if line.startswith("data: ")
    ]


def _assert_context_limit_terminal(events: list[dict]) -> None:
    errors = [event for event in events if event.get("type") == "error"]
    terminals = [
        {key: value for key, value in event.items() if key not in ("run_id", "seq")}
        for event in events if event.get("type") == "done"
    ]
    assert len(errors) == 1
    assert errors[0]["code"] == "CONTEXT_LIMIT"
    assert "已停止" in errors[0]["message"]
    assert "降级" not in errors[0]["message"]
    assert terminals == [{"type": "done", "success": False, "completed": False}]
    assert events[-1]["type"] == "done"


def test_pre_send_compaction_failure_stops_before_agent_stream_and_fallback(monkeypatch):
    from src.agent import agent_factory
    import src.agent.compact.autocompact as autocompact

    counters = {"agent_build": 0, "agent_stream": 0, "fallback": 0}

    async def no_op_reset(_session_id):
        return None

    async def preload_skills(snapshot, _session_id):
        return snapshot, [], True

    async def build_agent(*_args, **_kwargs):
        counters["agent_build"] += 1
        return object()

    async def compaction_failure(_messages, _creds, _context_window, **_kwargs):
        raise ContextLimitError(cumulative=101, limit=80)

    async def forbidden_agent_stream(*_args, **_kwargs):
        counters["agent_stream"] += 1
        raise AssertionError("pre-send compaction failure must precede Agent streaming")
        yield  # Keep the async-generator interface explicit.

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

    async def no_writer_reset(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_factory, "reset_thread_checkpoint", no_op_reset)
    monkeypatch.setattr(
        chat_routes, "_build_request_writer_binding",
        lambda *_a, **_k: _FakeWriterBinding(),
    )
    monkeypatch.setattr(
        chat_routes, "_acquire_request_writer_lease", lambda _binding: object(),
    )
    monkeypatch.setattr(
        agent_factory, "reset_thread_checkpoint_for_writer", no_writer_reset,
    )
    monkeypatch.setattr(chat_routes, "_ensure_skills_runtime", lambda snapshot: snapshot)
    monkeypatch.setattr(chat_routes, "_payload_with_request_snapshot", lambda payload, _snapshot: payload)
    monkeypatch.setattr(
        chat_routes, "_ensure_external_tool_snapshot", lambda snapshot, *_args: snapshot,
    )
    monkeypatch.setattr(chat_routes, "_preload_manual_skills", preload_skills)
    monkeypatch.setattr(
        chat_routes, "_ensure_request_tool_context", lambda snapshot, *_args: snapshot,
    )
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", build_agent)
    monkeypatch.setattr(chat_routes, "build_agent_config", lambda _session_id: {})
    monkeypatch.setattr(chat_routes, "_build_agent_messages", lambda _payload: [{"role": "user", "content": "synthetic question", "message_id": None}])
    monkeypatch.setattr(chat_routes, "_session_context_window", lambda _session_id: 32768)
    monkeypatch.setattr(chat_routes, "_dicts_to_langchain_messages", lambda messages: messages)
    monkeypatch.setattr(autocompact, "should_autocompact", lambda *_args: True)
    monkeypatch.setattr(chat_routes, "_run_autocompact", compaction_failure)
    monkeypatch.setattr(chat_routes, "_stream_agent_in_context", forbidden_agent_stream)

    events = asyncio.run(_collect_events(monkeypatch, chat_routes._run_agent_stream, counters))

    types = [event["type"] for event in events]
    assert types[0] == "run_started"
    assert "context_compressing" in types
    _assert_context_limit_terminal(events)
    assert counters == {"agent_build": 1, "agent_stream": 0, "fallback": 0}


def test_midrun_compaction_failure_keeps_prior_text_and_stops_without_retry(monkeypatch):
    counters = {"fallback": 0, "stream": 0}

    async def stream_then_context_limit(*_args, **_kwargs):
        counters["stream"] += 1
        yield sse_event("text", {"content": "synthetic partial answer"})
        raise ContextLimitError(cumulative=101, limit=80)

    events = asyncio.run(_collect_events(monkeypatch, stream_then_context_limit, counters))

    assert any(
        event.get("type") == "text" and event.get("content") == "synthetic partial answer"
        for event in events
    )
    _assert_context_limit_terminal(events)
    assert counters == {"fallback": 0, "stream": 1}


def test_sse_adapter_propagates_midrun_compaction_failure_and_closes_agent_stream(monkeypatch):
    from langgraph.errors import GraphDrained
    from src.agent import sse_adapter
    from tests.stream_writer_lease_support import authenticated_test_stream

    observed = {"continued_after_failure": False, "closed": False}

    class FakeAgent:
        async def astream(self, *_args, control=None, **_kwargs):
            try:
                yield "updates", {"node": {"messages": []}}
                if control is not None and control.drain_requested:
                    raise GraphDrained("context_compaction")
                observed["continued_after_failure"] = True
            finally:
                observed["closed"] = True

    async def failed_compaction(_state):
        return False

    monkeypatch.setattr(sse_adapter, "_do_compact", failed_compaction)
    state = {"needs_compact": True, "token_limit": 80}

    async def collect(agent, config):
        return [
            event async for event in sse_adapter._iter_agent_sse(
                agent, {}, config, Counter(), {}, state,
            )
        ]

    with authenticated_test_stream(
        monkeypatch, FakeAgent(), business_session_id="midrun-compaction-test",
    ) as (agent, config, _queue_id):
        with pytest.raises(ContextLimitError):
            asyncio.run(collect(agent, config))

    assert observed == {"continued_after_failure": False, "closed": True}
