"""Mid-run compaction is refused until Agent checkpoint writers can drain."""

import asyncio
import json
from collections import Counter
from types import SimpleNamespace

import pytest

from src.agent.exceptions import ContextLimitError


def _decode_sse(event: str) -> dict:
    data_line = next(line for line in event.splitlines() if line.startswith("data: "))
    return json.loads(data_line[6:])


def test_midrun_compaction_refuses_without_side_effects_and_closes_stream(monkeypatch):
    from src.agent import sse_adapter

    observed = {
        "do_compact": 0,
        "get_state": 0,
        "update_state": 0,
        "continued_after_refusal": False,
        "closed": False,
    }

    async def forbidden_compaction(_state):
        observed["do_compact"] += 1
        raise AssertionError("mid-run refusal must not enter the compaction path")

    monkeypatch.setattr(sse_adapter, "_do_compact", forbidden_compaction)

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):
            try:
                yield "updates", {"agent": {"messages": []}}
                observed["continued_after_refusal"] = True
                yield "updates", {"agent": {"messages": []}}
            finally:
                observed["closed"] = True

        def get_state(self, _config):
            observed["get_state"] += 1
            return SimpleNamespace(values={"messages": []})

        async def update_state(self, *_args, **_kwargs):
            observed["update_state"] += 1

    agent = FakeAgent()
    state = {
        "agent": agent,
        "config": {},
        "needs_compact": True,
        "token_limit": 100,
    }
    original_state = dict(state)
    events: list[str] = []

    async def collect():
        token = sse_adapter._CUMULATIVE_TOKENS.set(137)
        try:
            async for event in sse_adapter._iter_agent_sse(
                agent, {}, {}, Counter(), {}, state,
            ):
                events.append(event)
        finally:
            sse_adapter._CUMULATIVE_TOKENS.reset(token)

    with pytest.raises(ContextLimitError) as caught:
        asyncio.run(collect())

    assert caught.value.cumulative == 137
    assert caught.value.limit == 100
    assert not any(_decode_sse(event)["type"] == "context_compressing" for event in events)
    assert events == []
    assert observed == {
        "do_compact": 0,
        "get_state": 0,
        "update_state": 0,
        "continued_after_refusal": False,
        "closed": True,
    }
    assert state == original_state


def test_pre_send_compaction_success_path_remains_available(monkeypatch):
    from app.api import chat_routes
    import src.agent.compact.autocompact as autocompact

    observed = {"run_autocompact": 0}
    original_messages = [{"role": "user", "content": "synthetic question"}]
    compacted_messages = [{"role": "assistant", "content": "synthetic summary"}]
    agent_input = {"messages": original_messages}

    monkeypatch.setattr(chat_routes, "_session_context_window", lambda _session_id: 100)
    monkeypatch.setattr(chat_routes, "_dicts_to_langchain_messages", lambda messages: messages)
    monkeypatch.setattr(autocompact, "should_autocompact", lambda *_args: True)
    monkeypatch.setattr(
        chat_routes,
        "_langchain_messages_to_dicts",
        lambda _messages: [{"role": "assistant", "content": "synthetic summary"}],
    )

    async def fake_autocompact(_messages, _creds, _context_window):
        observed["run_autocompact"] += 1
        return compacted_messages

    monkeypatch.setattr(chat_routes, "_run_autocompact", fake_autocompact)

    async def collect():
        return [
            event async for event in chat_routes._check_and_compact_history(
                SimpleNamespace(session_id="synthetic-session"),
                {"model": "synthetic-model"},
                agent_input,
            )
        ]

    events = asyncio.run(collect())

    assert [_decode_sse(event)["type"] for event in events] == ["context_compressing"]
    assert observed["run_autocompact"] == 1
    assert agent_input["messages"] == [{"role": "assistant", "content": "synthetic summary"}]
