"""Integration checks for the v2 working-memory endpoint, clear flow and setting."""

from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import crud
from app.api.dependencies import current_user
from app.api.memory_epoch import MEMORY_CLEAR_EPOCH_KEY
from app.api.memory_routes import router as memory_router
from app.db import memory_models  # noqa: F401  registers the tables
from app.db.database import Base, get_db
from app.db.memory_store import MemoryStore
from app.db.models import Settings as SettingsModel


@pytest.fixture()
def db_factory():
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
def client(db_factory):
    app = FastAPI()
    app.include_router(memory_router)

    def database():
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: {
        "provider": None,
        "api_key": None,
        "anonymous": True,
    }
    with TestClient(app) as test_client:
        yield test_client


def _snapshot_body():
    return {
        "goal": [{"text": "Bring up the board", "source": "s1"}],
        "constraints": [{"text": "Use 3.3V logic", "source": "s1"}],
        "completed": [],
        "current_state": [{"text": "UART works", "source": "s1"}],
        "decisions": [],
        "failed_attempts": [],
        "open_questions": [],
        "artifacts_and_evidence": [{"text": "UART log", "source": "s1"}],
        "next_action": [{"text": "Test I2C", "source": "s1"}],
    }


def test_working_endpoint_returns_snapshot_and_fails_closed_after_clear(client, db_factory):
    store = MemoryStore(session_factory=db_factory)
    store.create_snapshot(
        session_id="s1",
        seal_id="seal-1",
        body=_snapshot_body(),
        memory_epoch="",
        delete_epoch="",
        generation=1,
    )

    first = client.get("/api/memory/working/s1")
    assert first.status_code == 200
    snapshot = first.json()["data"]["snapshot"]
    assert snapshot is not None
    assert snapshot["fields"]["goal"] == "Bring up the board"
    assert snapshot["generation"] == 1

    # A memory-clear epoch invalidates the old snapshot even before deletion.
    with db_factory.begin() as db:
        db.add(SettingsModel(
            key=MEMORY_CLEAR_EPOCH_KEY,
            value="a" * 32,
            updated_at=datetime.datetime.utcnow(),
        ))
    after_clear = client.get("/api/memory/working/s1")
    assert after_clear.status_code == 200
    assert after_clear.json()["data"]["snapshot"] is None


def test_clear_endpoint_purges_working_rows_and_advances_epoch(client, db_factory):
    store = MemoryStore(session_factory=db_factory)
    store.create(
        tier="working",
        scope_key="s2",
        kind="fact",
        content="Session-local UART note.",
    )
    store.create_snapshot(
        session_id="s2",
        seal_id="seal-2",
        body=_snapshot_body(),
        memory_epoch="",
        delete_epoch="",
    )

    response = client.post("/api/memory/clear", json={"session_id": "s2"})
    assert response.status_code == 200
    assert response.json()["data"]["cleared"] is True
    assert response.json()["data"]["removed"]["snapshots"] == 1

    with db_factory() as db:
        epoch = db.query(SettingsModel).filter(
            SettingsModel.key == MEMORY_CLEAR_EPOCH_KEY
        ).one().value
        assert len(epoch) == 32
    assert client.get("/api/memory/working/s2").json()["data"]["snapshot"] is None
    assert store.list(tier="working", scope_key="s2") == []


def test_auto_memory_setting_is_normalized_and_validated(db_factory):
    db = db_factory()
    try:
        result = crud.update_settings({"autoMemoryEnabled": True}, db=db, user={})
        assert result["success"] is True
        row = db.query(SettingsModel).filter(
            SettingsModel.key == "autoMemoryEnabled"
        ).one()
        assert row.value == "true"

        crud.update_settings({"autoMemoryEnabled": "false"}, db=db, user={})
        db.expire_all()
        row = db.query(SettingsModel).filter(
            SettingsModel.key == "autoMemoryEnabled"
        ).one()
        assert row.value == "false"

        with pytest.raises(HTTPException) as exc:
            crud.update_settings({"autoMemoryEnabled": "maybe"}, db=db, user={})
        assert exc.value.status_code == 422
    finally:
        db.close()
