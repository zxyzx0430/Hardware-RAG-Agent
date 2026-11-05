"""Fail-closed budgeting of the final text-only provider request.

UTF-8 JSON bytes are conservative local accounting units, not provider token
measurements. Remote conversation state, built-in tools and opaque modalities
have no verified local cost and must not bypass this boundary.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from src.agent.prompts import MAX_TOKEN_RATIO
from src.llm.multimodal import estimate_multimodal_tokens

logger = logging.getLogger(__name__)


class AgentRequestBudgetError(ValueError):
    """A local pre-send refusal, never a reason to omit history and retry."""


# Approximate bytes per token for text that is mostly ASCII (schemas, JSON
# framing). This is only used to reserve non-message wire overhead; it is not
# a provider token measurement.
_BYTES_PER_OVERHEAD_TOKEN = 4


def input_budget_tokens(
    context_window: int,
    output_reserve: int,
    overhead_bytes: int = 0,
    safety_ratio: float = MAX_TOKEN_RATIO,
) -> int:
    """Return the token budget available to estimated message bodies.

    The estimate is a local, conservative accounting figure. It is distinct
    from the final wire budget checked by ``validate_agent_request_budget``:
    the wire budget applies to the exact serialized provider payload, while
    this figure is used by the compaction trigger and summarizer input limit.
    ``output_reserve`` is the provider output capacity. ``overhead_bytes``
    covers tool schemas/request framing that are not message bodies.
    """
    if (type(context_window) is not int or isinstance(context_window, bool)
            or context_window <= 0):
        return 0
    if (type(output_reserve) is not int or isinstance(output_reserve, bool)
            or output_reserve < 0):
        raise AgentRequestBudgetError(
            "Agent context budget unavailable: invalid output reserve",
        )
    if (type(overhead_bytes) is not int or isinstance(overhead_bytes, bool)
            or overhead_bytes < 0):
        raise AgentRequestBudgetError(
            "Agent context budget unavailable: invalid overhead accounting",
        )
    if (type(safety_ratio) is not float and type(safety_ratio) is not int) or not 0 < safety_ratio <= 1:
        raise AgentRequestBudgetError(
            "Agent context budget unavailable: invalid safety ratio",
        )
    wire = wire_budget_tokens(context_window, output_reserve, overhead_bytes)
    if wire <= 0:
        return 0
    return int(wire * safety_ratio)


def wire_budget_tokens(
    context_window: int,
    output_reserve: int,
    overhead_bytes: int = 0,
) -> int:
    """Return the final wire budget in the same token units as the window.

    This is a hard local capacity check, not a safety-margin estimate. The
    final fail-closed gate still serializes and validates the real payload.
    """
    if (type(context_window) is not int or isinstance(context_window, bool)
            or context_window <= 0 or type(output_reserve) is not int
            or isinstance(output_reserve, bool) or output_reserve < 0
            or type(overhead_bytes) is not int or isinstance(overhead_bytes, bool)
            or overhead_bytes < 0):
        return 0
    overhead_tokens = (
        overhead_bytes + _BYTES_PER_OVERHEAD_TOKEN - 1
    ) // _BYTES_PER_OVERHEAD_TOKEN
    return context_window - output_reserve - overhead_tokens


def estimate_payload_tokens(value: Any) -> int:
    """Estimate tokens for text or structured content, never returning negative."""
    if value is None:
        return 0
    if isinstance(value, str):
        return max(estimate_multimodal_tokens(value), 0)
    if isinstance(value, list):
        return max(estimate_multimodal_tokens(value), 0)
    try:
        text = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        text = str(value)
    return max(estimate_multimodal_tokens(text), 0)


def estimate_message_tokens(message: Any) -> int:
    """Estimate one message body plus its visible tool routing metadata."""
    if type(message) is dict:
        total = estimate_payload_tokens(message.get("content"))
        if message.get("tool_calls"):
            total += estimate_payload_tokens(message["tool_calls"])
        if message.get("invalid_tool_calls"):
            total += estimate_payload_tokens(message["invalid_tool_calls"])
        tool_call_id = message.get("tool_call_id")
        name = message.get("name")
        if isinstance(tool_call_id, str) and tool_call_id:
            total += estimate_multimodal_tokens(tool_call_id)
        if isinstance(name, str) and name:
            total += estimate_multimodal_tokens(name)
        return total
    total = estimate_payload_tokens(getattr(message, "content", ""))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        total += estimate_payload_tokens(tool_calls)
    invalid_tool_calls = getattr(message, "invalid_tool_calls", None)
    if invalid_tool_calls:
        total += estimate_payload_tokens(invalid_tool_calls)
    tool_call_id = getattr(message, "tool_call_id", None)
    name = getattr(message, "name", None)
    if isinstance(tool_call_id, str) and tool_call_id:
        total += estimate_multimodal_tokens(tool_call_id)
    if isinstance(name, str) and name:
        total += estimate_multimodal_tokens(name)
    return total


def estimated_messages_tokens(messages: Any) -> int:
    """Estimate the cumulative message-body cost for compaction decisions."""
    return sum(estimate_message_tokens(message) for message in messages)


def _budget_json_value(value: Any) -> Any:
    """Account for SDK parse schemas without changing the actual SDK payload."""
    if isinstance(value, type) and issubclass(value, BaseModel):
        return value.model_json_schema()
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise AgentRequestBudgetError("Agent context budget unavailable: unsupported encoding")
        return {key: _budget_json_value(item) for key, item in value.items()}
    if type(value) is list:
        return [_budget_json_value(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise AgentRequestBudgetError("Agent context budget unavailable: unsupported encoding")


def _validate_text_payload(payload: dict) -> None:
    """Reject inputs whose actual context cannot be bounded by text bytes."""
    if any(payload.get(key) for key in (
        "previous_response_id", "conversation", "prompt", "context_management",
        "extra_body", "audio",
    )):
        raise AgentRequestBudgetError("Agent context budget unavailable for hidden provider input")
    if payload.get("truncation") not in (None, "disabled"):
        raise AgentRequestBudgetError("Agent context budget does not allow provider truncation")
    if payload.get("modalities") not in (None, ["text"]):
        raise AgentRequestBudgetError("Agent context budget unavailable for opaque modalities")
    tools = payload.get("tools")
    if tools is None:
        tools = []
    if type(tools) is not list or any(
        type(tool) is not dict or tool.get("type") != "function" for tool in tools
    ):
        raise AgentRequestBudgetError("Agent context budget unavailable for provider-hosted tools")

    if "messages" in payload and "input" in payload:
        raise AgentRequestBudgetError("Agent context budget unavailable: ambiguous input records")
    records = payload.get("messages", payload.get("input"))
    if type(records) is str:
        return
    if type(records) is not list:
        raise AgentRequestBudgetError("Agent context budget unavailable: missing input records")
    for record in records:
        if type(record) is not dict or record.get("audio"):
            raise AgentRequestBudgetError("Agent context budget unavailable for opaque input")
        kind = record.get("type", "message")
        if kind == "function_call":
            if type(record.get("arguments")) is not str:
                raise AgentRequestBudgetError("Agent context budget unavailable for nontext tool input")
            continue
        if kind == "function_call_output":
            content = record.get("output")
        elif kind == "message":
            content = record.get("content")
        else:
            raise AgentRequestBudgetError("Agent context budget unavailable for opaque input")
        if content is None and record.get("role") == "assistant":
            continue
        if type(content) is str:
            continue
        if type(content) is not list or any(
            type(block) is not dict
            or block.get("type") not in ("text", "input_text", "output_text", "refusal")
            or type(block.get("refusal") if block.get("type") == "refusal" else block.get("text")) is not str
            or not set(block).issubset({"type", "text", "refusal", "annotations", "logprobs"})
            for block in content
        ):
            raise AgentRequestBudgetError("Agent context budget unavailable for opaque content")


def validate_agent_request_budget(payload: dict) -> None:
    """Validate the actual model, all visible input and the wire output cap.

    No history is shortened, no tool permissions are inferred and no SDK
    parameters are changed. An unconfigured output cap reserves the locally
    declared maximum instead of assuming the provider's default is small.
    """
    model = payload.get("model")
    if type(model) is not str:
        raise AgentRequestBudgetError("Agent context budget unavailable: model is not registered")
    from src.agent.context_guard import (
        ContextLimitError,
        resolve_context_budget,
    )

    try:
        budget = resolve_context_budget(model)
    except ContextLimitError as exc:
        raise AgentRequestBudgetError(
            "Agent context budget unavailable: model is not registered",
        ) from exc
    window = budget.effective_window
    maximum = budget.output_tokens
    logger.info(
        "agent_request_budget model=%s source=%s window=%s output_cap=%s",
        model, budget.budget_source, window, maximum,
    )
    limits = [payload[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")
              if payload.get(key) is not None]
    if any(type(limit) is not int or limit <= 0 or limit > maximum for limit in limits):
        raise AgentRequestBudgetError("Agent context budget unavailable: invalid output capacity")
    if len(limits) > 1:
        raise AgentRequestBudgetError("Agent context budget unavailable: ambiguous output capacity")
    reserve = limits[0] if limits else maximum
    _validate_text_payload(payload)
    try:
        # SDK transport settings are not model input. Never persist or log them.
        accounted = _budget_json_value({
            key: value for key, value in payload.items()
            if key not in ("extra_headers", "extra_query", "timeout")
        })
        cost = len(json.dumps(accounted, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AgentRequestBudgetError("Agent context budget unavailable: unsupported encoding") from None
    if cost + 256 + reserve > window:
        raise AgentRequestBudgetError("Agent context budget exceeded; original history retained")
