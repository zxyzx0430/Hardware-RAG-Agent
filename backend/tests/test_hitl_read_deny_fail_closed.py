"""HITL checkpoint read and denial write failures must fail closed."""

import asyncio
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.agent import hitl_handler, sse_adapter, sse_helpers


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
    config = {"configurable": {"thread_id": "hitl-write-failure"}}
    pending = [
        {"name": "write_file", "args": {"path": "x"}, "call_id": "call-1"}
    ]
    update_state = AsyncMock(side_effect=error)
    agent = SimpleNamespace(aupdate_state=update_state)
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

    emitted = asyncio.run(collect())

    assert emitted == []
    update_state.assert_awaited_once()
    assert update_state.await_args.args[0] is config
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
    config = {"configurable": {"thread_id": "hitl-write-success"}}
    pending = [{"name": "write_file", "args": {}, "call_id": "call-2"}]
    actions = []

    async def update_state(received_config, *, values):
        assert received_config is config
        assert values["messages"][0].tool_call_id == "call-2"
        actions.append("state-write")

    def audit_deny(received_config, received_pending, source):
        assert received_config is config
        assert received_pending is pending
        assert source == hitl_handler.SOURCE_USER_DENY
        actions.append("audit")

    monkeypatch.setattr(hitl_handler, "_audit_deny_decisions", audit_deny)
    agent = SimpleNamespace(aupdate_state=AsyncMock(side_effect=update_state))

    asyncio.run(
        hitl_handler._inject_deny_messages(
            agent, config, pending, hitl_handler.SOURCE_USER_DENY
        )
    )

    assert actions == ["state-write", "audit"]
