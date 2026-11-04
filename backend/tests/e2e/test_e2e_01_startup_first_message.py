"""E2E 1: clean install, health, first message, missing-key contract."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import _RequestLogMiddleware
from src.llm.client import StreamChunk
from tests.e2e.helpers import collect_chat


def test_health_endpoint_carries_request_id():
    app = FastAPI()
    app.add_middleware(_RequestLogMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
    assert response.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_first_message_streams_text_and_done(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield StreamChunk(type="text", content="hello")

    events, _ = await collect_chat(monkeypatch, fake_stream)

    assert events[0]["type"] == "text"
    assert events[0]["content"] == "hello"
    assert events[-1]["type"] == "done"
    assert events[-1]["success"] is True


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="W5 target: reject missing key with 401/AUTH_FAILED before streaming",
    strict=False,
)
async def test_missing_key_is_rejected_before_streaming(monkeypatch):
    async def fake_stream(*_args, **_kwargs):
        yield StreamChunk(type="text", content="must not run")

    events, _ = await collect_chat(
        monkeypatch,
        fake_stream,
        credentials={
            "api_key": None,
            "base_url": "https://example.invalid/v1",
            "model": "test-model",
            "provider": "test-provider",
        },
    )

    assert events[0]["code"] == "AUTH_FAILED"
