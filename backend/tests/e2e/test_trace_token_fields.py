"""Trace/token field skeletons for the W5 observability contract."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import _RequestLogMiddleware
from tests.e2e.helpers import collect_chat


def test_request_middleware_adds_header():
    app = FastAPI()
    app.add_middleware(_RequestLogMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="W5 target: one trace_id on every SSE frame plus id/seq for replay",
    strict=False,
)
async def test_sse_frames_share_a_trace_id(monkeypatch):
    from src.llm.client import StreamChunk

    async def fake_stream(*_args, **_kwargs):
        yield StreamChunk(type="text", content="hello")

    events, body = await collect_chat(monkeypatch, fake_stream)

    trace_ids = {event.get("trace_id") for event in events}
    assert len(trace_ids) == 1
    assert None not in trace_ids
    assert "\nid: " in body


@pytest.mark.xfail(
    reason="W5 target: TokenUsage needs trace/cost/latency columns",
    strict=False,
)
def test_token_usage_model_has_observability_columns():
    from app.db.models import TokenUsage

    columns = set(TokenUsage.__table__.columns.keys())

    assert {
        "trace_id",
        "run_id",
        "call_id",
        "cached_tokens",
        "cost_usd",
        "latency_ms",
        "status",
        "source",
    } <= columns


@pytest.mark.xfail(
    reason="W5 target: ToolAudit needs trace_id/result_hash",
    strict=False,
)
def test_tool_audit_has_trace_and_result_hash():
    from app.db.models import ToolAudit

    columns = set(ToolAudit.__table__.columns.keys())

    assert {"trace_id", "call_id", "result_hash"} <= columns


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="W5 target: agent done event must carry non-null usage",
    strict=False,
)
async def test_agent_done_event_has_usage(monkeypatch):
    from app.api.sse import sse_event

    async def fake_stream(*_args, **_kwargs):
        yield sse_event("text", {"content": "answer"})

    events, _ = await collect_chat(monkeypatch, fake_stream, use_agent=True)

    done = [event for event in events if event["type"] == "done"][-1]
    assert done.get("usage") is not None


@pytest.mark.skip(
    reason="W5 target: agent TokenUsage row requires chat_routes ownership and a stable agent harness"
)
def test_agent_run_writes_token_usage_row():
    pass


@pytest.mark.skip(
    reason="W5 target: deny-row audit persistence belongs to W4 and needs the audit signature fix"
)
def test_audit_deny_row_is_written():
    pass
