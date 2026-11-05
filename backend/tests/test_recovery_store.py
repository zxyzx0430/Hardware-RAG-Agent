"""Recovery R1 storage tests.

Every test uses an isolated SQLite file under pytest's tmp_path; the user's
``backend/data`` database is never opened. The four recovery tables are
registered on the shared ``Base`` by importing ``app.db.recovery_models``.
"""

import datetime
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import recovery_models  # noqa: F401 - registers recovery tables
from app.db.database import Base
from app.db.recovery_models import PendingApproval, RequestSnapshot
from app.db.run_store import (
    RunStore,
    RuntimeRunStore,
    get_run_store,
    sanitize_payload,
)

UTC = datetime.timezone.utc


@pytest.fixture()
def recovery_db(tmp_path):
    path = tmp_path / "recovery-r1.sqlite"
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(
        autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
    )
    try:
        yield RunStore(session_factory=factory), factory
    finally:
        engine.dispose()


@pytest.fixture()
def runtime_store(recovery_db):
    typed_store, factory = recovery_db
    return RuntimeRunStore(typed_store), factory


def test_recovery_tables_are_registered():
    assert {
        "runs",
        "run_events",
        "pending_approvals",
        "request_snapshots",
    } <= set(Base.metadata.tables)


def test_append_event_assigns_monotonic_seq_starting_at_one(recovery_db):
    store, _ = recovery_db
    run = store.create_run("session-1", run_id="r_1")
    assert run.last_event_seq == 0
    assert run.started_at.tzinfo is not None

    events = [store.append_event("r_1", "token", {"i": i}) for i in range(3)]

    assert [event.seq for event in events] == [1, 2, 3]
    assert [event.created_at.tzinfo is not None for event in events] == [True] * 3
    assert store.get_run("r_1").last_event_seq == 3
    assert [event.seq for event in store.list_events("r_1")] == [1, 2, 3]


def test_repeated_append_keeps_both_events(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_1")

    first = store.append_event("r_1", "run_started", {"a": 1})
    second = store.append_event("r_1", "run_started", {"a": 1})

    assert (first.seq, second.seq) == (1, 2)
    events = store.list_events("r_1")
    assert [event.seq for event in events] == [1, 2]
    assert [event.payload for event in events] == [{"a": 1}, {"a": 1}]


def test_list_events_after_filter_and_limit(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_1")
    for index in range(5):
        store.append_event("r_1", "token", {"i": index})

    assert [event.seq for event in store.list_events("r_1", after=2)] == [3, 4, 5]
    assert [event.seq for event in store.list_events("r_1", after=5)] == []
    assert [event.seq for event in store.list_events("r_1", after=0, limit=2)] == [1, 2]
    with pytest.raises(ValueError):
        store.list_events("r_1", after=-1)


def test_append_event_unknown_run_raises_and_writes_nothing(recovery_db):
    store, _ = recovery_db

    with pytest.raises(LookupError):
        store.append_event("r_missing", "token", {})

    assert store.list_events("r_missing") == []


def test_concurrent_appends_are_unique_and_contiguous(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_concurrent")

    def worker(worker_id):
        return [
            store.append_event("r_concurrent", "token", {"w": worker_id}).seq
            for _ in range(5)
        ]

    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(worker, range(4)))

    seqs = sorted(seq for batch in batches for seq in batch)
    assert seqs == list(range(1, 21))
    assert store.get_run("r_concurrent").last_event_seq == 20
    assert [event.seq for event in store.list_events("r_concurrent")] == seqs


def test_mark_running_interrupted_keeps_pending_approvals(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_running", status="running")
    store.create_run(
        "session-1", run_id="r_waiting", status="awaiting_confirmation"
    )
    store.create_run("session-1", run_id="r_done", status="completed")
    approval = store.create_pending_approval(
        "r_waiting", "call-1", "write_file", {"path": "a.txt"}
    )

    changed = store.mark_running_interrupted()

    assert changed == 2
    interrupted = store.get_run("r_running")
    assert interrupted.status == "interrupted"
    assert interrupted.error_code == "PROCESS_RESTART"
    assert store.get_run("r_waiting").status == "interrupted"
    assert store.get_run("r_done").status == "completed"
    kept = store.get_pending_approval(approval.id)
    assert kept is not None and kept.status == "pending"


def test_pending_approval_is_consumed_once(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_1")
    approval = store.create_pending_approval(
        "r_1", "call-1", "write_file", {"path": "a.txt"}, risk_level="high"
    )

    decided = store.decide_pending_approval(
        approval.id, "allowed", principal_id="user-1"
    )

    assert decided.status == "allowed"
    assert decided.decided_at is not None
    assert decided.principal_id == "user-1"
    assert store.decide_pending_approval(approval.id, "denied") is None
    assert store.get_pending_approval(approval.id).status == "allowed"
    assert store.get_pending_approval_by_call("r_1", "call-1", status="pending") is None


def test_decide_pending_approval_by_call_consumes_pending(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_1")
    store.create_pending_approval("r_1", "call-1", "run_command", {"cmd": "dir"})

    decided = store.decide_pending_approval_by_call("r_1", "call-1", "denied")

    assert decided.status == "denied"
    assert store.decide_pending_approval_by_call("r_1", "call-1", "allowed") is None
    assert store.list_pending_approvals("r_1") == []


def test_concurrent_decisions_consume_the_approval_once(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_1")
    approval = store.create_pending_approval("r_1", "call-1", "write_file", {})
    decisions = ["allowed", "denied", "stopped", "allowed"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda status: store.decide_pending_approval(approval.id, status),
                decisions,
            )
        )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    final = store.get_pending_approval(approval.id)
    assert final.status in {"allowed", "denied", "stopped"}


def test_expire_pending_approvals_only_touches_overdue_rows(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_1")
    now = datetime.datetime.now(UTC)
    store.create_pending_approval(
        "r_1",
        "call-old",
        "write_file",
        {},
        approval_id="pa_old",
        expires_at=now - datetime.timedelta(minutes=5),
    )
    store.create_pending_approval(
        "r_1",
        "call-new",
        "write_file",
        {},
        approval_id="pa_new",
        expires_at=now + datetime.timedelta(minutes=5),
    )

    assert store.expire_pending_approvals() == 1
    assert store.get_pending_approval("pa_old").status == "expired"
    assert store.get_pending_approval("pa_new").status == "pending"
    assert store.decide_pending_approval("pa_old", "allowed") is None
    assert store.get_pending_approval("pa_old").status == "expired"


def test_decide_rejects_overdue_approval_before_sweep(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_1")
    store.create_pending_approval(
        "r_1",
        "call-old",
        "write_file",
        {},
        approval_id="pa_overdue",
        expires_at=datetime.datetime.now(UTC) - datetime.timedelta(seconds=1),
    )

    assert store.decide_pending_approval("pa_overdue", "allowed") is None
    assert store.get_pending_approval("pa_overdue").status == "expired"


def test_request_snapshot_drops_secret_fields_before_storage(recovery_db):
    store, factory = recovery_db
    store.create_run("session-1", run_id="r_1")
    snapshot = store.save_request_snapshot(
        "r_1",
        "session-1",
        {
            "model": "qwen",
            "max_tokens": 512,
            "token_limit": 4096,
            "messages": [{"role": "user", "content": "hello"}],
            "api_key": "sk-live-SHOULD-NOT-APPEAR",
            "nested": {
                "accessToken": "tok-SHOULD-NOT-APPEAR",
                "Authorization": "Bearer SHOULD-NOT-APPEAR",
                "keep": 1,
            },
            "headers": {"X-Api-Key": "hdr-SHOULD-NOT-APPEAR", "X-Trace": "t1"},
            "tools": [{"name": "safe", "api_key": "list-SHOULD-NOT-APPEAR"}],
        },
    )

    assert snapshot.payload == {
        "model": "qwen",
        "max_tokens": 512,
        "token_limit": 4096,
        "messages": [{"role": "user", "content": "hello"}],
        "nested": {"keep": 1},
        "headers": {"X-Trace": "t1"},
        "tools": [{"name": "safe"}],
    }
    with factory() as session:
        stored = session.get(RequestSnapshot, snapshot.id)
        assert "SHOULD-NOT-APPEAR" not in stored.payload_json
    assert store.get_request_snapshot(snapshot.id).payload == snapshot.payload
    assert store.get_request_snapshot(run_id="r_1").id == snapshot.id

    assert store.delete_request_snapshot(snapshot.id) == 1
    assert store.get_request_snapshot(snapshot.id) is None


def test_create_run_sanitizes_config(recovery_db):
    store, _ = recovery_db
    run = store.create_run(
        "session-1",
        run_id="r_1",
        model="qwen",
        permission_mode="default",
        config={"temperature": 0.2, "api_key": "sk-x", "headers": {"api-key": "sk-y"}},
    )

    assert run.model == "qwen"
    assert run.permission_mode == "default"
    assert run.config == {"temperature": 0.2, "headers": {}}


def test_update_run_status_sets_finished_at_for_terminal_status(recovery_db):
    store, _ = recovery_db
    store.create_run("session-1", run_id="r_1")

    running = store.update_run_status("r_1", "running")
    assert running.finished_at is None

    failed = store.update_run_status(
        "r_1", "failed", error_code="MODEL_ERROR", error_message="boom"
    )
    assert failed.status == "failed"
    assert failed.finished_at is not None
    assert failed.error_code == "MODEL_ERROR"
    assert failed.error_message == "boom"

    assert store.update_run_status("r_missing", "failed") is None
    with pytest.raises(ValueError):
        store.update_run_status("r_1", "bogus")


def test_sanitize_payload_keeps_ordinary_token_parameters():
    sanitized = sanitize_payload(
        {"max_tokens": 8, "token_limit": 100, "tokens": "abc", "idempotency_key": "k"}
    )
    assert sanitized == {"max_tokens": 8, "token_limit": 100}


def test_pending_approval_rows_are_reconstructable_after_reopen(recovery_db):
    """A fresh store over the same file sees the persisted pending approval."""
    store, factory = recovery_db
    store.create_run("session-1", run_id="r_1")
    store.create_pending_approval(
        "r_1", "call-1", "write_file", {"path": "b.txt"}, principal_id="user-1"
    )

    reopened = RunStore(session_factory=factory)
    pending = reopened.list_pending_approvals("r_1")

    assert len(pending) == 1
    assert pending[0].call_id == "call-1"
    assert pending[0].args == {"path": "b.txt"}
    with factory() as session:
        row = session.get(PendingApproval, pending[0].id)
        assert row.status == "pending"


# ---------------------------------------------------------------------------
# Runtime adapter (dict rows, ISO timestamps) used by src.agent.run_events
# ---------------------------------------------------------------------------


def test_get_run_store_returns_a_singleton():
    first = get_run_store()
    assert isinstance(first, RuntimeRunStore)
    assert first is get_run_store()


def _runtime_run(run_id="r_runtime", **overrides):
    run = {
        "id": run_id,
        "session_id": "session-1",
        "status": "running",
        "model": "qwen",
        "permission_mode": "default",
        "config_json": json.dumps({"temperature": 0.1, "api_key": "sk-x"}),
        "thread_id": "thread-1",
        "checkpoint_id": None,
        "started_at": "2026-11-05T10:00:00+00:00",
        "finished_at": None,
        "last_event_seq": 0,
        "error_code": None,
        "error_message": None,
    }
    run.update(overrides)
    return run


def _runtime_approval(call_id="call-1", approval_id="pa_1", **overrides):
    now = datetime.datetime.now(UTC)
    approval = {
        "id": approval_id,
        "run_id": "r_runtime",
        "call_id": call_id,
        "tool_name": "write_file",
        "args_json": json.dumps({"path": "a.txt"}),
        "risk_level": "high",
        "status": "pending",
        "created_at": (now - datetime.timedelta(minutes=1)).isoformat(),
        "expires_at": (now + datetime.timedelta(minutes=30)).isoformat(),
        "decided_at": None,
        "principal_id": "user-1",
    }
    approval.update(overrides)
    return approval


def test_runtime_store_round_trips_dict_rows(runtime_store):
    store, _ = runtime_store
    run = store.create_run(_runtime_run())

    assert set(run) == {
        "id",
        "session_id",
        "status",
        "model",
        "permission_mode",
        "config_json",
        "thread_id",
        "checkpoint_id",
        "started_at",
        "finished_at",
        "last_event_seq",
        "error_code",
        "error_message",
    }
    assert run["started_at"] == "2026-11-05T10:00:00+00:00"
    assert json.loads(run["config_json"]) == {"temperature": 0.1}

    assert store.append_run_event("r_runtime", "run_started", {"type": "run_started"}) == 1
    assert store.append_run_event("r_runtime", "token", {"text": "hi"}) == 2
    assert store.append_run_event("r_runtime", "done", {"type": "done"}) == 3

    events = store.list_run_events("r_runtime", after_seq=1)
    assert [event["seq"] for event in events] == [2, 3]
    assert events[0]["payload"] == {"text": "hi"}
    assert isinstance(events[0]["created_at"], str)

    assert store.update_run_status("r_runtime", "completed") is True
    finished = store.get_run("r_runtime")
    assert finished["status"] == "completed"
    assert finished["finished_at"] is not None
    assert store.update_run_status("r_missing", "completed") is False


def test_runtime_mark_interrupted_runs_stamps_finished_at(runtime_store):
    store, _ = runtime_store
    store.create_run(
        _runtime_run("r_waiting", status="awaiting_confirmation", config_json=None)
    )

    changed = store.mark_interrupted_runs(error_message="service restarted")

    assert changed == 1
    run = store.get_run("r_waiting")
    assert run["status"] == "interrupted"
    assert run["error_code"] == "PROCESS_RESTART"
    assert run["error_message"] == "service restarted"
    assert run["finished_at"] is not None


def test_runtime_pending_approval_is_idempotent_while_pending(runtime_store):
    store, _ = runtime_store
    store.create_run(_runtime_run())

    first = store.create_pending_approval(_runtime_approval())
    second = store.create_pending_approval(
        _runtime_approval(approval_id="pa_2", tool_name="write_file")
    )

    assert first["id"] == second["id"] == "pa_1"
    assert len(store.list_pending_approvals("r_runtime", status="pending")) == 1

    consumed = store.consume_pending_approvals(
        "r_runtime", call_id="call-1", decision="allow"
    )
    assert [row["status"] for row in consumed] == ["allowed"]
    assert store.consume_pending_approvals(
        "r_runtime", call_id="call-1", decision="allow"
    ) == []

    third = store.create_pending_approval(
        _runtime_approval(approval_id="pa_3", call_id="call-1")
    )
    assert third["id"] == "pa_3"


def test_runtime_consume_without_call_id_stops_all_pending(runtime_store):
    store, _ = runtime_store
    store.create_run(_runtime_run())
    store.create_pending_approval(_runtime_approval("call-1", "pa_1"))
    store.create_pending_approval(_runtime_approval("call-2", "pa_2"))

    consumed = store.consume_pending_approvals(
        "r_runtime", call_id=None, decision="stop"
    )

    assert {row["call_id"] for row in consumed} == {"call-1", "call-2"}
    assert {row["status"] for row in consumed} == {"stopped"}
    assert store.list_pending_approvals("r_runtime", status="pending") == []


def test_runtime_overdue_approval_is_marked_expired_not_consumed(runtime_store):
    store, _ = runtime_store
    store.create_run(_runtime_run())
    past = datetime.datetime.now(UTC) - datetime.timedelta(minutes=5)
    store.create_pending_approval(
        _runtime_approval(expires_at=past.isoformat())
    )

    assert store.consume_pending_approvals(
        "r_runtime", call_id="call-1", decision="allow"
    ) == []
    expired = store.list_pending_approvals("r_runtime", status="expired")
    assert [row["id"] for row in expired] == ["pa_1"]
    assert expired[0]["decided_at"] is not None


def test_runtime_concurrent_consumes_only_one_wins(runtime_store):
    store, _ = runtime_store
    store.create_run(_runtime_run())
    store.create_pending_approval(_runtime_approval())
    decisions = ["allow", "deny", "allow", "deny"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda decision: store.consume_pending_approvals(
                    "r_runtime", call_id="call-1", decision=decision
                ),
                decisions,
            )
        )

    assert len([result for result in results if result]) == 1
    final = store.list_pending_approvals("r_runtime", status="pending")
    assert final == []


def test_runtime_snapshot_is_sanitized_before_storage(runtime_store):
    store, factory = runtime_store
    store.create_run(_runtime_run())

    snapshot = store.create_request_snapshot({
        "id": "rs_1",
        "run_id": "r_runtime",
        "session_id": "session-1",
        "payload_json": json.dumps({
            "model": "qwen",
            "max_tokens": 16,
            "tool_keys": {"search": "sk-tool"},
            "api_key": "sk-live-SHOULD-NOT-APPEAR",
            "messages": [{"role": "user", "content": "hi"}],
        }),
        "created_at": "2026-11-05T10:00:06+00:00",
        "expires_at": None,
    })

    assert snapshot["payload_json"] is not None
    assert "SHOULD-NOT-APPEAR" not in snapshot["payload_json"]
    assert "tool_keys" not in snapshot["payload_json"]
    latest = store.get_latest_request_snapshot("r_runtime")
    assert latest["id"] == "rs_1"
    assert json.loads(latest["payload_json"]) == {
        "model": "qwen",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
    }
    with factory() as session:
        row = session.get(RequestSnapshot, "rs_1")
        assert "SHOULD-NOT-APPEAR" not in row.payload_json
