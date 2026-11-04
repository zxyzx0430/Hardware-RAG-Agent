"""Bounded context records use real SQLite and never become public settings."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import common, crud
from app.db.database import Base
from app.db.models import Session, Settings


@pytest.fixture
def archive_db(tmp_path, monkeypatch):
    path = tmp_path / "events.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(common, "SessionLocal", factory)
    with factory.begin() as db:
        db.add_all([Session(id="archive-a"), Session(id="archive-b")])
    yield SimpleNamespace(engine=engine, factory=factory, path=path)
    engine.dispose()


def namespace(session_id="archive-a"):
    return "_contextEvent:" + hashlib.sha256(session_id.encode()).hexdigest() + ":"


def test_event_namespace_is_not_public_settings(archive_db):
    with archive_db.factory.begin() as db:
        db.add(Settings(key=namespace() + "synthetic-batch", value="synthetic private text"))
        db.add(Settings(key="lang", value="en"))
    with archive_db.factory() as db:
        response = crud.get_settings(db=db, user=None)
    assert response["data"]["settings"] == {"lang": "en"}


def test_delete_purges_only_the_session_event_namespace(archive_db):
    with archive_db.factory.begin() as db:
        db.add_all([Settings(key=namespace() + "batch-a", value="synthetic a"),
                    Settings(key=namespace("archive-b") + "batch-b", value="synthetic b")])
    with archive_db.factory() as db:
        crud.delete_session("archive-a", db=db, user=None)
    with archive_db.factory() as db:
        assert db.get(Settings, namespace() + "batch-a") is None
        assert db.get(Settings, namespace("archive-b") + "batch-b").value == "synthetic b"


def scope(**changes):
    from app.api.context_events import ContextEventScope
    return ContextEventScope(**({"session_id": "archive-a", "request_id": "a" * 32,
                                 "principal_hash": "b" * 64, "session_epoch": ""} | changes))


def test_committed_events_survive_engine_reopen_before_replacement(archive_db, monkeypatch):
    from app.api.context_events import archive_before_replace, recover_context_event
    events = [{"role": "user", "content": "synthetic constraint " + "x" * 620 + " do not flash"}]
    original, replacement = events, [{"role": "assistant", "content": "synthetic summary"}]
    published, refs = archive_before_replace(original, replacement, scope(), authorized=True)
    assert published is replacement and len(refs) == 1
    archive_db.engine.dispose()
    reopened = create_engine(f"sqlite:///{archive_db.path}")
    monkeypatch.setattr(common, "SessionLocal", sessionmaker(bind=reopened))
    try:
        assert recover_context_event(scope(), refs[0], authorized=True, byte_budget=4096) == events[0]
    finally:
        reopened.dispose()


def save_events():
    from app.api.context_events import persist_context_events
    events = [{"role": "user", "content": "synthetic constraint: do not flash"},
              {"role": "assistant", "content": "synthetic observation"}]
    return events, persist_context_events(scope(), events, authorized=True)


@pytest.mark.parametrize("changes", [
    {"session_id": "archive-b"}, {"request_id": "c" * 32},
    {"principal_hash": "d" * 64}, {"session_epoch": "e" * 32},
])
def test_recovery_refuses_other_scope(archive_db, changes):
    from app.api.context_events import recover_context_event
    _events, refs = save_events()
    assert recover_context_event(scope(**changes), refs[0], authorized=True, byte_budget=4096) is None


@pytest.mark.parametrize("changes", [
    {"batch_id": "f" * 32}, {"session_id": "archive-b"}, {"request_id": "f" * 32},
    {"sequence": 0}, {"sequence": True}, {"sequence": 64}, {"version": 2},
    {"version": True}, {"content_sha256": "0" * 64}, {"batch_id": "../path"},
])
def test_reference_fields_are_not_model_authority(archive_db, changes):
    from app.api.context_events import recover_context_event
    _events, refs = save_events()
    bad = replace(refs[0], **changes)
    assert recover_context_event(scope(), bad, authorized=True, byte_budget=4096) is None


@pytest.mark.parametrize("authorized,budget", [(False, 4096), (1, 4096), (True, 1),
                                               (True, True), (True, 32769), (True, 0)])
def test_permission_and_recovery_budget_fail_closed(archive_db, authorized, budget):
    from app.api.context_events import recover_context_event
    _events, refs = save_events()
    assert recover_context_event(scope(), refs[0], authorized=authorized, byte_budget=budget) is None


@pytest.mark.parametrize("events", [
    [{"role": "tool", "content": "synthetic raw Skill resource"}],
    [{"role": "system", "content": "synthetic instructions"}],
    [{"role": "assistant", "content": "synthetic", "tool_calls": []}],
    [{"role": "user", "content": ["synthetic non-text"]}],
    [{"role": "user", "content": "api_key=synthetic-not-a-real-key"}],
    [{"role": "user", "content": "Bearer synthetic-not-a-real-token"}],
    [{"role": "user", "content": "-----BEGIN PRIVATE KEY----- synthetic"}],
    [{"role": "user", "content": "data:image/png;base64,c3ludGhldGlj"}],
    [{"role": "user", "content": "文" * 12000}],
    [{"role": "user", "content": "x" * 40000}],
    [{"role": "user", "content": "x"}] * 65, [],
])
def test_ineligible_input_is_neither_persisted_nor_replaced(archive_db, events):
    from app.api.context_events import archive_before_replace
    published, refs = archive_before_replace(events, ["synthetic replacement"], scope(), authorized=True)
    assert published is events and refs == ()
    with archive_db.factory() as db:
        assert not db.query(Settings).filter(Settings.key.startswith("_contextEvent:")).count()


def test_commit_failure_preserves_original_and_rolls_back_archive(archive_db, monkeypatch, caplog):
    from sqlalchemy.orm import Session as DBSession
    from app.api.context_events import archive_before_replace

    class FailingCommit(DBSession):
        def commit(self):
            raise RuntimeError("synthetic database failure: must not be logged")

    monkeypatch.setattr(common, "SessionLocal", sessionmaker(bind=archive_db.engine, class_=FailingCommit))
    original = [{"role": "user", "content": "synthetic original"}]
    published, refs = archive_before_replace(original, ["synthetic summary"], scope(), authorized=True)
    assert published is original and refs == ()
    assert "must not be logged" not in caplog.text and "synthetic original" not in caplog.text
    with archive_db.factory() as db:
        assert db.query(Settings).count() == 0


def test_delete_recreate_does_not_revive_or_reinsert_old_events(archive_db):
    from app.api.context_events import persist_context_events, recover_context_event
    events, refs = save_events()
    with archive_db.factory() as db:
        crud.delete_session("archive-a", db=db, user=None)
    with archive_db.factory.begin() as db:
        db.add(Session(id="archive-a"))
    assert recover_context_event(scope(), refs[0], authorized=True, byte_budget=4096) is None
    with pytest.raises(ValueError):
        persist_context_events(scope(), events, authorized=True)
    from app.api.session_epoch import read_session_delete_epoch
    fresh = scope(session_epoch=read_session_delete_epoch("archive-a"))
    fresh_refs = persist_context_events(fresh, events, authorized=True)
    assert recover_context_event(fresh, fresh_refs[0], authorized=True, byte_budget=4096) == events[0]
    assert recover_context_event(fresh, refs[0], authorized=True, byte_budget=4096) is None


def test_delete_rollback_keeps_archive_and_generation(archive_db, monkeypatch):
    from app.api.context_events import recover_context_event
    events, refs = save_events()
    with archive_db.factory() as db:
        def fail_commit():
            raise RuntimeError("synthetic failed deletion")
        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError):
            crud.delete_session("archive-a", db=db, user=None)
        db.rollback()
    assert recover_context_event(scope(), refs[0], authorized=True, byte_budget=4096) == events[0]


@pytest.mark.parametrize("value", ["not-json", "{}", "[]", "null", "tampered"])
def test_corrupt_or_changed_record_does_not_recover(archive_db, value):
    from app.api.context_events import recover_context_event
    _events, refs = save_events()
    with archive_db.factory.begin() as db:
        row = db.get(Settings, namespace() + refs[0].batch_id)
        if value == "tampered":
            batch = json.loads(row.value)
            batch["records"][0] = batch["records"][0].replace("do not flash", "do flash now")
            row.value = json.dumps(batch)
        else:
            row.value = value
    assert recover_context_event(scope(), refs[0], authorized=True, byte_budget=4096) is None


def test_capacity_refuses_without_evicting_existing_evidence(archive_db, monkeypatch):
    from app.api import context_events as archive
    events, refs = save_events()
    monkeypatch.setattr(archive, "_MAX_BATCHES", 1)
    with pytest.raises(ValueError):
        archive.persist_context_events(scope(), events, authorized=True)
    assert archive.recover_context_event(scope(), refs[0], authorized=True, byte_budget=4096) == events[0]


def test_namespace_cannot_be_written_through_settings(archive_db):
    from fastapi import HTTPException
    with archive_db.factory() as db, pytest.raises(HTTPException):
        crud.update_settings({namespace() + "spoof": "synthetic"}, db=db, user=None)


@pytest.mark.parametrize("limit_name", ["_SESSION_BYTES", "_TOTAL_BYTES"])
def test_byte_capacity_is_cumulative_not_per_row(archive_db, monkeypatch, limit_name):
    from app.api import context_events as archive
    events, refs = save_events()
    with archive_db.factory() as db:
        row = db.get(Settings, namespace() + refs[0].batch_id)
        used = len(row.value.encode("utf-8"))
    monkeypatch.setattr(archive, limit_name, used + 1)
    with pytest.raises(ValueError):
        archive.persist_context_events(scope(), events, authorized=True)
    assert archive.recover_context_event(scope(), refs[0], authorized=True, byte_budget=4096) == events[0]


@pytest.mark.parametrize("changes", [
    {"request_id": "model-invented"}, {"principal_hash": "unverified"},
    {"session_id": "missing-session"}, {"session_id": "invalid\x00id"},
    {"session_epoch": None},
])
def test_invalid_host_scope_keeps_original(archive_db, changes):
    from app.api.context_events import archive_before_replace
    original = [{"role": "user", "content": "synthetic original"}]
    published, refs = archive_before_replace(original, ["summary"], scope(**changes), authorized=True)
    assert published is original and refs == ()
    with archive_db.factory() as db:
        assert db.query(Settings).count() == 0


@pytest.mark.parametrize("value", ["", "malformed", None])
def test_corrupt_delete_marker_refuses_save_and_recovery(archive_db, value):
    from app.api.context_events import persist_context_events, recover_context_event
    from app.api.session_epoch import SESSION_DELETE_EPOCH_PREFIX
    events, refs = save_events()
    with archive_db.factory.begin() as db:
        db.add(Settings(key=SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(b"archive-a").hexdigest(), value=value))
    assert recover_context_event(scope(), refs[0], authorized=True, byte_budget=4096) is None
    with pytest.raises(ValueError):
        persist_context_events(scope(), events, authorized=True)


def test_order_hash_and_budget_cover_canonical_utf8_record(archive_db):
    from app.api.context_events import persist_context_events, recover_context_event
    events = [{"role": "user", "content": "synthetic 文\nquoted \"text\""},
              {"role": "assistant", "content": "synthetic second observation"}]
    refs = persist_context_events(scope(), events, authorized=True)
    assert [ref.sequence for ref in refs] == [1, 2]
    for event, ref in zip(events, refs):
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        assert ref.content_sha256 == hashlib.sha256(encoded).hexdigest()
        assert recover_context_event(scope(), ref, authorized=True, byte_budget=len(encoded)) == event
        assert recover_context_event(scope(), ref, authorized=True, byte_budget=len(encoded) - 1) is None


@pytest.mark.parametrize("permission", [False, 1, None])
def test_denied_archive_cannot_publish_replacement(archive_db, permission):
    from app.api.context_events import archive_before_replace
    original = [{"role": "user", "content": "synthetic original"}]
    published, refs = archive_before_replace(original, ["summary"], scope(), authorized=permission)
    assert published is original and refs == ()


def test_noop_does_not_create_event_rows(archive_db):
    from app.api.context_events import archive_before_replace
    original = [{"role": "user", "content": "synthetic original"}]
    published, refs = archive_before_replace(original, original, scope(), authorized=True)
    assert published is original and refs == ()
    with archive_db.factory() as db:
        assert db.query(Settings).count() == 0
