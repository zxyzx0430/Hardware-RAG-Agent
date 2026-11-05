"""Regression tests for the Agent session context-window hard cap.

All model-registry, provider-payload, and database boundaries are faked. These
tests must not contact a model provider or write to a real database.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.api import crud
from app.db.models import Session as SessionModel
from src.agent import context_guard
from src.agent.context_guard import resolve_context_budget
from src.agent.exceptions import ContextLimitError
from src.agent.reasoning_chat import (
    ReasoningChatOpenAI,
    _serialize_provider_payload,
)


AGENT_CONTEXT_WINDOW_CAP = 262_144
ONE_MILLION_TOKENS = 1_048_576


@pytest.mark.parametrize(
    ("model_window", "requested_window", "expected_window"),
    [
        (ONE_MILLION_TOKENS, ONE_MILLION_TOKENS, AGENT_CONTEXT_WINDOW_CAP),
        (ONE_MILLION_TOKENS, AGENT_CONTEXT_WINDOW_CAP, AGENT_CONTEXT_WINDOW_CAP),
        (ONE_MILLION_TOKENS, 65_536, 65_536),
        (65_536, ONE_MILLION_TOKENS, 65_536),
        (ONE_MILLION_TOKENS, None, AGENT_CONTEXT_WINDOW_CAP),
    ],
)
def test_effective_agent_window_is_minimum_of_native_requested_and_cap(
    monkeypatch,
    model_window: int,
    requested_window: int | None,
    expected_window: int,
):
    monkeypatch.setattr(
        context_guard,
        "get_registered_context_window",
        lambda _model: model_window,
    )
    monkeypatch.setattr(
        context_guard,
        "get_registered_max_tokens",
        lambda _model: 4_096,
    )

    budget = resolve_context_budget(
        "synthetic-1m-model",
        context_window_override=requested_window,
        requested_max_tokens=4_096,
    )

    assert budget.model_window == model_window
    assert budget.effective_window == expected_window
    assert budget.effective_window <= AGENT_CONTEXT_WINDOW_CAP


@pytest.mark.parametrize(
    "invalid_window",
    [0, -1, True, 1_000_000.0, "1048576"],
)
def test_invalid_explicit_window_fails_closed(monkeypatch, invalid_window):
    monkeypatch.setattr(
        context_guard,
        "get_registered_context_window",
        lambda _model: ONE_MILLION_TOKENS,
    )
    monkeypatch.setattr(
        context_guard,
        "get_registered_max_tokens",
        lambda _model: 4_096,
    )

    with pytest.raises(ContextLimitError):
        resolve_context_budget(
            "synthetic-1m-model",
            context_window_override=invalid_window,
            requested_max_tokens=4_096,
        )


def test_final_wire_payload_guard_enforces_cap_before_provider_call(monkeypatch):
    monkeypatch.setattr(
        context_guard,
        "get_registered_context_window",
        lambda _model: ONE_MILLION_TOKENS,
    )
    monkeypatch.setattr(
        context_guard,
        "get_registered_max_tokens",
        lambda _model: 4_096,
    )
    budget = resolve_context_budget(
        "synthetic-1m-model",
        context_window_override=ONE_MILLION_TOKENS,
        requested_max_tokens=4_096,
    )
    uncapped_model_limit = int(
        (
            ONE_MILLION_TOKENS
            - 4_096
            - context_guard.AGENT_PROTOCOL_FRAMING_TOKEN_RESERVE
        )
        * context_guard.MAX_TOKEN_RATIO
    )
    wire_payload = {
        "model": "synthetic-1m-model",
        "max_tokens": 4_096,
        "messages": [
            {"role": "user", "content": "x" * (budget.token_limit + 1_024)}
        ],
    }
    payload_size = len(_serialize_provider_payload(wire_payload))
    assert payload_size > budget.token_limit
    assert payload_size <= uncapped_model_limit

    base_payload_calls: list[dict] = []

    def fake_base_payload(_self, _input, *, stop=None, **_kwargs):
        base_payload_calls.append(wire_payload)
        return wire_payload

    monkeypatch.setattr(ChatOpenAI, "_get_request_payload", fake_base_payload)
    llm = ReasoningChatOpenAI(
        api_key="not-a-real-key",
        model="synthetic-1m-model",
        base_url="https://provider.invalid/v1",
        max_tokens=4_096,
        context_window_override=ONE_MILLION_TOKENS,
    )
    provider_create = Mock()
    monkeypatch.setattr(llm.client, "create", provider_create)

    with pytest.raises(ContextLimitError):
        list(llm.stream([HumanMessage(content="synthetic request")]))

    assert base_payload_calls == [wire_payload]
    provider_create.assert_not_called()


class _FakeDB:
    """Small CRUD fake that never opens or writes a real database."""

    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.commits = 0

    def query(self, model):
        self._model = model
        return self

    def filter(self, *_criteria):
        return self

    def first(self):
        return self.row if self._model is SessionModel else None

    def add(self, row):
        self.added.append(row)

    def flush(self, *_args):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def in_transaction(self):
        return False

    def get_bind(self):
        return SimpleNamespace(
            dialect=SimpleNamespace(name="sqlite"),
        )

    def execute(self, *_args, **_kwargs):
        return None


def _session_row(context_window: int):
    return SimpleNamespace(
        id="session-test",
        title="test",
        model="synthetic-1m-model",
        project="",
        pinned=False,
        msg_count=0,
        branch_from_session_id=None,
        branch_from_message_id=None,
        context_window=context_window,
        created_at=None,
        updated_at=None,
    )


def test_create_session_caps_window_before_persisting_and_responding():
    db = _FakeDB()

    response = crud.create_session(
        crud.SessionCreate(context_window=ONE_MILLION_TOKENS),
        db,
        user=None,
    )

    assert db.added[0].context_window == AGENT_CONTEXT_WINDOW_CAP
    assert response["data"]["context_window"] == AGENT_CONTEXT_WINDOW_CAP


def test_put_and_patch_session_updates_cap_requested_windows():
    put_row = _session_row(65_536)
    put_db = _FakeDB(put_row)
    put_response = crud.update_session(
        "session-test",
        crud.SessionUpdate(context_window=ONE_MILLION_TOKENS),
        put_db,
        user=None,
    )

    assert put_row.context_window == AGENT_CONTEXT_WINDOW_CAP
    assert put_response["data"] == {"id": "session-test", "updated": True}

    patch_row = _session_row(65_536)
    patch_db = _FakeDB(patch_row)
    patch_response = crud.patch_session(
        "session-test",
        crud.SessionUpdate(context_window=ONE_MILLION_TOKENS),
        patch_db,
        user=None,
    )

    assert patch_row.context_window == AGENT_CONTEXT_WINDOW_CAP
    assert patch_response["data"]["context_window"] == AGENT_CONTEXT_WINDOW_CAP


def test_small_session_window_is_preserved():
    db = _FakeDB()
    response = crud.create_session(
        crud.SessionCreate(context_window=65_536),
        db,
        user=None,
    )

    assert db.added[0].context_window == 65_536
    assert response["data"]["context_window"] == 65_536


def test_legacy_large_row_is_capped_in_response_without_migration():
    row = _session_row(ONE_MILLION_TOKENS)

    serialized = crud._serialize_session(row)

    assert serialized["context_window"] == AGENT_CONTEXT_WINDOW_CAP
    assert row.context_window == ONE_MILLION_TOKENS


def test_patch_response_caps_legacy_row_without_rewriting_it():
    row = _session_row(ONE_MILLION_TOKENS)
    db = _FakeDB(row)

    response = crud.patch_session(
        "session-test",
        crud.SessionUpdate(title="renamed"),
        db,
        user=None,
    )

    assert response["data"]["context_window"] == AGENT_CONTEXT_WINDOW_CAP
    assert row.context_window == ONE_MILLION_TOKENS
