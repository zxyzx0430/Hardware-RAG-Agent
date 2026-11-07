"""Bounded Agent streaming, real progress, and producer cleanup regressions."""

import asyncio
import time
from collections import Counter

import pytest

from src.agent import sse_adapter
from src.agent.exceptions import AgentStreamIdleTimeoutError
from src.agent.prompts import build_system_prompt
from src.agent.streaming_event_bus import get_queue
from tests.stream_writer_lease_support import authenticated_test_stream


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
    stream_double = _SilentAgent()
    with authenticated_test_stream(
        monkeypatch, stream_double, business_session_id="idle-timeout-test",
    ) as (agent, config, queue_id):
        with pytest.raises(AgentStreamIdleTimeoutError):
            async for _ in sse_adapter.stream_agent_to_sse(
                agent, {}, config, Counter(), model="test-model",
            ):
                pass

        assert stream_double.closed.is_set()
        assert get_queue(queue_id) is None


@pytest.mark.asyncio
async def test_cancelling_stream_awaits_agent_producer_and_unregisters_queue(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.004)
    monkeypatch.setattr(sse_adapter, "init_stream_state", lambda *_args: {})
    stream_double = _SilentAgent()
    with authenticated_test_stream(
        monkeypatch, stream_double, business_session_id="cancel-stream-test",
    ) as (agent, config, queue_id):
        stream = sse_adapter.stream_agent_to_sse(agent, {}, config, Counter(), model="test-model")

        first = await asyncio.wait_for(stream.__anext__(), timeout=0.2)
        assert '"type": "heartbeat"' in first
        assert get_queue(queue_id) is not None

        await stream.aclose()

        assert stream_double.closed.is_set()
        assert get_queue(queue_id) is None


@pytest.mark.asyncio
async def test_real_tool_progress_resets_idle_deadline_but_heartbeats_do_not(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(sse_adapter, "AGENT_STREAM_IDLE_TIMEOUT_S", 0.12)
    stream_double = _SilentAgent()
    queue = asyncio.Queue()
    with authenticated_test_stream(
        monkeypatch, stream_double, business_session_id="tool-progress-test",
    ) as (agent, config, _queue_id):
        async def produce_progress():
            for index in range(6):
                await asyncio.sleep(0.035)
                await queue.put({"type": "progress", "percent": index * 20, "message": "working"})
            stream_double.release.set()

        producer = asyncio.create_task(produce_progress())
        sse_events = [
            sse async for sse in sse_adapter._iter_agent_sse(
                agent, {}, config, Counter(), {}, {"tool_event_queue": queue},
            )
        ]
        await producer

    assert len([event for event in sse_events if '"type": "progress"' in event]) == 6
    assert stream_double.closed.is_set()


@pytest.mark.asyncio
async def test_continuous_empty_agent_ticks_do_not_extend_idle_deadline(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.004)
    monkeypatch.setattr(sse_adapter, "AGENT_STREAM_IDLE_TIMEOUT_S", 0.025)
    stream_double = _EmptyTickAgent()
    with authenticated_test_stream(
        monkeypatch, stream_double, business_session_id="empty-ticks-test",
    ) as (agent, config, _queue_id):
        async def consume():
            return [
                event async for event in sse_adapter._iter_agent_sse(
                    agent, {}, config, Counter(), {}, {"tool_event_queue": None},
                )
            ]

        with pytest.raises(AgentStreamIdleTimeoutError):
            await asyncio.wait_for(consume(), timeout=0.15)

    assert stream_double.closed.is_set()


@pytest.mark.asyncio
async def test_active_tool_uses_existing_tool_timeout_not_model_idle_deadline(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.004)
    monkeypatch.setattr(sse_adapter, "AGENT_STREAM_IDLE_TIMEOUT_S", 0.015)
    stream_double = _SilentAgent()
    with authenticated_test_stream(
        monkeypatch, stream_double, business_session_id="active-tool-test",
    ) as (agent, config, _queue_id):
        async def release_after_model_idle_deadline():
            await asyncio.sleep(0.04)
            stream_double.release.set()

        release_task = asyncio.create_task(release_after_model_idle_deadline())
        call_start_time = {"running-tool": time.time()}
        events = [
            event async for event in sse_adapter._iter_agent_sse(
                agent, {}, config, Counter(), call_start_time, {"tool_event_queue": None},
            )
        ]
        await release_task

    assert events == []
    assert stream_double.closed.is_set()


def test_system_prompt_sets_general_answer_completeness_and_permission_boundaries():
    prompt = build_system_prompt()

    assert "逐个列出被问到的位和值" in prompt
    assert "把推断标成推断" in prompt
    assert "实际盘点到的文件名" in prompt
    assert "不绕过权限门控" in prompt
    assert "本提示词不保证答案正确" in prompt


def test_system_prompt_requires_markdown_for_tool_progress_narration():
    prompt = build_system_prompt()

    assert "工具循环中的进度叙述" in prompt
    assert "边做边说" in prompt
    assert "1–3 条简短要点" in prompt
    assert "简单调用之间一句话带过即可" in prompt
    assert "关键文件、命令、函数、字段用行内代码" in prompt
    assert "不要写成长段落，也不要用标题或 fenced code block" in prompt
    assert "标题、表格和代码块留给最终回答" in prompt
    assert "进度叙述不要重复最终答案" in prompt
