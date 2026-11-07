"""Loopback-only local session bootstrap."""

import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.main import create_app


class _ClientHost:
    """Rewrite the ASGI client address so loopback checks are testable."""

    def __init__(self, app, host: str):
        self.app = app
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["client"] = (self.host, 12345)
        await self.app(scope, receive, send)


def _client(host: str) -> TestClient:
    return TestClient(_ClientHost(create_app(), host))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    data = {
        "providers": {
            "prov-first": {"encrypted_key": "x", "base_url": ""},
            "prov-second": {"encrypted_key": "y", "base_url": ""},
        },
        "sessions": {},
    }
    monkeypatch.setattr(auth, "STORE_PATH", tmp_path / "keys_store.json")
    monkeypatch.setattr(auth, "_load_store", lambda: data)
    monkeypatch.setattr(auth, "_save_store", lambda value: data.update(value))
    return data


def test_local_session_issues_a_token_for_loopback(store):
    response = _client("127.0.0.1").post(
        "/api/auth/local-session", json={"provider": "prov-second"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provider"] == "prov-second"
    assert len(data["session_token"]) == 64
    assert data["session_token"] in store["sessions"]


def test_local_session_rejects_non_loopback(store):
    response = _client("10.1.2.3").post("/api/auth/local-session", json={})

    assert response.status_code == 403


def test_local_session_requires_a_provider(store):
    store["providers"].clear()

    response = _client("127.0.0.1").post("/api/auth/local-session", json={})

    assert response.status_code == 409
