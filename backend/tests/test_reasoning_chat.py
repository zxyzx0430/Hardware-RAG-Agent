"""Tests for ReasoningChatOpenAI.

Verifies that the `_convert_chunk_to_generation_chunk` override still extracts
`reasoning_content` from streaming chunks after the langchain-openai 1.2.2
upgrade (langchain 1.x 升级全量审计 spec — Task 3).

Context:
- langchain-openai's base ChatOpenAI explicitly does NOT extract non-standard
  fields like `reasoning_content` (DeepSeek-R1 / QwQ / o1 via OpenAI-compat API).
- `ReasoningChatOpenAI` overrides `_convert_chunk_to_generation_chunk` to pull
  `delta.reasoning_content` into `additional_kwargs` so sse_adapter can emit
  thinking SSE events.
- After upgrading to langchain-openai 1.2.2, the override's signature must
  still match the base class (else Python silently fails to apply it).
"""
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_openai import ChatOpenAI

# Ensure backend/ is on sys.path so `src.agent...` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.reasoning_chat import ReasoningChatOpenAI  # noqa: E402


class _FakeStreamResponse:
    """Simulates an openai streaming response.

    openai's `chat.completions.create(stream=True)` returns an object that is
    both a context manager (`with response as response:`) and an iterable of
    chunks. We yield pre-built dict chunks so `_stream` skips `model_dump()`
    and hands them directly to `_convert_chunk_to_generation_chunk`.
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __iter__(self):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_reasoning_chunk(text: str, model: str = "deepseek-reasoner") -> dict:
    """DeepSeek-style streaming chunk carrying delta.reasoning_content."""
    return {
        "id": "chatcmpl-reasoning",
        "choices": [{"delta": {"reasoning_content": text}, "index": 0}],
        "model": model,
    }


def _make_content_chunk(
    text: str, model: str = "gpt-4o-mini", finish_reason: str | None = None
) -> dict:
    """Normal streaming chunk carrying delta.content only."""
    choice = {"delta": {"content": text}, "index": 0}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return {
        "id": "chatcmpl-content",
        "choices": [choice],
        "model": model,
    }


def _build_llm(model: str = "deepseek-reasoner") -> ReasoningChatOpenAI:
    """Build a ReasoningChatOpenAI with a fake key.

    openai.OpenAI() does NOT make a network call at construction time, so a
    fake key is safe. We patch `client.create` per-test to return fake chunks.
    """
    return ReasoningChatOpenAI(
        api_key="sk-test-key-not-real",
        model=model,
        base_url="https://fake.example.com/v1",
    )


@pytest.fixture
def reasoning_llm():
    """A ReasoningChatOpenAI instance wired with a fake key."""
    return _build_llm("deepseek-reasoner")


class TestReasoningChatOpenAIOverride:
    """Verify `_convert_chunk_to_generation_chunk` override still works on 1.2.2."""

    def test_reasoning_content_extracted_from_stream(self, reasoning_llm):
        """DeepSeek streaming chunks' reasoning_content must land in
        `AIMessageChunk.additional_kwargs["reasoning_content"]`.

        This is the core contract: sse_adapter reads additional_kwargs to emit
        thinking SSE events. If the override broke under 1.2.2, reasoning_content
        would be silently dropped (base class explicitly does not extract it).
        """
        chunks = [
            _make_reasoning_chunk("第一步：分析问题"),
            _make_reasoning_chunk("第二步：推导结论"),
            _make_content_chunk("答案是 42", model="deepseek-reasoner"),
        ]
        fake_response = _FakeStreamResponse(chunks)

        with patch.object(reasoning_llm.client, "create", return_value=fake_response):
            collected = list(reasoning_llm.stream([HumanMessage(content="求真理")]))

        # All yielded items should be AIMessageChunk (assistant role).
        assert all(isinstance(c, AIMessageChunk) for c in collected)

        # langchain 1.x may append a trailing empty terminator chunk
        # (chunk_position='last'); filter to chunks carrying actual payload.
        reasoning_chunks = [
            c for c in collected
            if c.additional_kwargs.get("reasoning_content")
        ]
        content_chunks = [c for c in collected if c.content]

        # Both reasoning chunks must be captured in order.
        assert len(reasoning_chunks) == 2
        assert reasoning_chunks[0].additional_kwargs["reasoning_content"] == "第一步：分析问题"
        assert reasoning_chunks[1].additional_kwargs["reasoning_content"] == "第二步：推导结论"

        # The content chunk must carry the final answer text intact, and must
        # NOT carry a spurious reasoning_content.
        assert len(content_chunks) == 1
        assert content_chunks[0].content == "答案是 42"
        assert content_chunks[0].additional_kwargs.get("reasoning_content", "") == ""

    def test_non_reasoning_model_passthrough(self, reasoning_llm):
        """Normal model chunks (no reasoning fields) must stream unmodified.

        The override must not break the normal flow when reasoning_content is
        absent — `additional_kwargs` should not gain a spurious
        `reasoning_content` key, and content must pass through intact.
        """
        chunks = [
            _make_content_chunk("hello ", model="gpt-4o-mini"),
            _make_content_chunk("world", model="gpt-4o-mini"),
        ]
        fake_response = _FakeStreamResponse(chunks)

        with patch.object(reasoning_llm.client, "create", return_value=fake_response):
            collected = list(reasoning_llm.stream([HumanMessage(content="hi")]))

        contents = "".join(c.content for c in collected if isinstance(c, AIMessageChunk))
        assert contents == "hello world"

        # No chunk should carry a non-empty reasoning_content.
        for c in collected:
            if isinstance(c, AIMessageChunk):
                assert c.additional_kwargs.get("reasoning_content", "") == ""

    def test_convert_chunk_signature_compatible(self):
        """Override signature must match langchain-openai 1.2.2 base class.

        If upstream renames/reorders params, Python still accepts the method
        override but param binding breaks at call time — silently disabling
        reasoning extraction. This test guards against that regression.
        """
        base_sig = inspect.signature(ChatOpenAI._convert_chunk_to_generation_chunk)
        sub_sig = inspect.signature(ReasoningChatOpenAI._convert_chunk_to_generation_chunk)

        base_params = list(base_sig.parameters.keys())
        sub_params = list(sub_sig.parameters.keys())
        assert sub_params == base_params, (
            f"Param name/order mismatch: base={base_params} sub={sub_params}"
        )

        # Param kinds (POSITIONAL_OR_KEYWORD, etc.) must match per param.
        for name in base_params:
            base_kind = base_sig.parameters[name].kind
            sub_kind = sub_sig.parameters[name].kind
            assert base_kind == sub_kind, (
                f"Param '{name}' kind mismatch: base={base_kind} sub={sub_kind}"
            )

        # Return annotation must be compatible. With `from __future__ import
        # annotations` annotations may be strings, so stringify both sides.
        base_ret = str(base_sig.return_annotation)
        sub_ret = str(sub_sig.return_annotation)
        assert "ChatGenerationChunk" in base_ret, (
            f"Unexpected base return annotation: {base_ret}"
        )
        assert "ChatGenerationChunk" in sub_ret, (
            f"Override return annotation lost ChatGenerationChunk: {sub_ret}"
        )
