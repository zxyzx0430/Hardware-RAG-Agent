"""permissionMode is persisted only in the three app-wide modes."""

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
def settings_client():
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


def _stored_mode(client) -> str | None:
    return client.get("/api/settings").json()["data"]["settings"].get("permissionMode")


@pytest.mark.parametrize("mode", ["bypassPermissions", "default", "acceptEdits"])
def test_supported_permission_modes_are_persisted(settings_client, mode):
    response = settings_client.put("/api/settings", json={"permissionMode": mode})

    assert response.status_code == 200
    assert _stored_mode(settings_client) == mode


@pytest.mark.parametrize("mode", ["plan", "PLAN", "", None, ["plan"], {"mode": "plan"}])
def test_unsupported_permission_modes_are_rejected(settings_client, mode):
    assert settings_client.put(
        "/api/settings", json={"permissionMode": "default"}
    ).status_code == 200

    response = settings_client.put("/api/settings", json={"permissionMode": mode})

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "INVALID_PERMISSION_MODE"
    assert _stored_mode(settings_client) == "default"


def test_plan_rejection_does_not_write_other_settings(settings_client):
    response = settings_client.put(
        "/api/settings", json={"permissionMode": "plan", "model": "new-model"}
    )

    assert response.status_code == 422
    stored = settings_client.get("/api/settings").json()["data"]["settings"]
    assert "permissionMode" not in stored
    assert "model" not in stored
