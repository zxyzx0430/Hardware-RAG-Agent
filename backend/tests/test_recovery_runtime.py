"""Recovery R1 runtime tests: run events, replay, HITL approvals, lifecycle."""

from __future__ import annotations

import asyncio
import json
from collections import Counter

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import chat_routes, runs
from app.api.sse import sse_event
from src.agent import run_events

RUN_ID = "r_runtime_test"
SESSION_ID = "session-runtime-test"


class FakeRunStore(run_events.InMemoryRunStore):
    """In-memory store that records append order to assert persist-before-yield."""

    def __init__(self) -> None:
        super().__init__()
        self.appended: list[tuple[str, int, dict]] = []

    def append_run_event(self, run_id, event_type, payload):
        seq = super().append_run_event(run_id, event_type, payload)
        self.appended.append((event_type, seq, dict(payload)))
        return seq


@pytest.fixture(autouse=True)
def isolated_run_runtime():
    store = FakeRunStore()
    run_events.set_run_store(store)
    run_events.reset_runtime_state()
    yield store
    run_events.reset_runtime_state()
    run_events.set_run_store(None)


def _seed_run(
    store: run_events.InMemoryRunStore,
    *,
    run_id: str = RUN_ID,
    session_id: str = SESSION_ID,
    status: str = run_events.RUN_STATUS_RUNNING,
    events: tuple[tuple[str, dict], ...] = (),
) -> None:
    store.create_run({
        "id": run_id,
        "session_id": session_id,
        "status": status,
        "model": "test-model",
        "permission_mode": "default",
        "config_json": "{}",
        "thread_id": session_id,
        "checkpoint_id": None,
        "started_at": run_events.utc_now_iso(),
        "finished_at": None,
        "last_event_seq": 0,
        "error_code": None,
        "error_message": None,
    })
    for event_type, payload in events:
        store.append_run_event(run_id, event_type, payload)


def _request(headers: dict[str, str] | None = None, path: str = "/api/runs/x") -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": raw_headers,
        "server": ("test", 80),
        "client": ("test", 1),
    })


async def _read_frames(response) -> list[tuple[str | None, dict]]:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    frames: list[tuple[str | None, dict]] = []
    for chunk in chunks:
        for raw in run_events.split_sse_frames(chunk):
            payload = run_events.decode_sse_frame(raw)
            assert payload is not None
            frame_id = next(
                (line[4:] for line in raw.splitlines() if line.startswith("id: ")), None,
            )
            frames.append((frame_id, payload))
    return frames


async def _collect(gen) -> list[tuple[str | None, dict]]:
    frames: list[tuple[str | None, dict]] = []
    async for chunk in gen:
        for raw in run_events.split_sse_frames(chunk):
            payload = run_events.decode_sse_frame(raw)
            assert payload is not None
            frame_id = next(
                (line[4:] for line in raw.splitlines() if line.startswith("id: ")), None,
            )
            frames.append((frame_id, payload))
    return frames


# ---------------------------------------------------------------------------
# Writer and stream wrapper
# ---------------------------------------------------------------------------

def test_writer_persists_before_returning_frame(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store)
    writer = run_events.RunEventWriter(store, RUN_ID)

    seq, frame = writer.append("text", {"type": "text", "content": "hello"})

    assert seq == 1
    assert store.appended == [("text", 1, {"type": "text", "content": "hello"})]
    assert frame == 'id: r_runtime_test:1\ndata: {"type": "text", "content": "hello"}\n\n'
    assert store.get_run(RUN_ID)["last_event_seq"] == 1


async def test_stream_emits_run_started_first_and_does_not_persist_heartbeat(
    isolated_run_runtime,
):
    store = isolated_run_runtime
    _seed_run(store)

    async def inner():
        yield sse_event("heartbeat", {})
        yield sse_event("text", {"content": "hi"})
        yield sse_event("done", {"success": True, "completed": True})

    frames = await _collect(run_events.stream_with_persistence(
        inner(), store=store, run_id=RUN_ID, session_id=SESSION_ID,
    ))

    assert frames[0][0] == f"{RUN_ID}:1"
    assert frames[0][1]["type"] == "run_started"
    assert frames[0][1]["run_id"] == RUN_ID
    assert frames[1][0] is None
    assert frames[1][1] == {"type": "heartbeat"}
    assert [frame_id for frame_id, _ in frames[2:]] == [f"{RUN_ID}:2", f"{RUN_ID}:3"]
    assert [payload["type"] for _, payload in frames[2:]] == ["text", "done"]
    assert [event_type for event_type, _, _ in store.appended] == [
        "run_started", "text", "done",
    ]
    run = store.get_run(RUN_ID)
    assert run["status"] == run_events.RUN_STATUS_COMPLETED
    assert run["last_event_seq"] == 3


async def test_stream_cancellation_persists_cancelled_terminal(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store)

    async def inner():
        yield sse_event("text", {"content": "partial"})
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _collect(run_events.stream_with_persistence(
            inner(), store=store, run_id=RUN_ID,
        ))

    run = store.get_run(RUN_ID)
    assert run["status"] == run_events.RUN_STATUS_CANCELLED
    terminal = store.list_run_events(RUN_ID)[-1]
    assert terminal["type"] == "done"
    assert terminal["payload"]["cancelled"] is True


async def test_stream_unexpected_error_persists_error_and_failed_terminal(
    isolated_run_runtime,
):
    store = isolated_run_runtime
    _seed_run(store)

    async def inner():
        yield sse_event("text", {"content": "partial"})
        raise RuntimeError("boom")

    frames = await _collect(run_events.stream_with_persistence(
        inner(), store=store, run_id=RUN_ID,
    ))

    assert [payload["type"] for _, payload in frames] == [
        "run_started", "text", "error", "done",
    ]
    assert frames[-1][1] == {"type": "done", "success": False, "completed": False}
    run = store.get_run(RUN_ID)
    assert run["status"] == run_events.RUN_STATUS_FAILED
    assert run["error_code"] == "INTERNAL_ERROR"


async def test_awaiting_confirmation_keeps_run_active(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store)

    async def inner():
        yield sse_event("tool_confirm_required", {"calls": [], "count": 0})
        yield sse_event("done", {"success": True, "awaiting_confirmation": True})

    await _collect(run_events.stream_with_persistence(
        inner(), store=store, run_id=RUN_ID, session_id=SESSION_ID,
    ))

    assert store.get_run(RUN_ID)["status"] == run_events.RUN_STATUS_AWAITING_CONFIRMATION


# ---------------------------------------------------------------------------
# POST /api/chat integration
# ---------------------------------------------------------------------------

def _configure_chat_route(monkeypatch, fake_stream) -> None:
    async def no_attachments(_payload):
        return "", []

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
    monkeypatch.setattr(
        chat_routes,
        "resolve_credentials",
        lambda _payload, _request: {
            "api_key": "test-key", "base_url": "https://example.invalid/v1",
            "model": "test-model", "provider": "test-provider",
        },
    )
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: True)
    monkeypatch.setattr(chat_routes, "_process_attachments", no_attachments)
    monkeypatch.setattr(chat_routes, "_build_chat_history", lambda _messages: ([], "question"))
    monkeypatch.setattr(chat_routes, "_build_system_prompt", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: object())
    monkeypatch.setattr(chat_routes, "_run_agent_stream", fake_stream)


async def test_chat_route_returns_run_id_and_persists_events(
    monkeypatch, isolated_run_runtime,
):
    store = isolated_run_runtime
    calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield sse_event("text", {"content": "answer"})
        yield sse_event("done", {"success": True, "completed": True})

    _configure_chat_route(monkeypatch, fake_stream)
    payload = chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(role="user", content="question")],
        use_agent=True,
        session_id=SESSION_ID,
    )
    response = await chat_routes.chat_sse(
        payload, _request(path="/api/chat"), user={},
    )

    run_id = response.headers["X-Run-Id"]
    assert run_id.startswith("r_")
    frames = await _read_frames(response)

    assert frames[0] == (
        f"{run_id}:1",
        {
            "type": "run_started", "run_id": run_id,
            "session_id": SESSION_ID, "seq": 1,
        },
    )
    assert [payload["type"] for _, payload in frames[1:]] == ["text", "done"]
    assert all(frame_id == f"{run_id}:{index}" for index, (frame_id, _) in enumerate(frames, start=1))

    run = store.get_run(run_id)
    assert run["status"] == run_events.RUN_STATUS_COMPLETED
    assert run["session_id"] == SESSION_ID
    assert calls == 1


async def test_hitl_confirmation_persists_pending_approvals_and_snapshot(
    monkeypatch, isolated_run_runtime,
):
    store = isolated_run_runtime

    async def fake_stream(*_args, **_kwargs):
        yield sse_event("tool_confirm_required", {
            "calls": [
                {
                    "name": "run_command",
                    "args": {"command": "pio run"},
                    "call_id": "call-1",
                    "risk_level": "high",
                },
            ],
            "count": 1,
        })
        yield sse_event("done", {"success": True, "awaiting_confirmation": True})

    _configure_chat_route(monkeypatch, fake_stream)
    payload = chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(role="user", content="flash")],
        use_agent=True,
        session_id=SESSION_ID,
        tool_keys={"tavily": "secret-value"},
    )
    response = await chat_routes.chat_sse(
        payload, _request(path="/api/chat"), user={},
    )
    run_id = response.headers["X-Run-Id"]
    await _read_frames(response)

    run = store.get_run(run_id)
    assert run["status"] == run_events.RUN_STATUS_AWAITING_CONFIRMATION
    approvals = store.list_pending_approvals(run_id)
    assert len(approvals) == 1
    assert approvals[0]["call_id"] == "call-1"
    assert approvals[0]["tool_name"] == "run_command"
    assert approvals[0]["status"] == run_events.APPROVAL_STATUS_PENDING
    assert json.loads(approvals[0]["args_json"]) == {"command": "pio run"}
    snapshot = store.get_latest_request_snapshot(run_id)
    assert snapshot is not None
    assert "secret-value" not in snapshot["payload_json"]


# ---------------------------------------------------------------------------
# Replay endpoint
# ---------------------------------------------------------------------------

async def test_replay_honors_after_and_last_event_id(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_COMPLETED, events=(
        ("text", {"type": "text", "content": "one"}),
        ("text", {"type": "text", "content": "two"}),
        ("done", {"type": "done", "success": True, "completed": True}),
    ))

    response = await runs.replay_run_events(
        RUN_ID, _request(), after=1, user={},
    )
    frames = await _read_frames(response)
    assert [frame_id for frame_id, _ in frames] == [f"{RUN_ID}:2", f"{RUN_ID}:3"]

    response = await runs.replay_run_events(
        RUN_ID, _request(headers={"Last-Event-ID": f"{RUN_ID}:2"}), after=None, user={},
    )
    frames = await _read_frames(response)
    assert [frame_id for frame_id, _ in frames] == [f"{RUN_ID}:3"]


async def test_replay_rejects_foreign_last_event_id(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_COMPLETED)

    with pytest.raises(HTTPException) as exc_info:
        await runs.replay_run_events(
            RUN_ID, _request(headers={"Last-Event-ID": "r_other:3"}), after=None, user={},
        )
    assert exc_info.value.status_code == 400


async def test_replay_interrupted_appends_error_and_done(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_INTERRUPTED, events=(
        ("text", {"type": "text", "content": "before crash"}),
    ))

    response = await runs.replay_run_events(RUN_ID, _request(), after=None, user={})
    frames = await _read_frames(response)

    assert frames[0][0] == f"{RUN_ID}:1"
    assert frames[1][0] is None
    assert frames[1][1]["type"] == "error"
    assert frames[1][1]["code"] == "INTERRUPTED"
    assert frames[2][0] is None
    assert frames[2][1]["type"] == "done"
    assert frames[2][1]["interrupted"] is True
    assert frames[2][1]["success"] is False


async def test_replay_follows_live_events(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, events=(("text", {"type": "text", "content": "first"}),))

    received: list[tuple[str | None, dict]] = []

    async def consume():
        async for chunk in run_events.iterate_run_events(
            RUN_ID, after_seq=1, store=store, heartbeat_interval=0.05,
        ):
            for raw in run_events.split_sse_frames(chunk):
                payload = run_events.decode_sse_frame(raw)
                frame_id = next(
                    (line[4:] for line in raw.splitlines() if line.startswith("id: ")), None,
                )
                received.append((frame_id, payload or {}))
                if (payload or {}).get("type") == "done":
                    return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    writer = run_events.RunEventWriter(store, RUN_ID, bus=run_events.get_run_bus())
    writer.append("text", {"type": "text", "content": "second"})
    writer.append("done", {"type": "done", "success": True, "completed": True})
    await asyncio.wait_for(task, timeout=1.0)

    assert any(payload.get("type") == "text" and payload.get("content") == "second"
               for _, payload in received)
    assert received[-1][1]["type"] == "done"


# ---------------------------------------------------------------------------
# Cancel, startup recovery, run detail
# ---------------------------------------------------------------------------

async def test_cancel_cancels_live_task_and_stops_pending_approvals(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store)
    store.create_pending_approval({
        "id": "pa_1", "run_id": RUN_ID, "call_id": "call-1",
        "tool_name": "run_command", "args_json": "{}", "risk_level": "high",
        "status": run_events.APPROVAL_STATUS_PENDING,
        "created_at": run_events.utc_now_iso(), "expires_at": None,
        "decided_at": None, "principal_id": None,
    })

    async def waiter():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(waiter())
    run_events.get_run_control().register(RUN_ID, task)

    result = await runs.cancel_run(RUN_ID, user={})

    assert result == {"success": True, "run_id": RUN_ID, "cancelled": True}
    with pytest.raises(asyncio.CancelledError):
        await task
    approvals = store.list_pending_approvals(RUN_ID)
    assert approvals[0]["status"] == "stopped"


async def test_cancel_without_live_task_marks_cancelled(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store)

    await runs.cancel_run(RUN_ID, user={})

    assert store.get_run(RUN_ID)["status"] == run_events.RUN_STATUS_CANCELLED


async def test_cancel_rejects_finished_run(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_COMPLETED)

    with pytest.raises(HTTPException) as exc_info:
        await runs.cancel_run(RUN_ID, user={})
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUN_NOT_ACTIVE"


async def test_startup_recovery_marks_active_runs_interrupted(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, run_id="r_running", status=run_events.RUN_STATUS_RUNNING)
    _seed_run(store, run_id="r_awaiting", status=run_events.RUN_STATUS_AWAITING_CONFIRMATION)
    _seed_run(store, run_id="r_done", status=run_events.RUN_STATUS_COMPLETED)

    await runs.recover_interrupted_runs()

    assert store.get_run("r_running")["status"] == run_events.RUN_STATUS_INTERRUPTED
    assert store.get_run("r_running")["error_code"] == "PROCESS_RESTART"
    assert store.get_run("r_awaiting")["status"] == run_events.RUN_STATUS_INTERRUPTED
    assert store.get_run("r_done")["status"] == run_events.RUN_STATUS_COMPLETED


async def test_run_detail_exposes_pending_approvals(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store)
    store.create_pending_approval({
        "id": "pa_1", "run_id": RUN_ID, "call_id": "call-1",
        "tool_name": "flash_firmware", "args_json": '{"port": "COM5"}',
        "risk_level": "high", "status": run_events.APPROVAL_STATUS_PENDING,
        "created_at": run_events.utc_now_iso(), "expires_at": None,
        "decided_at": None, "principal_id": None,
    })

    result = await runs.get_run(RUN_ID, user={})

    assert result["success"] is True
    assert result["run"]["id"] == RUN_ID
    assert result["run"]["status"] == run_events.RUN_STATUS_RUNNING
    assert result["run"]["pending_approvals"][0]["call_id"] == "call-1"
    assert result["run"]["pending_approvals"][0]["args"] == {"port": "COM5"}


# ---------------------------------------------------------------------------
# HITL resume validation and one-shot consumption
# ---------------------------------------------------------------------------

def test_require_resume_run_checks_session_status_and_call_id(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_AWAITING_CONFIRMATION)

    run = chat_routes._require_resume_run(
        store, RUN_ID, SESSION_ID, "allow", "call-1",
    )
    assert run["id"] == RUN_ID

    with pytest.raises(HTTPException) as exc_info:
        chat_routes._require_resume_run(store, RUN_ID, "other-session", "allow", "call-1")
    assert exc_info.value.detail["error"]["code"] == "RUN_SESSION_MISMATCH"

    with pytest.raises(HTTPException) as exc_info:
        chat_routes._require_resume_run(store, RUN_ID, SESSION_ID, "allow", None)
    assert exc_info.value.detail["error"]["code"] == "HITL_CONFIRMATION_STALE"

    store.update_run_status(RUN_ID, run_events.RUN_STATUS_COMPLETED)
    with pytest.raises(HTTPException) as exc_info:
        chat_routes._require_resume_run(store, RUN_ID, SESSION_ID, "allow", "call-1")
    assert exc_info.value.detail["error"]["code"] == "RUN_NOT_AWAITING_CONFIRMATION"


def test_pending_approval_is_consumed_once(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_AWAITING_CONFIRMATION)
    store.create_pending_approval({
        "id": "pa_1", "run_id": RUN_ID, "call_id": "call-1",
        "tool_name": "run_command", "args_json": "{}", "risk_level": "high",
        "status": run_events.APPROVAL_STATUS_PENDING,
        "created_at": run_events.utc_now_iso(), "expires_at": None,
        "decided_at": None, "principal_id": None,
    })

    consumed = chat_routes._consume_resume_approvals(store, RUN_ID, "allow", "call-1")
    assert [row["call_id"] for row in consumed] == ["call-1"]
    assert store.list_pending_approvals(RUN_ID)[0]["status"] == "allowed"

    with pytest.raises(HTTPException) as exc_info:
        chat_routes._consume_resume_approvals(store, RUN_ID, "allow", "call-1")
    assert exc_info.value.detail["error"]["code"] == "HITL_CONFIRMATION_STALE"


def test_expired_pending_approval_is_not_consumed(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_AWAITING_CONFIRMATION)
    store.create_pending_approval({
        "id": "pa_1", "run_id": RUN_ID, "call_id": "call-1",
        "tool_name": "run_command", "args_json": "{}", "risk_level": "high",
        "status": run_events.APPROVAL_STATUS_PENDING,
        "created_at": "2000-01-01T00:00:00+00:00",
        "expires_at": "2000-01-01T00:01:00+00:00",
        "decided_at": None, "principal_id": None,
    })

    with pytest.raises(HTTPException) as exc_info:
        chat_routes._consume_resume_approvals(store, RUN_ID, "allow", "call-1")
    assert exc_info.value.detail["error"]["code"] == "HITL_CONFIRMATION_STALE"
    assert store.list_pending_approvals(RUN_ID)[0]["status"] == "expired"


def test_resume_request_accepts_run_id():
    req = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="q")],
            session_id=SESSION_ID,
        ),
        decision="allow",
        call_id="call-1",
        run_id=RUN_ID,
    )
    assert req.run_id == RUN_ID


async def test_resume_route_rejects_unknown_run(isolated_run_runtime):
    req = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="q")],
            session_id=SESSION_ID,
        ),
        decision="allow",
        call_id="call-1",
        run_id="r_missing",
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.resume_agent(req, _request(path="/api/agent-sandbox/resume"), user={})
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "RUN_NOT_FOUND"


async def test_resume_route_rejects_foreign_session_run(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_AWAITING_CONFIRMATION)
    req = chat_routes.ResumeRequest(
        payload=chat_routes.ChatRequest(
            messages=[chat_routes.ChatMessageSchema(role="user", content="q")],
            session_id="another-session",
        ),
        decision="allow",
        call_id="call-1",
        run_id=RUN_ID,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.resume_agent(req, _request(path="/api/agent-sandbox/resume"), user={})
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUN_SESSION_MISMATCH"


async def test_run_resume_endpoint_is_unsupported_for_interrupted(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store, status=run_events.RUN_STATUS_INTERRUPTED)

    with pytest.raises(HTTPException) as exc_info:
        await runs.resume_run(RUN_ID, user={})
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RESUME_UNSUPPORTED"


async def test_run_resume_endpoint_rejects_running(isolated_run_runtime):
    store = isolated_run_runtime
    _seed_run(store)

    with pytest.raises(HTTPException) as exc_info:
        await runs.resume_run(RUN_ID, user={})
    assert exc_info.value.detail["error"]["code"] == "RUN_NOT_INTERRUPTED"
