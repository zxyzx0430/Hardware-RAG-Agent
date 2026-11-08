"""Session-local writer recovery: no permanent quarantine, one run per session."""

import asyncio
from types import SimpleNamespace

import pytest
from langgraph.runtime import RunControl

from app.api import chat_routes
from src.agent import agent_factory
from src.agent.compact.writer_lifecycle import (
    QuarantineReason,
    RunOutcome,
    WriterLease,
    WriterLeaseError,
    WriterLifecycleRegistry,
    WriterOwnerConflictError,
    WriterScope,
)


def _scope(owner_key="s-a", *, saver=None, graph=None):
    return WriterScope(
        owner_key=owner_key,
        graph=graph if graph is not None else object(),
        saver=saver if saver is not None else object(),
        request_scope=object(),
        epoch=object(),
    )


def _registry_for(scope):
    return WriterLifecycleRegistry(known_savers=(scope.saver,))


class _Binding:
    def __init__(self, registry, scope):
        self.registry = registry
        self.scope = scope

    def current_scope(self):
        return self.scope


def _finish(registry, lease, scope):
    registry.quarantine(
        lease, scope, reason=QuarantineReason.JOIN_UNCONFIRMED,
    )


def test_recovery_window_covers_stopped_run_teardown():
    assert chat_routes._WRITER_RECOVERY_WAIT_S >= 10.0
    assert 0.1 <= chat_routes._WRITER_RECOVERY_POLL_S <= 0.2


def test_quarantined_owner_reset_only_touches_that_session():
    scope_a = _scope("s-a")
    scope_b = _scope("s-b", saver=scope_a.saver)
    registry = _registry_for(scope_a)
    lease_a = registry.acquire(scope_a)
    lease_b = registry.acquire(scope_b)
    _finish(registry, lease_a, scope_a)

    assert registry.reset_owner("s-a") is True

    registry.assert_scope(lease_b, scope_b)
    assert registry.reset_owner("s-a") is False
    fresh_a = registry.acquire(_scope("s-a", saver=scope_a.saver))
    assert isinstance(fresh_a, WriterLease)


def test_active_run_blocks_reset_and_reports_retryable_conflict():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    registry.begin_run(lease, scope, RunControl())

    with pytest.raises(WriterOwnerConflictError):
        registry.reset_owner(scope.owner_key)
    with pytest.raises(WriterOwnerConflictError):
        registry.reset_owner(scope.owner_key, lease=lease)
    registry.assert_scope(lease, scope)


def test_owning_lease_can_reset_an_idle_owner_but_not_a_replacement():
    scope = _scope()
    registry = _registry_for(scope)
    old_lease = registry.acquire(scope)
    assert registry.reset_owner(scope.owner_key, lease=old_lease) is True

    new_lease = registry.acquire(scope)
    with pytest.raises(WriterLeaseError):
        registry.reset_owner(scope.owner_key, lease=old_lease)
    registry.assert_scope(new_lease, scope)


@pytest.mark.asyncio
async def test_acquire_recovers_quarantined_session_and_resets_checkpoint(monkeypatch):
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    _finish(registry, lease, scope)
    deleted = []
    monkeypatch.setattr(
        agent_factory, "delete_thread_checkpoint_direct",
        lambda thread_id: deleted.append(thread_id) or True,
    )

    recovered = await chat_routes._acquire_request_writer_lease_with_recovery(
        _Binding(registry, scope),
    )

    assert isinstance(recovered, WriterLease)
    assert deleted == [scope.owner_key]
    registry.release(recovered, scope)


@pytest.mark.asyncio
async def test_acquire_waits_for_active_run_then_reports_retryable_conflict(monkeypatch):
    monkeypatch.setattr(chat_routes, "_WRITER_RECOVERY_WAIT_S", 0.05)
    monkeypatch.setattr(chat_routes, "_WRITER_RECOVERY_POLL_S", 0.01)
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    handle = registry.begin_run(lease, scope, RunControl())

    with pytest.raises(WriterOwnerConflictError):
        await chat_routes._acquire_request_writer_lease_with_recovery(_Binding(registry, scope))

    registry.finish_run(
        lease, scope, handle, joined=True, outcome=RunOutcome.KNOWN_TERMINAL,
    )
    registry.release(lease, scope)
    acquired = await chat_routes._acquire_request_writer_lease_with_recovery(
        _Binding(registry, scope),
    )
    assert isinstance(acquired, WriterLease)


@pytest.mark.asyncio
async def test_release_failure_resets_only_its_own_session(monkeypatch):
    scope_a = _scope("s-a")
    scope_b = _scope("s-b", saver=scope_a.saver)
    registry = _registry_for(scope_a)
    lease_a = registry.acquire(scope_a)
    lease_b = registry.acquire(scope_b)
    registry.begin_run(lease_a, scope_a, RunControl())
    _finish(registry, lease_a, scope_a)
    deleted = []
    monkeypatch.setattr(
        agent_factory, "delete_thread_checkpoint_direct",
        lambda thread_id: deleted.append(thread_id) or True,
    )

    await chat_routes._release_request_writer_lease_with_recovery(
        _Binding(registry, scope_a), lease_a,
    )

    assert deleted == ["s-a"]
    registry.assert_scope(lease_b, scope_b)
    fresh_a = registry.acquire(scope_a)
    registry.release(fresh_a, scope_a)


def test_tool_context_receives_request_run_id():
    snapshot = chat_routes.AgentRequestSnapshot(
        session_id="s1", permission_mode="plan", run_id="r_test",
    )
    payload = SimpleNamespace(
        permission_mode="plan", session_id="s1", kb_ids=None, skills_mode="off",
    )

    bound = chat_routes._ensure_request_tool_context(snapshot, payload, "s1")

    assert bound.run_id == "r_test"
    assert bound.tool_context.run_id == "r_test"


def test_build_tool_ctx_carries_run_id():
    context = agent_factory._build_tool_ctx(
        SimpleNamespace(
            permission_mode="plan", session_id="s1", kb_ids=None, skills_mode="off",
        ),
        run_id="r_direct",
    )

    assert context.run_id == "r_direct"


def test_chat_stream_snapshot_keeps_created_run_id(monkeypatch):
    captured = {}

    async def fake_stream(
        _payload, _creds, *, long_term_memory, request_snapshot,
        authenticated_user, assembled_history,
    ):
        captured["run_id"] = request_snapshot.run_id
        from app.api.sse import sse_event

        yield sse_event("text", {"content": "ok"})
        yield sse_event("done", {"success": True, "completed": True})

    from fastapi import Request

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
    monkeypatch.setattr(
        chat_routes, "resolve_credentials",
        lambda _payload, _request: {
            "api_key": "test-key", "base_url": "https://example.invalid/v1",
            "model": "test-model", "provider": "test-provider",
        },
    )
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: True)
    monkeypatch.setattr(chat_routes, "_clamp_payload_to_agent_plan_mode", lambda p: p)
    monkeypatch.setattr(
        chat_routes, "_capture_request_snapshot_epochs", lambda _sid: ("", ""),
    )
    monkeypatch.setattr(
        chat_routes, "_require_request_snapshot_epochs_current", lambda *_args: None,
    )

    async def no_attachments(_payload):
        return "", []

    monkeypatch.setattr(chat_routes, "_process_attachments", no_attachments)
    monkeypatch.setattr(chat_routes, "_build_chat_history", lambda _messages: ([], "q"))
    monkeypatch.setattr(chat_routes, "_build_system_prompt", lambda *_a, **_k: "p")
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: object())
    monkeypatch.setattr(chat_routes, "_run_agent_stream", fake_stream)
    payload = chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(role="user", content="question")],
        use_agent=True,
        session_id="run-id-binding-test",
    )
    request = Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/chat", "raw_path": b"/api/chat",
        "query_string": b"", "headers": [], "server": ("test", 80),
        "client": ("test", 1),
    })

    async def _collect():
        response = await chat_routes.chat_sse(payload, request, user={})
        async for _ in response.body_iterator:
            pass
        return response

    response = asyncio.run(_collect())
    assert response.headers["X-Run-Id"] == captured["run_id"]
