"""Manual memory persistence uses a disposable database, never user settings."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.crud import db_router
from app.api.dependencies import current_user_optional
from app.db.database import Base, get_db


@pytest.fixture()
def memory_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    app = FastAPI()
    app.include_router(db_router)

    def database():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user_optional] = lambda: None
    with TestClient(app) as client:
        yield client
    engine.dispose()


def read_memory(client):
    return client.get("/api/settings").json()["data"]["settings"].get("longTermMemory")


def test_memory_save_replace_clear_and_reload(memory_client):
    for memory in ("Use synthetic board A.", "Use synthetic board B.", ""):
        response = memory_client.put("/api/settings", json={"longTermMemory": memory})
        assert response.status_code == 200
        assert response.json()["data"]["updated"] is True
        assert read_memory(memory_client) == memory


@pytest.mark.parametrize("invalid", [None, 42, False, [], {}, "x" * 4001])
def test_invalid_memory_leaves_all_previous_settings_unchanged(memory_client, invalid):
    assert memory_client.put(
        "/api/settings", json={"longTermMemory": "Synthetic preference", "model": "old"}
    ).status_code == 200
    response = memory_client.put(
        "/api/settings", json={"longTermMemory": invalid, "model": "new"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "INVALID_LONG_TERM_MEMORY"
    settings = memory_client.get("/api/settings").json()["data"]["settings"]
    assert settings["longTermMemory"] == "Synthetic preference"
    assert settings["model"] == "old"


def test_memory_limit_counts_unicode_characters(memory_client):
    memory = "板" * 4000
    assert memory_client.put("/api/settings", json={"longTermMemory": memory}).status_code == 200
    assert read_memory(memory_client) == memory


def test_omitting_memory_does_not_clear_it(memory_client):
    memory_client.put("/api/settings", json={"longTermMemory": "Synthetic preference"})
    assert memory_client.put("/api/settings", json={"temperature": 0.3}).status_code == 200
    assert read_memory(memory_client) == "Synthetic preference"


def test_settings_whitelist_still_rejects_unknown_fields(memory_client):
    response = memory_client.put("/api/settings", json={"longTermMemory": "", "unknown": "value"})
    assert response.status_code == 400
    assert read_memory(memory_client) is None


def test_retrieval_settings_save_and_reload_as_legacy_string_values(memory_client):
    response = memory_client.put(
        "/api/settings", json={"topK": 7, "relevanceThreshold": 50}
    )

    assert response.status_code == 200
    assert memory_client.get("/api/settings").json()["data"]["settings"] == {
        "topK": "7",
        "relevanceThreshold": "50",
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("topK", 0),
        ("topK", 1.5),
        ("topK", 21),
        ("topK", True),
        ("relevanceThreshold", -1),
        ("relevanceThreshold", 101),
        ("relevanceThreshold", "NaN"),
        ("relevanceThreshold", False),
    ],
)
def test_invalid_retrieval_setting_does_not_partially_update_settings(
    memory_client, key, value
):
    assert memory_client.put("/api/settings", json={"model": "old"}).status_code == 200

    response = memory_client.put(
        "/api/settings", json={"model": "new", key: value}
    )

    assert response.status_code == 422
    settings = memory_client.get("/api/settings").json()["data"]["settings"]
    assert settings == {"model": "old"}
