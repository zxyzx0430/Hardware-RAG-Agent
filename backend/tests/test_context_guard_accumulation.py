"""Cumulative token accounting must include dict tool results."""

from __future__ import annotations

import pytest

from src.agent import context_guard


def _counter() -> int:
    return context_guard._CUMULATIVE_TOKENS.get()


@pytest.fixture(autouse=True)
def _reset_counter():
    context_guard.reset_token_counter(0)
    yield
    context_guard.reset_token_counter(0)


def test_dict_tool_results_are_counted():
    context_guard.accumulate_tokens(
        {"output": "x" * 4000, "data": {"body": "y" * 4000}},
        {"token_limit": 100_000},
    )

    assert _counter() >= 3000


def test_text_chunks_still_counted():
    context_guard.accumulate_tokens("中文" * 1000, {"token_limit": 100_000})

    assert _counter() >= 1000


def test_zero_limit_is_a_noop():
    context_guard.accumulate_tokens({"output": "x" * 10_000}, {"token_limit": 0})

    assert _counter() == 0
