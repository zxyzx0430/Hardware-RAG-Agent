"""The CONTEXT_LIMIT SSE payload names the model/window and is not retryable."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.chat_routes import (
    ChatErrorCode,
    _context_limit_payload,
    _request_budget_payload,
)
from src.agent.request_budget import AgentRequestBudgetError


def test_context_limit_payload_is_specific_and_not_retryable():
    exc = SimpleNamespace(cumulative=235_894, limit=206_233)

    payload = _context_limit_payload(
        exc, model="deepseek-v3", effective_window=262_144,
    )

    assert payload["code"] == ChatErrorCode.CONTEXT_LIMIT == "CONTEXT_LIMIT"
    assert payload["retryable"] is False
    assert "deepseek-v3" in payload["message"]
    assert "206233" in payload["message"]
    assert "新会话" in payload["message"]
    assert "235894" in payload["detail"]
    assert "262144" in payload["detail"]


def test_context_limit_payload_without_model_context():
    exc = SimpleNamespace(cumulative=40_000, limit=32_000)

    payload = _context_limit_payload(exc)

    assert payload["retryable"] is False
    assert "当前模型" in payload["message"]
    assert "32_000".replace("_", "") in payload["message"].replace(",", "")


def test_request_budget_refusal_maps_to_context_limit():
    exc = AgentRequestBudgetError("Pre-send compaction was unavailable")

    payload = _request_budget_payload(
        exc, model="claude-3-5-sonnet", effective_window=200_000,
    )

    assert payload["code"] == ChatErrorCode.CONTEXT_LIMIT == "CONTEXT_LIMIT"
    assert payload["retryable"] is False
    assert "claude-3-5-sonnet" in payload["message"]
    assert "200000" in payload["detail"]
    assert "Pre-send compaction was unavailable" in payload["detail"]
