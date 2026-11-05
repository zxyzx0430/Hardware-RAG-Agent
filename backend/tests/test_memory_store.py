"""MemoryStore tests against a disposable SQLite database (no user data)."""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import memory_models  # noqa: F401  registers memory_entries on Base
from app.db.database import Base
from app.db.memory_store import (
    MemorySecretError,
    MemoryStore,
    MemoryValidationError,
)


@pytest.fixture()
def memory_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def store(memory_db):
    return MemoryStore(session_factory=memory_db)


def _create(store, **overrides):
    payload = {
        "scope": "project",
        "scope_key": "proj-a",
        "kind": "fact",
        "content": "Synthetic memory content.",
    }
    payload.update(overrides)
    return store.create(**payload)


def test_create_and_get_roundtrip(store):
    entry = store.create(
        scope="project",
        scope_key="proj-a",
        kind="preference",
        content="Use PlatformIO instead of the Arduino IDE.",
        structured={"tool": "platformio"},
        source="user",
        confidence=0.9,
    )
    assert entry["id"]
    assert entry["structured"] == {"tool": "platformio"}
    assert entry["source"] == "user"
    assert entry["created_at"].endswith("Z")

    loaded = store.get(entry["id"])
    assert loaded == entry


def test_global_scope_key_is_forced_empty(store):
    entry = _create(store, scope="global", scope_key="ignored")
    assert entry["scope_key"] == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope": "user"},
        {"kind": "note"},
        {"source": "admin"},
        {"confidence": 1.5},
        {"confidence": True},
        {"content": "   "},
        {"content": None},
        {"structured": ["not", "an", "object"]},
        {"scope_key": "bad\x00key"},
    ],
)
def test_invalid_inputs_are_rejected(store, overrides):
    with pytest.raises(MemoryValidationError):
        _create(store, **overrides)


@pytest.mark.parametrize(
    "secret",
    [
        "API key sk-" + "a1B2" * 6,
        "api_key = abc12345678",
        "token: 9f8e7d6c5b4a",
        "Bearer abcdefghijklmnop1234",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE...",
        "AKIAIOSFODNN7EXAMPLE",
    ],
)
def test_obvious_secrets_are_rejected(store, secret):
    with pytest.raises(MemorySecretError):
        _create(store, content=secret)


def test_structured_secret_is_rejected(store):
    with pytest.raises(MemorySecretError):
        _create(store, structured={"password": "hunter2-123456"})


def test_benign_secret_words_are_allowed(store):
    entry = _create(store, content="The password: required field must be filled by the user.")
    assert store.get(entry["id"]) is not None


def test_scope_isolation_for_list_and_search(store):
    global_entry = _create(store, scope="global", content="Global ESP32 note.")
    project_a = _create(store, scope="project", scope_key="proj-a", content="ESP32 project A.")
    project_b = _create(store, scope="project", scope_key="proj-b", content="ESP32 project B.")
    session_a = _create(store, scope="session", scope_key="sess-1", content="ESP32 session one.")

    listed = store.list(scope="project", scope_key="proj-a")
    assert [entry["id"] for entry in listed] == [project_a["id"]]

    searched = store.search("ESP32", scope="project", scope_key="proj-b")
    assert [entry["id"] for entry in searched] == [project_b["id"]]

    session_hits = store.search("ESP32", scope="session", scope_key="sess-1")
    assert [entry["id"] for entry in session_hits] == [session_a["id"]]

    all_ids = {entry["id"] for entry in store.search("ESP32", limit=50)}
    assert all_ids == {global_entry["id"], project_a["id"], project_b["id"], session_a["id"]}


def test_search_visible_limits_scopes(store):
    visible_global = _create(store, scope="global", content="ESP32 global.")
    visible_project = _create(store, scope="project", scope_key="proj-a", content="ESP32 project.")
    visible_session = _create(store, scope="session", scope_key="sess-1", content="ESP32 session.")
    _create(store, scope="project", scope_key="proj-b", content="ESP32 other project.")
    _create(store, scope="session", scope_key="sess-2", content="ESP32 other session.")

    hits = store.search_visible(
        "ESP32", project_key="proj-a", session_id="sess-1", limit=20
    )
    assert {entry["id"] for entry in hits} == {
        visible_global["id"],
        visible_project["id"],
        visible_session["id"],
    }


def test_search_ranks_keywords_before_confidence_and_skips_non_matches(store):
    strong_match = _create(
        store, content="ESP32 UART baud rate 115200", confidence=0.5
    )
    weak_match = _create(store, content="ESP32 only", confidence=0.95)
    _create(store, content="STM32 SPI bus", confidence=0.99)

    results = store.search("ESP32 UART")
    assert [entry["id"] for entry in results] == [strong_match["id"], weak_match["id"]]


def test_search_uses_recent_use_as_tiebreaker(store):
    first = _create(store, content="ESP32 UART one", confidence=0.7)
    second = _create(store, content="ESP32 UART two", confidence=0.7)
    store.mark_used([second["id"]])
    results = store.search("ESP32 UART")
    assert results[0]["id"] == second["id"]
    assert {entry["id"] for entry in results} == {first["id"], second["id"]}


def test_soft_delete_hides_entry_everywhere(store):
    entry = _create(store, content="ESP32 removable memory.")
    assert store.soft_delete(entry["id"]) is True
    assert store.get(entry["id"]) is None
    assert store.list() == []
    assert store.search("ESP32") == []
    assert store.soft_delete(entry["id"]) is False


def test_update_fields_and_missing_entry(store):
    entry = _create(store, content="ESP32 old content.", confidence=0.6)
    updated = store.update(
        entry["id"],
        content="ESP32 new content.",
        structured={"pin": 4},
        confidence=0.8,
        expires_at="2999-01-01T00:00:00Z",
    )
    assert updated["content"] == "ESP32 new content."
    assert updated["structured"] == {"pin": 4}
    assert updated["confidence"] == 0.8
    assert updated["expires_at"].startswith("2999-01-01T00:00:00")
    assert store.update("missing-id", content="x") is None


def test_update_rejects_secret_and_keeps_original(store):
    entry = _create(store, content="ESP32 safe content.")
    with pytest.raises(MemorySecretError):
        store.update(entry["id"], content="sk-" + "z9Y8" * 6)
    loaded = store.get(entry["id"])
    assert loaded["content"] == "ESP32 safe content."


def test_update_deleted_entry_returns_none(store):
    entry = _create(store, content="ESP32 removed.")
    store.soft_delete(entry["id"])
    assert store.update(entry["id"], content="ESP32 revived.") is None


def test_expired_entries_are_hidden(store):
    expired = _create(
        store,
        content="ESP32 expired memory.",
        expires_at="2000-01-01T00:00:00Z",
    )
    active = _create(store, content="ESP32 active memory.")
    assert store.get(expired["id"]) is None
    assert [entry["id"] for entry in store.list()] == [active["id"]]
    assert [entry["id"] for entry in store.search("ESP32")] == [active["id"]]
    assert store.get(expired["id"], include_expired=True) is not None


def test_mark_used_updates_only_existing_entries(store):
    entry = _create(store, content="ESP32 usage marker.")
    assert store.get(entry["id"])["last_used_at"] is None
    assert store.mark_used([entry["id"], "missing-id"]) == 1
    loaded = store.get(entry["id"])
    assert loaded["last_used_at"] is not None
    assert loaded["updated_at"] == entry["updated_at"]


def test_list_q_filters_and_limit(store):
    _create(store, content="ESP32 power note.", structured={"board": "esp32"})
    _create(store, content="STM32 clock note.")
    hits = store.list(q="ESP32")
    assert len(hits) == 1
    assert hits[0]["content"] == "ESP32 power note."

    _create(store, content="ESP32 second note.")
    assert len(store.list(limit=1)) == 1
    with pytest.raises(MemoryValidationError):
        store.list(limit=True)


def test_timestamps_are_utc_and_recent(store):
    entry = _create(store, content="ESP32 timestamp note.")
    created = datetime.datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00"))
    delta = datetime.datetime.now(datetime.timezone.utc) - created
    assert abs(delta.total_seconds()) < 60
