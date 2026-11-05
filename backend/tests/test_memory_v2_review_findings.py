"""Adversarial review checks for Memory v2 WP1 (review branch only).

Each test states the behavior a single-user installation needs. Failures are
findings recorded in ``memory-v2-review.md``; product code is intentionally
not changed on this branch to make them pass.
"""

import subprocess

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import memory_models  # noqa: F401  registers memory tables on Base
from app.db.database import Base
from app.db.memory_store import MemorySecretError, MemoryStore, MemoryValidationError
from app.db.memory_v2_schema import apply_memory_v2_schema
from app.db.project_identity import current_project_id

V1_ENTRY_COLUMNS = """
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    scope_key TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    structured_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'agent',
    confidence FLOAT NOT NULL DEFAULT 0.7,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    last_used_at DATETIME,
    expires_at DATETIME,
    deleted_at DATETIME,
    supersedes_id TEXT
"""


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


@pytest.fixture()
def legacy_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _create_v1_schema(conn):
    conn.execute(text(f"CREATE TABLE memory_entries ({V1_ENTRY_COLUMNS})"))
    conn.execute(
        text("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at DATETIME)")
    )


def _git(*args, cwd):
    completed = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


# ── store: dedupe identity ────────────────────────────────────────────


def test_working_memory_dedupe_stays_within_one_session(store):
    """Working memory is session-scoped; identical text in another session
    must not return the first session's row."""
    first = store.upsert_proposed(
        {
            "tier": "working",
            "scope_key": "session-a",
            "kind": "fact",
            "content": "UART uses pins 1 and 2.",
        }
    )
    second = store.upsert_proposed(
        {
            "tier": "working",
            "scope_key": "session-b",
            "kind": "fact",
            "content": "UART uses pins 1 and 2.",
        }
    )
    assert second["id"] != first["id"], (
        "dedupe returned session-a's working row for session-b; "
        f"scope_key={second['scope_key']!r}"
    )
    assert second["scope_key"] == "session-b"


def test_user_memory_dedupe_ignores_delivery_project(store):
    """User memory is global; a project_id carried by a proposal must not
    fragment the same preference into per-project duplicates."""
    first = store.upsert_proposed(
        {
            "tier": "user",
            "project_id": "project-a",
            "kind": "preference",
            "content": "Prefers 3.3V logic levels.",
        }
    )
    second = store.upsert_proposed(
        {
            "tier": "user",
            "project_id": "project-b",
            "kind": "preference",
            "content": "Prefers 3.3V logic levels.",
        }
    )
    assert second["id"] == first["id"], (
        "the same user preference was stored twice because the proposal carried "
        "a different project_id"
    )


def test_working_tier_requires_a_session_scope(store):
    """A working row without a session id can never be purged with its session."""
    with pytest.raises(MemoryValidationError):
        store.create(tier="working", kind="fact", content="Sessionless working note.")


# ── store: secret detection ───────────────────────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        "password: correct horse battery staple",
        "api_key: abcdefghijkl",
        "my password is correcthorse",
    ],
)
def test_natural_language_passwords_are_rejected(store, content):
    """Credential-shaped text without digits must still be rejected: the hard
    gate is zero secrets stored."""
    with pytest.raises(MemorySecretError):
        store.create(tier="user", kind="fact", content=content)


def test_evidence_natural_language_password_is_rejected(store):
    with pytest.raises(MemorySecretError):
        store.create(
            tier="user",
            kind="fact",
            content="Synthetic content.",
            evidence={"password": "correct horse battery staple"},
        )


# ── migration ─────────────────────────────────────────────────────────


def test_legacy_import_does_not_resurrect_after_physical_cleanup(legacy_engine):
    """The import marker lives in the imported row, so once ``purge_expired``
    physically removes it, the next startup imports the legacy text again."""
    with legacy_engine.begin() as conn:
        _create_v1_schema(conn)
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('longTermMemory', :value)"),
            {"value": "Prefer 3.3V logic levels."},
        )
        apply_memory_v2_schema(conn)
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM memory_entries WHERE source = 'import'")
            ).scalar()
            == 1
        )
        # purge_expired() deletes soft-deleted rows physically; simulate that.
        conn.execute(text("DELETE FROM memory_entries"))
        apply_memory_v2_schema(conn)
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM memory_entries WHERE source = 'import'")
            ).scalar()
            == 0
        ), "deleted legacy memory was re-imported on the next startup"


def test_legacy_project_scope_create_populates_project_id(store, tmp_path):
    """Callers still using scope='project' write schema_version=2 rows with an
    empty project_id, which the startup migration will never backfill."""
    project_root = tmp_path / "legacy-caller-repo"
    project_root.mkdir()
    entry = store.create(
        scope="project",
        scope_key=str(project_root),
        kind="fact",
        content="Written through the compatibility surface.",
    )
    assert entry["schema_version"] == 2
    assert entry["project_id"] == current_project_id(str(project_root)), (
        "legacy project scope kept project_id empty; v2 project filters and "
        "dedupe cannot see this row"
    )


# ── project identity ──────────────────────────────────────────────────


def test_existing_project_memory_survives_git_init_and_origin_add(store, tmp_path):
    """A folder without git has a path-based id; ``git init`` plus
    ``git remote add`` changes the id and orphans memory saved before."""
    root = tmp_path / "new-hardware-project"
    root.mkdir()
    id_before = current_project_id(str(root))
    entry = store.create(
        tier="project",
        project_id=id_before,
        kind="decision",
        content="Use PlatformIO for this board.",
    )

    _git("init", "-q", cwd=root)
    _git("remote", "add", "origin", "https://github.com/example/board.git", cwd=root)
    id_after = current_project_id(str(root))

    assert id_after == id_before, "project id changed after git init/remote add"
    visible = store.list(project_id=id_after, include_inactive=True)
    assert entry["id"] in {row["id"] for row in visible}
