"""Session deletion lifecycle for Memory v2 working memory and snapshots."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import crud
from app.db import memory_models  # noqa: F401  registers memory tables on Base
from app.db.database import Base
from app.db.memory_models import MemorySnapshot
from app.db.memory_store import MemoryStore
from app.db.models import Session as SessionModel


@pytest.fixture()
def session_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed(store, session_id):
    return {
        "user": store.create(tier="user", kind="preference", content="User preference."),
        "project": store.create(
            tier="project", project_id="proj-a", kind="fact", content="Project fact."
        ),
        "working": store.create(
            tier="working", scope_key=session_id, kind="fact", content="Working fact."
        ),
        "legacy_session": store.create(
            scope="session", scope_key=session_id, kind="fact", content="Legacy session fact."
        ),
        "other_session": store.create(
            tier="working", scope_key="other-session", kind="fact", content="Other session fact."
        ),
        "snapshot": store.create_snapshot(
            session_id=session_id, seal_id="seal-1", body={"goal": "working memory"}
        ),
        "other_snapshot": store.create_snapshot(
            session_id="other-session", seal_id="seal-2", body={"goal": "other memory"}
        ),
    }


def test_delete_session_purges_working_memory_and_snapshots(session_db):
    store = MemoryStore(session_factory=session_db)
    with session_db() as db:
        db.add(SessionModel(id="session-1"))
        db.commit()
    rows = _seed(store, "session-1")

    with session_db() as db:
        crud.delete_session("session-1", db=db, user=None)

    assert store.get(rows["working"]["id"], include_inactive=True) is None
    assert store.get(rows["legacy_session"]["id"], include_inactive=True) is None
    assert store.get(rows["user"]["id"]) is not None
    assert store.get(rows["project"]["id"]) is not None
    assert store.get(rows["other_session"]["id"]) is not None
    assert store.get_snapshot("session-1", "seal-1") is None
    assert store.get_snapshot("other-session", "seal-2") is not None
    with session_db() as db:
        assert db.query(MemorySnapshot).filter_by(session_id="session-1").count() == 0
        assert db.query(SessionModel).filter_by(id="session-1").first() is None


def test_purge_session_rows_is_idempotent(session_db):
    store = MemoryStore(session_factory=session_db)
    _seed(store, "session-1")
    with session_db() as db:
        crud._purge_session_rows(db, "session-1")
        db.commit()
    with session_db() as db:
        crud._purge_session_rows(db, "session-1")
        db.commit()
    assert store.get_snapshot("session-1", "seal-1") is None
    assert store.get_snapshot("other-session", "seal-2") is not None
