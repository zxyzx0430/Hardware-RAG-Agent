"""Generic tool observations and mid-run summaries use real bounded SQLite."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import common, crud
from app.db.database import Base
from app.db.models import Session, Settings


@pytest.fixture
def archive_db(tmp_path, monkeypatch):
    path = tmp_path / "tool-observations.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(common, "SessionLocal", factory)
    with factory.begin() as db:
        db.add_all([Session(id="obs-a"), Session(id="obs-b")])
    yield SimpleNamespace(engine=engine, factory=factory, path=path)
    engine.dispose()


def make_scope(**changes):
    from app.api.tool_observations import make_tool_observation_scope

    values = {
        "principal_id": "b" * 64,
        "session_id": "obs-a",
        "request_id": "a" * 32,
        "session_delete_epoch": "",
    }
    values.update(changes)
    scope = make_tool_observation_scope(**values)
    assert scope is not None
    return scope


def envelope(
    *,
    call_id="call-1",
    tool_name="search_docs",
    output="synthetic result",
    success=True,
    data=None,
    error=None,
):
    return {
        "success": success,
        "output": output,
        "data": data,
        "error": error,
        "metadata": {
            "tool_name": tool_name,
            "duration_ms": 3,
            "call_id": call_id,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "kb_coverage_hint": None,
            "compacted": False,
        },
    }


def archive(scope_value=None, **overrides):
    from app.api.tool_observations import archive_generic_tool_observation

    values = {
        "tool_name": "search_docs",
        "call_id": "call-1",
        "args": {"query": "synthetic"},
        "result": envelope(),
        "max_bytes": 128 * 1024,
    }
    values.update(overrides)
    return archive_generic_tool_observation(scope_value or make_scope(), **values)


def recover(ref, scope_value=None, budget=128 * 1024):
    from app.api.tool_observations import recover_generic_tool_observation

    return recover_generic_tool_observation(
        ref, scope=scope_value or make_scope(), byte_budget=budget,
    )


def store_summary(scope_value=None, **overrides):
    from app.api.tool_observations import store_midrun_compaction_summary

    values = {
        "summary_text": "synthetic summary",
        "observation_refs": (),
        "idempotency_key": "run-1",
    }
    values.update(overrides)
    return store_midrun_compaction_summary(scope_value or make_scope(), **values)


def read_summary(ref, scope_value=None):
    from app.api.tool_observations import read_midrun_compaction_summary

    return read_midrun_compaction_summary(
        ref, scope=scope_value or make_scope(),
    )


def generic_row_count(factory):
    with factory() as db:
        return db.query(Settings).filter(
            Settings.key.startswith("_toolObservation:v1:generic:", autoescape=True)
        ).count()


def test_generic_archive_and_recover_round_trip(archive_db):
    ref = archive()
    assert type(ref) is str and len(ref) == 43 and ref.isascii()
    recovered = recover(ref)
    assert recovered["tool_name"] == "search_docs"
    assert recovered["call_id"] == "call-1"
    assert recovered["args"] == {"query": "synthetic"}
    assert recovered["envelope"] == envelope()


def test_failed_result_is_archived(archive_db):
    failure = envelope(
        success=False,
        output="",
        error={
            "error_type": "EXEC_ERROR",
            "error_message": "synthetic failure",
            "suggestion": "retry once",
            "retryable": True,
        },
    )
    ref = archive(result=failure)
    assert ref is not None
    assert recover(ref)["envelope"] == failure


def test_same_call_and_canonical_result_is_idempotent(archive_db):
    first = archive()
    second = archive(args={"query": "different"})
    assert first is not None and first == second
    assert generic_row_count(archive_db.factory) == 3


def test_conflicting_result_for_same_call_is_refused(archive_db):
    first = archive()
    second = archive(result=envelope(output="different result"))
    assert first is not None and second is None
    assert recover(first)["envelope"]["output"] == "synthetic result"


@pytest.mark.parametrize("tool_name", [
    "load_skill", "read_skill_resource", "read_tool_observation",
    "read_context_event", "import_skill",
])
def test_skill_and_read_tools_are_not_generically_archived(archive_db, tool_name):
    assert archive(tool_name=tool_name, result=envelope(tool_name=tool_name)) is None
    assert generic_row_count(archive_db.factory) == 0


@pytest.mark.parametrize("changes", [
    {"principal_id": "c" * 64},
    {"session_id": "obs-b"},
    {"request_id": "c" * 32},
])
def test_recovery_refuses_other_scope(archive_db, changes):
    ref = archive()
    assert ref is not None
    assert recover(ref, scope_value=make_scope(**changes)) is None


def test_delete_epoch_invalidates_archive_and_allows_fresh_generation(archive_db):
    from app.api.session_epoch import SESSION_DELETE_EPOCH_PREFIX

    ref = archive()
    key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(b"obs-a").hexdigest()
    with archive_db.factory.begin() as db:
        db.add(Settings(key=key, value="d" * 32))

    assert recover(ref) is None
    assert archive() is None
    fresh = make_scope(session_delete_epoch="d" * 32)
    assert recover(ref, scope_value=fresh) is None
    fresh_ref = archive(scope_value=fresh)
    assert fresh_ref is not None and fresh_ref != ref
    assert recover(fresh_ref, scope_value=fresh) is not None


def test_credential_shaped_result_is_refused(archive_db):
    assert archive(result=envelope(output="api_key=synthetic-not-a-real-key")) is None
    assert generic_row_count(archive_db.factory) == 0


def test_credential_shaped_args_are_omitted_not_stored(archive_db):
    ref = archive(args={"token": "synthetic-not-a-real-token"})
    assert ref is not None
    assert recover(ref)["args"] is None


def test_oversized_or_invalid_limits_are_refused(archive_db):
    assert archive(max_bytes=1024, result=envelope(output="x" * 2048)) is None
    assert archive(max_bytes=0) is None
    assert archive(max_bytes=True) is None
    assert archive(max_bytes=256 * 1024 + 1) is None
    assert generic_row_count(archive_db.factory) == 0


def test_tampered_body_and_reference_are_not_recovered(archive_db):
    ref = archive()
    digest = hashlib.sha256(ref.encode("ascii")).hexdigest()
    with archive_db.factory.begin() as db:
        row = db.get(Settings, f"_toolObservation:v1:generic:body:{digest}")
        row.value = row.value.replace("synthetic result", "tampered result")
    assert recover(ref) is None
    assert recover("A" * 43) is None


def test_session_quota_refuses_without_evicting_existing(archive_db, monkeypatch):
    from app.api import tool_observations as store

    ref = archive()
    with archive_db.factory() as db:
        row = db.query(Settings).filter(
            Settings.key.startswith(
                "_toolObservation:v1:generic:meta:", autoescape=True,
            )
        ).one()
        used = json.loads(row.value)["body_bytes"]
    monkeypatch.setattr(store, "MAX_GENERIC_TOOL_OBSERVATION_SESSION_BYTES", used + 1)
    assert archive(call_id="call-2", result=envelope(call_id="call-2")) is None
    assert recover(ref) is not None


def test_archive_rows_are_not_public_settings(archive_db):
    ref = archive()
    assert ref is not None
    with archive_db.factory.begin() as db:
        db.add(Settings(key="lang", value="en"))
    with archive_db.factory() as db:
        response = crud.get_settings(db=db, user=None)
    assert response["data"]["settings"] == {"lang": "en"}


@pytest.mark.asyncio
async def test_read_tool_observation_returns_untrusted_generic_history(archive_db):
    from app.api.context_events import ContextEventScope
    from app.api.tool_events import bind_tool_event_archive
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.read_tool_observation import (
        ReadToolObservationTool,
    )

    ref = archive()
    event_scope = ContextEventScope(
        session_id="obs-a",
        request_id="a" * 32,
        principal_hash="b" * 64,
        session_epoch="",
    )
    with bind_tool_event_archive(event_scope):
        result = await ReadToolObservationTool().execute(
            {"reference": ref, "byte_budget": 128 * 1024},
            ToolContext(session_id="obs-a", skills_mode="off"),
        )
        wrong_session = await ReadToolObservationTool().execute(
            {"reference": ref, "byte_budget": 128 * 1024},
            ToolContext(session_id="obs-b", skills_mode="off"),
        )
    assert result["available"] is True
    assert result["untrusted_history"] is True
    assert result["untrusted_historical_tool_observation"] is True
    assert result["permission_granted"] is False
    assert result["observation"]["envelope"] == envelope()
    assert wrong_session["available"] is False


def test_midrun_summary_round_trip(archive_db):
    observation_ref = archive()
    ref = store_summary(
        summary_text="synthetic compacted summary",
        observation_refs=(observation_ref,),
    )
    assert type(ref) is str and len(ref) == 43
    summary = read_summary(ref)
    assert summary["kind"] == "midrun_compaction_summary"
    assert summary["summary_text"] == "synthetic compacted summary"
    assert summary["observation_refs"] == [observation_ref]
    assert summary["request_id"] == "a" * 32
    assert summary["untrusted_history"] is True


def test_midrun_summary_is_idempotent_and_conflicts_are_refused(archive_db):
    first = store_summary()
    second = store_summary()
    assert first is not None and first == second
    assert store_summary(summary_text="different summary") is None
    other_key = store_summary(idempotency_key="run-2")
    assert other_key is not None and other_key != first
    assert read_summary(first)["summary_text"] == "synthetic summary"


def test_midrun_summary_scope_and_epoch_are_enforced(archive_db):
    from app.api.session_epoch import SESSION_DELETE_EPOCH_PREFIX

    ref = store_summary()
    assert ref is not None
    assert read_summary(ref, scope_value=make_scope(request_id="c" * 32)) is None
    key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(b"obs-a").hexdigest()
    with archive_db.factory.begin() as db:
        db.add(Settings(key=key, value="e" * 32))
    assert read_summary(ref) is None


@pytest.mark.parametrize("changes", [
    {"summary_text": ""},
    {"summary_text": "x" * (64 * 1024 + 1)},
    {"summary_text": "password=synthetic-not-a-real-password"},
    {"observation_refs": ("too-short",)},
    {"observation_refs": tuple("A" * 43 for _ in range(257))},
    {"idempotency_key": ""},
    {"idempotency_key": "bad\x00key"},
])
def test_ineligible_midrun_summary_is_refused(archive_db, changes):
    assert store_summary(**changes) is None
    with archive_db.factory() as db:
        assert not db.query(Settings).filter(
            Settings.key.startswith("_toolObservation:v1:midrun:", autoescape=True)
        ).count()


def test_tampered_midrun_summary_is_not_recovered(archive_db):
    ref = store_summary(summary_text="synthetic compacted summary")
    digest = hashlib.sha256(ref.encode("ascii")).hexdigest()
    with archive_db.factory.begin() as db:
        row = db.get(Settings, f"_toolObservation:v1:midrun:body:{digest}")
        row.value = row.value.replace("compacted", "overwritten")
    assert read_summary(ref) is None
