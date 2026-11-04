"""Shared helpers for W5 E2E skeletons."""

import json
from typing import Any

from fastapi import Request


class _FakeClient:
    def __init__(self, stream):
        self.chat_stream = stream


def request_stub(path: str = "/api/chat") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("test", 1),
    })


def parse_sse(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def _fake_credentials(credentials: dict | None):
    def resolve(_payload, _request):
        if credentials is not None:
            return credentials
        return {
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "model": "test-model",
            "provider": "test-provider",
        }
    return resolve


async def _read_body(response) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


async def collect_chat(
    monkeypatch,
    fake_stream,
    *,
    use_agent: bool = False,
    credentials: dict | None = None,
    top_k: int = 0,
) -> tuple[list[dict[str, Any]], str]:
    from app.api import chat_routes

    async def no_attachments(_payload):
        return "", []

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
    monkeypatch.setattr(chat_routes, "resolve_credentials", _fake_credentials(credentials))
    monkeypatch.setattr(chat_routes, "_process_attachments", no_attachments)
    monkeypatch.setattr(chat_routes, "_build_chat_history", lambda _messages: ([], "question"))
    monkeypatch.setattr(chat_routes, "_build_system_prompt", lambda *_a, **_kw: "test prompt")
    if use_agent:
        monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
        monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_a: True)
        monkeypatch.setattr(chat_routes, "_run_agent_stream", fake_stream)
    else:
        monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", False)
        monkeypatch.setattr(chat_routes, "make_client", lambda **_kw: _FakeClient(fake_stream))
    payload = chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(role="user", content="question")],
        use_agent=use_agent,
        session_id="w5-e2e",
        top_k=top_k,
    )
    response = await chat_routes.chat_sse(payload, request_stub(), user={})
    body = await _read_body(response)
    return parse_sse(body), body
