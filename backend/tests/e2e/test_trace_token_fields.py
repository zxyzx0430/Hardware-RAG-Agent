"""Trace/token field skeletons for the W5 observability contract."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import _RequestLogMiddleware
from src.observability.trace import new_trace, reset_trace, set_trace
from tests.e2e.helpers import collect_chat


def test_request_middleware_adds_trace_header():
    app = FastAPI()
    app.add_middleware(_RequestLogMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")
    assert response.headers.get("x-trace-id")


@pytest.mark.asyncio
async def test_sse_frames_share_a_trace_id(monkeypatch):
    from src.llm.client import StreamChunk

    async def fake_stream(*_args, **_kwargs):
        yield StreamChunk(type="text", content="hello")

    trace = new_trace("trace-sse")
    token = set_trace(trace)
    try:
        events, body = await collect_chat(monkeypatch, fake_stream)
    finally:
        reset_trace(token)

    trace_ids = {event.get("trace_id") for event in events}
    assert trace_ids == {"trace-sse"}
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert "id: trace-sse:" in body


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


def test_tool_audit_has_trace_and_result_hash():
    from app.db.models import ToolAudit

    columns = set(ToolAudit.__table__.columns.keys())

    assert {"trace_id", "call_id", "result_hash"} <= columns


@pytest.mark.asyncio
async def test_token_usage_recorder_persists_trace():
    from app.api.chat_helpers import _record_token_usage
    from app.db.database import SessionLocal
    from app.db.models import TokenUsage

    trace = new_trace("trace-token")
    token = set_trace(trace)
    try:
        _record_token_usage(
            "test-model",
            "test-provider",
            "session-token",
            {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            source="e2e",
        )
    finally:
        reset_trace(token)

    with SessionLocal() as db:
        row = db.query(TokenUsage).order_by(TokenUsage.id.desc()).first()

    assert row is not None
    assert row.trace_id == "trace-token"
    assert row.cached_tokens == 0
    assert row.source == "e2e"


def test_audit_logger_accepts_extended_fields():
    from app.db.database import SessionLocal
    from app.db.models import ToolAudit
    from src.agent.audit_logger import log_tool_call

    log_tool_call(
        session_id="session-audit",
        tool_name="read_file",
        args={"path": "x"},
        decision="deny",
        call_id="call-audit",
        success=False,
        error_type="denied",
        trace_id="trace-audit",
    )

    with SessionLocal() as db:
        row = db.query(ToolAudit).filter_by(call_id="call-audit").one()

    assert row.trace_id == "trace-audit"
    assert row.success is False
    assert row.result_hash is None


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="W5 target: agent done event must carry non-null usage (chat_routes/sse_adapter owner)",
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
