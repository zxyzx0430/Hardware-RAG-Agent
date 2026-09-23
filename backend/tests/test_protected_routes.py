"""Routes that spend stored credentials must reject anonymous requests."""

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.main import create_app
from app.api.dependencies import ws_auth


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/chat", {"messages": [{"role": "user", "content": "hi"}]}),
        ("post", "/api/tool", {"tool": "audit_pins", "args": {}}),
        ("post", "/api/mcp/servers", {"id": "test", "name": "test", "command": "noop"}),
        ("post", "/api/agent-sandbox/policy", {"permission_mode": "default"}),
    ],
)
def test_sensitive_route_requires_token_after_provider_configuration(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, payload: dict
) -> None:
    monkeypatch.setattr("app.api.auth._load_store", lambda: {"providers": {"test": {}}})
    client = TestClient(create_app())
    response = getattr(client, method)(path, json=payload)
    assert response.status_code == 401


def test_serial_websocket_requires_token_after_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.auth._load_store", lambda: {"providers": {"test": {}}})
    websocket = SimpleNamespace(query_params={}, headers={})
    assert ws_auth(websocket) is None


def test_optional_route_rejects_invalid_token_instead_of_downgrading_to_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.auth._load_store", lambda: {"providers": {"test": {}}})
    monkeypatch.setattr("app.api.dependencies.get_provider_key_by_session", lambda token: None)
    client = TestClient(create_app())

    response = client.get("/api/sessions", headers={"Authorization": "Bearer invalid"})

    assert response.status_code == 401
