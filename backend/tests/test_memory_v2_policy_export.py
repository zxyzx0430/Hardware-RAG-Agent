"""Memory v2 Markdown mirror: layout, idempotency, secrets, atomic failures."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import memory_models  # noqa: F401  registers memory tables on Base
from app.db.database import Base
from app.db.memory_models import MemoryEntry
from app.db.memory_store import MemoryStore, utcnow_naive
from src.agent import memory_export


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


def _insert_raw(memory_db, **fields):
    now = utcnow_naive()
    defaults = {
        "id": uuid.uuid4().hex,
        "scope": "project",
        "scope_key": "proj-a",
        "tier": "project",
        "status": "active",
        "project_id": "proj-a",
        "principal_id": "local",
        "kind": "fact",
        "content": "",
        "structured_json": "{}",
        "evidence_json": "{}",
        "schema_version": 2,
        "source": "import",
        "confidence": 0.7,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(fields)
    with memory_db() as db:
        db.add(MemoryEntry(**defaults))
        db.commit()
    return defaults["id"]


def test_export_layout_and_frontmatter(store, tmp_path):
    store.create(tier="user", kind="preference", content="Use metric units.")
    store.create(
        tier="project",
        project_id="proj-a",
        kind="decision",
        content="Use PlatformIO for builds.",
        evidence={"seal_id": "seal-1"},
    )
    result = memory_export.export_all(store=store, home=tmp_path, project_id="proj-a")
    assert result["failed"] == 0

    user_index = tmp_path / "memory" / "MEMORY.md"
    user_topic = tmp_path / "memory" / "preferences.md"
    project_index = tmp_path / "projects" / "proj-a" / "memory" / "MEMORY.md"
    project_topic = tmp_path / "projects" / "proj-a" / "memory" / "decisions.md"
    for path in (user_index, user_topic, project_index, project_topic):
        assert path.exists(), path

    index_text = project_index.read_text(encoding="utf-8")
    assert "tier: \"project\"" in index_text
    assert "project_id: \"proj-a\"" in index_text
    assert "[decisions.md](decisions.md)" in index_text

    topic_text = project_topic.read_text(encoding="utf-8")
    for field in memory_export.FRONTMATTER_FIELDS:
        assert f"{field}:" in topic_text
    assert "Use PlatformIO for builds." in topic_text
    assert '"seal_id":"seal-1"' in topic_text


def test_export_is_idempotent(store, tmp_path):
    store.create(tier="user", kind="fact", content="Stable content.")
    memory_export.export_all(store=store, home=tmp_path)
    index_path = tmp_path / "memory" / "MEMORY.md"
    topic_path = tmp_path / "memory" / "facts.md"
    first = (index_path.read_bytes(), topic_path.read_bytes())
    first_mtimes = (index_path.stat().st_mtime_ns, topic_path.stat().st_mtime_ns)

    memory_export.export_all(store=store, home=tmp_path)
    assert (index_path.read_bytes(), topic_path.read_bytes()) == first
    assert (index_path.stat().st_mtime_ns, topic_path.stat().st_mtime_ns) == first_mtimes


def test_only_active_entries_enter_the_index(store, tmp_path):
    active = store.create(tier="user", kind="fact", content="ACTIVE exported.")
    proposed = store.upsert_proposed(
        {"tier": "user", "kind": "fact", "content": "PROPOSED pending."}
    )
    rejected = store.upsert_proposed(
        {"tier": "user", "kind": "fact", "content": "REJECTED candidate."}
    )
    store.update_status(rejected["id"], "rejected")
    superseded = store.create(tier="user", kind="fact", content="SUPERSEDED old.")
    store.update_status(superseded["id"], "superseded")

    memory_export.export_all(store=store, home=tmp_path)
    index_text = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    facts = (tmp_path / "memory" / "facts.md").read_text(encoding="utf-8")
    proposals = (tmp_path / "memory" / "PROPOSALS.md").read_text(encoding="utf-8")

    assert active["id"] in facts
    assert "ACTIVE exported." in facts
    assert "REJECTED candidate." not in facts
    assert "SUPERSEDED old." not in facts
    assert proposed["id"] not in facts
    assert proposed["id"] in proposals
    assert "PROPOSED pending." in proposals
    assert proposed["id"] not in index_text


def test_secret_rows_are_not_exported(memory_db, store, tmp_path):
    _insert_raw(
        memory_db,
        content="token: 9f8e7d6c5b4a2c3d",
        project_id="proj-a",
    )
    memory_export.export_all(store=store, home=tmp_path, project_id="proj-a")
    exported = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*.md")
    )
    assert "9f8e7d6c5b4a2c3d" not in exported


def test_atomic_write_failure_does_not_raise_or_change_db(store, tmp_path, monkeypatch):
    entry = store.create(tier="user", kind="fact", content="Keep in DB.")

    def broken_write(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(memory_export, "_atomic_write", broken_write)
    result = memory_export.export_all(store=store, home=tmp_path)
    assert result["failed"] >= 1
    assert store.get(entry["id"]) is not None


def test_unsafe_project_id_is_never_written_outside_home(store, tmp_path):
    store.create(
        tier="project",
        project_id="../escape",
        kind="fact",
        content="Unsafe project id.",
    )
    result = memory_export.export_all(store=store, home=tmp_path)
    assert result["failed"] >= 1
    assert not (tmp_path.parent / "escape").exists()


def test_export_entry_keeps_siblings(store, tmp_path):
    first = store.create(tier="project", project_id="proj-a", kind="fact", content="First fact.")
    store.create(tier="project", project_id="proj-a", kind="fact", content="Second fact.")
    assert memory_export.export_entry(first, store=store, home=tmp_path) is True
    topic = (tmp_path / "projects" / "proj-a" / "memory" / "facts.md").read_text(
        encoding="utf-8"
    )
    assert "First fact." in topic
    assert "Second fact." in topic


def test_working_entries_are_not_mirrored(store, tmp_path):
    store.create(tier="working", scope_key="session-1", kind="fact", content="Working only.")
    memory_export.export_all(store=store, home=tmp_path)
    exported = "\n".join(
        path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.md")
    )
    assert "Working only." not in exported
