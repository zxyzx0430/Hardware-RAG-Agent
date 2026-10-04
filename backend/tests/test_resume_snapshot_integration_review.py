"""Resume must not replace an unverifiable paused-request security snapshot."""

import pytest
from fastapi import HTTPException, Request

from app.api import chat_routes


@pytest.mark.asyncio
@pytest.mark.parametrize("memory", ["", "Synthetic changed preference."])
async def test_missing_snapshot_cannot_be_replaced_by_resume_payload(monkeypatch, memory):
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_get_pending_memory_snapshot", lambda *_args: None)
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: {
        "model": "synthetic", "api_key": "synthetic", "base_url": "http://invalid.test",
    })

    async def forbidden_build(*args, **kwargs):
        pytest.fail("A missing snapshot must fail before constructing or dispatching an Agent")

    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", forbidden_build)
    payload = chat_routes.ChatRequest(
        messages=[{"role": "user", "content": "Synthetic request"}],
        session_id="synthetic-missing-snapshot",
        long_term_memory=memory,
    )
    request = Request({
        "type": "http", "method": "POST", "path": "/api/agent-sandbox/resume",
        "headers": [], "query_string": b"",
    })
    with pytest.raises(HTTPException) as error:
        await chat_routes.resume_agent(
            chat_routes.ResumeRequest(payload=payload, decision="allow"), request, user={},
        )
    assert error.value.status_code == 409
