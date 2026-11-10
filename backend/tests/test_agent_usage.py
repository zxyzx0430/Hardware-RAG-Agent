from langchain_core.messages import AIMessageChunk

from src.agent.sse_adapter import _capture_usage


def _chunk(input_tokens: int, output_tokens: int, cached: int = 0) -> AIMessageChunk:
    return AIMessageChunk(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cached},
            "output_token_details": {"reasoning": 0},
        },
    )


def test_capture_usage_accumulates_calls_and_cache_read():
    state: dict = {}

    _capture_usage(_chunk(100, 20, cached=30), state)
    _capture_usage(_chunk(50, 5), state)

    assert state["usage"] == {
        "prompt_tokens": 150,
        "completion_tokens": 25,
        "total_tokens": 175,
        "cached_tokens": 30,
    }


def test_capture_usage_ignores_empty_metadata():
    state: dict = {}
    _capture_usage(AIMessageChunk(content="text"), state)
    assert "usage" not in state
