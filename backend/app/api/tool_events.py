"""Private ToolMessage adapter over the existing local observation store.

This module does not register tools or add an HTTP, Agent, ToolRouter, producer,
publisher, or ``read_tool_event`` path. The host may call the private adapter
only after its existing authorization/HITL flow and after a complete ordered
ToolNode result batch exists.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

from app.api.tool_observations import (
    MAX_TOOL_OBSERVATION_BYTES,
    ToolObservationScope,
    archive_tool_observation,
    read_tool_observation,
)


logger = logging.getLogger(__name__)

_ARCHIVE_TOOL_NAME = "tool_message_batch_v3"
_ENVELOPE_SCHEMA = 3
_ENVELOPE_KIND = "tool_message_batch"
_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_PRINCIPAL_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_MAX_BATCH = 64
_MAX_SESSION_ID_BYTES = 1024
_ALLOWED_TOOL_CATEGORIES = frozenset({
    "builtin_non_command",
    "builtin_command",
    "command",
    "mcp",
    "skill",
    "skills",
})
_ALLOWED_MESSAGE_STATUSES = frozenset({"success", "error"})


@dataclass(frozen=True)
class ExpectedToolCall:
    """Host-derived exact call identity; intentionally contains no raw args."""

    host_call_id: str
    tool_call_id: str
    tool_name: str


@dataclass(frozen=True)
class ToolMessageCapture:
    """An actual ToolMessage plus host-owned non-secret instance identity."""

    tool_message: Any
    tool_instance_id: str
    tool_contract_version: str
    tool_category: str


@dataclass(frozen=True)
class ToolMessageBatchWriteResult:
    status: str
    reference: str | None = None
    chain_head: str | None = None


@dataclass(frozen=True)
class ToolMessageBatchReadResult:
    status: str
    messages: tuple[Any, ...] = ()
    chain_head: str | None = None


def _archive_tool_message_batch(
    scope: ToolObservationScope,
    expected_pairs: Sequence[ExpectedToolCall],
    captures: Sequence[ToolMessageCapture],
) -> ToolMessageBatchWriteResult:
    """Archive one full, ordered ToolMessage batch through the existing store.

    Content is copied only through strict JSON-native serialization. The
    existing ``_toolObservation:v1`` store remains responsible for opaque refs,
    local persistence, epoch checks, transactions, and its current quotas.
    """
    if not _valid_scope(scope):
        return ToolMessageBatchWriteResult("INVALID_SCOPE")
    pairs = _validate_pairs(expected_pairs)
    if isinstance(pairs, str):
        return ToolMessageBatchWriteResult(pairs)
    prepared = _prepare_batch(pairs, captures)
    if isinstance(prepared, str):
        return ToolMessageBatchWriteResult(prepared)

    envelope, chain_head = _build_envelope(scope, prepared)
    if envelope is None or chain_head is None:
        return ToolMessageBatchWriteResult("SERIALIZATION_ERROR")
    envelope_bytes = _strict_utf8(envelope)
    if envelope_bytes is None:
        return ToolMessageBatchWriteResult("SERIALIZATION_ERROR")
    if not envelope_bytes or len(envelope_bytes) > MAX_TOOL_OBSERVATION_BYTES:
        return ToolMessageBatchWriteResult("CAPACITY")

    try:
        reference = archive_tool_observation(
            envelope,
            _ARCHIVE_TOOL_NAME,
            scope,
        )
    except Exception:
        logger.warning("tool message observation archive unavailable")
        return ToolMessageBatchWriteResult("STORE_UNAVAILABLE")
    if reference is None:
        return ToolMessageBatchWriteResult("STORE_REJECTED")
    if type(reference) is not str or _REFERENCE_PATTERN.fullmatch(reference) is None:
        # The existing store contract returns this opaque token shape. Do not
        # expose an unexpected value as a usable reference.
        return ToolMessageBatchWriteResult("STORE_UNAVAILABLE")
    return ToolMessageBatchWriteResult("STORED", reference, chain_head)


def _read_tool_message_batch(
    reference: str,
    scope: ToolObservationScope,
    expected_pairs: Sequence[ExpectedToolCall],
    *,
    byte_budget: int,
) -> ToolMessageBatchReadResult:
    """Read one exact existing-store ref and validate its entire ordered batch."""
    if not _valid_scope(scope):
        return ToolMessageBatchReadResult("INVALID_SCOPE")
    if type(reference) is not str or _REFERENCE_PATTERN.fullmatch(reference) is None:
        return ToolMessageBatchReadResult("INVALID_REFERENCE")
    if type(byte_budget) is not int or byte_budget <= 0:
        return ToolMessageBatchReadResult("INVALID_BUDGET")
    pairs = _validate_pairs(expected_pairs)
    if isinstance(pairs, str):
        return ToolMessageBatchReadResult(pairs)

    try:
        from langchain_core.messages import ToolMessage

        envelope = read_tool_observation(reference, scope)
        if type(envelope) is not str:
            return ToolMessageBatchReadResult("UNAVAILABLE")
        envelope_bytes = _strict_utf8(envelope)
        if envelope_bytes is None:
            return ToolMessageBatchReadResult("SERIALIZATION_ERROR")
        if len(envelope_bytes) > min(byte_budget, MAX_TOOL_OBSERVATION_BYTES):
            return ToolMessageBatchReadResult("OVER_BUDGET")

        decoded = _parse_json_object(envelope)
        if decoded is None:
            return ToolMessageBatchReadResult("SERIALIZATION_ERROR")
        messages = _validate_envelope(decoded, scope, pairs)
        if isinstance(messages, str):
            return ToolMessageBatchReadResult(messages)

        restored: list[Any] = []
        for item in messages["messages"]:
            body = item["tool_message"]
            try:
                restored.append(ToolMessage(
                    content=body["content"],
                    name=body["name"],
                    tool_call_id=body["tool_call_id"],
                    status=body["status"],
                ))
            except Exception:
                return ToolMessageBatchReadResult("SERIALIZATION_ERROR")
        return ToolMessageBatchReadResult(
            "AVAILABLE",
            tuple(restored),
            messages["chain_head"],
        )
    except Exception:
        logger.warning("tool message observation read unavailable")
        return ToolMessageBatchReadResult("STORE_UNAVAILABLE")


def _validate_pairs(value: Any) -> list[ExpectedToolCall] | str:
    if (
        type(value) not in (list, tuple)
        or not value
        or len(value) > _MAX_BATCH
    ):
        return "INVALID_BATCH"
    pairs: list[ExpectedToolCall] = []
    seen_host_ids: set[str] = set()
    seen_tool_ids: set[str] = set()
    for pair in value:
        if type(pair) is not ExpectedToolCall:
            return "PAIR_MISMATCH"
        if not all((
            _valid_text(pair.host_call_id, 256),
            _valid_text(pair.tool_call_id, 256),
            _valid_text(pair.tool_name, 256),
        )):
            return "PAIR_MISMATCH"
        if pair.host_call_id in seen_host_ids or pair.tool_call_id in seen_tool_ids:
            return "PAIR_MISMATCH"
        seen_host_ids.add(pair.host_call_id)
        seen_tool_ids.add(pair.tool_call_id)
        pairs.append(pair)
    return pairs


def _prepare_batch(
    pairs: list[ExpectedToolCall],
    captures: Any,
) -> list[dict[str, Any]] | str:
    if type(captures) not in (list, tuple) or len(captures) != len(pairs):
        return "INCOMPLETE_BATCH"
    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return "UNSUPPORTED_MESSAGE"

    prepared: list[dict[str, Any]] = []
    for sequence, (pair, capture) in enumerate(zip(pairs, captures, strict=True)):
        if type(capture) is not ToolMessageCapture:
            return "UNSUPPORTED_MESSAGE"
        if (
            type(capture.tool_category) is not str
            or capture.tool_category not in _ALLOWED_TOOL_CATEGORIES
        ):
            return "INVALID_TOOL_CATEGORY"
        if (
            not _valid_text(capture.tool_instance_id, 128)
            or not _valid_text(capture.tool_contract_version, 128)
        ):
            return "INVALID_TOOL_IDENTITY"
        if not isinstance(capture.tool_message, ToolMessage):
            return "UNSUPPORTED_MESSAGE"

        message = capture.tool_message
        message_status = getattr(message, "status", None)
        if (
            type(message.name) is not str
            or type(message.tool_call_id) is not str
            or message.name != pair.tool_name
            or message.tool_call_id != pair.tool_call_id
            or type(message_status) is not str
            or message_status not in _ALLOWED_MESSAGE_STATUSES
        ):
            return "PAIR_OR_STATUS_MISMATCH"
        content_json = _compact_json(message.content)
        if content_json is None:
            return "SERIALIZATION_ERROR"
        content_bytes = _strict_utf8(content_json)
        if content_bytes is None:
            return "SERIALIZATION_ERROR"
        try:
            # Snapshot the exact JSON-native value that will be serialized.
            content = json.loads(content_json)
        except (TypeError, ValueError, RecursionError):
            return "SERIALIZATION_ERROR"
        content_bytes = _json_native_bytes(content)
        if content_bytes is None:
            return "SERIALIZATION_ERROR"
        prepared.append({
            "sequence": sequence,
            "pair": pair,
            "tool_instance_id": capture.tool_instance_id,
            "tool_contract_version": capture.tool_contract_version,
            "tool_category": capture.tool_category,
            "status": message_status,
            "content": content,
            "content_bytes": content_bytes,
        })
    return prepared


def _build_envelope(
    scope: ToolObservationScope,
    prepared: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    scope_value = {
        "principal_id": scope.principal_id,
        "session_id": scope.session_id,
        "origin_request_id": scope.request_id,
        "delete_epoch": scope.session_delete_epoch,
    }
    messages: list[dict[str, Any]] = []
    previous_digest = ""
    for item in prepared:
        pair: ExpectedToolCall = item["pair"]
        body = {
            "type": "tool",
            "name": pair.tool_name,
            "tool_call_id": pair.tool_call_id,
            "status": item["status"],
            "content": item["content"],
        }
        body_digest = hashlib.sha256(item["content_bytes"]).hexdigest()
        chain_payload = {
            "scope": scope_value,
            "sequence": item["sequence"],
            "pair": {
                "host_call_id": pair.host_call_id,
                "tool_call_id": pair.tool_call_id,
                "tool_name": pair.tool_name,
            },
            "tool_instance_id": item["tool_instance_id"],
            "tool_contract_version": item["tool_contract_version"],
            "tool_category": item["tool_category"],
            "return_state": "returned",
            "tool_message_status": item["status"],
            "body_sha256": body_digest,
            "previous_digest": previous_digest,
        }
        chain_bytes = _json_native_bytes(chain_payload)
        if chain_bytes is None:
            return None, None
        chain_digest = hashlib.sha256(chain_bytes).hexdigest()
        messages.append({
            "sequence": item["sequence"],
            "pair": chain_payload["pair"],
            "tool_instance_id": item["tool_instance_id"],
            "tool_contract_version": item["tool_contract_version"],
            "tool_category": item["tool_category"],
            "return_state": "returned",
            "tool_message": body,
            "body_sha256": body_digest,
            "previous_digest": previous_digest,
            "chain_sha256": chain_digest,
        })
        previous_digest = chain_digest

    envelope_value = {
        "schema_version": _ENVELOPE_SCHEMA,
        "kind": _ENVELOPE_KIND,
        "scope": scope_value,
        "messages": messages,
        "chain_head": previous_digest,
    }
    envelope = _compact_json(envelope_value)
    return envelope, previous_digest if envelope is not None else None


def _validate_envelope(
    envelope: dict[str, Any],
    scope: ToolObservationScope,
    expected_pairs: list[ExpectedToolCall],
) -> dict[str, Any] | str:
    if set(envelope) != {"schema_version", "kind", "scope", "messages", "chain_head"}:
        return "UNSUPPORTED_SCHEMA"
    if envelope.get("schema_version") != _ENVELOPE_SCHEMA or envelope.get("kind") != _ENVELOPE_KIND:
        return "UNSUPPORTED_SCHEMA"
    origin_scope = envelope.get("scope")
    if type(origin_scope) is not dict or set(origin_scope) != {
        "principal_id", "session_id", "origin_request_id", "delete_epoch",
    }:
        return "SCOPE_MISMATCH"
    origin_request_id = origin_scope.get("origin_request_id")
    if (
        type(origin_request_id) is not str
        or _REQUEST_PATTERN.fullmatch(origin_request_id) is None
    ):
        return "INVALID_ORIGIN_REQUEST"
    expected_origin_scope = {
        "principal_id": scope.principal_id,
        "session_id": scope.session_id,
        "origin_request_id": origin_request_id,
        "delete_epoch": scope.session_delete_epoch,
    }
    if origin_scope != expected_origin_scope:
        return "SCOPE_MISMATCH"
    records = envelope.get("messages")
    if type(records) is not list or len(records) != len(expected_pairs):
        return "INCOMPLETE_BATCH"

    previous_digest = ""
    for sequence, (record, pair) in enumerate(zip(records, expected_pairs, strict=True)):
        expected_record_keys = {
            "sequence", "pair", "tool_instance_id", "tool_contract_version",
            "tool_category", "return_state", "tool_message", "body_sha256",
            "previous_digest", "chain_sha256",
        }
        if type(record) is not dict or set(record) != expected_record_keys:
            return "UNSUPPORTED_SCHEMA"
        expected_pair = {
            "host_call_id": pair.host_call_id,
            "tool_call_id": pair.tool_call_id,
            "tool_name": pair.tool_name,
        }
        if (
            type(record.get("sequence")) is not int
            or record.get("sequence") != sequence
            or record.get("pair") != expected_pair
        ):
            return "PAIR_OR_ORDER_MISMATCH"
        if (
            record.get("tool_category") not in _ALLOWED_TOOL_CATEGORIES
            or record.get("return_state") != "returned"
            or not _valid_text(record.get("tool_instance_id"), 128)
            or not _valid_text(record.get("tool_contract_version"), 128)
        ):
            return "PAIR_OR_OUTCOME_MISMATCH"
        body = record.get("tool_message")
        if type(body) is not dict or set(body) != {
            "type", "name", "tool_call_id", "status", "content",
        }:
            return "UNSUPPORTED_SCHEMA"
        if (
            body.get("type") != "tool"
            or body.get("name") != pair.tool_name
            or body.get("tool_call_id") != pair.tool_call_id
            or type(body.get("status")) is not str
            or body.get("status") not in _ALLOWED_MESSAGE_STATUSES
        ):
            return "PAIR_OR_OUTCOME_MISMATCH"
        content_bytes = _json_native_bytes(body.get("content"))
        if content_bytes is None:
            return "SERIALIZATION_ERROR"
        body_digest = hashlib.sha256(content_bytes).hexdigest()
        if body_digest != record.get("body_sha256"):
            return "DIGEST_MISMATCH"
        if record.get("previous_digest") != previous_digest:
            return "CHAIN_MISMATCH"
        chain_payload = {
            "scope": expected_origin_scope,
            "sequence": sequence,
            "pair": expected_pair,
            "tool_instance_id": record["tool_instance_id"],
            "tool_contract_version": record["tool_contract_version"],
            "tool_category": record["tool_category"],
            "return_state": record["return_state"],
            "tool_message_status": body["status"],
            "body_sha256": body_digest,
            "previous_digest": previous_digest,
        }
        chain_bytes = _json_native_bytes(chain_payload)
        if chain_bytes is None:
            return "SERIALIZATION_ERROR"
        previous_digest = hashlib.sha256(chain_bytes).hexdigest()
        if record.get("chain_sha256") != previous_digest:
            return "DIGEST_MISMATCH"

    if envelope.get("chain_head") != previous_digest:
        return "DIGEST_MISMATCH"
    return envelope


def _parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, RecursionError):
        return None
    if type(decoded) is not dict or not _is_json_native(decoded):
        return None
    if _compact_json(decoded) != value:
        return None
    return decoded


def _compact_json(value: Any) -> str | None:
    encoded = _json_native_bytes(value)
    if encoded is None:
        return None
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _json_native_bytes(value: Any) -> bytes | None:
    try:
        is_native = _is_json_native(value)
    except RecursionError:
        return None
    if not is_native:
        return None
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError, OverflowError):
        return None


def _is_json_native(value: Any) -> bool:
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return _is_json_native_sequence(value, set())
    if type(value) is dict:
        return _is_json_native_mapping(value, set())
    return False


def _is_json_native_sequence(value: list[Any], active: set[int]) -> bool:
    identity = id(value)
    if identity in active:
        return False
    active.add(identity)
    try:
        return all(_is_json_native_child(item, active) for item in value)
    finally:
        active.remove(identity)


def _is_json_native_mapping(value: dict[str, Any], active: set[int]) -> bool:
    identity = id(value)
    if identity in active:
        return False
    active.add(identity)
    try:
        return all(
            type(key) is str and _is_json_native_child(item, active)
            for key, item in value.items()
        )
    finally:
        active.remove(identity)


def _is_json_native_child(value: Any, active: set[int]) -> bool:
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return _is_json_native_sequence(value, active)
    if type(value) is dict:
        return _is_json_native_mapping(value, active)
    return False


def _strict_utf8(value: Any) -> bytes | None:
    if type(value) is not str:
        return None
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _valid_scope(scope: Any) -> bool:
    if type(scope) is not ToolObservationScope:
        return False
    if (
        type(scope.principal_id) is not str
        or _PRINCIPAL_PATTERN.fullmatch(scope.principal_id) is None
        or type(scope.session_id) is not str
        or not scope.session_id
        or type(scope.request_id) is not str
        or _REQUEST_PATTERN.fullmatch(scope.request_id) is None
        or type(scope.session_delete_epoch) is not str
        or (
            scope.session_delete_epoch
            and _REQUEST_PATTERN.fullmatch(scope.session_delete_epoch) is None
        )
    ):
        return False
    session_bytes = _strict_utf8(scope.session_id)
    return session_bytes is not None and len(session_bytes) <= _MAX_SESSION_ID_BYTES


def _valid_text(value: Any, max_bytes: int) -> bool:
    if type(value) is not str or not value:
        return False
    encoded = _strict_utf8(value)
    return encoded is not None and len(encoded) <= max_bytes
