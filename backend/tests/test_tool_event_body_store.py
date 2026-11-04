import json

import pytest
from langchain_core.messages import ToolMessage

from app.api import tool_events
from app.api.tool_observations import make_tool_observation_scope


_REFERENCE = "R" * 43


@pytest.fixture
def scope():
    return make_tool_observation_scope(
        principal_id="a" * 64,
        session_id="local-session",
        request_id="b" * 32,
        session_delete_epoch="",
    )


def _pair(host_call_id="audit-call-1", tool_call_id="model-call-1", name="search_docs"):
    return tool_events.ExpectedToolCall(
        host_call_id=host_call_id,
        tool_call_id=tool_call_id,
        tool_name=name,
    )


def _capture(
    content,
    *,
    name="search_docs",
    tool_call_id="model-call-1",
    category="builtin_non_command",
    status="success",
):
    return tool_events.ToolMessageCapture(
        # ToolMessage() coerces a dict content into its str repr. Archive
        # drops must round-trip the payload exactly, so build without that
        # lossy validation pass.
        tool_message=ToolMessage.model_construct(
            content=content,
            name=name,
            tool_call_id=tool_call_id,
            status=status,
        ),
        tool_instance_id="tool-instance-0001",
        tool_contract_version="1",
        tool_category=category,
    )


def _patch_store(monkeypatch, scope):
    archived = {}

    def archive(envelope, tool_name, actual_scope):
        assert tool_name == tool_events._ARCHIVE_TOOL_NAME
        assert actual_scope is scope
        archived["envelope"] = envelope
        return _REFERENCE

    def read(reference, actual_scope):
        if (
            reference != _REFERENCE
            or type(actual_scope) is not type(scope)
            or actual_scope.principal_id != scope.principal_id
            or actual_scope.session_id != scope.session_id
            or actual_scope.session_delete_epoch != scope.session_delete_epoch
        ):
            return None
        return archived.get("envelope")

    monkeypatch.setattr(tool_events, "archive_tool_observation", archive)
    monkeypatch.setattr(tool_events, "read_tool_observation", read)
    return archived


def test_adapter_preserves_native_content_pair_and_return_semantics(monkeypatch, scope):
    archived = _patch_store(monkeypatch, scope)
    content = ["原样文本\n", {"type": "text", "text": "私有知识库片段"}]

    written = tool_events._archive_tool_message_batch(
        scope,
        [_pair()],
        [_capture(content)],
    )

    assert written.status == "STORED"
    assert written.reference == _REFERENCE
    envelope = json.loads(archived["envelope"])
    assert envelope["schema_version"] == 3
    assert envelope["scope"] == {
        "principal_id": scope.principal_id,
        "session_id": scope.session_id,
        "origin_request_id": scope.request_id,
        "delete_epoch": scope.session_delete_epoch,
    }
    item = envelope["messages"][0]
    assert item["tool_message"]["content"] == content
    assert item["pair"] == {
        "host_call_id": "audit-call-1",
        "tool_call_id": "model-call-1",
        "tool_name": "search_docs",
    }
    assert item["tool_instance_id"] == "tool-instance-0001"
    assert item["tool_contract_version"] == "1"
    assert item["return_state"] == "returned"
    assert not {"args", "command", "approved", "completed"}.intersection(item)

    restored = tool_events._read_tool_message_batch(
        _REFERENCE,
        scope,
        [_pair()],
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )
    assert restored.status == "AVAILABLE"
    assert len(restored.messages) == 1
    assert restored.messages[0].content == content
    assert restored.messages[0].name == "search_docs"
    assert restored.messages[0].tool_call_id == "model-call-1"


def test_writer_requires_complete_exact_ordered_pairs(monkeypatch, scope):
    calls = []
    monkeypatch.setattr(
        tool_events,
        "archive_tool_observation",
        lambda *args: calls.append(args) or _REFERENCE,
    )

    result = tool_events._archive_tool_message_batch(
        scope,
        [_pair(), _pair("audit-call-2", "model-call-2", "list_kb_docs")],
        [_capture("only one result")],
    )

    assert result.status == "INCOMPLETE_BATCH"
    assert result.reference is None
    assert calls == []


@pytest.mark.parametrize("category", ["mcp", "command", "skills"])
@pytest.mark.parametrize("status", ["success", "error"])
def test_writer_round_trips_source_category_and_tool_message_status(
    monkeypatch,
    scope,
    category,
    status,
):
    archived = _patch_store(monkeypatch, scope)

    written = tool_events._archive_tool_message_batch(
        scope,
        [_pair()],
        [_capture({"private_result": "allowed locally"}, category=category, status=status)],
    )

    assert written.status == "STORED"
    envelope = json.loads(archived["envelope"])
    item = envelope["messages"][0]
    assert item["tool_category"] == category
    assert item["tool_message"]["status"] == status
    assert item["return_state"] == "returned"
    assert not {"approved", "completed", "effect_persisted"}.intersection(item)

    restored = tool_events._read_tool_message_batch(
        _REFERENCE,
        scope,
        [_pair()],
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )
    assert restored.status == "AVAILABLE"
    assert restored.messages[0].status == status
    assert restored.messages[0].content == {"private_result": "allowed locally"}


def test_writer_rejects_unknown_category_and_non_tool_message(monkeypatch, scope):
    calls = []
    monkeypatch.setattr(
        tool_events,
        "archive_tool_observation",
        lambda *args: calls.append(args) or _REFERENCE,
    )

    unknown_category = tool_events._archive_tool_message_batch(
        scope, [_pair()], [_capture("private", category="unknown")],
    )
    non_tool_message = tool_events._archive_tool_message_batch(
        scope,
        [_pair()],
        [tool_events.ToolMessageCapture(
            tool_message={"content": "not a ToolMessage"},
            tool_instance_id="tool-instance-0001",
            tool_contract_version="1",
            tool_category="mcp",
        )],
    )

    assert unknown_category.status == "INVALID_TOOL_CATEGORY"
    assert non_tool_message.status == "UNSUPPORTED_MESSAGE"
    assert unknown_category.reference is non_tool_message.reference is None
    assert calls == []


def test_oversize_envelope_is_rejected_before_existing_store(monkeypatch, scope):
    calls = []
    monkeypatch.setattr(
        tool_events,
        "archive_tool_observation",
        lambda *args: calls.append(args) or _REFERENCE,
    )

    result = tool_events._archive_tool_message_batch(
        scope,
        [_pair()],
        [_capture("x" * tool_events.MAX_TOOL_OBSERVATION_BYTES)],
    )

    assert result.status == "CAPACITY"
    assert result.reference is None
    assert calls == []


def test_read_is_all_or_nothing_for_pair_mismatch_and_budget(monkeypatch, scope):
    _patch_store(monkeypatch, scope)
    written = tool_events._archive_tool_message_batch(
        scope, [_pair()], [_capture({"snippet": "kept exactly"})],
    )
    assert written.status == "STORED"

    mismatch = tool_events._read_tool_message_batch(
        _REFERENCE,
        scope,
        [_pair(tool_call_id="wrong-call")],
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )
    over_budget = tool_events._read_tool_message_batch(
        _REFERENCE,
        scope,
        [_pair()],
        byte_budget=1,
    )

    assert mismatch.status == "PAIR_OR_ORDER_MISMATCH"
    assert mismatch.messages == ()
    assert over_budget.status == "OVER_BUDGET"
    assert over_budget.messages == ()


def test_reader_allows_new_request_with_same_principal_session_and_epoch(monkeypatch, scope):
    _patch_store(monkeypatch, scope)
    written = tool_events._archive_tool_message_batch(
        scope, [_pair()], [_capture({"saved": "from an earlier request"})],
    )
    assert written.status == "STORED"
    current_scope = make_tool_observation_scope(
        principal_id=scope.principal_id,
        session_id=scope.session_id,
        request_id="c" * 32,
        session_delete_epoch=scope.session_delete_epoch,
    )

    restored = tool_events._read_tool_message_batch(
        _REFERENCE,
        current_scope,
        [_pair()],
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )

    assert restored.status == "AVAILABLE"
    assert restored.messages[0].content == {"saved": "from an earlier request"}


@pytest.mark.parametrize("mismatch", ["principal", "session", "epoch"])
def test_reader_still_rejects_owner_session_or_epoch_mismatch(monkeypatch, scope, mismatch):
    _patch_store(monkeypatch, scope)
    written = tool_events._archive_tool_message_batch(
        scope, [_pair()], [_capture("scoped result")],
    )
    assert written.status == "STORED"
    values = {
        "principal_id": scope.principal_id,
        "session_id": scope.session_id,
        "request_id": "c" * 32,
        "session_delete_epoch": scope.session_delete_epoch,
    }
    if mismatch == "principal":
        values["principal_id"] = "d" * 64
    elif mismatch == "session":
        values["session_id"] = "other-session"
    else:
        values["session_delete_epoch"] = "e" * 32
    current_scope = make_tool_observation_scope(**values)

    restored = tool_events._read_tool_message_batch(
        _REFERENCE,
        current_scope,
        [_pair()],
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )

    assert restored.status == "UNAVAILABLE"
    assert restored.messages == ()


def test_reader_rejects_invalid_origin_request_id(monkeypatch, scope):
    archived = _patch_store(monkeypatch, scope)
    written = tool_events._archive_tool_message_batch(
        scope, [_pair()], [_capture("body")],
    )
    assert written.status == "STORED"
    envelope = json.loads(archived["envelope"])
    envelope["scope"]["origin_request_id"] = "not-a-request-id"
    archived["envelope"] = tool_events._compact_json(envelope)

    restored = tool_events._read_tool_message_batch(
        _REFERENCE,
        scope,
        [_pair()],
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )

    assert restored.status == "INVALID_ORIGIN_REQUEST"
    assert restored.messages == ()


def test_reader_rejects_origin_scope_change_through_chain_digest(monkeypatch, scope):
    archived = _patch_store(monkeypatch, scope)
    written = tool_events._archive_tool_message_batch(
        scope, [_pair()], [_capture("body")],
    )
    assert written.status == "STORED"
    envelope = json.loads(archived["envelope"])
    envelope["scope"]["origin_request_id"] = "c" * 32
    archived["envelope"] = tool_events._compact_json(envelope)

    restored = tool_events._read_tool_message_batch(
        _REFERENCE,
        scope,
        [_pair()],
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )

    assert restored.status == "DIGEST_MISMATCH"
    assert restored.messages == ()


def test_reader_requires_host_expected_pairs(monkeypatch, scope):
    reads = []
    monkeypatch.setattr(
        tool_events,
        "read_tool_observation",
        lambda *args: reads.append(args) or "{}",
    )

    restored = tool_events._read_tool_message_batch(
        _REFERENCE,
        scope,
        [],
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )

    assert restored.status == "INVALID_BATCH"
    assert restored.messages == ()
    assert reads == []


def test_reader_returns_no_prefix_when_later_body_digest_is_wrong(monkeypatch, scope):
    archived = _patch_store(monkeypatch, scope)
    pairs = [
        _pair(),
        _pair("audit-call-2", "model-call-2", "list_kb_docs"),
    ]
    captures = [
        _capture("first"),
        _capture("second", name="list_kb_docs", tool_call_id="model-call-2"),
    ]
    written = tool_events._archive_tool_message_batch(scope, pairs, captures)
    assert written.status == "STORED"

    envelope = json.loads(archived["envelope"])
    envelope["messages"][1]["tool_message"]["content"] = "tampered"
    archived["envelope"] = tool_events._compact_json(envelope)

    restored = tool_events._read_tool_message_batch(
        _REFERENCE,
        scope,
        pairs,
        byte_budget=tool_events.MAX_TOOL_OBSERVATION_BYTES,
    )

    assert restored.status == "DIGEST_MISMATCH"
    assert restored.messages == ()


def test_json_native_serializer_rejects_objects_and_cycles_without_string_fallback():
    assert tool_events._compact_json({"text": "原样"}) == '{"text":"原样"}'
    assert tool_events._compact_json({"unsupported": object()}) is None
    cyclic = []
    cyclic.append(cyclic)
    assert tool_events._compact_json(cyclic) is None
