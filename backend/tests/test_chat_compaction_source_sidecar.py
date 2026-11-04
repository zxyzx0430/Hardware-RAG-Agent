"""Route-side identity checks for compaction source sidecars."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import chat_routes, compaction_archive, compaction_checkpoint, context_events
from src.agent.compact import autocompact, checkpoint_publish
from src.agent.request_budget import AgentRequestBudgetError


def _payload(messages: list[dict]) -> chat_routes.ChatRequest:
    return chat_routes.ChatRequest(
        messages=[chat_routes.ChatMessageSchema(**message) for message in messages],
    )


def _projection(payload: chat_routes.ChatRequest) -> list[dict]:
    return [
        {"role": message.role, "content": message.content}
        for message in payload.messages
    ]


def test_quote_and_current_user_are_one_transient_suffix():
    payload = _payload([
        {"role": "user", "content": "first question", "message_id": "user-1"},
        {"role": "assistant", "content": "first answer", "message_id": "assistant-1"},
        {"role": "system", "content": "first quote"},
        {"role": "system", "content": "second quote"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])

    sidecar = chat_routes._build_compaction_source_message_ids(
        payload, _projection(payload), transient_current_user=True,
    )
    resume_sidecar = chat_routes._build_compaction_source_message_ids(
        payload, _projection(payload), transient_current_user=False,
    )

    assert sidecar == ("user-1", "assistant-1", None, None, None)
    assert resume_sidecar == ("user-1", "assistant-1", None, None, None)


def test_resume_without_quote_keeps_verified_current_user_id():
    payload = _payload([
        {"role": "user", "content": "first question", "message_id": "user-1"},
        {"role": "assistant", "content": "first answer", "message_id": "assistant-1"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])

    assert chat_routes._build_compaction_source_message_ids(
        payload, _projection(payload), transient_current_user=False,
    ) == ("user-1", "assistant-1", "user-current")


def test_published_history_reader_receives_persisted_prefix_only(monkeypatch):
    payload = _payload([
        {"role": "user", "content": "first question", "message_id": "user-1"},
        {"role": "assistant", "content": "first answer", "message_id": "assistant-1"},
        {"role": "system", "content": "quoted text"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])
    scope = context_events.ContextEventScope(
        session_id="session-1",
        request_id="request-1",
        principal_hash="principal-1",
        session_epoch="epoch-1",
    )
    snapshot = type("Snapshot", (), {
        "skills_mode": "off",
        "context_event_scope": scope,
        "principal_hash": scope.principal_hash,
        "business_session_id": scope.session_id,
        "request_id": scope.request_id,
        "session_delete_epoch": scope.session_epoch,
    })()
    incoming = _projection(payload)
    received_by_owner_reader: list[list[dict]] = []

    monkeypatch.setattr(compaction_archive, "get_compaction_scope", lambda: scope)

    async def capture_owner_reader(_operation, _scope, messages, *, skills_mode):
        assert skills_mode == "off"
        received_by_owner_reader.append(messages)
        return None

    monkeypatch.setattr(
        checkpoint_publish,
        "_run_host_record_transaction",
        capture_owner_reader,
    )

    admitted, sidecar = asyncio.run(chat_routes._admit_published_history(
        snapshot,
        payload,
        incoming,
        source_message_ids=("user-1", "assistant-1", None, None),
    ))

    assert admitted is incoming
    assert sidecar == ("user-1", "assistant-1", None, None)
    assert received_by_owner_reader == [incoming[:2]]


def test_system_quote_in_persistent_history_middle_fails_closed():
    payload = _payload([
        {"role": "user", "content": "first question", "message_id": "user-1"},
        {"role": "system", "content": "quote in the middle"},
        {"role": "assistant", "content": "first answer", "message_id": "assistant-1"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])

    assert chat_routes._build_compaction_source_message_ids(
        payload, _projection(payload), transient_current_user=True,
    ) is None
    assert chat_routes._build_compaction_source_message_ids(
        payload, _projection(payload), transient_current_user=False,
    ) is None

    identified_quote = _payload([
        {"role": "user", "content": "first question", "message_id": "user-1"},
        {"role": "system", "content": "quoted text", "message_id": "quote-id"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])
    assert chat_routes._build_compaction_source_message_ids(
        identified_quote, _projection(identified_quote), transient_current_user=True,
    ) is None


def test_projection_mismatch_id_hole_and_multimodal_input_fail_closed():
    payload = _payload([
        {"role": "user", "content": "first question", "message_id": "user-1"},
        {"role": "assistant", "content": "first answer", "message_id": "assistant-1"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])
    mismatched_projection = _projection(payload)
    mismatched_projection[1] = {"role": "assistant", "content": "different answer"}
    assert chat_routes._build_compaction_source_message_ids(
        payload, mismatched_projection, transient_current_user=True,
    ) is None
    mismatched_role = _projection(payload)
    mismatched_role[1]["role"] = "user"
    assert chat_routes._build_compaction_source_message_ids(
        payload, mismatched_role, transient_current_user=True,
    ) is None
    reordered_projection = _projection(payload)
    reordered_projection[:2] = reversed(reordered_projection[:2])
    assert chat_routes._build_compaction_source_message_ids(
        payload, reordered_projection, transient_current_user=True,
    ) is None

    missing_id = _payload([
        {"role": "user", "content": "unidentified history"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])
    assert chat_routes._build_compaction_source_message_ids(
        missing_id, _projection(missing_id), transient_current_user=True,
    ) is None
    duplicate_ids = _payload([
        {"role": "user", "content": "first question", "message_id": "duplicate"},
        {"role": "assistant", "content": "first answer", "message_id": "duplicate"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])
    assert chat_routes._build_compaction_source_message_ids(
        duplicate_ids, _projection(duplicate_ids), transient_current_user=True,
    ) is None

    multimodal = _payload([
        {"role": "user", "content": [{"type": "text", "text": "image context"}], "message_id": "user-1"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])
    assert chat_routes._build_compaction_source_message_ids(
        multimodal, _projection(multimodal), transient_current_user=True,
    ) is None


def test_pre_send_compaction_preserves_quote_suffix_after_admitted_checkpoint(monkeypatch):
    from langchain_core.messages import SystemMessage

    payload = _payload([
        {"role": "user", "content": "first question", "message_id": "user-1"},
        {"role": "assistant", "content": "first answer", "message_id": "assistant-1"},
        {"role": "system", "content": "quoted text"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])
    marker = {"test_checkpoint": True}

    def retain_test_checkpoint(metadata):
        return marker if type(metadata) is dict and metadata.get("test_checkpoint") else {}

    monkeypatch.setattr(compaction_checkpoint, "checkpoint_metadata_for_transport", retain_test_checkpoint)
    monkeypatch.setattr(chat_routes, "_session_context_window", lambda _session_id, _model=None: 4096)
    should_calls = 0

    def should_compact_once(_messages, _threshold):
        nonlocal should_calls
        should_calls += 1
        # One pre-loop trigger and one loop-entry check; stop after the first
        # generation so the loop can exit without a second compaction.
        return should_calls <= 2

    monkeypatch.setattr(autocompact, "should_autocompact", should_compact_once)
    observed_source_ids: list[tuple[str | None, ...]] = []

    async def compact_messages(
        messages, _creds, _context_window, *, source_message_ids, token_threshold=None,
    ):
        observed_source_ids.append(source_message_ids)
        return [
            SystemMessage(content="new checkpoint", additional_kwargs=marker),
            *messages[-3:],
        ]

    monkeypatch.setattr(chat_routes, "_run_autocompact", compact_messages)
    agent_input = {
        "messages": [
            {"role": "system", "content": "verified checkpoint", "additional_kwargs": marker},
            {"role": "assistant", "content": "first answer"},
            {"role": "system", "content": "quoted text"},
            {"role": "user", "content": "follow-up"},
        ]
    }

    source_ids_out = [None]

    async def collect_events():
        return [
            event async for event in chat_routes._check_and_compact_history(
                payload,
                {},
                agent_input,
                allow_compaction=True,
                source_message_ids=(None, "assistant-1", None, None),
                source_message_ids_out=source_ids_out,
            )
        ]

    events = asyncio.run(collect_events())
    assert len(events) == 1
    assert observed_source_ids == [(None, "assistant-1", None, None)]
    assert source_ids_out == [(None, "assistant-1", None, None)]


def test_pre_send_checkpoint_cannot_absorb_quote_slot(monkeypatch):
    payload = _payload([
        {"role": "user", "content": "persisted", "message_id": "user-1"},
        {"role": "system", "content": "first quote"},
        {"role": "system", "content": "second quote"},
        {"role": "user", "content": "follow-up", "message_id": "user-current"},
    ])
    marker = {"test_checkpoint": True}
    monkeypatch.setattr(
        compaction_checkpoint,
        "checkpoint_metadata_for_transport",
        lambda metadata: marker if type(metadata) is dict and metadata.get("test_checkpoint") else {},
    )
    monkeypatch.setattr(chat_routes, "_session_context_window", lambda _session_id, _model=None: 4096)
    monkeypatch.setattr(autocompact, "should_autocompact", lambda _messages, _threshold: True)
    agent_input = {
        "messages": [
            {"role": "system", "content": "verified checkpoint", "additional_kwargs": marker},
            {"role": "system", "content": "second quote"},
            {"role": "user", "content": "follow-up"},
        ]
    }

    async def collect_events():
        return [
            event async for event in chat_routes._check_and_compact_history(
                payload,
                {},
                agent_input,
                allow_compaction=True,
                source_message_ids=(None, None, None),
            )
        ]

    with pytest.raises(AgentRequestBudgetError):
        asyncio.run(collect_events())
