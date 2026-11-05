"""Server-authoritative request history assembly.

These are implementation tests. They are written before the unified isolated
verification pass and are intentionally not executed in this phase.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.api import context_history


@dataclass
class _PayloadMessage:
    role: str
    content: object
    message_id: str | None = None


def _identity(value):
    return value


def test_server_history_appends_only_the_active_turn(monkeypatch):
    monkeypatch.setattr(
        context_history,
        "_persisted_rows",
        lambda session_id, user: [("user", "q1", "m1"), ("assistant", "a1", "m2")],
    )
    assembled = context_history.assemble_server_history(
        "s1",
        {"anonymous": False},
        [_PayloadMessage("user", "q2", "m3")],
        normalize_content=_identity,
    )
    assert assembled is not None
    assert assembled.messages == (
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    )
    assert assembled.source_message_ids == ("m1", "m2", None)
    assert assembled.transient_start == 2


def test_server_history_keeps_quote_system_messages_transient(monkeypatch):
    monkeypatch.setattr(
        context_history,
        "_persisted_rows",
        lambda session_id, user: [("user", "q1", "m1"), ("assistant", "a1", "m2")],
    )
    assembled = context_history.assemble_server_history(
        "s1",
        {"anonymous": False},
        [
            _PayloadMessage("system", "quoted text"),
            _PayloadMessage("user", "q2", "m3"),
        ],
        normalize_content=_identity,
    )
    assert assembled is not None
    assert [message["role"] for message in assembled.messages] == [
        "user", "assistant", "system", "user",
    ]
    assert assembled.source_message_ids == ("m1", "m2", None, None)


def test_server_history_reuses_an_already_saved_last_user_turn(monkeypatch):
    monkeypatch.setattr(
        context_history,
        "_persisted_rows",
        lambda session_id, user: [("user", "q1", "m1"), ("assistant", "a1", "m2"),
                                  ("user", "q2", "m3")],
    )
    assembled = context_history.assemble_server_history(
        "s1",
        {"anonymous": False},
        [_PayloadMessage("user", "q2", "m3")],
        normalize_content=_identity,
    )
    assert assembled is not None
    assert assembled.messages[-1] == {"role": "user", "content": "q2"}
    assert assembled.source_message_ids == ("m1", "m2", "m3")
    assert assembled.transient_start == 3


def test_server_history_rejects_an_already_answered_turn(monkeypatch):
    monkeypatch.setattr(
        context_history,
        "_persisted_rows",
        lambda session_id, user: [("user", "q1", "m1"), ("assistant", "a1", "m2")],
    )
    assembled = context_history.assemble_server_history(
        "s1",
        {"anonymous": False},
        [_PayloadMessage("user", "q1", "m1")],
        normalize_content=_identity,
    )
    assert assembled is None


def test_server_history_rejects_an_unverified_owner(monkeypatch):
    monkeypatch.setattr(context_history, "_persisted_rows", lambda session_id, user: None)
    assembled = context_history.assemble_server_history(
        "s1",
        {"anonymous": True},
        [_PayloadMessage("user", "q1", "m1")],
        normalize_content=_identity,
    )
    assert assembled is None


def test_graph_source_ids_match_only_the_durable_tail(monkeypatch):
    monkeypatch.setattr(
        context_history,
        "_persisted_rows",
        lambda session_id, user: [("user", "q1", "m1"), ("assistant", "a1", "m2")],
    )

    class _Message:
        def __init__(self, type_, content):
            self.type = type_
            self.content = content

    messages = [
        _Message("system", "summary"),
        _Message("human", "q1"),
        _Message("assistant", "a1"),
        _Message("ai", "unpersisted current text"),
        _Message("tool", "tool output"),
    ]
    assert context_history.build_graph_message_source_ids(
        "s1", {"anonymous": False}, messages,
    ) == (None, "m1", "m2", None, None)
