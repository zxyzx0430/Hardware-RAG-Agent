"""Resume context-limit SSE behavior with a mocked Agent handler."""

import asyncio
import json
from collections import Counter

import pytest
from fastapi import HTTPException

from app.api import chat_routes
from app.api.sse import sse_event
from src.agent import hitl_handler
from src.agent.exceptions import ContextLimitError

# The resume generator refuses to run without a request snapshot whose epochs
# still match the current store, so the fixture supplies both halves.
_SNAPSHOT = chat_routes.AgentRequestSnapshot(
    session_delete_epoch="session-epoch",
    memory_clear_epoch="memory-epoch",
)


@pytest.fixture(autouse=True)
def _stable_epochs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        chat_routes, "read_session_delete_epoch", lambda _session_id: "session-epoch",
    )
    monkeypatch.setattr(chat_routes, "read_memory_clear_epoch", lambda: "memory-epoch")


def _decode_events(frames: list[str]) -> list[dict]:
    return [
        json.loads(line[6:])
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.parametrize("decision", ["allow", "deny", "stop"])
def test_resume_context_limit_emits_failed_terminal_and_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    handler_calls: list[str] = []
    cleared_sessions: list[str | None] = []
    session_id = f"resume-context-limit-{decision}"
    pending_sessions: dict[str, object] = {session_id: object()}

    def clear_pending(session: str | None) -> None:
        cleared_sessions.append(session)
        if session is not None:
            pending_sessions.pop(session, None)

    async def fake_resume_agent_after_user(
        _agent, _config, resume_decision, _counter, _permission_mode, **_kwargs,
    ):
        handler_calls.append(resume_decision)
        yield sse_event("text", {"content": "partial answer"})
        raise ContextLimitError(cumulative=101, limit=80)

    monkeypatch.setattr(
        hitl_handler,
        "resume_agent_after_user",
        fake_resume_agent_after_user,
    )
    monkeypatch.setattr(
        chat_routes,
        "_clear_pending_request_snapshot",
        clear_pending,
    )
    monkeypatch.setattr(
        chat_routes,
        "_get_pending_request_snapshot",
        lambda session: pending_sessions.get(session) if session is not None else None,
    )
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)

    request = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="question")],
            session_id=session_id,
        ),
        decision=decision,
    )
    ctx = chat_routes.ResumeContext(
        agent=object(),
        config={},
        req=request,
        call_counter=Counter(),
        model="test-model",
        request_snapshot=_SNAPSHOT,
    )

    async def collect() -> list[dict]:
        frames = [
            frame async for frame in chat_routes._resume_event_generator_from_ctx(ctx)
        ]
        return _decode_events(frames)

    events = asyncio.run(collect())

    assert [event["type"] for event in events] == ["text", "error", "done"]
    assert events[0] == {"type": "text", "content": "partial answer"}
    assert events[1] == {
        "type": "error",
        "code": "CONTEXT_LIMIT",
        "retryable": False,
        "message": (
            "上下文超过模型 test-model 的可处理上限（80 tokens），本轮已停止。"
            "直接重试会再次超限；请开启新会话，或缩小扫描/对话范围后重新提问。"
        ),
        "detail": "本轮累计估算 101 tokens；已执行的工具输出也计入上下文。",
    }
    assert events[2] == {"type": "done", "success": False, "completed": False}
    assert handler_calls == [decision]
    assert cleared_sessions == [session_id]
    assert pending_sessions == {}

    async def duplicate_resume_is_rejected() -> None:
        http_request = chat_routes.Request({
            "type": "http",
            "method": "POST",
            "path": "/api/agent-sandbox/resume",
            "headers": [],
        })
        with pytest.raises(HTTPException) as raised:
            await chat_routes.resume_agent(request, http_request, user={})
        assert raised.value.status_code == 409
        assert raised.value.detail["error"]["code"] == "HITL_REQUEST_SNAPSHOT_UNAVAILABLE"

    asyncio.run(duplicate_resume_is_rejected())
    assert handler_calls == [decision]


def test_resume_other_exception_keeps_agent_resume_failed_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_resume_agent_after_user(*_args, **_kwargs):
        raise RuntimeError("resume failed")
        yield  # Keep this a lazy async generator, as the real handler is.

    monkeypatch.setattr(
        hitl_handler,
        "resume_agent_after_user",
        failing_resume_agent_after_user,
    )
    request = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="question")]
        ),
        decision="allow",
    )
    ctx = chat_routes.ResumeContext(
        agent=object(),
        config={},
        req=request,
        call_counter=Counter(),
        model="test-model",
        request_snapshot=_SNAPSHOT,
    )

    async def collect() -> list[dict]:
        frames = [
            frame async for frame in chat_routes._resume_event_generator_from_ctx(ctx)
        ]
        return _decode_events(frames)

    events = asyncio.run(collect())

    assert [event["type"] for event in events] == ["error", "done"]
    assert events[0] == {
        "type": "error",
        "code": "AGENT_RESUME_FAILED",
        "message": "resume failed",
    }
    assert events[1] == {"type": "done", "success": False, "completed": False}
