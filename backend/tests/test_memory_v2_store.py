"""Memory v2 store tests: tiers, review states, dedupe, snapshots, purge."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import memory_models  # noqa: F401  registers tables on Base
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


def test_tier_only_create_derives_legacy_scope(store):
    user = store.create(tier="user", kind="preference", content="Use metric units.")
    assert user["tier"] == "user"
    assert user["scope"] == "global"
    assert user["scope_key"] == ""

    project = store.create(
        tier="project",
        project_id="project-abc",
        kind="decision",
        content="Use PlatformIO.",
    )
    assert project["scope"] == "project"
    assert project["scope_key"] == "project-abc"

    working = store.create(
        tier="working", scope_key="session-1", kind="fact", content="UART is on pins 1/2."
    )
    assert working["scope"] == "session"
    assert working["scope_key"] == "session-1"

    episodic = store.create(tier="episodic", kind="decision", content="Earlier decision.")
    assert episodic["scope"] == "project"


def test_legacy_scope_create_still_works(store):
    entry = store.create(
        scope="session", scope_key="session-1", kind="fact", content="Legacy session note."
    )
    assert entry["tier"] == "working"
    assert entry["status"] == "active"
    assert entry["project_id"] == ""
    assert entry["principal_id"] == "local"
    assert entry["schema_version"] == 2

    global_entry = store.create(scope="global", kind="fact", content="Legacy global note.")
    assert global_entry["tier"] == "user"


def test_create_requires_scope_or_tier(store):
    with pytest.raises(MemoryValidationError):
        store.create(kind="fact", content="No scope.")


def test_create_rejects_superseded_start(store):
    with pytest.raises(MemoryValidationError):
        store.create(tier="user", status="superseded", kind="fact", content="Bad start.")


def test_proposed_entries_are_hidden_from_default_reads(store):
    proposed = store.create(
        tier="user", status="proposed", kind="preference", content="Prefers dark mode."
    )
    assert store.get(proposed["id"]) is None
    assert store.list() == []
    assert store.search("dark") == []
    assert store.get(proposed["id"], include_inactive=True)["status"] == "proposed"
    assert [entry["id"] for entry in store.list(status="proposed")] == [proposed["id"]]
    assert [entry["id"] for entry in store.search("dark", status="proposed")] == [
        proposed["id"]
    ]


def test_status_machine_allows_only_review_transitions(store):
    proposed = store.create(
        tier="project", status="proposed", kind="decision", content="Use SPI mode 0."
    )
    assert store.update_status(proposed["id"], "superseded") is None
    assert store.update_status(proposed["id"], "proposed") is None
    active = store.update_status(proposed["id"], "active")
    assert active["status"] == "active"
    superseded = store.update_status(active["id"], "superseded")
    assert superseded["status"] == "superseded"
    assert store.update_status(superseded["id"], "active") is None
    assert store.update_status("missing-id", "active") is None


def test_rejected_entries_can_be_reproposed(store):
    rejected = store.create(
        tier="user", status="proposed", kind="preference", content="Prefers tabs."
    )
    assert store.update_status(rejected["id"], "rejected")["status"] == "rejected"
    replacement = store.upsert_proposed(
        {"tier": "user", "kind": "preference", "content": "Prefers tabs."}
    )
    assert replacement["id"] != rejected["id"]
    assert replacement["status"] == "proposed"


def test_find_duplicate_normalizes_text_and_scopes(store):
    first = store.create(
        tier="project",
        project_id="proj-a",
        kind="fact",
        content="Use   SPI mode 0.",
    )
    duplicate = store.find_duplicate(
        "project", "proj-a", "local", "fact", "  use spi   MODE 0.  "
    )
    assert duplicate["id"] == first["id"]
    assert store.find_duplicate("project", "proj-b", "local", "fact", "use spi mode 0.") is None
    assert store.find_duplicate("user", "", "local", "fact", "use spi mode 0.") is None

    store.soft_delete(first["id"])
    assert store.find_duplicate("project", "proj-a", "local", "fact", "use spi mode 0.") is None


def test_find_duplicate_ignores_rejected_entries(store):
    entry = store.create(
        tier="user", status="proposed", kind="preference", content="Prefers 115200 baud."
    )
    store.update_status(entry["id"], "rejected")
    assert (
        store.find_duplicate("user", "", "local", "preference", "prefers 115200 baud.") is None
    )


def test_upsert_proposed_is_idempotent_for_active_and_proposed(store):
    created = store.upsert_proposed(
        {
            "tier": "project",
            "project_id": "proj-a",
            "kind": "decision",
            "content": "Use 8 MHz crystal.",
            "evidence": {"seal_id": "seal-1"},
        }
    )
    assert created["status"] == "proposed"
    again = store.upsert_proposed(
        {
            "tier": "project",
            "project_id": "proj-a",
            "kind": "decision",
            "content": "use 8 mhz crystal.",
        }
    )
    assert again["id"] == created["id"]

    store.update_status(created["id"], "active")
    active_again = store.upsert_proposed(
        {"tier": "project", "project_id": "proj-a", "kind": "decision", "content": "Use 8 MHz crystal."}
    )
    assert active_again["id"] == created["id"]
    assert active_again["status"] == "active"


def test_evidence_secret_is_rejected(store):
    with pytest.raises(MemorySecretError):
        store.create(
            tier="user",
            kind="fact",
            content="Synthetic content.",
            evidence={"api_key": "abc12345678"},
        )
    assert store.list(include_inactive=True) == []


def test_principal_and_project_validation(store):
    with pytest.raises(MemoryValidationError):
        store.create(tier="user", kind="fact", content="x", principal_id="")
    with pytest.raises(MemoryValidationError):
        store.create(tier="project", kind="fact", content="x", project_id="bad\x00id")


def test_list_and_search_filter_by_v2_fields(store):
    user = store.create(tier="user", kind="preference", content="ESP32 prefers 3.3V.")
    project_a = store.create(
        tier="project", project_id="proj-a", kind="fact", content="ESP32 project A."
    )
    project_b = store.create(
        tier="project", project_id="proj-b", kind="fact", content="ESP32 project B."
    )
    other_principal = store.create(
        tier="project",
        project_id="proj-a",
        principal_id="guest",
        kind="fact",
        content="ESP32 guest note.",
    )

    assert {entry["id"] for entry in store.list(tier="project")} == {
        project_a["id"],
        project_b["id"],
        other_principal["id"],
    }
    assert {entry["id"] for entry in store.list(project_id="proj-a")} == {
        project_a["id"],
        other_principal["id"],
    }
    assert [entry["id"] for entry in store.list(principal_id="guest")] == [
        other_principal["id"]
    ]
    assert {entry["id"] for entry in store.search("ESP32", tier="project")} == {
        project_a["id"],
        project_b["id"],
        other_principal["id"],
    }
    assert {entry["id"] for entry in store.search("ESP32", tiers=("user",))} == {
        user["id"]
    }


def test_legacy_search_visible_excludes_episodic_tier(store):
    user = store.create(tier="user", kind="fact", content="EPISODIC shared note.")
    episodic = store.create(tier="episodic", kind="fact", content="EPISODIC history note.")
    hits = store.search_visible("EPISODIC", project_key="proj-a", session_id="s1")
    ids = {entry["id"] for entry in hits}
    assert user["id"] in ids
    assert episodic["id"] not in ids


def test_snapshot_idempotency_and_epoch_filters(store):
    snapshot = store.create_snapshot(
        session_id="session-1",
        seal_id="seal-1",
        body={"goal": "ship memory v2"},
        principal_hash="a" * 64,
        memory_epoch="epoch-a",
        delete_epoch="delete-a",
        generation=3,
    )
    duplicate = store.create_snapshot(
        session_id="session-1",
        seal_id="seal-1",
        body={"goal": "different body must not replace the seal"},
        memory_epoch="epoch-b",
    )
    assert duplicate["id"] == snapshot["id"]
    assert duplicate["body"] == {"goal": "ship memory v2"}
    assert store.get_snapshot("session-1", "seal-1")["generation"] == 3

    assert [item["id"] for item in store.list_snapshots("session-1", memory_epoch="epoch-a")] == [
        snapshot["id"]
    ]
    assert store.list_snapshots("session-1", memory_epoch="other-epoch") == []
    assert store.list_snapshots("session-1", delete_epoch="delete-a")[0]["id"] == snapshot["id"]
    assert store.latest_snapshot("session-1", memory_epoch="epoch-a")["id"] == snapshot["id"]
    assert store.latest_snapshot("session-1", memory_epoch="other-epoch") is None


def test_snapshot_body_secret_is_rejected(store):
    with pytest.raises(MemorySecretError):
        store.create_snapshot(
            session_id="session-1",
            seal_id="seal-1",
            body={"note": "api_key = abc12345678"},
        )
    assert store.list_snapshots("session-1") == []


def test_purge_session_removes_working_memory_and_snapshots_only(store):
    user = store.create(tier="user", kind="preference", content="User preference.")
    project = store.create(
        tier="project", project_id="proj-a", kind="fact", content="Project fact."
    )
    legacy_session = store.create(
        scope="session", scope_key="session-1", kind="fact", content="Legacy session fact."
    )
    working_a = store.create(
        tier="working", scope_key="session-1", kind="fact", content="Working fact A."
    )
    working_b = store.create(
        tier="working", scope_key="session-2", kind="fact", content="Working fact B."
    )
    snapshot_a = store.create_snapshot(
        session_id="session-1", seal_id="seal-1", body={"goal": "a"}
    )
    snapshot_b = store.create_snapshot(
        session_id="session-2", seal_id="seal-2", body={"goal": "b"}
    )

    counts = store.purge_session("session-1")
    assert counts == {"entries": 2, "snapshots": 1}
    assert store.get(legacy_session["id"], include_inactive=True) is None
    assert store.get(working_a["id"], include_inactive=True) is None
    assert store.get(working_b["id"]) is not None
    assert store.get(user["id"]) is not None
    assert store.get(project["id"]) is not None
    assert store.get_snapshot("session-1", "seal-1") is None
    assert store.get_snapshot("session-2", "seal-2")["id"] == snapshot_b["id"]
    assert snapshot_a["id"]

    assert store.purge_session("session-1") == {"entries": 0, "snapshots": 0}


def test_purge_expired_removes_soft_deleted_and_expired_only(store):
    active = store.create(tier="project", kind="fact", content="Active fact.")
    expired = store.create(
        tier="user", kind="fact", content="Expired fact.", expires_at="2000-01-01T00:00:00Z"
    )
    soft_deleted = store.create(tier="user", kind="fact", content="Soft deleted fact.")
    store.soft_delete(soft_deleted["id"])

    counts = store.purge_expired()
    assert counts == {"soft_deleted": 1, "expired": 1}
    assert store.get(active["id"]) is not None
    assert store.get(expired["id"], include_expired=True) is None
    assert store.get(soft_deleted["id"], include_deleted=True) is None
    assert store.purge_expired() == {"soft_deleted": 0, "expired": 0}
