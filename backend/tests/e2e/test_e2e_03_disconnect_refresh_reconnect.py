"""E2E 3: disconnect/refresh keeps partial text; reconnect contract."""

import pytest

from app.api.sse import sse_event
from tests.e2e.helpers import collect_chat


@pytest.mark.asyncio
async def test_partial_text_survives_stream_failure(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield sse_event("text", {"content": "partial answer"})
        raise RuntimeError("network disconnected")

    events, _ = await collect_chat(monkeypatch, fake_stream, use_agent=True)

    assert any(event.get("content") == "partial answer" for event in events)
    assert any(event["type"] == "error" for event in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["success"] is False


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="W5 target: SSE frames need trace_id/seq and id: for Last-Event-ID replay",
    strict=False,
)
async def test_sse_frames_support_reconnect_replay(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield sse_event("text", {"content": "one"})
        yield sse_event("text", {"content": "two"})

    events, body = await collect_chat(monkeypatch, fake_stream, use_agent=True)

    assert all("trace_id" in event and "seq" in event for event in events)
    assert "\nid: " in body
