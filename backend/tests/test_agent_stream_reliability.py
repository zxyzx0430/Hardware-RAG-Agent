"""Bounded Agent streaming, real progress, and producer cleanup regressions."""

import asyncio
import time
from collections import Counter

import pytest

from src.agent import sse_adapter
from src.agent.exceptions import AgentStreamIdleTimeoutError
from src.agent.prompts import build_system_prompt
from src.agent.streaming_event_bus import get_queue


class _SilentAgent:
    def __init__(self):
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def astream(self, *_args, **_kwargs):
        try:
            await self.release.wait()
            yield "updates", {}
        finally:
            self.closed.set()


class _EmptyTickAgent:
    def __init__(self):
        self.closed = asyncio.Event()

    async def astream(self, *_args, **_kwargs):
        try:
            while True:
                await asyncio.sleep(0.002)
                yield "updates", {}
        finally:
            self.closed.set()


@pytest.mark.asyncio
async def test_model_silence_times_out_and_removes_registered_queue(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.004)
    monkeypatch.setattr(sse_adapter, "AGENT_STREAM_IDLE_TIMEOUT_S", 0.025)
    monkeypatch.setattr(sse_adapter, "init_stream_state", lambda *_args: {})
    agent = _SilentAgent()
    config = {"configurable": {"thread_id": "idle-timeout-test"}}

    with pytest.raises(AgentStreamIdleTimeoutError):
        async for _ in sse_adapter.stream_agent_to_sse(
            agent, {}, config, Counter(), model="test-model",
        ):
            pass

    assert agent.closed.is_set()
    assert get_queue("idle-timeout-test") is None


@pytest.mark.asyncio
async def test_cancelling_stream_awaits_agent_producer_and_unregisters_queue(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.004)
    monkeypatch.setattr(sse_adapter, "init_stream_state", lambda *_args: {})
    agent = _SilentAgent()
    config = {"configurable": {"thread_id": "cancel-stream-test"}}
    stream = sse_adapter.stream_agent_to_sse(agent, {}, config, Counter(), model="test-model")

    first = await asyncio.wait_for(stream.__anext__(), timeout=0.2)
    assert '"type": "heartbeat"' in first
    assert get_queue("cancel-stream-test") is not None

    await stream.aclose()

    assert agent.closed.is_set()
    assert get_queue("cancel-stream-test") is None


@pytest.mark.asyncio
async def test_real_tool_progress_resets_idle_deadline_but_heartbeats_do_not(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(sse_adapter, "AGENT_STREAM_IDLE_TIMEOUT_S", 0.12)
    agent = _SilentAgent()
    queue = asyncio.Queue()

    async def produce_progress():
        for index in range(6):
            await asyncio.sleep(0.035)
            await queue.put({"type": "progress", "percent": index * 20, "message": "working"})
        agent.release.set()

    producer = asyncio.create_task(produce_progress())
    sse_events = [
        sse async for sse in sse_adapter._iter_agent_sse(
            agent, {}, {}, Counter(), {}, {"tool_event_queue": queue},
        )
    ]
    await producer

    assert len([event for event in sse_events if '"type": "progress"' in event]) == 6
    assert agent.closed.is_set()


@pytest.mark.asyncio
async def test_continuous_empty_agent_ticks_do_not_extend_idle_deadline(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.004)
    monkeypatch.setattr(sse_adapter, "AGENT_STREAM_IDLE_TIMEOUT_S", 0.025)
    agent = _EmptyTickAgent()

    async def consume():
        return [
            event async for event in sse_adapter._iter_agent_sse(
                agent, {}, {}, Counter(), {}, {"tool_event_queue": None},
            )
        ]

    with pytest.raises(AgentStreamIdleTimeoutError):
        await asyncio.wait_for(consume(), timeout=0.15)

    assert agent.closed.is_set()


@pytest.mark.asyncio
async def test_active_tool_uses_existing_tool_timeout_not_model_idle_deadline(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.004)
    monkeypatch.setattr(sse_adapter, "AGENT_STREAM_IDLE_TIMEOUT_S", 0.015)
    agent = _SilentAgent()

    async def release_after_model_idle_deadline():
        await asyncio.sleep(0.04)
        agent.release.set()

    release_task = asyncio.create_task(release_after_model_idle_deadline())
    call_start_time = {"running-tool": time.time()}
    events = [
        event async for event in sse_adapter._iter_agent_sse(
            agent, {}, {}, Counter(), call_start_time, {"tool_event_queue": None},
        )
    ]
    await release_task

    assert events == []
    assert agent.closed.is_set()


def test_system_prompt_sets_general_answer_completeness_and_permission_boundaries():
    prompt = build_system_prompt()

    assert "逐个列出被问到的位和值" in prompt
    assert "把推断标成推断" in prompt
    assert "实际盘点到的文件名" in prompt
    assert "不绕过权限门控" in prompt
    assert "本提示词不保证答案正确" in prompt
