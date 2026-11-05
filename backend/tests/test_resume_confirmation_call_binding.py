"""A non-MCP HITL ALLOW must bind to the single current pending call."""

import asyncio
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, Request

from app.api import chat_routes
from src.agent import hitl_handler, sse_adapter


def test_pending_confirmation_accepts_only_the_exact_single_call() -> None:
    pending = [{"name": "write_file", "args": {"path": "x"}, "call_id": "call-current"}]

    hitl_handler._require_single_pending_confirmation_call("call-current", pending)


@pytest.mark.parametrize(
    ("call_id", "pending"),
    [
        (None, [{"call_id": "call-current"}]),
        ("", [{"call_id": "call-current"}]),
        ("call-old", [{"call_id": "call-current"}]),
        ("call-current", []),
        ("call-current", [{"call_id": "call-current"}, {"call_id": "call-other"}]),
        ("call-current", [{"call_id": ""}]),
        ("call-current", [None]),
        ("call-current", None),
    ],
)
def test_pending_confirmation_rejects_missing_stale_or_ambiguous_call(
    call_id: str | None, pending: list[dict] | None,
) -> None:
    with pytest.raises(ValueError, match="one pending call"):
        hitl_handler._require_single_pending_confirmation_call(call_id, pending)


def test_resume_handler_rechecks_confirmation_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = [{"name": "write_file", "args": {}, "call_id": "call-new"}]
    detect_pending = AsyncMock(return_value=pending)
    dispatch = Mock()
    monkeypatch.setattr(hitl_handler, "_get_tool_ctx", lambda: SimpleNamespace(mcp_authorization=None))
    monkeypatch.setattr(hitl_handler, "_detect_tools_interrupt", detect_pending)
    monkeypatch.setattr(sse_adapter, "_iter_agent_sse", dispatch)

    async def collect() -> None:
        with pytest.raises(ValueError, match="one pending call"):
            async for _ in hitl_handler.resume_agent_after_user(
                object(),
                {"configurable": {"thread_id": "confirmation-test"}},
                "allow",
                Counter(),
                "default",
                confirmed_call_id="call-old",
            ):
                pass

    asyncio.run(collect())

    detect_pending.assert_awaited_once()
    dispatch.assert_not_called()


def test_resume_handler_checkpoint_read_failure_prevents_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detect_pending = AsyncMock(side_effect=RuntimeError("checkpoint unavailable"))
    dispatch = Mock()
    monkeypatch.setattr(hitl_handler, "_get_tool_ctx", lambda: SimpleNamespace(mcp_authorization=None))
    monkeypatch.setattr(hitl_handler, "_detect_tools_interrupt", detect_pending)
    monkeypatch.setattr(sse_adapter, "_iter_agent_sse", dispatch)

    async def collect() -> None:
        with pytest.raises(RuntimeError, match="checkpoint unavailable"):
            async for _ in hitl_handler.resume_agent_after_user(
                object(),
                {"configurable": {"thread_id": "confirmation-test"}},
                "allow",
                Counter(),
                "default",
                confirmed_call_id="call-current",
            ):
                pass

    asyncio.run(collect())

    detect_pending.assert_awaited_once()
    dispatch.assert_not_called()


def test_resume_api_rejects_allow_without_call_id_before_building_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = chat_routes.AgentRequestSnapshot(
        session_delete_epoch="session-epoch",
        memory_clear_epoch="memory-epoch",
        principal_id="principal",
        pending_call_ids=("call-current",),
    )
    build_agent = AsyncMock(side_effect=AssertionError("agent must not be built"))
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(
        chat_routes, "_capture_request_snapshot_epochs",
        lambda _session_id: ("session-epoch", "memory-epoch"),
    )
    monkeypatch.setattr(chat_routes, "_get_pending_request_snapshot", lambda _key: snapshot)
    monkeypatch.setattr(chat_routes, "_verified_principal_id", lambda _user: "principal")
    monkeypatch.setattr(chat_routes, "_require_resume_snapshot_current", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", build_agent)

    resume = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="confirm")],
            session_id="confirmation-test",
        ),
        decision="allow",
    )
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/agent-sandbox/resume",
        "headers": [],
        "query_string": b"",
    })

    async def invoke() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await chat_routes.resume_agent(resume, request, user={})
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"]["code"] == "HITL_CONFIRMATION_STALE"

    asyncio.run(invoke())
    build_agent.assert_not_awaited()


def test_resume_api_rejects_call_id_that_differs_from_current_pending_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.agent import context_guard

    snapshot = chat_routes.AgentRequestSnapshot(
        session_delete_epoch="session-epoch",
        memory_clear_epoch="memory-epoch",
        principal_id="principal",
    )
    agent = object()
    lease = object()
    assert_scope = Mock()
    detect_pending = AsyncMock(return_value=[{
        "name": "write_file",
        "args": {"path": "new"},
        "call_id": "call-current",
    }])
    binding = SimpleNamespace(
        registry=SimpleNamespace(assert_scope=assert_scope),
        current_scope=Mock(return_value="current-scope"),
    )
    build_agent = AsyncMock(return_value=agent)
    release_lease = Mock()
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(
        chat_routes, "_capture_request_snapshot_epochs",
        lambda _session_id: ("session-epoch", "memory-epoch"),
    )
    monkeypatch.setattr(chat_routes, "_get_pending_request_snapshot", lambda _key: snapshot)
    monkeypatch.setattr(chat_routes, "_verified_principal_id", lambda _user: "principal")
    monkeypatch.setattr(chat_routes, "_require_resume_snapshot_current", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "_payload_with_request_snapshot", lambda payload, *_args: payload)
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: {"model": "test-model"})
    monkeypatch.setattr(chat_routes, "_session_context_window", lambda _session_id: 128)
    monkeypatch.setattr(
        context_guard,
        "resolve_context_budget",
        lambda *_args: SimpleNamespace(effective_window=128),
    )
    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", build_agent)
    monkeypatch.setattr(chat_routes, "build_agent_config", lambda _session_id: {"configurable": {}})
    monkeypatch.setattr(chat_routes, "_build_request_writer_binding", lambda *_args: binding)
    monkeypatch.setattr(chat_routes, "_acquire_request_writer_lease", lambda _binding: lease)
    monkeypatch.setattr(chat_routes, "_release_request_writer_lease", release_lease)
    monkeypatch.setattr(hitl_handler, "_detect_tools_interrupt", detect_pending)

    resume = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="confirm")],
            session_id="confirmation-test",
        ),
        decision="allow",
        call_id="call-old",
    )
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/agent-sandbox/resume",
        "headers": [],
        "query_string": b"",
    })

    async def invoke() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await chat_routes.resume_agent(resume, request, user={})
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"]["code"] == "HITL_CONFIRMATION_STALE"

    asyncio.run(invoke())

    build_agent.assert_awaited_once()
    detect_pending.assert_awaited_once_with(agent, {"configurable": {}})
    assert_scope.assert_not_called()
    release_lease.assert_called_once_with(binding, lease)
