"""v3-T5: Cumulative context-limit + wall-clock timeout guards for the Agent.

Lives outside sse_adapter so the adapter stays under the 300-line file cap
(AGENTS.md). All token accounting flows through a module-level ContextVar so
concurrent Agent requests (each in its own asyncio task) never share state.

Spec: docs/superpowers/specs/2026-06-30-agent-react-design.md §6.3, §8.5.
"""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from src.agent.exceptions import AgentTimeoutError, ContextLimitError
from src.agent.prompts import MAX_TOKEN_RATIO, TOOL_CALL_TIMEOUT_S
from src.llm.client import LLMClient
from src.llm.model_registry import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_TOKENS,
    get_registered_context_window,
    get_registered_max_tokens,
)

logger = logging.getLogger(__name__)

# Per-request cumulative token counter. Each Agent request runs in its own
# asyncio task → contextvar naturally isolates the count without locks.
_CUMULATIVE_TOKENS: ContextVar[int] = ContextVar("agent_cumulative_tokens", default=0)

AGENT_CONTEXT_WINDOW_CAP = 262_144
# Reserve fixed room for provider message wrappers, tool-call JSON, and framing
# that the local text estimator does not include in message contents.
AGENT_PROTOCOL_FRAMING_TOKEN_RESERVE = 256


@dataclass(frozen=True)
class ContextBudget:
    """Resolved model, output, framing, and input limits for one Agent request."""

    model_window: int
    effective_window: int
    output_tokens: int
    framing_tokens: int
    prompt_window: int
    token_limit: int
    budget_source: str = "registered"


def resolve_context_budget(
    model: str | None,
    context_window_override: int | None = None,
    requested_max_tokens: int | None = None,
) -> ContextBudget:
    """Resolve an Agent budget without silently trusting an unknown model.

    Registered models use their real window. An explicit positive session
    override may tighten it. An unknown model is still usable: a positive
    override is honored up to the hard cap; without one, a conservative 32K
    window and 4K output reserve are used. Invalid overrides and impossible
    output/window combinations still fail closed.
    """
    registered = get_registered_context_window(model)
    override_valid = (
        type(context_window_override) is int and context_window_override > 0
    )
    if context_window_override is not None and not override_valid:
        # A supplied but invalid session/request override must not silently fall
        # back to a larger default.
        raise ContextLimitError(cumulative=0, limit=0)

    if registered is not None:
        model_window = registered
        budget_source = "registered"
        if override_valid:
            effective_window = min(
                registered, context_window_override, AGENT_CONTEXT_WINDOW_CAP,
            )
            budget_source = "explicit_override"
        else:
            effective_window = min(registered, AGENT_CONTEXT_WINDOW_CAP)
    elif override_valid:
        model_window = context_window_override
        effective_window = min(context_window_override, AGENT_CONTEXT_WINDOW_CAP)
        budget_source = "explicit_override"
    else:
        model_window = DEFAULT_CONTEXT_WINDOW
        effective_window = min(DEFAULT_CONTEXT_WINDOW, AGENT_CONTEXT_WINDOW_CAP)
        budget_source = "conservative_default"

    if requested_max_tokens is None:
        output_tokens = get_registered_max_tokens(model)
        if output_tokens is None and registered is None:
            output_tokens = DEFAULT_MAX_TOKENS
    elif type(requested_max_tokens) is int and requested_max_tokens > 0:
        output_tokens = requested_max_tokens
        if registered is None:
            output_tokens = min(output_tokens, DEFAULT_MAX_TOKENS)
    else:
        output_tokens = None
    if output_tokens is None:
        raise ContextLimitError(cumulative=0, limit=0)

    framing_tokens = AGENT_PROTOCOL_FRAMING_TOKEN_RESERVE
    prompt_window = effective_window - output_tokens - framing_tokens
    if prompt_window <= 0:
        raise ContextLimitError(cumulative=1, limit=0)

    token_limit = int(prompt_window * MAX_TOKEN_RATIO)
    if token_limit <= 0:
        raise ContextLimitError(cumulative=1, limit=0)
    logger.info(
        "context_budget model=%s source=%s model_window=%s effective_window=%s "
        "output_tokens=%s token_limit=%s",
        model, budget_source, model_window, effective_window, output_tokens,
        token_limit,
    )
    return ContextBudget(
        model_window=model_window,
        effective_window=effective_window,
        output_tokens=output_tokens,
        framing_tokens=framing_tokens,
        prompt_window=prompt_window,
        token_limit=token_limit,
        budget_source=budget_source,
    )


def compute_token_limit(
    model: str | None,
    context_window_override: int | None = None,
    *,
    output_reserve: int = 0,
    overhead_bytes: int = 0,
) -> int:
    """Compatibility wrapper around the shared ContextBudget resolver.

    ``overhead_bytes`` is retained for existing callers; the shared resolver
    reserves protocol framing tokens instead of estimating arbitrary wire bytes.
    """
    budget = resolve_context_budget(
        model,
        context_window_override,
        requested_max_tokens=output_reserve or None,
    )
    return budget.token_limit


def reset_token_counter(initial_tokens: int = 0) -> None:
    """Start the per-request counter at the already-present input token count."""
    _CUMULATIVE_TOKENS.set(initial_tokens if type(initial_tokens) is int and initial_tokens > 0 else 0)


_MISSING_CONTEXT_FIELD = object()


def _fail_closed_context_estimate(partial_tokens: int = 0) -> None:
    """Stop when an input shape cannot be conservatively measured."""
    raise ContextLimitError(cumulative=max(partial_tokens, 0) + 1, limit=0)


def _message_field(message: Any, name: str) -> Any:
    if isinstance(message, dict):
        return message.get(name, _MISSING_CONTEXT_FIELD)
    try:
        return getattr(message, name)
    except AttributeError:
        return _MISSING_CONTEXT_FIELD
    except Exception as exc:
        raise ContextLimitError(cumulative=1, limit=0) from exc


def _estimate_text_tokens(content: Any, partial_tokens: int) -> int:
    if content is None:
        return 0
    if type(content) is str:
        estimate = LLMClient._estimate_tokens(content)
        if type(estimate) is not int or estimate < 0:
            _fail_closed_context_estimate(partial_tokens)
        return estimate
    if type(content) is list:
        for part in content:
            if (
                type(part) is not dict
                or not _has_only_context_keys(part, {"type", "text"})
                or part.get("type") != "text"
                or type(part.get("text")) is not str
            ):
                _fail_closed_context_estimate(partial_tokens)
        estimate = LLMClient._estimate_tokens(content)
        if type(estimate) is not int or estimate < 0:
            _fail_closed_context_estimate(partial_tokens)
        return estimate
    _fail_closed_context_estimate(partial_tokens)


def _is_supported_context_json(value: Any) -> bool:
    if value is None or type(value) in (str, bool, int, float):
        return True
    if type(value) is list:
        return all(_is_supported_context_json(item) for item in value)
    if type(value) is dict:
        return all(
            type(key) is str and _is_supported_context_json(item)
            for key, item in value.items()
        )
    return False


def _has_only_context_keys(value: dict, allowed: set[str]) -> bool:
    return all(type(key) is str and key in allowed for key in value)


def _serialize_context_json(value: Any, partial_tokens: int) -> str:
    try:
        if not _is_supported_context_json(value):
            raise TypeError("unsupported JSON value in context metadata")
        serialized = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
        serialized.encode("utf-8")
        if json.loads(serialized) != value:
            raise ValueError("JSON serialization changed the input shape")
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ContextLimitError(
            cumulative=max(partial_tokens, 0) + 1, limit=0,
        ) from exc
    return serialized


def _estimate_context_json_tokens(value: Any, partial_tokens: int) -> int:
    serialized = _serialize_context_json(value, partial_tokens)
    estimate = LLMClient._estimate_tokens(serialized)
    if type(estimate) is not int or estimate < 0:
        _fail_closed_context_estimate(partial_tokens)
    return estimate


def _validate_raw_tool_call_duplicate(
    raw_call: Any, normalized_call: dict, partial_tokens: int,
) -> None:
    if (
        type(raw_call) is not dict
        or not _has_only_context_keys(
            raw_call, {"id", "type", "function", "index"},
        )
        or type(normalized_call) is not dict
        or not _has_only_context_keys(
            normalized_call, {"id", "name", "args", "type"},
        )
        or "id" not in normalized_call
        or type(normalized_call.get("name")) is not str
        or not normalized_call.get("name")
        or type(normalized_call.get("args")) is not dict
        or (
            "type" in normalized_call
            and (
                type(normalized_call["type"]) is not str
                or normalized_call["type"] != "tool_call"
            )
        )
    ):
        _fail_closed_context_estimate(partial_tokens)
    if (
        type(raw_call.get("id")) is not str
        or not raw_call["id"]
        or raw_call["id"] != normalized_call.get("id")
    ):
        _fail_closed_context_estimate(partial_tokens)
    raw_type = raw_call.get("type", "function")
    if type(raw_type) is not str or raw_type not in {"function", "tool_call"}:
        _fail_closed_context_estimate(partial_tokens)
    if "index" in raw_call and (
        type(raw_call["index"]) is not int or raw_call["index"] < 0
    ):
        _fail_closed_context_estimate(partial_tokens)
    function = raw_call.get("function")
    if (
        type(function) is not dict
        or not _has_only_context_keys(function, {"name", "arguments"})
    ):
        _fail_closed_context_estimate(partial_tokens)
    raw_name = function.get("name")
    raw_args = function.get("arguments", _MISSING_CONTEXT_FIELD)
    if type(raw_name) is not str or raw_name != normalized_call.get("name"):
        _fail_closed_context_estimate(partial_tokens)
    if type(raw_args) is str:
        try:
            raw_args = json.loads(raw_args)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ContextLimitError(
                cumulative=max(partial_tokens, 0) + 1, limit=0,
            ) from exc
    if type(raw_args) is not dict or raw_args != normalized_call.get("args"):
        _fail_closed_context_estimate(partial_tokens)
    _serialize_context_json(raw_args, partial_tokens)


def estimate_context_tokens(messages: Any) -> int:
    """Estimate text and normalized tool metadata for initial/resumed context.

    Raw provider tool calls in ``additional_kwargs`` are checked against the
    normalized calls and not counted again. Unknown or unfaithfully serializable
    shapes stop the request through ``ContextLimitError``.
    """
    if isinstance(messages, dict):
        messages = messages.get("messages", _MISSING_CONTEXT_FIELD)
    if not isinstance(messages, (list, tuple)):
        _fail_closed_context_estimate()

    total = 0
    for message in messages:
        content = _message_field(message, "content")
        if content is _MISSING_CONTEXT_FIELD:
            _fail_closed_context_estimate(total)
        total += _estimate_text_tokens(content, total)

        tool_calls = _message_field(message, "tool_calls")
        invalid_tool_calls = _message_field(message, "invalid_tool_calls")
        additional_kwargs = _message_field(message, "additional_kwargs")
        if tool_calls is _MISSING_CONTEXT_FIELD or tool_calls is None:
            tool_calls = []
        if type(tool_calls) is not list:
            _fail_closed_context_estimate(total)
        if (
            invalid_tool_calls is not _MISSING_CONTEXT_FIELD
            and invalid_tool_calls is not None
        ):
            if type(invalid_tool_calls) is not list or invalid_tool_calls:
                _fail_closed_context_estimate(total)
        if additional_kwargs is _MISSING_CONTEXT_FIELD or additional_kwargs is None:
            additional_kwargs = {}
        if type(additional_kwargs) is not dict:
            _fail_closed_context_estimate(total)
        if "function_call" in additional_kwargs:
            _fail_closed_context_estimate(total)

        raw_calls = additional_kwargs.get("tool_calls", _MISSING_CONTEXT_FIELD)
        if raw_calls is not _MISSING_CONTEXT_FIELD:
            if type(raw_calls) is not list or len(raw_calls) != len(tool_calls):
                _fail_closed_context_estimate(total)
            for raw_call, normalized_call in zip(raw_calls, tool_calls):
                _validate_raw_tool_call_duplicate(raw_call, normalized_call, total)

        for call in tool_calls:
            if (
                type(call) is not dict
                or not _has_only_context_keys(
                    call, {"id", "name", "args", "type"},
                )
                or "id" not in call
            ):
                _fail_closed_context_estimate(total)
            call_id = call.get("id")
            name = call.get("name")
            args = call.get("args")
            if (
                (
                    call_id is not None
                    and (type(call_id) is not str or not call_id)
                )
                or type(name) is not str
                or not name
                or type(args) is not dict
                or (
                    "type" in call
                    and (
                        type(call["type"]) is not str
                        or call["type"] != "tool_call"
                    )
                )
            ):
                _fail_closed_context_estimate(total)
            total += _estimate_context_json_tokens(
                {"name": name, "id": call_id, "args": args}, total,
            )

        tool_call_id = _message_field(message, "tool_call_id")
        message_type = _message_field(message, "type")
        message_role = _message_field(message, "role")
        is_tool_message = (
            tool_call_id is not _MISSING_CONTEXT_FIELD
            or (
                type(message_type) is str
                and message_type in {"tool", "function", "tool_result"}
            )
            or (
                type(message_role) is str
                and message_role in {"tool", "function", "tool_result"}
            )
            or type(message).__name__ == "ToolMessage"
        )
        if is_tool_message:
            name = _message_field(message, "name")
            if (
                type(tool_call_id) is not str or not tool_call_id
                or (
                    name is not _MISSING_CONTEXT_FIELD
                    and name is not None
                    and (type(name) is not str or not name)
                )
            ):
                _fail_closed_context_estimate(total)
            total += _estimate_context_json_tokens({
                "tool_call_id": tool_call_id,
                "name": None if name is _MISSING_CONTEXT_FIELD else name,
            }, total)
    return total


def compute_token_limit(
    model: str | None,
    context_window_override: int | None = None,
    requested_max_tokens: int | None = None,
) -> int:
    """Return the shared strict context budget threshold for an Agent request."""
    return resolve_context_budget(
        model, context_window_override, requested_max_tokens,
    ).token_limit


def accumulate_tokens(content: Any, state: dict) -> None:
    """Estimate tokens for a chunk and add to the contextvar.

    Tokens are accumulated silently; the caller is responsible for checking
    the limit via _CUMULATIVE_TOKENS (see sse_adapter._should_compact).
    No-op when token_limit <= 0 (model unknown / not provided).
    """
    if state.get("token_limit", 0) <= 0:
        return
    delta = LLMClient._estimate_tokens(content)
    cumulative = _CUMULATIVE_TOKENS.get() + max(delta, 0)
    _CUMULATIVE_TOKENS.set(cumulative)


def check_tool_timeout(call_start_time: dict[str, float], call_id: str) -> None:
    """Raise AgentTimeoutError when a single tool call exceeds TOOL_CALL_TIMEOUT_S.

    Per-tool wall-clock check (spec agent-reliability-batch MODIFIED Requirements).
    If call_id is not in call_start_time, returns without checking (no active call).
    """
    if call_id not in call_start_time:
        return
    elapsed = time.time() - call_start_time[call_id]
    if elapsed > TOOL_CALL_TIMEOUT_S:
        logger.warning(
            "tool_timeout call_id=%s elapsed=%.1fs limit=%ss",
            call_id, elapsed, TOOL_CALL_TIMEOUT_S,
        )
        raise AgentTimeoutError(elapsed=elapsed, limit=TOOL_CALL_TIMEOUT_S)


def context_limit_event(exc: ContextLimitError) -> str:
    """Build the SSE error event for cumulative-token overflow."""
    from app.api.sse import sse_event

    return sse_event("error", {
        "code": "CONTEXT_LIMIT",
        "message": (
            f"上下文超出限制（{exc.cumulative} > {exc.limit}），"
            "本轮请求已停止（CONTEXT_LIMIT）。请缩短对话历史或开启新会话后重试。"
        ),
    })


def timeout_event(exc: AgentTimeoutError) -> str:
    """Build the SSE error event for wall-clock timeout."""
    from app.api.sse import sse_event

    return sse_event("error", {
        "code": "AGENT_TIMEOUT",
        "message": f"Agent 执行超时（{exc.elapsed:.0f}s > {exc.limit}s），降级为基础模式",
    })
