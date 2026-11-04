import pytest
from langchain_core.messages import HumanMessage

import src.agent.compact.autocompact as autocompact
from src.agent.exceptions import ContextLimitError


def _use_deterministic_token_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autocompact, "MAX_TOKEN_RATIO", 1.0)
    monkeypatch.setattr(autocompact, "AUTOCOMPACT_KEEP_RECENT_MSGS", 2)
    monkeypatch.setattr(
        autocompact,
        "_msg_tokens",
        lambda message: len(str(getattr(message, "content", ""))),
    )


def _over_budget_messages() -> list[HumanMessage]:
    return [
        HumanMessage(content="x" * 40),
        HumanMessage(content="y"),
        HumanMessage(content="z"),
    ]


def _message_snapshot(messages: list) -> list[tuple[int, str]]:
    return [(id(message), message.content) for message in messages]


def _assert_messages_unchanged(messages: list, snapshot: list[tuple[int, str]]) -> None:
    assert _message_snapshot(messages) == snapshot


@pytest.mark.asyncio
async def test_short_over_budget_history_raises_without_dropping_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_deterministic_token_counts(monkeypatch)
    messages = [HumanMessage(content="x" * 12), HumanMessage(content="y" * 11)]
    snapshot = _message_snapshot(messages)

    with pytest.raises(ContextLimitError) as raised:
        await autocompact.autocompact_messages(messages, object(), context_window=20)

    assert raised.value.cumulative == 23
    assert raised.value.limit == 20
    _assert_messages_unchanged(messages, snapshot)


@pytest.mark.asyncio
async def test_unavailable_summarizer_raises_without_snipping_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_deterministic_token_counts(monkeypatch)
    messages = _over_budget_messages()
    snapshot = _message_snapshot(messages)

    with pytest.raises(ContextLimitError) as raised:
        await autocompact.autocompact_messages(messages, None, context_window=20)

    assert raised.value.cumulative == 42
    assert raised.value.limit == 20
    _assert_messages_unchanged(messages, snapshot)


@pytest.mark.asyncio
async def test_failed_summary_retries_then_raises_without_snipping_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_deterministic_token_counts(monkeypatch)
    messages = _over_budget_messages()
    snapshot = _message_snapshot(messages)
    calls = 0

    async def fail_summary(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("synthetic summarizer failure")

    monkeypatch.setattr(autocompact, "_call_summarizer", fail_summary)

    with pytest.raises(ContextLimitError):
        await autocompact.autocompact_messages(messages, object(), context_window=20)

    assert calls == autocompact.AUTOCOMPACT_MAX_RETRIES
    _assert_messages_unchanged(messages, snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("summary", ["", " \t\n"])
async def test_empty_or_whitespace_summary_raises_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    summary: str,
) -> None:
    _use_deterministic_token_counts(monkeypatch)
    messages = _over_budget_messages()
    snapshot = _message_snapshot(messages)

    async def return_summary(*_args, **_kwargs):
        return summary

    monkeypatch.setattr(autocompact, "_summarize_with_breaker", return_summary)

    with pytest.raises(ContextLimitError):
        await autocompact.autocompact_messages(messages, object(), context_window=20)

    _assert_messages_unchanged(messages, snapshot)


@pytest.mark.asyncio
async def test_summary_that_still_exceeds_budget_raises_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_deterministic_token_counts(monkeypatch)
    messages = _over_budget_messages()
    snapshot = _message_snapshot(messages)

    async def return_oversized_summary(*_args, **_kwargs):
        return "s" * 40

    monkeypatch.setattr(
        autocompact, "_summarize_with_breaker", return_oversized_summary,
    )

    with pytest.raises(ContextLimitError) as raised:
        await autocompact.autocompact_messages(messages, object(), context_window=20)

    assert raised.value.cumulative > raised.value.limit
    assert raised.value.limit == 20
    _assert_messages_unchanged(messages, snapshot)


@pytest.mark.asyncio
async def test_recovery_propagates_compaction_failure_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_deterministic_token_counts(monkeypatch)
    messages = _over_budget_messages()
    snapshot = _message_snapshot(messages)
    monkeypatch.setattr(autocompact, "_make_summary_client", lambda: None)
    fallback_calls: list[list] = []

    async def record_fallback(fallback_messages: list) -> str:
        fallback_calls.append(fallback_messages)
        return "incorrectly used fallback"

    monkeypatch.setattr(autocompact, "_try_fallback_model", record_fallback)

    class Agent:
        async def update_state(self, *_args, **_kwargs) -> None:
            raise AssertionError("state must not update after failed compaction")

    with pytest.raises(ContextLimitError):
        await autocompact.attempt_context_recovery(
            Agent(), {"configurable": {"context_window": 20}}, messages,
        )

    assert fallback_calls == []
    _assert_messages_unchanged(messages, snapshot)


@pytest.mark.asyncio
async def test_successful_summary_returns_compacted_history_that_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_deterministic_token_counts(monkeypatch)
    messages = _over_budget_messages()
    snapshot = _message_snapshot(messages)

    async def return_summary(*_args, **_kwargs):
        return "ok"

    monkeypatch.setattr(autocompact, "_summarize_with_breaker", return_summary)

    compacted = await autocompact.autocompact_messages(
        messages, object(), context_window=20,
    )

    assert compacted is not messages
    assert compacted[0].content.startswith(autocompact._SUMMARY_PREFIX)
    assert compacted[1:] == messages[-2:]
    assert sum(autocompact._msg_tokens(message) for message in compacted) <= 20
    _assert_messages_unchanged(messages, snapshot)
