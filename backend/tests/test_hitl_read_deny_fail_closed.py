"""HITL checkpoint read and denial write failures must fail closed."""

import asyncio
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage

from src.agent import hitl_handler, sse_adapter, sse_helpers
from src.agent.compact.writer_lifecycle import (
    WriterLifecycleRegistry,
    WriterScope,
    bind_writer_scope,
)

PENDING_CALL = {"name": "write_file", "args": {"path": "x"}, "call_id": "call-1"}


def _checkpoint_config(thread_id: str, checkpoint_id: str) -> dict:
    return {"configurable": {
        "thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": checkpoint_id,
    }}


@contextmanager
def _bound_writer(thread_id: str):
    """Install the host-owned writer binding the real resume path provides."""
    saver = object()
    scope = WriterScope(
        owner_key=thread_id,
        graph=object(),
        saver=saver,
        request_scope=object(),
        epoch=object(),
    )
    registry = WriterLifecycleRegistry(known_savers=(saver,))
    lease = registry.acquire(scope)
    with bind_writer_scope(registry, lease, scope, scope_provider=lambda: scope):
        yield


def _checkpoint_agent(thread_id: str, update_state: AsyncMock, call: dict | None = None):
    """Fake agent with the checkpoint shape HITL denial verification expects."""
    pending = call or PENDING_CALL
    before_config = _checkpoint_config(thread_id, "cp-1")
    applied_config = _checkpoint_config(thread_id, "cp-2")
    ai_message = AIMessage(
        content="",
        tool_calls=[{
            "id": pending["call_id"],
            "name": pending["name"],
            "args": pending["args"],
            "type": "tool_call",
        }],
    )
    before = SimpleNamespace(
        next=("tools",), values={"messages": [ai_message]},
        config=before_config, metadata={"source": "loop"},
    )
    denials: dict = {}

    async def aget_state(config):
        if "messages" not in denials:
            return before
        return SimpleNamespace(
            next=("model",), values={"messages": [ai_message, *denials["messages"]]},
            config=applied_config, metadata={"source": "update"},
        )

    async def aupdate_state(config, *, values):
        result = await update_state(config, values=values)
        denials["messages"] = values["messages"]
        return result

    return SimpleNamespace(aget_state=aget_state, aupdate_state=aupdate_state), before_config


def test_checkpoint_read_failure_is_not_treated_as_no_pending_interrupt() -> None:
    error = RuntimeError("checkpoint read failed")
    config = {"configurable": {"thread_id": "hitl-read-failure"}}
    get_state = AsyncMock(side_effect=error)
    agent = SimpleNamespace(aget_state=get_state)

    async def read_state() -> None:
        with pytest.raises(RuntimeError, match="checkpoint read failed"):
            await hitl_handler._detect_tools_interrupt(agent, config)

    asyncio.run(read_state())

    get_state.assert_awaited_once_with(config)


@pytest.mark.parametrize("entrypoint", ["user_resume", "auto_resume"])
def test_deny_write_failure_emits_no_deny_result_or_resume(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    error = RuntimeError("checkpoint write failed")
    thread_id = "hitl-write-failure"
    config = {"configurable": {"thread_id": thread_id}}
    pending = [dict(PENDING_CALL)]
    update_state = AsyncMock(side_effect=error)
    agent, before_config = _checkpoint_agent(thread_id, update_state)
    detect_interrupt = AsyncMock(return_value=pending)
    audit_deny = Mock()
    deny_results = Mock(return_value=("deny-result",))
    resume_calls = []

    async def fake_resume(*args):
        resume_calls.append(args)
        yield "resume-result"

    resume_dispatch = Mock(side_effect=fake_resume)
    monkeypatch.setattr(hitl_handler, "_detect_tools_interrupt", detect_interrupt)
    monkeypatch.setattr(hitl_handler, "_get_tool_ctx", lambda: None)
    monkeypatch.setattr(hitl_handler, "_audit_deny_decisions", audit_deny)
    monkeypatch.setattr(hitl_handler, "emit_deny_tool_results", deny_results)
    monkeypatch.setattr(
        hitl_handler,
        "evaluate_pending",
        lambda *_args: hitl_handler.DECISION_DENY,
    )
    monkeypatch.setattr(sse_adapter, "_iter_agent_sse", resume_dispatch)
    monkeypatch.setattr(sse_helpers, "init_stream_state", lambda *_args: {})

    if entrypoint == "user_resume":
        stream = hitl_handler.resume_agent_after_user(
            agent, config, "deny", Counter(), "default"
        )
    else:
        stream = hitl_handler.handle_auto_resume(
            agent, config, "default", Counter(), {}, {}
        )

    async def collect() -> list[str]:
        emitted = []
        with pytest.raises(RuntimeError, match="checkpoint write failed"):
            async for frame in stream:
                emitted.append(frame)
        return emitted

    with _bound_writer(thread_id):
        emitted = asyncio.run(collect())

    assert emitted == []
    update_state.assert_awaited_once()
    assert update_state.await_args.args[0] is before_config
    assert (
        update_state.await_args.kwargs["values"]["messages"][0].tool_call_id
        == "call-1"
    )
    detect_interrupt.assert_awaited_once_with(agent, config)
    expected_source = (
        hitl_handler.SOURCE_USER_DENY
        if entrypoint == "user_resume"
        else hitl_handler.SOURCE_PATH_DENY
    )
    audit_deny.assert_called_once_with(config, pending, expected_source)
    deny_results.assert_not_called()
    resume_dispatch.assert_not_called()
    assert resume_calls == []


def test_successful_deny_write_keeps_audit_after_state_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "hitl-write-success"
    config = {"configurable": {"thread_id": thread_id}}
    pending = [{"name": "write_file", "args": {}, "call_id": "call-2"}]
    actions = []

    async def write_state(received_config, *, values):
        assert received_config["configurable"]["thread_id"] == thread_id
        assert values["messages"][0].tool_call_id == "call-2"
        actions.append("state-write")
        return _checkpoint_config(thread_id, "cp-2")

    def audit_deny(received_config, received_pending, source):
        assert received_config is config
        assert received_pending is pending
        assert source == hitl_handler.SOURCE_USER_DENY
        actions.append("audit")

    monkeypatch.setattr(hitl_handler, "_audit_deny_decisions", audit_deny)
    update_state = AsyncMock(side_effect=write_state)
    agent, _ = _checkpoint_agent(thread_id, update_state, pending[0])

    with _bound_writer(thread_id):
        asyncio.run(
            hitl_handler._inject_deny_messages(
                agent, config, pending, hitl_handler.SOURCE_USER_DENY
            )
        )

    assert actions == ["state-write", "audit"]

    assert actions == ["state-write", "audit"]
