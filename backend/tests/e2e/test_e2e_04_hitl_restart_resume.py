"""E2E 4: HITL after backend restart must fail closed until approvals persist."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat_routes
from app.api.dependencies import current_user


def _client(monkeypatch):
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(
        chat_routes, "_capture_request_snapshot_epochs", lambda *_a: ("0", "0")
    )
    monkeypatch.setattr(chat_routes, "_get_pending_request_snapshot", lambda *_a: None)
    monkeypatch.setattr(chat_routes, "_clear_pending_request_snapshot", lambda *_a: None)
    app = FastAPI()
    app.include_router(chat_routes.router)
    app.dependency_overrides[current_user] = lambda: {
        "provider": None,
        "api_key": None,
        "anonymous": True,
    }
    return TestClient(app)


def test_resume_after_restart_returns_409(monkeypatch):
    response = _client(monkeypatch).post(
        "/api/agent-sandbox/resume",
        json={
            "payload": {
                "messages": [{"role": "user", "content": "question"}],
                "session_id": "hitl-e2e",
            },
            "decision": "allow",
            "call_id": "call-1",
        },
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"]["code"]
        == "HITL_REQUEST_SNAPSHOT_UNAVAILABLE"
    )


@pytest.mark.skip(
    reason="W5 target: resume 200 after restart requires W3 persistent pending approvals"
)
def test_resume_after_restart_continues_run():
    pass
