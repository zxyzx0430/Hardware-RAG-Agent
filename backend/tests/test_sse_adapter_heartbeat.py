import asyncio

import pytest

from src.agent import sse_adapter


@pytest.mark.asyncio
async def test_agent_stream_emits_heartbeat_while_waiting(monkeypatch):
    monkeypatch.setattr(sse_adapter, "AGENT_HEARTBEAT_INTERVAL", 0.01, raising=False)
    release = asyncio.Event()

    class SilentAgent:
        async def astream(self, *_args, **_kwargs):
            await release.wait()
            yield "messages", "finished"

    stream = sse_adapter._merge_agent_and_tool_events(
        SilentAgent(), {}, {}, asyncio.Queue()
    )
    try:
        event = await asyncio.wait_for(stream.__anext__(), timeout=0.2)
        assert event == {"source": "tool_event", "event": {"type": "heartbeat"}}
    finally:
        release.set()
        await stream.aclose()
