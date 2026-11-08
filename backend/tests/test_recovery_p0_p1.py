"""P0/P1 recovery tests: server-owned runs and durable restart recovery.

These tests use a real SQLite ``RuntimeRunStore`` (never the developer DB) so
event ordering, one-shot approvals and status claims are exercised through the
same persistence the server uses. The agent stream is faked at the SSE
boundary; no browser or end-to-end path is involved.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api import chat_routes, runs
from app.api.sse import sse_event
from app.db import recovery_models  # noqa: F401 - registers recovery tables
from app.db.database import Base
from app.db.run_store import RunStore, RuntimeRunStore
from src.agent import run_events

RUN_ID = "r_p0p1_test"
SESSION_ID = "session-p0p1"
OWNER_USER = {"anonymous": False, "provider": "test-provider", "api_key": "owner-key"}
OTHER_USER = {"anonymous": False, "provider": "test-provider", "api_key": "other-key"}


@pytest.fixture()
def runtime_store(tmp_path):
    path = tmp_path / "p0p1.sqlite"
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(
        autocommit=False, autoflush=False, expire_on_commit=False, bind=engine,
    )
    store = RuntimeRunStore(RunStore(session_factory=factory))
    try:
        yield store
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def isolated_run_runtime(runtime_store):
    chat_routes._clear_pending_request_snapshot(SESSION_ID)
    run_events.set_run_store(runtime_store)
    run_events.reset_runtime_state()
    yield
    run_events.reset_runtime_state()
    run_events.set_run_store(None)
    chat_routes._clear_pending_request_snapshot(SESSION_ID)


def _seed_run(
    store: RuntimeRunStore,
    run_id: str = RUN_ID,
    *,
    status: str = run_events.RUN_STATUS_RUNNING,
    session_id: str = SESSION_ID,
    config: dict | None = None,
) -> None:
    store.create_run({
        "id": run_id,
        "session_id": session_id,
        "status": status,
        "model": "test-model",
        "permission_mode": "default",
        "config_json": json.dumps(config or {}),
        "thread_id": session_id,
        "checkpoint_id": None,
        "started_at": run_events.utc_now_iso(),
        "finished_at": None,
        "last_event_seq": 0,
        "error_code": None,
        "error_message": None,
    })


def _add_approval(
    store: RuntimeRunStore,
    run_id: str,
    call_id: str,
    *,
    tool_name: str = "write_file",
    expires_in_seconds: float | None = 3600,
) -> None:
    expires_at = (
        (run_events.utc_now() + datetime.timedelta(seconds=expires_in_seconds)).isoformat()
        if expires_in_seconds is not None else None
    )
    store.create_pending_approval({
        "id": f"pa_{run_id}_{call_id}",
        "run_id": run_id,
        "call_id": call_id,
        "tool_name": tool_name,
        "args_json": "{}",
        "risk_level": "high",
        "status": run_events.APPROVAL_STATUS_PENDING,
        "created_at": run_events.utc_now_iso(),
        "expires_at": expires_at,
        "decided_at": None,
        "principal_id": None,
    })


def _add_snapshot(store: RuntimeRunStore, run_id: str, payload: dict) -> None:
    store.create_request_snapshot({
        "id": f"rs_{run_id}",
        "run_id": run_id,
        "session_id": SESSION_ID,
        "payload_json": json.dumps(payload),
        "created_at": run_events.utc_now_iso(),
        "expires_at": (run_events.utc_now() + datetime.timedelta(hours=1)).isoformat(),
    })


def _current_epochs(session_id: str) -> dict[str, str | None]:
    from app.api.memory_epoch import read_memory_clear_epoch
    from app.api.session_epoch import read_session_delete_epoch

    return {
        "session_delete": read_session_delete_epoch(session_id),
        "memory_clear": read_memory_clear_epoch(),
    }


@pytest.fixture()
def bound_session():
    """Create a real owner-bound session in the process test database."""
    from app.api.session_owner import bind_new_session_owner
    from app.db.database import SessionLocal
    from app.db.models import Session as SessionModel, SessionIncarnation, SessionOwnerBinding

    session_id = f"owned-{uuid.uuid4().hex[:10]}"
    db = SessionLocal()
    try:
        session = SessionModel(id=session_id, title="owned")
        db.add(session)
        bind_new_session_owner(
            db, session, OWNER_USER, datetime.datetime.now(datetime.timezone.utc),
        )
        db.commit()
    finally:
        db.close()
    try:
        yield session_id
    finally:
        db = SessionLocal()
        try:
            binding = (
                db.query(SessionOwnerBinding)
                .filter(SessionOwnerBinding.session_id == session_id)
                .first()
            )
            incarnation_id = binding.incarnation_id if binding is not None else None
            db.query(SessionOwnerBinding).filter(
                SessionOwnerBinding.session_id == session_id
            ).delete()
            if incarnation_id is not None:
                db.query(SessionIncarnation).filter(
                    SessionIncarnation.id == incarnation_id
                ).delete()
            db.query(SessionModel).filter(SessionModel.id == session_id).delete()
            db.commit()
        finally:
            db.close()


def _request(headers: dict[str, str] | None = None, path: str = "/api/runs/x"):
    from starlette.requests import Request

    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
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


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


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
    monkeypatch.setattr(
        chat_routes, "_build_chat_history", lambda _messages: ([], "question"),
    )
    monkeypatch.setattr(
        chat_routes, "_build_system_prompt", lambda *_args, **_kwargs: "prompt",
    )
    monkeypatch.setattr(chat_routes, "make_client", lambda **_kwargs: object())
    monkeypatch.setattr(chat_routes, "_run_agent_stream", fake_stream)


def _chat_payload(session_id: str = SESSION_ID):
    return chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(role="user", content="question")],
        use_agent=True,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# P0 — server-owned run independent of the SSE response
# ---------------------------------------------------------------------------

async def test_run_continues_after_disconnect_and_replay_has_no_duplicates(
    monkeypatch, runtime_store,
):
    store = runtime_store
    release = asyncio.Event()
    calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield sse_event("text", {"content": "first"})
        await release.wait()
        yield sse_event("text", {"content": "second"})
        yield sse_event("done", {"success": True, "completed": True})

    _configure_chat_route(monkeypatch, fake_stream)
    response = await chat_routes.chat_sse(
        _chat_payload(), _request(path="/api/chat"), user={},
    )
    run_id = response.headers["X-Run-Id"]

    # Read until the first visible token, then drop the connection.
    iterator = response.body_iterator
    seen: list[dict] = []
    async for chunk in iterator:
        for raw in run_events.split_sse_frames(chunk):
            payload = run_events.decode_sse_frame(raw)
            if payload is not None:
                seen.append(payload)
        if any(event.get("content") == "first" for event in seen):
            break
    await iterator.aclose()

    # The server keeps executing after the client is gone.
    assert store.get_run(run_id)["status"] == run_events.RUN_STATUS_RUNNING
    release.set()
    await _wait_for(
        lambda: store.get_run(run_id)["status"] == run_events.RUN_STATUS_COMPLETED,
    )
    assert calls == 1

    replay = await runs.replay_run_events(run_id, _request(), after=None, user={})
    frames = await _read_frames(replay)
    seqs = [int(frame_id.rsplit(":", 1)[1]) for frame_id, _ in frames if frame_id]
    assert seqs == list(range(1, len(frames) + 1))
    assert [payload["type"] for _, payload in frames] == [
        "run_started", "text", "text", "done",
    ]
    assert [payload.get("content") for _, payload in frames if payload["type"] == "text"] == [
        "first", "second",
    ]

    # A second subscriber replays the same ledger; nothing executes again.
    replay_again = await runs.replay_run_events(run_id, _request(), after=None, user={})
    await _read_frames(replay_again)
    assert calls == 1


async def test_active_runs_endpoint_exposes_reconnect_contract(runtime_store):
    store = runtime_store
    _seed_run(store, "r_active")
    store.append_run_event("r_active", "text", {"type": "text", "content": "x"})
    store.append_run_event(
        "r_active", "tool_confirm_required",
        {
            "type": "tool_confirm_required",
            "calls": [{"name": "write_file", "args": {}, "call_id": "c1"}],
            "count": 1,
        },
    )
    _add_approval(store, "r_active", "c1")
    _seed_run(store, "r_done", status=run_events.RUN_STATUS_COMPLETED)
    _seed_run(store, "r_other", session_id="other-session")

    result = await runs.get_active_runs(SESSION_ID, user={})

    assert result["success"] is True
    assert [run["id"] for run in result["runs"]] == ["r_active"]
    active = result["active_run"]
    assert active["status"] == run_events.RUN_STATUS_RUNNING
    assert active["last_event_seq"] == 2
    assert active["pending_approvals"][0]["call_id"] == "c1"


async def test_ledger_preserves_tool_status_error_and_evidence(runtime_store):
    store = runtime_store
    _seed_run(store)

    evidence = {
        "id": "src1",
        "title": "BME280 datasheet",
        "doc_id": "doc-1",
        "page": 12,
        "excerpt": "I2C address 0x76",
        "artifact_ref": "artifact:table-3",
    }

    async def inner():
        yield sse_event("source", evidence)
        yield sse_event("tool_call", {
            "tool": "search_docs", "args": {"query": "i2c address"},
            "call_id": "call-1", "risk_level": "low",
            "decision_source": "auto_allow", "step_index": 1,
        })
        yield sse_event("tool_result", {
            "call_id": "call-1", "tool": "search_docs", "success": True,
            "result": {
                "success": True,
                "data": {"side_effect": "read_only", "locator": "kb://doc-1#p12"},
            },
            "status": "ok",
        })
        yield sse_event("context_compressing", {"message": "compacting"})
        yield sse_event("error", {
            "code": "RATE_LIMITED", "message": "slow down", "retryable": True,
        })
        yield sse_event("done", {"success": False, "completed": False})

    await _collect(run_events.stream_with_persistence(
        inner(), store=store, run_id=RUN_ID, session_id=SESSION_ID,
    ))

    events = store.list_run_events(RUN_ID)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    by_type = {event["type"]: event["payload"] for event in events}
    assert by_type["source"]["id"] == "src1"
    assert by_type["source"]["page"] == 12
    assert by_type["source"]["artifact_ref"] == "artifact:table-3"
    assert by_type["tool_call"]["call_id"] == "call-1"
    assert by_type["tool_call"]["risk_level"] == "low"
    assert by_type["tool_result"]["call_id"] == "call-1"
    assert by_type["tool_result"]["status"] == "ok"
    assert by_type["tool_result"]["result"]["data"]["side_effect"] == "read_only"
    assert by_type["tool_result"]["result"]["data"]["locator"] == "kb://doc-1#p12"
    assert by_type["context_compressing"]["message"] == "compacting"
    assert by_type["error"]["code"] == "RATE_LIMITED"
    summary = run_events.summarize_run_events(events)
    assert summary["open_tool_calls"] == []
    assert summary["has_terminal"] is True


async def test_success_done_is_downgraded_when_a_tool_result_is_missing(runtime_store):
    store = runtime_store
    _seed_run(store)

    async def inner():
        yield sse_event("tool_call", {
            "tool": "write_file", "args": {"path": "a.txt"},
            "call_id": "call-9", "risk_level": "high",
            "decision_source": "auto_allow",
        })
        yield sse_event("done", {"success": True, "completed": True})

    frames = await _collect(run_events.stream_with_persistence(
        inner(), store=store, run_id=RUN_ID,
    ))

    assert [payload["type"] for _, payload in frames] == [
        "run_started", "tool_call", "error", "done",
    ]
    events = store.list_run_events(RUN_ID)
    error_payload = events[-2]["payload"]
    done_payload = events[-1]["payload"]
    assert error_payload["code"] == "UNRESOLVED_SIDE_EFFECT"
    assert error_payload["unresolved_tool_calls"][0]["call_id"] == "call-9"
    assert done_payload["success"] is False
    assert done_payload["completed"] is False
    assert done_payload["unresolved_side_effects"][0]["tool"] == "write_file"
    run = store.get_run(RUN_ID)
    assert run["status"] == run_events.RUN_STATUS_FAILED
    assert run["error_code"] == "UNRESOLVED_SIDE_EFFECT"


async def test_success_guard_pairs_calls_across_resume_segments(runtime_store):
    store = runtime_store
    _seed_run(store)
    store.append_run_event(RUN_ID, "tool_call", {
        "type": "tool_call", "tool": "write_file",
        "call_id": "c-prev", "risk_level": "high",
    })

    async def inner_without_result():
        yield sse_event("text", {"content": "continuing"})
        yield sse_event("done", {"success": True, "completed": True})

    frames = await _collect(run_events.stream_with_persistence(
        inner_without_result(), store=store, run_id=RUN_ID, emit_started=False,
    ))
    assert [payload["type"] for _, payload in frames] == ["text", "error", "done"]
    done = store.list_run_events(RUN_ID)[-1]["payload"]
    assert done["success"] is False
    assert done["unresolved_side_effects"][0]["call_id"] == "c-prev"
    assert store.get_run(RUN_ID)["status"] == run_events.RUN_STATUS_FAILED

    # A close in the resumed segment clears the pairing and lets it succeed.
    _seed_run(store, "r_paired")
    store.append_run_event("r_paired", "tool_call", {
        "type": "tool_call", "tool": "write_file",
        "call_id": "c-prev", "risk_level": "high",
    })

    async def inner_with_result():
        yield sse_event("tool_result", {
            "call_id": "c-prev", "tool": "write_file",
            "success": True, "result": {"success": True},
        })
        yield sse_event("done", {"success": True, "completed": True})

    await _collect(run_events.stream_with_persistence(
        inner_with_result(), store=store, run_id="r_paired", emit_started=False,
    ))
    assert store.get_run("r_paired")["status"] == run_events.RUN_STATUS_COMPLETED


# ---------------------------------------------------------------------------
# P1 — restart recovery, controlled resume, approvals
# ---------------------------------------------------------------------------

async def test_startup_recovery_writes_terminal_events_once(runtime_store):
    store = runtime_store
    _seed_run(store, "r_running")
    store.append_run_event("r_running", "text", {"type": "text", "content": "half"})
    _seed_run(store, "r_awaiting", status=run_events.RUN_STATUS_AWAITING_CONFIRMATION)
    store.append_run_event(
        "r_awaiting", "done",
        {"type": "done", "success": True, "awaiting_confirmation": True},
    )
    _seed_run(store, "r_completed", status=run_events.RUN_STATUS_COMPLETED)
    _seed_run(store, "r_terminal_event")
    store.append_run_event(
        "r_terminal_event", "done",
        {"type": "done", "success": True, "completed": True},
    )
    _seed_run(store, "r_empty_ledger")
    _add_approval(store, "r_running", "c-expired", expires_in_seconds=-60)

    await runs.recover_interrupted_runs()

    running = store.get_run("r_running")
    assert running["status"] == run_events.RUN_STATUS_INTERRUPTED
    assert running["error_code"] == "PROCESS_RESTART"
    events = store.list_run_events("r_running")
    assert events[-2]["payload"]["code"] == "INTERRUPTED"
    assert events[-1]["payload"]["type"] == "done"
    assert events[-1]["payload"]["interrupted"] is True
    seq_after_first_pass = running["last_event_seq"]

    # A paused run keeps its persisted awaiting-confirmation done; a run whose
    # terminal event landed before the crash is repaired, not duplicated.
    assert store.get_run("r_awaiting")["status"] == run_events.RUN_STATUS_INTERRUPTED
    assert store.list_run_events("r_awaiting")[-1]["payload"]["awaiting_confirmation"] is True
    assert store.get_run("r_completed")["status"] == run_events.RUN_STATUS_COMPLETED
    assert store.get_run("r_terminal_event")["status"] == run_events.RUN_STATUS_COMPLETED
    assert len(store.list_run_events("r_terminal_event")) == 1
    assert store.get_run("r_empty_ledger")["status"] == run_events.RUN_STATUS_INTERRUPTED
    assert [event["type"] for event in store.list_run_events("r_empty_ledger")] == [
        "run_started", "error", "done",
    ]
    expired = store.list_pending_approvals(
        "r_running", status=run_events.APPROVAL_STATUS_EXPIRED,
    )
    assert [row["call_id"] for row in expired] == ["c-expired"]

    # Idempotent: the second pass appends nothing.
    await runs.recover_interrupted_runs()
    assert store.get_run("r_running")["last_event_seq"] == seq_after_first_pass


async def test_interrupted_replay_uses_persisted_terminal_once(runtime_store):
    store = runtime_store
    _seed_run(store, "r_running")
    store.append_run_event("r_running", "text", {"type": "text", "content": "half"})

    await runs.recover_interrupted_runs()

    response = await runs.replay_run_events("r_running", _request(), after=None, user={})
    frames = await _read_frames(response)
    payloads = [payload for _, payload in frames]
    assert [payload["type"] for payload in payloads] == ["text", "error", "done"]
    assert sum(1 for payload in payloads if payload["type"] == "done") == 1
    assert sum(1 for payload in payloads if payload["type"] == "error") == 1
    assert all(frame_id is not None for frame_id, _ in frames)


async def test_resume_classification_separates_safe_and_unknown(runtime_store):
    store = runtime_store
    _seed_run(store, "r_safe", status=run_events.RUN_STATUS_INTERRUPTED)
    store.append_run_event("r_safe", "tool_call", {
        "type": "tool_call", "tool": "write_file",
        "call_id": "c1", "risk_level": "high",
    })
    _add_approval(store, "r_safe", "c1")
    _add_snapshot(store, "r_safe", {"session_id": SESSION_ID, "permission_mode": "default"})
    info = runs.classify_run_resume(store, store.get_run("r_safe"))
    assert info["mode"] == "hitl_approval"
    assert info["pending_approval_count"] == 1
    assert info["request_snapshot_available"] is True

    _seed_run(store, "r_unknown", status=run_events.RUN_STATUS_INTERRUPTED)
    store.append_run_event("r_unknown", "tool_call", {
        "type": "tool_call", "tool": "flash_firmware",
        "call_id": "c9", "risk_level": "high",
    })
    info = runs.classify_run_resume(store, store.get_run("r_unknown"))
    assert info["mode"] == "needs_review"
    assert info["unresolved_tool_calls"][0]["call_id"] == "c9"

    _seed_run(store, "r_expired", status=run_events.RUN_STATUS_INTERRUPTED)
    store.append_run_event("r_expired", "tool_call", {
        "type": "tool_call", "tool": "write_file",
        "call_id": "c2", "risk_level": "high",
    })
    _add_approval(store, "r_expired", "c2", expires_in_seconds=-60)
    assert runs.classify_run_resume(store, store.get_run("r_expired"))["mode"] == "needs_review"

    _seed_run(store, "r_empty", status=run_events.RUN_STATUS_INTERRUPTED)
    assert runs.classify_run_resume(store, store.get_run("r_empty"))["mode"] == "needs_review"

    _seed_run(store, "r_running")
    assert runs.classify_run_resume(store, store.get_run("r_running"))["mode"] == "none"


async def test_run_detail_carries_resume_verdict(runtime_store):
    store = runtime_store
    _seed_run(store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED)
    store.append_run_event(RUN_ID, "tool_call", {
        "type": "tool_call", "tool": "write_file",
        "call_id": "c1", "risk_level": "high",
    })
    _add_approval(store, RUN_ID, "c1")
    _add_snapshot(store, RUN_ID, {"session_id": SESSION_ID})

    result = await runs.get_run(RUN_ID, user={})

    assert result["run"]["resume"]["mode"] == "hitl_approval"
    assert result["run"]["resume"]["resume_endpoint"] == "/api/agent-sandbox/resume"
    assert result["run"]["pending_approvals"][0]["call_id"] == "c1"


async def test_restart_resume_rejects_payload_that_does_not_match_snapshot(
    monkeypatch, runtime_store,
):
    store = runtime_store
    _seed_run(
        store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED,
        config={"principal_id": None, "long_term_memory": ""},
    )
    store.append_run_event(RUN_ID, "tool_call", {
        "type": "tool_call", "tool": "write_file",
        "call_id": "c1", "risk_level": "high",
    })
    _add_approval(store, RUN_ID, "c1")
    payload = _chat_payload()
    stored = chat_routes._sanitized_run_request(payload)
    stored["permission_mode"] = "plan"
    stored["rag_source_registry_snapshot"] = {"version": 1, "next_id": 1, "entries": []}
    _add_snapshot(store, RUN_ID, stored)
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)

    request = chat_routes.ResumeRequest(
        payload=payload, decision="allow", call_id="c1", run_id=RUN_ID,
    )
    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.resume_agent(
            request, _request(path="/api/agent-sandbox/resume"), user={},
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RESUME_SNAPSHOT_UNAVAILABLE"


async def test_restart_resume_refuses_unknown_side_effect_even_with_approval(
    monkeypatch, runtime_store,
):
    store = runtime_store
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    _seed_run(store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED)
    store.append_run_event(RUN_ID, "tool_call", {
        "type": "tool_call", "tool": "write_file",
        "call_id": "c1", "risk_level": "high",
    })
    store.append_run_event(RUN_ID, "tool_call", {
        "type": "tool_call", "tool": "flash_firmware",
        "call_id": "c9", "risk_level": "high",
    })
    _add_approval(store, RUN_ID, "c1")

    request = chat_routes.ResumeRequest(
        payload=_chat_payload(), decision="allow", call_id="c1", run_id=RUN_ID,
    )
    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.resume_agent(
            request, _request(path="/api/agent-sandbox/resume"), user={},
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RESUME_NEEDS_REVIEW"
    assert exc_info.value.detail["resume"]["mode"] == "needs_review"


async def test_resume_run_route_requires_decision_then_delegates(
    monkeypatch, runtime_store,
):
    store = runtime_store
    _seed_run(
        store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED,
        config={"principal_id": None, "long_term_memory": ""},
    )
    store.append_run_event(RUN_ID, "tool_call", {
        "type": "tool_call", "tool": "write_file",
        "call_id": "c1", "risk_level": "high",
    })
    _add_approval(store, RUN_ID, "c1")
    payload = _chat_payload()
    stored = chat_routes._sanitized_run_request(payload)
    stored["permission_mode"] = "plan"  # force the rebuild to fail closed
    stored["rag_source_registry_snapshot"] = {"version": 1, "next_id": 1, "entries": []}
    _add_snapshot(store, RUN_ID, stored)
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)

    body = runs.RunResumeRequest(
        payload=payload.model_dump(), decision="allow", call_id="c1",
    )
    with pytest.raises(HTTPException) as exc_info:
        await runs.resume_run(RUN_ID, _request(path="/api/runs/resume"), body=body, user={})
    assert exc_info.value.detail["error"]["code"] == "RESUME_SNAPSHOT_UNAVAILABLE"

    # Without a body the route reports what is required instead of replaying.
    with pytest.raises(HTTPException) as exc_info:
        await runs.resume_run(RUN_ID, _request(), body=None, user={})
    assert exc_info.value.detail["error"]["code"] == "RESUME_REQUIRES_DECISION"
    assert exc_info.value.detail["resume"]["mode"] == "hitl_approval"


def test_resume_guard_is_one_shot(runtime_store):
    control = run_events.get_run_control()

    assert control.begin_resume(RUN_ID) is True
    assert control.begin_resume(RUN_ID) is False
    assert control.is_resuming(RUN_ID) is True
    control.end_resume(RUN_ID)
    assert control.is_resuming(RUN_ID) is False
    assert control.begin_resume(RUN_ID) is True
    control.end_resume(RUN_ID)


def test_store_status_claim_has_a_single_concurrent_winner(runtime_store):
    store = runtime_store
    _seed_run(store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED)

    def claim(_):
        return store.claim_run_status(
            RUN_ID,
            expected_status=run_events.RUN_STATUS_INTERRUPTED,
            status=run_events.RUN_STATUS_RUNNING,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(4)))

    assert sum(1 for result in results if result is not None) == 1
    run = store.get_run(RUN_ID)
    assert run["status"] == run_events.RUN_STATUS_RUNNING
    assert run["error_code"] is None


async def test_cancel_appends_one_terminal_and_second_cancel_conflicts(runtime_store):
    store = runtime_store
    _seed_run(store)

    started = asyncio.Event()

    async def slow_stream():
        started.set()
        try:
            while True:
                await asyncio.sleep(0.05)
                yield sse_event("text", {"content": "."})
        finally:
            pass

    run_events.start_run_task(
        slow_stream(), store=store, run_id=RUN_ID, emit_started=True,
    )
    await asyncio.wait_for(started.wait(), timeout=2.0)
    await _wait_for(lambda: store.get_run(RUN_ID)["last_event_seq"] >= 1)
    await _wait_for(lambda: run_events.get_run_control().has_live_task(RUN_ID))

    result = await runs.cancel_run(RUN_ID, user={})
    assert result["cancelled"] is True
    await _wait_for(
        lambda: store.get_run(RUN_ID)["status"] == run_events.RUN_STATUS_CANCELLED,
    )
    events = store.list_run_events(RUN_ID)
    assert sum(1 for event in events if event["type"] == "done") == 1
    assert events[-1]["payload"]["cancelled"] is True

    with pytest.raises(HTTPException) as exc_info:
        await runs.cancel_run(RUN_ID, user={})
    assert exc_info.value.detail["error"]["code"] == "RUN_NOT_ACTIVE"


async def test_restore_request_snapshot_from_run_round_trip(monkeypatch, runtime_store):
    store = runtime_store
    _seed_run(
        store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED,
        config={"principal_id": "principal-1", "long_term_memory": "memory-1"},
    )
    store.append_run_event(RUN_ID, "tool_call", {
        "type": "tool_call", "tool": "search_docs",
        "call_id": "c1", "risk_level": "low",
    })
    _add_approval(store, RUN_ID, "c1", tool_name="search_docs")
    payload = _chat_payload()
    stored = chat_routes._sanitized_run_request(payload)
    from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry

    registry = RagSourceRegistry()
    registry.allocate_ids(1, context=SimpleNamespace(source_counter=0))
    stored["rag_source_registry_snapshot"] = registry.to_snapshot()
    stored["_recovery_epochs"] = _current_epochs(payload.session_id)
    _add_snapshot(store, RUN_ID, stored)
    monkeypatch.setattr(chat_routes, "_verified_principal_id", lambda _user: "principal-1")
    monkeypatch.setattr(chat_routes, "compaction_principal", lambda _user: "hash-1")
    monkeypatch.setattr(chat_routes, "_ensure_skills_runtime", lambda snapshot: snapshot)

    def fake_tool_context(snapshot, _payload, _session_id):
        return chat_routes.replace(
            snapshot,
            tool_context=SimpleNamespace(
                run_id=snapshot.run_id, source_counter=0, rag_source_registry=None,
            ),
        )

    monkeypatch.setattr(chat_routes, "_ensure_request_tool_context", fake_tool_context)

    restored = chat_routes._restore_request_snapshot_from_run(
        store, store.get_run(RUN_ID), payload, user={"anonymous": False},
    )

    try:
        assert restored is not None
        assert restored.pending_call_ids == ("c1",)
        assert restored.run_id == RUN_ID
        assert restored.long_term_memory == "memory-1"
        assert restored.tool_context.source_counter == 1
        registry = restored.tool_context.rag_source_registry
        assert registry.next_id == 2
    finally:
        chat_routes._clear_pending_request_snapshot(payload.session_id)


async def test_restore_request_snapshot_refuses_missing_registry(
    monkeypatch, runtime_store,
):
    store = runtime_store
    _seed_run(
        store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED,
        config={"principal_id": "principal-1", "long_term_memory": ""},
    )
    _add_approval(store, RUN_ID, "c1", tool_name="search_docs")
    payload = _chat_payload()
    stored = chat_routes._sanitized_run_request(payload)  # no registry snapshot
    stored["_recovery_epochs"] = _current_epochs(payload.session_id)
    _add_snapshot(store, RUN_ID, stored)
    monkeypatch.setattr(chat_routes, "_verified_principal_id", lambda _user: "principal-1")
    monkeypatch.setattr(chat_routes, "compaction_principal", lambda _user: "hash-1")
    monkeypatch.setattr(chat_routes, "_ensure_skills_runtime", lambda snapshot: snapshot)
    monkeypatch.setattr(
        chat_routes, "_ensure_request_tool_context",
        lambda snapshot, _payload, _session: chat_routes.replace(
            snapshot, tool_context=SimpleNamespace(
                run_id=snapshot.run_id, source_counter=0, rag_source_registry=None,
            ),
        ),
    )

    assert chat_routes._restore_request_snapshot_from_run(
        store, store.get_run(RUN_ID), payload, user={"anonymous": False},
    ) is None


async def test_restore_request_snapshot_refuses_stale_epochs(
    monkeypatch, runtime_store,
):
    store = runtime_store
    _seed_run(
        store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED,
        config={"principal_id": "principal-1", "long_term_memory": ""},
    )
    _add_approval(store, RUN_ID, "c1", tool_name="search_docs")
    payload = _chat_payload()
    stored = chat_routes._sanitized_run_request(payload)
    from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry

    registry = RagSourceRegistry()
    registry.allocate_ids(1, context=SimpleNamespace(source_counter=0))
    stored["rag_source_registry_snapshot"] = registry.to_snapshot()
    stored["_recovery_epochs"] = {
        "session_delete": "stale-session-epoch",
        "memory_clear": "stale-memory-epoch",
    }
    _add_snapshot(store, RUN_ID, stored)
    monkeypatch.setattr(chat_routes, "_verified_principal_id", lambda _user: "principal-1")
    monkeypatch.setattr(chat_routes, "compaction_principal", lambda _user: "hash-1")
    monkeypatch.setattr(chat_routes, "_ensure_skills_runtime", lambda snapshot: snapshot)
    monkeypatch.setattr(
        chat_routes, "_ensure_request_tool_context",
        lambda snapshot, _payload, _session: chat_routes.replace(
            snapshot, tool_context=SimpleNamespace(
                run_id=snapshot.run_id, source_counter=0, rag_source_registry=None,
            ),
        ),
    )

    assert chat_routes._restore_request_snapshot_from_run(
        store, store.get_run(RUN_ID), payload, user={"anonymous": False},
    ) is None


# ---------------------------------------------------------------------------
# Session ownership on run endpoints
# ---------------------------------------------------------------------------

async def test_active_runs_requires_session_owner_when_bound(
    runtime_store, bound_session,
):
    store = runtime_store
    _seed_run(store, "r_owned_active", session_id=bound_session)
    store.append_run_event("r_owned_active", "text", {"type": "text", "content": "x"})

    owner_result = await runs.get_active_runs(bound_session, user=OWNER_USER)
    assert [run["id"] for run in owner_result["runs"]] == ["r_owned_active"]

    with pytest.raises(HTTPException) as exc_info:
        await runs.get_active_runs(bound_session, user=OTHER_USER)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "FORBIDDEN"


async def test_run_endpoints_reject_cross_user_when_session_bound(
    runtime_store, bound_session,
):
    store = runtime_store
    _seed_run(store, RUN_ID, session_id=bound_session)
    store.append_run_event(RUN_ID, "text", {"type": "text", "content": "private"})

    with pytest.raises(HTTPException) as exc_info:
        await runs.get_run(RUN_ID, user=OTHER_USER)
    assert exc_info.value.status_code == 403
    assert "private" not in json.dumps(exc_info.value.detail)

    with pytest.raises(HTTPException) as exc_info:
        await runs.replay_run_events(RUN_ID, _request(), after=None, user=OTHER_USER)
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        await runs.cancel_run(RUN_ID, user=OTHER_USER)
    assert exc_info.value.status_code == 403
    assert store.get_run(RUN_ID)["status"] == run_events.RUN_STATUS_RUNNING

    with pytest.raises(HTTPException) as exc_info:
        await runs.resume_run(RUN_ID, _request(), body=None, user=OTHER_USER)
    assert exc_info.value.status_code == 403

    owner_result = await runs.get_run(RUN_ID, user=OWNER_USER)
    assert owner_result["run"]["id"] == RUN_ID


async def test_run_endpoints_keep_legacy_unbound_sessions_open(runtime_store):
    store = runtime_store
    _seed_run(
        store, RUN_ID, status=run_events.RUN_STATUS_COMPLETED,
        session_id="legacy-unbound-session",
    )
    store.append_run_event(RUN_ID, "text", {"type": "text", "content": "legacy"})
    store.append_run_event(
        RUN_ID, "done", {"type": "done", "success": True, "completed": True},
    )
    _seed_run(store, "r_legacy_active", session_id="legacy-unbound-session")

    result = await runs.get_run(RUN_ID, user=OTHER_USER)
    assert result["run"]["id"] == RUN_ID

    replay = await runs.replay_run_events(RUN_ID, _request(), after=None, user=OTHER_USER)
    frames = await _read_frames(replay)
    assert [payload["type"] for _, payload in frames] == ["text", "done"]

    active = await runs.get_active_runs("legacy-unbound-session", user=OTHER_USER)
    assert [run["id"] for run in active["runs"]] == ["r_legacy_active"]


async def test_run_endpoint_missing_run_stays_404_for_any_user(runtime_store):
    with pytest.raises(HTTPException) as exc_info:
        await runs.get_run("r_missing_owner", user=OTHER_USER)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "RUN_NOT_FOUND"


async def test_agent_sandbox_resume_enforces_run_session_owner(
    monkeypatch, runtime_store, bound_session,
):
    store = runtime_store
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    _seed_run(
        store, RUN_ID, status=run_events.RUN_STATUS_INTERRUPTED,
        session_id=bound_session,
    )
    request = chat_routes.ResumeRequest(
        payload=_chat_payload(bound_session), decision="deny", run_id=RUN_ID,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.resume_agent(
            request, _request(path="/api/agent-sandbox/resume"), user=OTHER_USER,
        )
    assert exc_info.value.status_code == 403

    # The owner passes the ownership guard and reaches the resume verdict.
    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.resume_agent(
            request, _request(path="/api/agent-sandbox/resume"), user=OWNER_USER,
        )
    assert exc_info.value.detail["error"]["code"] == "RESUME_NEEDS_REVIEW"
