"""Plan/todo SSE events: emission, persistence and progress recognition."""

import pytest

from src.agent import run_events
from src.agent.sse_adapter import _sse_indicates_progress
from src.agent.sse_helpers import _assemble_tool_result_events, _maybe_emit_plan_update
from src.agent.run_events import InMemoryRunStore, stream_with_persistence
from app.api.sse import sse_event


def _plan():
    return {
        "id": "pl_1", "session_id": "s1", "version": 1, "status": "pending",
        "title": "Plan", "summary": "Summary", "steps": [], "risks": [],
        "open_questions": [], "run_id": "r1", "executed_run_id": None,
        "created_at": "2026-11-06T00:00:00Z", "decided_at": None,
    }


def test_maybe_emit_plan_update():
    events: list[str] = []
    _maybe_emit_plan_update(events, {"_plan_update": True, "plan": _plan()})

    assert any("plan_update" in event and "pl_1" in event for event in events)


def test_plan_update_is_emitted_before_tool_result():
    events = _assemble_tool_result_events(
        "submit_plan", "c1", 5, True, {"_plan_update": True, "plan": _plan()},
    )

    assert "plan_update" in events[0]
    assert "tool_result" in events[1]


def test_empty_todo_update_is_a_progress_event():
    frame = sse_event("todo_update", {"todos": []})

    assert _sse_indicates_progress(frame) is True


def test_plan_update_is_a_progress_event():
    frame = sse_event("plan_update", {"plan": _plan()})

    assert _sse_indicates_progress(frame) is True


@pytest.mark.asyncio
async def test_plan_update_is_persisted():
    store = InMemoryRunStore()
    store.create_run({"id": "r1", "session_id": "s1"})

    async def inner():
        yield sse_event("plan_update", {"plan": _plan()})
        yield sse_event("done", {"success": True, "completed": True})

    frames = [
        frame
        async for frame in stream_with_persistence(
            inner(), store=store, run_id="r1", emit_started=False,
        )
    ]

    assert any("plan_update" in frame for frame in frames)
    events = store.list_run_events("r1")
    assert [event["type"] for event in events] == ["plan_update", "done"]
