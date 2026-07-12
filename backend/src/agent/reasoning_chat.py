"""ChatOpenAI subclass that extracts reasoning_content from third-party providers.

langchain-openai's ChatOpenAI officially does NOT extract non-standard fields
like reasoning_content (DeepSeek-R1 / QwQ / o1 via OpenAI-compatible API).
This subclass overrides _convert_chunk_to_generation_chunk to read
delta.reasoning_content and place it into additional_kwargs so downstream
(sse_adapter) can emit thinking SSE events.

Spec: .trae/specs/surface-agent-reasoning/spec.md
"""
from __future__ import annotations

from langchain_core.messages import BaseMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

# Known reasoning field names across providers (priority order).
_REASONING_FIELDS: tuple[str, ...] = ("reasoning_content", "thinking", "reasoning")


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI that preserves reasoning_content from third-party providers."""

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
