"""Lossless model-visible message adapter for the rolling-history event store.

This module does not read storage, resolve attachments, execute tools, or grant
permissions. Callers retain the source-sequence map alongside appended refs.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.api.rolling_history import RollingHistoryEvent
    from src.agent.compact.rolling_window import SequencedMessage


_CODEC_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)


class RollingHistoryCodecError(ValueError):
    """A message or event cannot be represented without losing model-visible data."""


@dataclass(frozen=True, slots=True)
class EncodedRollingHistory:
    """One event per source message, with a parallel host-side sequence map.

    source_sequence_by_event_index[i] identifies the planner message that
    produced events[i]. The map is not embedded in model-visible content;
    callers must retain it next to committed event references when they need
    the original planner sequence after a later read.
    """

    events: tuple[dict[str, object], ...]
    source_sequence_by_event_index: tuple[int, ...]


def encode_rolling_history_messages(messages: Sequence[SequencedMessage]) -> EncodedRollingHistory:
    """Encode a complete contiguous planner suffix as one event per message.

    Supported payloads are text-only HumanMessage, AIMessage, and ToolMessage.
    User and assistant envelopes preserve provider-visible names. AI tool calls
    and tool results use versioned JSON envelopes because the event store has
    one event_type and one text content field. Provider-only transport and
    configuration data is never copied.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from src.agent.compact.rolling_window import SequencedMessage

    if type(messages) not in (list, tuple) or not messages:
        raise RollingHistoryCodecError("A nonempty complete sequenced message suffix is required")

    events: list[dict[str, object]] = []
    source_sequences: list[int] = []
    pair_records: list[tuple[str, object]] = []
    expected_sequence: int | None = None

    for item in messages:
        if type(item) is not SequencedMessage or type(item.sequence) is not int or item.sequence < 1:
            raise RollingHistoryCodecError("Every message must have a positive planner source sequence")
        if expected_sequence is not None and item.sequence != expected_sequence:
            raise RollingHistoryCodecError("Planner source messages must be contiguous and ordered")
        expected_sequence = item.sequence + 1

        message = item.message
        if type(message) not in (HumanMessage, AIMessage, ToolMessage):
            raise RollingHistoryCodecError("Only HumanMessage, AIMessage, and ToolMessage are supported")
        body = _require_text(message.content, "Message content")
        source_sequences.append(item.sequence)

        if type(message) is HumanMessage:
            additional_kwargs = _message_additional_kwargs(message, allowed_keys={"name"})
            name = _provider_message_name(message, additional_kwargs)
            events.append({
                "event_type": "user_text",
                "content": _canonical_json({
                    "codec_version": _CODEC_VERSION,
                    "content": body,
                    "name": name,
                }),
            })
            pair_records.append(("user_text", None))
            continue

        if type(message) is AIMessage:
            if type(message.invalid_tool_calls) is not list or message.invalid_tool_calls:
                raise RollingHistoryCodecError("AIMessage contains an unparsed or malformed tool call")
            additional_kwargs = _message_additional_kwargs(
                message,
                allowed_keys={"name", "reasoning_content", "tool_calls", "function_call", "audio"},
            )
            reasoning_content = additional_kwargs.get("reasoning_content")
            if reasoning_content is not None and type(reasoning_content) is not str:
                raise RollingHistoryCodecError("Host-only reasoning_content must be text")
            name = _provider_message_name(message, additional_kwargs)
            calls = _validated_tool_calls(message.tool_calls)
            if not calls and "tool_calls" in additional_kwargs:
                calls = _parse_raw_tool_calls(additional_kwargs["tool_calls"])
            elif not calls and "function_call" in additional_kwargs:
                raise RollingHistoryCodecError(
                    "Legacy function_call is provider-visible but has no call ID for strict result pairing"
                )
            # ReasoningChatOpenAI adds reasoning_content to additional_kwargs,
            # but the pinned ChatOpenAI message converter does not send it.
            # It is intentionally accepted and omitted from this model payload.
            if additional_kwargs.get("audio"):
                raise RollingHistoryCodecError(
                    "Provider-visible assistant audio is unsupported without a verified audio resolver"
                )
            if calls:
                content = _canonical_json({
                    "codec_version": _CODEC_VERSION,
                    "content": body,
                    "name": name,
                    "tool_calls": calls,
                })
                events.append({"event_type": "tool_call", "content": content})
                pair_records.append(("tool_call", calls))
            else:
                events.append({
                    "event_type": "assistant_text",
                    "content": _canonical_json({
                        "codec_version": _CODEC_VERSION,
                        "content": body,
                        "name": name,
                    }),
                })
                pair_records.append(("assistant_text", None))
            continue

        call_id = message.tool_call_id
        if type(call_id) is not str or not call_id:
            raise RollingHistoryCodecError("ToolMessage is missing its tool_call_id")
        _require_text(call_id, "Tool call ID")
        _message_additional_kwargs(message, allowed_keys={"name"})
        content = _canonical_json({
            "codec_version": _CODEC_VERSION,
            "content": body,
            "tool_call_id": call_id,
        })
        events.append({"event_type": "tool_result", "content": content})
        pair_records.append(("tool_result", call_id))

    _validate_tool_pairs(pair_records)
    return EncodedRollingHistory(tuple(events), tuple(source_sequences))


def decode_rolling_history_events(
    events: Sequence[RollingHistoryEvent],
    *,
    source_sequence_by_event_index: Sequence[int] | None = None,
) -> tuple[SequencedMessage, ...]:
    """Restore verified text events as planner-consumable typed messages.

    Events must come from the complete-event reader, not preview/chunk reads.
    The reader verifies scope and the event chain; this adapter independently
    validates each full payload hash, event category, envelope, and tool pair.
    If the caller retained the encoder's parallel source map, pass it here.
    Otherwise committed rolling-history event sequences become planner source
    sequences.
    """
    from app.api.rolling_history import RollingHistoryEvent, RollingHistoryReference
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from src.agent.compact.rolling_window import SequencedMessage

    if type(events) not in (list, tuple) or not events:
        raise RollingHistoryCodecError("A nonempty complete rolling-history event sequence is required")

    decoded: list[object] = []
    pair_records: list[tuple[str, object]] = []
    event_sequences: list[int] = []

    for event in events:
        if type(event) is not RollingHistoryEvent or type(event.reference) is not RollingHistoryReference:
            raise RollingHistoryCodecError("Only complete RollingHistoryEvent values are supported")
        reference = event.reference
        if (type(reference.sequence) is not int or reference.sequence < 1
                or type(reference.version) is not int or reference.version != 1
                or type(reference.content_sha256) is not str
                or _SHA256.fullmatch(reference.content_sha256) is None
                or type(reference.chain_sha256) is not str
                or _SHA256.fullmatch(reference.chain_sha256) is None):
            raise RollingHistoryCodecError("Rolling-history event reference is invalid")
        event_sequences.append(reference.sequence)

        if type(event.attachments) is not tuple:
            raise RollingHistoryCodecError("Rolling-history attachments must be verified references")
        if event.attachments:
            raise RollingHistoryCodecError(
                "Attachment restoration is blocked: no host-injected verified attachment resolver is connected"
            )
        if type(event.event_type) is not str or type(event.content) is not str:
            raise RollingHistoryCodecError("Rolling-history event type and content must be complete text fields")
        try:
            event.content.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise RollingHistoryCodecError("Rolling-history event content is not valid UTF-8") from exc

        model_payload = event.model_payload()
        if (type(model_payload) is not dict
                or set(model_payload) != {"event_type", "content", "attachments"}
                or model_payload["event_type"] != event.event_type
                or model_payload["content"] != event.content
                or model_payload["attachments"] != []):
            raise RollingHistoryCodecError("Rolling-history event has an incomplete model payload")
        if _sha256(_canonical_json(model_payload)) != reference.content_sha256:
            raise RollingHistoryCodecError("Rolling-history event content hash mismatch")

        if event.event_type == "user_text":
            payload = _decode_envelope(
                event.content,
                expected_keys={"codec_version", "content", "name"},
            )
            body = _require_text(payload["content"], "User message content")
            name = _optional_message_name(payload["name"], "User message name")
            message = HumanMessage(content=body, name=name)
            pair_records.append(("user_text", None))
        elif event.event_type == "assistant_text":
            payload = _decode_envelope(
                event.content,
                expected_keys={"codec_version", "content", "name"},
            )
            body = _require_text(payload["content"], "Assistant message content")
            name = _optional_message_name(payload["name"], "Assistant message name")
            message = AIMessage(content=body, name=name)
            pair_records.append(("assistant_text", None))
        elif event.event_type == "tool_call":
            payload = _decode_envelope(
                event.content,
                expected_keys={"codec_version", "content", "name", "tool_calls"},
            )
            body = _require_text(payload["content"], "Tool-call assistant content")
            name = _optional_message_name(payload["name"], "Tool-call assistant name")
            calls = _validated_tool_calls(payload["tool_calls"])
            if not calls:
                raise RollingHistoryCodecError("tool_call event must contain at least one complete tool call")
            try:
                message = AIMessage(content=body, name=name, tool_calls=calls)
            except (TypeError, ValueError) as exc:
                raise RollingHistoryCodecError("LangChain rejected the complete tool-call payload") from exc
            if _validated_tool_calls(message.tool_calls) != calls:
                raise RollingHistoryCodecError("LangChain did not preserve the complete tool-call payload")
            pair_records.append(("tool_call", calls))
        elif event.event_type == "tool_result":
            payload = _decode_envelope(
                event.content,
                expected_keys={"codec_version", "content", "tool_call_id"},
            )
            body = _require_text(payload["content"], "Tool-result content")
            call_id = payload["tool_call_id"]
            if type(call_id) is not str or not call_id:
                raise RollingHistoryCodecError("tool_result event is missing its tool_call_id")
            try:
                message = ToolMessage(content=body, tool_call_id=call_id)
            except (TypeError, ValueError) as exc:
                raise RollingHistoryCodecError("LangChain rejected the complete tool-result payload") from exc
            pair_records.append(("tool_result", call_id))
        else:
            raise RollingHistoryCodecError(
                f"Unsupported rolling-history event_type: {event.event_type}"
            )
        decoded.append(message)

    _require_contiguous_sequences(event_sequences, "Rolling-history event")
    _validate_tool_pairs(pair_records)

    source_sequences = event_sequences
    if source_sequence_by_event_index is not None:
        if (type(source_sequence_by_event_index) not in (list, tuple)
                or len(source_sequence_by_event_index) != len(events)):
            raise RollingHistoryCodecError("Source-sequence map must align with the event list")
        source_sequences = list(source_sequence_by_event_index)
        _require_contiguous_sequences(source_sequences, "Planner source")

    return tuple(
        SequencedMessage(sequence=sequence, message=message)
        for sequence, message in zip(source_sequences, decoded, strict=True)
    )


def _message_additional_kwargs(
    message: object,
    *,
    allowed_keys: set[str],
) -> dict[str, object]:
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if type(additional_kwargs) is not dict:
        raise RollingHistoryCodecError("Message additional_kwargs must be a dictionary")
    if any(type(key) is not str for key in additional_kwargs):
        raise RollingHistoryCodecError("Message additional_kwargs keys must be strings")
    if set(additional_kwargs) - allowed_keys:
        raise RollingHistoryCodecError("Message contains unsupported additional_kwargs fields")
    return additional_kwargs


def _provider_message_name(
    message: object,
    additional_kwargs: dict[str, object],
) -> str | None:
    # Match langchain-openai's converter precedence and falsey-name behavior.
    value = getattr(message, "name", None) or additional_kwargs.get("name")
    return _optional_message_name(value, "Provider-visible message name")


def _optional_message_name(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, label)


def _parse_raw_tool_calls(value: object) -> list[dict[str, object]]:
    """Normalize the provider-visible raw Chat Completions tool-call fallback."""
    if type(value) is not list or not value:
        raise RollingHistoryCodecError("Raw provider tool_calls must be a nonempty list")
    calls: list[dict[str, object]] = []
    for raw_call in value:
        if type(raw_call) is not dict or set(raw_call) != {"id", "type", "function"}:
            raise RollingHistoryCodecError(
                "Every raw provider tool call must contain only its visible ID, type, and function fields"
            )
        function = raw_call.get("function")
        if type(function) is not dict:
            raise RollingHistoryCodecError("Every raw provider tool call must include function data")
        if set(function) != {"name", "arguments"}:
            raise RollingHistoryCodecError(
                "Raw provider function data contains fields outside the supported visible payload"
            )
        call_id = raw_call.get("id")
        call_type = raw_call.get("type")
        name = function.get("name")
        arguments = function.get("arguments")
        if (type(call_id) is not str or not call_id
                or type(call_type) is not str or call_type != "function"
                or type(name) is not str or not name
                or type(arguments) is not str):
            raise RollingHistoryCodecError(
                "Raw provider tool calls require a call ID, function name, and JSON arguments text"
            )
        _require_text(call_id, "Tool-call ID")
        _require_text(name, "Tool-call name")
        _require_text(arguments, "Tool-call arguments")
        try:
            args = json.loads(arguments, object_pairs_hook=_unique_object_pairs)
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise RollingHistoryCodecError("Raw provider tool-call arguments are not unambiguous JSON") from exc
        if type(args) is not dict:
            raise RollingHistoryCodecError("Raw provider tool-call arguments must decode to an object")
        try:
            _require_json_value(args, "Tool-call arguments")
        except RecursionError as exc:
            raise RollingHistoryCodecError("Tool-call arguments are cyclic or nested too deeply") from exc
        calls.append({"id": call_id, "name": name, "args": args, "type": "tool_call"})
    return calls


def _unique_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _require_text(value: object, label: str) -> str:
    if type(value) is not str:
        raise RollingHistoryCodecError(
            f"{label} must be plain text; multi-block content requires structured storage and a verified attachment resolver"
        )
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RollingHistoryCodecError(f"{label} is not valid UTF-8") from exc
    return value


def _validated_tool_calls(value: object) -> list[dict[str, object]]:
    if type(value) is not list:
        raise RollingHistoryCodecError("AIMessage tool_calls must be a complete list")
    calls: list[dict[str, object]] = []
    for call in value:
        if (type(call) is not dict
                or set(call) != {"id", "name", "args", "type"}
                or type(call["id"]) is not str or not call["id"]
                or type(call["name"]) is not str or not call["name"]
                or type(call["type"]) is not str or call["type"] != "tool_call"
                or type(call["args"]) is not dict):
            raise RollingHistoryCodecError(
                "Every AI tool call must include its ID, name, object arguments, and tool_call type"
            )
        _require_text(call["id"], "Tool-call ID")
        _require_text(call["name"], "Tool-call name")
        try:
            _require_json_value(call["args"], "Tool-call arguments")
        except RecursionError as exc:
            raise RollingHistoryCodecError("Tool-call arguments are cyclic or nested too deeply") from exc
        calls.append({
            "id": call["id"],
            "name": call["name"],
            "args": call["args"],
            "type": "tool_call",
        })
    return calls


def _require_json_value(value: object, label: str) -> None:
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise RollingHistoryCodecError(f"{label} contain a non-finite number")
        return
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise RollingHistoryCodecError(f"{label} contain invalid UTF-8 text") from exc
        return
    if type(value) is list:
        for item in value:
            _require_json_value(item, label)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise RollingHistoryCodecError(f"{label} must use string object keys")
            _require_json_value(key, label)
            _require_json_value(item, label)
        return
    raise RollingHistoryCodecError(f"{label} contain a non-JSON value")


def _validate_tool_pairs(records: Sequence[tuple[str, object]]) -> None:
    pending: dict[str, str] = {}
    seen_call_ids: set[str] = set()
    for event_type, payload in records:
        if event_type == "tool_call":
            calls = payload
            if type(calls) is not list or not calls:
                raise RollingHistoryCodecError("tool_call event has no complete calls")
            for call in calls:
                call_id = call["id"]
                if call_id in seen_call_ids:
                    raise RollingHistoryCodecError("Duplicate tool-call ID in rolling history")
                seen_call_ids.add(call_id)
                pending[call_id] = call["name"]
        elif event_type == "tool_result":
            call_id = payload
            if type(call_id) is not str or not call_id:
                raise RollingHistoryCodecError("Malformed tool-result pairing metadata")
            if call_id not in pending:
                raise RollingHistoryCodecError("Tool result has no unique preceding tool call")
            pending.pop(call_id)
    if pending:
        raise RollingHistoryCodecError("Rolling history ends with an unmatched tool call")


def _decode_envelope(content: str, *, expected_keys: set[str]) -> dict[str, object]:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise RollingHistoryCodecError("Structured rolling-history content is invalid JSON") from exc
    if (type(value) is not dict or set(value) != expected_keys
            or type(value.get("codec_version")) is not int
            or value["codec_version"] != _CODEC_VERSION
            or _canonical_json(value) != content):
        raise RollingHistoryCodecError("Structured rolling-history content is incomplete or noncanonical")
    return value


def _require_contiguous_sequences(values: Sequence[object], label: str) -> None:
    previous: int | None = None
    for value in values:
        if type(value) is not int or value < 1:
            raise RollingHistoryCodecError(f"{label} sequence must be a positive integer")
        if previous is not None and value != previous + 1:
            raise RollingHistoryCodecError(f"{label} sequences must be contiguous and ordered")
        previous = value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RollingHistoryCodecError("Rolling-history payload is not stable JSON") from exc


def _sha256(value: str) -> str:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RollingHistoryCodecError("Rolling-history payload is not valid UTF-8") from exc
    return hashlib.sha256(encoded).hexdigest()


_LEGACY_MESSAGE_FIELDS = (
    "id",
    "session_id",
    "role",
    "content",
    "sources",
    "tool_calls",
    "activity",
    "created_at",
)
_LEGACY_MESSAGE_FIELD_SET = frozenset(_LEGACY_MESSAGE_FIELDS)
_LEGACY_ENVELOPE_FORMAT = "legacy-message-snapshot"
_LEGACY_ENVELOPE_VERSION = "legacy-message-envelope-v1"
_LEGACY_CANONICAL_PROFILE = "legacy-snapshot-c14n-v1"
_LEGACY_ROW_HASH_DOMAIN = b"legacy-message-row-v1\0"
_LEGACY_CHAIN_HASH_DOMAIN = b"legacy-message-chain-v1\0"
_LEGACY_UINT64_MAX = (1 << 64) - 1


class LegacyMessageSnapshotCodecError(ValueError):
    """A legacy SQL message row cannot be represented or validated losslessly."""


@dataclass(frozen=True, slots=True)
class LegacyMessageTextProjection:
    """A single plain-text row projection, never a completeness or auth proof."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class LegacyMessageSnapshotValidation:
    """Validated envelope metadata and an optional row-only text projection."""

    canonical_envelope_json: str
    model_status: str
    model_reason: str | None
    model_view: LegacyMessageTextProjection | None


@dataclass(frozen=True, slots=True)
class LegacyMessageSnapshotEncoding:
    """Result of encoding one already-read Message field mapping."""

    sequence: int | None
    legacy_message_id: str | None
    source_message_revision: int | None
    capture_status: str
    capture_reason: str | None
    canonical_envelope_json: str | None
    row_sha256: str | None
    model_status: str
    model_reason: str | None
    model_view: LegacyMessageTextProjection | None


def encode_legacy_message_snapshot(
    message_fields: object,
    *,
    sequence: int,
    legacy_message_id: str,
    source_message_revision: int,
) -> LegacyMessageSnapshotEncoding:
    """Encode one host-read Message mapping and bind it to its snapshot slot.

    The caller must materialize the eight ORM fields before calling this pure
    function. It does not access a session, database, environment, or ORM
    descriptor. Failures produce an ``unavailable`` row with a reason; the
    caller must retain the original source row outside this codec.
    """
    metadata_error = _legacy_snapshot_metadata_error(
        sequence=sequence,
        legacy_message_id=legacy_message_id,
        source_message_revision=source_message_revision,
    )
    if metadata_error is not None:
        return LegacyMessageSnapshotEncoding(
            sequence=None,
            legacy_message_id=None,
            source_message_revision=None,
            capture_status="unavailable",
            capture_reason=metadata_error,
            canonical_envelope_json=None,
            row_sha256=None,
            model_status="unavailable",
            model_reason=metadata_error,
            model_view=None,
        )

    try:
        if type(message_fields) is not dict or set(message_fields) != _LEGACY_MESSAGE_FIELD_SET:
            raise LegacyMessageSnapshotCodecError(
                "Message fields are missing or differ from the legacy schema"
            )
        fields = _legacy_validate_message_fields(message_fields)
        if fields["id"] != legacy_message_id:
            raise LegacyMessageSnapshotCodecError(
                "Message id does not match the snapshot row identity"
            )
        envelope = _legacy_build_envelope(fields)
        canonical_envelope_json = _legacy_canonical_json(envelope)
        row_sha256 = legacy_message_row_hash(
            sequence=sequence,
            legacy_message_id=legacy_message_id,
            source_message_revision=source_message_revision,
            capture_status="captured",
            capture_reason=None,
            canonical_envelope_json=canonical_envelope_json,
        )
        validation = validate_legacy_message_snapshot(canonical_envelope_json)
        return LegacyMessageSnapshotEncoding(
            sequence=sequence,
            legacy_message_id=legacy_message_id,
            source_message_revision=source_message_revision,
            capture_status="captured",
            capture_reason=None,
            canonical_envelope_json=canonical_envelope_json,
            row_sha256=row_sha256,
            model_status=validation.model_status,
            model_reason=validation.model_reason,
            model_view=validation.model_view,
        )
    except (LegacyMessageSnapshotCodecError, RecursionError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, LegacyMessageSnapshotCodecError)
            else "Message fields exceed the codec's supported nesting depth"
        )
        row_sha256 = legacy_message_row_hash(
            sequence=sequence,
            legacy_message_id=legacy_message_id,
            source_message_revision=source_message_revision,
            capture_status="unavailable",
            capture_reason=reason,
            canonical_envelope_json=None,
        )
        return LegacyMessageSnapshotEncoding(
            sequence=sequence,
            legacy_message_id=legacy_message_id,
            source_message_revision=source_message_revision,
            capture_status="unavailable",
            capture_reason=reason,
            canonical_envelope_json=None,
            row_sha256=row_sha256,
            model_status="unavailable",
            model_reason=reason,
            model_view=None,
        )


def validate_legacy_message_snapshot(
    canonical_envelope_json: str,
) -> LegacyMessageSnapshotValidation:
    """Strictly validate a canonical envelope and derive its row-only view."""
    try:
        fields = _legacy_parse_envelope(canonical_envelope_json)
    except RecursionError as exc:
        raise LegacyMessageSnapshotCodecError(
            "Legacy snapshot envelope exceeds the codec's supported nesting depth"
        ) from exc
    model_status, model_reason, model_view = _legacy_model_text_projection(fields)
    return LegacyMessageSnapshotValidation(
        canonical_envelope_json=canonical_envelope_json,
        model_status=model_status,
        model_reason=model_reason,
        model_view=model_view,
    )


def decode_legacy_message_snapshot(canonical_envelope_json: str) -> dict[str, object]:
    """Validate an envelope and return a fresh mapping of its eight ORM values."""
    try:
        return _legacy_parse_envelope(canonical_envelope_json)
    except RecursionError as exc:
        raise LegacyMessageSnapshotCodecError(
            "Legacy snapshot envelope exceeds the codec's supported nesting depth"
        ) from exc


def legacy_message_row_hash(
    *,
    sequence: int,
    legacy_message_id: str,
    source_message_revision: int,
    capture_status: str,
    capture_reason: str | None,
    canonical_envelope_json: str | None,
) -> str:
    """Hash the complete legacy snapshot row metadata and optional envelope.

    Captured envelopes are parsed and revalidated before hashing. Missing and
    unavailable rows hash their explicit reason with a null envelope.
    """
    _legacy_require_snapshot_metadata(sequence, legacy_message_id, source_message_revision)
    if (type(capture_status) is not str
            or capture_status not in ("captured", "missing", "unavailable")):
        raise LegacyMessageSnapshotCodecError("Unsupported snapshot capture status")
    if capture_status == "captured":
        if capture_reason is not None or type(canonical_envelope_json) is not str:
            raise LegacyMessageSnapshotCodecError(
                "Captured rows require an envelope and no capture reason"
            )
        fields = _legacy_parse_envelope(canonical_envelope_json)
        if fields["id"] != legacy_message_id:
            raise LegacyMessageSnapshotCodecError(
                "Envelope Message id does not match the snapshot row identity"
            )
        canonical_envelope = json.loads(
            canonical_envelope_json,
            object_pairs_hook=_unique_object_pairs,
            parse_constant=_legacy_reject_json_constant,
        )
    else:
        if (type(capture_reason) is not str or not capture_reason
                or canonical_envelope_json is not None):
            raise LegacyMessageSnapshotCodecError(
                "Missing and unavailable rows require a reason and null envelope"
            )
        _legacy_text_node(capture_reason)
        canonical_envelope = None

    try:
        revision_text = str(source_message_revision)
    except ValueError as exc:
        raise LegacyMessageSnapshotCodecError(
            "Source message revision cannot be represented in the canonical profile"
        ) from exc
    row_input = {
        "canonical_envelope": canonical_envelope,
        "canonical_profile": _LEGACY_CANONICAL_PROFILE,
        "capture_reason": None if capture_reason is None else _legacy_text_node(capture_reason),
        "capture_status": capture_status,
        "legacy_message_id": _legacy_text_node(legacy_message_id),
        "sequence": str(sequence),
        "source_message_revision": revision_text,
    }
    canonical = _legacy_canonical_json(row_input).encode("ascii")
    return hashlib.sha256(_LEGACY_ROW_HASH_DOMAIN + canonical).hexdigest()


def legacy_message_chain_hash(
    *,
    sequence: int,
    previous_chain_sha256: str | None,
    row_sha256: str,
) -> str:
    """Hash one zero-based U64 slot, its previous chain head, and row digest."""
    sequence_bytes = _legacy_sequence_bytes(sequence)
    if sequence == 0:
        if previous_chain_sha256 is not None:
            raise LegacyMessageSnapshotCodecError(
                "The first snapshot slot must use the zero chain genesis"
            )
        previous_bytes = bytes(32)
    else:
        if (type(previous_chain_sha256) is not str
                or _SHA256.fullmatch(previous_chain_sha256) is None):
            raise LegacyMessageSnapshotCodecError(
                "A noninitial snapshot slot requires a lowercase previous chain hash"
            )
        previous_bytes = bytes.fromhex(previous_chain_sha256)
    if type(row_sha256) is not str or _SHA256.fullmatch(row_sha256) is None:
        raise LegacyMessageSnapshotCodecError("Row hash must be lowercase SHA-256 hex")
    row_bytes = bytes.fromhex(row_sha256)
    return hashlib.sha256(
        _LEGACY_CHAIN_HASH_DOMAIN + previous_bytes + sequence_bytes + row_bytes
    ).hexdigest()


def _legacy_snapshot_metadata_error(
    *,
    sequence: object,
    legacy_message_id: object,
    source_message_revision: object,
) -> str | None:
    try:
        _legacy_require_snapshot_metadata(sequence, legacy_message_id, source_message_revision)
    except LegacyMessageSnapshotCodecError as exc:
        return str(exc)
    return None


def _legacy_require_snapshot_metadata(
    sequence: object,
    legacy_message_id: object,
    source_message_revision: object,
) -> None:
    _legacy_sequence_bytes(sequence)
    if type(legacy_message_id) is not str:
        raise LegacyMessageSnapshotCodecError("Snapshot row identity must be text")
    if type(source_message_revision) is not int or source_message_revision < 0:
        raise LegacyMessageSnapshotCodecError(
            "Source message revision must be a nonnegative integer"
        )
    try:
        str(source_message_revision)
    except ValueError as exc:
        raise LegacyMessageSnapshotCodecError(
            "Source message revision exceeds the runtime's decimal conversion limit"
        ) from exc
    _legacy_text_node(legacy_message_id)


def _legacy_sequence_bytes(sequence: object) -> bytes:
    if (type(sequence) is not int or sequence < 0
            or sequence > _LEGACY_UINT64_MAX):
        raise LegacyMessageSnapshotCodecError(
            "Snapshot sequence must be a zero-based unsigned 64-bit integer"
        )
    return sequence.to_bytes(8, byteorder="big", signed=False)


def _legacy_validate_message_fields(message_fields: dict[str, object]) -> dict[str, object]:
    if type(message_fields["id"]) is not str:
        raise LegacyMessageSnapshotCodecError("Message id must be text")
    if message_fields["session_id"] is not None and type(message_fields["session_id"]) is not str:
        raise LegacyMessageSnapshotCodecError("Message session_id must be text or null")
    if type(message_fields["role"]) is not str:
        raise LegacyMessageSnapshotCodecError("Message role must be text")
    if type(message_fields["content"]) is not str:
        raise LegacyMessageSnapshotCodecError("Message content must be text")
    fields: dict[str, object] = {}
    for name in _LEGACY_MESSAGE_FIELDS:
        value = message_fields[name]
        if name in ("sources", "tool_calls", "activity"):
            _legacy_pack_json_value(value)
        elif name == "created_at":
            _legacy_pack_datetime(value)
        elif name == "session_id":
            if value is not None:
                _legacy_text_node(value)
        else:
            _legacy_text_node(value)
        fields[name] = value
    return fields


def _legacy_build_envelope(fields: dict[str, object]) -> dict[str, object]:
    encoded_fields: dict[str, object] = {
        "activity": ["json", _legacy_pack_json_value(fields["activity"])],
        "content": _legacy_text_node(fields["content"]),
        "created_at": _legacy_pack_datetime(fields["created_at"]),
        "id": _legacy_text_node(fields["id"]),
        "role": _legacy_text_node(fields["role"]),
        "session_id": ["nullable-text", None]
        if fields["session_id"] is None
        else ["nullable-text", _legacy_text_node(fields["session_id"])],
        "sources": ["json", _legacy_pack_json_value(fields["sources"])],
        "tool_calls": ["json", _legacy_pack_json_value(fields["tool_calls"])],
    }
    return {
        "canonical_profile": _LEGACY_CANONICAL_PROFILE,
        "fields": encoded_fields,
        "format": _LEGACY_ENVELOPE_FORMAT,
        "version": _LEGACY_ENVELOPE_VERSION,
    }


def _legacy_parse_envelope(canonical_envelope_json: str) -> dict[str, object]:
    if type(canonical_envelope_json) is not str:
        raise LegacyMessageSnapshotCodecError("Legacy snapshot envelope must be text")
    try:
        canonical_envelope_json.encode("ascii", errors="strict")
        envelope = json.loads(
            canonical_envelope_json,
            object_pairs_hook=_unique_object_pairs,
            parse_constant=_legacy_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise LegacyMessageSnapshotCodecError(
            "Legacy snapshot envelope is not unambiguous ASCII JSON"
        ) from exc
    if (type(envelope) is not dict
            or set(envelope) != {"canonical_profile", "fields", "format", "version"}
            or envelope.get("canonical_profile") != _LEGACY_CANONICAL_PROFILE
            or envelope.get("format") != _LEGACY_ENVELOPE_FORMAT
            or envelope.get("version") != _LEGACY_ENVELOPE_VERSION
            or _legacy_canonical_json(envelope) != canonical_envelope_json):
        raise LegacyMessageSnapshotCodecError(
            "Legacy snapshot envelope is incomplete, unsupported, or noncanonical"
        )
    encoded_fields = envelope.get("fields")
    if type(encoded_fields) is not dict or set(encoded_fields) != _LEGACY_MESSAGE_FIELD_SET:
        raise LegacyMessageSnapshotCodecError(
            "Legacy snapshot envelope does not contain exactly the Message fields"
        )
    fields: dict[str, object] = {}
    for name in _LEGACY_MESSAGE_FIELDS:
        node = encoded_fields[name]
        if name in ("sources", "tool_calls", "activity"):
            if type(node) is not list or len(node) != 2 or node[0] != "json":
                raise LegacyMessageSnapshotCodecError(
                    f"Legacy snapshot {name} field has an invalid JSON envelope"
                )
            fields[name] = _legacy_unpack_json_value(node[1])
        elif name == "created_at":
            fields[name] = _legacy_unpack_datetime(node)
        elif name == "session_id":
            if (type(node) is not list or len(node) != 2
                    or node[0] != "nullable-text"):
                raise LegacyMessageSnapshotCodecError(
                    "Legacy snapshot session_id field has an invalid envelope"
                )
            fields[name] = None if node[1] is None else _legacy_unpack_text_node(node[1])
        else:
            fields[name] = _legacy_unpack_text_node(node)
    if type(fields["id"]) is not str or type(fields["role"]) is not str or type(fields["content"]) is not str:
        raise LegacyMessageSnapshotCodecError("Legacy snapshot scalar fields are invalid")
    if fields["session_id"] is not None and type(fields["session_id"]) is not str:
        raise LegacyMessageSnapshotCodecError("Legacy snapshot session_id must be text or null")
    return fields


def _legacy_model_text_projection(
    fields: dict[str, object],
) -> tuple[str, str | None, LegacyMessageTextProjection | None]:
    role = fields["role"]
    content = fields["content"]
    if role not in ("user", "assistant"):
        return (
            "archive_only",
            "Only plain user and assistant text rows have a model-view projection",
            None,
        )
    tool_calls = fields["tool_calls"]
    if tool_calls is not None and not (type(tool_calls) is list and not tool_calls):
        return (
            "archive_only",
            "Legacy tool_calls are archived but cannot be paired with a verified tool result",
            None,
        )
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeError:
        return (
            "archive_only",
            "Message text is not strict UTF-8 and cannot be projected to a model",
            None,
        )
    return (
        "text_projection_available",
        None,
        LegacyMessageTextProjection(role=role, content=content),
    )


def _legacy_pack_datetime(value: object) -> list[object]:
    from datetime import datetime, timedelta, timezone

    if value is None:
        return ["datetime", "null"]
    if type(value) is not datetime:
        raise LegacyMessageSnapshotCodecError(
            "created_at must be a datetime or null"
        )
    parts = [
        str(value.year),
        str(value.month),
        str(value.day),
        str(value.hour),
        str(value.minute),
        str(value.second),
        str(value.microsecond),
        str(value.fold),
    ]
    if value.tzinfo is None:
        zone: list[object] = ["naive"]
    elif type(value.tzinfo) is timezone:
        try:
            offset = value.utcoffset()
            name = value.tzname()
        except (OverflowError, ValueError) as exc:
            raise LegacyMessageSnapshotCodecError(
                "created_at fixed-offset timezone cannot be represented"
            ) from exc
        if type(offset) is not timedelta or type(name) is not str:
            raise LegacyMessageSnapshotCodecError(
                "created_at fixed-offset timezone metadata is unsupported"
            )
        offset_microseconds = (
            (offset.days * 86400 + offset.seconds) * 1_000_000 + offset.microseconds
        )
        try:
            offset_text = str(offset_microseconds)
        except ValueError as exc:
            raise LegacyMessageSnapshotCodecError(
                "created_at timezone offset cannot be represented"
            ) from exc
        zone = ["fixed-offset", offset_text, _legacy_text_node(name)]
    else:
        raise LegacyMessageSnapshotCodecError(
            "created_at uses a non-fixed timezone that this codec cannot restore"
        )
    return ["datetime", [*parts, zone]]


def _legacy_unpack_datetime(node: object) -> object:
    from datetime import datetime, timedelta, timezone

    if type(node) is not list or len(node) != 2 or node[0] != "datetime":
        raise LegacyMessageSnapshotCodecError("Legacy snapshot created_at envelope is invalid")
    if node[1] == "null":
        return None
    parts = node[1]
    if type(parts) is not list or len(parts) != 9:
        raise LegacyMessageSnapshotCodecError("Legacy snapshot datetime components are invalid")
    numbers: list[int] = []
    for raw in parts[:8]:
        if type(raw) is not str or re.fullmatch(r"(?:0|[1-9][0-9]*)", raw, re.ASCII) is None:
            raise LegacyMessageSnapshotCodecError("Legacy snapshot datetime integer is noncanonical")
        try:
            numbers.append(int(raw))
        except ValueError as exc:
            raise LegacyMessageSnapshotCodecError("Legacy snapshot datetime integer is invalid") from exc
    year, month, day, hour, minute, second, microsecond, fold = numbers
    zone_node = parts[8]
    if type(zone_node) is not list or not zone_node:
        raise LegacyMessageSnapshotCodecError("Legacy snapshot datetime timezone is invalid")
    if zone_node == ["naive"]:
        tzinfo = None
    elif (len(zone_node) == 3 and zone_node[0] == "fixed-offset"
            and type(zone_node[1]) is str
            and re.fullmatch(r"(?:0|-?[1-9][0-9]*)", zone_node[1], re.ASCII) is not None):
        try:
            offset_microseconds = int(zone_node[1])
            name = _legacy_unpack_text_node(zone_node[2])
            tzinfo = timezone(timedelta(microseconds=offset_microseconds), name)
        except (OverflowError, TypeError, ValueError) as exc:
            raise LegacyMessageSnapshotCodecError(
                "Legacy snapshot fixed-offset timezone is invalid"
            ) from exc
    else:
        raise LegacyMessageSnapshotCodecError("Legacy snapshot datetime timezone is unsupported")
    try:
        value = datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            microsecond,
            tzinfo=tzinfo,
            fold=fold,
        )
    except (OverflowError, TypeError, ValueError) as exc:
        raise LegacyMessageSnapshotCodecError("Legacy snapshot datetime value is invalid") from exc
    if _legacy_pack_datetime(value) != node:
        raise LegacyMessageSnapshotCodecError("Legacy snapshot datetime did not round-trip canonically")
    return value


def _legacy_pack_json_value(value: object) -> list[object]:
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["boolean", "true" if value else "false"]
    if type(value) is int:
        try:
            decimal = str(value)
        except ValueError as exc:
            raise LegacyMessageSnapshotCodecError(
                "JSON integer exceeds the runtime's canonical decimal conversion limit"
            ) from exc
        return ["integer", decimal]
    if type(value) is float:
        if not math.isfinite(value):
            raise LegacyMessageSnapshotCodecError(
                "JSON non-finite numbers cannot be captured losslessly"
            )
        return ["binary64", value.hex()]
    if type(value) is str:
        return _legacy_text_node(value)
    if type(value) is list:
        return ["array", [_legacy_pack_json_value(item) for item in value]]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise LegacyMessageSnapshotCodecError("JSON object keys must be text")
        ordered_keys = sorted(value, key=lambda key: tuple(map(ord, key)))
        return [
            "object",
            [
                [_legacy_text_node(key), _legacy_pack_json_value(value[key])]
                for key in ordered_keys
            ],
        ]
    raise LegacyMessageSnapshotCodecError(
        "JSON field contains a value outside the supported JSON data model"
    )


def _legacy_unpack_json_value(node: object) -> object:
    if type(node) is not list or not node or type(node[0]) is not str:
        raise LegacyMessageSnapshotCodecError("Legacy JSON value node is malformed")
    kind = node[0]
    if kind == "null" and len(node) == 1:
        return None
    if kind == "boolean" and len(node) == 2 and node[1] in ("true", "false"):
        return node[1] == "true"
    if kind == "integer" and len(node) == 2 and type(node[1]) is str:
        raw = node[1]
        if re.fullmatch(r"(?:0|-?[1-9][0-9]*)", raw, re.ASCII) is None:
            raise LegacyMessageSnapshotCodecError("Legacy JSON integer is noncanonical")
        try:
            value = int(raw, 10)
            if str(value) != raw:
                raise LegacyMessageSnapshotCodecError("Legacy JSON integer is noncanonical")
        except ValueError as exc:
            raise LegacyMessageSnapshotCodecError(
                "Legacy JSON integer exceeds the runtime conversion limit"
            ) from exc
        return value
    if kind == "binary64" and len(node) == 2 and type(node[1]) is str:
        try:
            value = float.fromhex(node[1])
        except (OverflowError, ValueError) as exc:
            raise LegacyMessageSnapshotCodecError("Legacy JSON binary64 value is invalid") from exc
        if not math.isfinite(value) or value.hex() != node[1]:
            raise LegacyMessageSnapshotCodecError("Legacy JSON binary64 value is noncanonical")
        return value
    if kind == "text" and len(node) == 2:
        return _legacy_unpack_text_node(node)
    if kind == "array" and len(node) == 2 and type(node[1]) is list:
        return [_legacy_unpack_json_value(item) for item in node[1]]
    if kind == "object" and len(node) == 2 and type(node[1]) is list:
        result: dict[str, object] = {}
        previous_key: tuple[int, ...] | None = None
        for pair in node[1]:
            if type(pair) is not list or len(pair) != 2:
                raise LegacyMessageSnapshotCodecError("Legacy JSON object pair is malformed")
            key = _legacy_unpack_text_node(pair[0])
            ordering_key = tuple(map(ord, key))
            if previous_key is not None and ordering_key <= previous_key:
                raise LegacyMessageSnapshotCodecError(
                    "Legacy JSON object keys are duplicated or not canonically ordered"
                )
            previous_key = ordering_key
            result[key] = _legacy_unpack_json_value(pair[1])
        return result
    raise LegacyMessageSnapshotCodecError("Legacy JSON value node has an unsupported shape")


def _legacy_text_node(value: str) -> list[str]:
    if type(value) is not str:
        raise LegacyMessageSnapshotCodecError("Legacy text value must be a string")
    return ["text", "".join(f"{ord(character):06x}" for character in value)]


def _legacy_unpack_text_node(node: object) -> str:
    if (type(node) is not list or len(node) != 2 or node[0] != "text"
            or type(node[1]) is not str
            or re.fullmatch(r"(?:[0-9a-f]{6})*", node[1], re.ASCII) is None):
        raise LegacyMessageSnapshotCodecError("Legacy text node is malformed")
    codepoints: list[str] = []
    for index in range(0, len(node[1]), 6):
        codepoint = int(node[1][index:index + 6], 16)
        if codepoint > 0x10FFFF:
            raise LegacyMessageSnapshotCodecError("Legacy text code point is out of range")
        codepoints.append(chr(codepoint))
    return "".join(codepoints)


def _legacy_canonical_json(value: object) -> str:
    """Serialize the ASCII-only legacy profile without changing the v1 codec."""
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is str:
        if any(ord(character) > 0x7F for character in value):
            raise LegacyMessageSnapshotCodecError(
                "Legacy canonical profile permits only ASCII envelope strings"
            )
        escaped: list[str] = ['"']
        for character in value:
            codepoint = ord(character)
            if character == '"':
                escaped.append('\\"')
            elif character == "\\":
                escaped.append("\\\\")
            elif codepoint < 0x20:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(character)
        escaped.append('"')
        return "".join(escaped)
    if type(value) is list:
        return "[" + ",".join(_legacy_canonical_json(item) for item in value) + "]"
    if type(value) is dict:
        if any(type(key) is not str or any(ord(character) > 0x7F for character in key) for key in value):
            raise LegacyMessageSnapshotCodecError(
                "Legacy canonical profile requires ASCII object keys"
            )
        members = (
            _legacy_canonical_json(key) + ":" + _legacy_canonical_json(value[key])
            for key in sorted(value, key=lambda item: item.encode("ascii"))
        )
        return "{" + ",".join(members) + "}"
    raise LegacyMessageSnapshotCodecError(
        "Legacy canonical profile does not permit JSON numbers or custom values"
    )


def _legacy_reject_json_constant(value: str) -> object:
    raise LegacyMessageSnapshotCodecError(
        f"Nonstandard JSON numeric constant is unsupported: {value}"
    )
