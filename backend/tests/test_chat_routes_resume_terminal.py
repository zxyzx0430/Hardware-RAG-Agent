"""Resume SSE terminal tests use mocked Agent events and no chat database."""

import asyncio
import json
from collections import Counter

import pytest

from app.api import chat_routes
from app.api.sse import sse_event
from src.agent import hitl_handler


@pytest.mark.parametrize(
    ("decision", "handler_terminal", "expected_done"),
    [
        ("allow", False, {"type": "done", "success": True, "usage": None}),
        ("stop", True, {"type": "done", "success": False, "reason": "user stopped"}),
    ],
)
def test_resume_generator_emits_one_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    handler_terminal: bool,
    expected_done: dict,
) -> None:
    async def fake_resume_agent_after_user(*_args):
        yield sse_event("text", {"content": "partial"})
        if handler_terminal:
            yield sse_event("done", {"success": False, "reason": "user stopped"})

    monkeypatch.setattr(hitl_handler, "resume_agent_after_user", fake_resume_agent_after_user)
    request = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="question")]
        ),
        decision=decision,
    )
    ctx = chat_routes.ResumeContext(
        agent=object(),
        config={},
        req=request,
        call_counter=Counter(),
        model="test-model",
    )

    async def collect_events() -> list[dict]:
        frames = [frame async for frame in chat_routes._resume_event_generator_from_ctx(ctx)]
        return [json.loads(line[6:]) for frame in frames for line in frame.splitlines() if line.startswith("data: ")]

    events = asyncio.run(collect_events())
    done_events = [event for event in events if event.get("type") == "done"]

    assert done_events == [expected_done]
