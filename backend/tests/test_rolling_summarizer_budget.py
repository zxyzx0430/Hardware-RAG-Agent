"""Rolling-summary request budget is compared in tokens, not bytes."""

from types import SimpleNamespace

from src.agent.compact.rolling_summarizer import _request_fits


class _FakeClient:
    def __init__(self, tokens: int):
        self.tokens = tokens

    def _request_limits(self):
        return "model-a", 1_000, 100

    def _message_input_tokens(self, _messages):
        return self.tokens


def _budget():
    return SimpleNamespace(
        model="model-a",
        context_window_tokens=1_000,
        output_reserve_tokens=100,
    )


def test_request_fits_uses_token_estimate():
    # 600 + 256 framing + 100 reserve = 956 tokens <= 1000
    assert _request_fits(_FakeClient(600), _budget(), "system", "user") is True


def test_request_rejects_token_estimate_over_window():
    # 700 + 256 + 100 = 1056 tokens > 1000
    assert _request_fits(_FakeClient(700), _budget(), "system", "user") is False
