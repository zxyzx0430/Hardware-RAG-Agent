"""Persistent owner-bound positions for paged rolling-history reads.

Locators identify positions in an immutable v1 archive snapshot. They never
grant access: every operation bootstraps the current authenticated TOOL owner
and resolves the locator again in that owner's transaction. Inline v1 event
bodies are indexed on first use by bounded SQLite byte slices; the complete
canonical payload hash is streamed before the immutable sidecar is published.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import secrets
from typing import Any, Callable

from app.api import rolling_history as _history
from app.api.context_events import ContextEventScope
from app.api.legacy_snapshot_store import (
    LegacyMessageSourceFence,
    read_message_source_fence_in_transaction,
)
from app.api.rolling_history_owner_scope import (
    OperationMode,
    RollingHistoryOwnerIdentity,
    ScopeOrigin,
    bootstrap_rolling_history_owner_scope,
    get_rolling_history_owner_identity,
    run_rolling_history_owner_operation,
)
from app.api.tool_events import get_tool_event_scope


_SCHEMA_VERSION = 1
_LOCATOR_NAMESPACE = "owner-history-locator:v1:"
_HANDLE_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_GENERATION_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_INDEX_CHUNK_BYTES = 32768
_MAX_SETTING_READ_BYTES = 65536
_MAX_OFFSET_RECORD_BYTES = 4096
_MAX_PAGE_SIZE = 100
_MIN_CHUNK_BYTES = 4
_MAX_CHUNK_BYTES = 65536
_UNAVAILABLE = "Owner rolling history is unavailable."
_EVENT_FIELDS = frozenset({
    "schema_version", "kind", "scope", "request_id", "sequence", "event_id",
    "idempotency_id", "payload", "content_sha256", "previous_chain_sha256", "chain_sha256",
})
_PAYLOAD_FIELDS = frozenset({"event_type", "content", "attachments"})
_SCOPE_FIELDS = frozenset({"session_id", "principal_hash", "session_epoch"})
_SKILL_EVENT_TYPES = frozenset({"skill_instruction", "skill_resource"})


class RollingHistoryLocatorUnavailable(RuntimeError):
    """Sanitized failure for a malformed, stale, or unavailable locator."""


@dataclass(frozen=True)
class _OwnerBinding:
    session_id: str
    principal_hash: str
    delete_epoch: str
    incarnation_id: str
    namespace_digest: str

    def wire(self) -> dict[str, str]:
        return {
            "session_sha256": _sha256(self.session_id.encode("utf-8")),
            "principal_hash": self.principal_hash,
            "delete_epoch": self.delete_epoch,
            "incarnation_id": self.incarnation_id,
            "namespace_digest": self.namespace_digest,
        }


@dataclass(frozen=True)
class _EventHeader:
    sequence: int
    request_id: str
    event_id: str
    content_sha256: str
    previous_chain_sha256: str
    chain_sha256: str
    event_type: str
    content_utf8_byte_length: int
    has_attachments: bool


@dataclass(frozen=True)
class _BodyManifest:
    event_id: str
    sequence: int
    content_sha256: str
    chain_sha256: str
    body_utf8_byte_length: int
    body_sha256: str
    chunk_count: int
    merkle_root: str
    manifest_sha256: str


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"),
        sort_keys=True, allow_nan=False,
    )


def _sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if type(value) is str else value
    return hashlib.sha256(raw).hexdigest()


def _unavailable() -> RollingHistoryLocatorUnavailable:
    return RollingHistoryLocatorUnavailable(_UNAVAILABLE)


def _rows(handle, statement: str, parameters: object, columns: tuple[str, ...]):
    result = _history._owner_history_rows(handle, statement, parameters, columns)
    if type(result) is not tuple:
        raise _unavailable()
    return result


def _read_setting(
    handle,
    key: str,
    *,
    maximum_bytes: int = _MAX_SETTING_READ_BYTES,
) -> str | bytes | None:
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= _MAX_SETTING_READ_BYTES:
        raise _unavailable()
    rows = _rows(
        handle,
        "SELECT typeof(value) AS value_type, "
        "length(CAST(value AS BLOB)) AS value_length, "
        "substr(CAST(value AS BLOB), 1, ?) AS bounded_value "
        "FROM settings WHERE key = ?",
        (maximum_bytes + 1, key),
        ("value_type", "value_length", "bounded_value"),
    )
    if len(rows) > 1:
        raise _unavailable()
    if not rows:
        return None
    value_type, value_length, bounded_value = rows[0]
    if (
        value_type not in ("text", "blob")
        or type(value_length) is not int
        or value_length < 0
        or value_length > maximum_bytes
        or type(bounded_value) is not bytes
        or len(bounded_value) != value_length
    ):
        raise _unavailable()
    if value_type == "blob":
        return bounded_value
    try:
        return bounded_value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _unavailable() from None


def _insert_cas(handle, key: str, value: str | bytes) -> None:
    """Insert immutable Settings data with insert-if-absent CAS and readback."""
    changed = handle.execute(
        "INSERT INTO settings (key, value, updated_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO NOTHING",
        (key, value),
    )
    if type(changed) is not int or changed not in (0, 1):
        raise _unavailable()
    observed = _read_setting(handle, key)
    if observed != value:
        raise _unavailable()


def _require_owner_binding(
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
) -> _OwnerBinding:
    if (
        type(scope) is not ContextEventScope
        or type(identity) is not RollingHistoryOwnerIdentity
        or type(scope.session_id) is not str
        or not 1 <= len(scope.session_id) <= 128
        or any(ord(char) < 32 or ord(char) == 127 for char in scope.session_id)
        or type(scope.request_id) is not str
        or _REQUEST_RE.fullmatch(scope.request_id) is None
        or type(scope.principal_hash) is not str
        or _HASH_RE.fullmatch(scope.principal_hash) is None
        or type(scope.session_epoch) is not str
        or (scope.session_epoch != "" and _GENERATION_RE.fullmatch(scope.session_epoch) is None)
        or identity.session_id != scope.session_id
        or identity.request_id != scope.request_id
        or identity.principal_hash != scope.principal_hash
        or identity.delete_epoch != scope.session_epoch
        or type(identity.incarnation_id) is not str
        or _GENERATION_RE.fullmatch(identity.incarnation_id) is None
        or type(identity.canonical_namespace_digest) is not str
        or _HASH_RE.fullmatch(identity.canonical_namespace_digest) is None
    ):
        raise _unavailable()
    return _OwnerBinding(
        scope.session_id,
        identity.principal_hash,
        identity.delete_epoch,
        identity.incarnation_id,
        identity.canonical_namespace_digest,
    )


def _storage_prefix(scope: ContextEventScope, owner: _OwnerBinding) -> str:
    owner_tag = _sha256(_canonical(owner.wire()))
    return _history.rolling_history_session_prefix(scope.session_id) + _LOCATOR_NAMESPACE + owner_tag + ":"


def _history_prefix(scope: ContextEventScope) -> str:
    return _history._scope_prefix(scope)


def _history_head(handle, scope: ContextEventScope, prefix: str) -> tuple[int, str]:
    raw = _read_setting(handle, prefix + "meta:head")
    if raw is None:
        return 0, _history._genesis_chain(scope)
    if type(raw) is not str:
        raise _unavailable()
    try:
        value = json.loads(raw)
    except Exception:
        raise _unavailable() from None
    expected = {
        "schema_version": _history._SCHEMA_VERSION,
        "kind": "history_head",
        "scope": _history._identity(scope),
    }
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "kind", "scope", "last_sequence", "chain_sha256"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != _history._SCHEMA_VERSION
        or any(value.get(key) != item for key, item in expected.items())
        or type(value["last_sequence"]) is not int
        or value["last_sequence"] < 0
        or type(value["chain_sha256"]) is not str
        or _HASH_RE.fullmatch(value["chain_sha256"]) is None
        or _canonical(value) != raw
        or (value["last_sequence"] == 0 and value["chain_sha256"] != _history._genesis_chain(scope))
    ):
        raise _unavailable()
    return value["last_sequence"], value["chain_sha256"]


def _require_v1_only(handle, scope: ContextEventScope) -> None:
    """Refuse any current or residual v2 revision namespace; never fall back."""
    prefix = _history.rolling_history_session_prefix(scope.session_id) + "revision-ledger:v2:"
    rows = _rows(
        handle,
        "SELECT 1 AS present FROM settings WHERE key >= ? AND key < ? ORDER BY key LIMIT 1",
        (prefix, prefix + "\uffff"),
        ("present",),
    )
    if rows:
        raise _unavailable()


def _source_fence(handle, session_id: str) -> LegacyMessageSourceFence:
    try:
        fence = read_message_source_fence_in_transaction(handle, session_id)
    except Exception:
        raise _unavailable() from None
    if (
        type(fence) is not LegacyMessageSourceFence
        or any(
            type(value) is not int or value < 0
            for value in (
                fence.message_source_generation,
                fence.message_count,
                fence.non_append_generation,
            )
        )
    ):
        raise _unavailable()
    return fence


def _fence_wire(fence: LegacyMessageSourceFence) -> dict[str, int]:
    return {
        "message_source_generation": fence.message_source_generation,
        "message_count": fence.message_count,
        "non_append_generation": fence.non_append_generation,
    }


def _validate_append_only_fence(snapshot: dict[str, Any], current: LegacyMessageSourceFence) -> None:
    prior = snapshot.get("source_fence")
    if (
        type(prior) is not dict
        or set(prior) != {"message_source_generation", "message_count", "non_append_generation"}
        or any(type(prior[name]) is not int or prior[name] < 0 for name in prior)
        or current.non_append_generation != prior["non_append_generation"]
        or current.message_source_generation < prior["message_source_generation"]
        or current.message_count < prior["message_count"]
        or current.message_source_generation - prior["message_source_generation"]
        != current.message_count - prior["message_count"]
    ):
        raise _unavailable()


def _require_object_keys(handle, row_key: str, path: str, expected: frozenset[str]) -> None:
    if path == "$":
        statement = (
            "SELECT substr(item.key, 1, 257) AS object_key "
            "FROM settings AS source, json_each(source.value) AS item "
            "WHERE source.key = ? ORDER BY item.key LIMIT ?"
        )
    else:
        statement = (
            "SELECT substr(item.key, 1, 257) AS object_key "
            "FROM settings AS source, json_each(source.value, ?) AS item "
            "WHERE source.key = ? ORDER BY item.key LIMIT ?"
        )
    parameters = (row_key, len(expected) + 1) if path == "$" else (
        path, row_key, len(expected) + 1,
    )
    rows = _rows(handle, statement, parameters, ("object_key",))
    keys = tuple(row[0] for row in rows)
    if (
        len(keys) != len(expected)
        or any(type(key) is not str or len(key) > 256 for key in keys)
        or frozenset(keys) != expected
    ):
        raise _unavailable()


def _event_header(
    handle,
    scope: ContextEventScope,
    prefix: str,
    sequence: int,
) -> _EventHeader:
    if type(sequence) is not int or sequence < 1:
        raise _unavailable()
    event_key = _history._event_key(prefix, sequence)
    rows = _rows(
        handle,
        "SELECT json_type(value) AS envelope_type, "
        "json_type(value, '$.schema_version') AS schema_version_type, "
        "json_extract(value, '$.schema_version') AS schema_version, "
        "substr(json_extract(value, '$.kind'), 1, 16) AS kind, "
        "substr(json_extract(value, '$.scope.session_id'), 1, 129) AS session_id, "
        "substr(json_extract(value, '$.scope.principal_hash'), 1, 65) AS principal_hash, "
        "substr(json_extract(value, '$.scope.session_epoch'), 1, 33) AS session_epoch, "
        "substr(json_extract(value, '$.request_id'), 1, 33) AS request_id, "
        "json_type(value, '$.sequence') AS sequence_type, "
        "json_extract(value, '$.sequence') AS sequence, "
        "substr(json_extract(value, '$.event_id'), 1, 65) AS event_id, "
        "substr(json_extract(value, '$.idempotency_id'), 1, 65) AS idempotency_id, "
        "substr(json_extract(value, '$.content_sha256'), 1, 65) AS content_sha256, "
        "substr(json_extract(value, '$.previous_chain_sha256'), 1, 65) AS previous_chain_sha256, "
        "substr(json_extract(value, '$.chain_sha256'), 1, 65) AS chain_sha256, "
        "substr(json_extract(value, '$.payload.event_type'), 1, 129) AS event_type, "
        "json_type(value, '$.payload.content') AS content_type, "
        "length(CAST(json_extract(value, '$.payload.content') AS BLOB)) AS content_bytes, "
        "json_type(value, '$.payload.attachments') AS attachments_type, "
        "json_array_length(value, '$.payload.attachments') AS attachment_count "
        "FROM settings WHERE key = ?",
        (event_key,),
        (
            "envelope_type", "schema_version_type", "schema_version", "kind", "session_id",
            "principal_hash", "session_epoch", "request_id", "sequence_type", "sequence",
            "event_id", "idempotency_id", "content_sha256", "previous_chain_sha256",
            "chain_sha256", "event_type", "content_type", "content_bytes",
            "attachments_type", "attachment_count",
        ),
    )
    if len(rows) != 1:
        raise _unavailable()
    row = rows[0]
    if (
        row[0] != "object"
        or row[1] != "integer"
        or type(row[2]) is not int or row[2] != _history._SCHEMA_VERSION
        or row[3] != "event"
        or (row[4], row[5], row[6]) != (
            scope.session_id, scope.principal_hash, scope.session_epoch,
        )
        or type(row[7]) is not str or _REQUEST_RE.fullmatch(row[7]) is None
        or row[8] != "integer"
        or type(row[9]) is not int or row[9] != sequence
        or type(row[10]) is not str or _HASH_RE.fullmatch(row[10]) is None
        or type(row[11]) is not str or _HASH_RE.fullmatch(row[11]) is None
        or type(row[12]) is not str or _HASH_RE.fullmatch(row[12]) is None
        or type(row[13]) is not str or _HASH_RE.fullmatch(row[13]) is None
        or type(row[14]) is not str or _HASH_RE.fullmatch(row[14]) is None
        or type(row[15]) is not str or row[15] not in _history._EVENT_TYPES
        or row[16] != "text"
        or type(row[17]) is not int or row[17] < 0
        or row[18] != "array"
        or type(row[19]) is not int or row[19] < 0
    ):
        raise _unavailable()
    _require_object_keys(handle, event_key, "$", _EVENT_FIELDS)
    _require_object_keys(handle, event_key, "$.scope", _SCOPE_FIELDS)
    _require_object_keys(handle, event_key, "$.payload", _PAYLOAD_FIELDS)

    event_scope = ContextEventScope(
        scope.session_id, row[7], scope.principal_hash, scope.session_epoch,
    )
    expected_chain = _history._event_chain_digest(
        event_scope, sequence, row[13], row[10], row[12],
    )
    if expected_chain != row[14]:
        raise _unavailable()
    header = _EventHeader(
        sequence=sequence,
        request_id=row[7],
        event_id=row[10],
        content_sha256=row[12],
        previous_chain_sha256=row[13],
        chain_sha256=row[14],
        event_type=row[15],
        content_utf8_byte_length=row[17],
        has_attachments=row[19] > 0,
    )
    _validate_event_id_mapping(handle, scope, prefix, header)
    return header


def _validate_event_id_mapping(handle, scope: ContextEventScope, prefix: str, header: _EventHeader) -> None:
    raw = _read_setting(handle, _history._event_id_key(prefix, header.event_id))
    if type(raw) is not str:
        raise _unavailable()
    try:
        value = json.loads(raw)
    except Exception:
        raise _unavailable() from None
    expected = {
        "schema_version": _history._SCHEMA_VERSION,
        "kind": "event_id",
        "scope": _history._identity(scope),
        "event_id": header.event_id,
        "request_id": header.request_id,
        "sequence": header.sequence,
        "content_sha256": header.content_sha256,
        "chain_sha256": header.chain_sha256,
    }
    if type(value) is not dict or value != expected or _canonical(value) != raw:
        raise _unavailable()


def _attachment_at(handle, event_key: str, ordinal: int) -> dict[str, Any] | None:
    rows = _rows(
        handle,
        "SELECT CAST(item.key AS INTEGER) AS ordinal, "
        "json_type(item.value, '$.attachment_id') AS attachment_id_type, "
        "substr(json_extract(item.value, '$.attachment_id'), 1, 257) AS attachment_id, "
        "json_type(item.value, '$.sha256') AS sha256_type, "
        "substr(json_extract(item.value, '$.sha256'), 1, 65) AS sha256, "
        "json_type(item.value, '$.size_bytes') AS size_bytes_type, "
        "json_extract(item.value, '$.size_bytes') AS size_bytes, "
        "json_type(item.value, '$.media_type') AS media_type_type, "
        "substr(json_extract(item.value, '$.media_type'), 1, 256) AS media_type, "
        "json_type(item.value) AS item_type, "
        "(SELECT count(*) FROM json_each(item.value)) AS field_count "
        "FROM settings AS source, json_each(source.value, '$.payload.attachments') AS item "
        "WHERE source.key = ? AND CAST(item.key AS INTEGER) >= ? "
        "ORDER BY CAST(item.key AS INTEGER) LIMIT 1",
        (event_key, ordinal),
        (
            "ordinal", "attachment_id_type", "attachment_id", "sha256_type", "sha256",
            "size_bytes_type", "size_bytes", "media_type_type", "media_type", "item_type",
            "field_count",
        ),
    )
    if not rows:
        return None
    row = rows[0]
    if (
        type(row[0]) is not int or row[0] != ordinal
        or row[1] != "text" or type(row[2]) is not str or len(row[2]) > 256
        or row[3] != "text" or type(row[4]) is not str or len(row[4]) > 64
        or row[5] != "integer" or type(row[6]) is not int or row[6] < 0
        or row[7] != "text" or type(row[8]) is not str or len(row[8]) > 255
        or row[9] != "object"
        or type(row[10]) is not int or row[10] != 4
    ):
        raise _unavailable()
    parsed = _history._parse_attachment({
        "attachment_id": row[2],
        "sha256": row[4],
        "size_bytes": row[6],
        "media_type": row[8],
    })
    return asdict(parsed)


def _read_body_slice(handle, event_key: str, offset: int, maximum: int) -> bytes:
    rows = _rows(
        handle,
        "SELECT substr(CAST(json_extract(value, '$.payload.content') AS BLOB), ?, ?) AS body_slice "
        "FROM settings WHERE key = ?",
        (offset + 1, maximum, event_key),
        ("body_slice",),
    )
    if len(rows) != 1 or type(rows[0][0]) is not bytes or len(rows[0][0]) > maximum:
        raise _unavailable()
    return rows[0][0]


def _escaped_json_string_content(value: str) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if type(encoded) is not str or len(encoded) < 2 or encoded[0] != '"' or encoded[-1] != '"':
        raise _unavailable()
    return encoded[1:-1].encode("utf-8", errors="strict")


def _chunk_offset_key(base: str, event_id: str, offset: int) -> str:
    return f"{base}body:{event_id}:offset:{offset:032d}"


def _chunk_data_key(base: str, event_id: str, ordinal: int) -> str:
    return f"{base}body:{event_id}:chunk:{ordinal:032d}"


def _node_key(base: str, event_id: str, level: int, index: int) -> str:
    return f"{base}body:{event_id}:node:{level:08d}:{index:032d}"


def _manifest_key(base: str, event_id: str) -> str:
    return f"{base}body:{event_id}:manifest"


def _leaf_digest(header: _EventHeader, ordinal: int, offset: int, body: bytes) -> str:
    commitment = _canonical({
        "event_id": header.event_id,
        "sequence": header.sequence,
        "content_sha256": header.content_sha256,
        "chain_sha256": header.chain_sha256,
        "ordinal": ordinal,
        "offset": offset,
        "length": len(body),
    }).encode("utf-8")
    return _sha256(b"rolling-history-body-leaf-v1\0" + commitment + b"\0" + body)


def _node_digest(left: str, right: str) -> str:
    if _HASH_RE.fullmatch(left) is None or _HASH_RE.fullmatch(right) is None:
        raise _unavailable()
    return _sha256(
        b"rolling-history-body-node-v1\0"
        + bytes.fromhex(left)
        + bytes.fromhex(right)
    )


def _index_event(
    handle,
    scope: ContextEventScope,
    prefix: str,
    storage_prefix: str,
    sequence: int,
) -> _BodyManifest:
    header = _event_header(handle, scope, prefix, sequence)
    manifest_key = _manifest_key(storage_prefix, header.event_id)
    existing = _read_setting(handle, manifest_key)
    if existing is not None:
        return _decode_manifest(existing, header)

    event_key = _history._event_key(prefix, sequence)
    payload_digest = hashlib.sha256()
    payload_digest.update(b'{"attachments":[')
    attachment_ordinal = 0
    while True:
        attachment = _attachment_at(handle, event_key, attachment_ordinal)
        if attachment is None:
            break
        if attachment_ordinal:
            payload_digest.update(b",")
        payload_digest.update(_canonical(attachment).encode("utf-8"))
        attachment_ordinal += 1
    payload_digest.update(b'],"content":"')

    body_digest = hashlib.sha256()
    chunk_count = 0
    byte_offset = 0
    while byte_offset < header.content_utf8_byte_length:
        candidate_length = min(
            _INDEX_CHUNK_BYTES,
            header.content_utf8_byte_length - byte_offset,
        )
        read_length = min(
            _INDEX_CHUNK_BYTES + 4,
            header.content_utf8_byte_length - byte_offset,
        )
        raw_slice = _read_body_slice(handle, event_key, byte_offset, read_length)
        if not raw_slice:
            raise _unavailable()
        end = candidate_length
        if byte_offset + end < header.content_utf8_byte_length:
            while end > 0 and end < len(raw_slice) and raw_slice[end] & 0xC0 == 0x80:
                end -= 1
        if end <= 0:
            raise _unavailable()
        body = raw_slice[:end]
        try:
            body_text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise _unavailable() from None
        payload_digest.update(_escaped_json_string_content(body_text))
        body_digest.update(body)

        leaf = _leaf_digest(header, chunk_count, byte_offset, body)
        _insert_cas(handle, _chunk_data_key(storage_prefix, header.event_id, chunk_count), body)
        offset_value = {
            "schema_version": _SCHEMA_VERSION,
            "kind": "body_chunk_offset",
            "ordinal": chunk_count,
            "start_offset": byte_offset,
            "end_offset": byte_offset + len(body),
            "leaf_sha256": leaf,
        }
        _insert_cas(
            handle,
            _chunk_offset_key(storage_prefix, header.event_id, byte_offset),
            _canonical(offset_value),
        )
        _insert_cas(handle, _node_key(storage_prefix, header.event_id, 0, chunk_count), leaf)

        byte_offset += len(body)
        chunk_count += 1

    payload_digest.update(b'","event_type":')
    payload_digest.update(_canonical(header.event_type).encode("utf-8"))
    payload_digest.update(b"}")
    if payload_digest.hexdigest() != header.content_sha256:
        raise _unavailable()

    if chunk_count == 0:
        if header.content_utf8_byte_length != 0:
            raise _unavailable()
        root = _sha256(
            b"rolling-history-body-empty-v1\0"
            + _canonical({
                "event_id": header.event_id,
                "sequence": header.sequence,
                "content_sha256": header.content_sha256,
                "chain_sha256": header.chain_sha256,
            }).encode("utf-8")
        )
        depth = 0
    else:
        current_count = chunk_count
        level = 0
        while current_count > 1:
            parent_count = (current_count + 1) // 2
            for parent_index in range(parent_count):
                left_index = parent_index * 2
                left = _read_setting(
                    handle,
                    _node_key(storage_prefix, header.event_id, level, left_index),
                )
                if type(left) is not str or _HASH_RE.fullmatch(left) is None:
                    raise _unavailable()
                right_index = left_index + 1
                if right_index < current_count:
                    right = _read_setting(
                        handle,
                        _node_key(storage_prefix, header.event_id, level, right_index),
                    )
                    if type(right) is not str or _HASH_RE.fullmatch(right) is None:
                        raise _unavailable()
                else:
                    right = left
                _insert_cas(
                    handle,
                    _node_key(storage_prefix, header.event_id, level + 1, parent_index),
                    _node_digest(left, right),
                )
            current_count = parent_count
            level += 1
        root_value = _read_setting(
            handle,
            _node_key(storage_prefix, header.event_id, level, 0),
        )
        if type(root_value) is not str or _HASH_RE.fullmatch(root_value) is None:
            raise _unavailable()
        root = root_value
        depth = level

    body_sha = body_digest.hexdigest()
    manifest_body = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "body_manifest",
        "event_id": header.event_id,
        "sequence": header.sequence,
        "content_sha256": header.content_sha256,
        "chain_sha256": header.chain_sha256,
        "body_utf8_byte_length": header.content_utf8_byte_length,
        "body_sha256": body_sha,
        "chunk_size": _INDEX_CHUNK_BYTES,
        "chunk_count": chunk_count,
        "merkle_depth": depth,
        "merkle_root": root,
        "attachment_count": attachment_ordinal,
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": _sha256(_canonical(manifest_body)),
    }
    encoded_manifest = _canonical(manifest)
    _insert_cas(handle, manifest_key, encoded_manifest)
    return _decode_manifest(encoded_manifest, header)


def _decode_manifest(raw: object, header: _EventHeader) -> _BodyManifest:
    if type(raw) is not str:
        raise _unavailable()
    try:
        value = json.loads(raw)
    except Exception:
        raise _unavailable() from None
    required = {
        "schema_version", "kind", "event_id", "sequence", "content_sha256",
        "chain_sha256", "body_utf8_byte_length", "body_sha256", "chunk_size",
        "chunk_count", "merkle_depth", "merkle_root", "attachment_count", "manifest_sha256",
    }
    if (
        type(value) is not dict or set(value) != required
        or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
        or value["kind"] != "body_manifest"
        or value["event_id"] != header.event_id
        or type(value["sequence"]) is not int or value["sequence"] != header.sequence
        or value["content_sha256"] != header.content_sha256
        or value["chain_sha256"] != header.chain_sha256
        or type(value["body_utf8_byte_length"]) is not int
        or value["body_utf8_byte_length"] != header.content_utf8_byte_length
        or type(value["body_sha256"]) is not str or _HASH_RE.fullmatch(value["body_sha256"]) is None
        or type(value["chunk_size"]) is not int or value["chunk_size"] != _INDEX_CHUNK_BYTES
        or type(value["chunk_count"]) is not int or value["chunk_count"] < 0
        or type(value["merkle_depth"]) is not int or value["merkle_depth"] < 0
        or type(value["merkle_root"]) is not str or _HASH_RE.fullmatch(value["merkle_root"]) is None
        or type(value["attachment_count"]) is not int or value["attachment_count"] < 0
        or type(value["manifest_sha256"]) is not str
        or _HASH_RE.fullmatch(value["manifest_sha256"]) is None
        or _canonical(value) != raw
    ):
        raise _unavailable()
    body = {key: value[key] for key in value if key != "manifest_sha256"}
    if _sha256(_canonical(body)) != value["manifest_sha256"]:
        raise _unavailable()
    count = value["chunk_count"]
    if (
        (count == 0 and value["body_utf8_byte_length"] != 0)
        or (count > 0 and not (count <= value["body_utf8_byte_length"] <= count * _INDEX_CHUNK_BYTES))
    ):
        raise _unavailable()
    depth = 0
    tree_width = count
    while tree_width > 1:
        depth += 1
        tree_width = (tree_width + 1) // 2
    if depth != value["merkle_depth"]:
        raise _unavailable()
    return _BodyManifest(
        event_id=value["event_id"],
        sequence=value["sequence"],
        content_sha256=value["content_sha256"],
        chain_sha256=value["chain_sha256"],
        body_utf8_byte_length=value["body_utf8_byte_length"],
        body_sha256=value["body_sha256"],
        chunk_count=count,
        merkle_root=value["merkle_root"],
        manifest_sha256=value["manifest_sha256"],
    )


def _read_manifest(handle, storage_prefix: str, header: _EventHeader) -> _BodyManifest:
    raw = _read_setting(handle, _manifest_key(storage_prefix, header.event_id))
    if raw is None:
        raise _unavailable()
    manifest = _decode_manifest(raw, header)
    if manifest.chunk_count:
        root = _read_setting(
            handle,
            _node_key(
                storage_prefix,
                header.event_id,
                manifest.merkle_depth,
                0,
            ),
        )
        if root != manifest.merkle_root:
            raise _unavailable()
    return manifest


def _read_locator_value(handle, storage_prefix: str, token: str) -> dict[str, Any]:
    if type(token) is not str or _HANDLE_RE.fullmatch(token) is None:
        raise _unavailable()
    raw = _read_setting(handle, storage_prefix + "handle:" + _sha256(token))
    if type(raw) is not str:
        raise _unavailable()
    try:
        value = json.loads(raw)
    except Exception:
        raise _unavailable() from None
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "kind", "snapshot_id", "position"}
        or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
        or value["kind"] != "locator"
        or type(value["snapshot_id"]) is not str or _HASH_RE.fullmatch(value["snapshot_id"]) is None
        or type(value["position"]) is not dict
        or _canonical(value) != raw
    ):
        raise _unavailable()
    return value


def _read_snapshot(handle, storage_prefix: str, snapshot_id: str) -> dict[str, Any]:
    raw = _read_setting(handle, storage_prefix + "snapshot:" + snapshot_id)
    if type(raw) is not str:
        raise _unavailable()
    try:
        value = json.loads(raw)
    except Exception:
        raise _unavailable() from None
    if (
        type(value) is not dict
        or set(value) != {
            "schema_version", "kind", "snapshot_id", "owner", "history_scope_digest",
            "head_sequence", "head_chain_sha256", "source_fence",
        }
        or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
        or value["kind"] != "snapshot"
        or type(value["snapshot_id"]) is not str or value["snapshot_id"] != snapshot_id
        or type(value["owner"]) is not dict
        or type(value["history_scope_digest"]) is not str
        or _HASH_RE.fullmatch(value["history_scope_digest"]) is None
        or type(value["head_sequence"]) is not int or value["head_sequence"] < 0
        or type(value["head_chain_sha256"]) is not str
        or _HASH_RE.fullmatch(value["head_chain_sha256"]) is None
        or type(value["source_fence"]) is not dict
        or _canonical(value) != raw
    ):
        raise _unavailable()
    body = {key: value[key] for key in value if key != "snapshot_id"}
    if _sha256(_canonical(body)) != snapshot_id:
        raise _unavailable()
    return value


def _position_fields(position: dict[str, Any]) -> str:
    kind = position.get("kind")
    required = {
        "snapshot": {"kind", "after_sequence", "after_chain_sha256"},
        "page": {"kind", "after_sequence", "after_chain_sha256"},
        "event": {
            "kind", "sequence", "event_id", "request_id", "version",
            "content_sha256", "chain_sha256",
        },
        "chunk": {"kind", "event_sequence", "offset"},
    }.get(kind)
    if required is None or set(position) != required:
        raise _unavailable()
    if kind in {"snapshot", "page"}:
        if (
            type(position["after_sequence"]) is not int
            or position["after_sequence"] < 0
            or type(position["after_chain_sha256"]) is not str
            or _HASH_RE.fullmatch(position["after_chain_sha256"]) is None
        ):
            raise _unavailable()
    elif kind == "event":
        if (
            type(position["sequence"]) is not int or position["sequence"] < 1
            or type(position["event_id"]) is not str or _HASH_RE.fullmatch(position["event_id"]) is None
            or type(position["request_id"]) is not str or _REQUEST_RE.fullmatch(position["request_id"]) is None
            or type(position["version"]) is not int or position["version"] != _history._SCHEMA_VERSION
            or type(position["content_sha256"]) is not str
            or _HASH_RE.fullmatch(position["content_sha256"]) is None
            or type(position["chain_sha256"]) is not str
            or _HASH_RE.fullmatch(position["chain_sha256"]) is None
        ):
            raise _unavailable()
    elif (
        type(position["event_sequence"]) is not int or position["event_sequence"] < 1
        or type(position["offset"]) is not int or position["offset"] < 0
    ):
        raise _unavailable()
    return kind


def _resolve_locator(
    handle,
    storage_prefix: str,
    token: str,
    owner: _OwnerBinding,
    allowed_kinds: frozenset[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    locator = _read_locator_value(handle, storage_prefix, token)
    position = locator["position"]
    kind = _position_fields(position)
    if kind not in allowed_kinds:
        raise _unavailable()
    snapshot = _read_snapshot(handle, storage_prefix, locator["snapshot_id"])
    if snapshot["owner"] != owner.wire():
        raise _unavailable()
    return locator, snapshot, position


def _locator_natural_key(snapshot_id: str, position: dict[str, Any]) -> str:
    return _canonical({"snapshot_id": snapshot_id, "position": position})


def _get_or_create_locator(
    handle,
    storage_prefix: str,
    snapshot_id: str,
    position: dict[str, Any],
) -> str:
    _position_fields(position)
    natural = _locator_natural_key(snapshot_id, position)
    index_key = storage_prefix + "locator-index:" + _sha256(natural)
    raw_index = _read_setting(handle, index_key)
    if raw_index is None:
        token = secrets.token_urlsafe(32)
        if _HANDLE_RE.fullmatch(token) is None:
            raise _unavailable()
        locator_value = _canonical({
            "schema_version": _SCHEMA_VERSION,
            "kind": "locator",
            "snapshot_id": snapshot_id,
            "position": position,
        })
        _insert_cas(handle, storage_prefix + "handle:" + _sha256(token), locator_value)
        index_value = _canonical({
            "schema_version": _SCHEMA_VERSION,
            "kind": "locator_index",
            "natural_key": natural,
            "token": token,
        })
        _insert_cas(handle, index_key, index_value)
        return token
    if type(raw_index) is not str:
        raise _unavailable()
    try:
        index = json.loads(raw_index)
    except Exception:
        raise _unavailable() from None
    if (
        type(index) is not dict
        or set(index) != {"schema_version", "kind", "natural_key", "token"}
        or type(index["schema_version"]) is not int or index["schema_version"] != _SCHEMA_VERSION
        or index["kind"] != "locator_index"
        or index["natural_key"] != natural
        or type(index["token"]) is not str or _HANDLE_RE.fullmatch(index["token"]) is None
        or _canonical(index) != raw_index
    ):
        raise _unavailable()
    locator = _read_locator_value(handle, storage_prefix, index["token"])
    if locator != {
        "schema_version": _SCHEMA_VERSION,
        "kind": "locator",
        "snapshot_id": snapshot_id,
        "position": position,
    }:
        raise _unavailable()
    return index["token"]


def _create_snapshot(
    handle,
    scope: ContextEventScope,
    owner: _OwnerBinding,
    storage_prefix: str,
    history_scope_digest: str,
    source_fence: LegacyMessageSourceFence,
    head: tuple[int, str],
) -> tuple[str, str]:
    body = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "snapshot",
        "owner": owner.wire(),
        "history_scope_digest": history_scope_digest,
        "head_sequence": head[0],
        "head_chain_sha256": head[1],
        "source_fence": _fence_wire(source_fence),
    }
    snapshot_id = _sha256(_canonical(body))
    snapshot = {**body, "snapshot_id": snapshot_id}
    _insert_cas(handle, storage_prefix + "snapshot:" + snapshot_id, _canonical(snapshot))
    locator = _get_or_create_locator(
        handle,
        storage_prefix,
        snapshot_id,
        {
            "kind": "snapshot",
            "after_sequence": 0,
            "after_chain_sha256": _history._genesis_chain(scope),
        },
    )
    return snapshot_id, locator


def _validate_snapshot_current(
    handle,
    scope: ContextEventScope,
    owner: _OwnerBinding,
    history_prefix: str,
    storage_prefix: str,
    snapshot: dict[str, Any],
    *,
    require_anchor_manifest: bool,
) -> tuple[int, str]:
    if (
        snapshot["owner"] != owner.wire()
        or snapshot["history_scope_digest"] != _history._scope_digest(scope)
    ):
        raise _unavailable()
    _require_v1_only(handle, scope)
    current_fence = _source_fence(handle, owner.session_id)
    _validate_append_only_fence(snapshot, current_fence)
    current_head = _history_head(handle, scope, history_prefix)
    pinned_sequence = snapshot["head_sequence"]
    pinned_chain = snapshot["head_chain_sha256"]
    if current_head[0] < pinned_sequence:
        raise _unavailable()
    if pinned_sequence == 0:
        if pinned_chain != _history._genesis_chain(scope):
            raise _unavailable()
    else:
        anchor = _event_header(handle, scope, history_prefix, pinned_sequence)
        if anchor.chain_sha256 != pinned_chain:
            raise _unavailable()
        if anchor.previous_chain_sha256 != _previous_chain_for(
            handle, scope, history_prefix, pinned_sequence,
        ):
            raise _unavailable()
        if require_anchor_manifest:
            _read_manifest(handle, storage_prefix, anchor)
    if current_head[0] == pinned_sequence:
        if current_head[1] != pinned_chain:
            raise _unavailable()
    else:
        successor = _event_header(handle, scope, history_prefix, pinned_sequence + 1)
        expected_previous = pinned_chain if pinned_sequence else _history._genesis_chain(scope)
        if successor.previous_chain_sha256 != expected_previous:
            raise _unavailable()
    return current_head


def _previous_chain_for(handle, scope: ContextEventScope, prefix: str, sequence: int) -> str:
    if sequence == 1:
        return _history._genesis_chain(scope)
    parent = _event_header(handle, scope, prefix, sequence - 1)
    return parent.chain_sha256


def _run_owner_operation(
    authenticated_user: dict | None,
    mode: OperationMode,
    expected_owner_identity: RollingHistoryOwnerIdentity,
    callback: Callable[[Any, ContextEventScope, RollingHistoryOwnerIdentity, _OwnerBinding], Any],
):
    if (
        type(authenticated_user) is not dict
        or type(expected_owner_identity) is not RollingHistoryOwnerIdentity
        or type(mode) is not OperationMode
    ):
        raise _unavailable()
    try:
        with bootstrap_rolling_history_owner_scope(authenticated_user, ScopeOrigin.TOOL):
            scope = get_tool_event_scope()
            identity = get_rolling_history_owner_identity()
            owner = _require_owner_binding(scope, identity)
            if (
                expected_owner_identity.session_id != identity.session_id
                or expected_owner_identity.request_id != identity.request_id
                or expected_owner_identity.principal_hash != identity.principal_hash
                or expected_owner_identity.delete_epoch != identity.delete_epoch
                or expected_owner_identity.incarnation_id != identity.incarnation_id
                or expected_owner_identity.canonical_namespace_digest != identity.canonical_namespace_digest
            ):
                raise _unavailable()

            def operation(handle):
                current_scope = get_tool_event_scope()
                current_identity = get_rolling_history_owner_identity()
                current_owner = _require_owner_binding(current_scope, current_identity)
                if current_owner != owner:
                    raise _unavailable()
                return callback(handle, current_scope, current_identity, current_owner)

            return run_rolling_history_owner_operation(
                authenticated_user,
                mode,
                operation,
            )
    except RollingHistoryLocatorUnavailable:
        raise
    except Exception:
        raise _unavailable() from None


def _snapshot_and_position(
    handle,
    scope: ContextEventScope,
    owner: _OwnerBinding,
    storage_prefix: str,
    token: str,
    allowed_kinds: frozenset[str],
    *,
    require_anchor_manifest: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], tuple[int, str]]:
    locator, snapshot, position = _resolve_locator(
        handle, storage_prefix, token, owner, allowed_kinds,
    )
    if snapshot["history_scope_digest"] != _history._scope_digest(scope):
        raise _unavailable()
    current_head = _validate_snapshot_current(
        handle,
        scope,
        owner,
        _history_prefix(scope),
        storage_prefix,
        snapshot,
        require_anchor_manifest=require_anchor_manifest,
    )
    return locator, snapshot, position, current_head


def open_owner_history_snapshot(
    authenticated_user: dict | None,
    *,
    expected_owner_identity: RollingHistoryOwnerIdentity,
    snapshot_handle: str | None = None,
) -> str:
    """Open the current immutable v1 head, or idempotently reopen a locator."""
    if snapshot_handle is not None and (
        type(snapshot_handle) is not str or _HANDLE_RE.fullmatch(snapshot_handle) is None
    ):
        raise _unavailable()

    def resolve_snapshot(handle, scope, _identity, owner, token: str):
        history_prefix = _history_prefix(scope)
        storage_prefix = _storage_prefix(scope, owner)
        _locator, snapshot, position = _resolve_locator(
            handle,
            storage_prefix,
            token,
            owner,
            frozenset({"snapshot"}),
        )
        if position != {
            "kind": "snapshot",
            "after_sequence": 0,
            "after_chain_sha256": _history._genesis_chain(scope),
        }:
            raise _unavailable()
        _validate_snapshot_current(
            handle,
            scope,
            owner,
            history_prefix,
            storage_prefix,
            snapshot,
            require_anchor_manifest=False,
        )
        return token

    if snapshot_handle is not None:
        return _run_owner_operation(
            authenticated_user,
            OperationMode.READ,
            expected_owner_identity,
            lambda handle, scope, identity, owner: resolve_snapshot(
                handle, scope, identity, owner, snapshot_handle,
            ),
        )

    def operation(handle, scope, _identity, owner):
        history_prefix = _history_prefix(scope)
        storage_prefix = _storage_prefix(scope, owner)

        _require_v1_only(handle, scope)
        fence = _source_fence(handle, owner.session_id)
        head = _history_head(handle, scope, history_prefix)
        _snapshot_id, locator = _create_snapshot(
            handle,
            scope,
            owner,
            storage_prefix,
            _history._scope_digest(scope),
            fence,
            head,
        )
        return locator

    created_handle = _run_owner_operation(
        authenticated_user,
        OperationMode.WRITE,
        expected_owner_identity,
        operation,
    )
    return _run_owner_operation(
        authenticated_user,
        OperationMode.READ,
        expected_owner_identity,
        lambda handle, scope, identity, owner: resolve_snapshot(
            handle, scope, identity, owner, created_handle,
        ),
    )


def _page_plan(
    handle,
    scope: ContextEventScope,
    owner: _OwnerBinding,
    snapshot_handle: str,
    limit: int,
) -> tuple[dict[str, Any], dict[str, Any], int, str, tuple[int, ...]]:
    storage_prefix = _storage_prefix(scope, owner)
    locator, snapshot, position, current_head = _snapshot_and_position(
        handle,
        scope,
        owner,
        storage_prefix,
        snapshot_handle,
        frozenset({"snapshot", "page"}),
        require_anchor_manifest=False,
    )
    del locator
    if type(limit) is not int or not 1 <= limit <= _MAX_PAGE_SIZE:
        raise _unavailable()
    after = position["after_sequence"]
    after_chain = position["after_chain_sha256"]
    pinned_head = snapshot["head_sequence"]
    if (
        type(after) is not int or after < 0 or after > pinned_head
        or type(after_chain) is not str or _HASH_RE.fullmatch(after_chain) is None
    ):
        raise _unavailable()
    if after == 0:
        if after_chain != _history._genesis_chain(scope):
            raise _unavailable()
    else:
        anchor = _event_header(handle, scope, _history_prefix(scope), after)
        if anchor.chain_sha256 != after_chain:
            raise _unavailable()
    count = min(limit, pinned_head - after)
    sequences = tuple(range(after + 1, after + count + 1))
    return snapshot, position, after, after_chain, sequences


def _ensure_event_indexed(
    authenticated_user: dict,
    expected_owner_identity: RollingHistoryOwnerIdentity,
    snapshot_handle: str,
    sequence: int,
) -> None:
    def operation(handle, scope, _identity, owner):
        storage_prefix = _storage_prefix(scope, owner)
        locator, snapshot, position = _resolve_locator(
            handle,
            storage_prefix,
            snapshot_handle,
            owner,
            frozenset({"snapshot", "page"}),
        )
        del locator, position
        _validate_snapshot_current(
            handle,
            scope,
            owner,
            _history_prefix(scope),
            storage_prefix,
            snapshot,
            require_anchor_manifest=False,
        )
        if sequence < 1 or sequence > snapshot["head_sequence"]:
            raise _unavailable()
        _index_event(
            handle,
            scope,
            _history_prefix(scope),
            storage_prefix,
            sequence,
        )

    _run_owner_operation(
        authenticated_user,
        OperationMode.WRITE,
        expected_owner_identity,
        operation,
    )


def _prepare_page_locators(
    authenticated_user: dict,
    expected_owner_identity: RollingHistoryOwnerIdentity,
    snapshot_handle: str,
    limit: int,
) -> tuple[str, tuple[str, ...], str | None]:
    def operation(handle, scope, _identity, owner):
        storage_prefix = _storage_prefix(scope, owner)
        _locator, snapshot, position, _current_head = _snapshot_and_position(
            handle,
            scope,
            owner,
            storage_prefix,
            snapshot_handle,
            frozenset({"snapshot", "page"}),
            require_anchor_manifest=snapshot_handle is not None,
        )
        after = position["after_sequence"]
        after_chain = position["after_chain_sha256"]
        if type(limit) is not int or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise _unavailable()
        if after == 0:
            if after_chain != _history._genesis_chain(scope):
                raise _unavailable()
        else:
            anchor = _event_header(handle, scope, _history_prefix(scope), after)
            if anchor.chain_sha256 != after_chain:
                raise _unavailable()
            _read_manifest(handle, storage_prefix, anchor)
        count = min(limit, snapshot["head_sequence"] - after)
        event_handles: list[str] = []
        last_sequence = after
        last_chain = after_chain
        for sequence in range(after + 1, after + count + 1):
            header = _event_header(handle, scope, _history_prefix(scope), sequence)
            if header.previous_chain_sha256 != last_chain:
                raise _unavailable()
            manifest = _read_manifest(handle, storage_prefix, header)
            if manifest.body_utf8_byte_length != header.content_utf8_byte_length:
                raise _unavailable()
            position_value = {
                "kind": "event",
                "sequence": sequence,
                "event_id": header.event_id,
                "request_id": header.request_id,
                "version": _history._SCHEMA_VERSION,
                "content_sha256": header.content_sha256,
                "chain_sha256": header.chain_sha256,
            }
            event_handles.append(_get_or_create_locator(
                handle, storage_prefix, snapshot["snapshot_id"], position_value,
            ))
            last_sequence = sequence
            last_chain = header.chain_sha256

        next_handle = None
        if last_sequence < snapshot["head_sequence"]:
            next_handle = _get_or_create_locator(
                handle,
                storage_prefix,
                snapshot["snapshot_id"],
                {
                    "kind": "page",
                    "after_sequence": last_sequence,
                    "after_chain_sha256": last_chain,
                },
            )
        stable_snapshot_handle = _get_or_create_locator(
            handle,
            storage_prefix,
            snapshot["snapshot_id"],
            {
                "kind": "snapshot",
                "after_sequence": 0,
                "after_chain_sha256": _history._genesis_chain(scope),
            },
        )
        return stable_snapshot_handle, tuple(event_handles), next_handle

    return _run_owner_operation(
        authenticated_user,
        OperationMode.WRITE,
        expected_owner_identity,
        operation,
    )


def read_owner_history_page(
    authenticated_user: dict | None,
    snapshot_handle: str,
    *,
    limit: int,
    expected_owner_identity: RollingHistoryOwnerIdentity,
):
    """Read one immutable page, after legacy rows have bounded sidecar indexes."""
    if type(authenticated_user) is not dict or type(snapshot_handle) is not str:
        raise _unavailable()
    # A READ preflight resolves the position before any indexing writes.
    plan = _run_owner_operation(
        authenticated_user,
        OperationMode.READ,
        expected_owner_identity,
        lambda handle, scope, _identity, owner: _page_plan(
            handle, scope, owner, snapshot_handle, limit,
        ),
    )
    snapshot, position, after, after_chain, sequences = plan
    del position
    if snapshot["head_sequence"] > 0:
        _ensure_event_indexed(
            authenticated_user,
            expected_owner_identity,
            snapshot_handle,
            snapshot["head_sequence"],
        )
    if after > 0:
        _ensure_event_indexed(authenticated_user, expected_owner_identity, snapshot_handle, after)
    for sequence in sequences:
        _ensure_event_indexed(authenticated_user, expected_owner_identity, snapshot_handle, sequence)
    stable_snapshot_handle, event_handles, prepared_next_handle = _prepare_page_locators(
        authenticated_user,
        expected_owner_identity,
        snapshot_handle,
        limit,
    )

    def read_page(handle, scope, _identity, owner):
        storage_prefix = _storage_prefix(scope, owner)
        _locator, current_snapshot, position_value, _current_head = _snapshot_and_position(
            handle,
            scope,
            owner,
            storage_prefix,
            snapshot_handle,
            frozenset({"snapshot", "page"}),
            require_anchor_manifest=True,
        )
        current_after = position_value["after_sequence"]
        current_after_chain = position_value["after_chain_sha256"]
        if current_after != after or current_after_chain != after_chain:
            raise _unavailable()
        count = min(limit, current_snapshot["head_sequence"] - current_after)
        if len(event_handles) != count:
            raise _unavailable()
        events = []
        last_chain = current_after_chain
        for offset, sequence in enumerate(range(current_after + 1, current_after + count + 1)):
            header = _event_header(handle, scope, _history_prefix(scope), sequence)
            if header.previous_chain_sha256 != last_chain:
                raise _unavailable()
            manifest = _read_manifest(handle, storage_prefix, header)
            locator, snapshot, position = _resolve_locator(
                handle,
                storage_prefix,
                event_handles[offset],
                owner,
                frozenset({"event"}),
            )
            if (
                snapshot["snapshot_id"] != current_snapshot["snapshot_id"]
                or position != {
                    "kind": "event",
                    "sequence": sequence,
                    "event_id": header.event_id,
                    "request_id": header.request_id,
                    "version": _history._SCHEMA_VERSION,
                    "content_sha256": header.content_sha256,
                    "chain_sha256": header.chain_sha256,
                }
                or manifest.body_utf8_byte_length != header.content_utf8_byte_length
            ):
                raise _unavailable()
            from app.api.rolling_history_owner_reader import RollingHistoryOwnerLocatorEvent

            events.append(RollingHistoryOwnerLocatorEvent(
                reference=event_handles[offset],
                event_type=header.event_type,
                content_utf8_byte_length=manifest.body_utf8_byte_length,
                has_attachments=header.has_attachments,
                skill_provenance_status=(
                    "unknown" if header.event_type in _SKILL_EVENT_TYPES else "not_applicable"
                ),
            ))
            last_chain = header.chain_sha256

        next_handle = None
        if current_after + count < current_snapshot["head_sequence"]:
            if type(prepared_next_handle) is not str:
                raise _unavailable()
            _next_locator, _next_snapshot, next_position = _resolve_locator(
                handle,
                storage_prefix,
                prepared_next_handle,
                owner,
                frozenset({"page"}),
            )
            if (
                _next_snapshot["snapshot_id"] != current_snapshot["snapshot_id"]
                or next_position != {
                    "kind": "page",
                    "after_sequence": current_after + count,
                    "after_chain_sha256": last_chain,
                }
            ):
                raise _unavailable()
            next_handle = prepared_next_handle
        elif prepared_next_handle is not None:
            raise _unavailable()
        from app.api.rolling_history_owner_reader import RollingHistoryOwnerLocatorPage

        return RollingHistoryOwnerLocatorPage(
            snapshot_handle=stable_snapshot_handle,
            events=tuple(events),
            next_handle=next_handle,
            head_sequence=current_snapshot["head_sequence"],
            head_chain_sha256=current_snapshot["head_chain_sha256"],
        )

    return _run_owner_operation(
        authenticated_user,
        OperationMode.READ,
        expected_owner_identity,
        read_page,
    )


def _read_offset_record(handle, storage_prefix: str, event_id: str, target_offset: int) -> dict[str, Any]:
    offset_prefix = f"{storage_prefix}body:{event_id}:offset:"
    key_bytes = len(offset_prefix.encode("utf-8")) + 33
    rows = _rows(
        handle,
        "SELECT length(CAST(key AS BLOB)) AS key_length, "
        "substr(CAST(key AS BLOB), 1, ?) AS bounded_key, "
        "length(CAST(value AS BLOB)) AS value_length, "
        "substr(CAST(value AS BLOB), 1, ?) AS bounded_value "
        "FROM settings "
        "WHERE key >= ? AND key < ? AND key <= ? ORDER BY key DESC LIMIT 1",
        (
            key_bytes,
            _MAX_OFFSET_RECORD_BYTES + 1,
            offset_prefix,
            f"{storage_prefix}body:{event_id}:offset;",
            _chunk_offset_key(storage_prefix, event_id, target_offset),
        ),
        ("key_length", "bounded_key", "value_length", "bounded_value"),
    )
    if (
        len(rows) != 1
        or type(rows[0][0]) is not int
        or rows[0][0] != key_bytes - 1
        or type(rows[0][1]) is not bytes
        or len(rows[0][1]) != rows[0][0]
        or type(rows[0][2]) is not int
        or rows[0][2] > _MAX_OFFSET_RECORD_BYTES
        or type(rows[0][3]) is not bytes
        or len(rows[0][3]) != rows[0][2]
    ):
        raise _unavailable()
    key_raw = rows[0][1]
    raw_bytes = rows[0][3]
    try:
        key = key_raw.decode("utf-8", errors="strict")
        raw = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _unavailable() from None
    if not key.startswith(offset_prefix):
        raise _unavailable()
    try:
        key_offset = int(key[len(offset_prefix):])
        value = json.loads(raw)
    except Exception:
        raise _unavailable() from None
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "kind", "ordinal", "start_offset", "end_offset", "leaf_sha256"}
        or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
        or value["kind"] != "body_chunk_offset"
        or type(value["ordinal"]) is not int or value["ordinal"] < 0
        or type(value["start_offset"]) is not int or value["start_offset"] != key_offset
        or type(value["end_offset"]) is not int or value["end_offset"] <= value["start_offset"]
        or value["start_offset"] > target_offset or target_offset >= value["end_offset"]
        or type(value["leaf_sha256"]) is not str or _HASH_RE.fullmatch(value["leaf_sha256"]) is None
        or _canonical(value) != raw
    ):
        raise _unavailable()
    return value


def _read_successor_offset_records(
    handle,
    storage_prefix: str,
    event_id: str,
    start_offset: int,
) -> tuple[dict[str, Any], ...]:
    first = _read_offset_record(handle, storage_prefix, event_id, start_offset)
    prefix = f"{storage_prefix}body:{event_id}:offset:"
    key_bytes = len(prefix.encode("utf-8")) + 33
    rows = _rows(
        handle,
        "SELECT length(CAST(key AS BLOB)) AS key_length, "
        "substr(CAST(key AS BLOB), 1, ?) AS bounded_key, "
        "length(CAST(value AS BLOB)) AS value_length, "
        "substr(CAST(value AS BLOB), 1, ?) AS bounded_value "
        "FROM settings WHERE key >= ? AND key < ? "
        "ORDER BY key LIMIT 3",
        (
            key_bytes,
            _MAX_OFFSET_RECORD_BYTES + 1,
            _chunk_offset_key(storage_prefix, event_id, first["start_offset"]),
            f"{storage_prefix}body:{event_id}:offset;",
        ),
        ("key_length", "bounded_key", "value_length", "bounded_value"),
    )
    records = []
    for row in rows:
        if (
            type(row[0]) is not int
            or row[0] != key_bytes - 1
            or type(row[1]) is not bytes
            or len(row[1]) != row[0]
            or type(row[2]) is not int
            or row[2] > _MAX_OFFSET_RECORD_BYTES
            or type(row[3]) is not bytes
            or len(row[3]) != row[2]
        ):
            raise _unavailable()
        try:
            key = row[1].decode("utf-8", errors="strict")
            raw = row[3].decode("utf-8", errors="strict")
            key_offset = int(key[len(prefix):])
            value = json.loads(raw)
        except Exception:
            raise _unavailable() from None
        if (
            type(value) is not dict
            or set(value) != {"schema_version", "kind", "ordinal", "start_offset", "end_offset", "leaf_sha256"}
            or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
            or value["kind"] != "body_chunk_offset"
            or type(value["ordinal"]) is not int or value["ordinal"] < 0
            or type(value["start_offset"]) is not int or value["start_offset"] != key_offset
            or type(value["end_offset"]) is not int or value["end_offset"] <= value["start_offset"]
            or type(value["leaf_sha256"]) is not str or _HASH_RE.fullmatch(value["leaf_sha256"]) is None
            or _canonical(value) != raw
        ):
            raise _unavailable()
        records.append(value)
    if not records or records[0] != first:
        raise _unavailable()
    return tuple(records)


def _verify_merkle_chunk(
    handle,
    storage_prefix: str,
    header: _EventHeader,
    manifest: _BodyManifest,
    record: dict[str, Any],
    body: bytes,
) -> None:
    if (
        record["end_offset"] - record["start_offset"] != len(body)
        or len(body) > _INDEX_CHUNK_BYTES
        or _leaf_digest(header, record["ordinal"], record["start_offset"], body) != record["leaf_sha256"]
    ):
        raise _unavailable()
    node_hash = record["leaf_sha256"]
    node_index = record["ordinal"]
    width = manifest.chunk_count
    for level in range(manifest.merkle_depth):
        sibling_index = node_index ^ 1
        if sibling_index >= width:
            sibling_hash = node_hash
        else:
            sibling = _read_setting(
                handle,
                _node_key(storage_prefix, header.event_id, level, sibling_index),
            )
            if type(sibling) is not str or _HASH_RE.fullmatch(sibling) is None:
                raise _unavailable()
            sibling_hash = sibling
        node_hash = (
            _node_digest(node_hash, sibling_hash)
            if node_index % 2 == 0
            else _node_digest(sibling_hash, node_hash)
        )
        node_index //= 2
        width = (width + 1) // 2
    if node_hash != manifest.merkle_root:
        raise _unavailable()


def _read_verified_body_chunk(
    handle,
    storage_prefix: str,
    header: _EventHeader,
    manifest: _BodyManifest,
    offset: int,
    byte_budget: int,
) -> tuple[str, int, bool]:
    if (
        type(offset) is not int or offset < 0 or offset > manifest.body_utf8_byte_length
        or type(byte_budget) is not int or not _MIN_CHUNK_BYTES <= byte_budget <= _MAX_CHUNK_BYTES
    ):
        raise _unavailable()
    if manifest.body_utf8_byte_length == 0:
        if offset != 0 or manifest.chunk_count != 0:
            raise _unavailable()
        return "", 0, True
    if offset == manifest.body_utf8_byte_length:
        raise _unavailable()
    records = _read_successor_offset_records(
        handle, storage_prefix, header.event_id, offset,
    )
    body_rows: list[bytes] = []
    previous_end: int | None = None
    previous_ordinal: int | None = None
    for record in records:
        if previous_end is not None and (
            record["start_offset"] != previous_end
            or record["ordinal"] != previous_ordinal + 1
        ):
            raise _unavailable()
        value = _read_setting(
            handle,
            _chunk_data_key(storage_prefix, header.event_id, record["ordinal"]),
            maximum_bytes=_INDEX_CHUNK_BYTES,
        )
        if type(value) is not bytes:
            raise _unavailable()
        _verify_merkle_chunk(handle, storage_prefix, header, manifest, record, value)
        try:
            value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise _unavailable() from None
        body_rows.append(value)
        previous_end = record["end_offset"]
        previous_ordinal = record["ordinal"]
    first_record = records[0]
    first_body = body_rows[0]
    relative_offset = offset - first_record["start_offset"]
    if (
        relative_offset < 0 or relative_offset >= len(first_body)
        or (first_body[relative_offset] & 0xC0) == 0x80
    ):
        raise _unavailable()
    available = b"".join(body_rows)
    target_end = min(relative_offset + byte_budget, len(available))
    absolute_end = first_record["start_offset"] + target_end
    if absolute_end < manifest.body_utf8_byte_length and target_end < len(available):
        while target_end > relative_offset and available[target_end] & 0xC0 == 0x80:
            target_end -= 1
        absolute_end = first_record["start_offset"] + target_end
    if target_end <= relative_offset:
        raise _unavailable()
    body_bytes = available[relative_offset:target_end]
    if len(body_bytes) > byte_budget:
        raise _unavailable()
    try:
        body_text = body_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _unavailable() from None
    complete = absolute_end == manifest.body_utf8_byte_length
    return body_text, absolute_end, complete


def read_owner_history_chunk(
    authenticated_user: dict | None,
    event_handle: str,
    *,
    cursor_handle: str | None,
    byte_budget: int,
    expected_owner_identity: RollingHistoryOwnerIdentity,
):
    """Read one UTF-8-aligned body chunk through persistent snapshot locators."""
    if (
        type(authenticated_user) is not dict
        or type(event_handle) is not str
        or (cursor_handle is not None and type(cursor_handle) is not str)
        or type(byte_budget) is not int
        or not _MIN_CHUNK_BYTES <= byte_budget <= _MAX_CHUNK_BYTES
    ):
        raise _unavailable()

    def prepare(handle, scope, _identity, owner):
        storage_prefix = _storage_prefix(scope, owner)
        _locator, snapshot, position = _resolve_locator(
            handle,
            storage_prefix,
            event_handle,
            owner,
            frozenset({"event"}),
        )
        _validate_snapshot_current(
            handle,
            scope,
            owner,
            _history_prefix(scope),
            storage_prefix,
            snapshot,
            require_anchor_manifest=False,
        )
        sequence = position["sequence"]
        if type(sequence) is not int or sequence < 1 or sequence > snapshot["head_sequence"]:
            raise _unavailable()
        _index_event(handle, scope, _history_prefix(scope), storage_prefix, sequence)

    _run_owner_operation(
        authenticated_user,
        OperationMode.WRITE,
        expected_owner_identity,
        prepare,
    )

    def read_chunk(handle, scope, _identity, owner):
        storage_prefix = _storage_prefix(scope, owner)
        _event_locator, snapshot, event_position = _resolve_locator(
            handle,
            storage_prefix,
            event_handle,
            owner,
            frozenset({"event"}),
        )
        _validate_snapshot_current(
            handle,
            scope,
            owner,
            _history_prefix(scope),
            storage_prefix,
            snapshot,
            require_anchor_manifest=True,
        )
        sequence = event_position["sequence"]
        if type(sequence) is not int or sequence < 1 or sequence > snapshot["head_sequence"]:
            raise _unavailable()
        header = _event_header(handle, scope, _history_prefix(scope), sequence)
        if header.previous_chain_sha256 != _previous_chain_for(
            handle, scope, _history_prefix(scope), sequence,
        ):
            raise _unavailable()
        expected_event_position = {
            "kind": "event",
            "sequence": sequence,
            "event_id": header.event_id,
            "request_id": header.request_id,
            "version": _history._SCHEMA_VERSION,
            "content_sha256": header.content_sha256,
            "chain_sha256": header.chain_sha256,
        }
        if event_position != expected_event_position:
            raise _unavailable()
        manifest = _read_manifest(handle, storage_prefix, header)
        if cursor_handle is None:
            offset = 0
        else:
            _cursor_locator, cursor_snapshot, cursor_position = _resolve_locator(
                handle,
                storage_prefix,
                cursor_handle,
                owner,
                frozenset({"chunk"}),
            )
            if (
                cursor_snapshot["snapshot_id"] != snapshot["snapshot_id"]
                or cursor_position["event_sequence"] != sequence
                or type(cursor_position["offset"]) is not int
            ):
                raise _unavailable()
            offset = cursor_position["offset"]
        body_text, end_offset, complete = _read_verified_body_chunk(
            handle,
            storage_prefix,
            header,
            manifest,
            offset,
            byte_budget,
        )
        return snapshot, header, manifest, offset, body_text, end_offset, complete

    snapshot, header, manifest, offset, body_text, end_offset, complete = _run_owner_operation(
        authenticated_user,
        OperationMode.READ,
        expected_owner_identity,
        read_chunk,
    )

    next_handle = None
    if not complete:
        def persist_next(handle, scope, _identity, owner):
            storage_prefix = _storage_prefix(scope, owner)
            _event_locator, current_snapshot, event_position = _resolve_locator(
                handle,
                storage_prefix,
                event_handle,
                owner,
                frozenset({"event"}),
            )
            _validate_snapshot_current(
                handle,
                scope,
                owner,
                _history_prefix(scope),
                storage_prefix,
                current_snapshot,
                require_anchor_manifest=True,
            )
            if (
                current_snapshot["snapshot_id"] != snapshot["snapshot_id"]
                or event_position["sequence"] != header.sequence
                or end_offset <= offset
                or end_offset >= manifest.body_utf8_byte_length
            ):
                raise _unavailable()
            return _get_or_create_locator(
                handle,
                storage_prefix,
                current_snapshot["snapshot_id"],
                {
                    "kind": "chunk",
                    "event_sequence": header.sequence,
                    "offset": end_offset,
                },
            )

        next_handle = _run_owner_operation(
            authenticated_user,
            OperationMode.WRITE,
            expected_owner_identity,
            persist_next,
        )

    from app.api.rolling_history_owner_reader import RollingHistoryOwnerLocatorChunk

    return RollingHistoryOwnerLocatorChunk(
        reference=event_handle,
        event_type=header.event_type,
        body_chunk=body_text,
        start_offset=offset,
        end_offset=end_offset,
        total_utf8_bytes=manifest.body_utf8_byte_length,
        complete=complete,
        next_handle=next_handle,
        skill_provenance_status=(
            "unknown" if header.event_type in _SKILL_EVENT_TYPES else "not_applicable"
        ),
        has_attachments=header.has_attachments,
    )

