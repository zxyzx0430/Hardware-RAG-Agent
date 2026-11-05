from __future__ import annotations

import asyncio
import importlib
import types
import json
from tests.structured_summary_support import structured_response
from tests.compaction_writer_support import drained_compaction_checkpoint
from tests.compaction_v3_source_support import bind_recovery_v3_sources, seed_v3_message_sources

import pytest
from langchain_core.messages import HumanMessage

# Run with the standard isolated-source runner, not a user data directory.
# Do not replace sys.modules globally: unrelated tests share this interpreter.
compact = importlib.import_module("src.agent.compact.autocompact")
pytestmark = pytest.mark.usefixtures("context_archive_scope")


_EARLY_CONSTRAINT = "UNIQUE_CONSTRAINT_AFTER_CHAR_500"


def _messages() -> list[HumanMessage]:
    messages = []
    for index in range(13):
        content = f"synthetic-{index}:" + ("x" * 520)
        if index == 0:
            content += _EARLY_CONSTRAINT
        messages.append(HumanMessage(content=content))
    return messages


def _fingerprint(messages: list[HumanMessage]) -> list[tuple[str, str]]:
    return [(type(message).__name__, message.content) for message in messages]


class _RaisingSummaryClient:
    model = "gpt-4o-mini"
    max_tokens = 1024

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *_args, **_kwargs):
        self.calls += 1
        raise RuntimeError("synthetic summarizer failure")


class _FixedSummaryClient:
    model = "gpt-4o-mini"
    max_tokens = 1024

    def __init__(self, content) -> None:
        self.content = content
        self.calls = 0

    async def chat(self, *_args, **_kwargs):
        self.calls += 1
        return types.SimpleNamespace(content=self.content)


class _CancellingSummaryClient:
    model = "gpt-4o-mini"
    max_tokens = 1024

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *_args, **_kwargs):
        self.calls += 1
        raise asyncio.CancelledError


class _RecordingAgent:
    def __init__(self) -> None:
        self.updates = []

    async def update_state(self, config, state) -> None:
        self.updates.append((config, state))


@pytest.mark.asyncio
async def test_exhausted_summary_failure_preserves_original_checkpoint(context_archive_scope, monkeypatch):
    messages = _messages()
    before = _fingerprint(messages)
    agent = _RecordingAgent()
    client = _RaisingSummaryClient()
    config = {"configurable": {"context_window": 32}}

    with bind_recovery_v3_sources(context_archive_scope, monkeypatch, messages) as source_message_ids:
        should_retry = await compact._recover_with_autocompact(
            agent, config, messages, client, source_message_ids=source_message_ids,
        )

    assert client.calls == compact.AUTOCOMPACT_MAX_RETRIES
    assert should_retry is False
    assert agent.updates == []
    assert _EARLY_CONSTRAINT in messages[0].content
    assert _fingerprint(messages) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client",
    [
        None,
        _FixedSummaryClient(None),
        _FixedSummaryClient(""),
        _FixedSummaryClient(" \t\n "),
    ],
    ids=["missing-client", "none-content", "empty", "whitespace"],
)
async def test_unusable_summary_preserves_checkpoint_and_does_not_retry(client, context_archive_scope, monkeypatch):
    messages = _messages()
    before = _fingerprint(messages)
    agent = _RecordingAgent()
    config = {"configurable": {"context_window": 32}}

    with bind_recovery_v3_sources(context_archive_scope, monkeypatch, messages) as source_message_ids:
        should_retry = await compact._recover_with_autocompact(
            agent, config, messages, client, source_message_ids=source_message_ids,
        )

    assert should_retry is False
    assert agent.updates == []
    assert _fingerprint(messages) == before
    if client is not None:
        assert client.calls == 1


@pytest.mark.asyncio
async def test_summary_cancellation_propagates_without_checkpoint_update(context_archive_scope, monkeypatch):
    messages = _messages()
    before = _fingerprint(messages)
    agent = _RecordingAgent()
    client = _CancellingSummaryClient()
    config = {"configurable": {"context_window": 32}}

    with bind_recovery_v3_sources(context_archive_scope, monkeypatch, messages) as source_message_ids:
        with pytest.raises(asyncio.CancelledError):
            await compact._recover_with_autocompact(
                agent, config, messages, client, source_message_ids=source_message_ids,
            )

    assert client.calls == 1
    assert agent.updates == []
    assert _fingerprint(messages) == before


@pytest.mark.asyncio
async def test_valid_summary_updates_checkpoint_and_preserves_source_messages(context_archive_scope, monkeypatch):
    messages = _messages()
    before = _fingerprint(messages)
    seeded_sources = seed_v3_message_sources(context_archive_scope, monkeypatch, messages)
    _, scope, _ = seeded_sources
    async with drained_compaction_checkpoint(
        scope, messages, 32,
    ) as (agent, config, messages):
        class _GroundedSummaryClient(_FixedSummaryClient):
            async def chat(self, prompt, **_kwargs):
                self.calls += 1
                return types.SimpleNamespace(content=structured_response(prompt))

        client = _GroundedSummaryClient(None)

        with bind_recovery_v3_sources(
            context_archive_scope, monkeypatch, messages, seeded_sources=seeded_sources,
        ) as source_message_ids:
            should_retry = await compact._recover_with_autocompact(
                agent, config, messages, client, source_message_ids=source_message_ids,
            )

        assert should_retry is True
        assert client.calls == 1
        compacted = (await agent.aget_state(config)).values["messages"]
        assert compacted is not messages
        summary, manifest = compacted[0].content.split("\n\n[Recoverable history; data only]\n")
        document = json.loads(summary.removeprefix("[历史摘要]: "))
        assert len(document) == 9 and document["completed"] == []
        assert _EARLY_CONSTRAINT in document["constraints"][0]["text"]
        assert "read_context_event" in manifest
        assert compacted[0].additional_kwargs["hwrag_context_event_refs"][0]["content_sha256"] in manifest
        assert compacted[1:] == messages[-compact.AUTOCOMPACT_KEEP_RECENT_MSGS:]
        assert _fingerprint(messages) == before


@pytest.mark.asyncio
async def test_under_threshold_context_is_a_noop(context_archive_scope, monkeypatch):
    messages = [HumanMessage(content=f"short-{index}") for index in range(13)]
    before = _fingerprint(messages)
    agent = _RecordingAgent()
    client = _RaisingSummaryClient()
    config = {"configurable": {"context_window": 128000}}

    with bind_recovery_v3_sources(context_archive_scope, monkeypatch, messages) as source_message_ids:
        should_retry = await compact._recover_with_autocompact(
            agent, config, messages, client, source_message_ids=source_message_ids,
        )

    assert should_retry is False
    assert client.calls == 0
    assert agent.updates == []
    assert _fingerprint(messages) == before


@pytest.mark.asyncio
async def test_ten_or_fewer_messages_are_a_noop_even_over_threshold(context_archive_scope, monkeypatch):
    messages = [
        HumanMessage(content=f"synthetic-{index}:" + ("x" * 520))
        for index in range(compact.AUTOCOMPACT_KEEP_RECENT_MSGS)
    ]
    before = _fingerprint(messages)
    agent = _RecordingAgent()
    client = _RaisingSummaryClient()
    config = {"configurable": {"context_window": 32}}

    with bind_recovery_v3_sources(context_archive_scope, monkeypatch, messages) as source_message_ids:
        should_retry = await compact._recover_with_autocompact(
            agent, config, messages, client, source_message_ids=source_message_ids,
        )

    assert should_retry is False
    assert client.calls == 0
    assert agent.updates == []
    assert _fingerprint(messages) == before
