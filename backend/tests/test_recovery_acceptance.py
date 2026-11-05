"""Recovery R1 acceptance tests.

These tests are deliberately written against the frozen contract in
``E:/DevCaches/Temp/recovery-r1-contract.md`` (tables, HTTP surface and SSE
framing), not against a particular implementation module layout.

When a storage/runtime dependency is not merged yet the affected test is
*skipped* with the missing artefact named in the reason.  Skips are not passes:
the acceptance runner reports them separately.

Safety: the module only runs against an isolated SQLite database.  Running the
file on its own creates a temporary database automatically.  Inside a larger
suite (where ``app.db.database`` was already imported) the tests are skipped
unless the caller explicitly opts in with ``RECOVERY_ACCEPTANCE_ISOLATED=1``.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TEMP_BASES = (Path("E:/DevCaches/Temp"), Path("D:/DevCaches/Temp"))


def _temp_root() -> Path:
    for base in _TEMP_BASES:
        if base.is_dir():
            return base
    return Path(tempfile.gettempdir())


def _isolate_database() -> bool:
    if os.environ.get("RECOVERY_ACCEPTANCE_ISOLATED") == "1":
        return True
    # A previous test module already bound app.db.database to a shared DB.
    # Never redirect (or touch) it implicitly.
    if "app.db.database" in sys.modules:
        return False
    root = Path(tempfile.mkdtemp(prefix="recovery-r1-acceptance-", dir=str(_temp_root())))
    os.environ["SQLITE_DB_PATH"] = str(root / "recovery-acceptance.sqlite")
    os.environ["RECOVERY_ACCEPTANCE_ISOLATED"] = "1"
    os.environ.setdefault("AGENT_CHECKPOINTER_TYPE", "memory")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ["TEMP"] = str(root)
    os.environ["TMP"] = str(root)
    return True


_ISOLATED = _isolate_database()

pytestmark = pytest.mark.skipif(
    not _ISOLATED,
    reason=(
        "recovery acceptance needs an isolated database: run this file on its own "
        "or set RECOVERY_ACCEPTANCE_ISOLATED=1 with a temporary SQLITE_DB_PATH"
    ),
)

CONTRACT_TABLES = ("runs", "run_events", "pending_approvals", "request_snapshots")

READ_ONLY_TOOLS = (
    "search_docs",
    "list_kb_docs",
    "read_file",
    "grep",
    "glob",
    "list_files",
)

SIDE_EFFECT_TOOLS = (
    "write_file",
    "edit_file",
    "run_command",
    "build_firmware",
    "flash_firmware",
)

_RECOVERY_MODULES = (
    "app.db.run_store",
    "app.db.recovery_models",
    "app.api.run_events",
    "app.api.runs",
    "app.api.runs_routes",
    "src.agent.recovery",
)


# ---------------------------------------------------------------------------
# SQLite helpers (contract tables only)
# ---------------------------------------------------------------------------


def _utc_text(delta_seconds: int = 0) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def _db_path() -> Path:
    from app.db import database

    return Path(database.DB_PATH)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def _missing_contract_tables() -> list[str]:
    with _connect() as conn:
        return [name for name in CONTRACT_TABLES if name not in _table_names(conn)]


def _require_contract_tables() -> None:
    # The acceptance runner points SQLITE_DB_PATH at a brand-new file; create the
    # schema before deciding the dependency is absent.
    try:
        from app.db.database import init_db

        init_db()
    except Exception as exc:
        pytest.skip(
            "storage dependency missing: init_db() could not run: "
            f"{type(exc).__name__}: {exc}"
        )
    missing = _missing_contract_tables()
    if missing:
        pytest.skip(
            "storage dependency missing: contract tables "
            f"{missing} were not created by app startup; "
            "expected recovery models in app/db + init_db()"
        )


def _new_run_id() -> str:
    return f"r_acc_{uuid.uuid4().hex}"


def _seed_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    *,
    session_id: str | None = None,
    last_event_seq: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO runs (
            id, session_id, status, model, permission_mode, config_json,
            thread_id, checkpoint_id, started_at, finished_at,
            last_event_seq, error_code, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            session_id or f"acc-session-{uuid.uuid4().hex}",
            status,
            "acceptance-model",
            "default",
            "{}",
            None,
            None,
            _utc_text(),
            None,
            last_event_seq,
            None,
            None,
        ),
    )


def _seed_event(
    conn: sqlite3.Connection,
    run_id: str,
    seq: int,
    event_type: str,
    payload: dict,
) -> None:
    conn.execute(
        "INSERT INTO run_events (run_id, seq, type, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, seq, event_type, json.dumps(payload, ensure_ascii=False), _utc_text()),
    )


def _seed_pending_approval(
    conn: sqlite3.Connection,
    run_id: str,
    call_id: str,
    tool_name: str = "write_file",
) -> str:
    approval_id = f"pa_acc_{uuid.uuid4().hex}"
    conn.execute(
        """
        INSERT INTO pending_approvals (
            id, run_id, call_id, tool_name, args_json, risk_level, status,
            created_at, expires_at, decided_at, principal_id
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, NULL, ?)
        """,
        (
            approval_id,
            run_id,
            call_id,
            tool_name,
            json.dumps({"path": "acceptance.txt"}, ensure_ascii=False),
            "medium",
            _utc_text(),
            _utc_text(3600),
            "acceptance-principal",
        ),
    )
    return approval_id


def _cleanup_runs(run_ids: list[str]) -> None:
    if not run_ids:
        return
    try:
        with _connect() as conn:
            placeholders = ",".join("?" for _ in run_ids)
            for table in ("run_events", "pending_approvals", "request_snapshots"):
                conn.execute(f"DELETE FROM {table} WHERE run_id IN ({placeholders})", run_ids)
            conn.execute(f"DELETE FROM runs WHERE id IN ({placeholders})", run_ids)
            conn.commit()
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------------------
# App / HTTP helpers
# ---------------------------------------------------------------------------


def _create_app():
    from app.main import create_app

    return create_app()


@contextmanager
def _started_app():
    """Start a fresh app instance (runs startup recovery) and yield a client."""

    from fastapi.testclient import TestClient

    with TestClient(_create_app()) as client:
        yield client


def _trigger_restart_recovery() -> list[str]:
    """Run startup recovery the way a backend restart would."""

    attempts: list[str] = []
    with _started_app():
        attempts.append("create_app/TestClient startup")
    attempts.extend(_call_optional_recovery_hooks())
    return attempts


def _call_optional_recovery_hooks() -> list[str]:
    attempts: list[str] = []
    hook_names = (
        "mark_interrupted_runs",
        "recover_interrupted_runs",
        "mark_running_runs_interrupted",
        "recover_on_startup",
        "startup_recovery",
        "recover_pending_runs",
    )
    for module_name in _RECOVERY_MODULES:
        module = _try_import(module_name)
        if module is None:
            continue
        for hook_name in hook_names:
            hook = getattr(module, hook_name, None)
            if not callable(hook):
                continue
            try:
                result = hook()
                if inspect.isawaitable(result):
                    asyncio.run(result)
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - surfaced in assertion text
                attempts.append(f"{module_name}.{hook_name} raised {type(exc).__name__}: {exc}")
                continue
            attempts.append(f"{module_name}.{hook_name}")
    return attempts


def _try_import(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _is_default_not_found(response) -> bool:
    if response.status_code != 404:
        return False
    try:
        return response.json().get("detail") == "Not Found"
    except Exception:
        return False


def _require_run_status_route(client) -> None:
    response = client.get("/api/runs/__acceptance_probe__")
    if _is_default_not_found(response):
        pytest.skip(
            "runtime dependency missing: GET /api/runs/{run_id} is not registered "
            "(expected the runs router from the runtime thread)"
        )


def _require_replay_route(client) -> None:
    response = client.get("/api/runs/__acceptance_probe__/events")
    if _is_default_not_found(response):
        pytest.skip(
            "runtime dependency missing: GET /api/runs/{run_id}/events is not registered "
            "(expected replay support from the runtime thread)"
        )


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    normalized = text.replace("\r\n", "\n")
    for block in normalized.split("\n\n"):
        if not block.strip():
            continue
        event_id = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("id:"):
                event_id = line[len("id:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        payload = None
        if data_lines:
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                payload = {"_raw": "\n".join(data_lines)}
        events.append({"id": event_id, "data": payload, "raw": block})
    return events


def _event_id_seq(event_id: str | None) -> tuple[str | None, int | None]:
    if not event_id or ":" not in event_id:
        return None, None
    run_id, seq_text = event_id.rsplit(":", 1)
    try:
        return run_id, int(seq_text)
    except ValueError:
        return None, None


def _run_chat(client, *, secret: str | None = None, tool_secret: str | None = None):
    from src.llm.client import StreamChunk

    async def fake_stream(*_args, **_kwargs):
        yield StreamChunk(type="thinking", content="acceptance thinking")
        yield StreamChunk(type="text", content="acceptance answer")

    body = {
        "messages": [{"role": "user", "content": "recovery acceptance"}],
        "top_k": 0,
        "use_agent": False,
    }
    if tool_secret is not None:
        body["tool_keys"] = {"tavily": tool_secret}
    headers = {"X-API-Key": secret} if secret else {}

    with patch("app.api.common.LLMClient") as mock_llm:
        mock_llm.return_value.chat_stream = fake_stream
        return client.post("/api/chat", json=body, headers=headers)


def _assert_chat_ok(response) -> None:
    assert response.status_code == 200, (
        f"/api/chat returned {response.status_code}: {response.text[:2000]}"
    )
    assert response.headers.get("content-type", "").startswith("text/event-stream"), (
        response.headers.get("content-type")
    )


def _run_record(run_id: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def _run_event_rows(run_id: str) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT seq, type, payload_json FROM run_events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()


def _extract_status(payload: dict) -> str | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("status"), str):
            return payload["status"]
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("status"), str):
            return data["status"]
    return None


def _extract_error_code(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("code"), str):
        return payload["code"]
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    return None


# ---------------------------------------------------------------------------
# 1. Persisted stream: run id, monotonic seq, first run_started, terminal done
# ---------------------------------------------------------------------------


def test_stream_persists_monotonic_seq_and_run_started_event() -> None:
    _require_contract_tables()
    run_id = None
    try:
        with _started_app() as client:
            _require_run_status_route(client)
            response = _run_chat(client)
            _assert_chat_ok(response)

            run_id = response.headers.get("X-Run-Id")
            assert run_id, "POST /api/chat must return X-Run-Id"
            assert run_id.startswith("r_"), f"unexpected run id: {run_id!r}"

            events = [event for event in _parse_sse(response.text) if event["data"] is not None]
            assert events, "SSE response contained no data events"

            first = events[0]
            assert first["data"].get("type") == "run_started", (
                f"first SSE event must be run_started, got {first['data']!r}"
            )
            assert first["data"].get("run_id") == run_id
            assert first["data"].get("seq") == 1, first["data"]
            first_run_id, first_seq = _event_id_seq(first["id"])
            assert (first_run_id, first_seq) == (run_id, 1), (
                f"first event id must be {run_id}:1, got {first['id']!r}"
            )

            persisted = [event for event in events if event["id"] is not None]
            seqs = []
            for event in persisted:
                event_run_id, seq = _event_id_seq(event["id"])
                assert event_run_id == run_id, (
                    f"event id {event['id']!r} does not belong to run {run_id!r}"
                )
                assert event["data"].get("seq") == seq, (
                    f"SSE id seq {seq} disagrees with payload {event['data']!r}"
                )
                seqs.append(seq)
            assert seqs == list(range(1, len(seqs) + 1)), f"seq gap/duplicate: {seqs}"

            assert events[-1]["data"].get("type") == "done", (
                f"terminal event must be done, got {events[-1]['data']!r}"
            )

            record = _run_record(run_id)
            assert record is not None, "POST /api/chat did not persist a runs row"
            assert record["status"] == "completed", (
                f"successful stream left run status {record['status']!r}"
            )
            assert record["last_event_seq"] == len(seqs), (
                f"last_event_seq {record['last_event_seq']} != persisted events {len(seqs)}"
            )
            assert record["started_at"], "runs.started_at must be set"

            rows = _run_event_rows(run_id)
            assert [row["seq"] for row in rows] == seqs
            assert rows[0]["type"] == "run_started"
            assert rows[-1]["type"] == "done"
    finally:
        _cleanup_runs([run_id] if run_id else [])


# ---------------------------------------------------------------------------
# 2. Replay after disconnect: after= and Last-Event-ID, no duplicates or gaps
# ---------------------------------------------------------------------------


def test_replay_after_seq_has_no_duplicates_or_gaps() -> None:
    _require_contract_tables()
    run_id = None
    try:
        with _started_app() as client:
            _require_replay_route(client)
            response = _run_chat(client)
            _assert_chat_ok(response)
            run_id = response.headers.get("X-Run-Id")
            assert run_id

            original = [
                event for event in _parse_sse(response.text) if event["data"] is not None
            ]
            original_seqs = [_event_id_seq(event["id"])[1] for event in original]
            assert original_seqs and original_seqs == list(range(1, len(original_seqs) + 1))

            full = client.get(f"/api/runs/{run_id}/events", params={"after": "0"})
            assert full.status_code == 200, full.text[:2000]
            full_events = [e for e in _parse_sse(full.text) if e["data"] is not None]
            full_seqs = [_event_id_seq(e["id"])[1] for e in full_events]
            assert full_seqs == original_seqs, (
                f"full replay {full_seqs} != original stream {original_seqs}"
            )
            assert full_events[-1]["data"].get("type") == "done"

            tail = client.get(f"/api/runs/{run_id}/events", params={"after": "2"})
            assert tail.status_code == 200, tail.text[:2000]
            tail_events = [e for e in _parse_sse(tail.text) if e["data"] is not None]
            tail_seqs = [_event_id_seq(e["id"])[1] for e in tail_events]
            assert tail_seqs == original_seqs[2:], (
                f"after=2 replay {tail_seqs} != expected tail {original_seqs[2:]}"
            )
            assert len(tail_seqs) == len(set(tail_seqs)), f"duplicate seq in replay: {tail_seqs}"

            header_tail = client.get(
                f"/api/runs/{run_id}/events",
                headers={"Last-Event-ID": f"{run_id}:2"},
            )
            assert header_tail.status_code == 200, header_tail.text[:2000]
            header_events = [
                e for e in _parse_sse(header_tail.text) if e["data"] is not None
            ]
            header_seqs = [_event_id_seq(e["id"])[1] for e in header_events]
            assert header_seqs == tail_seqs, (
                f"Last-Event-ID replay {header_seqs} != after=2 replay {tail_seqs}"
            )
    finally:
        _cleanup_runs([run_id] if run_id else [])


# ---------------------------------------------------------------------------
# 3. Restart marks running / awaiting_confirmation runs interrupted
# ---------------------------------------------------------------------------


def test_restart_marks_non_terminal_runs_interrupted() -> None:
    _require_contract_tables()
    running_id = _new_run_id()
    awaiting_id = _new_run_id()
    completed_id = _new_run_id()
    try:
        with _connect() as conn:
            _seed_run(conn, running_id, "running", last_event_seq=1)
            _seed_event(conn, running_id, 1, "run_started", {"run_id": running_id, "seq": 1})
            _seed_run(conn, awaiting_id, "awaiting_confirmation", last_event_seq=1)
            _seed_event(conn, awaiting_id, 1, "run_started", {"run_id": awaiting_id, "seq": 1})
            _seed_run(conn, completed_id, "completed", last_event_seq=2)
            _seed_event(conn, completed_id, 1, "run_started", {"run_id": completed_id, "seq": 1})
            _seed_event(conn, completed_id, 2, "done", {"success": True})
            conn.commit()

        attempts = _trigger_restart_recovery()

        running = _run_record(running_id)
        awaiting = _run_record(awaiting_id)
        completed = _run_record(completed_id)
        assert running is not None and awaiting is not None and completed is not None
        assert running["status"] == "interrupted", (
            f"running run stayed {running['status']!r}; recovery attempted via {attempts}"
        )
        assert running["error_code"] == "PROCESS_RESTART", running["error_code"]
        assert awaiting["status"] == "interrupted", (
            f"awaiting_confirmation run stayed {awaiting['status']!r}"
        )
        assert awaiting["error_code"] == "PROCESS_RESTART", awaiting["error_code"]
        assert completed["status"] == "completed", (
            f"terminal run was rewritten to {completed['status']!r}"
        )
        assert completed["error_code"] is None

        assert [row["seq"] for row in _run_event_rows(running_id)] == [1]
    finally:
        _cleanup_runs([running_id, awaiting_id, completed_id])


# ---------------------------------------------------------------------------
# 4. Interrupted replay: persisted tail + error(INTERRUPTED) + done(false)
# ---------------------------------------------------------------------------


def test_interrupted_replay_terminates_without_tool_replay() -> None:
    _require_contract_tables()
    run_id = _new_run_id()
    try:
        with _connect() as conn:
            _seed_run(conn, run_id, "interrupted", last_event_seq=2)
            conn.execute(
                "UPDATE runs SET error_code = 'PROCESS_RESTART' WHERE id = ?", (run_id,)
            )
            _seed_event(conn, run_id, 1, "run_started", {"run_id": run_id, "seq": 1})
            _seed_event(conn, run_id, 2, "text", {"content": "partial answer"})
            conn.commit()

        with _started_app() as client:
            _require_replay_route(client)

            status_response = client.get(f"/api/runs/{run_id}")
            if status_response.status_code == 200:
                assert _extract_status(status_response.json()) == "interrupted", (
                    status_response.json()
                )

            replay = client.get(f"/api/runs/{run_id}/events", params={"after": "0"})
            assert replay.status_code == 200, replay.text[:2000]
            events = [e for e in _parse_sse(replay.text) if e["data"] is not None]
            types = [e["data"].get("type") for e in events]
            seqs = [_event_id_seq(e["id"])[1] for e in events]

            assert types[:2] == ["run_started", "text"], types
            assert seqs[:2] == [1, 2], seqs
            # The persisted tail must carry ids; the synthetic interrupted
            # terminal events may or may not be persisted by the implementation.
            assert all(seq is not None for seq in seqs[:2]), seqs
            tail_seqs = seqs[2:]
            non_null_tail = [seq for seq in tail_seqs if seq is not None]
            assert non_null_tail == sorted(non_null_tail), (
                f"replay seq not monotonic: {seqs}"
            )
            assert len(non_null_tail) == len(set(non_null_tail)), (
                f"duplicate seq in replay: {seqs}"
            )
            for seq in non_null_tail:
                assert seq >= 3, f"synthetic replay event reused a persisted seq: {seqs}"

            error_events = [e for e in events if e["data"].get("type") == "error"]
            assert error_events, f"interrupted replay emitted no error event: {types}"
            assert _extract_error_code(error_events[-1]["data"]) == "INTERRUPTED", (
                error_events[-1]["data"]
            )
            done_events = [e for e in events if e["data"].get("type") == "done"]
            assert done_events, f"interrupted replay emitted no done event: {types}"
            assert done_events[-1]["data"].get("success") is False, done_events[-1]["data"]

            replayed_after_tail = types[2:]
            forbidden = [
                event_type
                for event_type in replayed_after_tail
                if isinstance(event_type, str)
                and ("tool" in event_type or "command" in event_type)
            ]
            assert not forbidden, (
                f"interrupted replay produced tool/command events {forbidden}; "
                "side-effecting work must not be replayed"
            )
    finally:
        _cleanup_runs([run_id])


# ---------------------------------------------------------------------------
# 5. Pending approvals survive restart and are consumed exactly once
# ---------------------------------------------------------------------------


def _find_approval_consumer():
    allowed_params = {
        "run_id",
        "call_id",
        "approval_id",
        "decision",
        "status",
        "principal_id",
        "now",
        "expires_at",
    }
    keywords = ("consume", "claim", "decide", "resolve")
    for module_name in _RECOVERY_MODULES + ("app.api.chat_routes",):
        module = _try_import(module_name)
        if module is None:
            continue
        for name in dir(module):
            lowered = name.lower()
            if "approval" not in lowered:
                continue
            if not any(keyword in lowered for keyword in keywords):
                continue
            candidate = getattr(module, name)
            if not callable(candidate):
                continue
            try:
                signature = inspect.signature(candidate)
            except (TypeError, ValueError):
                continue
            required = [
                p.name
                for p in signature.parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind
                not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            ]
            if all(param in allowed_params for param in required):
                return module_name, name, candidate
    return None


def _call_approval_consumer(consumer, *, run_id: str, call_id: str, approval_id: str):
    _module_name, _name, function = consumer
    signature = inspect.signature(function)
    values = {
        "run_id": run_id,
        "call_id": call_id,
        "approval_id": approval_id,
        "decision": "allow",
        "status": "allowed",
        "principal_id": "acceptance-principal",
        "now": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    kwargs = {}
    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.name in values:
            kwargs[param.name] = values[param.name]
    result = function(**kwargs)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result


def test_pending_approval_survives_restart_and_consumes_once() -> None:
    _require_contract_tables()
    run_id = _new_run_id()
    call_id = f"call_acc_{uuid.uuid4().hex}"
    approval_id = None
    try:
        with _connect() as conn:
            _seed_run(conn, run_id, "awaiting_confirmation", last_event_seq=1)
            _seed_event(conn, run_id, 1, "run_started", {"run_id": run_id, "seq": 1})
            approval_id = _seed_pending_approval(conn, run_id, call_id)
            conn.commit()

        _trigger_restart_recovery()

        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        assert row is not None, "non-expired pending approval was deleted by restart"
        assert row["status"] == "pending", (
            f"pending approval was rewritten to {row['status']!r} by restart"
        )
        assert row["args_json"] == json.dumps(
            {"path": "acceptance.txt"}, ensure_ascii=False
        )

        consumer = _find_approval_consumer()
        if consumer is None:
            pytest.skip(
                "runtime dependency missing: no one-shot approval consumer exposed "
                "(expected a run_store/recovery function named consume/claim/decide/resolve "
                "*approval*; HTTP resume one-shot must then be covered by the runtime thread "
                "with a live graph fixture)"
            )

        _call_approval_consumer(
            consumer, run_id=run_id, call_id=call_id, approval_id=approval_id
        )
        with _connect() as conn:
            first = conn.execute(
                "SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)
            ).fetchone()

        retained = first is not None
        decided_at = None
        first_status = None
        if retained:
            assert first["status"] in {"allowed", "denied", "stopped", "expired"}, (
                f"approval stayed {first['status']!r} after consumption"
            )
            assert first["decided_at"] is not None, "decided_at was not recorded"
            decided_at = first["decided_at"]
            first_status = first["status"]

        second_error = None
        try:
            second_result = _call_approval_consumer(
                consumer, run_id=run_id, call_id=call_id, approval_id=approval_id
            )
        except Exception as exc:  # a second consumption may raise by design
            second_result = None
            second_error = exc
        with _connect() as conn:
            second = conn.execute(
                "SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)
            ).fetchone()

        if retained:
            assert second is not None, "second consumption deleted a retained approval row"
            assert second["decided_at"] == decided_at, (
                f"second consumption mutated decided_at ({decided_at!r} -> "
                f"{second['decided_at']!r}); approval must be consumable exactly once"
            )
            if second_error is None and second_result not in (None, False):
                assert second["status"] == first_status
        else:
            assert second is None, "second consumption recreated a deleted approval row"
            if second_error is None:
                assert not second_result, (
                    "second consumption succeeded after the row was already consumed: "
                    f"{second_result!r}"
                )
    finally:
        _cleanup_runs([run_id])


# ---------------------------------------------------------------------------
# 6. Persisted request/config data never contains credentials
# ---------------------------------------------------------------------------


def _dump_text_values(conn: sqlite3.Connection, query: str, params: tuple) -> str:
    chunks: list[str] = []
    for row in conn.execute(query, params).fetchall():
        chunks.append(json.dumps({key: row[key] for key in row.keys()}, default=str))
    return "\n".join(chunks)


def test_request_snapshot_and_run_config_exclude_secrets() -> None:
    _require_contract_tables()
    run_id = None
    api_secret = f"sk-recovery-acceptance-{uuid.uuid4().hex}"
    tool_secret = f"tvly-recovery-acceptance-{uuid.uuid4().hex}"
    try:
        with _started_app() as client:
            _require_run_status_route(client)
            response = _run_chat(client, secret=api_secret, tool_secret=tool_secret)
            _assert_chat_ok(response)
            run_id = response.headers.get("X-Run-Id")
            assert run_id

        with _connect() as conn:
            batches = [
                _dump_text_values(conn, "SELECT * FROM runs WHERE id = ?", (run_id,)),
                _dump_text_values(
                    conn, "SELECT * FROM run_events WHERE run_id = ?", (run_id,)
                ),
                _dump_text_values(
                    conn, "SELECT * FROM pending_approvals WHERE run_id = ?", (run_id,)
                ),
                _dump_text_values(conn, "SELECT * FROM request_snapshots", ()),
            ]
            run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

        combined = "\n".join(batches)
        assert api_secret not in combined, (
            "X-API-Key value was persisted in recovery tables; config/snapshot must be sanitized"
        )
        assert tool_secret not in combined, (
            "per-request tool credential was persisted in recovery tables"
        )
        assert run_row is not None
        config_json = run_row["config_json"]
        if config_json:
            parsed = json.loads(config_json)
            assert isinstance(parsed, dict), "runs.config_json must be a JSON object"
    finally:
        _cleanup_runs([run_id] if run_id else [])


# ---------------------------------------------------------------------------
# 7. Replay policy: read-only tools idempotent, side-effecting tools never replayed
# ---------------------------------------------------------------------------


def _coerce_replay_flag(result) -> bool | None:
    if isinstance(result, bool):
        return result
    if result is None:
        return None
    if isinstance(result, dict):
        for key in ("replayable", "replay_safe", "safe", "allowed", "idempotent"):
            if key in result and isinstance(result[key], bool):
                return result[key]
        return None
    if isinstance(result, str):
        lowered = result.lower()
        if any(token in lowered for token in ("read_only", "readonly", "safe", "allow")):
            return True
        if any(token in lowered for token in ("side_effect", "write", "never", "deny")):
            return False
        return None
    return bool(result)


def _discover_replay_controls():
    classifiers = []
    tool_sets = []
    allow_markers = (
        "read_only",
        "readonly",
        "allow",
        "safe",
        "idempot",
        "replay",
    )
    deny_markers = (
        "side_effect",
        "sideeffect",
        "block",
        "deny",
        "denied",
        "danger",
        "mutating",
        "non_replay",
        "not_replay",
    )
    for module_name in _RECOVERY_MODULES:
        module = _try_import(module_name)
        if module is None:
            continue
        for name in dir(module):
            lowered = name.lower()
            value = getattr(module, name)
            values = None
            if isinstance(value, dict):
                values = {key for key in value.keys() if isinstance(key, str)}
            elif isinstance(value, (set, frozenset, tuple, list)):
                values = {item for item in value if isinstance(item, str)}
            if values is not None:
                if "search_docs" not in values and "write_file" not in values:
                    continue
                if any(marker in lowered for marker in deny_markers):
                    tool_sets.append((module_name, name, values, "deny"))
                elif any(marker in lowered for marker in allow_markers):
                    tool_sets.append((module_name, name, values, "allow"))
                continue
            if not callable(value):
                continue
            if "read_only" in lowered or "side_effect" in lowered:
                if not any(
                    token in lowered
                    for token in ("tool", "tools", "name", "allowed", "safe", "block", "deny")
                ):
                    continue
            elif "replay" in lowered or "idempot" in lowered:
                if not any(
                    token in lowered
                    for token in (
                        "safe",
                        "allow",
                        "should",
                        "can",
                        "classif",
                        "policy",
                        "eligible",
                        "block",
                        "deny",
                        "read_only",
                    )
                ):
                    continue
            else:
                continue
            try:
                signature = inspect.signature(value)
            except (TypeError, ValueError):
                continue
            positional = [
                p
                for p in signature.parameters.values()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if positional:
                classifiers.append((module_name, name, value))
    return classifiers, tool_sets


def test_read_only_tools_replay_safe_and_side_effecting_tools_blocked() -> None:
    classifiers, tool_sets = _discover_replay_controls()

    if not classifiers and not tool_sets:
        pytest.skip(
            "runtime dependency missing: no replay/idempotency policy exposed in "
            f"{_RECOVERY_MODULES}; expected the recovery thread's read-only whitelist "
            "or a replay classifier"
        )

    if tool_sets:
        for module_name, name, values, kind in tool_sets:
            if kind == "deny":
                for tool in SIDE_EFFECT_TOOLS:
                    assert tool in values, (
                        f"{module_name}.{name} does not list side-effecting tool {tool!r}"
                    )
                for tool in READ_ONLY_TOOLS:
                    assert tool not in values, (
                        f"{module_name}.{name} must not block read-only tool {tool!r}"
                    )
            else:
                for tool in READ_ONLY_TOOLS:
                    assert tool in values, (
                        f"{module_name}.{name} does not allow read-only tool {tool!r}"
                    )
                for tool in SIDE_EFFECT_TOOLS:
                    assert tool not in values, (
                        f"{module_name}.{name} must not allow side-effecting tool {tool!r}"
                    )
        return

    working_classifier = None
    for module_name, name, classifier in classifiers:
        try:
            probe = classifier("search_docs")
            if inspect.isawaitable(probe):
                asyncio.run(probe)
            working_classifier = (module_name, name, classifier)
            break
        except Exception:
            continue
    if working_classifier is None:
        pytest.skip(
            "runtime dependency missing: replay policy candidates exist but none accepted "
            "a tool name: "
            + ", ".join(f"{m}.{n}" for m, n, _ in classifiers)
        )

    module_name, name, classifier = working_classifier

    def classify(tool: str) -> bool:
        result = classifier(tool)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        flag = _coerce_replay_flag(result)
        assert flag is not None, (
            f"{module_name}.{name}({tool!r}) returned unrecognised value {result!r}"
        )
        return flag

    for tool in READ_ONLY_TOOLS:
        assert classify(tool) is True, (
            f"{module_name}.{name} must allow read-only replay for {tool!r}"
        )
    for tool in SIDE_EFFECT_TOOLS:
        assert classify(tool) is False, (
            f"{module_name}.{name} must block automatic replay for {tool!r}"
        )


def test_tool_audit_carries_run_id_for_idempotency() -> None:
    _require_contract_tables()
    with _connect() as conn:
        tables = _table_names(conn)
        if "tool_audit" not in tables:
            pytest.skip(
                "storage dependency missing: tool_audit table not present; "
                "run_id + (run_id, call_id) idempotency source cannot be verified"
            )
        columns = _table_columns(conn, "tool_audit")
    assert "run_id" in columns, (
        "contract requires tool_audit.run_id so replay can reuse the recorded outcome"
    )
