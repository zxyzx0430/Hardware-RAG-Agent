"""ChatOpenAI subclass that extracts reasoning_content from third-party providers.

langchain-openai's ChatOpenAI officially does NOT extract non-standard fields
like reasoning_content (DeepSeek-R1 / QwQ / o1 via OpenAI-compatible API).
This subclass overrides _convert_chunk_to_generation_chunk to read
delta.reasoning_content and place it into additional_kwargs so downstream
(sse_adapter) can emit thinking SSE events.

Spec: .trae/specs/surface-agent-reasoning/spec.md
"""
from __future__ import annotations

import json
import math
from typing import Any

from pydantic import Field
from langchain_core.messages import BaseMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from src.agent.context_guard import resolve_context_budget
from src.agent.exceptions import ContextLimitError

# Known reasoning field names across providers (priority order).
_REASONING_FIELDS: tuple[str, ...] = ("reasoning_content", "thinking", "reasoning")


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI that preserves reasoning_content from third-party providers."""

    context_window_override: int | None = Field(default=None, exclude=True)

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Guard ChatOpenAI's final wire payload before any SDK request begins.

        langchain-openai builds and mutates the provider payload in this hook for
        both Chat Completions and Responses. The base generate/stream methods
        call it immediately before their sync or async SDK request.
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        _guard_provider_payload(payload, self.context_window_override)
        return payload

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type[BaseMessageChunk],
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen_chunk is None:
            return None
        reasoning = _extract_reasoning(chunk)
        if reasoning:
            existing = gen_chunk.message.additional_kwargs.get("reasoning_content", "")
            gen_chunk.message.additional_kwargs["reasoning_content"] = existing + reasoning
        return gen_chunk


def _guard_provider_payload(
    payload: Any,
    context_window_override: int | None,
) -> None:
    """Reject requests whose complete JSON body exceeds the shared context budget.

    The body is measured with the local tokenizer rather than raw UTF-8 bytes;
    bytes-as-tokens previously stopped a 200k window at roughly a quarter of
    its real capacity. The shared budget's 0.8 ratio remains the safety margin.
    """
    if type(payload) is not dict or any(type(key) is not str for key in payload):
        _fail_closed_context_guard()

    # The OpenAI SDK merges extra_body into the actual JSON body, with its keys
    # taking precedence. Measure that effective body so model/output overrides
    # and every additional provider field are included.
    extra_body = payload.get("extra_body")
    if extra_body is not None and type(extra_body) is not dict:
        _fail_closed_context_guard()
    wire_payload = {
        key: value for key, value in payload.items() if key != "extra_body"
    }
    if extra_body is not None:
        wire_payload.update(extra_body)

    if "previous_response_id" in wire_payload:
        _fail_closed_context_guard()

    model = wire_payload.get("model")
    if type(model) is not str or not model:
        _fail_closed_context_guard()
    if context_window_override is not None and (
        type(context_window_override) is not int or context_window_override <= 0
    ):
        _fail_closed_context_guard()

    output_limits = []
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        if key in wire_payload:
            value = wire_payload[key]
            if type(value) is not int or value <= 0:
                _fail_closed_context_guard()
            output_limits.append(value)
    requested_max_tokens = max(output_limits) if output_limits else None

    _serialize_provider_payload(wire_payload)  # fail closed on unsupported shapes
    budget = resolve_context_budget(
        model,
        context_window_override,
        requested_max_tokens,
    )
    from src.agent.request_budget import estimate_payload_tokens

    payload_tokens = estimate_payload_tokens(wire_payload)
    if payload_tokens > budget.token_limit:
        raise ContextLimitError(
            cumulative=payload_tokens,
            limit=budget.token_limit,
        )


def _serialize_provider_payload(payload: dict[str, Any]) -> bytes:
    """Serialize only plain JSON wire values; fail closed on unknown shapes."""
    try:
        if not _is_provider_json_value(payload, set()):
            _fail_closed_context_guard()
        serialized = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = serialized.encode("utf-8")
        if json.loads(serialized) != payload:
            _fail_closed_context_guard()
        return encoded
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ContextLimitError(cumulative=1, limit=0) from exc


def _is_provider_json_value(value: Any, ancestors: set[int]) -> bool:
    """Accept exact JSON types and reject cycles, subclasses, and non-finite floats."""
    if value is None or type(value) in (bool, int):
        return True
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) not in (list, dict):
        return False

    identity = id(value)
    if identity in ancestors:
        return False
    ancestors.add(identity)
    try:
        if type(value) is list:
            return all(_is_provider_json_value(item, ancestors) for item in value)
        return all(
            type(key) is str
            and _is_provider_json_value(key, ancestors)
            and _is_provider_json_value(item, ancestors)
            for key, item in value.items()
        )
    finally:
        ancestors.remove(identity)


def _fail_closed_context_guard() -> None:
    raise ContextLimitError(cumulative=1, limit=0)


def _extract_reasoning(chunk: dict) -> str:
    """Pull reasoning text from a stream chunk delta across providers.

    Tries known field names in priority order: reasoning_content (DeepSeek/
    QwQ/Qwen/GLM-4.6) → thinking (some providers) → reasoning (OpenAI o1 compat).
    Returns first non-empty string, or "" if none found.
    """
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    for field in _REASONING_FIELDS:
        val = delta.get(field)
        if isinstance(val, str) and val:
            return val
    return ""
