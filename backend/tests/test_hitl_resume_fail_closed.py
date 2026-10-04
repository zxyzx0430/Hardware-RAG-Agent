"""HITL decisions fail closed at both the HTTP and handler boundaries."""

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import chat_routes
from app.api.dependencies import current_user
from app.api.sse import sse_event
from src.agent import hitl_handler
from src.agent import sse_adapter
from src.agent import sse_helpers


def _resume_payload(decision):
    return {
        "payload": {
            "messages": [{"role": "user", "content": "test"}],
            "session_id": "hitl-test",
        },
        "decision": decision,
    }


@pytest.mark.parametrize(
    "decision",
    ["unexpected", "", "ALLOW", "Allow", "DENY", None, 1, [], {}],
)
def test_resume_api_rejects_invalid_decision_before_agent_build(
    monkeypatch: pytest.MonkeyPatch, decision
) -> None:
    app = FastAPI()
    app.include_router(chat_routes.router)
    app.dependency_overrides[current_user] = lambda: {
        "provider": None,
        "api_key": None,
        "anonymous": True,
    }
    build_agent = AsyncMock(side_effect=AssertionError("agent must not be built"))
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", build_agent)

    response = TestClient(app).post(
        "/api/agent-sandbox/resume", json=_resume_payload(decision)
    )

    assert response.status_code == 422
    build_agent.assert_not_awaited()


@pytest.mark.parametrize("decision", ["allow", "deny", "stop"])
def test_resume_request_accepts_only_exact_supported_decisions(decision: str) -> None:
    request = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="test")]
        ),
        decision=decision,
    )

    assert request.decision == decision


@pytest.mark.parametrize(
    "decision",
    ["unexpected", "", "ALLOW", "Allow", "DENY", None, 1, [], {}],
)
def test_resume_handler_rejects_invalid_decision_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, decision
) -> None:
    dispatch = AsyncMock()
    monkeypatch.setattr(sse_adapter, "_iter_agent_sse", dispatch)

    async def collect() -> None:
        async for _ in hitl_handler.resume_agent_after_user(
            object(), {"configurable": {"thread_id": "hitl-test"}},
            decision, Counter(), "default",
        ):
            pass

    with pytest.raises(ValueError, match="Invalid HITL decision"):
        asyncio.run(collect())

    dispatch.assert_not_awaited()


@pytest.mark.parametrize("decision", ["allow", "deny", "stop"])
def test_resume_handler_preserves_allow_deny_stop_semantics(
    monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    pending = [{"name": "write_file", "call_id": "call-1", "args": {}}]
    resume_commands = []
    deny_sources = []
    allow_marker = Mock()
    monkeypatch.setattr(hitl_handler, "_mark_user_allow_in_ctx", allow_marker)

    async def find_pending(*_args):
        return pending

    async def record_deny(_agent, _config, _pending, source):
        deny_sources.append(source)

    async def fake_dispatch(_agent, command, *_args):
        resume_commands.append(command)
        yield sse_event("done", {"success": True})

    async def no_auto_resume(*_args):
        if False:
            yield ""

    monkeypatch.setattr(hitl_handler, "_detect_tools_interrupt", find_pending)
    monkeypatch.setattr(hitl_handler, "_inject_deny_messages", record_deny)
    monkeypatch.setattr(hitl_handler, "emit_deny_tool_results", lambda *_args: ())
    monkeypatch.setattr(hitl_handler, "handle_auto_resume", no_auto_resume)
    monkeypatch.setattr(sse_adapter, "_iter_agent_sse", fake_dispatch)
    monkeypatch.setattr(sse_helpers, "init_stream_state", lambda *_args: {})

    async def collect() -> list[dict]:
        frames = [
            frame
            async for frame in hitl_handler.resume_agent_after_user(
                object(), {"configurable": {"thread_id": "hitl-test"}},
                decision, Counter(), "default",
            )
        ]
        return [
            json.loads(line[6:])
            for frame in frames
            for line in frame.splitlines()
            if line.startswith("data: ")
        ]

    events = asyncio.run(collect())

    if decision == "allow":
        assert resume_commands == [hitl_handler._RESUME_ALLOW]
        allow_marker.assert_called_once_with()
        assert deny_sources == []
    elif decision == "deny":
        assert resume_commands == [hitl_handler._RESUME_DENY]
        allow_marker.assert_not_called()
        assert deny_sources == [hitl_handler.SOURCE_USER_DENY]
    else:
        assert resume_commands == []
        allow_marker.assert_not_called()
        assert deny_sources == [hitl_handler.SOURCE_USER_DENY]
        assert events[-1] == {
            "type": "done",
            "success": False,
            "reason": "user stopped",
        }
