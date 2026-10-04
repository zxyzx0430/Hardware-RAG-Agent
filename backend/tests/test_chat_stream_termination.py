"""Chat SSE terminal-state regressions using a fake Agent stream."""

import asyncio
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from fastapi import Request
import pytest

from app.api import chat_routes
from app.api.sse import sse_event
from src.agent import hitl_handler


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


def _configure_agent_route(monkeypatch, fake_stream):
    async def no_attachments(_payload):
        return "", []

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
    monkeypatch.setattr(
        chat_routes,
        "resolve_credentials",
        lambda _payload, _request: {
            "api_key": "test-key", "base_url": "https://example.invalid/v1",
            "model": "test-model", "provider": "test-provider",
        },
    )
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: True)
    monkeypatch.setattr(chat_routes, "_process_attachments", no_attachments)
    monkeypatch.setattr(chat_routes, "_build_chat_history", lambda _messages: ([], "question"))
    monkeypatch.setattr(chat_routes, "_build_system_prompt", lambda *_args, **_kwargs: "test prompt")
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: object())
    monkeypatch.setattr(chat_routes, "_run_agent_stream", fake_stream)


async def _collect_chat(monkeypatch, fake_stream) -> list[dict]:
    _configure_agent_route(monkeypatch, fake_stream)
    payload = chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(role="user", content="question")],
        use_agent=True,
        session_id="stream-termination-test",
    )
    response = await chat_routes.chat_sse(payload, _request(), user={})
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    body = "".join(chunks)
    return [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


async def _collect_fallback_chat(monkeypatch, fake_client) -> list[dict]:
    async def no_attachments(_payload):
        return "", []

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
    monkeypatch.setattr(
        chat_routes,
        "resolve_credentials",
        lambda _payload, _request: {
            "api_key": "test-key", "base_url": "https://example.invalid/v1",
            "model": "test-model", "provider": "test-provider",
        },
    )
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", False)
    monkeypatch.setattr(chat_routes, "_process_attachments", no_attachments)
    monkeypatch.setattr(chat_routes, "_build_chat_history", lambda _messages: ([], "question"))
    monkeypatch.setattr(chat_routes, "_build_system_prompt", lambda *_args, **_kwargs: "test prompt")
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: fake_client)
    payload = chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(role="user", content="question")],
        session_id="fallback-empty-stream-test",
    )
    response = await chat_routes.chat_sse(payload, _request(), user={})
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return [
        json.loads(line[6:])
        for line in "".join(chunks).splitlines()
        if line.startswith("data: ")
    ]


def test_agent_error_after_partial_text_keeps_text_and_emits_one_failed_terminal(monkeypatch):
    calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield sse_event("text", {"content": "partial answer"})
        raise RuntimeError("provider stream failed")

    events = asyncio.run(_collect_chat(monkeypatch, fake_stream))

    assert [event["type"] for event in events].count("done") == 1
    assert any(event.get("type") == "text" and event.get("content") == "partial answer" for event in events)
    errors = [event for event in events if event.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == chat_routes.ChatErrorCode.INTERNAL_ERROR
    assert events[-1] == {
        "type": "done", "success": False, "completed": False,
    }
    assert calls == 1


@pytest.mark.parametrize(
    "frames",
    [
        [],
        [sse_event("thinking", {"content": "reasoning only", "source": "reasoning"})],
        [
            sse_event("text", {"content": "  \n"}),
            sse_event("done", {"success": True}),
        ],
    ],
    ids=["no-output", "reasoning-only", "empty-terminal"],
)
def test_agent_without_visible_answer_is_model_empty_not_success_or_timeout(monkeypatch, frames):
    calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        for frame in frames:
            yield frame

    events = asyncio.run(_collect_chat(monkeypatch, fake_stream))

    errors = [event for event in events if event.get("type") == "error"]
    terminals = [event for event in events if event.get("type") == "done"]
    assert len(errors) == 1
    assert errors[0]["code"] == "MODEL_EMPTY_RESPONSE"
    assert len(terminals) == 1
    assert terminals[0]["success"] is False
    assert terminals[0]["completed"] is False
    assert calls == 1


def test_fallback_provider_empty_stream_emits_typed_failure_without_replay(monkeypatch):
    calls = 0

    class EmptyClient:
        async def chat_stream(self, **_kwargs):
            nonlocal calls
            calls += 1
            if False:
                yield None

    events = asyncio.run(_collect_fallback_chat(monkeypatch, EmptyClient()))

    assert [event["type"] for event in events].count("done") == 1
    error = next(event for event in events if event["type"] == "error")
    assert error["code"] == "MODEL_EMPTY_RESPONSE"
    assert events[-1] == {"type": "done", "success": False, "completed": False}
    assert calls == 1


def test_generic_error_message_containing_empty_is_not_misclassified(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        raise RuntimeError("empty from proxy adapter; upstream status unavailable")
        yield "unreachable"

    events = asyncio.run(_collect_chat(monkeypatch, fake_stream))

    error = next(event for event in events if event["type"] == "error")
    assert error["code"] == chat_routes.ChatErrorCode.INTERNAL_ERROR
    assert events[-1] == {"type": "done", "success": False, "completed": False}


@pytest.mark.parametrize("partial_text", [False, True], ids=["no-text", "partial-text"])
def test_known_upstream_zero_output_error_is_typed_without_leaking_url(monkeypatch, partial_text):
    signature = "Empty response from upstream (zero output tokens)"

    class UpstreamError(RuntimeError):
        detail = signature

    calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if partial_text:
            yield sse_event("text", {"content": "已收到的部分回答"})
        try:
            raise UpstreamError("https://provider.example/private-request?token=secret")
        except UpstreamError as upstream_error:
            raise chat_routes.LLMError(
                f"API 错误：{signature} https://provider.example/private-request?token=secret"
            ) from upstream_error

    events = asyncio.run(_collect_chat(monkeypatch, fake_stream))
    error = next(event for event in events if event["type"] == "error")

    assert error["code"] == "MODEL_EMPTY_RESPONSE"
    assert "https://" not in error["detail"]
    assert "token=secret" not in str(events)
    assert "已收到的部分回答" in str(events) if partial_text else "已收到的部分回答" not in str(events)
    assert events[-1] == {"type": "done", "success": False, "completed": False}
    assert calls == 1


def test_fallback_known_upstream_zero_output_error_is_typed(monkeypatch):
    signature = "Empty response from upstream (zero output tokens)"

    class UpstreamError(RuntimeError):
        message = signature

    class FailingClient:
        async def chat_stream(self, **_kwargs):
            try:
                raise UpstreamError("https://provider.example/private-request")
            except UpstreamError as upstream_error:
                raise chat_routes.LLMError(f"API 错误：{signature}") from upstream_error
            yield None

    events = asyncio.run(_collect_fallback_chat(monkeypatch, FailingClient()))
    error = next(event for event in events if event["type"] == "error")

    assert error["code"] == "MODEL_EMPTY_RESPONSE"
    assert "https://" not in str(events)
    assert events[-1] == {"type": "done", "success": False, "completed": False}


def test_agent_confirmation_is_waiting_not_empty_or_complete(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield sse_event("tool_confirm_required", {"calls": [], "count": 0})

    events = asyncio.run(_collect_chat(monkeypatch, fake_stream))

    assert not any(event.get("type") == "error" for event in events)
    assert [event for event in events if event.get("type") == "done"] == [{
        "type": "done",
        "success": True,
        "completed": False,
        "awaiting_confirmation": True,
        "usage": None,
    }]


def test_agent_idle_timeout_is_reported_as_timeout_without_network_blame(monkeypatch):
    from src.agent.exceptions import AgentStreamIdleTimeoutError

    async def fake_stream(*_args, **_kwargs):
        yield sse_event("text", {"content": "partial before stall"})
        raise AgentStreamIdleTimeoutError(0.1, 0.1)

    events = asyncio.run(_collect_chat(monkeypatch, fake_stream))

    errors = [event for event in events if event.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "TIMEOUT"
    assert "network" not in errors[0]["message"].lower()
    assert [event for event in events if event.get("type") == "done"] == [{
        "type": "done", "success": False, "completed": False,
    }]


def test_resume_confirmation_fallback_done_remains_incomplete_and_keeps_snapshot(monkeypatch):
    session_id = "resume-confirm-terminal-test"

    async def fake_resume(*_args, **_kwargs):
        yield sse_event("tool_confirm_required", {
            "calls": [{
                "name": "write_file", "args": {"path": "out.txt"},
                "call_id": "resume-confirm", "risk_level": "high",
            }],
            "count": 1,
        })

    monkeypatch.setattr(hitl_handler, "resume_agent_after_user", fake_resume)
    request = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="continue")],
            session_id=session_id,
        ),
        decision="allow",
    )
    ctx = chat_routes.ResumeContext(
        agent=object(), config={}, req=request, call_counter=Counter(), model="test-model",
    )

    async def collect():
        frames = [
            frame async for frame in chat_routes._resume_event_generator_from_ctx(ctx)
        ]
        body = "".join(frames)
        return [
            json.loads(line[6:])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]

    try:
        events = asyncio.run(collect())
        assert events[-1] == {
            "type": "done",
            "success": True,
            "completed": False,
            "awaiting_confirmation": True,
            "usage": None,
        }
        assert chat_routes._get_pending_request_snapshot(session_id) is not None
    finally:
        chat_routes._clear_pending_request_snapshot(session_id)


def _run_import_guard_subprocess(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    backend_root = Path(__file__).resolve().parents[1]
    runtime_temp = tmp_path / "runtime-temp"
    runtime_temp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "SQLITE_DB_PATH": str(tmp_path / "import-order.sqlite3"),
        "AGENT_CHECKPOINTER_TYPE": "memory",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TEMP": str(runtime_temp),
        "TMP": str(runtime_temp),
        "PYTHONPATH": str(backend_root),
    })
    for key in tuple(env):
        if "API_KEY" in key or "API_TOKEN" in key:
            env[key] = ""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


@pytest.mark.parametrize("order", ["adapter-first", "main-first"])
def test_chat_stream_adapter_import_order_preserves_agent_route_symbol(tmp_path, order):
    if order == "adapter-first":
        import_sequence = """
import src.agent.sse_adapter as adapter
import app.api.chat_routes as routes
assert "src.agent.sse_adapter" in sys.modules
import app.main
"""
    else:
        import_sequence = """
import app.main
import app.api.chat_routes as routes
assert "src.agent.sse_adapter" not in sys.modules
import src.agent.sse_adapter as adapter
"""
    code = f"""
import sys
{import_sequence}
assert routes._AGENT_PATH_AVAILABLE is True, routes._AGENT_PATH_AVAILABLE
assert callable(routes.stream_agent_to_sse)
assert callable(adapter.stream_agent_to_sse)
"""

    result = _run_import_guard_subprocess(tmp_path, code)

    assert result.returncode == 0, (
        f"{order} import subprocess failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_missing_agent_factory_dependency_keeps_patchable_stream_symbol(tmp_path):
    code = """
import builtins
real_import = builtins.__import__
def block_optional_agent_factory(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "src.agent.agent_factory":
        raise ModuleNotFoundError("simulated optional dependency missing", name="langgraph")
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = block_optional_agent_factory
import app.api.chat_routes as routes
assert routes._AGENT_PATH_AVAILABLE is False
assert callable(routes.stream_agent_to_sse)
"""

    result = _run_import_guard_subprocess(tmp_path, code)

    assert result.returncode == 0, (
        f"missing-dependency subprocess failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("shutdown", ["aclose", "cancel"])
def test_stream_route_wrapper_closes_adapter_iterator(monkeypatch, shutdown):
    from src.agent import sse_adapter

    class ControlledAdapterStream:
        def __init__(self):
            self._yielded = False
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._yielded:
                self._yielded = True
                return "partial-frame"
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    adapter_stream = ControlledAdapterStream()
    monkeypatch.setattr(
        sse_adapter,
        "stream_agent_to_sse",
        lambda *_args, **_kwargs: adapter_stream,
    )

    async def exercise_shutdown():
        wrapper = chat_routes.stream_agent_to_sse(
            object(), {}, {}, Counter(), model="test-model",
        )
        assert "partial" in await anext(wrapper)
        if shutdown == "aclose":
            await wrapper.aclose()
        else:
            pending = asyncio.create_task(anext(wrapper))
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        assert adapter_stream.closed is True

    asyncio.run(exercise_shutdown())
