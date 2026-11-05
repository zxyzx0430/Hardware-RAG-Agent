"""Recovery R1 storage tests.

Every test uses an isolated SQLite file under pytest's tmp_path; the user's
``backend/data`` database is never opened. The four recovery tables are
registered on the shared ``Base`` by importing ``app.db.recovery_models``.
"""

import datetime
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import recovery_models  # noqa: F401 - registers recovery tables
from app.db.database import Base
from app.db.recovery_models import PendingApproval, RequestSnapshot
from app.db.run_store import RunStore, sanitize_payload

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
