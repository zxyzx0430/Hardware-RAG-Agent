"""Tool-level idempotency and unknown-result protection tests.

Each test uses a real SQLite ``RuntimeRunStore`` (never the developer DB) so
the ``tool_invocations`` ledger is exercised through the same persistence the
server uses. Tool implementations are recording fakes; no model, browser or
real hardware is involved.
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import json
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, PrivateAttr
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api import runs
from app.db import recovery_models  # noqa: F401 - registers recovery tables
from app.db.database import Base
from app.db.run_store import RunStore, RuntimeRunStore
from src.agent import run_events, tool_idempotency
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

RUN_ID = "r_idem_test"
SESSION_ID = "session-idem"
OWNER_USER = {"anonymous": False, "provider": "test-provider", "api_key": "owner-key"}
OTHER_USER = {"anonymous": False, "provider": "test-provider", "api_key": "other-key"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runtime_store(tmp_path):
    path = tmp_path / "idempotency.sqlite"
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
    run_events.set_run_store(runtime_store)
    run_events.reset_runtime_state()
    yield
    run_events.reset_runtime_state()
    run_events.set_run_store(None)


def _seed_run(
    store: RuntimeRunStore,
    *,
    run_id: str = RUN_ID,
    session_id: str = SESSION_ID,
    status: str = run_events.RUN_STATUS_RUNNING,
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


def _ctx(**overrides) -> ToolContext:
    values = {
        "permission_mode": "bypassPermissions",
        "session_id": SESSION_ID,
        "run_id": RUN_ID,
    }
    values.update(overrides)
    return ToolContext(**values)


def _begin_started(
    store: RuntimeRunStore,
    call_id: str,
    tool_name: str,
    args: dict,
    side_effect: str,
    *,
    target: str | None = None,
    version: str | None = None,
):
    return store.begin_tool_invocation({
        "run_id": RUN_ID,
        "call_id": call_id,
        "tool_name": tool_name,
        "args_hash": tool_idempotency.args_hash(args),
        "side_effect": side_effect,
        "target": target,
        "status": tool_idempotency.STATUS_STARTED,
        "attempts": 1,
        "version": version,
        "started_at": run_events.utc_now_iso(),
    })


def _row(store: RuntimeRunStore, call_id: str):
    return store.get_tool_invocation(RUN_ID, call_id)


def _mark_unknown(store: RuntimeRunStore, call_id: str) -> None:
    store.mark_tool_invocation_unknown(
        RUN_ID,
        call_id,
        error_code=run_events.TOOL_RESULT_UNKNOWN_CODE,
        error_message="simulated process restart",
    )


class _Audit:
    def __init__(self) -> None:
        self.records = []

    def record(self, record) -> None:
        self.records.append(record)


class _McpGrant:
    def dispatch_decision(self, _call_id, _spec, _args):
        return True, "user_allow"


# ---------------------------------------------------------------------------
# Recording tool fakes
# ---------------------------------------------------------------------------


class _Args(BaseModel):
    path: str = ""
    content: str = ""
    command: str = ""
    port: str = ""
    value: str = ""
    api_key: str = ""


class _RecordingTool(ToolSpec):
    description: str = "recording test tool"
    args_schema: type = _Args
    risk_level: RiskLevel = RiskLevel.LOW
    max_retries: int = 0
    _calls: list[dict] = PrivateAttr(default_factory=list)
    _behavior: object = PrivateAttr(default=None)

    def __init__(self, behavior=None):
        super().__init__()
        self._behavior = behavior

    async def execute(self, args: dict, ctx: ToolContext) -> dict:
        self._calls.append(dict(args))
        behavior = self._behavior
        if behavior is None:
            return {"output": "ok", "value": args.get("value")}
        result = behavior(args, ctx)
        if inspect.isawaitable(result):
            result = await result
        return result


class _ReadTool(_RecordingTool):
    name: str = "read_file"


class _WriteTool(_RecordingTool):
    name: str = "write_file"


class _CommandTool(_RecordingTool):
    name: str = "run_command"


class _FlashTool(_RecordingTool):
    name: str = "flash_firmware"


class _UnknownTool(_RecordingTool):
    name: str = "mystery_tool"


class _McpTool(_RecordingTool):
    name: str = "mcp_echo"
    mcp_info: dict | None = {"server_name": "srv", "tool_name": "echo"}


def _write_behavior(args, _ctx):
    path = Path(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.get("content", ""), encoding="utf-8")
    return {"output": f"wrote {path}", "path": str(path), "success": True}


def _sandbox_file() -> Path:
    root = Path(tempfile.gettempdir()) / "agent-sandbox"
    return root / f"idem-{uuid.uuid4().hex}" / "note.txt"


async def _dispatch(router: ToolRouter, spec: ToolSpec, call_id: str, args: dict,
                    ctx: ToolContext | None = None) -> dict:
    return await router.dispatch(
        call_id, spec.name, dict(args), ctx or _ctx(), tool_spec=spec,
    )


# ---------------------------------------------------------------------------
# Duplicate dispatch
# ---------------------------------------------------------------------------


async def test_duplicate_call_id_executes_once_and_replays_success(runtime_store):
    _seed_run(runtime_store)
    spec = _ReadTool()
    router = ToolRouter(_Audit())

    first = await _dispatch(router, spec, "c1", {"path": "a.txt"})
    second = await _dispatch(router, spec, "c1", {"path": "a.txt"})

    assert len(spec._calls) == 1
    assert first["success"] is True
    assert second["success"] is True
    assert second["metadata"]["idempotent_replay"] is True
    assert second["output"] == first["output"]
    assert second["data"] == first["data"]
    row = _row(runtime_store, "c1")
    assert row["status"] == tool_idempotency.STATUS_SUCCEEDED
    assert isinstance(row["result_hash"], str) and len(row["result_hash"]) == 64
    assert row["side_effect"] == tool_idempotency.SIDE_EFFECT_READ_ONLY


async def test_duplicate_call_id_with_different_args_conflicts(runtime_store):
    _seed_run(runtime_store)
    spec = _ReadTool()
    router = ToolRouter(_Audit())

    await _dispatch(router, spec, "c1", {"path": "a.txt"})
    conflict = await _dispatch(router, spec, "c1", {"path": "b.txt"})

    assert len(spec._calls) == 1
    assert conflict["success"] is False
    assert conflict["error"]["error_type"] == tool_idempotency.IDEMPOTENCY_CONFLICT_CODE
    assert _row(runtime_store, "c1")["status"] == tool_idempotency.STATUS_SUCCEEDED


async def test_same_call_id_for_a_different_tool_conflicts(runtime_store):
    _seed_run(runtime_store)
    _begin_started(
        runtime_store, "c-cross", "read_file", {"path": "a.txt"},
        tool_idempotency.SIDE_EFFECT_READ_ONLY,
    )
    spec = _CommandTool()
    router = ToolRouter(_Audit())

    result = await _dispatch(router, spec, "c-cross", {"path": "a.txt"})

    assert len(spec._calls) == 0
    assert result["success"] is False
    assert result["error"]["error_type"] == tool_idempotency.IDEMPOTENCY_CONFLICT_CODE


async def test_failed_result_is_replayed_not_reexecuted(runtime_store):
    _seed_run(runtime_store)

    def boom(_args, _ctx):
        raise RuntimeError("synthetic failure")

    spec = _CommandTool(boom)
    router = ToolRouter(_Audit())

    first = await _dispatch(router, spec, "c1", {"command": "false"})
    second = await _dispatch(router, spec, "c1", {"command": "false"})

    assert len(spec._calls) == 1
    assert first["success"] is False
    assert second["success"] is False
    assert second["metadata"]["idempotent_replay"] is True
    assert second["error"]["error_type"] == first["error"]["error_type"]
    assert _row(runtime_store, "c1")["status"] == tool_idempotency.STATUS_FAILED


# ---------------------------------------------------------------------------
# Unknown outcomes
# ---------------------------------------------------------------------------


async def test_started_command_becomes_unknown_and_is_not_redispatched(runtime_store):
    _seed_run(runtime_store, status=run_events.RUN_STATUS_INTERRUPTED)
    args = {"command": "pio run -t upload"}
    _begin_started(
        runtime_store, "c-cmd", "run_command", args,
        tool_idempotency.SIDE_EFFECT_COMMAND,
    )
    _mark_unknown(runtime_store, "c-cmd")
    spec = _CommandTool()
    router = ToolRouter(_Audit())

    result = await _dispatch(router, spec, "c-cmd", args)

    assert len(spec._calls) == 0
    assert result["success"] is False
    assert result["error"]["error_type"] == tool_idempotency.TOOL_RESULT_UNKNOWN_CODE
    row = _row(runtime_store, "c-cmd")
    assert row["status"] == tool_idempotency.STATUS_UNKNOWN

    info = runs.classify_run_resume(runtime_store, runtime_store.get_run(RUN_ID))
    assert info["mode"] == "needs_review"
    assert info["unknown_side_effect_calls"][0]["call_id"] == "c-cmd"
    assert info["unknown_side_effect_calls"][0]["side_effect"] == "command"


async def test_started_read_only_is_safely_replayed(runtime_store):
    _seed_run(runtime_store)
    args = {"path": "a.txt"}
    _begin_started(
        runtime_store, "c-read", "read_file", args,
        tool_idempotency.SIDE_EFFECT_READ_ONLY,
    )
    spec = _ReadTool()
    router = ToolRouter(_Audit())

    result = await _dispatch(router, spec, "c-read", args)

    assert len(spec._calls) == 1
    assert result["success"] is True
    row = _row(runtime_store, "c-read")
    assert row["status"] == tool_idempotency.STATUS_SUCCEEDED
    assert row["attempts"] == 2


async def test_flash_unknown_not_retried_and_port_recorded(runtime_store):
    _seed_run(runtime_store)
    args = {"port": "COM5", "value": "firmware.bin"}
    _begin_started(
        runtime_store, "c-flash", "flash_firmware", args,
        tool_idempotency.SIDE_EFFECT_HARDWARE,
        target="port=COM5",
    )
    _mark_unknown(runtime_store, "c-flash")
    spec = _FlashTool()
    router = ToolRouter(_Audit())

    result = await _dispatch(router, spec, "c-flash", args)

    assert len(spec._calls) == 0
    assert result["error"]["error_type"] == tool_idempotency.TOOL_RESULT_UNKNOWN_CODE
    row = _row(runtime_store, "c-flash")
    assert row["status"] == tool_idempotency.STATUS_UNKNOWN
    assert row["side_effect"] == "hardware"
    assert "COM5" in row["target"]


async def test_mcp_unknown_not_retried_and_target_has_no_args(runtime_store):
    _seed_run(runtime_store)
    args = {"value": "secret-argument"}
    _begin_started(
        runtime_store, "c-mcp", "mcp_echo", args,
        tool_idempotency.SIDE_EFFECT_MCP,
        target="srv/echo",
    )
    _mark_unknown(runtime_store, "c-mcp")
    spec = _McpTool()
    router = ToolRouter(_Audit())

    result = await _dispatch(
        router, spec, "c-mcp", args, _ctx(mcp_authorization=_McpGrant()),
    )

    assert len(spec._calls) == 0
    assert result["error"]["error_type"] == tool_idempotency.TOOL_RESULT_UNKNOWN_CODE
    row = _row(runtime_store, "c-mcp")
    assert row["status"] == tool_idempotency.STATUS_UNKNOWN
    assert row["target"] == "srv/echo"
    assert "secret-argument" not in json.dumps(row, default=str)


async def test_side_effect_tool_does_not_retry_on_timeout(runtime_store):
    _seed_run(runtime_store)

    def timeout(_args, _ctx):
        raise asyncio.TimeoutError()

    command = _CommandTool(timeout)
    command.max_retries = 3
    read = _ReadTool(timeout)
    read.max_retries = 3
    router = ToolRouter(_Audit())

    command_result = await _dispatch(router, command, "c-timeout", {"command": "slow"})
    read_result = await _dispatch(router, read, "c-read-retry", {"path": "x"})

    assert len(command._calls) == 1
    assert command_result["error"]["error_type"] == "TIMEOUT"
    assert len(read._calls) == 4  # initial attempt + max_retries
    assert read_result["error"]["error_type"] == "TIMEOUT"


# ---------------------------------------------------------------------------
# Local write versions
# ---------------------------------------------------------------------------


async def test_local_write_version_conflict_blocks_replay(runtime_store):
    _seed_run(runtime_store)
    path = _sandbox_file()
    args = {"path": str(path), "content": "one"}
    spec = _WriteTool(_write_behavior)
    router = ToolRouter(_Audit())

    first = await _dispatch(router, spec, "c-write", args)
    assert first["success"] is True
    assert path.read_text(encoding="utf-8") == "one"

    path.write_text("two", encoding="utf-8")
    replay = await _dispatch(router, spec, "c-write", args)

    assert len(spec._calls) == 1
    assert replay["success"] is False
    assert (
        replay["error"]["error_type"]
        == tool_idempotency.IDEMPOTENCY_VERSION_CONFLICT_CODE
    )
    assert path.read_text(encoding="utf-8") == "two"
    row = _row(runtime_store, "c-write")
    assert row["status"] == tool_idempotency.STATUS_SUCCEEDED
    assert row["version"] is not None


async def test_local_write_unknown_with_unchanged_version_can_retry(runtime_store):
    _seed_run(runtime_store)
    path = _sandbox_file()
    args = {"path": str(path), "content": "hello"}
    version = tool_idempotency.initial_version(args)
    _begin_started(
        runtime_store, "c-write-retry", "write_file", args,
        tool_idempotency.SIDE_EFFECT_LOCAL_WRITE,
        target=str(path),
        version=version,
    )
    _mark_unknown(runtime_store, "c-write-retry")
    spec = _WriteTool(_write_behavior)
    router = ToolRouter(_Audit())

    result = await _dispatch(router, spec, "c-write-retry", args)

    assert len(spec._calls) == 1
    assert result["success"] is True
    assert path.read_text(encoding="utf-8") == "hello"
    row = _row(runtime_store, "c-write-retry")
    assert row["status"] == tool_idempotency.STATUS_SUCCEEDED
    assert row["attempts"] == 2


async def test_local_write_unknown_with_changed_file_blocks_retry(runtime_store):
    _seed_run(runtime_store)
    path = _sandbox_file()
    args = {"path": str(path), "content": "from-agent"}
    version = tool_idempotency.initial_version(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("already-changed", encoding="utf-8")
    _begin_started(
        runtime_store, "c-write-blocked", "write_file", args,
        tool_idempotency.SIDE_EFFECT_LOCAL_WRITE,
        target=str(path),
        version=version,
    )
    _mark_unknown(runtime_store, "c-write-blocked")
    spec = _WriteTool(_write_behavior)
    router = ToolRouter(_Audit())

    result = await _dispatch(router, spec, "c-write-blocked", args)

    assert len(spec._calls) == 0
    assert result["error"]["error_type"] == tool_idempotency.TOOL_RESULT_UNKNOWN_CODE
    assert path.read_text(encoding="utf-8") == "already-changed"
    assert _row(runtime_store, "c-write-blocked")["status"] == "unknown"


async def test_live_started_local_write_returns_in_progress(runtime_store):
    _seed_run(runtime_store)
    path = _sandbox_file()
    args = {"path": str(path), "content": "live"}
    spec = _WriteTool(_write_behavior)
    target = tool_idempotency.extract_target(
        spec, args, tool_idempotency.SIDE_EFFECT_LOCAL_WRITE,
    )
    _begin_started(
        runtime_store, "c-live", "write_file", args,
        tool_idempotency.SIDE_EFFECT_LOCAL_WRITE,
        target=target,
        version=tool_idempotency.initial_version(args),
    )
    router = ToolRouter(_Audit())

    result = await _dispatch(router, spec, "c-live", args)

    assert len(spec._calls) == 0
    assert result["success"] is False
    assert result["error"]["error_type"] == tool_idempotency.TOOL_IN_PROGRESS_CODE
    assert _row(runtime_store, "c-live")["status"] == tool_idempotency.STATUS_STARTED


# ---------------------------------------------------------------------------
# Parallelism and resource conflicts
# ---------------------------------------------------------------------------


async def test_different_call_ids_run_in_parallel_and_each_record(runtime_store):
    _seed_run(runtime_store)
    entered: list[str] = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def behavior(args, _ctx):
        entered.append(args.get("value", ""))
        if len(entered) == 2:
            both_started.set()
        await release.wait()
        return {"output": "ok", "value": args.get("value", "")}

    spec = _ReadTool(behavior)
    router = ToolRouter(_Audit())

    first = asyncio.create_task(
        _dispatch(router, spec, "par-1", {"value": "one"})
    )
    second = asyncio.create_task(
        _dispatch(router, spec, "par-2", {"value": "two"})
    )
    await asyncio.wait_for(both_started.wait(), timeout=5)
    # Both calls are inside execute before either returns: no global ledger
    # lock or batch transaction serialized them.
    assert sorted(entered) == ["one", "two"]
    release.set()
    results = await asyncio.gather(first, second)

    assert all(result["success"] is True for result in results)
    assert _row(runtime_store, "par-1")["status"] == "succeeded"
    assert _row(runtime_store, "par-2")["status"] == "succeeded"


async def test_concurrent_writes_to_the_same_file_conflict(runtime_store):
    _seed_run(runtime_store)
    path = _sandbox_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def behavior(args, _ctx):
        started.set()
        await release.wait()
        target = Path(args["path"])
        target.write_text(args["content"], encoding="utf-8")
        return {"output": "wrote", "path": str(target), "success": True}

    spec = _WriteTool(behavior)
    router = ToolRouter(_Audit())
    args = {"path": str(path), "content": "first"}

    first = asyncio.create_task(_dispatch(router, spec, "wr-1", args))
    await asyncio.wait_for(started.wait(), timeout=5)
    second = await _dispatch(
        router, spec, "wr-2", {"path": str(path), "content": "second"},
    )

    assert len(spec._calls) == 1
    assert second["success"] is False
    assert (
        second["error"]["error_type"]
        == tool_idempotency.IDEMPOTENCY_TARGET_CONFLICT_CODE
    )
    assert _row(runtime_store, "wr-2") is None

    release.set()
    first_result = await first
    assert first_result["success"] is True
    assert path.read_text(encoding="utf-8") == "first"


async def test_different_hardware_ports_run_in_parallel(runtime_store):
    _seed_run(runtime_store)
    entered: list[str] = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def behavior(args, _ctx):
        entered.append(args["port"])
        if len(entered) == 2:
            both_started.set()
        await release.wait()
        return {"output": "ok", "port": args["port"]}

    spec = _FlashTool(behavior)
    router = ToolRouter(_Audit())

    first = asyncio.create_task(
        _dispatch(router, spec, "fl-1", {"port": "COM5"})
    )
    second = asyncio.create_task(
        _dispatch(router, spec, "fl-2", {"port": "COM6"})
    )
    await asyncio.wait_for(both_started.wait(), timeout=5)
    assert sorted(entered) == ["COM5", "COM6"]
    release.set()
    results = await asyncio.gather(first, second)

    assert all(result["success"] is True for result in results)
    assert _row(runtime_store, "fl-1")["status"] == "succeeded"
    assert _row(runtime_store, "fl-2")["status"] == "succeeded"
    assert _row(runtime_store, "fl-1")["target"] != _row(runtime_store, "fl-2")["target"]


# ---------------------------------------------------------------------------
# Crash and restart recovery
# ---------------------------------------------------------------------------


async def test_cancelled_command_leaves_started_then_recovery_marks_unknown(
    runtime_store,
):
    _seed_run(runtime_store)

    async def cancelled(_args, _ctx):
        raise asyncio.CancelledError()

    spec = _CommandTool(cancelled)
    router = ToolRouter(_Audit())
    args = {"command": "flash-tool --port COM5"}

    with pytest.raises(asyncio.CancelledError):
        await _dispatch(router, spec, "c-cancel", args)

    row = _row(runtime_store, "c-cancel")
    assert row["status"] == tool_idempotency.STATUS_STARTED
    assert row["side_effect"] == "command"

    await runs.recover_interrupted_runs()

    row = _row(runtime_store, "c-cancel")
    assert row["status"] == tool_idempotency.STATUS_UNKNOWN
    assert row["error_code"] == run_events.TOOL_RESULT_UNKNOWN_CODE
    info = runs.classify_run_resume(runtime_store, runtime_store.get_run(RUN_ID))
    assert info["mode"] == "needs_review"

    blocked = await _dispatch(router, spec, "c-cancel", args)
    assert len(spec._calls) == 1  # only the cancelled attempt
    assert blocked["error"]["error_type"] == tool_idempotency.TOOL_RESULT_UNKNOWN_CODE


async def test_success_guard_sees_unknown_invocation(runtime_store):
    _seed_run(runtime_store, status=run_events.RUN_STATUS_INTERRUPTED)
    _begin_started(
        runtime_store, "c-guard", "run_command", {"command": "build"},
        tool_idempotency.SIDE_EFFECT_COMMAND,
    )
    pump = run_events.RunEventPump(
        _EmptyStream(), store=runtime_store, run_id=RUN_ID, emit_started=False,
    )

    guarded, error = pump._guard_success_terminal({"success": True})

    assert guarded["success"] is False
    assert guarded["unresolved_side_effects"][0]["call_id"] == "c-guard"
    assert error["code"] == run_events.UNRESOLVED_SIDE_EFFECT_CODE


class _EmptyStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


# ---------------------------------------------------------------------------
# Sensitive data and owner/resume contract
# ---------------------------------------------------------------------------


async def test_sensitive_args_and_results_are_not_persisted(runtime_store):
    _seed_run(runtime_store)
    secret = "SUPERSECRET-TOKEN-VALUE"
    api_key = "sk-abcdefgh12345678"

    def echo_secret(_args, _ctx):
        return {"output": f"token={secret}", "value": "done"}

    spec = _CommandTool(echo_secret)
    router = ToolRouter(_Audit())
    args = {
        "command": f"curl --token={secret} https://example.invalid",
        "api_key": api_key,
    }

    result = await _dispatch(router, spec, "c-secret", args)

    assert result["success"] is True
    row = _row(runtime_store, "c-secret")
    persisted = json.dumps(row, default=str)
    assert secret not in persisted
    assert api_key not in persisted
    assert "***REDACTED***" in row["target"]
    assert row["result_json"] is not None
    assert secret not in row["result_json"]


@pytest.fixture()
def bound_session():
    from app.api.session_owner import bind_new_session_owner
    from app.db.database import SessionLocal
    from app.db.models import (
        Session as SessionModel,
        SessionIncarnation,
        SessionOwnerBinding,
    )

    session_id = f"owned-idem-{uuid.uuid4().hex[:10]}"
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


async def test_owner_guard_and_classify_unknown_side_effect(
    runtime_store, bound_session,
):
    _seed_run(
        runtime_store,
        session_id=bound_session,
        status=run_events.RUN_STATUS_INTERRUPTED,
    )
    _begin_started(
        runtime_store, "c-owner", "run_command", {"command": "build"},
        tool_idempotency.SIDE_EFFECT_COMMAND,
    )
    run = runtime_store.get_run(RUN_ID)

    runs.authorize_run_session(run, OWNER_USER)
    with pytest.raises(HTTPException) as exc_info:
        runs.authorize_run_session(run, OTHER_USER)
    assert exc_info.value.status_code == 403

    info = runs.classify_run_resume(runtime_store, run)
    assert info["mode"] == "needs_review"
    assert info["unknown_side_effect_calls"][0]["call_id"] == "c-owner"


# ---------------------------------------------------------------------------
# Schema upgrade and index scope
# ---------------------------------------------------------------------------


def test_started_target_index_is_added_and_scoped_to_resource_side_effects(
    tmp_path, monkeypatch,
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.db import database

    path = tmp_path / "legacy.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE tool_invocations ("
            "run_id TEXT NOT NULL, call_id TEXT NOT NULL, target TEXT, "
            "status TEXT NOT NULL, side_effect TEXT NOT NULL, "
            "PRIMARY KEY (run_id, call_id))"
        ))
    monkeypatch.setattr(database, "engine", engine)

    database._ensure_tool_invocation_started_target_index()
    database._ensure_tool_invocation_started_target_index()

    with engine.connect() as conn:
        sql = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_tool_invocations_started_target'"
        )).scalar_one()
    assert "UNIQUE INDEX" in sql

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tool_invocations "
            "(run_id, call_id, target, status, side_effect) "
            "VALUES ('r', 'c1', 'file.txt', 'started', 'local_write')"
        ))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO tool_invocations "
                "(run_id, call_id, target, status, side_effect) "
                "VALUES ('r', 'c2', 'file.txt', 'started', 'local_write')"
            ))
    # Commands keep the same target string but are not resource-locked.
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tool_invocations "
            "(run_id, call_id, target, status, side_effect) "
            "VALUES ('r', 'c3', 'file.txt', 'started', 'command')"
        ))
    # A finished write no longer holds the target.
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tool_invocations "
            "(run_id, call_id, target, status, side_effect) "
            "VALUES ('r', 'c4', 'other.txt', 'started', 'local_write')"
        ))
        conn.execute(text(
            "INSERT INTO tool_invocations "
            "(run_id, call_id, target, status, side_effect) "
            "VALUES ('r', 'c5', 'other.txt', 'succeeded', 'local_write')"
        ))
    engine.dispose()
