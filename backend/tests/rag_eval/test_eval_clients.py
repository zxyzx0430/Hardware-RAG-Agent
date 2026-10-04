"""Both evaluation clients must request and verify the Agent chat path."""

import json

import httpx

from tests.rag_eval.run_eval import RAGTestClient
from tests.rag_eval.run_golden_eval import RAGChatClient


class FakeResponse:
    status_code = 200

    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return ["data: " + json.dumps(event) for event in self.events]


class FakeClient:
    payloads = []
    events = []

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, _method, _url, **kwargs):
        self.payloads.append(kwargs["json"])
        return FakeResponse(self.events)


def _events():
    return [
        {"type": "tool_call", "tool": "search_docs"},
        {"type": "source", "id": "src1", "kb_id": "kb-fixed", "excerpt": "GPIO MODER"},
        {"type": "tool_result", "tool": "search_docs", "success": True},
        {"type": "text", "content": "MODER [src1]"},
        {"type": "done", "success": True},
    ]


def test_golden_client_uses_agent_and_records_path(monkeypatch):
    FakeClient.payloads = []
    FakeClient.events = _events()
    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = RAGChatClient("http://localhost/api", "fake", "fake", "http://fake")
    answer, context, _usage, evidence = client.chat("GPIO?")
    assert FakeClient.payloads[0]["use_agent"] is True
    assert answer == "MODER [src1]"
    assert context == ["GPIO MODER"]
    assert evidence["status"] == "verified_source_path"


def test_rule_client_rejects_stream_without_done(monkeypatch):
    FakeClient.payloads = []
    FakeClient.events = _events()[:-1]
    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = RAGTestClient("fake", "fake", "http://fake", "fake", "fake", "http://fake")
    result = client.chat("GPIO?", ["kb-fixed"])
    assert FakeClient.payloads[0]["use_agent"] is True
    assert result["path_evidence"]["status"] == "request_failed"
