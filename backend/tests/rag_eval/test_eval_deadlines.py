"""Deadline and partial-stream regression tests with no network transport."""

import asyncio
import json

import httpx

from tests.rag_eval import run_eval
from tests.rag_eval.run_golden_eval import RAGChatClient


class _FakeResponse:
    status_code = 200

    def __init__(self, events, *, delay=0.0, error=None, owner=None):
        self.events = events
        self.delay = delay
        self.error = error
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        if self.owner is not None:
            self.owner.stream_closed = True
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for event in self.events:
            yield "data: " + json.dumps(event)
        if self.delay:
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                if self.owner is not None:
                    self.owner.cancelled = True
                raise
        if self.error is not None:
            raise self.error


class _FakeAsyncClient:
    events = []
    delay = 0.0
    error = None
    client_closed = False
    stream_closed = False
    cancelled = False

    def __init__(self, **_kwargs):
        type(self).client_closed = False
        type(self).stream_closed = False
        type(self).cancelled = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        type(self).client_closed = True
        return False

    def stream(self, *_args, **_kwargs):
        return _FakeResponse(
            type(self).events, delay=type(self).delay,
            error=type(self).error, owner=type(self),
        )


def _stream_events(content):
    return [
        {"type": "tool_call", "tool": "search_docs"},
        {
            "type": "source", "id": "src1", "kb_id": "kb-test",
            "title": "GPIO reference", "page": 9, "excerpt": content[:100],
        },
        {
            "type": "tool_result", "tool": "search_docs", "success": True,
            "result": {
                "success": True,
                "data": {"results": [{
                    "id": "src1", "title": "GPIO reference", "page": 9,
                    "section_title": "Mode register", "chunk_index": 12,
                    "content": content, "private_internal_field": "must not persist",
                }]},
            },
        },
        {"type": "text", "content": "MODER controls GPIO mode [src1]."},
    ]


def test_golden_client_deadline_cancels_stream_and_keeps_partial_evidence(monkeypatch):
    content = "full parent context for grading " * 120
    _FakeAsyncClient.events = _stream_events(content)
    _FakeAsyncClient.delay = 0.5
    _FakeAsyncClient.error = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    client = RAGChatClient(
        "http://127.0.0.1/api", "fake-key", "fake-model", "http://provider",
        chat_deadline_seconds=0.04, read_timeout_seconds=2.0,
    )
    answer, contexts, _usage, evidence = client.chat("GPIO mode?")

    assert answer == "MODER controls GPIO mode [src1]."
    assert contexts == [content]
    assert len(contexts[0]) > 2000
    assert evidence["answer_error"] == "answer_deadline"
    assert evidence["status"] == "request_failed"
    assert evidence["quality_input_status"] == "complete"
    assert evidence["source_evidence"][-1]["page"] == 9
    assert "private_internal_field" not in str(evidence["source_evidence"])
    assert _FakeAsyncClient.cancelled
    assert _FakeAsyncClient.stream_closed and _FakeAsyncClient.client_closed

def test_rule_client_deadline_is_separate_from_read_timeout_and_keeps_partial_data(monkeypatch):
    content = "rule diagnostic parent context " * 110
    _FakeAsyncClient.events = _stream_events(content)
    _FakeAsyncClient.delay = 0.5
    _FakeAsyncClient.error = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = run_eval.RAGTestClient(
        "fake-key", "fake-model", "http://provider", "fake-embedding-key",
        "fake-embedding", "http://embedding", chat_deadline_seconds=0.04,
        chat_read_timeout_seconds=2.0,
    )

    try:
        result = client.chat("GPIO mode?", ["kb-test"])
    finally:
        client.client.close()

    assert result["answer"] == "MODER controls GPIO mode [src1]."
    assert result["agent_context"] == [content]
    assert result["quality_input_status"] == "complete"
    assert result["error"] == "answer_deadline"
    assert result["path_evidence"]["status"] == "request_failed"
    assert _FakeAsyncClient.cancelled
    assert _FakeAsyncClient.stream_closed and _FakeAsyncClient.client_closed


def test_read_timeout_is_classified_separately_and_retains_partial_answer(monkeypatch):
    _FakeAsyncClient.events = [{"type": "text", "content": "partial response"}]
    _FakeAsyncClient.delay = 0.0
    _FakeAsyncClient.error = httpx.ReadTimeout("provider error with secret=do-not-log")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = RAGChatClient(
        "http://127.0.0.1/api", "fake-key", "fake-model", "http://provider",
        chat_deadline_seconds=2.0, read_timeout_seconds=0.02,
    )

    answer, _contexts, _usage, evidence = client.chat("question")

    assert answer == "partial response"
    assert evidence["answer_error"] == "answer_read_timeout"
    assert "secret" not in str(evidence)
    assert _FakeAsyncClient.stream_closed and _FakeAsyncClient.client_closed
