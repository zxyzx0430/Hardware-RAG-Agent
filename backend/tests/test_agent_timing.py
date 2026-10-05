"""Privacy-safe timing coverage for the Agent's streamed execution path."""

import json
import logging
import uuid
from collections import Counter

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from src.agent import hitl_handler, sse_adapter


def _event_payloads(events: list[str]) -> list[dict]:
    payloads = []
    for event in events:
        for line in event.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))
    return payloads


def _timing_records(caplog) -> list[dict]:
    records = []
    for record in caplog.records:
        message = record.getMessage()
        prefix = "agent_timing "
        if message.startswith(prefix):
            records.append(json.loads(message[len(prefix):]))
    return records


class _ModelOutputAgent:
    async def astream(self, _events, *, config, stream_mode):
        assert stream_mode == ["messages", "updates"]
        callbacks = config.get("callbacks", [])
        handlers = callbacks if isinstance(callbacks, (list, tuple)) else callbacks.handlers
        run_id = uuid.UUID(int=1)
        for handler in handlers:
            handler.on_chat_model_start({}, [], run_id=run_id)
            handler.on_llm_new_token("PRIVATE_MODEL_TOKEN", run_id=run_id)
            handler.on_llm_end(None, run_id=run_id)
        yield "messages", (AIMessageChunk(content="PRIVATE_ANSWER_MARKER"), {})
        yield "updates", {
            "model": {"messages": [AIMessage(content="PRIVATE_ANSWER_MARKER")]},
        }


class _FailingAgent:
    async def astream(self, _events, *, config, stream_mode):
        if False:
            yield None
        callbacks = config.get("callbacks", [])
        handlers = callbacks if isinstance(callbacks, (list, tuple)) else callbacks.handlers
        run_id = uuid.UUID(int=2)
        for handler in handlers:
            handler.on_chat_model_start({}, [], run_id=run_id)
            handler.on_llm_new_token("PRIVATE_PARTIAL_MARKER", run_id=run_id)
            handler.on_llm_error(RuntimeError("PRIVATE_ERROR_MARKER"), run_id=run_id)
        raise RuntimeError("PRIVATE_ERROR_MARKER")


async def _no_resume(*_args, **_kwargs):
    if False:
        yield ""


@pytest.mark.asyncio
async def test_agent_stream_logs_model_timing_without_payload_or_sse_changes(
    monkeypatch, caplog,
):
    monkeypatch.setattr(
        sse_adapter,
        "init_stream_state",
        lambda *_args: {"step_index": 0, "text_buffer": "", "pending_tool_calls": set()},
    )
    monkeypatch.setattr(hitl_handler, "handle_auto_resume", _no_resume)
    agent = _ModelOutputAgent()
    config = {"configurable": {"thread_id": "private-session-marker"}}

    with caplog.at_level(logging.INFO, logger="src.agent.telemetry"):
        events = [
            event async for event in sse_adapter.stream_agent_to_sse(
                agent,
                {"messages": []},
                config,
                Counter(),
                model="test-model",
            )
        ]

    records = _timing_records(caplog)
    by_event = {record["event"]: record for record in records}
    assert {"request_start", "model_call_start", "model_first_output", "model_call_end", "request_end"} <= set(by_event)
    request_id = by_event["request_start"]["request_id"]
    assert all(record["request_id"] == request_id for record in records)
    assert by_event["model_call_end"]["duration_ms"] >= 0
    assert by_event["model_call_end"]["first_output_ms"] >= 0
    assert by_event["request_end"]["outcome"] == "completed"
    assert [payload["type"] for payload in _event_payloads(events)] == ["text"]
    assert "agent_timing" not in "\n".join(events)
    assert "PRIVATE_MODEL_TOKEN" not in caplog.text
    assert "PRIVATE_ANSWER_MARKER" not in caplog.text
    assert "private-session-marker" not in caplog.text


@pytest.mark.asyncio
async def test_agent_stream_records_safe_error_and_closes_model_call(monkeypatch, caplog):
    monkeypatch.setattr(
        sse_adapter,
        "init_stream_state",
        lambda *_args: {"step_index": 0, "text_buffer": "", "pending_tool_calls": set()},
    )
    monkeypatch.setattr(hitl_handler, "handle_auto_resume", _no_resume)

    with caplog.at_level(logging.INFO, logger="src.agent.telemetry"):
        with pytest.raises(RuntimeError, match="PRIVATE_ERROR_MARKER"):
            async for _ in sse_adapter.stream_agent_to_sse(
                _FailingAgent(), {"messages": []}, {}, Counter(), model="test-model",
            ):
                pass

    by_event = {record["event"]: record for record in _timing_records(caplog)}
    assert by_event["model_call_end"]["status"] == "error"
    assert by_event["model_call_end"]["first_output_observed"] is True
    assert by_event["request_end"]["outcome"] == "error"
    assert by_event["request_end"]["error_type"] == "RuntimeError"
    assert "PRIVATE_PARTIAL_MARKER" not in caplog.text
    assert "PRIVATE_ERROR_MARKER" not in caplog.text
