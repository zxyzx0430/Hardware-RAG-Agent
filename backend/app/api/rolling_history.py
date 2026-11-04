"""Transactional host-scoped rolling history primitives.

This module stores immutable event payloads and prepared summary-index records
in SQLite's existing Settings table. It does not wire request routes, delete
history, or claim that an external model/checkpoint accepted a prepared index.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from collections.abc import Iterable

from sqlalchemy import func, text

from app.api.common import get_db_ctx
from app.api.context_events import ContextEventScope, _validate_scope
from app.api.session_epoch import SESSION_DELETE_EPOCH_PREFIX
from app.db.models import Settings


# The existing settings endpoint hides this private prefix. This child namespace
# does not overlap the exact per-session epoch key written by session_epoch.py.
_PREFIX = SESSION_DELETE_EPOCH_PREFIX + "rolling-history:v1:"
_SCHEMA_VERSION = 1
_CHUNKED_EVENT_SCHEMA_VERSION = 2
_EVENT_TEXT_CHUNK_MAX_BYTES = 2048
_EVENT_CHUNK_ROW_MAX_BYTES = 16384
_EVENT_MANIFEST_MAX_BYTES = 4096
_EVENT_ID_MAPPING_MAX_BYTES = 2048
_BODY_BACKED_EVENT_SCHEMA_VERSION = 3
_MESSAGE_BODY_CHUNK_SIZE = 8192
# A body-backed chat event may use at most 128 of the existing 8 KiB body chunks.
MAX_BODY_EVENT_CONTENT_UTF8_BYTES = 1_048_576
MAX_BODY_EVENT_METADATA_UTF8_BYTES = 16_384
_EVENT_CHUNK_EMPTY_ROOT = hashlib.sha256(b"rolling-history-empty-event-chunks-v2").hexdigest()
_MAX_PAGE_SIZE = 100
_MAX_PAGE_SCAN = 512
_PREVIEW_CHARS = 256
_EVENT_TYPES = frozenset({
    "user_text",
    "assistant_text",
    "tool_call",
    "tool_result",
    "skill_instruction",
    "skill_resource",
})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)
_REQUEST_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)
_LEGACY_MESSAGE_BLOB_SOURCE_METADATA_KIND = "legacy_message_blob_snapshot_v1"
_SKILL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", re.ASCII)
_SKILL_CALL_ID = re.compile(r"[^\x00-\x1f\x7f]{1,256}", re.ASCII)
_MIME = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}",
    re.ASCII,
)


@dataclass(frozen=True)
class AttachmentReference:
    """Metadata-only attachment locator; attachment bytes are never event text."""

    attachment_id: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True)
class RollingHistoryReference:
    """Stable host locator for one immutable event."""

    event_id: str
    session_id: str
    request_id: str
    sequence: int
    version: int
    content_sha256: str
    chain_sha256: str


@dataclass(frozen=True)
class RollingHistoryEvent:
    """Integrity-checked event; scope and locator data stay host-side."""

    reference: RollingHistoryReference
    event_type: str
    content: str
    attachments: tuple[AttachmentReference, ...]
    skill_provenance: "RollingHistorySkillProvenance | None" = None

    def model_payload(self) -> dict:
        """Return event data only, without session, request, or principal fields."""
        return {
            "event_type": self.event_type,
            "content": self.content,
            "attachments": [asdict(item) for item in self.attachments],
        }


@dataclass(frozen=True)
class RollingHistorySkillRequestBinding:
    """The exact owner, request, delete epoch, and memory epoch for one Skill event."""

    principal_hash: str
    session_id: str
    request_id: str
    session_delete_epoch: str
    memory_clear_epoch: str


@dataclass(frozen=True)
class RollingHistorySkillProvenance:
    """Validated host source mapping bound to an immutable event reference and body hash."""

    request_binding: RollingHistorySkillRequestBinding
    stage: str
    skill_id: str
    package_content_hash: str
    source_path: str
    source_call_id: str
    source_call_id_role: str
    observation_revision: int
    content_sha256: str
    content_utf8_byte_length: int


@dataclass(frozen=True)
class RollingHistoryChunkCursor:
    """Host-neutral continuation locator, bound to one immutable event version."""

    cursor_version: int
    event_id: str
    payload_sha256: str
    text_sha256: str
    chain_sha256: str
    next_offset: int
    session_id: str | None = None
    sequence: int | None = None
    manifest_sha256: str | None = None
    next_chunk_index: int | None = None
    expected_chunk_sha256: str | None = None


class RollingHistoryEventChunkMigrationRequired(ValueError):
    """A legacy inline event needs an explicit bounded migration before chunk reads."""

    code = "legacy_inline_event_requires_migration"


@dataclass(frozen=True)
class RollingHistoryEventChunk:
    """A complete UTF-8-aligned text fragment and its verified event hashes."""

    event_id: str
    event_type: str
    content_chunk: str
    start_offset: int
    end_offset: int
    payload_sha256: str
    text_sha256: str
    chain_sha256: str
    attachments: tuple[AttachmentReference, ...]
    next_cursor: RollingHistoryChunkCursor | None
    content_bytes: bytes | None = None
    body_manifest_sha256: str | None = None
    body_chunk_sha256: str | None = None
    body_previous_chain_sha256: str | None = None
    body_chain_sha256: str | None = None


@dataclass(frozen=True)
class RollingHistoryBodyChunkCursor:
    """Continuation bound to a verified body, owner pin, and locator page."""

    cursor_version: int
    session_id: str
    page_pin_sha256: str
    page_index: int
    locator: "RollingHistoryV2EventLocator"
    branch_id: str
    revision_id: str
    revision_sha256: str
    head_sha256: str
    lineage_root_sha256: str
    lineage_proof_sha256: str
    event_manifest_sha256: str
    body_manifest_sha256: str
    payload_sha256: str
    body_sha256: str
    event_chain_sha256: str
    next_offset: int
    previous_body_chain_sha256: str
    cursor_sha256: str


@dataclass(frozen=True)
class RollingHistoryBodyEventChunk:
    """One verified body-store byte chunk tied to a pinned event locator."""

    page_pin_sha256: str
    page_index: int
    branch_id: str
    revision_id: str
    revision_sha256: str
    head_sha256: str
    lineage_root_sha256: str
    lineage_proof_sha256: str
    locator: "RollingHistoryV2EventLocator"
    event_id: str
    sequence: int
    message_id: str | None
    created_at: str | None
    message_event_sha256: str | None
    source_identity_sha256: str | None
    body_id: str
    start_offset: int
    end_offset: int
    content_bytes: bytes
    body_manifest_sha256: str
    body_chunk_sha256: str
    previous_body_chain_sha256: str
    body_chain_sha256: str
    payload_sha256: str
    text_sha256: str
    event_chain_sha256: str
    next_cursor: RollingHistoryBodyChunkCursor | None


@dataclass(frozen=True)
class RollingHistoryBodyEventInput:
    """Validated source message metadata and its exact body text."""

    message_event: dict
    body_text: str
    created_at: str
    event_type: str
    attachments: tuple[AttachmentReference, ...] = ()

    @property
    def message_id(self) -> str:
        return self.message_event.get("message_id") if type(self.message_event) is dict else ""


@dataclass(frozen=True)
class RollingHistorySourceMessageWriteReceipt:
    """Typed receipt from the caller's same-handle Message CRUD helper."""

    message_id: str
    outcome: str
    payload_sha256: str


@dataclass(frozen=True)
class RollingHistoryPreview:
    """Bounded search/list result; use its event ID for a complete read."""

    event_id: str
    sequence: int
    event_type: str
    content_sha256: str
    preview: str
    has_attachments: bool


@dataclass(frozen=True)
class RollingHistoryPage:
    """A bounded page and an optional sequence cursor for the next page."""

    events: tuple[RollingHistoryPreview, ...]
    next_after_sequence: int | None


@dataclass(frozen=True)
class PreparedRollingHistoryIndex:
    """Durably prepared index; this value is not a publication confirmation."""

    prepared_id: str
    revision: int
    covered_through: int
    retained_from: int
    summary_sha256: str
    source_sha256: str
    prepared_sha256: str


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if type(value) is str else value
    return hashlib.sha256(raw).hexdigest()


def _identity(scope: ContextEventScope) -> dict:
    return {
        "session_id": scope.session_id,
        "principal_hash": scope.principal_hash,
        "session_epoch": scope.session_epoch,
    }


def _scope_digest(scope: ContextEventScope) -> str:
    return _sha256(_canonical(_identity(scope)))


def rolling_history_session_prefix(session_id: str) -> str:
    """Return the stable hashed Settings prefix for same-transaction session cleanup."""
    if (type(session_id) is not str or not 1 <= len(session_id) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in session_id)):
        raise ValueError("Invalid rolling history session ID")
    session_hash = _sha256(session_id)
    return _PREFIX + session_hash + ":"


def _scope_prefix(scope: ContextEventScope) -> str:
    return rolling_history_session_prefix(scope.session_id) + _scope_digest(scope) + ":"


def _format_sequence(sequence: int) -> str:
    return f"{sequence:032d}"


def _event_key(prefix: str, sequence: int) -> str:
    return prefix + "event:" + _format_sequence(sequence)


def _event_chunk_key(prefix: str, event_id: str, chunk_index: int) -> str:
    return prefix + "event-chunk:" + event_id + ":" + _format_sequence(chunk_index)


def _event_manifest_key(prefix: str, event_id: str) -> str:
    return prefix + "event-manifest:" + event_id


def _event_sequence_manifest_key(prefix: str, sequence: int) -> str:
    return prefix + "event-sequence-manifest:" + _format_sequence(sequence)


def _event_id_key(prefix: str, event_id: str) -> str:
    return prefix + "event-id:" + event_id


def _skill_provenance_key(prefix: str, event_id: str) -> str:
    return prefix + "skill-provenance:" + event_id


def _scope_matches(raw, scope: ContextEventScope) -> bool:
    return type(raw) is dict and raw == _identity(scope)


def _valid_request_id(value) -> bool:
    return type(value) is str and _REQUEST_ID.fullmatch(value) is not None


def _require_bound_scope(scope: ContextEventScope) -> None:
    """Require the exact host scope bound by the current chat execution."""
    if type(scope) is not ContextEventScope:
        raise ValueError("Invalid rolling history scope")
    from app.api.compaction_archive import get_compaction_scope
    from app.api.tool_events import get_tool_event_scope

    active = (get_compaction_scope(), get_tool_event_scope())
    bound = [item for item in active if type(item) is ContextEventScope]
    if not bound or any(asdict(item) != asdict(scope) for item in bound):
        raise ValueError("Rolling history requires the bound host scope")


def _genesis_chain(scope: ContextEventScope) -> str:
    return _sha256(_canonical({
        "schema_version": _SCHEMA_VERSION,
        "kind": "history_genesis",
        "scope": _scope_digest(scope),
    }))


def _event_chain_digest(
    scope: ContextEventScope,
    sequence: int,
    previous_chain_sha256: str,
    event_id: str,
    content_sha256: str,
    *,
    schema_version: int = _SCHEMA_VERSION,
) -> str:
    return _sha256(_canonical({
        "schema_version": schema_version,
        "scope": _scope_digest(scope),
        "sequence": sequence,
        "previous_chain_sha256": previous_chain_sha256,
        "event_id": event_id,
        "content_sha256": content_sha256,
    }))


def _split_event_text(content: str) -> tuple[tuple[int, int, str], ...]:
    raw = content.encode("utf-8")
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(raw):
        end = _utf8_chunk_end(raw, start, _EVENT_TEXT_CHUNK_MAX_BYTES)
        chunks.append((start, end, raw[start:end].decode("utf-8")))
        start = end
    return tuple(chunks)


def _new_chunked_event_records(
    scope: ContextEventScope,
    *,
    request_id: str,
    sequence: int,
    event_id: str,
    idempotency_id: str,
    payload: dict,
    encoded_payload: str,
    previous_chain_sha256: str,
) -> tuple[dict, tuple[str, ...]]:
    text_chunks = _split_event_text(payload["content"])
    raw_text = payload["content"].encode("utf-8")
    chunk_records: list[str | None] = [None] * len(text_chunks)
    next_chunk_sha256 = _EVENT_CHUNK_EMPTY_ROOT
    for index in range(len(text_chunks) - 1, -1, -1):
        start, end, text_value = text_chunks[index]
        fields = {
            "schema_version": _CHUNKED_EVENT_SCHEMA_VERSION,
            "kind": "event_text_chunk",
            "scope": _identity(scope),
            "event_id": event_id,
            "sequence": sequence,
            "chunk_index": index,
            "start_offset": start,
            "end_offset": end,
            "content_chunk": text_value,
            "next_chunk_sha256": next_chunk_sha256,
        }
        chunk_sha256 = _sha256(_canonical(fields))
        chunk_records[index] = _canonical({**fields, "chunk_sha256": chunk_sha256})
        next_chunk_sha256 = chunk_sha256

    core = {
        "schema_version": _CHUNKED_EVENT_SCHEMA_VERSION,
        "kind": "event_chunk_manifest",
        "scope": _identity(scope),
        "request_id": request_id,
        "sequence": sequence,
        "event_id": event_id,
        "idempotency_id": idempotency_id,
        "event_type": payload["event_type"],
        "attachments": payload["attachments"],
        "payload_sha256": _sha256(encoded_payload),
        "content_utf8_byte_length": len(raw_text),
        "text_sha256": _sha256(raw_text),
        "chunk_count": len(text_chunks),
        "chunk_root_sha256": next_chunk_sha256,
        "previous_chain_sha256": previous_chain_sha256,
    }
    content_sha256 = _sha256(_canonical(core))
    chain_sha256 = _event_chain_digest(
        scope, sequence, previous_chain_sha256, event_id, content_sha256,
        schema_version=_CHUNKED_EVENT_SCHEMA_VERSION,
    )
    manifest = {
        **core,
        "content_sha256": content_sha256,
        "chain_sha256": chain_sha256,
    }
    if any(record is None for record in chunk_records):
        raise ValueError("Rolling history event chunk manifest is incomplete")
    return manifest, tuple(record for record in chunk_records if record is not None)


def _body_id_for_event(event_id: str) -> str:
    """Derive the 32-hex body-store key from a 64-hex event identity."""
    if type(event_id) is not str or _HASH.fullmatch(event_id) is None:
        raise ValueError("Invalid event id for body storage")
    return event_id[:32]


def _write_message_body_in_owner_transaction(
    handle,
    scope: ContextEventScope,
    *,
    body_id: str,
    content: str,
):
    from app.db.database import (
        append_message_body_chunk_in_transaction,
        begin_message_body_write_in_transaction,
        finalize_message_body_write_in_transaction,
    )

    raw = content.encode("utf-8")
    begin_message_body_write_in_transaction(
        handle, session_id=scope.session_id, body_id=body_id,
    )
    for offset in range(0, len(raw), _MESSAGE_BODY_CHUNK_SIZE):
        append_message_body_chunk_in_transaction(
            handle,
            session_id=scope.session_id,
            body_id=body_id,
            byte_offset=offset,
            chunk_bytes=raw[offset:offset + _MESSAGE_BODY_CHUNK_SIZE],
        )
    manifest = finalize_message_body_write_in_transaction(
        handle, session_id=scope.session_id, body_id=body_id,
    )
    manifest_value = asdict(manifest)
    if (manifest_value != {
            "body_id": body_id,
            "session_id": scope.session_id,
            "byte_length": len(raw),
            "sha256": _sha256(raw),
            "chunk_size": _MESSAGE_BODY_CHUNK_SIZE,
            "chunk_count": (len(raw) + _MESSAGE_BODY_CHUNK_SIZE - 1)
            // _MESSAGE_BODY_CHUNK_SIZE,
            "final_chain_sha256": manifest_value.get("final_chain_sha256"),
            "status": "writer_verified",
            "encoding": "utf-8",
        }
            or type(manifest_value.get("final_chain_sha256")) is not str
            or _HASH.fullmatch(manifest_value["final_chain_sha256"]) is None):
        raise ValueError("Owner-bound message body manifest does not match the event")
    return manifest, _sha256(_canonical(manifest_value))


def _new_body_backed_event_manifest(
    scope: ContextEventScope,
    *,
    request_id: str,
    sequence: int,
    event_id: str,
    idempotency_id: str,
    payload: dict,
    encoded_payload: str,
    previous_chain_sha256: str,
    body_manifest,
    body_manifest_sha256: str,
    source_metadata: dict | None = None,
) -> dict:
    body_raw = payload["content"].encode("utf-8")
    source_metadata = _normalize_body_event_source_metadata(source_metadata)
    core = {
        "schema_version": _BODY_BACKED_EVENT_SCHEMA_VERSION,
        "kind": "event_body_manifest",
        "scope": _identity(scope),
        "request_id": request_id,
        "sequence": sequence,
        "event_id": event_id,
        "idempotency_id": idempotency_id,
        "event_type": payload["event_type"],
        "attachments": payload["attachments"],
        "payload_sha256": _sha256(encoded_payload),
        "content_utf8_byte_length": len(body_raw),
        "text_sha256": _sha256(body_raw),
        "body_id": body_manifest.body_id,
        "body_manifest_sha256": body_manifest_sha256,
        "body_byte_length": body_manifest.byte_length,
        "body_sha256": body_manifest.sha256,
        "body_chunk_size": body_manifest.chunk_size,
        "body_chunk_count": body_manifest.chunk_count,
        "body_chain_sha256": body_manifest.final_chain_sha256,
        "source_metadata": source_metadata,
        "previous_chain_sha256": previous_chain_sha256,
    }
    content_sha256 = _sha256(_canonical(core))
    chain_sha256 = _event_chain_digest(
        scope, sequence, previous_chain_sha256, event_id, content_sha256,
        schema_version=_BODY_BACKED_EVENT_SCHEMA_VERSION,
    )
    return {
        **core,
        "content_sha256": content_sha256,
        "chain_sha256": chain_sha256,
    }


def _validate_body_backed_event_manifest(
    raw: str,
    scope: ContextEventScope,
    expected_sequence: int | None = None,
) -> dict:
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError("Rolling history body manifest is unavailable") from exc
    required = {
        "schema_version", "kind", "scope", "request_id", "sequence",
        "event_id", "idempotency_id", "event_type", "attachments",
        "payload_sha256", "content_utf8_byte_length", "text_sha256",
        "body_id", "body_manifest_sha256", "body_byte_length", "body_sha256",
        "body_chunk_size", "body_chunk_count", "body_chain_sha256",
        "source_metadata", "previous_chain_sha256", "content_sha256", "chain_sha256",
    }
    if (type(value) is not dict or set(value) != required
            or value["schema_version"] != _BODY_BACKED_EVENT_SCHEMA_VERSION
            or value["kind"] != "event_body_manifest"
            or not _scope_matches(value["scope"], scope)
            or not _valid_request_id(value["request_id"])
            or type(value["sequence"]) is not int or value["sequence"] < 1
            or (expected_sequence is not None and value["sequence"] != expected_sequence)
            or type(value["event_id"]) is not str or _HASH.fullmatch(value["event_id"]) is None
            or type(value["idempotency_id"]) is not str or _HASH.fullmatch(value["idempotency_id"]) is None
            or value["body_id"] != _body_id_for_event(value["event_id"])
            or type(value["event_type"]) is not str or value["event_type"] not in _EVENT_TYPES
            or type(value["attachments"]) is not list
            or any(type(item) is not dict for item in value["attachments"])
            or type(value["payload_sha256"]) is not str or _HASH.fullmatch(value["payload_sha256"]) is None
            or type(value["content_utf8_byte_length"]) is not int or value["content_utf8_byte_length"] < 0
            or type(value["text_sha256"]) is not str or _HASH.fullmatch(value["text_sha256"]) is None
            or type(value["body_manifest_sha256"]) is not str or _HASH.fullmatch(value["body_manifest_sha256"]) is None
            or type(value["body_byte_length"]) is not int or value["body_byte_length"] < 0
            or value["body_byte_length"] != value["content_utf8_byte_length"]
            or type(value["body_sha256"]) is not str or _HASH.fullmatch(value["body_sha256"]) is None
            or value["body_sha256"] != value["text_sha256"]
            or type(value["body_chunk_size"]) is not int or value["body_chunk_size"] != _MESSAGE_BODY_CHUNK_SIZE
            or type(value["body_chunk_count"]) is not int
            or value["body_chunk_count"] != (
                value["body_byte_length"] + _MESSAGE_BODY_CHUNK_SIZE - 1
            ) // _MESSAGE_BODY_CHUNK_SIZE
            or type(value["body_chain_sha256"]) is not str or _HASH.fullmatch(value["body_chain_sha256"]) is None
            or type(value["previous_chain_sha256"]) is not str or _HASH.fullmatch(value["previous_chain_sha256"]) is None
            or type(value["content_sha256"]) is not str or _HASH.fullmatch(value["content_sha256"]) is None
            or type(value["chain_sha256"]) is not str or _HASH.fullmatch(value["chain_sha256"]) is None
            or _canonical(value) != raw):
        raise ValueError("Invalid rolling history body manifest")
    if value["source_metadata"] != _normalize_body_event_source_metadata(
        value["source_metadata"],
    ):
        raise ValueError("Invalid rolling history body source metadata")
    core = {key: item for key, item in value.items()
            if key not in ("content_sha256", "chain_sha256")}
    if (_sha256(_canonical(core)) != value["content_sha256"]
            or _event_chain_digest(
                scope, value["sequence"], value["previous_chain_sha256"],
                value["event_id"], value["content_sha256"],
                schema_version=_BODY_BACKED_EVENT_SCHEMA_VERSION,
            ) != value["chain_sha256"]):
        raise ValueError("Rolling history body manifest commitment mismatch")
    for item in value["attachments"]:
        _parse_attachment(item)
    return value


def _empty_body_event_source_metadata() -> dict:
    return {
        "message_id": None,
        "created_at": None,
        "message_event_sha256": None,
        "source_identity_sha256": None,
        "append_fence": None,
        "batch_count": None,
        "batch_index": None,
    }


def _normalize_body_event_source_metadata(raw: object | None) -> dict:
    if raw is None:
        return _empty_body_event_source_metadata()
    append_fields = {
        "message_id", "created_at", "message_event_sha256", "source_identity_sha256",
        "append_fence", "batch_count", "batch_index",
    }
    legacy_fields = {
        "source_kind", "migration_id", "snapshot_id", "source_root_sha256",
        "source_sequence", "row_sha256", "source_chain_sha256", "message_id",
        "created_at", "message_event_sha256", "source_identity_sha256",
    }
    if type(raw) is dict and set(raw) == legacy_fields:
        created_at = raw["created_at"]
        if (raw["source_kind"] != _LEGACY_MESSAGE_BLOB_SOURCE_METADATA_KIND
                or type(raw["migration_id"]) is not str
                or _LEGACY_SOURCE_GENERATION_ID.fullmatch(raw["migration_id"]) is None
                or type(raw["snapshot_id"]) is not str
                or _LEGACY_SOURCE_GENERATION_ID.fullmatch(raw["snapshot_id"]) is None
                or type(raw["source_root_sha256"]) is not str
                or _HASH.fullmatch(raw["source_root_sha256"]) is None
                or type(raw["source_sequence"]) is not int
                or raw["source_sequence"] < 0
                or type(raw["row_sha256"]) is not str
                or _HASH.fullmatch(raw["row_sha256"]) is None
                or type(raw["source_chain_sha256"]) is not str
                or _HASH.fullmatch(raw["source_chain_sha256"]) is None
                or type(raw["message_id"]) is not str
                or _ID.fullmatch(raw["message_id"]) is None
                or (created_at is not None and type(created_at) is not str)
                or (type(created_at) is str
                    and (len(created_at.encode("utf-8")) > 128
                         or any(ord(char) < 32 or ord(char) == 127
                                for char in created_at)))
                or type(raw["message_event_sha256"]) is not str
                or _HASH.fullmatch(raw["message_event_sha256"]) is None
                or type(raw["source_identity_sha256"]) is not str
                or _HASH.fullmatch(raw["source_identity_sha256"]) is None):
            raise ValueError("Invalid rolling history legacy snapshot source metadata")
        return {
            "source_kind": _LEGACY_MESSAGE_BLOB_SOURCE_METADATA_KIND,
            "migration_id": raw["migration_id"],
            "snapshot_id": raw["snapshot_id"],
            "source_root_sha256": raw["source_root_sha256"],
            "source_sequence": raw["source_sequence"],
            "row_sha256": raw["row_sha256"],
            "source_chain_sha256": raw["source_chain_sha256"],
            "message_id": raw["message_id"],
            "created_at": created_at,
            "message_event_sha256": raw["message_event_sha256"],
            "source_identity_sha256": raw["source_identity_sha256"],
        }

    required = append_fields
    if type(raw) is not dict or set(raw) != required:
        raise ValueError("Invalid rolling history body source metadata")
    fence = raw["append_fence"]
    before = fence.get("before") if type(fence) is dict else None
    after = fence.get("after") if type(fence) is dict else None
    fence_fields = {
        "message_source_generation", "message_count", "non_append_generation",
    }
    if (type(raw["message_id"]) is not str or _ID.fullmatch(raw["message_id"]) is None
            or type(raw["created_at"]) is not str
            or not 1 <= len(raw["created_at"].encode("utf-8")) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in raw["created_at"])
            or type(raw["message_event_sha256"]) is not str
            or _HASH.fullmatch(raw["message_event_sha256"]) is None
            or type(raw["source_identity_sha256"]) is not str
            or _HASH.fullmatch(raw["source_identity_sha256"]) is None
            or type(fence) is not dict or set(fence) != {"before", "after"}
            or type(before) is not dict or set(before) != fence_fields
            or type(after) is not dict or set(after) != fence_fields
            or any(type(value) is not int or value < 0
                   for value in (*before.values(), *after.values()))
            or type(raw["batch_count"]) is not int or raw["batch_count"] < 1
            or type(raw["batch_index"]) is not int
            or not 1 <= raw["batch_index"] <= raw["batch_count"]):
        raise ValueError("Invalid rolling history body source metadata")
    generation_delta = (
        after["message_source_generation"] - before["message_source_generation"]
    )
    count_delta = after["message_count"] - before["message_count"]
    if (after["non_append_generation"] != before["non_append_generation"]
            or generation_delta != raw["batch_count"]
            or count_delta != raw["batch_count"]):
        raise ValueError("Rolling history body source fence is not append-only")
    return {
        "message_id": raw["message_id"],
        "created_at": raw["created_at"],
        "message_event_sha256": raw["message_event_sha256"],
        "source_identity_sha256": raw["source_identity_sha256"],
        "append_fence": {
            "before": {key: before[key] for key in sorted(fence_fields)},
            "after": {key: after[key] for key in sorted(fence_fields)},
        },
        "batch_count": raw["batch_count"],
        "batch_index": raw["batch_index"],
    }


def _append_rolling_history_legacy_message_snapshot_event_in_transaction(
    handle,
    scope: ContextEventScope,
    *,
    row_metadata: dict,
    body_text: str,
    expected_incarnation_id: str,
    source_root_sha256: str,
    source_chain_sha256: str,
    idempotency_id: str,
    event_id: str,
) -> RollingHistoryReference:
    """Append one sealed legacy Message as a migration-owned body event.

    This path writes only rolling-history body/event records. It never inserts
    or updates a Message row, and it does not advance the live message fence.
    The source Message metadata is canonicalized and size-checked before the
    first event or body write.
    """
    from app.api.rolling_history_owner_scope import (
        OperationMode,
        get_rolling_history_owner_identity,
    )

    _require_owner_history_handle(handle, scope, OperationMode.WRITE)
    identity = get_rolling_history_owner_identity()
    required_row_fields = {
        "migration_id", "snapshot_id", "sequence", "message_id", "role",
        "created_at", "metadata_json", "body_id", "body_byte_length",
        "body_sha256", "row_hash", "status",
    }
    if (type(row_metadata) is not dict or set(row_metadata) != required_row_fields
            or type(row_metadata["migration_id"]) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(row_metadata["migration_id"]) is None
            or type(row_metadata["snapshot_id"]) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(row_metadata["snapshot_id"]) is None
            or type(row_metadata["sequence"]) is not int
            or row_metadata["sequence"] < 0
            or type(row_metadata["message_id"]) is not str
            or _ID.fullmatch(row_metadata["message_id"]) is None
            or type(row_metadata["role"]) is not str
            or row_metadata["role"] not in {"user", "assistant"}
            or (row_metadata["created_at"] is not None
                and type(row_metadata["created_at"]) is not str)
            or type(row_metadata["metadata_json"]) is not str
            or type(row_metadata["body_id"]) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(row_metadata["body_id"]) is None
            or type(row_metadata["body_byte_length"]) is not int
            or row_metadata["body_byte_length"] < 0
            or type(row_metadata["body_sha256"]) is not str
            or _HASH.fullmatch(row_metadata["body_sha256"]) is None
            or type(row_metadata["row_hash"]) is not str
            or _HASH.fullmatch(row_metadata["row_hash"]) is None
            or row_metadata["status"] != "sealed"
            or type(expected_incarnation_id) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(expected_incarnation_id) is None
            or expected_incarnation_id != identity.incarnation_id
            or type(source_root_sha256) is not str
            or _HASH.fullmatch(source_root_sha256) is None
            or type(source_chain_sha256) is not str
            or _HASH.fullmatch(source_chain_sha256) is None
            or type(idempotency_id) is not str or _HASH.fullmatch(idempotency_id) is None
            or type(event_id) is not str or _HASH.fullmatch(event_id) is None):
        raise ValueError("Invalid sealed legacy Message migration input")

    try:
        metadata = json.loads(
            row_metadata["metadata_json"],
            object_pairs_hook=_legacy_source_unique_object_pairs,
            parse_constant=_legacy_source_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Legacy Message snapshot metadata is unavailable") from exc
    metadata_fields = {
        "activity_json", "created_at", "legacy_message_id", "role", "session_id",
        "sources_json", "tool_calls_json",
    }
    if (type(metadata) is not dict or set(metadata) != metadata_fields
            or metadata["legacy_message_id"] != row_metadata["message_id"]
            or metadata["session_id"] != scope.session_id
            or metadata["role"] != row_metadata["role"]
            or metadata["created_at"] != row_metadata["created_at"]
            or _canonical(metadata) != row_metadata["metadata_json"]):
        raise ValueError("Legacy Message snapshot metadata binding mismatch")

    def decode_json_column(value: object) -> object:
        if value is None:
            return None
        if type(value) is not str:
            raise ValueError("Legacy Message JSON metadata has an invalid type")
        try:
            return json.loads(
                value,
                object_pairs_hook=_legacy_source_unique_object_pairs,
                parse_constant=_legacy_source_reject_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Legacy Message JSON metadata is unavailable") from exc

    message_event = {
        "message_id": row_metadata["message_id"],
        "expected_incarnation_id": expected_incarnation_id,
        "role": row_metadata["role"],
        "content": body_text,
        "sources": decode_json_column(metadata["sources_json"]),
        "tool_calls": decode_json_column(metadata["tool_calls_json"]),
        "activity": decode_json_column(metadata["activity_json"]),
    }
    event_type = "user_text" if row_metadata["role"] == "user" else "assistant_text"
    metadata_payload = {
        "event_type": event_type,
        "attachments": [],
        "created_at": row_metadata["created_at"],
        "message_event": {
            key: value for key, value in message_event.items() if key != "content"
        },
    }
    encoded_metadata = _canonical(metadata_payload).encode("utf-8")
    if len(encoded_metadata) > MAX_BODY_EVENT_METADATA_UTF8_BYTES:
        raise ValueError("Rolling history body event metadata exceeds byte budget")
    validate_rolling_history_body_event_content(body_text)
    body_bytes = body_text.encode("utf-8", errors="strict")
    if (len(body_bytes) != row_metadata["body_byte_length"]
            or _sha256(body_bytes) != row_metadata["body_sha256"]):
        raise ValueError("Legacy Message migration body does not match its sealed row")

    message_event_sha256 = _sha256(_canonical(message_event))
    source_identity_sha256 = _sha256(_canonical({
        "kind": "rolling_history_legacy_message_snapshot_source",
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "session_id": scope.session_id,
        "message_id": row_metadata["message_id"],
    }))
    source_metadata = _normalize_body_event_source_metadata({
        "source_kind": _LEGACY_MESSAGE_BLOB_SOURCE_METADATA_KIND,
        "migration_id": row_metadata["migration_id"],
        "snapshot_id": row_metadata["snapshot_id"],
        "source_root_sha256": source_root_sha256,
        "source_sequence": row_metadata["sequence"],
        "row_sha256": row_metadata["row_hash"],
        "source_chain_sha256": source_chain_sha256,
        "message_id": row_metadata["message_id"],
        "created_at": row_metadata["created_at"],
        "message_event_sha256": message_event_sha256,
        "source_identity_sha256": source_identity_sha256,
    })
    payload, encoded_payload = _encode_event({
        "event_type": event_type,
        "content": body_text,
        "attachments": [],
    })
    prefix = _scope_prefix(scope)
    last_sequence, previous_chain = _owner_history_head(handle, scope, prefix)
    sequence = last_sequence + 1
    if (_owner_history_read_setting(handle, _event_key(prefix, sequence)) is not None
            or _owner_history_read_setting(handle, _event_id_key(prefix, event_id)) is not None
            or _owner_history_read_setting(handle, _event_manifest_key(prefix, event_id)) is not None
            or _owner_history_read_setting(
                handle, _event_sequence_manifest_key(prefix, sequence),
            ) is not None):
        raise ValueError("Legacy Message migration event locator collision")

    body_manifest, body_manifest_sha256 = _write_message_body_in_owner_transaction(
        handle, scope, body_id=_body_id_for_event(event_id), content=payload["content"],
    )
    event_value = _new_body_backed_event_manifest(
        scope,
        request_id=scope.request_id,
        sequence=sequence,
        event_id=event_id,
        idempotency_id=idempotency_id,
        payload=payload,
        encoded_payload=encoded_payload,
        previous_chain_sha256=previous_chain,
        body_manifest=body_manifest,
        body_manifest_sha256=body_manifest_sha256,
        source_metadata=source_metadata,
    )
    encoded_manifest = _canonical(event_value)
    if len(encoded_manifest.encode("utf-8")) > _EVENT_MANIFEST_MAX_BYTES:
        raise ValueError("Rolling history body manifest exceeds byte budget")
    content_sha256 = event_value["content_sha256"]
    chain_sha256 = event_value["chain_sha256"]
    reference = RollingHistoryReference(
        event_id, scope.session_id, scope.request_id, sequence,
        _BODY_BACKED_EVENT_SCHEMA_VERSION, content_sha256, chain_sha256,
    )
    id_value = {
        "schema_version": _BODY_BACKED_EVENT_SCHEMA_VERSION,
        "kind": "event_id",
        "scope": _identity(scope),
        "event_id": event_id,
        "request_id": scope.request_id,
        "sequence": sequence,
        "content_sha256": content_sha256,
        "chain_sha256": chain_sha256,
    }
    sequence_value = {
        "schema_version": _BODY_BACKED_EVENT_SCHEMA_VERSION,
        "kind": "event_sequence_manifest",
        "scope": _identity(scope),
        "sequence": sequence,
        "event_id": event_id,
        "manifest_sha256": _sha256(encoded_manifest),
        "content_sha256": content_sha256,
        "chain_sha256": chain_sha256,
    }
    if (len(_canonical(id_value).encode("utf-8")) > _EVENT_ID_MAPPING_MAX_BYTES
            or len(_canonical(sequence_value).encode("utf-8")) > _EVENT_ID_MAPPING_MAX_BYTES):
        raise ValueError("Rolling history body locator exceeds byte budget")
    _owner_history_insert_setting(handle, _event_key(prefix, sequence), encoded_manifest)
    _owner_history_insert_setting(handle, _event_manifest_key(prefix, event_id), encoded_manifest)
    _owner_history_insert_setting(
        handle, _event_sequence_manifest_key(prefix, sequence), _canonical(sequence_value),
    )
    _owner_history_insert_setting(handle, _event_id_key(prefix, event_id), _canonical(id_value))
    _owner_history_set_setting(
        handle,
        prefix + "meta:head",
        _canonical({
            "schema_version": _SCHEMA_VERSION,
            "kind": "history_head",
            "scope": _identity(scope),
            "last_sequence": sequence,
            "chain_sha256": chain_sha256,
        }),
    )
    return reference


def _body_event_batch_digest(
    encoded_payloads: tuple[str, ...],
    source_metadata: tuple[dict, ...],
) -> str:
    if len(encoded_payloads) != len(source_metadata):
        raise ValueError("Rolling history body event metadata count mismatch")
    return _batch_digest(tuple(
        _canonical({
            "payload": encoded_payload,
            "source_metadata": _normalize_body_event_source_metadata(metadata),
        })
        for encoded_payload, metadata in zip(
            encoded_payloads, source_metadata, strict=True,
        )
    ))


def validate_rolling_history_body_event_content(body_text: object) -> None:
    """Validate the full UTF-8 byte capacity for a body-backed chat event.

    Chat CRUD route preflight may call this public helper so request rejection
    uses the same limit as the immutable rolling-history writer.
    """
    if type(body_text) is not str:
        raise ValueError("Invalid rolling history body event content")
    if len(body_text) > MAX_BODY_EVENT_CONTENT_UTF8_BYTES:
        raise ValueError("Rolling history body event content exceeds byte budget")
    try:
        body_byte_length = len(body_text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("Rolling history body event content is not valid UTF-8") from exc
    if body_byte_length > MAX_BODY_EVENT_CONTENT_UTF8_BYTES:
        raise ValueError("Rolling history body event content exceeds byte budget")


def _encode_body_event_input(
    event: RollingHistoryBodyEventInput,
) -> tuple[dict, str]:
    if type(event) is not RollingHistoryBodyEventInput:
        raise ValueError("Invalid rolling history body event input")
    validate_rolling_history_body_event_content(event.body_text)
    payload, encoded_payload = _encode_event({
        "event_type": event.event_type,
        "content": event.body_text,
        "attachments": [asdict(item) for item in event.attachments],
    })
    message_event = event.message_event
    message_fields = {"message_id", "expected_incarnation_id", "role", "content"}
    optional_fields = {"sources", "tool_calls", "activity"}
    if (type(message_event) is not dict
            or not message_fields.issubset(message_event)
            or set(message_event) - message_fields - optional_fields
            or type(message_event.get("message_id")) is not str
            or _ID.fullmatch(message_event["message_id"]) is None
            or type(message_event.get("expected_incarnation_id")) is not str
            or not 1 <= len(message_event["expected_incarnation_id"]) <= 128
            or any(ord(char) < 32 or ord(char) == 127
                   for char in message_event["expected_incarnation_id"])
            or type(message_event.get("role")) is not str
            or not 1 <= len(message_event["role"]) <= 32
            or any(ord(char) < 32 or ord(char) == 127 for char in message_event["role"])
            or type(message_event.get("content")) is not str
            or message_event["content"] != event.body_text
            or type(event.created_at) is not str
            or not 1 <= len(event.created_at.encode("utf-8")) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in event.created_at)
    ):
        raise ValueError("Invalid rolling history body event source fields")
    metadata_payload = {
        "event_type": payload["event_type"],
        "attachments": payload["attachments"],
        "created_at": event.created_at,
        "message_event": {
            key: value for key, value in message_event.items() if key != "content"
        },
    }
    if (len(_canonical(metadata_payload).encode("utf-8"))
            > MAX_BODY_EVENT_METADATA_UTF8_BYTES):
        raise ValueError("Rolling history body event metadata exceeds byte budget")
    return payload, encoded_payload


def _parse_attachment(raw) -> AttachmentReference:
    if (type(raw) is not dict or set(raw) != {"attachment_id", "sha256", "size_bytes", "media_type"}
            or type(raw["attachment_id"]) is not str or _ID.fullmatch(raw["attachment_id"]) is None
            or type(raw["sha256"]) is not str or _HASH.fullmatch(raw["sha256"]) is None
            or type(raw["size_bytes"]) is not int or raw["size_bytes"] < 0
            or type(raw["media_type"]) is not str or _MIME.fullmatch(raw["media_type"]) is None):
        raise ValueError("Invalid attachment reference")
    return AttachmentReference(raw["attachment_id"], raw["sha256"], raw["size_bytes"], raw["media_type"])


def _encode_event(raw) -> tuple[dict, str]:
    """Accept only the host-approved model-visible projection, not transport config."""
    if (type(raw) is not dict or set(raw) not in ({"event_type", "content"}, {"event_type", "content", "attachments"})
            or type(raw.get("event_type")) is not str or raw["event_type"] not in _EVENT_TYPES
            or type(raw.get("content")) is not str):
        raise ValueError("Invalid rolling history event")
    raw_attachments = raw.get("attachments", [])
    if type(raw_attachments) is not list:
        raise ValueError("Attachments must be references")
    attachments = [_parse_attachment(item) for item in raw_attachments]
    payload = {
        "event_type": raw["event_type"],
        "content": raw["content"],
        "attachments": [asdict(item) for item in attachments],
    }
    return payload, _canonical(payload)


def _rolling_history_skill_provenance_value(
    provenance: RollingHistorySkillProvenance,
) -> dict:
    binding = provenance.request_binding
    return {
        "schema_version": 1,
        "kind": "skill_source_mapping",
        "request_binding": {
            "principal_hash": binding.principal_hash,
            "session_id": binding.session_id,
            "request_id": binding.request_id,
            "session_delete_epoch": binding.session_delete_epoch,
            "memory_clear_epoch": binding.memory_clear_epoch,
        },
        "stage": provenance.stage,
        "skill_id": provenance.skill_id,
        "package_content_hash": provenance.package_content_hash,
        "source_path": provenance.source_path,
        "source_call_id": provenance.source_call_id,
        "source_call_id_role": provenance.source_call_id_role,
        "observation_revision": provenance.observation_revision,
        "content_sha256": provenance.content_sha256,
        "content_utf8_byte_length": provenance.content_utf8_byte_length,
    }


def _parse_rolling_history_skill_provenance(
    raw,
    scope: ContextEventScope,
    event_type: str,
    content: str,
) -> RollingHistorySkillProvenance:
    keys = {
        "schema_version", "kind", "request_binding", "stage", "skill_id",
        "package_content_hash", "source_path", "source_call_id",
        "source_call_id_role", "observation_revision", "content_sha256",
        "content_utf8_byte_length",
    }
    binding_keys = {
        "principal_hash", "session_id", "request_id", "session_delete_epoch",
        "memory_clear_epoch",
    }
    if (type(raw) is not dict or set(raw) != keys
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or raw["kind"] != "skill_source_mapping"
            or type(raw["request_binding"]) is not dict
            or set(raw["request_binding"]) != binding_keys):
        raise ValueError("Invalid rolling Skill provenance mapping")
    binding = raw["request_binding"]
    if (type(binding["principal_hash"]) is not str
            or _HASH.fullmatch(binding["principal_hash"]) is None
            or type(binding["session_id"]) is not str
            or type(binding["request_id"]) is not str
            or not _valid_request_id(binding["request_id"])
            or type(binding["session_delete_epoch"]) is not str
            or (binding["session_delete_epoch"] != ""
                and _REQUEST_ID.fullmatch(binding["session_delete_epoch"]) is None)
            or type(binding["memory_clear_epoch"]) is not str
            or (binding["memory_clear_epoch"] != ""
                and _REQUEST_ID.fullmatch(binding["memory_clear_epoch"]) is None)
            or (binding["session_id"], binding["request_id"], binding["principal_hash"],
                binding["session_delete_epoch"])
            != (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)):
        raise ValueError("Rolling Skill provenance owner binding mismatch")
    if (type(raw["stage"]) is not str or raw["stage"] not in {"instructions", "resource"}
            or (raw["stage"] == "instructions") != (event_type == "skill_instruction")
            or (raw["stage"] == "resource") != (event_type == "skill_resource")
            or type(raw["skill_id"]) is not str or _SKILL_ID.fullmatch(raw["skill_id"]) is None
            or type(raw["package_content_hash"]) is not str
            or _HASH.fullmatch(raw["package_content_hash"]) is None
            or type(raw["source_path"]) is not str
            or not 1 <= len(raw["source_path"]) <= 512
            or any(ord(char) < 32 or ord(char) == 127 for char in raw["source_path"])
            or type(raw["source_call_id"]) is not str
            or _SKILL_CALL_ID.fullmatch(raw["source_call_id"]) is None
            or raw["source_call_id_role"] != "correlation_only"
            or type(raw["observation_revision"]) is not int
            or raw["observation_revision"] < 0
            or type(raw["content_sha256"]) is not str
            or _HASH.fullmatch(raw["content_sha256"]) is None
            or type(raw["content_utf8_byte_length"]) is not int
            or raw["content_utf8_byte_length"] < 0
            or type(content) is not str):
        raise ValueError("Invalid rolling Skill provenance fields")
    try:
        content_bytes = content.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("Rolling Skill event body is not strict UTF-8") from exc
    if (raw["content_sha256"] != _sha256(content_bytes)
            or raw["content_utf8_byte_length"] != len(content_bytes)):
        raise ValueError("Rolling Skill provenance body commitment mismatch")
    return RollingHistorySkillProvenance(
        RollingHistorySkillRequestBinding(
            binding["principal_hash"], binding["session_id"], binding["request_id"],
            binding["session_delete_epoch"], binding["memory_clear_epoch"],
        ),
        raw["stage"], raw["skill_id"], raw["package_content_hash"], raw["source_path"],
        raw["source_call_id"], raw["source_call_id_role"], raw["observation_revision"],
        raw["content_sha256"], raw["content_utf8_byte_length"],
    )


def _encode_prepared_rolling_skill_provenance(
    provenance,
    scope: ContextEventScope,
    event_type: str,
    content: str,
) -> tuple[dict, str, str]:
    from src.agent.compact.rolling_skill_event_bridge import RollingSkillEventProvenance

    if type(provenance) is not RollingSkillEventProvenance:
        raise ValueError("Prepared Skill provenance is unavailable")
    binding = provenance.request_binding
    raw = {
        "schema_version": 1,
        "kind": "skill_source_mapping",
        "request_binding": {
            "principal_hash": binding.principal_hash,
            "session_id": binding.session_id,
            "request_id": binding.request_id,
            "session_delete_epoch": binding.session_delete_epoch,
            "memory_clear_epoch": binding.memory_clear_epoch,
        },
        "stage": provenance.stage,
        "skill_id": provenance.skill_id,
        "package_content_hash": provenance.package_content_hash,
        "source_path": provenance.source_path,
        "source_call_id": provenance.source_call_id,
        "source_call_id_role": provenance.source_call_id_role,
        "observation_revision": provenance.observation_revision,
        "content_sha256": provenance.content_sha256,
        "content_utf8_byte_length": provenance.content_utf8_byte_length,
    }
    parsed = _parse_rolling_history_skill_provenance(raw, scope, event_type, content)
    encoded = _canonical(_rolling_history_skill_provenance_value(parsed))
    return raw, encoded, _sha256(encoded)


def _rolling_history_skill_provenance_mapping_base(
    scope: ContextEventScope,
    reference: RollingHistoryReference,
    provenance: RollingHistorySkillProvenance,
) -> dict:
    return {
        "schema_version": 1,
        "kind": "skill_provenance",
        "scope": _identity(scope),
        "event_reference": {
            "event_id": reference.event_id,
            "request_id": reference.request_id,
            "sequence": reference.sequence,
            "version": reference.version,
            "content_sha256": reference.content_sha256,
            "chain_sha256": reference.chain_sha256,
        },
        "provenance": _rolling_history_skill_provenance_value(provenance),
    }


def _make_rolling_history_skill_provenance_mapping(
    scope: ContextEventScope,
    reference: RollingHistoryReference,
    provenance: RollingHistorySkillProvenance,
) -> dict:
    base = _rolling_history_skill_provenance_mapping_base(scope, reference, provenance)
    return {**base, "mapping_sha256": _sha256(_canonical(base))}


def _parse_rolling_history_skill_provenance_mapping(
    raw: str,
    scope: ContextEventScope,
    reference: RollingHistoryReference,
    event_type: str,
    content: str,
) -> RollingHistorySkillProvenance:
    if type(raw) is not str:
        raise ValueError("Rolling Skill provenance mapping is unavailable")
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError("Rolling Skill provenance mapping is unavailable") from exc
    keys = {
        "schema_version", "kind", "scope", "event_reference", "provenance",
        "mapping_sha256",
    }
    expected_reference = {
        "event_id": reference.event_id,
        "request_id": reference.request_id,
        "sequence": reference.sequence,
        "version": reference.version,
        "content_sha256": reference.content_sha256,
        "chain_sha256": reference.chain_sha256,
    }
    event_reference = value.get("event_reference") if type(value) is dict else None
    if (type(value) is not dict or set(value) != keys
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "skill_provenance"
            or not _scope_matches(value["scope"], scope)
            or type(event_reference) is not dict
            or set(event_reference) != set(expected_reference)
            or type(event_reference["event_id"]) is not str
            or event_reference["event_id"] != reference.event_id
            or type(event_reference["request_id"]) is not str
            or event_reference["request_id"] != reference.request_id
            or type(event_reference["sequence"]) is not int
            or event_reference["sequence"] != reference.sequence
            or type(event_reference["version"]) is not int
            or event_reference["version"] != reference.version
            or type(event_reference["content_sha256"]) is not str
            or event_reference["content_sha256"] != reference.content_sha256
            or type(event_reference["chain_sha256"]) is not str
            or event_reference["chain_sha256"] != reference.chain_sha256
            or type(value["mapping_sha256"]) is not str
            or _HASH.fullmatch(value["mapping_sha256"]) is None
            or _canonical(value) != raw):
        raise ValueError("Invalid rolling Skill provenance mapping envelope")
    base = {key: value[key] for key in value if key != "mapping_sha256"}
    if _sha256(_canonical(base)) != value["mapping_sha256"]:
        raise ValueError("Rolling Skill provenance mapping hash mismatch")
    provenance = _parse_rolling_history_skill_provenance(
        value["provenance"], scope, event_type, content,
    )
    if _canonical(_rolling_history_skill_provenance_value(provenance)) != _canonical(value["provenance"]):
        raise ValueError("Noncanonical rolling Skill provenance mapping")
    return provenance


def _batch_digest(encoded_events: Iterable[str]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    for encoded in encoded_events:
        if not first:
            digest.update(b",")
        digest.update(encoded.encode("utf-8"))
        first = False
    digest.update(b"]")
    return digest.hexdigest()


def _history_head(db, prefix: str, scope: ContextEventScope) -> tuple[int, str]:
    row = db.get(Settings, prefix + "meta:head")
    if row is None:
        head_sequence = 0
        head_chain = _genesis_chain(scope)
    else:
        try:
            value = json.loads(row.value)
        except Exception as exc:
            raise ValueError("Rolling history head is unavailable") from exc
        if (type(value) is not dict
                or set(value) != {"schema_version", "kind", "scope", "last_sequence", "chain_sha256"}
                or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
                or value["kind"] != "history_head" or not _scope_matches(value["scope"], scope)
                or type(value["last_sequence"]) is not int or value["last_sequence"] < 0
                or type(value["chain_sha256"]) is not str or _HASH.fullmatch(value["chain_sha256"]) is None
                or _canonical(value) != row.value):
            raise ValueError("Invalid rolling history head")
        head_sequence = value["last_sequence"]
        head_chain = value["chain_sha256"]

    latest = db.query(Settings.key).filter(
        Settings.key.startswith(prefix + "event:", autoescape=True),
    ).order_by(Settings.key.desc()).first()
    if latest is None:
        observed_sequence = 0
        observed_chain = _genesis_chain(scope)
    else:
        suffix = latest[0][len(prefix + "event:"):]
        if not suffix.isdigit():
            raise ValueError("Invalid rolling history sequence key")
        observed_sequence = int(suffix)
        event = _load_event_at_sequence(db, scope, prefix, observed_sequence)
        if event is None:
            raise ValueError("Rolling history head event is unavailable")
        observed_chain = event.reference.chain_sha256
    if observed_sequence != head_sequence or observed_chain != head_chain:
        raise ValueError("Rolling history head does not match stored events")
    return head_sequence, head_chain


def _set_row(db, key: str, value: str) -> None:
    row = db.get(Settings, key)
    if row is None:
        db.add(Settings(key=key, value=value))
    else:
        row.value = value


def _chunked_manifest_core(value: dict) -> dict:
    return {key: item for key, item in value.items()
            if key not in ("content_sha256", "chain_sha256")}


def _validate_chunked_event_manifest(
    raw: str,
    scope: ContextEventScope,
    expected_sequence: int | None = None,
) -> dict:
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError("Rolling history chunk manifest is unavailable") from exc
    required = {
        "schema_version", "kind", "scope", "request_id", "sequence", "event_id",
        "idempotency_id", "event_type", "attachments", "payload_sha256",
        "content_utf8_byte_length", "text_sha256", "chunk_count",
        "chunk_root_sha256", "previous_chain_sha256", "content_sha256", "chain_sha256",
    }
    if (type(value) is not dict or set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _CHUNKED_EVENT_SCHEMA_VERSION
            or value["kind"] != "event_chunk_manifest"
            or not _scope_matches(value["scope"], scope)
            or not _valid_request_id(value["request_id"])
            or type(value["sequence"]) is not int or value["sequence"] < 1
            or (expected_sequence is not None and value["sequence"] != expected_sequence)
            or type(value["event_id"]) is not str or _HASH.fullmatch(value["event_id"]) is None
            or type(value["idempotency_id"]) is not str or _HASH.fullmatch(value["idempotency_id"]) is None
            or type(value["event_type"]) is not str or value["event_type"] not in _EVENT_TYPES
            or type(value["attachments"]) is not list
            or any(type(item) is not dict for item in value["attachments"])
            or type(value["payload_sha256"]) is not str or _HASH.fullmatch(value["payload_sha256"]) is None
            or type(value["content_utf8_byte_length"]) is not int or value["content_utf8_byte_length"] < 0
            or type(value["text_sha256"]) is not str or _HASH.fullmatch(value["text_sha256"]) is None
            or type(value["chunk_count"]) is not int or value["chunk_count"] < 0
            or type(value["chunk_root_sha256"]) is not str
            or _HASH.fullmatch(value["chunk_root_sha256"]) is None
            or type(value["previous_chain_sha256"]) is not str
            or _HASH.fullmatch(value["previous_chain_sha256"]) is None
            or type(value["content_sha256"]) is not str
            or _HASH.fullmatch(value["content_sha256"]) is None
            or type(value["chain_sha256"]) is not str or _HASH.fullmatch(value["chain_sha256"]) is None
            or ((value["content_utf8_byte_length"] == 0)
                != (value["chunk_count"] == 0))
            or ((value["chunk_count"] == 0)
                != (value["chunk_root_sha256"] == _EVENT_CHUNK_EMPTY_ROOT))
            or _canonical(value) != raw):
        raise ValueError("Invalid rolling history chunk manifest")
    if (_sha256(_canonical(_chunked_manifest_core(value))) != value["content_sha256"]
            or _event_chain_digest(
                scope, value["sequence"], value["previous_chain_sha256"],
                value["event_id"], value["content_sha256"],
                schema_version=_CHUNKED_EVENT_SCHEMA_VERSION,
            ) != value["chain_sha256"]):
        raise ValueError("Rolling history chunk manifest commitment mismatch")
    return value


def _read_chunk_record(
    raw: str | None,
    scope: ContextEventScope,
    *,
    event_id: str,
    sequence: int,
    chunk_index: int,
    expected_sha256: str,
    expected_offset: int,
) -> tuple[str, str, int, int]:
    if raw is None:
        raise ValueError("Rolling history event chunk is unavailable")
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError("Rolling history event chunk is unavailable") from exc
    required = {
        "schema_version", "kind", "scope", "event_id", "sequence",
        "chunk_index", "start_offset", "end_offset", "content_chunk",
        "next_chunk_sha256", "chunk_sha256",
    }
    if (type(value) is not dict or set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _CHUNKED_EVENT_SCHEMA_VERSION
            or value["kind"] != "event_text_chunk"
            or not _scope_matches(value["scope"], scope)
            or value["event_id"] != event_id or value["sequence"] != sequence
            or type(value["chunk_index"]) is not int or value["chunk_index"] != chunk_index
            or type(value["start_offset"]) is not int or value["start_offset"] != expected_offset
            or type(value["end_offset"]) is not int or value["end_offset"] < expected_offset
            or type(value["content_chunk"]) is not str
            or type(value["next_chunk_sha256"]) is not str
            or _HASH.fullmatch(value["next_chunk_sha256"]) is None
            or type(value["chunk_sha256"]) is not str
            or _HASH.fullmatch(value["chunk_sha256"]) is None
            or expected_sha256 != value["chunk_sha256"]
            or _canonical(value) != raw):
        raise ValueError("Invalid rolling history event chunk")
    fields = {key: item for key, item in value.items() if key != "chunk_sha256"}
    raw_text = value["content_chunk"].encode("utf-8")
    if (value["end_offset"] - value["start_offset"] != len(raw_text)
            or len(raw_text) > _EVENT_TEXT_CHUNK_MAX_BYTES
            or _sha256(_canonical(fields)) != value["chunk_sha256"]):
        raise ValueError("Rolling history event chunk commitment mismatch")
    return (
        value["content_chunk"], value["next_chunk_sha256"],
        value["start_offset"], value["end_offset"],
    )


def _load_chunked_event_text(
    read_setting,
    prefix: str,
    scope: ContextEventScope,
    manifest: dict,
) -> str:
    expected_sha256 = manifest["chunk_root_sha256"]
    offset = 0
    pieces: list[str] = []
    for chunk_index in range(manifest["chunk_count"]):
        chunk_raw = read_setting(_event_chunk_key(prefix, manifest["event_id"], chunk_index))
        piece, next_sha256, start, end = _read_chunk_record(
            chunk_raw, scope,
            event_id=manifest["event_id"],
            sequence=manifest["sequence"],
            chunk_index=chunk_index,
            expected_sha256=expected_sha256,
            expected_offset=offset,
        )
        pieces.append(piece)
        offset = end
        expected_sha256 = next_sha256
    content = "".join(pieces)
    raw_text = content.encode("utf-8")
    if (expected_sha256 != _EVENT_CHUNK_EMPTY_ROOT
            or offset != manifest["content_utf8_byte_length"]
            or _sha256(raw_text) != manifest["text_sha256"]):
        raise ValueError("Rolling history event chunk sequence is incomplete")
    return content


def _load_event_row(
    db,
    scope: ContextEventScope,
    prefix: str,
    raw: str,
    expected_sequence: int | None = None,
    *,
    verify_parent: bool = True,
) -> RollingHistoryEvent:
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError("Rolling history event is unavailable") from exc
    if type(value) is not dict or type(value.get("schema_version")) is not int:
        raise ValueError("Invalid rolling history event envelope")
    version = value["schema_version"]
    if version == _SCHEMA_VERSION:
        keys = {
            "schema_version", "kind", "scope", "request_id", "sequence", "event_id",
            "idempotency_id", "payload", "content_sha256", "previous_chain_sha256", "chain_sha256",
        }
        if (set(value) != keys or value["kind"] != "event"
                or not _scope_matches(value["scope"], scope)
                or not _valid_request_id(value["request_id"])
                or type(value["sequence"]) is not int or value["sequence"] < 1
                or (expected_sequence is not None and value["sequence"] != expected_sequence)
                or type(value["event_id"]) is not str or _HASH.fullmatch(value["event_id"]) is None
                or type(value["idempotency_id"]) is not str or _HASH.fullmatch(value["idempotency_id"]) is None
                or type(value["payload"]) is not dict
                or type(value["content_sha256"]) is not str or _HASH.fullmatch(value["content_sha256"]) is None
                or type(value["previous_chain_sha256"]) is not str
                or _HASH.fullmatch(value["previous_chain_sha256"]) is None
                or type(value["chain_sha256"]) is not str or _HASH.fullmatch(value["chain_sha256"]) is None
                or _canonical(value) != raw):
            raise ValueError("Invalid rolling history event envelope")
        payload, encoded_payload = _encode_event(value["payload"])
        if payload != value["payload"] or _sha256(encoded_payload) != value["content_sha256"]:
            raise ValueError("Rolling history event hash mismatch")
    elif version == _CHUNKED_EVENT_SCHEMA_VERSION:
        value = _validate_chunked_event_manifest(raw, scope, expected_sequence)
        content = _load_chunked_event_text(
            lambda key: (row.value if (row := db.get(Settings, key)) is not None else None),
            prefix, scope, value,
        )
        payload, encoded_payload = _encode_event({
            "event_type": value["event_type"],
            "content": content,
            "attachments": value["attachments"],
        })
        if _sha256(encoded_payload) != value["payload_sha256"]:
            raise ValueError("Rolling history event payload commitment mismatch")
    else:
        raise ValueError("Unsupported rolling history event version")

    expected_chain = _event_chain_digest(
        scope, value["sequence"], value["previous_chain_sha256"],
        value["event_id"], value["content_sha256"], schema_version=version,
    )
    if expected_chain != value["chain_sha256"]:
        raise ValueError("Rolling history event chain hash mismatch")
    reference = RollingHistoryReference(
        value["event_id"], scope.session_id, value["request_id"], value["sequence"],
        version, value["content_sha256"], value["chain_sha256"],
    )
    id_row = db.get(Settings, _event_id_key(prefix, reference.event_id))
    if id_row is None:
        raise ValueError("Rolling history event ID is unavailable")
    try:
        id_value = json.loads(id_row.value)
    except Exception as exc:
        raise ValueError("Rolling history event ID is unavailable") from exc
    expected_id = {
        "schema_version": version,
        "kind": "event_id",
        "scope": _identity(scope),
        "event_id": reference.event_id,
        "request_id": reference.request_id,
        "sequence": reference.sequence,
        "content_sha256": reference.content_sha256,
        "chain_sha256": reference.chain_sha256,
    }
    if id_value != expected_id or _canonical(expected_id) != id_row.value:
        raise ValueError("Rolling history event ID binding mismatch")
    provenance_row = db.get(Settings, _skill_provenance_key(prefix, reference.event_id))
    skill_provenance = None if provenance_row is None else _parse_rolling_history_skill_provenance_mapping(
        provenance_row.value, scope, reference, payload["event_type"], payload["content"],
    )
    if verify_parent:
        if reference.sequence == 1:
            previous_chain = _genesis_chain(scope)
        else:
            parent_row = db.get(Settings, _event_key(prefix, reference.sequence - 1))
            if parent_row is None:
                raise ValueError("Rolling history event chain has a gap")
            parent = _load_event_row(
                db, scope, prefix, parent_row.value, reference.sequence - 1, verify_parent=False,
            )
            previous_chain = parent.reference.chain_sha256
        if value["previous_chain_sha256"] != previous_chain:
            raise ValueError("Rolling history event chain is discontinuous")
    return RollingHistoryEvent(
        reference,
        payload["event_type"],
        payload["content"],
        tuple(_parse_attachment(item) for item in payload["attachments"]),
        skill_provenance,
    )


def _load_event_at_sequence(
    db,
    scope: ContextEventScope,
    prefix: str,
    sequence: int,
    *,
    verify_parent: bool = True,
) -> RollingHistoryEvent | None:
    row = db.get(Settings, _event_key(prefix, sequence))
    if row is None:
        return None
    return _load_event_row(db, scope, prefix, row.value, sequence, verify_parent=verify_parent)


def _event_id_mapping(db, scope: ContextEventScope, prefix: str, event_id: str) -> dict | None:
    row = db.get(Settings, _event_id_key(prefix, event_id))
    if row is None:
        return None
    try:
        value = json.loads(row.value)
    except Exception as exc:
        raise ValueError("Rolling history event ID is unavailable") from exc
    expected_keys = {
        "schema_version", "kind", "scope", "event_id", "request_id",
        "sequence", "content_sha256", "chain_sha256",
    }
    if (type(value) is not dict or set(value) != expected_keys
            or type(value["schema_version"]) is not int
            or value["schema_version"] not in (
                _SCHEMA_VERSION, _CHUNKED_EVENT_SCHEMA_VERSION,
                _BODY_BACKED_EVENT_SCHEMA_VERSION,
            )
            or value["kind"] != "event_id" or value["event_id"] != event_id
            or not _scope_matches(value["scope"], scope)
            or not _valid_request_id(value["request_id"])
            or type(value["sequence"]) is not int or value["sequence"] < 1
            or type(value["content_sha256"]) is not str or _HASH.fullmatch(value["content_sha256"]) is None
            or type(value["chain_sha256"]) is not str or _HASH.fullmatch(value["chain_sha256"]) is None
            or _canonical(value) != row.value):
        raise ValueError("Invalid rolling history event ID")
    return value


def _is_utf8_boundary(raw: bytes, offset: int) -> bool:
    return (0 <= offset <= len(raw)
            and (offset == 0 or offset == len(raw) or (raw[offset] & 0xC0) != 0x80))


def _utf8_chunk_end(raw: bytes, start: int, max_bytes: int) -> int:
    end = min(start + max_bytes, len(raw))
    while end > start and not _is_utf8_boundary(raw, end):
        end -= 1
    if end == start and start < len(raw):
        raise ValueError("Chunk budget cannot hold the next UTF-8 code point")
    return end


def _append_idempotency_id(scope: ContextEventScope, idempotency_key: str) -> str:
    return _sha256(_canonical({
        "scope": _scope_digest(scope),
        "idempotency_key": idempotency_key,
    }))


def append_rolling_history(
    scope: ContextEventScope,
    events: list[dict],
    *,
    idempotency_key: str,
) -> tuple[RollingHistoryReference, ...]:
    """Atomically append immutable model-visible events and return locators after commit.

    Content is preserved as supplied by the trusted host producer. The producer
    must pass only model-visible event data, never transport credentials or host
    configuration. Retries can use a fresh request ID with the same scoped key.
    """
    _require_bound_scope(scope)
    if (type(events) is not list or not events
            or type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid rolling history append")
    payloads = []
    encoded = []
    for raw in events:
        payload, canonical = _encode_event(raw)
        payloads.append(payload)
        encoded.append(canonical)
    batch_sha256 = _batch_digest(encoded)
    idempotency_id = _append_idempotency_id(scope, idempotency_key)
    prefix = _scope_prefix(scope)
    idempotency_db_key = prefix + "idempotency:" + idempotency_id
    refs: list[RollingHistoryReference] = []

    with get_db_ctx() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        _validate_scope(db, scope)
        existing = db.get(Settings, idempotency_db_key)
        if existing is not None:
            try:
                value = json.loads(existing.value)
            except Exception as exc:
                raise ValueError("Rolling history retry record is unavailable") from exc
            expected_keys = {
                "schema_version", "kind", "scope", "idempotency_id", "request_id",
                "batch_sha256", "first_sequence", "event_count",
            }
            if (type(value) is not dict or set(value) != expected_keys
                    or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
                    or value["kind"] != "append_idempotency" or not _scope_matches(value["scope"], scope)
                    or value["idempotency_id"] != idempotency_id or not _valid_request_id(value["request_id"])
                    or value["batch_sha256"] != batch_sha256
                    or type(value["first_sequence"]) is not int or value["first_sequence"] < 1
                    or type(value["event_count"]) is not int or value["event_count"] != len(payloads)
                    or _canonical(value) != existing.value):
                raise ValueError("Conflicting rolling history retry")
            for offset, encoded_payload in enumerate(encoded):
                sequence = value["first_sequence"] + offset
                event = _load_event_at_sequence(db, scope, prefix, sequence)
                expected_event_id = _sha256(f"{idempotency_id}\0{offset + 1}")
                if (event is None or event.reference.request_id != value["request_id"]
                        or event.reference.event_id != expected_event_id
                        or _canonical(event.model_payload()) != encoded_payload):
                    raise ValueError("Rolling history retry content mismatch")
                refs.append(event.reference)
            return tuple(refs)

        last_sequence, last_chain = _history_head(db, prefix, scope)
        first_sequence = last_sequence + 1
        previous_chain = last_chain
        for offset, (payload, encoded_payload) in enumerate(zip(payloads, encoded, strict=True), start=1):
            sequence = last_sequence + offset
            event_id = _sha256(f"{idempotency_id}\0{offset}")
            event_value, chunk_records = _new_chunked_event_records(
                scope,
                request_id=scope.request_id,
                sequence=sequence,
                event_id=event_id,
                idempotency_id=idempotency_id,
                payload=payload,
                encoded_payload=encoded_payload,
                previous_chain_sha256=previous_chain,
            )
            content_sha256 = event_value["content_sha256"]
            chain_sha256 = event_value["chain_sha256"]
            reference = RollingHistoryReference(
                event_id, scope.session_id, scope.request_id, sequence, _CHUNKED_EVENT_SCHEMA_VERSION,
                content_sha256, chain_sha256,
            )
            id_value = {
                "schema_version": _CHUNKED_EVENT_SCHEMA_VERSION,
                "kind": "event_id",
                "scope": _identity(scope),
                "event_id": event_id,
                "request_id": scope.request_id,
                "sequence": sequence,
                "content_sha256": content_sha256,
                "chain_sha256": chain_sha256,
            }
            if (db.get(Settings, _event_key(prefix, sequence)) is not None
                    or db.get(Settings, _event_id_key(prefix, event_id)) is not None
                    or db.get(Settings, _event_manifest_key(prefix, event_id)) is not None
                    or db.get(Settings, _event_sequence_manifest_key(prefix, sequence)) is not None):
                raise ValueError("Rolling history event locator collision")
            encoded_manifest = _canonical(event_value)
            db.add(Settings(key=_event_key(prefix, sequence), value=encoded_manifest))
            db.add(Settings(key=_event_manifest_key(prefix, event_id), value=encoded_manifest))
            db.add(Settings(
                key=_event_sequence_manifest_key(prefix, sequence),
                value=_canonical({
                    "schema_version": _CHUNKED_EVENT_SCHEMA_VERSION,
                    "kind": "event_sequence_manifest",
                    "scope": _identity(scope),
                    "sequence": sequence,
                    "event_id": event_id,
                    "manifest_sha256": _sha256(encoded_manifest),
                    "content_sha256": content_sha256,
                    "chain_sha256": chain_sha256,
                }),
            ))
            db.add(Settings(key=_event_id_key(prefix, event_id), value=_canonical(id_value)))
            for chunk_index, chunk_record in enumerate(chunk_records):
                db.add(Settings(
                    key=_event_chunk_key(prefix, event_id, chunk_index),
                    value=chunk_record,
                ))
            refs.append(reference)
            previous_chain = chain_sha256

        idempotency_value = {
            "schema_version": _SCHEMA_VERSION,
            "kind": "append_idempotency",
            "scope": _identity(scope),
            "idempotency_id": idempotency_id,
            "request_id": scope.request_id,
            "batch_sha256": batch_sha256,
            "first_sequence": first_sequence,
            "event_count": len(payloads),
        }
        db.add(Settings(key=idempotency_db_key, value=_canonical(idempotency_value)))
        head_value = {
            "schema_version": _SCHEMA_VERSION,
            "kind": "history_head",
            "scope": _identity(scope),
            "last_sequence": last_sequence + len(payloads),
            "chain_sha256": previous_chain,
        }
        _set_row(db, prefix + "meta:head", _canonical(head_value))
    return tuple(refs)


def _require_owner_history_handle(
    handle: "RollingHistorySqlHandle",
    scope: ContextEventScope,
    expected_mode: "OperationMode",
) -> None:
    from app.api.rolling_history_owner_scope import (
        _require_active_rolling_history_sql_handle,
    )

    identity = _require_active_rolling_history_sql_handle(handle, expected_mode)
    if ((scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
            != (
                identity.session_id,
                identity.request_id,
                identity.principal_hash,
                identity.delete_epoch,
            )):
        raise ValueError("Rolling history scope does not match the active owner transaction")


def append_rolling_history_in_transaction(
    handle: "RollingHistorySqlHandle",
    scope: ContextEventScope,
    events: list[dict],
    *,
    idempotency_key: str,
    event_source_metadata: tuple[dict, ...] | None = None,
) -> tuple[RollingHistoryReference, ...]:
    """Append writer-verified body events inside the caller's owner transaction.

    The function never opens, commits, or rolls back a transaction. The supplied
    restricted owner handle, body chunks, event manifests, idempotency receipt,
    and history head all participate in the same transaction. Returned locators
    are provisional until the caller commits that outer transaction.
    """
    from app.api.rolling_history_owner_scope import OperationMode

    _require_owner_history_handle(handle, scope, OperationMode.WRITE)
    if (type(events) is not list or not events
            or type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid rolling history append")

    payloads: list[dict] = []
    encoded_payloads: list[str] = []
    for raw in events:
        payload, encoded_payload = _encode_event(raw)
        payloads.append(payload)
        encoded_payloads.append(encoded_payload)

    if event_source_metadata is None:
        source_metadata = tuple(
            _empty_body_event_source_metadata() for _ in payloads
        )
    elif (type(event_source_metadata) is tuple
            and len(event_source_metadata) == len(payloads)):
        source_metadata = tuple(
            _normalize_body_event_source_metadata(item)
            for item in event_source_metadata
        )
    else:
        raise ValueError("Invalid rolling history body event source metadata")
    batch_sha256 = _body_event_batch_digest(
        tuple(encoded_payloads), source_metadata,
    )
    idempotency_id = _append_idempotency_id(scope, idempotency_key)
    prefix = _scope_prefix(scope)
    idempotency_db_key = prefix + "idempotency:" + idempotency_id
    existing_raw, too_large = _owner_history_read_setting_bounded(
        handle, idempotency_db_key, _EVENT_MANIFEST_MAX_BYTES,
    )
    if too_large:
        raise ValueError("Rolling history retry record exceeds byte budget")
    if existing_raw is not None:
        try:
            existing = json.loads(existing_raw)
        except Exception as exc:
            raise ValueError("Rolling history retry record is unavailable") from exc
        expected_keys = {
            "schema_version", "kind", "scope", "idempotency_id", "request_id",
            "batch_sha256", "first_sequence", "event_count",
        }
        if (type(existing) is not dict or set(existing) != expected_keys
                or type(existing["schema_version"]) is not int
                or existing["schema_version"] != _SCHEMA_VERSION
                or existing["kind"] != "append_idempotency"
                or not _scope_matches(existing["scope"], scope)
                or existing["idempotency_id"] != idempotency_id
                or not _valid_request_id(existing["request_id"])
                or existing["batch_sha256"] != batch_sha256
                or type(existing["first_sequence"]) is not int
                or existing["first_sequence"] < 1
                or type(existing["event_count"]) is not int
                or existing["event_count"] != len(payloads)
                or _canonical(existing) != existing_raw):
            raise ValueError("Conflicting rolling history retry")

        references: list[RollingHistoryReference] = []
        previous_chain = (
            _genesis_chain(scope)
            if existing["first_sequence"] == 1
            else _owner_history_read_event_reference_at_sequence(
                handle, scope, prefix, existing["first_sequence"] - 1,
            ).chain_sha256
        )
        for offset, encoded_payload in enumerate(encoded_payloads):
            sequence = existing["first_sequence"] + offset
            reference = _owner_history_read_event_reference_at_sequence(
                handle,
                scope,
                prefix,
                sequence,
                expected_previous_chain_sha256=previous_chain,
            )
            if (reference.request_id != existing["request_id"]
                    or reference.event_id != _sha256(f"{idempotency_id}\0{offset + 1}")
                    or reference.version not in (
                        _CHUNKED_EVENT_SCHEMA_VERSION, _BODY_BACKED_EVENT_SCHEMA_VERSION,
                    )):
                raise ValueError("Rolling history retry locator mismatch")
            manifest_raw, manifest_too_large = _owner_history_read_setting_bounded(
                handle, _event_manifest_key(prefix, reference.event_id),
                _EVENT_MANIFEST_MAX_BYTES,
            )
            if manifest_too_large or manifest_raw is None:
                raise ValueError("Rolling history retry manifest is unavailable")
            manifest = (
                _validate_chunked_event_manifest(manifest_raw, scope, sequence)
                if reference.version == _CHUNKED_EVENT_SCHEMA_VERSION
                else _validate_body_backed_event_manifest(manifest_raw, scope, sequence)
            )
            existing_source_metadata = (
                manifest.get("source_metadata", _empty_body_event_source_metadata())
            )
            if (manifest["payload_sha256"] != _sha256(encoded_payload)
                    or existing_source_metadata != source_metadata[offset]):
                raise ValueError("Rolling history retry content mismatch")
            references.append(reference)
            previous_chain = reference.chain_sha256
        return tuple(references)

    last_sequence, previous_chain = _owner_history_head(handle, scope, prefix)
    first_sequence = last_sequence + 1
    references: list[RollingHistoryReference] = []
    for offset, (payload, encoded_payload) in enumerate(
        zip(payloads, encoded_payloads, strict=True), start=1,
    ):
        sequence = last_sequence + offset
        event_id = _sha256(f"{idempotency_id}\0{offset}")
        if (_owner_history_read_setting(handle, _event_key(prefix, sequence)) is not None
                or _owner_history_read_setting(handle, _event_id_key(prefix, event_id)) is not None
                or _owner_history_read_setting(handle, _event_manifest_key(prefix, event_id)) is not None
                or _owner_history_read_setting(
                    handle, _event_sequence_manifest_key(prefix, sequence),
                ) is not None):
            raise ValueError("Rolling history event locator collision")

        body_manifest, body_manifest_sha256 = _write_message_body_in_owner_transaction(
            handle, scope, body_id=_body_id_for_event(event_id), content=payload["content"],
        )
        event_value = _new_body_backed_event_manifest(
            scope,
            request_id=scope.request_id,
            sequence=sequence,
            event_id=event_id,
            idempotency_id=idempotency_id,
            payload=payload,
            encoded_payload=encoded_payload,
            previous_chain_sha256=previous_chain,
            body_manifest=body_manifest,
            body_manifest_sha256=body_manifest_sha256,
            source_metadata=source_metadata[offset - 1],
        )
        encoded_manifest = _canonical(event_value)
        if len(encoded_manifest.encode("utf-8")) > _EVENT_MANIFEST_MAX_BYTES:
            raise ValueError("Rolling history body manifest exceeds byte budget")
        content_sha256 = event_value["content_sha256"]
        chain_sha256 = event_value["chain_sha256"]
        reference = RollingHistoryReference(
            event_id, scope.session_id, scope.request_id, sequence,
            _BODY_BACKED_EVENT_SCHEMA_VERSION, content_sha256, chain_sha256,
        )
        id_value = {
            "schema_version": _BODY_BACKED_EVENT_SCHEMA_VERSION,
            "kind": "event_id",
            "scope": _identity(scope),
            "event_id": event_id,
            "request_id": scope.request_id,
            "sequence": sequence,
            "content_sha256": content_sha256,
            "chain_sha256": chain_sha256,
        }
        encoded_id = _canonical(id_value)
        sequence_value = {
            "schema_version": _BODY_BACKED_EVENT_SCHEMA_VERSION,
            "kind": "event_sequence_manifest",
            "scope": _identity(scope),
            "sequence": sequence,
            "event_id": event_id,
            "manifest_sha256": _sha256(encoded_manifest),
            "content_sha256": content_sha256,
            "chain_sha256": chain_sha256,
        }
        encoded_sequence = _canonical(sequence_value)
        if (len(encoded_id.encode("utf-8")) > _EVENT_ID_MAPPING_MAX_BYTES
                or len(encoded_sequence.encode("utf-8")) > _EVENT_ID_MAPPING_MAX_BYTES):
            raise ValueError("Rolling history body locator exceeds byte budget")

        _owner_history_insert_setting(handle, _event_key(prefix, sequence), encoded_manifest)
        _owner_history_insert_setting(
            handle, _event_manifest_key(prefix, event_id), encoded_manifest,
        )
        _owner_history_insert_setting(
            handle, _event_sequence_manifest_key(prefix, sequence), encoded_sequence,
        )
        _owner_history_insert_setting(handle, _event_id_key(prefix, event_id), encoded_id)
        references.append(reference)
        previous_chain = chain_sha256

    idempotency_value = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "append_idempotency",
        "scope": _identity(scope),
        "idempotency_id": idempotency_id,
        "request_id": scope.request_id,
        "batch_sha256": batch_sha256,
        "first_sequence": first_sequence,
        "event_count": len(payloads),
    }
    encoded_idempotency = _canonical(idempotency_value)
    if len(encoded_idempotency.encode("utf-8")) > _EVENT_MANIFEST_MAX_BYTES:
        raise ValueError("Rolling history retry record exceeds byte budget")
    _owner_history_insert_setting(handle, idempotency_db_key, encoded_idempotency)
    _owner_history_set_setting(
        handle,
        prefix + "meta:head",
        _canonical({
            "schema_version": _SCHEMA_VERSION,
            "kind": "history_head",
            "scope": _identity(scope),
            "last_sequence": last_sequence + len(payloads),
            "chain_sha256": previous_chain,
        }),
    )
    return tuple(references)


class RollingSkillEventAppendOutcomeUnknown(RuntimeError):
    """The owner transaction did not return a definitive post-commit receipt.

    The operation may have committed before its result became unavailable.
    Callers should retry with the same idempotency key to retrieve its receipt.
    """


@dataclass(frozen=True)
class _OwnerSkillEventAppendResult:
    reference: RollingHistoryReference | None = None
    outcome: str = "stored"


def _owner_history_rows(handle, statement: str, parameters, columns: tuple[str, ...]):
    from app.api.rolling_history_owner_scope import MaterializedRows

    result = handle.execute(statement, parameters)
    if type(result) is not MaterializedRows or result.columns != columns:
        raise ValueError("Rolling history SQL result is unavailable")
    return result.rows


def _owner_history_read_setting(handle, key: str) -> str | None:
    rows = _owner_history_rows(
        handle,
        "SELECT value FROM settings WHERE key = ?",
        (key,),
        ("value",),
    )
    if len(rows) > 1:
        raise ValueError("Duplicate rolling history setting")
    if not rows:
        return None
    value = rows[0][0]
    if type(value) is not str:
        raise ValueError("Invalid rolling history setting")
    return value


def _owner_history_read_setting_bounded(
    handle,
    key: str,
    max_bytes: int,
) -> tuple[str | None, bool]:
    rows = _owner_history_rows(
        handle,
        "SELECT substr(value, 1, ?) AS value_prefix, length(value) AS value_bytes FROM settings WHERE key = ?",
        (max_bytes, key),
        ("value_prefix", "value_bytes"),
    )
    if len(rows) > 1:
        raise ValueError("Duplicate rolling history setting")
    if not rows:
        return None, False
    prefix, value_bytes = rows[0]
    if (type(prefix) is not str or type(value_bytes) is not int
            or value_bytes < 0):
        raise ValueError("Invalid rolling history setting")
    if value_bytes > max_bytes:
        return None, True
    return prefix, False


def _owner_history_insert_setting(handle, key: str, value: str) -> None:
    changed = handle.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value),
    )
    if type(changed) is not int or changed != 1:
        raise ValueError("Rolling history setting write is unavailable")


def _owner_history_set_setting(handle, key: str, value: str) -> None:
    existing = _owner_history_read_setting(handle, key)
    if existing is None:
        _owner_history_insert_setting(handle, key, value)
        return
    changed = handle.execute(
        "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
        (value, key),
    )
    if type(changed) is not int or changed != 1:
        raise ValueError("Rolling history setting write is unavailable")


def _owner_history_read_message_body(
    handle,
    scope: ContextEventScope,
    event_manifest: dict,
) -> str:
    from app.db.database import read_message_body_chunk_in_transaction

    body_manifest = {
        "body_id": event_manifest["body_id"],
        "session_id": scope.session_id,
        "byte_length": event_manifest["body_byte_length"],
        "sha256": event_manifest["body_sha256"],
        "chunk_size": event_manifest["body_chunk_size"],
        "chunk_count": event_manifest["body_chunk_count"],
        "final_chain_sha256": event_manifest["body_chain_sha256"],
        "status": "writer_verified",
        "encoding": "utf-8",
    }
    body_manifest_sha256 = _sha256(_canonical(body_manifest))
    if body_manifest_sha256 != event_manifest["body_manifest_sha256"]:
        raise ValueError("Owner-bound message body manifest commitment mismatch")

    body_bytes = bytearray()
    previous_chain_sha256 = None
    final_chain_sha256 = None
    for chunk_index in range(event_manifest["body_chunk_count"]):
        offset = chunk_index * _MESSAGE_BODY_CHUNK_SIZE
        chunk = read_message_body_chunk_in_transaction(
            handle,
            session_id=scope.session_id,
            body_id=event_manifest["body_id"],
            byte_offset=offset,
            max_rows=3,
            max_bytes=_MESSAGE_BODY_CHUNK_SIZE,
        )
        if (chunk is None
                or chunk.body_id != event_manifest["body_id"]
                or chunk.byte_offset != offset
                or chunk.chunk_length != len(chunk.chunk_bytes)
                or not 1 <= chunk.chunk_length <= _MESSAGE_BODY_CHUNK_SIZE
                or chunk.next_offset != offset + chunk.chunk_length
                or chunk.manifest_byte_length != event_manifest["body_byte_length"]
                or chunk.manifest_sha256 != event_manifest["body_sha256"]
                or chunk.manifest_chunk_count != event_manifest["body_chunk_count"]
                or chunk.manifest_chain_sha256 != event_manifest["body_chain_sha256"]
                or (previous_chain_sha256 is not None
                    and chunk.previous_chain_sha256 != previous_chain_sha256)):
            raise ValueError("Owner-bound message body chunk does not match its event manifest")
        body_bytes.extend(chunk.chunk_bytes)
        previous_chain_sha256 = chunk.chain_sha256
        final_chain_sha256 = chunk.chain_sha256
    if (len(body_bytes) != event_manifest["body_byte_length"]
            or _sha256(bytes(body_bytes)) != event_manifest["body_sha256"]
            or (event_manifest["body_chunk_count"]
                and final_chain_sha256 != event_manifest["body_chain_sha256"])):
        raise ValueError("Owner-bound message body does not match its event commitment")
    try:
        return bytes(body_bytes).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Owner-bound message body is not valid UTF-8") from exc


def _body_chunk_cursor_value(cursor: RollingHistoryBodyChunkCursor) -> dict:
    return {
        "cursor_version": cursor.cursor_version,
        "session_id": cursor.session_id,
        "page_pin_sha256": cursor.page_pin_sha256,
        "page_index": cursor.page_index,
        "locator": asdict(cursor.locator),
        "branch_id": cursor.branch_id,
        "revision_id": cursor.revision_id,
        "revision_sha256": cursor.revision_sha256,
        "head_sha256": cursor.head_sha256,
        "lineage_root_sha256": cursor.lineage_root_sha256,
        "lineage_proof_sha256": cursor.lineage_proof_sha256,
        "event_manifest_sha256": cursor.event_manifest_sha256,
        "body_manifest_sha256": cursor.body_manifest_sha256,
        "payload_sha256": cursor.payload_sha256,
        "body_sha256": cursor.body_sha256,
        "event_chain_sha256": cursor.event_chain_sha256,
        "next_offset": cursor.next_offset,
        "previous_body_chain_sha256": cursor.previous_body_chain_sha256,
    }


def _validate_body_chunk_cursor(cursor: object) -> None:
    if (type(cursor) is not RollingHistoryBodyChunkCursor
            or type(cursor.cursor_version) is not int or cursor.cursor_version != 1
            or type(cursor.locator) is not RollingHistoryV2EventLocator
            or cursor.locator.version != _BODY_BACKED_EVENT_SCHEMA_VERSION
            or type(cursor.page_index) is not int or cursor.page_index < 0
            or type(cursor.next_offset) is not int or cursor.next_offset < 1
            or cursor.next_offset % _MESSAGE_BODY_CHUNK_SIZE != 0
            or any(type(value) is not str or _HASH.fullmatch(value) is None for value in (
                cursor.page_pin_sha256,
                cursor.branch_id,
                cursor.revision_id,
                cursor.revision_sha256,
                cursor.head_sha256,
                cursor.lineage_root_sha256,
                cursor.lineage_proof_sha256,
                cursor.event_manifest_sha256,
                cursor.body_manifest_sha256,
                cursor.payload_sha256,
                cursor.body_sha256,
                cursor.event_chain_sha256,
                cursor.previous_body_chain_sha256,
                cursor.cursor_sha256,
            ))
            or type(cursor.session_id) is not str
            or cursor.cursor_sha256 != _sha256(_canonical(_body_chunk_cursor_value(cursor)))):
        raise ValueError("Invalid rolling history body chunk cursor")


def _new_body_chunk_cursor(
    *,
    session_id: str,
    page_pin_sha256: str,
    page_index: int,
    locator: "RollingHistoryV2EventLocator",
    branch_id: str,
    revision_id: str,
    revision_sha256: str,
    head_sha256: str,
    lineage_root_sha256: str,
    lineage_proof_sha256: str,
    event_manifest_sha256: str,
    body_manifest_sha256: str,
    payload_sha256: str,
    body_sha256: str,
    event_chain_sha256: str,
    next_offset: int,
    previous_body_chain_sha256: str,
) -> RollingHistoryBodyChunkCursor:
    values = {
        "cursor_version": 1,
        "session_id": session_id,
        "page_pin_sha256": page_pin_sha256,
        "page_index": page_index,
        "locator": locator,
        "branch_id": branch_id,
        "revision_id": revision_id,
        "revision_sha256": revision_sha256,
        "head_sha256": head_sha256,
        "lineage_root_sha256": lineage_root_sha256,
        "lineage_proof_sha256": lineage_proof_sha256,
        "event_manifest_sha256": event_manifest_sha256,
        "body_manifest_sha256": body_manifest_sha256,
        "payload_sha256": payload_sha256,
        "body_sha256": body_sha256,
        "event_chain_sha256": event_chain_sha256,
        "next_offset": next_offset,
        "previous_body_chain_sha256": previous_body_chain_sha256,
    }
    provisional = RollingHistoryBodyChunkCursor(
        **values,
        cursor_sha256="0" * 64,
    )
    result = RollingHistoryBodyChunkCursor(
        **values,
        cursor_sha256=_sha256(_canonical(_body_chunk_cursor_value(provisional))),
    )
    _validate_body_chunk_cursor(result)
    return result


def read_rolling_history_event_body_chunk_in_transaction(
    handle: "RollingHistorySqlHandle",
    scope: ContextEventScope,
    session_id: str,
    *,
    page_pin_sha256: str,
    page_index: int,
    locator: "RollingHistoryV2EventLocator",
    branch_id: str,
    revision_id: str,
    revision_sha256: str,
    head_sha256: str,
    lineage_root_sha256: str,
    lineage_proof_sha256: str,
    cursor: RollingHistoryBodyChunkCursor | None = None,
    max_rows: int = 3,
    max_bytes: int = _MESSAGE_BODY_CHUNK_SIZE,
) -> RollingHistoryBodyEventChunk | None:
    """Read exactly one verified body chunk under the caller's pinned owner READ handle."""
    from app.api.rolling_history_owner_scope import OperationMode

    _require_owner_history_handle(handle, scope, OperationMode.READ)
    if (session_id != scope.session_id
            or type(page_pin_sha256) is not str or _HASH.fullmatch(page_pin_sha256) is None
            or type(page_index) is not int or page_index < 0
            or type(locator) is not RollingHistoryV2EventLocator
            or locator.version != _BODY_BACKED_EVENT_SCHEMA_VERSION
            or any(type(value) is not str or _HASH.fullmatch(value) is None for value in (
                branch_id, revision_id, revision_sha256,
                head_sha256, lineage_root_sha256, lineage_proof_sha256,
            ))
            or type(max_rows) is not int or max_rows != 3
            or type(max_bytes) is not int or max_bytes != _MESSAGE_BODY_CHUNK_SIZE):
        raise ValueError("Invalid rolling history body chunk read")
    if cursor is not None:
        _validate_body_chunk_cursor(cursor)
        if (cursor.session_id != session_id
                or cursor.page_pin_sha256 != page_pin_sha256
                or cursor.page_index != page_index
                or cursor.locator != locator
                or cursor.branch_id != branch_id
                or cursor.revision_id != revision_id
                or cursor.revision_sha256 != revision_sha256
                or cursor.head_sha256 != head_sha256
                or cursor.lineage_root_sha256 != lineage_root_sha256
                or cursor.lineage_proof_sha256 != lineage_proof_sha256):
            raise ValueError("Rolling history body cursor belongs to a different pinned page")

    prefix = _scope_prefix(scope)
    previous_chain = (
        _genesis_chain(scope)
        if locator.sequence == 1
        else _owner_history_read_event_reference_at_sequence(
            handle, scope, prefix, locator.sequence - 1,
        ).chain_sha256
    )
    reference = _owner_history_read_event_reference_at_sequence(
        handle,
        scope,
        prefix,
        locator.sequence,
        expected_previous_chain_sha256=previous_chain,
    )
    if (reference.event_id != locator.event_id
            or reference.request_id != locator.request_id
            or reference.sequence != locator.sequence
            or reference.version != locator.version
            or reference.content_sha256 != locator.content_sha256
            or reference.chain_sha256 != locator.chain_sha256):
        raise ValueError("Pinned locator does not match its event source mapping")

    manifest_raw, too_large = _owner_history_read_setting_bounded(
        handle, _event_manifest_key(prefix, locator.event_id),
        _EVENT_MANIFEST_MAX_BYTES,
    )
    if too_large or manifest_raw is None:
        raise ValueError("Rolling history body event manifest is unavailable")
    manifest = _validate_body_backed_event_manifest(
        manifest_raw, scope, locator.sequence,
    )
    event_manifest_sha256 = _sha256(manifest_raw)
    if (manifest["event_id"] != locator.event_id
            or manifest["request_id"] != locator.request_id
            or manifest["content_sha256"] != locator.content_sha256
            or manifest["chain_sha256"] != locator.chain_sha256
            or manifest["body_id"] != _body_id_for_event(locator.event_id)):
        raise ValueError("Rolling history body manifest does not match its pinned locator")

    body_manifest_value = {
        "body_id": manifest["body_id"],
        "session_id": session_id,
        "byte_length": manifest["body_byte_length"],
        "sha256": manifest["body_sha256"],
        "chunk_size": manifest["body_chunk_size"],
        "chunk_count": manifest["body_chunk_count"],
        "final_chain_sha256": manifest["body_chain_sha256"],
        "status": "writer_verified",
        "encoding": "utf-8",
    }
    body_manifest_sha256 = _sha256(_canonical(body_manifest_value))
    if body_manifest_sha256 != manifest["body_manifest_sha256"]:
        raise ValueError("Rolling history body store manifest commitment mismatch")
    if cursor is not None and (
        cursor.event_manifest_sha256 != event_manifest_sha256
        or cursor.body_manifest_sha256 != body_manifest_sha256
        or cursor.payload_sha256 != manifest["payload_sha256"]
        or cursor.body_sha256 != manifest["body_sha256"]
        or cursor.event_chain_sha256 != manifest["chain_sha256"]
    ):
        raise ValueError("Rolling history body cursor commitment mismatch")

    offset = 0 if cursor is None else cursor.next_offset
    if offset >= manifest["body_byte_length"]:
        if manifest["body_byte_length"] == 0 and cursor is None:
            return None
        raise ValueError("Rolling history body cursor is at or beyond EOF")
    if offset % _MESSAGE_BODY_CHUNK_SIZE != 0:
        raise ValueError("Rolling history body cursor is not chunk aligned")

    from app.db.database import read_message_body_chunk_in_transaction

    chunk = read_message_body_chunk_in_transaction(
        handle,
        session_id=session_id,
        body_id=manifest["body_id"],
        byte_offset=offset,
        max_rows=3,
        max_bytes=max_bytes,
    )
    if chunk is None:
        raise ValueError("Rolling history body chunk is unavailable before EOF")
    raw_chunk = chunk.chunk_bytes
    chunk_length = chunk.chunk_length
    end_offset = chunk.next_offset
    if (type(raw_chunk) is not bytes
            or type(chunk_length) is not int or chunk_length != len(raw_chunk)
            or not 1 <= chunk_length <= max_bytes
            or chunk.byte_offset != offset
            or end_offset != offset + chunk_length
            or chunk.manifest_byte_length != manifest["body_byte_length"]
            or chunk.manifest_sha256 != manifest["body_sha256"]
            or chunk.manifest_chunk_count != manifest["body_chunk_count"]
            or chunk.manifest_chain_sha256 != manifest["body_chain_sha256"]
            or type(chunk.previous_chain_sha256) is not str
            or _HASH.fullmatch(chunk.previous_chain_sha256) is None
            or type(chunk.chain_sha256) is not str
            or _HASH.fullmatch(chunk.chain_sha256) is None
            or (cursor is not None
                and chunk.previous_chain_sha256 != cursor.previous_body_chain_sha256)
            or (end_offset < manifest["body_byte_length"]
                and chunk_length != _MESSAGE_BODY_CHUNK_SIZE)
            or (end_offset == manifest["body_byte_length"]
                and chunk_length != manifest["body_byte_length"] - offset)):
        raise ValueError("Rolling history body chunk does not match its manifest or cursor")
    body_chunk_sha256 = _sha256(raw_chunk)
    next_cursor = None
    if end_offset < manifest["body_byte_length"]:
        next_cursor = _new_body_chunk_cursor(
            session_id=session_id,
            page_pin_sha256=page_pin_sha256,
            page_index=page_index,
            locator=locator,
            branch_id=branch_id,
            revision_id=revision_id,
            revision_sha256=revision_sha256,
            head_sha256=head_sha256,
            lineage_root_sha256=lineage_root_sha256,
            lineage_proof_sha256=lineage_proof_sha256,
            event_manifest_sha256=event_manifest_sha256,
            body_manifest_sha256=body_manifest_sha256,
            payload_sha256=manifest["payload_sha256"],
            body_sha256=manifest["body_sha256"],
            event_chain_sha256=manifest["chain_sha256"],
            next_offset=end_offset,
            previous_body_chain_sha256=chunk.chain_sha256,
        )
    return RollingHistoryBodyEventChunk(
        page_pin_sha256=page_pin_sha256,
        page_index=page_index,
        branch_id=branch_id,
        revision_id=revision_id,
        revision_sha256=revision_sha256,
        head_sha256=head_sha256,
        lineage_root_sha256=lineage_root_sha256,
        lineage_proof_sha256=lineage_proof_sha256,
        locator=locator,
        event_id=manifest["event_id"],
        sequence=manifest["sequence"],
        message_id=manifest["source_metadata"]["message_id"],
        created_at=manifest["source_metadata"]["created_at"],
        message_event_sha256=manifest["source_metadata"]["message_event_sha256"],
        source_identity_sha256=manifest["source_metadata"]["source_identity_sha256"],
        body_id=manifest["body_id"],
        start_offset=offset,
        end_offset=end_offset,
        content_bytes=raw_chunk,
        body_manifest_sha256=body_manifest_sha256,
        body_chunk_sha256=body_chunk_sha256,
        previous_body_chain_sha256=chunk.previous_chain_sha256,
        body_chain_sha256=chunk.chain_sha256,
        payload_sha256=manifest["payload_sha256"],
        text_sha256=manifest["text_sha256"],
        event_chain_sha256=manifest["chain_sha256"],
        next_cursor=next_cursor,
    )


def _owner_history_load_event_row(
    handle,
    scope: ContextEventScope,
    prefix: str,
    raw: str,
    expected_sequence: int,
    *,
    verify_parent: bool = True,
) -> RollingHistoryEvent:
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError("Rolling history event is unavailable") from exc
    if type(value) is not dict or type(value.get("schema_version")) is not int:
        raise ValueError("Invalid rolling history event envelope")
    version = value["schema_version"]
    if version == _SCHEMA_VERSION:
        keys = {
            "schema_version", "kind", "scope", "request_id", "sequence", "event_id",
            "idempotency_id", "payload", "content_sha256", "previous_chain_sha256", "chain_sha256",
        }
        if (set(value) != keys or value["kind"] != "event"
                or not _scope_matches(value["scope"], scope)
                or not _valid_request_id(value["request_id"])
                or type(value["sequence"]) is not int or value["sequence"] != expected_sequence
                or expected_sequence < 1
                or type(value["event_id"]) is not str or _HASH.fullmatch(value["event_id"]) is None
                or type(value["idempotency_id"]) is not str or _HASH.fullmatch(value["idempotency_id"]) is None
                or type(value["payload"]) is not dict
                or type(value["content_sha256"]) is not str or _HASH.fullmatch(value["content_sha256"]) is None
                or type(value["previous_chain_sha256"]) is not str
                or _HASH.fullmatch(value["previous_chain_sha256"]) is None
                or type(value["chain_sha256"]) is not str or _HASH.fullmatch(value["chain_sha256"]) is None
                or _canonical(value) != raw):
            raise ValueError("Invalid rolling history event envelope")
        payload, encoded_payload = _encode_event(value["payload"])
        if payload != value["payload"] or _sha256(encoded_payload) != value["content_sha256"]:
            raise ValueError("Rolling history event hash mismatch")
    elif version == _CHUNKED_EVENT_SCHEMA_VERSION:
        value = _validate_chunked_event_manifest(raw, scope, expected_sequence)
        content = _load_chunked_event_text(
            lambda key: _owner_history_read_setting(handle, key), prefix, scope, value,
        )
        payload, encoded_payload = _encode_event({
            "event_type": value["event_type"],
            "content": content,
            "attachments": value["attachments"],
        })
        if _sha256(encoded_payload) != value["payload_sha256"]:
            raise ValueError("Rolling history event payload commitment mismatch")
    elif version == _BODY_BACKED_EVENT_SCHEMA_VERSION:
        value = _validate_body_backed_event_manifest(raw, scope, expected_sequence)
        content = _owner_history_read_message_body(handle, scope, value)
        payload, encoded_payload = _encode_event({
            "event_type": value["event_type"],
            "content": content,
            "attachments": value["attachments"],
        })
        if _sha256(encoded_payload) != value["payload_sha256"]:
            raise ValueError("Rolling history body payload commitment mismatch")
    else:
        raise ValueError("Unsupported rolling history event version")
    expected_chain = _event_chain_digest(
        scope, expected_sequence, value["previous_chain_sha256"],
        value["event_id"], value["content_sha256"], schema_version=version,
    )
    if expected_chain != value["chain_sha256"]:
        raise ValueError("Rolling history event chain hash mismatch")

    reference = RollingHistoryReference(
        value["event_id"], scope.session_id, value["request_id"], expected_sequence,
        version, value["content_sha256"], value["chain_sha256"],
    )
    raw_id = _owner_history_read_setting(handle, _event_id_key(prefix, reference.event_id))
    if raw_id is None:
        raise ValueError("Rolling history event ID is unavailable")
    try:
        id_value = json.loads(raw_id)
    except Exception as exc:
        raise ValueError("Rolling history event ID is unavailable") from exc
    expected_id = {
        "schema_version": version,
        "kind": "event_id",
        "scope": _identity(scope),
        "event_id": reference.event_id,
        "request_id": reference.request_id,
        "sequence": reference.sequence,
        "content_sha256": reference.content_sha256,
        "chain_sha256": reference.chain_sha256,
    }
    if id_value != expected_id or _canonical(expected_id) != raw_id:
        raise ValueError("Rolling history event ID binding mismatch")
    raw_provenance = _owner_history_read_setting(
        handle, _skill_provenance_key(prefix, reference.event_id),
    )
    skill_provenance = None if raw_provenance is None else (
        _parse_rolling_history_skill_provenance_mapping(
            raw_provenance, scope, reference, payload["event_type"], payload["content"],
        )
    )

    if verify_parent:
        if reference.sequence == 1:
            previous_chain = _genesis_chain(scope)
        else:
            parent_raw = _owner_history_read_setting(
                handle, _event_key(prefix, reference.sequence - 1),
            )
            if parent_raw is None:
                raise ValueError("Rolling history event chain has a gap")
            parent = _owner_history_load_event_row(
                handle, scope, prefix, parent_raw, reference.sequence - 1,
                verify_parent=False,
            )
            previous_chain = parent.reference.chain_sha256
        if value["previous_chain_sha256"] != previous_chain:
            raise ValueError("Rolling history event chain is discontinuous")

    return RollingHistoryEvent(
        reference,
        payload["event_type"],
        payload["content"],
        tuple(_parse_attachment(item) for item in payload["attachments"]),
        skill_provenance,
    )


def _owner_history_load_event_at_sequence(
    handle,
    scope: ContextEventScope,
    prefix: str,
    sequence: int,
    *,
    verify_parent: bool = True,
) -> RollingHistoryEvent | None:
    raw = _owner_history_read_setting(handle, _event_key(prefix, sequence))
    if raw is None:
        return None
    return _owner_history_load_event_row(
        handle, scope, prefix, raw, sequence, verify_parent=verify_parent,
    )


def _owner_history_read_event_reference_at_sequence(
    handle,
    scope: ContextEventScope,
    prefix: str,
    sequence: int,
    *,
    expected_previous_chain_sha256: str | None = None,
) -> RollingHistoryReference:
    marker_raw, too_large = _owner_history_read_setting_bounded(
        handle, _event_sequence_manifest_key(prefix, sequence),
        _EVENT_ID_MAPPING_MAX_BYTES,
    )
    if too_large:
        raise ValueError("Rolling history event sequence manifest exceeds byte budget")
    if marker_raw is None:
        raise RollingHistoryEventChunkMigrationRequired(
            RollingHistoryEventChunkMigrationRequired.code,
        )
    try:
        marker = json.loads(marker_raw)
    except Exception as exc:
        raise ValueError("Rolling history event sequence manifest is unavailable") from exc
    marker_keys = {
        "schema_version", "kind", "scope", "sequence", "event_id",
        "manifest_sha256", "content_sha256", "chain_sha256",
    }
    if (type(marker) is not dict or set(marker) != marker_keys
            or marker["schema_version"] not in (
                _CHUNKED_EVENT_SCHEMA_VERSION, _BODY_BACKED_EVENT_SCHEMA_VERSION,
            )
            or marker["kind"] != "event_sequence_manifest"
            or not _scope_matches(marker["scope"], scope)
            or type(marker["sequence"]) is not int or marker["sequence"] != sequence
            or type(marker["event_id"]) is not str or _HASH.fullmatch(marker["event_id"]) is None
            or type(marker["manifest_sha256"]) is not str or _HASH.fullmatch(marker["manifest_sha256"]) is None
            or type(marker["content_sha256"]) is not str or _HASH.fullmatch(marker["content_sha256"]) is None
            or type(marker["chain_sha256"]) is not str or _HASH.fullmatch(marker["chain_sha256"]) is None
            or _canonical(marker) != marker_raw):
        raise ValueError("Invalid rolling history event sequence manifest")
    manifest_raw, too_large = _owner_history_read_setting_bounded(
        handle, _event_manifest_key(prefix, marker["event_id"]),
        _EVENT_MANIFEST_MAX_BYTES,
    )
    if too_large or manifest_raw is None:
        raise ValueError("Rolling history event manifest is unavailable")
    manifest = (
        _validate_chunked_event_manifest(manifest_raw, scope, sequence)
        if marker["schema_version"] == _CHUNKED_EVENT_SCHEMA_VERSION
        else _validate_body_backed_event_manifest(manifest_raw, scope, sequence)
    )
    if (marker["manifest_sha256"] != _sha256(manifest_raw)
            or marker["event_id"] != manifest["event_id"]
            or marker["content_sha256"] != manifest["content_sha256"]
            or marker["chain_sha256"] != manifest["chain_sha256"]
            or (expected_previous_chain_sha256 is not None
                and manifest["previous_chain_sha256"] != expected_previous_chain_sha256)):
        raise ValueError("Rolling history event sequence manifest binding mismatch")
    mapping_raw, too_large = _owner_history_read_setting_bounded(
        handle, _event_id_key(prefix, marker["event_id"]),
        _EVENT_ID_MAPPING_MAX_BYTES,
    )
    if too_large or mapping_raw is None:
        raise ValueError("Rolling history event ID is unavailable")
    expected_mapping = {
        "schema_version": marker["schema_version"],
        "kind": "event_id",
        "scope": _identity(scope),
        "event_id": manifest["event_id"],
        "request_id": manifest["request_id"],
        "sequence": sequence,
        "content_sha256": manifest["content_sha256"],
        "chain_sha256": manifest["chain_sha256"],
    }
    if mapping_raw != _canonical(expected_mapping):
        raise ValueError("Rolling history event ID binding mismatch")
    return RollingHistoryReference(
        manifest["event_id"], scope.session_id, manifest["request_id"], sequence,
        marker["schema_version"], manifest["content_sha256"],
        manifest["chain_sha256"],
    )


def _owner_history_head(handle, scope: ContextEventScope, prefix: str) -> tuple[int, str]:
    head_raw = _owner_history_read_setting(handle, prefix + "meta:head")
    if head_raw is None:
        head_sequence = 0
        head_chain = _genesis_chain(scope)
    else:
        try:
            value = json.loads(head_raw)
        except Exception as exc:
            raise ValueError("Rolling history head is unavailable") from exc
        if (type(value) is not dict
                or set(value) != {"schema_version", "kind", "scope", "last_sequence", "chain_sha256"}
                or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
                or value["kind"] != "history_head" or not _scope_matches(value["scope"], scope)
                or type(value["last_sequence"]) is not int or value["last_sequence"] < 0
                or type(value["chain_sha256"]) is not str
                or _HASH.fullmatch(value["chain_sha256"]) is None
                or _canonical(value) != head_raw):
            raise ValueError("Invalid rolling history head")
        head_sequence = value["last_sequence"]
        head_chain = value["chain_sha256"]

    event_prefix = prefix + "event:"
    latest_rows = _owner_history_rows(
        handle,
        "SELECT key FROM settings WHERE key >= ? AND key < ? ORDER BY key DESC LIMIT 1",
        (event_prefix, prefix + "event;"),
        ("key",),
    )
    if not latest_rows:
        observed_sequence = 0
        observed_chain = _genesis_chain(scope)
    else:
        latest_key = latest_rows[0][0]
        if type(latest_key) is not str or not latest_key.startswith(event_prefix):
            raise ValueError("Invalid rolling history sequence key")
        suffix = latest_key[len(event_prefix):]
        if not suffix.isdigit():
            raise ValueError("Invalid rolling history sequence key")
        observed_sequence = int(suffix)
        reference = _owner_history_read_event_reference_at_sequence(
            handle, scope, prefix, observed_sequence,
        )
        observed_chain = reference.chain_sha256
    if observed_sequence != head_sequence or observed_chain != head_chain:
        raise ValueError("Rolling history head does not match stored events")
    return head_sequence, head_chain


def _append_prepared_skill_event_in_owner_transaction(
    handle,
    scope: ContextEventScope,
    event_payload: dict,
    encoded_payload: str,
    skill_provenance: dict,
    encoded_skill_provenance: str,
    skill_provenance_sha256: str,
    idempotency_key: str,
) -> _OwnerSkillEventAppendResult:
    parsed_provenance = _parse_rolling_history_skill_provenance(
        skill_provenance, scope, event_payload["event_type"], event_payload["content"],
    )
    if (_canonical(_rolling_history_skill_provenance_value(parsed_provenance))
            != encoded_skill_provenance
            or _sha256(encoded_skill_provenance) != skill_provenance_sha256):
        raise ValueError("Rolling Skill provenance commitment changed")
    batch_sha256 = _batch_digest((encoded_payload, encoded_skill_provenance))
    idempotency_id = _append_idempotency_id(scope, idempotency_key)
    prefix = _scope_prefix(scope)
    idempotency_db_key = prefix + "idempotency:" + idempotency_id
    existing_raw = _owner_history_read_setting(handle, idempotency_db_key)

    if existing_raw is not None:
        try:
            existing = json.loads(existing_raw)
        except Exception:
            return _OwnerSkillEventAppendResult(outcome="unavailable")
        expected_keys = {
            "schema_version", "kind", "scope", "idempotency_id", "request_id",
            "batch_sha256", "first_sequence", "event_count",
        }
        if (type(existing) is not dict or set(existing) != expected_keys
                or type(existing["schema_version"]) is not int
                or existing["schema_version"] != _SCHEMA_VERSION
                or existing["kind"] != "append_idempotency"
                or existing["idempotency_id"] != idempotency_id
                or not _valid_request_id(existing["request_id"])
                or type(existing["first_sequence"]) is not int or existing["first_sequence"] < 1
                or type(existing["event_count"]) is not int
                or _canonical(existing) != existing_raw):
            return _OwnerSkillEventAppendResult(outcome="unavailable")
        if (not _scope_matches(existing["scope"], scope)
                or existing["batch_sha256"] != batch_sha256
                or existing["event_count"] != 1):
            return _OwnerSkillEventAppendResult(outcome="conflict")

        try:
            event = _owner_history_load_event_at_sequence(
                handle, scope, prefix, existing["first_sequence"],
            )
        except ValueError:
            return _OwnerSkillEventAppendResult(outcome="unavailable")
        expected_event_id = _sha256(f"{idempotency_id}\0{1}")
        if (event is None or event.reference.request_id != existing["request_id"]
                or event.reference.event_id != expected_event_id):
            return _OwnerSkillEventAppendResult(outcome="unavailable")
        if _canonical(event.model_payload()) != encoded_payload:
            return _OwnerSkillEventAppendResult(outcome="conflict")
        if (event.skill_provenance is None
                or _canonical(_rolling_history_skill_provenance_value(event.skill_provenance))
                != encoded_skill_provenance):
            return _OwnerSkillEventAppendResult(outcome="conflict")
        return _OwnerSkillEventAppendResult(reference=event.reference)

    try:
        last_sequence, last_chain = _owner_history_head(handle, scope, prefix)
    except ValueError:
        return _OwnerSkillEventAppendResult(outcome="unavailable")
    sequence = last_sequence + 1
    event_id = _sha256(f"{idempotency_id}\0{1}")
    body_manifest, body_manifest_sha256 = _write_message_body_in_owner_transaction(
        handle, scope, body_id=_body_id_for_event(event_id), content=event_payload["content"],
    )
    event_value = _new_body_backed_event_manifest(
        scope,
        request_id=scope.request_id,
        sequence=sequence,
        event_id=event_id,
        idempotency_id=idempotency_id,
        payload=event_payload,
        encoded_payload=encoded_payload,
        previous_chain_sha256=last_chain,
        body_manifest=body_manifest,
        body_manifest_sha256=body_manifest_sha256,
    )
    content_sha256 = event_value["content_sha256"]
    chain_sha256 = event_value["chain_sha256"]
    reference = RollingHistoryReference(
        event_id, scope.session_id, scope.request_id, sequence,
        _BODY_BACKED_EVENT_SCHEMA_VERSION,
        content_sha256, chain_sha256,
    )
    id_value = {
        "schema_version": _BODY_BACKED_EVENT_SCHEMA_VERSION,
        "kind": "event_id",
        "scope": _identity(scope),
        "event_id": event_id,
        "request_id": scope.request_id,
        "sequence": sequence,
        "content_sha256": content_sha256,
        "chain_sha256": chain_sha256,
    }
    provenance_value = _make_rolling_history_skill_provenance_mapping(
        scope, reference, parsed_provenance,
    )
    if (_owner_history_read_setting(handle, _event_key(prefix, sequence)) is not None
            or _owner_history_read_setting(handle, _event_id_key(prefix, event_id)) is not None
            or _owner_history_read_setting(handle, _event_manifest_key(prefix, event_id)) is not None
            or _owner_history_read_setting(
                handle, _event_sequence_manifest_key(prefix, sequence),
            ) is not None
            or _owner_history_read_setting(handle, _skill_provenance_key(prefix, event_id)) is not None
            or _owner_history_read_setting(handle, idempotency_db_key) is not None):
        return _OwnerSkillEventAppendResult(outcome="unavailable")

    idempotency_value = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "append_idempotency",
        "scope": _identity(scope),
        "idempotency_id": idempotency_id,
        "request_id": scope.request_id,
        "batch_sha256": batch_sha256,
        "first_sequence": sequence,
        "event_count": 1,
    }
    encoded_manifest = _canonical(event_value)
    _owner_history_insert_setting(handle, _event_key(prefix, sequence), encoded_manifest)
    _owner_history_insert_setting(
        handle, _event_manifest_key(prefix, event_id), encoded_manifest,
    )
    _owner_history_insert_setting(
        handle,
        _event_sequence_manifest_key(prefix, sequence),
        _canonical({
            "schema_version": _BODY_BACKED_EVENT_SCHEMA_VERSION,
            "kind": "event_sequence_manifest",
            "scope": _identity(scope),
            "sequence": sequence,
            "event_id": event_id,
            "manifest_sha256": _sha256(encoded_manifest),
            "content_sha256": content_sha256,
            "chain_sha256": chain_sha256,
        }),
    )
    _owner_history_insert_setting(handle, _event_id_key(prefix, event_id), _canonical(id_value))
    _owner_history_insert_setting(
        handle, _skill_provenance_key(prefix, event_id), _canonical(provenance_value),
    )
    _owner_history_insert_setting(
        handle, idempotency_db_key, _canonical(idempotency_value),
    )
    head_value = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "history_head",
        "scope": _identity(scope),
        "last_sequence": sequence,
        "chain_sha256": chain_sha256,
    }
    _owner_history_set_setting(handle, prefix + "meta:head", _canonical(head_value))
    return _OwnerSkillEventAppendResult(reference=reference)


def append_prepared_rolling_skill_event(
    prepared: object,
    user: dict | None,
    origin,
    *,
    idempotency_key: str,
) -> RollingHistoryReference:
    """Persist one complete, host-verified Skill event inside the owner transaction.

    ``user`` must be the authenticated route's ``current_user`` value, ``origin``
    must identify the already-bound host archive scope, and an owner bootstrap
    must be active. The active proof collector's reentrant lock remains held
    through owner transaction commit. No route or production caller is wired.
    """
    from src.agent.compact.rolling_skill_event_bridge import (
        PreparedRollingSkillEvent,
        RollingSkillEventBridgeRejected,
    )
    from src.agent.skill_proof import SkillProofCollector, current_skill_proof_collector

    if type(prepared) is not PreparedRollingSkillEvent:
        raise RollingSkillEventBridgeRejected("prepared_host_event_required")
    proof_collector = getattr(prepared.proof, "_collector", None)
    if (type(proof_collector) is not SkillProofCollector
            or getattr(prepared.candidate, "_collector", None) is not proof_collector
            or current_skill_proof_collector() is not proof_collector
            or not proof_collector.active):
        raise RollingSkillEventBridgeRejected("active_collector_changed_or_closed")
    proof_lock = getattr(proof_collector, "_lock", None)
    if (proof_lock is None
            or not callable(getattr(proof_lock, "__enter__", None))
            or not callable(getattr(proof_lock, "__exit__", None))):
        raise RollingSkillEventBridgeRejected("active_collector_lock_unavailable")
    with proof_lock:
        if (current_skill_proof_collector() is not proof_collector
                or not proof_collector.active):
            raise RollingSkillEventBridgeRejected("active_collector_changed_or_closed")
        return _append_prepared_rolling_skill_event_under_collector_lock(
            prepared,
            user,
            origin,
            idempotency_key=idempotency_key,
            proof_collector=proof_collector,
        )


def _append_prepared_rolling_skill_event_under_collector_lock(
    prepared: object,
    user: dict | None,
    origin,
    *,
    idempotency_key: str,
    proof_collector,
) -> RollingHistoryReference:
    """Run prevalidation, strict write, and postvalidation under the collector lock."""
    from app.api.compaction_archive import get_compaction_scope
    from app.api.rolling_history_owner_scope import (
        RollingHistoryOwnerScopeError,
        ScopeOrigin,
        get_rolling_history_owner_identity,
        run_rolling_history_owner_write_operation_strict_memory_epoch,
    )
    from app.api.session_owner import principal_digest_for_user
    from app.api.tool_events import get_tool_event_scope
    from src.agent.compact.rolling_skill_event_bridge import (
        PreparedRollingSkillEvent,
        RollingSkillEventBridgeRejected,
        validate_prepared_skill_event_before_transaction,
    )

    if type(prepared) is not PreparedRollingSkillEvent:
        raise RollingSkillEventBridgeRejected("prepared_host_event_required")
    if (prepared.proof._collector is not proof_collector
            or prepared.candidate._collector is not proof_collector):
        raise RollingSkillEventBridgeRejected("active_collector_changed_or_closed")
    validate_prepared_skill_event_before_transaction(prepared)
    if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid rolling history append")
    if type(origin) is not ScopeOrigin:
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")

    identity = get_rolling_history_owner_identity()
    prepared_object = prepared
    prepared_candidate = prepared.candidate
    prepared_provenance = prepared.provenance
    prepared_request_binding = prepared_provenance.request_binding
    prepared_proof = prepared.proof
    prepared_event_type = prepared.event_type
    prepared_content = prepared.content
    binding = prepared_request_binding
    memory_epoch = binding.memory_clear_epoch
    if (type(memory_epoch) is not str
            or (memory_epoch != "" and _REQUEST_ID.fullmatch(memory_epoch) is None)):
        raise ValueError("Prepared Skill event lacks a strict memory epoch")
    try:
        host_principal = principal_digest_for_user(user)
    except Exception:
        raise RollingHistoryOwnerScopeError(
            "Rolling history owner scope is unavailable",
        ) from None
    scope = (
        get_compaction_scope()
        if origin is ScopeOrigin.COMPACTION
        else get_tool_event_scope()
    )
    _require_bound_scope(scope)
    if (host_principal != identity.principal_hash
            or binding.principal_hash != identity.principal_hash
            or binding.session_id != identity.session_id
            or binding.request_id != identity.request_id
            or binding.session_delete_epoch != identity.delete_epoch
            or (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
            != (
                identity.session_id,
                identity.request_id,
                identity.principal_hash,
                identity.delete_epoch,
            )):
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    if prepared.event_type not in {"skill_instruction", "skill_resource"}:
        raise RollingSkillEventBridgeRejected("prepared_event_type_mismatch")

    event_payload, encoded_payload = _encode_event({
        "event_type": prepared_event_type,
        "content": prepared_content,
    })
    skill_provenance_value, encoded_skill_provenance, skill_provenance_sha256 = (
        _encode_prepared_rolling_skill_provenance(
            prepared_provenance, scope, prepared_event_type, prepared_content,
        )
    )

    def revalidate_captured_prepared_event():
        if (prepared is not prepared_object
                or prepared.candidate is not prepared_candidate
                or prepared.provenance is not prepared_provenance
                or prepared.proof is not prepared_proof
                or prepared.provenance.request_binding is not prepared_request_binding
                or prepared_request_binding != binding
                or prepared_request_binding.memory_clear_epoch != memory_epoch
                or prepared.event_type != prepared_event_type
                or prepared.content is not prepared_content):
            raise RollingSkillEventBridgeRejected("prepared_event_changed_during_append")
        fresh_proof = validate_prepared_skill_event_before_transaction(prepared)
        if (fresh_proof != prepared_proof
                or fresh_proof._collector is not prepared_proof._collector):
            raise RollingSkillEventBridgeRejected("prepared_proof_changed_during_append")
        return fresh_proof

    def operation(handle):
        try:
            active_identity = get_rolling_history_owner_identity()
        except RollingHistoryOwnerScopeError:
            return _OwnerSkillEventAppendResult(outcome="unavailable")
        if active_identity != identity:
            return _OwnerSkillEventAppendResult(outcome="unavailable")
        proof_before_write = revalidate_captured_prepared_event()
        result = _append_prepared_skill_event_in_owner_transaction(
            handle, scope, event_payload, encoded_payload, skill_provenance_value,
            encoded_skill_provenance, skill_provenance_sha256, idempotency_key,
        )
        proof_after_write = revalidate_captured_prepared_event()
        if (proof_after_write != proof_before_write
                or proof_after_write._collector is not proof_before_write._collector):
            raise RollingSkillEventBridgeRejected("prepared_proof_changed_during_append")
        return result

    try:
        result = run_rolling_history_owner_write_operation_strict_memory_epoch(
            user, memory_epoch, operation,
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        raise RollingSkillEventAppendOutcomeUnknown(
            "Rolling Skill event append outcome is unknown; retry with the same idempotency key",
        ) from None
    if type(result) is not _OwnerSkillEventAppendResult:
        raise RollingSkillEventAppendOutcomeUnknown(
            "Rolling Skill event append outcome is unknown; retry with the same idempotency key",
        )
    if result.outcome == "conflict":
        raise ValueError("Conflicting rolling history retry")
    if result.outcome != "stored" or type(result.reference) is not RollingHistoryReference:
        raise ValueError("Rolling history event is unavailable")
    return result.reference


def read_rolling_history_event(
    scope: ContextEventScope,
    *,
    event_id: str | None = None,
    sequence: int | None = None,
    max_bytes: int | None = None,
) -> RollingHistoryEvent | None:
    """Read one complete event by ID or sequence and verify its content hash."""
    _require_bound_scope(scope)
    if ((event_id is None) == (sequence is None)
            or (event_id is not None and (type(event_id) is not str or _HASH.fullmatch(event_id) is None))
            or (sequence is not None and (type(sequence) is not int or sequence < 1))
            or (max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0))):
        raise ValueError("Invalid rolling history read")
    prefix = _scope_prefix(scope)
    with get_db_ctx() as db:
        db.execute(text("BEGIN"))
        _validate_scope(db, scope)
        if event_id is not None:
            id_row = db.get(Settings, _event_id_key(prefix, event_id))
            if id_row is None:
                return None
            try:
                id_value = json.loads(id_row.value)
            except Exception as exc:
                raise ValueError("Rolling history event ID is unavailable") from exc
            expected_keys = {
                "schema_version", "kind", "scope", "event_id", "request_id",
                "sequence", "content_sha256", "chain_sha256",
            }
            if (type(id_value) is not dict or set(id_value) != expected_keys
                    or type(id_value["schema_version"]) is not int or id_value["schema_version"] != _SCHEMA_VERSION
                    or id_value["kind"] != "event_id" or id_value["event_id"] != event_id
                    or not _scope_matches(id_value["scope"], scope)
                    or not _valid_request_id(id_value["request_id"])
                    or type(id_value["sequence"]) is not int or id_value["sequence"] < 1
                    or type(id_value["content_sha256"]) is not str or _HASH.fullmatch(id_value["content_sha256"]) is None
                    or type(id_value["chain_sha256"]) is not str or _HASH.fullmatch(id_value["chain_sha256"]) is None
                    or _canonical(id_value) != id_row.value):
                raise ValueError("Invalid rolling history event ID")
            sequence = id_value["sequence"]
        event = _load_event_at_sequence(db, scope, prefix, sequence)
        if event is None:
            return None
        if event_id is not None and event.reference.event_id != event_id:
            raise ValueError("Rolling history event ID does not match sequence")
        if max_bytes is not None and len(_canonical(event.model_payload()).encode("utf-8")) > max_bytes:
            raise ValueError("Complete rolling history event exceeds read budget")
        return event


def read_rolling_skill_event(
    reference: RollingHistoryReference,
    user: dict | None,
    origin,
) -> RollingHistoryEvent | None:
    """Read a complete Skill body and provenance for this authenticated owner.

    The caller must pass the authenticated route user, the active host scope
    origin, and the exact immutable reference returned by the append. Reads run
    through the owner-restricted transaction and return no legacy event without
    a verified provenance mapping. A reference can locate an earlier request's
    event only within the authenticated owner's current session.
    """
    from app.api.compaction_archive import get_compaction_scope
    from app.api.rolling_history_owner_scope import (
        OperationMode,
        RollingHistoryOwnerScopeError,
        ScopeOrigin,
        get_rolling_history_owner_identity,
        run_rolling_history_owner_operation,
    )
    from app.api.session_owner import principal_digest_for_user
    from app.api.tool_events import get_tool_event_scope

    if (type(reference) is not RollingHistoryReference
            or type(reference.event_id) is not str or _HASH.fullmatch(reference.event_id) is None
            or type(reference.session_id) is not str
            or type(reference.request_id) is not str or not _valid_request_id(reference.request_id)
            or type(reference.sequence) is not int or reference.sequence < 1
            or type(reference.version) is not int
            or reference.version not in (
                _SCHEMA_VERSION, _CHUNKED_EVENT_SCHEMA_VERSION,
                _BODY_BACKED_EVENT_SCHEMA_VERSION,
            )
            or type(reference.content_sha256) is not str
            or _HASH.fullmatch(reference.content_sha256) is None
            or type(reference.chain_sha256) is not str
            or _HASH.fullmatch(reference.chain_sha256) is None
            or type(origin) is not ScopeOrigin):
        raise ValueError("Invalid rolling Skill event reference")

    identity = get_rolling_history_owner_identity()
    try:
        principal_hash = principal_digest_for_user(user)
    except Exception:
        raise RollingHistoryOwnerScopeError(
            "Rolling history owner scope is unavailable",
        ) from None
    active_scope = (
        get_compaction_scope()
        if origin is ScopeOrigin.COMPACTION
        else get_tool_event_scope()
    )
    _require_bound_scope(active_scope)
    if (principal_hash != identity.principal_hash
            or (active_scope.session_id, active_scope.request_id,
                active_scope.principal_hash, active_scope.session_epoch)
            != (
                identity.session_id,
                identity.request_id,
                identity.principal_hash,
                identity.delete_epoch,
            )):
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    if reference.session_id != identity.session_id:
        return None

    scope = ContextEventScope(
        identity.session_id, reference.request_id,
        identity.principal_hash, identity.delete_epoch,
    )
    prefix = _scope_prefix(scope)

    def operation(handle):
        if get_rolling_history_owner_identity() != identity:
            raise RollingHistoryOwnerScopeError(
                "Rolling history owner scope is unavailable",
            )
        event = _owner_history_load_event_at_sequence(
            handle, scope, prefix, reference.sequence,
        )
        if (event is None or event.reference != reference
                or event.skill_provenance is None):
            return None
        return event

    return run_rolling_history_owner_operation(user, OperationMode.READ, operation)


def read_rolling_history_event_chunk(
    scope: ContextEventScope,
    *,
    event_id: str | None = None,
    cursor: RollingHistoryChunkCursor | None = None,
    max_bytes: int = 16384,
) -> RollingHistoryEventChunk | None:
    """Read one UTF-8-safe body fragment from a fully verified event version.

    The cursor contains only an event locator, hashes, and a byte offset. The
    active host scope always comes from ContextVar binding, never from the cursor.
    """
    _require_bound_scope(scope)
    if ((event_id is None) == (cursor is None)
            or (event_id is not None and (type(event_id) is not str or _HASH.fullmatch(event_id) is None))
            or (cursor is not None and (
                type(cursor) is not RollingHistoryChunkCursor
                or type(cursor.cursor_version) is not int or cursor.cursor_version != _SCHEMA_VERSION
                or type(cursor.event_id) is not str or _HASH.fullmatch(cursor.event_id) is None
                or type(cursor.payload_sha256) is not str or _HASH.fullmatch(cursor.payload_sha256) is None
                or type(cursor.text_sha256) is not str or _HASH.fullmatch(cursor.text_sha256) is None
                or type(cursor.chain_sha256) is not str or _HASH.fullmatch(cursor.chain_sha256) is None
                or type(cursor.next_offset) is not int or cursor.next_offset < 1
            ))
            or type(max_bytes) is not int or not 4 <= max_bytes <= 65536):
        raise ValueError("Invalid rolling history chunk read")

    requested_event_id = event_id if cursor is None else cursor.event_id
    prefix = _scope_prefix(scope)
    with get_db_ctx() as db:
        db.execute(text("BEGIN"))
        _require_bound_scope(scope)
        _validate_scope(db, scope)
        id_value = _event_id_mapping(db, scope, prefix, requested_event_id)
        if id_value is None:
            return None
        event = _load_event_at_sequence(db, scope, prefix, id_value["sequence"])
        if event is None:
            return None
        reference = event.reference
        if (reference.event_id != requested_event_id
                or id_value["request_id"] != reference.request_id
                or id_value["content_sha256"] != reference.content_sha256
                or id_value["chain_sha256"] != reference.chain_sha256):
            raise ValueError("Rolling history chunk event binding mismatch")

        raw_text = event.content.encode("utf-8")
        text_sha256 = _sha256(raw_text)
        if cursor is not None and (
            cursor.payload_sha256 != reference.content_sha256
            or cursor.text_sha256 != text_sha256
            or cursor.chain_sha256 != reference.chain_sha256
        ):
            raise ValueError("Rolling history chunk cursor belongs to a different event version")
        start = 0 if cursor is None else cursor.next_offset
        if (start > len(raw_text) or not _is_utf8_boundary(raw_text, start)
                or (cursor is not None and start == len(raw_text))):
            raise ValueError("Rolling history chunk cursor offset is invalid")
        end = _utf8_chunk_end(raw_text, start, max_bytes)
        content_chunk = raw_text[start:end].decode("utf-8")
        next_cursor = None
        if end < len(raw_text):
            next_cursor = RollingHistoryChunkCursor(
                cursor_version=_SCHEMA_VERSION,
                event_id=reference.event_id,
                payload_sha256=reference.content_sha256,
                text_sha256=text_sha256,
                chain_sha256=reference.chain_sha256,
                next_offset=end,
            )
        return RollingHistoryEventChunk(
            event_id=reference.event_id,
            event_type=event.event_type,
            content_chunk=content_chunk,
            start_offset=start,
            end_offset=end,
            payload_sha256=reference.content_sha256,
            text_sha256=text_sha256,
            chain_sha256=reference.chain_sha256,
            attachments=event.attachments,
            next_cursor=next_cursor,
        )


def read_rolling_history_event_chunk_in_transaction(
    handle,
    scope: ContextEventScope,
    session_id: str,
    *,
    event_id: str | None = None,
    cursor: RollingHistoryChunkCursor | None = None,
    max_rows: int = 3,
    max_bytes: int = 24576,
) -> RollingHistoryEventChunk | None:
    """Read one committed text chunk using only a restricted owner handle.

    The source-row budget is at most three Settings rows (ID mapping, manifest,
    and one chunk) and ``max_bytes`` of encoded row values. Legacy inline event
    rows are rejected after a bounded prefix read; this function never decodes
    their full value.
    """
    _require_bound_scope(scope)
    if (type(session_id) is not str or session_id != scope.session_id
            or type(max_rows) is not int or max_rows != 3
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= (
                _EVENT_ID_MAPPING_MAX_BYTES
                + _EVENT_MANIFEST_MAX_BYTES
                + _EVENT_CHUNK_ROW_MAX_BYTES
            )
            or ((event_id is None) == (cursor is None))
            or (event_id is not None and (
                type(event_id) is not str or _HASH.fullmatch(event_id) is None
            ))):
        raise ValueError("Invalid owner-bound rolling history chunk read")
    if cursor is not None:
        if (type(cursor) is not RollingHistoryChunkCursor
                or type(cursor.cursor_version) is not int
                or cursor.cursor_version != _CHUNKED_EVENT_SCHEMA_VERSION
                or cursor.session_id != session_id
                or type(cursor.sequence) is not int or cursor.sequence < 1
                or type(cursor.manifest_sha256) is not str
                or _HASH.fullmatch(cursor.manifest_sha256) is None
                or type(cursor.next_chunk_index) is not int or cursor.next_chunk_index < 0
                or type(cursor.expected_chunk_sha256) is not str
                or _HASH.fullmatch(cursor.expected_chunk_sha256) is None
                or type(cursor.next_offset) is not int or cursor.next_offset < 0):
            raise ValueError("Invalid owner-bound rolling history chunk cursor")

    requested_event_id = event_id if cursor is None else cursor.event_id
    prefix = _scope_prefix(scope)
    remaining = max_bytes
    id_raw, too_large = _owner_history_read_setting_bounded(
        handle, _event_id_key(prefix, requested_event_id),
        min(_EVENT_ID_MAPPING_MAX_BYTES, remaining),
    )
    if too_large:
        raise ValueError("Rolling history event ID mapping exceeds byte budget")
    if id_raw is None:
        return None
    remaining -= len(id_raw.encode("utf-8"))
    try:
        id_value = json.loads(id_raw)
    except Exception as exc:
        raise ValueError("Rolling history event ID is unavailable") from exc
    id_keys = {
        "schema_version", "kind", "scope", "event_id", "request_id",
        "sequence", "content_sha256", "chain_sha256",
    }
    if (type(id_value) is not dict or set(id_value) != id_keys
            or type(id_value["schema_version"]) is not int
            or id_value["schema_version"] not in (
                _SCHEMA_VERSION, _CHUNKED_EVENT_SCHEMA_VERSION,
                _BODY_BACKED_EVENT_SCHEMA_VERSION,
            )
            or id_value["kind"] != "event_id"
            or id_value["event_id"] != requested_event_id
            or not _scope_matches(id_value["scope"], scope)
            or not _valid_request_id(id_value["request_id"])
            or type(id_value["sequence"]) is not int or id_value["sequence"] < 1
            or type(id_value["content_sha256"]) is not str
            or _HASH.fullmatch(id_value["content_sha256"]) is None
            or type(id_value["chain_sha256"]) is not str
            or _HASH.fullmatch(id_value["chain_sha256"]) is None
            or _canonical(id_value) != id_raw):
        raise ValueError("Invalid rolling history event ID mapping")
    if id_value["schema_version"] == _SCHEMA_VERSION:
        raise RollingHistoryEventChunkMigrationRequired(
            RollingHistoryEventChunkMigrationRequired.code,
        )
    if id_value["schema_version"] == _BODY_BACKED_EVENT_SCHEMA_VERSION:
        raise ValueError("Body-backed events require the pinned revision reader")

    if cursor is not None and (
        cursor.event_id != requested_event_id
        or cursor.sequence != id_value["sequence"]
        or cursor.payload_sha256 != id_value["content_sha256"]
        or cursor.chain_sha256 != id_value["chain_sha256"]
    ):
        raise ValueError("Rolling history chunk cursor belongs to another event")
    event_raw, too_large = _owner_history_read_setting_bounded(
        handle, _event_manifest_key(prefix, requested_event_id),
        min(_EVENT_MANIFEST_MAX_BYTES, remaining),
    )
    if too_large:
        raise ValueError("Rolling history event manifest exceeds byte budget")
    if event_raw is None:
        raise RollingHistoryEventChunkMigrationRequired(
            RollingHistoryEventChunkMigrationRequired.code,
        )
    remaining -= len(event_raw.encode("utf-8"))
    try:
        event_header = json.loads(event_raw)
    except Exception as exc:
        raise ValueError("Rolling history event manifest is unavailable") from exc
    if type(event_header) is not dict or event_header.get("schema_version") != _CHUNKED_EVENT_SCHEMA_VERSION:
        raise RollingHistoryEventChunkMigrationRequired(
            RollingHistoryEventChunkMigrationRequired.code,
        )
    manifest = _validate_chunked_event_manifest(
        event_raw, scope, id_value["sequence"],
    )
    manifest_sha256 = _sha256(event_raw)
    if (manifest["event_id"] != requested_event_id
            or manifest["content_sha256"] != id_value["content_sha256"]
            or manifest["chain_sha256"] != id_value["chain_sha256"]
            or manifest["request_id"] != id_value["request_id"]):
        raise ValueError("Rolling history chunk manifest does not match its locator")
    if cursor is not None and cursor.manifest_sha256 != manifest_sha256:
        raise ValueError("Rolling history chunk cursor manifest changed")

    attachments = tuple(_parse_attachment(item) for item in manifest["attachments"])
    if manifest["chunk_count"] == 0:
        if (cursor is not None
                or manifest["text_sha256"] != _sha256(b"")
                or manifest["content_utf8_byte_length"] != 0):
            raise ValueError("Invalid empty rolling history event chunk manifest")
        payload, encoded_payload = _encode_event({
            "event_type": manifest["event_type"], "content": "",
            "attachments": manifest["attachments"],
        })
        if _sha256(encoded_payload) != manifest["payload_sha256"]:
            raise ValueError("Rolling history event payload commitment mismatch")
        return RollingHistoryEventChunk(
            event_id=requested_event_id,
            event_type=manifest["event_type"],
            content_chunk="",
            start_offset=0,
            end_offset=0,
            payload_sha256=manifest["content_sha256"],
            text_sha256=manifest["text_sha256"],
            chain_sha256=manifest["chain_sha256"],
            attachments=attachments,
            next_cursor=None,
        )

    chunk_index = 0 if cursor is None else cursor.next_chunk_index
    expected_sha256 = (
        manifest["chunk_root_sha256"]
        if cursor is None else cursor.expected_chunk_sha256
    )
    expected_offset = 0 if cursor is None else cursor.next_offset
    if chunk_index >= manifest["chunk_count"]:
        raise ValueError("Rolling history chunk cursor is past the manifest")
    chunk_raw, too_large = _owner_history_read_setting_bounded(
        handle,
        _event_chunk_key(prefix, requested_event_id, chunk_index),
        min(_EVENT_CHUNK_ROW_MAX_BYTES, remaining),
    )
    if too_large:
        raise ValueError("Rolling history event chunk exceeds byte budget")
    if chunk_raw is None:
        raise ValueError("Rolling history event chunk is unavailable")
    if len(chunk_raw.encode("utf-8")) > remaining:
        raise ValueError("Rolling history event chunk exceeds byte budget")
    content_chunk, next_sha256, start, end = _read_chunk_record(
        chunk_raw, scope,
        event_id=requested_event_id,
        sequence=manifest["sequence"],
        chunk_index=chunk_index,
        expected_sha256=expected_sha256,
        expected_offset=expected_offset,
    )
    next_cursor = None
    if chunk_index + 1 < manifest["chunk_count"]:
        if next_sha256 == _EVENT_CHUNK_EMPTY_ROOT:
            raise ValueError("Rolling history event chunk chain ended early")
        next_cursor = RollingHistoryChunkCursor(
            cursor_version=_CHUNKED_EVENT_SCHEMA_VERSION,
            event_id=requested_event_id,
            payload_sha256=manifest["content_sha256"],
            text_sha256=manifest["text_sha256"],
            chain_sha256=manifest["chain_sha256"],
            next_offset=end,
            session_id=session_id,
            sequence=manifest["sequence"],
            manifest_sha256=manifest_sha256,
            next_chunk_index=chunk_index + 1,
            expected_chunk_sha256=next_sha256,
        )
    elif (next_sha256 != _EVENT_CHUNK_EMPTY_ROOT
            or end != manifest["content_utf8_byte_length"]):
        raise ValueError("Rolling history event chunk chain does not reach its manifest")

    return RollingHistoryEventChunk(
        event_id=requested_event_id,
        event_type=manifest["event_type"],
        content_chunk=content_chunk,
        start_offset=start,
        end_offset=end,
        payload_sha256=manifest["content_sha256"],
        text_sha256=manifest["text_sha256"],
        chain_sha256=manifest["chain_sha256"],
        attachments=attachments,
        next_cursor=next_cursor,
    )


def page_rolling_history(
    scope: ContextEventScope,
    *,
    after_sequence: int = 0,
    limit: int = 50,
    query: str | None = None,
    event_types: Iterable[str] | None = None,
) -> RollingHistoryPage:
    """Return bounded previews/search results with a resumable sequence cursor."""
    _require_bound_scope(scope)
    if (type(after_sequence) is not int or after_sequence < 0
            or type(limit) is not int or not 1 <= limit <= _MAX_PAGE_SIZE
            or (query is not None and (type(query) is not str or len(query) > 256))):
        raise ValueError("Invalid rolling history page")
    accepted_types = None
    if event_types is not None:
        if type(event_types) not in (list, tuple, set, frozenset):
            raise ValueError("Invalid rolling history event filters")
        accepted_types = frozenset(event_types)
        if any(type(item) is not str or item not in _EVENT_TYPES for item in accepted_types):
            raise ValueError("Invalid rolling history event filters")

    prefix = _scope_prefix(scope)
    with get_db_ctx() as db:
        db.execute(text("BEGIN"))
        _validate_scope(db, scope)
        _history_head(db, prefix, scope)
        rows = db.query(Settings.key, Settings.value).filter(
            Settings.key.startswith(prefix + "event:", autoescape=True),
            Settings.key > _event_key(prefix, after_sequence),
        ).order_by(Settings.key.asc()).limit(_MAX_PAGE_SCAN).all()
        previews = []
        scanned_sequence = after_sequence
        folded_query = query.casefold() if query else None
        for row_index, (row_key, raw) in enumerate(rows):
            suffix = row_key[len(prefix + "event:"):]
            if not suffix.isdigit():
                raise ValueError("Invalid rolling history sequence key")
            sequence = int(suffix)
            event = _load_event_row(db, scope, prefix, raw, sequence)
            scanned_sequence = sequence
            if accepted_types is not None and event.event_type not in accepted_types:
                continue
            if folded_query is not None and folded_query not in event.content.casefold():
                continue
            previews.append(RollingHistoryPreview(
                event.reference.event_id,
                sequence,
                event.event_type,
                event.reference.content_sha256,
                event.content[:_PREVIEW_CHARS],
                bool(event.attachments),
            ))
            if len(previews) == limit:
                if row_index + 1 < len(rows) or len(rows) == _MAX_PAGE_SCAN:
                    return RollingHistoryPage(tuple(previews), sequence)
                return RollingHistoryPage(tuple(previews), None)
        next_cursor = scanned_sequence if len(rows) == _MAX_PAGE_SCAN else None
        return RollingHistoryPage(tuple(previews), next_cursor)


def _index_record_hash(value: dict) -> str:
    fields = {key: item for key, item in value.items() if key != "prepared_sha256"}
    return _sha256(_canonical(fields))


def _prepared_id(scope: ContextEventScope, idempotency_key: str) -> str:
    return _sha256(_canonical({
        "scope": _scope_digest(scope),
        "idempotency_key": idempotency_key,
    }))


def _source_commitment(
    db,
    scope: ContextEventScope,
    prefix: str,
    covered_through: int,
    retained_from: int,
) -> dict:
    if covered_through:
        stored_prefix = prefix + "event:"
        event_count = db.query(func.count(Settings.key)).filter(
            Settings.key.startswith(stored_prefix, autoescape=True),
            Settings.key <= _event_key(prefix, covered_through),
        ).scalar()
        if event_count != covered_through:
            raise ValueError("Rolling history index source coverage has a gap")
    if covered_through == 0:
        endpoint = None
        chain_sha256 = _genesis_chain(scope)
    else:
        event = _load_event_at_sequence(db, scope, prefix, covered_through)
        if event is None:
            raise ValueError("Rolling history index coverage is unavailable")
        endpoint = asdict(event.reference)
        chain_sha256 = event.reference.chain_sha256
    return {
        "covered_through": covered_through,
        "retained_from": retained_from,
        "chain_sha256": chain_sha256,
        "endpoint_reference": endpoint,
    }


def _decode_prepared_index(db, scope: ContextEventScope, prefix: str, raw: str) -> dict:
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError("Prepared rolling history index is unavailable") from exc
    required = {
        "schema_version", "kind", "scope", "request_id", "prepared_id", "revision",
        "idempotency_id", "covered_through", "retained_from", "summary",
        "summary_sha256", "source", "source_sha256", "prepared_sha256",
    }
    if (type(value) is not dict or set(value) != required
            or type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION
            or value["kind"] != "prepared_index" or not _scope_matches(value["scope"], scope)
            or not _valid_request_id(value["request_id"])
            or type(value["prepared_id"]) is not str or _HASH.fullmatch(value["prepared_id"]) is None
            or type(value["idempotency_id"]) is not str or _HASH.fullmatch(value["idempotency_id"]) is None
            or value["prepared_id"] != value["idempotency_id"]
            or type(value["revision"]) is not int or value["revision"] < 1
            or type(value["covered_through"]) is not int or value["covered_through"] < 0
            or type(value["retained_from"]) is not int or value["retained_from"] != value["covered_through"] + 1
            or type(value["summary"]) is not str
            or type(value["summary_sha256"]) is not str or _HASH.fullmatch(value["summary_sha256"]) is None
            or type(value["source"]) is not dict
            or type(value["source_sha256"]) is not str or _HASH.fullmatch(value["source_sha256"]) is None
            or type(value["prepared_sha256"]) is not str or _HASH.fullmatch(value["prepared_sha256"]) is None
            or _canonical(value) != raw):
        raise ValueError("Invalid prepared rolling history index")
    expected_source = _source_commitment(
        db, scope, prefix, value["covered_through"], value["retained_from"],
    )
    if (value["source"] != expected_source
            or _sha256(value["summary"]) != value["summary_sha256"]
            or _sha256(_canonical(value["source"])) != value["source_sha256"]
            or _index_record_hash(value) != value["prepared_sha256"]):
        raise ValueError("Prepared rolling history index digest mismatch")
    return value


def _prepared_result(value: dict) -> PreparedRollingHistoryIndex:
    return PreparedRollingHistoryIndex(
        prepared_id=value["prepared_id"],
        revision=value["revision"],
        covered_through=value["covered_through"],
        retained_from=value["retained_from"],
        summary_sha256=value["summary_sha256"],
        source_sha256=value["source_sha256"],
        prepared_sha256=value["prepared_sha256"],
    )


def prepare_rolling_history_index(
    scope: ContextEventScope,
    *,
    idempotency_key: str,
    covered_through: int,
    retained_from: int,
    summary: str,
) -> PreparedRollingHistoryIndex:
    """Persist a versioned prepared index with a fixed-size event-chain source."""
    _require_bound_scope(scope)
    if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)
            or type(covered_through) is not int or covered_through < 0
            or type(retained_from) is not int or retained_from != covered_through + 1
            or type(summary) is not str):
        raise ValueError("Invalid rolling history index preparation")
    prefix = _scope_prefix(scope)
    prepared_id = _prepared_id(scope, idempotency_key)
    index_key = prefix + "index:prepared:" + prepared_id
    summary_sha256 = _sha256(summary)

    with get_db_ctx() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        _validate_scope(db, scope)
        last_sequence, _ = _history_head(db, prefix, scope)
        if covered_through > last_sequence:
            raise ValueError("Rolling history index covers events that are unavailable")
        source = _source_commitment(db, scope, prefix, covered_through, retained_from)
        source_sha256 = _sha256(_canonical(source))
        existing = db.get(Settings, index_key)
        if existing is not None:
            value = _decode_prepared_index(db, scope, prefix, existing.value)
            if (value["covered_through"] != covered_through
                    or value["retained_from"] != retained_from
                    or value["summary"] != summary
                    or value["source"] != source):
                raise ValueError("Conflicting rolling history index retry")
            return _prepared_result(value)

        head_key = prefix + "index:meta:head"
        head_row = db.get(Settings, head_key)
        if head_row is None:
            revision = 1
        else:
            try:
                head = json.loads(head_row.value)
            except Exception as exc:
                raise ValueError("Rolling history index head is unavailable") from exc
            if (type(head) is not dict
                    or set(head) != {"schema_version", "kind", "scope", "revision"}
                    or type(head["schema_version"]) is not int or head["schema_version"] != _SCHEMA_VERSION
                    or head["kind"] != "index_head" or not _scope_matches(head["scope"], scope)
                    or type(head["revision"]) is not int or head["revision"] < 1
                    or _canonical(head) != head_row.value):
                raise ValueError("Invalid rolling history index head")
            revision = head["revision"] + 1
        value = {
            "schema_version": _SCHEMA_VERSION,
            "kind": "prepared_index",
            "scope": _identity(scope),
            "request_id": scope.request_id,
            "prepared_id": prepared_id,
            "revision": revision,
            "idempotency_id": prepared_id,
            "covered_through": covered_through,
            "retained_from": retained_from,
            "summary": summary,
            "summary_sha256": summary_sha256,
            "source": source,
            "source_sha256": source_sha256,
        }
        value["prepared_sha256"] = _index_record_hash(value)
        db.add(Settings(key=index_key, value=_canonical(value)))
        head_value = {
            "schema_version": _SCHEMA_VERSION,
            "kind": "index_head",
            "scope": _identity(scope),
            "revision": revision,
        }
        _set_row(db, head_key, _canonical(head_value))
        return _prepared_result(value)


# Rolling-history v2 is deliberately a value-model and validation foundation.
# It has no root-creation, persistence, deletion, or graph-publication API until
# the host can supply a verified owner/incarnation binding.
_REVISION_LEDGER_V2_SCHEMA_VERSION = 2
_REVISION_LEDGER_V3_SCHEMA_VERSION = 3
_ROLLING_HISTORY_V2_LOCATOR_INDEX_SCHEMA_VERSION = 1
_ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE = 128
_ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT = 16
_REVISION_LEDGER_V2_MAX_LINEAGE_DEPTH = 64
_REVISION_LEDGER_V2_OPERATIONS = frozenset({
    "root", "fork", "cut", "append", "append_body", "snapshot", "migration_append",
})
_REVISION_LEDGER_V2_REPRESENTATIONS = frozenset({"snapshot", "delta", "inherit"})

# Legacy SQL message rows use a separate source namespace. These values are
# intentionally not aliases for v1 events or rolling-history v2 locators.
_LEGACY_SOURCE_SCHEMA_VERSION = 1
_LEGACY_SOURCE_WIRE_PROFILE = "legacy-source-wire-json-v1"
_LEGACY_SOURCE_ORDERING_VERSION = "created_at_nulls_first_id_asc_v1"
_LEGACY_SOURCE_SNAPSHOT_CANONICAL_PROFILE = "legacy-snapshot-c14n-v1"
_LEGACY_SOURCE_SNAPSHOT_ENVELOPE_FORMAT = "legacy-message-snapshot"
_LEGACY_SOURCE_SNAPSHOT_ENVELOPE_VERSION = "legacy-message-envelope-v1"
_LEGACY_SOURCE_CHAIN_HASH_DOMAIN = b"legacy-message-chain-v1\0"
_LEGACY_SOURCE_GENERATION_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)
_LEGACY_SOURCE_UINT64_MAX = (1 << 64) - 1
_LEGACY_SOURCE_MAX_COUNT = _LEGACY_SOURCE_UINT64_MAX + 1
_LEGACY_SOURCE_MAX_REVISION = (1 << 63) - 1
_LEGACY_SOURCE_ZERO_HASH = "0" * 64
_LEGACY_SOURCE_CAPTURE_STATUSES = frozenset({"captured", "missing", "unavailable"})
_LEGACY_SOURCE_MODEL_AVAILABILITY = frozenset({
    "text_projection_available", "archive_only", "unavailable",
})
_LEGACY_SOURCE_COVERAGE_STATUSES = frozenset({
    "pending", "text_projection_available", "archive_only", "unavailable",
})


@dataclass(frozen=True)
class RollingHistoryV2EventLocator:
    """Locator-only reference to one immutable v1 event."""

    event_id: str
    request_id: str
    sequence: int
    version: int
    content_sha256: str
    chain_sha256: str


@dataclass(frozen=True, slots=True)
class LegacySourceLocatorV1:
    """Locator for one legacy SQL snapshot slot, not a v1/v2 event locator."""

    schema_version: int
    migration_id: str
    ordering_version: str
    source_message_revision: int
    sequence: int
    legacy_message_id: str
    row_sha256: str
    chain_sha256: str
    capture_status: str
    model_availability: str


@dataclass(frozen=True, slots=True)
class LegacySourcePayloadV1:
    """Untrusted row payload fields supplied beside a legacy source locator."""

    capture_reason: str | None
    canonical_envelope_json: str | None
    model_role: str | None
    model_content: str | None


@dataclass(frozen=True, slots=True)
class LegacySourceCoverageV1:
    """Incremental source cursor; status describes only the consumed prefix."""

    schema_version: int
    migration_id: str
    ordering_version: str
    source_message_revision: int
    total_count: int
    next_sequence: int
    previous_chain_sha256: str
    text_projection_count: int
    archive_only_count: int
    unavailable_count: int
    model_coverage_status: str


@dataclass(frozen=True)
class RollingHistoryV2LocatorIndex:
    """Committed root for the immutable, page-addressable visible locator set."""

    schema_version: int
    root_sha256: str
    locator_count: int
    page_count: int
    tree_height: int


@dataclass(frozen=True)
class RollingHistoryV2Revision:
    """Immutable branch revision bound to a source history sequence and chain hash."""

    schema_version: int
    scope_sha256: str
    branch_id: str
    revision_id: str
    revision_sha256: str
    revision_number: int
    history_revision: int
    history_head_sha256: str
    request_id: str
    idempotency_id: str
    idempotency_key_sha256: str
    operation: str
    representation: str
    parent_revision_id: str | None
    parent_revision_sha256: str | None
    lineage_depth: int
    event_locators: tuple[RollingHistoryV2EventLocator, ...]
    locator_index: RollingHistoryV2LocatorIndex | None = None
    lineage_index: RollingHistoryV2LocatorIndex | None = None
    parent_lineage_commitment_sha256: str | None = None
    lineage_root_sha256: str | None = None
    lineage_commitment_sha256: str | None = None


@dataclass(frozen=True)
class RollingHistoryV2Branch:
    """Immutable branch identity bound to its root or fork revision."""

    schema_version: int
    scope_sha256: str
    branch_id: str
    created_revision_id: str
    created_revision_sha256: str
    request_id: str
    idempotency_id: str
    idempotency_key_sha256: str
    branch_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2Head:
    """Immutable expected-head token suitable for a future transactional CAS."""

    schema_version: int
    scope_sha256: str
    branch_id: str
    revision_id: str
    revision_sha256: str
    revision_number: int
    history_revision: int
    history_head_sha256: str
    head_sha256: str


@dataclass(frozen=True)
class PreparedRollingHistoryV2Summary:
    """Prepared-only summary commitment bound to one exact history revision."""

    schema_version: int
    prepared_id: str
    scope_sha256: str
    branch_id: str
    request_id: str
    idempotency_id: str
    idempotency_key_sha256: str
    history_revision_id: str
    history_revision_sha256: str
    summary_sha256: str
    prepared_sha256: str


_ROLLING_HISTORY_V2_MIGRATION_BINDING_MINT = object()
_ROLLING_HISTORY_V2_MIGRATION_PAGE_MINT = object()
_ROLLING_HISTORY_V2_MIGRATION_PAGE_MAX_EVENTS = _MAX_PAGE_SIZE


@dataclass(frozen=True, init=False)
class RollingHistoryV2MigrationLiveFence:
    """Typed mutable-source boundary, separate from the immutable archive source."""

    message_source_generation: int
    message_count: int
    non_append_generation: int

    def __init__(self, *args, **kwargs):
        raise TypeError("Migration live fences are issued by the trusted host store")


@dataclass(frozen=True, init=False)
class RollingHistoryV2MigrationRootBinding:
    """Internal host token minted after the store revalidates its versioned root map."""

    migration_id: str
    canonical_namespace_digest: str
    scope_sha256: str
    mapping_sha256: str
    source_generation_sha256: str
    live_fence: RollingHistoryV2MigrationLiveFence
    branch_id: str
    root_revision_id: str
    root_revision_sha256: str
    root_history_revision: int
    root_history_head_sha256: str
    binding_sha256: str

    def __init__(self, *args, **kwargs):
        raise TypeError("Migration root bindings are issued by the trusted host store")


@dataclass(frozen=True, init=False)
class RollingHistoryV2MigrationSourceGeneration:
    """Current owner-bound legacy generation captured by the trusted host store."""

    migration_id: str
    canonical_namespace_digest: str
    session_id: str
    principal_hash: str
    delete_epoch: str
    incarnation_id: str
    source_generation_sha256: str

    def __init__(self, *args, **kwargs):
        raise TypeError("Migration source generations are issued by the trusted host store")


@dataclass(frozen=True, init=False)
class RollingHistoryV2MigrationCheckpoint:
    """Trusted continuation point for an already verified v1 event prefix."""

    schema_version: int
    migration_id: str
    root_binding_sha256: str
    source_generation_sha256: str
    canonical_namespace_digest: str
    live_fence: RollingHistoryV2MigrationLiveFence
    sequence: int
    chain_sha256: str
    parent_checkpoint_sha256: str | None
    checkpoint_sha256: str

    def __init__(self, *args, **kwargs):
        raise TypeError("Migration checkpoints are issued by the trusted host store")


@dataclass(frozen=True, init=False)
class RollingHistoryV2MigrationSourcePage:
    """One source-verified, bounded contiguous tail page for a migration host."""

    migration_id: str
    root_binding_sha256: str
    source_generation_sha256: str
    canonical_namespace_digest: str
    prior_checkpoint_sha256: str
    source_head_sequence: int
    source_head_chain_sha256: str
    start_sequence: int
    start_chain_sha256: str
    end_sequence: int
    end_chain_sha256: str
    event_locators: tuple[RollingHistoryV2EventLocator, ...]
    checkpoint: RollingHistoryV2MigrationCheckpoint
    has_more: bool
    page_sha256: str

    def __init__(self, *args, **kwargs):
        raise TypeError("Migration source pages are issued by the source verifier")


def _legacy_source_valid_generation_fields(
    migration_id: object,
    ordering_version: object,
    source_message_revision: object,
) -> None:
    if (type(migration_id) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(migration_id) is None
            or type(ordering_version) is not str
            or ordering_version != _LEGACY_SOURCE_ORDERING_VERSION
            or type(source_message_revision) is not int
            or not 0 <= source_message_revision <= _LEGACY_SOURCE_MAX_REVISION):
        raise ValueError("Invalid legacy source generation binding")


def _validate_legacy_source_locator_fields(locator: object) -> None:
    if (type(locator) is not LegacySourceLocatorV1
            or type(locator.schema_version) is not int
            or locator.schema_version != _LEGACY_SOURCE_SCHEMA_VERSION):
        raise ValueError("Invalid legacy source locator version")
    _legacy_source_valid_generation_fields(
        locator.migration_id,
        locator.ordering_version,
        locator.source_message_revision,
    )
    if (type(locator.sequence) is not int
            or not 0 <= locator.sequence <= _LEGACY_SOURCE_UINT64_MAX
            or type(locator.legacy_message_id) is not str
            or type(locator.row_sha256) is not str
            or _HASH.fullmatch(locator.row_sha256) is None
            or type(locator.chain_sha256) is not str
            or _HASH.fullmatch(locator.chain_sha256) is None
            or locator.row_sha256 == locator.chain_sha256
            or type(locator.capture_status) is not str
            or locator.capture_status not in _LEGACY_SOURCE_CAPTURE_STATUSES
            or type(locator.model_availability) is not str
            or locator.model_availability not in _LEGACY_SOURCE_MODEL_AVAILABILITY):
        raise ValueError("Invalid legacy source locator fields")
    if ((locator.capture_status == "captured"
         and locator.model_availability not in ("text_projection_available", "archive_only"))
            or (locator.capture_status != "captured"
                and locator.model_availability != "unavailable")):
        raise ValueError("Legacy source capture and model status disagree")


def _legacy_source_unique_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Legacy source envelope has duplicate object keys")
        result[key] = value
    return result


def _legacy_source_reject_json_constant(value: str) -> object:
    raise ValueError("Legacy source envelope contains a non-JSON constant")


def _legacy_source_decode_text_node(node: object, label: str) -> str:
    if (type(node) is not list or len(node) != 2 or node[0] != "text"
            or type(node[1]) is not str
            or re.fullmatch(r"(?:[0-9a-f]{6})*", node[1], re.ASCII) is None):
        raise ValueError(f"Legacy source envelope {label} text node is invalid")
    codepoints: list[str] = []
    for index in range(0, len(node[1]), 6):
        codepoint = int(node[1][index:index + 6], 16)
        if codepoint > 0x10FFFF:
            raise ValueError(f"Legacy source envelope {label} code point is out of range")
        codepoints.append(chr(codepoint))
    return "".join(codepoints)


def _legacy_source_envelope_text(
    canonical_envelope_json: object,
    legacy_message_id: str,
) -> tuple[str, str]:
    if type(canonical_envelope_json) is not str or not canonical_envelope_json:
        raise ValueError("Captured legacy source requires a canonical envelope")
    try:
        canonical_envelope_json.encode("ascii", errors="strict")
        envelope = json.loads(
            canonical_envelope_json,
            object_pairs_hook=_legacy_source_unique_object_pairs,
            parse_constant=_legacy_source_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("Legacy source envelope is not unambiguous ASCII JSON") from exc
    if (type(envelope) is not dict
            or set(envelope) != {"canonical_profile", "fields", "format", "version"}
            or envelope.get("canonical_profile") != _LEGACY_SOURCE_SNAPSHOT_CANONICAL_PROFILE
            or envelope.get("format") != _LEGACY_SOURCE_SNAPSHOT_ENVELOPE_FORMAT
            or envelope.get("version") != _LEGACY_SOURCE_SNAPSHOT_ENVELOPE_VERSION):
        raise ValueError("Legacy source envelope profile or shape is unsupported")
    fields = envelope.get("fields")
    if (type(fields) is not dict
            or set(fields) != {
                "activity", "content", "created_at", "id", "role", "session_id",
                "sources", "tool_calls",
            }):
        raise ValueError("Legacy source envelope does not contain the complete row shape")
    envelope_id = _legacy_source_decode_text_node(fields["id"], "id")
    role = _legacy_source_decode_text_node(fields["role"], "role")
    content = _legacy_source_decode_text_node(fields["content"], "content")
    if envelope_id != legacy_message_id:
        raise ValueError("Legacy source envelope ID does not match its locator")
    return role, content


def validate_legacy_source_locator_v1(
    locator: LegacySourceLocatorV1,
    payload: LegacySourcePayloadV1,
) -> LegacySourceLocatorV1:
    """Validate source locator/payload shape without claiming source authenticity."""
    _validate_legacy_source_locator_fields(locator)
    if type(payload) is not LegacySourcePayloadV1:
        raise ValueError("Invalid legacy source payload")
    if locator.capture_status == "captured":
        if payload.capture_reason is not None:
            raise ValueError("Captured legacy source cannot have a capture reason")
        envelope_role, envelope_content = _legacy_source_envelope_text(
            payload.canonical_envelope_json,
            locator.legacy_message_id,
        )
        if locator.model_availability == "text_projection_available":
            if (type(payload.model_role) is not str
                    or payload.model_role not in ("user", "assistant")
                    or type(payload.model_content) is not str
                    or payload.model_role != envelope_role
                    or payload.model_content != envelope_content):
                raise ValueError("Recoverable legacy text must match its captured envelope body")
            try:
                payload.model_content.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise ValueError("Recoverable legacy text is not strict UTF-8") from exc
        elif payload.model_role is not None or payload.model_content is not None:
            raise ValueError("Archive-only legacy source cannot carry a model projection")
    else:
        if (type(payload.capture_reason) is not str
                or not payload.capture_reason.strip()
                or payload.canonical_envelope_json is not None
                or payload.model_role is not None
                or payload.model_content is not None):
            raise ValueError("Unavailable legacy source requires only a capture reason")
    return locator


def _legacy_source_coverage_status(
    *,
    total_count: int,
    next_sequence: int,
    archive_only_count: int,
    unavailable_count: int,
) -> str:
    if unavailable_count:
        return "unavailable"
    if archive_only_count:
        return "archive_only"
    if next_sequence == 0 and total_count > 0:
        return "pending"
    return "text_projection_available"


def _validate_legacy_source_coverage_v1(coverage: object) -> None:
    if (type(coverage) is not LegacySourceCoverageV1
            or type(coverage.schema_version) is not int
            or coverage.schema_version != _LEGACY_SOURCE_SCHEMA_VERSION):
        raise ValueError("Invalid legacy source coverage version")
    _legacy_source_valid_generation_fields(
        coverage.migration_id,
        coverage.ordering_version,
        coverage.source_message_revision,
    )
    counts = (
        coverage.total_count,
        coverage.next_sequence,
        coverage.text_projection_count,
        coverage.archive_only_count,
        coverage.unavailable_count,
    )
    if (any(type(value) is not int for value in counts)
            or not 0 <= coverage.total_count <= _LEGACY_SOURCE_MAX_COUNT
            or not 0 <= coverage.next_sequence <= coverage.total_count
            or min(
                coverage.text_projection_count,
                coverage.archive_only_count,
                coverage.unavailable_count,
            ) < 0
            or coverage.text_projection_count
            + coverage.archive_only_count
            + coverage.unavailable_count != coverage.next_sequence
            or type(coverage.previous_chain_sha256) is not str
            or _HASH.fullmatch(coverage.previous_chain_sha256) is None
            or (coverage.next_sequence == 0
                and coverage.previous_chain_sha256 != _LEGACY_SOURCE_ZERO_HASH)
            or type(coverage.model_coverage_status) is not str
            or coverage.model_coverage_status not in _LEGACY_SOURCE_COVERAGE_STATUSES):
        raise ValueError("Invalid legacy source coverage cursor or hashes")
    expected_status = _legacy_source_coverage_status(
        total_count=coverage.total_count,
        next_sequence=coverage.next_sequence,
        archive_only_count=coverage.archive_only_count,
        unavailable_count=coverage.unavailable_count,
    )
    if coverage.model_coverage_status != expected_status:
        raise ValueError("Legacy source coverage status does not match its prefix")


def new_legacy_source_coverage_v1(
    *,
    migration_id: str,
    ordering_version: str,
    source_message_revision: int,
    total_count: int,
) -> LegacySourceCoverageV1:
    """Create the zero-cursor descriptor for one versioned source generation."""
    coverage = LegacySourceCoverageV1(
        schema_version=_LEGACY_SOURCE_SCHEMA_VERSION,
        migration_id=migration_id,
        ordering_version=ordering_version,
        source_message_revision=source_message_revision,
        total_count=total_count,
        next_sequence=0,
        previous_chain_sha256=_LEGACY_SOURCE_ZERO_HASH,
        text_projection_count=0,
        archive_only_count=0,
        unavailable_count=0,
        model_coverage_status=_legacy_source_coverage_status(
            total_count=total_count,
            next_sequence=0,
            archive_only_count=0,
            unavailable_count=0,
        ),
    )
    _validate_legacy_source_coverage_v1(coverage)
    return coverage


def legacy_source_chain_sha256_v1(
    *,
    sequence: int,
    previous_chain_sha256: str,
    row_sha256: str,
) -> str:
    """Compute the codec's v1 row-chain framing without importing the codec."""
    if (type(sequence) is not int
            or not 0 <= sequence <= _LEGACY_SOURCE_UINT64_MAX
            or type(previous_chain_sha256) is not str
            or _HASH.fullmatch(previous_chain_sha256) is None
            or type(row_sha256) is not str
            or _HASH.fullmatch(row_sha256) is None
            or (sequence == 0 and previous_chain_sha256 != _LEGACY_SOURCE_ZERO_HASH)):
        raise ValueError("Invalid legacy source chain inputs")
    return hashlib.sha256(
        _LEGACY_SOURCE_CHAIN_HASH_DOMAIN
        + bytes.fromhex(previous_chain_sha256)
        + sequence.to_bytes(8, byteorder="big", signed=False)
        + bytes.fromhex(row_sha256)
    ).hexdigest()


def advance_legacy_source_coverage_v1(
    coverage: LegacySourceCoverageV1,
    locator: LegacySourceLocatorV1,
    payload: LegacySourcePayloadV1,
) -> LegacySourceCoverageV1:
    """Advance exactly one row; unavailable rows permanently mark the prefix."""
    _validate_legacy_source_coverage_v1(coverage)
    validate_legacy_source_locator_v1(locator, payload)
    if (locator.migration_id != coverage.migration_id
            or locator.ordering_version != coverage.ordering_version
            or locator.source_message_revision != coverage.source_message_revision):
        raise ValueError("Legacy source locator belongs to another generation")
    if locator.sequence != coverage.next_sequence:
        raise ValueError("Legacy source locator is not the next zero-based sequence")
    if coverage.next_sequence >= coverage.total_count:
        raise ValueError("Legacy source coverage is already at its declared total")
    if (locator.row_sha256 == coverage.previous_chain_sha256
            or locator.chain_sha256 == coverage.previous_chain_sha256):
        raise ValueError("Legacy source coverage repeats a prior hash")
    expected_chain = legacy_source_chain_sha256_v1(
        sequence=locator.sequence,
        previous_chain_sha256=coverage.previous_chain_sha256,
        row_sha256=locator.row_sha256,
    )
    if locator.chain_sha256 != expected_chain:
        raise ValueError("Legacy source chain hash mismatch")

    text_count = coverage.text_projection_count
    archive_count = coverage.archive_only_count
    unavailable_count = coverage.unavailable_count
    if locator.model_availability == "text_projection_available":
        text_count += 1
    elif locator.model_availability == "archive_only":
        archive_count += 1
    else:
        unavailable_count += 1
    next_sequence = coverage.next_sequence + 1
    advanced = LegacySourceCoverageV1(
        schema_version=_LEGACY_SOURCE_SCHEMA_VERSION,
        migration_id=coverage.migration_id,
        ordering_version=coverage.ordering_version,
        source_message_revision=coverage.source_message_revision,
        total_count=coverage.total_count,
        next_sequence=next_sequence,
        previous_chain_sha256=locator.chain_sha256,
        text_projection_count=text_count,
        archive_only_count=archive_count,
        unavailable_count=unavailable_count,
        model_coverage_status=_legacy_source_coverage_status(
            total_count=coverage.total_count,
            next_sequence=next_sequence,
            archive_only_count=archive_count,
            unavailable_count=unavailable_count,
        ),
    )
    _validate_legacy_source_coverage_v1(advanced)
    return advanced


def _legacy_source_wire_json(value: dict[str, object]) -> str:
    """Encode the fixed-order ASCII wire profile; this is not JCS."""
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Legacy source wire value is not serializable") from exc


def canonical_legacy_source_locator_json_v1(locator: LegacySourceLocatorV1) -> str:
    """Serialize locator metadata with the fixed-order legacy source wire v1 profile."""
    _validate_legacy_source_locator_fields(locator)
    return _legacy_source_wire_json({
        "wire_profile": _LEGACY_SOURCE_WIRE_PROFILE,
        "kind": "locator",
        "schema_version": locator.schema_version,
        "migration_id": locator.migration_id,
        "ordering_version": locator.ordering_version,
        "source_message_revision": locator.source_message_revision,
        "sequence": locator.sequence,
        "legacy_message_id": locator.legacy_message_id,
        "row_sha256": locator.row_sha256,
        "chain_sha256": locator.chain_sha256,
        "capture_status": locator.capture_status,
        "model_availability": locator.model_availability,
    })


def canonical_legacy_source_coverage_json_v1(coverage: LegacySourceCoverageV1) -> str:
    """Serialize the generation-bound cursor with the fixed-order wire v1 profile."""
    _validate_legacy_source_coverage_v1(coverage)
    return _legacy_source_wire_json({
        "wire_profile": _LEGACY_SOURCE_WIRE_PROFILE,
        "kind": "coverage",
        "schema_version": coverage.schema_version,
        "migration_id": coverage.migration_id,
        "ordering_version": coverage.ordering_version,
        "source_message_revision": coverage.source_message_revision,
        "total_count": coverage.total_count,
        "next_sequence": coverage.next_sequence,
        "previous_chain_sha256": coverage.previous_chain_sha256,
        "text_projection_count": coverage.text_projection_count,
        "archive_only_count": coverage.archive_only_count,
        "unavailable_count": coverage.unavailable_count,
        "model_coverage_status": coverage.model_coverage_status,
    })


def _rolling_history_v2_idempotency_id(
    scope_sha256: str,
    request_id: str,
    idempotency_key_sha256: str,
) -> str:
    if (type(scope_sha256) is not str or _HASH.fullmatch(scope_sha256) is None
            or type(request_id) is not str or not _valid_request_id(request_id)
            or type(idempotency_key_sha256) is not str
            or _HASH.fullmatch(idempotency_key_sha256) is None):
        raise ValueError("Invalid rolling history v2 idempotency binding")
    return _sha256(_canonical({
        "schema_version": _REVISION_LEDGER_V2_SCHEMA_VERSION,
        "scope_sha256": scope_sha256,
        "request_id": request_id,
        "idempotency_key_sha256": idempotency_key_sha256,
    }))


def _rolling_history_v2_locator_tuple(value) -> tuple[RollingHistoryV2EventLocator, ...]:
    if type(value) is not tuple:
        raise ValueError("Invalid rolling history v2 locator list")
    previous_sequence = 0
    seen_event_ids = set()
    for locator in value:
        if (type(locator) is not RollingHistoryV2EventLocator
                or type(locator.event_id) is not str or _HASH.fullmatch(locator.event_id) is None
                or type(locator.request_id) is not str or not _valid_request_id(locator.request_id)
                or type(locator.sequence) is not int or locator.sequence <= previous_sequence
                or type(locator.version) is not int
                or locator.version not in (
                    _SCHEMA_VERSION, _CHUNKED_EVENT_SCHEMA_VERSION,
                    _BODY_BACKED_EVENT_SCHEMA_VERSION,
                )
                or type(locator.content_sha256) is not str or _HASH.fullmatch(locator.content_sha256) is None
                or type(locator.chain_sha256) is not str or _HASH.fullmatch(locator.chain_sha256) is None
                or locator.event_id in seen_event_ids):
            raise ValueError("Invalid rolling history v2 event locator")
        previous_sequence = locator.sequence
        seen_event_ids.add(locator.event_id)
    return value


def _rolling_history_v2_locator_index_envelope(
    index: RollingHistoryV2LocatorIndex,
) -> dict:
    return {
        "schema_version": index.schema_version,
        "root_sha256": index.root_sha256,
        "locator_count": index.locator_count,
        "page_count": index.page_count,
        "tree_height": index.tree_height,
    }


def _rolling_history_v2_empty_locator_index_root() -> str:
    return _sha256(_canonical({
        "schema_version": _ROLLING_HISTORY_V2_LOCATOR_INDEX_SCHEMA_VERSION,
        "kind": "rolling_history_v2_locator_index_empty",
    }))


def _validate_rolling_history_v2_locator_index(index: object) -> None:
    if (type(index) is not RollingHistoryV2LocatorIndex
            or type(index.schema_version) is not int
            or index.schema_version != _ROLLING_HISTORY_V2_LOCATOR_INDEX_SCHEMA_VERSION
            or type(index.root_sha256) is not str or _HASH.fullmatch(index.root_sha256) is None
            or type(index.locator_count) is not int or index.locator_count < 0
            or type(index.page_count) is not int or index.page_count < 0
            or type(index.tree_height) is not int or index.tree_height < 0
            or index.page_count != (
                index.locator_count + _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE - 1
            ) // _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE):
        raise ValueError("Invalid rolling history v2 locator index")
    if index.page_count == 0:
        if index.root_sha256 != _rolling_history_v2_empty_locator_index_root() or index.tree_height != 0:
            raise ValueError("Invalid empty rolling history v2 locator index")
        return
    capacity = _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** (index.tree_height + 1)
    minimum_capacity = _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** index.tree_height
    if (index.page_count > capacity
            or (index.tree_height > 0 and index.page_count <= minimum_capacity)):
        raise ValueError("Noncanonical rolling history v2 locator index height")


def _rolling_history_v2_lineage_root_sha256(
    scope_sha256: str,
    branch_id: str,
) -> str:
    return _sha256(_canonical({
        "schema_version": _REVISION_LEDGER_V3_SCHEMA_VERSION,
        "kind": "rolling_history_v2_branch_lineage_root",
        "scope_sha256": scope_sha256,
        "branch_id": branch_id,
    }))


def _rolling_history_v2_lineage_commitment_body(revision: RollingHistoryV2Revision) -> dict:
    return {
        "schema_version": revision.schema_version,
        "scope_sha256": revision.scope_sha256,
        "branch_id": revision.branch_id,
        "lineage_root_sha256": revision.lineage_root_sha256,
        "parent_revision_id": revision.parent_revision_id,
        "parent_revision_sha256": revision.parent_revision_sha256,
        "parent_lineage_commitment_sha256": revision.parent_lineage_commitment_sha256,
        "revision_number": revision.revision_number,
        "request_id": revision.request_id,
        "idempotency_id": revision.idempotency_id,
        "idempotency_key_sha256": revision.idempotency_key_sha256,
        "operation": revision.operation,
        "representation": revision.representation,
        "history_revision": revision.history_revision,
        "history_head_sha256": revision.history_head_sha256,
        "locator_index": _rolling_history_v2_locator_index_envelope(revision.locator_index),
    }


def _rolling_history_v2_lineage_commitment_sha256(
    revision: RollingHistoryV2Revision,
) -> str:
    return _sha256(_canonical(_rolling_history_v2_lineage_commitment_body(revision)))


def _rolling_history_v2_revision_envelope(revision: RollingHistoryV2Revision) -> dict:
    envelope = {
        "schema_version": revision.schema_version,
        "scope_sha256": revision.scope_sha256,
        "branch_id": revision.branch_id,
        "revision_id": revision.revision_id,
        "revision_number": revision.revision_number,
        "history_revision": revision.history_revision,
        "history_head_sha256": revision.history_head_sha256,
        "request_id": revision.request_id,
        "idempotency_id": revision.idempotency_id,
        "idempotency_key_sha256": revision.idempotency_key_sha256,
        "operation": revision.operation,
        "representation": revision.representation,
        "parent_revision_id": revision.parent_revision_id,
        "parent_revision_sha256": revision.parent_revision_sha256,
        "lineage_depth": revision.lineage_depth,
    }
    if revision.schema_version == _REVISION_LEDGER_V3_SCHEMA_VERSION:
        _validate_rolling_history_v2_locator_index(revision.locator_index)
        envelope["locator_index"] = _rolling_history_v2_locator_index_envelope(
            revision.locator_index,
        )
        _validate_rolling_history_v2_locator_index(revision.lineage_index)
        envelope["lineage_index"] = _rolling_history_v2_locator_index_envelope(
            revision.lineage_index,
        )
        envelope["parent_lineage_commitment_sha256"] = revision.parent_lineage_commitment_sha256
        envelope["lineage_root_sha256"] = revision.lineage_root_sha256
        envelope["lineage_commitment_sha256"] = revision.lineage_commitment_sha256
    else:
        envelope["event_locators"] = [asdict(item) for item in revision.event_locators]
    return envelope


def _validate_indexed_rolling_history_v2_revision(
    revision: RollingHistoryV2Revision,
) -> None:
    if (type(revision.scope_sha256) is not str or _HASH.fullmatch(revision.scope_sha256) is None
            or type(revision.branch_id) is not str or _HASH.fullmatch(revision.branch_id) is None
            or type(revision.revision_id) is not str or _HASH.fullmatch(revision.revision_id) is None
            or type(revision.revision_sha256) is not str or _HASH.fullmatch(revision.revision_sha256) is None
            or type(revision.revision_number) is not int or revision.revision_number < 1
            or type(revision.history_revision) is not int or revision.history_revision < 0
            or type(revision.history_head_sha256) is not str
            or _HASH.fullmatch(revision.history_head_sha256) is None
            or type(revision.request_id) is not str or not _valid_request_id(revision.request_id)
            or type(revision.idempotency_id) is not str or _HASH.fullmatch(revision.idempotency_id) is None
            or type(revision.idempotency_key_sha256) is not str
            or _HASH.fullmatch(revision.idempotency_key_sha256) is None
            or type(revision.operation) is not str
            or revision.operation not in _REVISION_LEDGER_V2_OPERATIONS
            or type(revision.representation) is not str
            or revision.representation not in _REVISION_LEDGER_V2_REPRESENTATIONS
            or type(revision.lineage_depth) is not int or revision.lineage_depth != 0
            or type(revision.event_locators) is not tuple or revision.event_locators
            or revision.locator_index is None
            or type(revision.parent_lineage_commitment_sha256) not in (str, type(None))
            or type(revision.lineage_root_sha256) is not str
            or _HASH.fullmatch(revision.lineage_root_sha256) is None
            or type(revision.lineage_commitment_sha256) is not str
            or _HASH.fullmatch(revision.lineage_commitment_sha256) is None
            or revision.lineage_index is None):
        raise ValueError("Invalid indexed rolling history v2 revision")
    _validate_rolling_history_v2_locator_index(revision.locator_index)
    _validate_rolling_history_v2_locator_index(revision.lineage_index)
    if revision.lineage_index.locator_count != revision.revision_number:
        raise ValueError("Indexed rolling history v2 lineage index count mismatch")
    if revision.idempotency_id != _rolling_history_v2_idempotency_id(
        revision.scope_sha256, revision.request_id,
        revision.idempotency_key_sha256,
    ):
        raise ValueError("Rolling history v2 request idempotency binding mismatch")
    has_parent = revision.parent_revision_id is not None
    if has_parent != (revision.parent_revision_sha256 is not None):
        raise ValueError("Invalid rolling history v2 parent binding")
    if has_parent and (
        type(revision.parent_revision_id) is not str
        or _HASH.fullmatch(revision.parent_revision_id) is None
        or type(revision.parent_revision_sha256) is not str
        or _HASH.fullmatch(revision.parent_revision_sha256) is None
    ):
        raise ValueError("Invalid rolling history v2 parent binding")
    expected_lineage_root = _rolling_history_v2_lineage_root_sha256(
        revision.scope_sha256, revision.branch_id,
    )
    if (revision.lineage_root_sha256 != expected_lineage_root
            or (has_parent and (
                type(revision.parent_lineage_commitment_sha256) is not str
                or _HASH.fullmatch(revision.parent_lineage_commitment_sha256) is None
            ))
            or (not has_parent and revision.parent_lineage_commitment_sha256 is not None)
            or revision.lineage_commitment_sha256
            != _rolling_history_v2_lineage_commitment_sha256(revision)):
        raise ValueError("Indexed rolling history v2 lineage commitment mismatch")
    if revision.operation == "root":
        if (has_parent or revision.revision_number != 1 or revision.representation != "snapshot"
                or revision.locator_index.locator_count != 0):
            raise ValueError("Invalid indexed rolling history v2 root")
    elif revision.operation == "fork":
        if (not has_parent or revision.revision_number != 1
                or revision.representation not in ("inherit", "snapshot")):
            raise ValueError("Invalid indexed rolling history v2 fork")
    elif revision.operation == "cut":
        if not has_parent or revision.representation != "snapshot":
            raise ValueError("Invalid indexed rolling history v2 cut")
    elif revision.operation in ("append", "append_body", "migration_append"):
        if not has_parent or revision.locator_index.locator_count == 0:
            raise ValueError("Invalid indexed rolling history v2 append")
    elif revision.operation == "snapshot" and not has_parent:
        raise ValueError("Invalid indexed rolling history v2 snapshot")

    envelope = _rolling_history_v2_revision_envelope(revision)
    identity_fields = {key: value for key, value in envelope.items() if key != "revision_id"}
    if (revision.revision_id != _sha256(_canonical(identity_fields))
            or revision.revision_sha256 != _sha256(_canonical(envelope))):
        raise ValueError("Indexed rolling history v2 revision hash mismatch")


def _validate_rolling_history_v2_revision(revision: RollingHistoryV2Revision) -> None:
    if (type(revision) is RollingHistoryV2Revision
            and type(revision.schema_version) is int
            and revision.schema_version == _REVISION_LEDGER_V3_SCHEMA_VERSION):
        _validate_indexed_rolling_history_v2_revision(revision)
        return
    if (type(revision) is not RollingHistoryV2Revision
            or type(revision.schema_version) is not int
            or revision.schema_version != _REVISION_LEDGER_V2_SCHEMA_VERSION
            or revision.locator_index is not None
            or type(revision.scope_sha256) is not str or _HASH.fullmatch(revision.scope_sha256) is None
            or type(revision.branch_id) is not str or _HASH.fullmatch(revision.branch_id) is None
            or type(revision.revision_id) is not str or _HASH.fullmatch(revision.revision_id) is None
            or type(revision.revision_sha256) is not str or _HASH.fullmatch(revision.revision_sha256) is None
            or type(revision.revision_number) is not int or revision.revision_number < 1
            or type(revision.history_revision) is not int or revision.history_revision < 0
            or type(revision.history_head_sha256) is not str
            or _HASH.fullmatch(revision.history_head_sha256) is None
            or type(revision.request_id) is not str or not _valid_request_id(revision.request_id)
            or type(revision.idempotency_id) is not str or _HASH.fullmatch(revision.idempotency_id) is None
            or type(revision.idempotency_key_sha256) is not str
            or _HASH.fullmatch(revision.idempotency_key_sha256) is None
            or type(revision.operation) is not str or revision.operation not in _REVISION_LEDGER_V2_OPERATIONS
            or type(revision.representation) is not str
            or revision.representation not in _REVISION_LEDGER_V2_REPRESENTATIONS
            or type(revision.lineage_depth) is not int
            or not 0 <= revision.lineage_depth <= _REVISION_LEDGER_V2_MAX_LINEAGE_DEPTH):
        raise ValueError("Invalid rolling history v2 revision")
    if revision.idempotency_id != _rolling_history_v2_idempotency_id(
        revision.scope_sha256, revision.request_id,
        revision.idempotency_key_sha256,
    ):
        raise ValueError("Rolling history v2 request idempotency binding mismatch")
    has_parent_id = revision.parent_revision_id is not None
    has_parent_hash = revision.parent_revision_sha256 is not None
    if has_parent_id != has_parent_hash:
        raise ValueError("Invalid rolling history v2 parent binding")
    if has_parent_id and (
        type(revision.parent_revision_id) is not str
        or _HASH.fullmatch(revision.parent_revision_id) is None
        or type(revision.parent_revision_sha256) is not str
        or _HASH.fullmatch(revision.parent_revision_sha256) is None
    ):
        raise ValueError("Invalid rolling history v2 parent binding")
    locators = _rolling_history_v2_locator_tuple(revision.event_locators)
    if revision.operation == "root":
        if (has_parent_id or revision.revision_number != 1 or revision.lineage_depth != 0
                or revision.representation != "snapshot" or locators):
            raise ValueError("Invalid rolling history v2 root shape")
    elif revision.operation == "fork":
        if (not has_parent_id or revision.revision_number != 1
                or revision.representation not in ("inherit", "snapshot")
                or (revision.representation == "inherit" and locators)
                or (revision.representation == "inherit" and revision.lineage_depth == 0)
                or (revision.representation == "snapshot" and revision.lineage_depth != 0)):
            raise ValueError("Invalid rolling history v2 fork shape")
    elif revision.operation == "cut":
        if not has_parent_id or revision.representation != "snapshot" or revision.lineage_depth != 0:
            raise ValueError("Invalid rolling history v2 cut shape")
    elif revision.operation == "append":
        if (not has_parent_id or revision.representation not in ("delta", "snapshot") or not locators
                or (revision.representation == "delta" and revision.lineage_depth == 0)
                or (revision.representation == "snapshot" and revision.lineage_depth != 0)):
            raise ValueError("Invalid rolling history v2 append shape")
    elif revision.operation == "migration_append":
        if (not has_parent_id or revision.representation != "delta"
                or not locators or revision.lineage_depth != 0):
            raise ValueError("Invalid rolling history v2 migration append shape")
    elif revision.operation == "snapshot":
        if not has_parent_id or revision.representation != "snapshot" or revision.lineage_depth != 0:
            raise ValueError("Invalid rolling history v2 snapshot shape")

    envelope = _rolling_history_v2_revision_envelope(revision)
    identity_fields = {key: value for key, value in envelope.items() if key != "revision_id"}
    if (revision.revision_id != _sha256(_canonical(identity_fields))
            or revision.revision_sha256 != _sha256(_canonical(envelope))):
        raise ValueError("Rolling history v2 revision hash mismatch")


def _append_rolling_history_v2_locators(
    visible: tuple[RollingHistoryV2EventLocator, ...],
    appended: tuple[RollingHistoryV2EventLocator, ...],
) -> tuple[RollingHistoryV2EventLocator, ...]:
    _rolling_history_v2_locator_tuple(visible)
    _rolling_history_v2_locator_tuple(appended)
    if not appended or (visible and appended[0].sequence <= visible[-1].sequence):
        raise ValueError("Rolling history v2 append is not after the visible prefix")
    visible_ids = {item.event_id for item in visible}
    if any(item.event_id in visible_ids for item in appended):
        raise ValueError("Rolling history v2 append repeats a visible event")
    return visible + appended


def _append_rolling_history_v2_revision_events(
    visible: tuple[RollingHistoryV2EventLocator, ...],
    appended: tuple[RollingHistoryV2EventLocator, ...],
    parent: RollingHistoryV2Revision,
    revision: RollingHistoryV2Revision,
) -> tuple[RollingHistoryV2EventLocator, ...]:
    if any(
        locator.sequence <= parent.history_revision
        or locator.sequence > revision.history_revision
        or locator.request_id != revision.request_id
        for locator in appended
    ):
        raise ValueError("Rolling history v2 append is outside its bound history revision")
    return _append_rolling_history_v2_locators(visible, appended)


def _append_rolling_history_v2_migration_lineage_locators(
    visible: tuple[RollingHistoryV2EventLocator, ...],
    appended: tuple[RollingHistoryV2EventLocator, ...],
    parent: RollingHistoryV2Revision,
    revision: RollingHistoryV2Revision,
) -> tuple[RollingHistoryV2EventLocator, ...]:
    """Validate a migration revision's contiguous source range during full reads."""
    _rolling_history_v2_locator_tuple(visible)
    _rolling_history_v2_locator_tuple(appended)
    if (revision.operation != "migration_append"
            or not appended
            or revision.history_revision - parent.history_revision != len(appended)
            or any(locator.sequence != parent.history_revision + offset
                   for offset, locator in enumerate(appended, start=1))
            or appended[-1].chain_sha256 != revision.history_head_sha256):
        raise ValueError("Rolling history v2 migration append is not a contiguous source range")
    return _append_rolling_history_v2_locators(visible, appended)


def _validate_rolling_history_v2_lineage(
    revisions: tuple[RollingHistoryV2Revision, ...],
) -> tuple[RollingHistoryV2EventLocator, ...]:
    """Resolve only the visible prefix represented by a complete revision chain."""
    if type(revisions) is not tuple or not revisions:
        raise ValueError("Rolling history v2 lineage is unavailable")
    visible: tuple[RollingHistoryV2EventLocator, ...] = ()
    previous = None
    seen_revision_ids = set()
    seen_idempotency_ids = set()
    for revision in revisions:
        _validate_rolling_history_v2_revision(revision)
        if revision.revision_id in seen_revision_ids or revision.idempotency_id in seen_idempotency_ids:
            raise ValueError("Rolling history v2 lineage contains a duplicate revision or request")
        seen_revision_ids.add(revision.revision_id)
        seen_idempotency_ids.add(revision.idempotency_id)
        if previous is None:
            if revision.operation != "root":
                raise ValueError("Rolling history v2 lineage must start at its authorized root")
            previous = revision
            continue

        if (revision.scope_sha256 != previous.scope_sha256
                or revision.parent_revision_id != previous.revision_id
                or revision.parent_revision_sha256 != previous.revision_sha256
                or revision.history_revision < previous.history_revision
                or (revision.history_revision == previous.history_revision
                    and revision.history_head_sha256 != previous.history_head_sha256)):
            raise ValueError("Rolling history v2 lineage binding mismatch")
        if (revision.history_revision > previous.history_revision
                and revision.history_head_sha256 == previous.history_head_sha256):
            raise ValueError("Rolling history v2 history head did not advance with its sequence")
        if revision.operation == "root":
            raise ValueError("Rolling history v2 lineage contains a second root")
        if revision.operation == "fork":
            if revision.branch_id == previous.branch_id:
                raise ValueError("Rolling history v2 fork must create a distinct branch")
            if revision.representation == "inherit":
                if revision.lineage_depth != previous.lineage_depth + 1:
                    raise ValueError("Invalid rolling history v2 fork depth")
            elif revision.event_locators != visible:
                raise ValueError("Rolling history v2 fork snapshot is not the parent prefix")
        elif revision.operation == "migration_append":
            if (revision.branch_id != previous.branch_id
                    or revision.revision_number != previous.revision_number + 1):
                raise ValueError("Rolling history v2 migration branch revision conflict")
        else:
            if (revision.branch_id != previous.branch_id
                    or revision.revision_number != previous.revision_number + 1):
                raise ValueError("Rolling history v2 branch revision conflict")

        if revision.operation == "migration_append":
            visible = _append_rolling_history_v2_migration_lineage_locators(
                visible, revision.event_locators, previous, revision,
            )
        elif revision.representation == "snapshot":
            if revision.lineage_depth != 0:
                raise ValueError("Rolling history v2 snapshot did not reset lineage depth")
            if revision.operation == "cut":
                if revision.event_locators != visible[:len(revision.event_locators)]:
                    raise ValueError("Rolling history v2 cut exposes events outside its parent prefix")
                visible = revision.event_locators
            elif revision.operation in ("fork", "snapshot"):
                if revision.event_locators != visible:
                    raise ValueError("Rolling history v2 snapshot does not match its visible parent")
                visible = revision.event_locators
            elif revision.operation == "append":
                if (len(revision.event_locators) <= len(visible)
                        or revision.event_locators[:len(visible)] != visible):
                    raise ValueError("Rolling history v2 flattened append changed its parent prefix")
                _append_rolling_history_v2_revision_events(
                    visible, revision.event_locators[len(visible):], previous, revision,
                )
                visible = revision.event_locators
        elif revision.representation == "delta":
            if (revision.lineage_depth != previous.lineage_depth + 1
                    or previous.lineage_depth >= _REVISION_LEDGER_V2_MAX_LINEAGE_DEPTH):
                raise ValueError("Rolling history v2 delta lineage depth exceeded")
            visible = _append_rolling_history_v2_revision_events(
                visible, revision.event_locators, previous, revision,
            )
        elif revision.representation == "inherit":
            if (revision.lineage_depth != previous.lineage_depth + 1
                    or previous.lineage_depth >= _REVISION_LEDGER_V2_MAX_LINEAGE_DEPTH):
                raise ValueError("Rolling history v2 inherited lineage depth exceeded")
        previous = revision
    return visible


def _validate_rolling_history_v2_migration_live_fence(
    live_fence: object,
) -> None:
    if (type(live_fence) is not RollingHistoryV2MigrationLiveFence
            or type(live_fence.message_source_generation) is not int
            or live_fence.message_source_generation < 0
            or type(live_fence.message_count) is not int
            or live_fence.message_count < 0
            or type(live_fence.non_append_generation) is not int
            or live_fence.non_append_generation < 0):
        raise ValueError("Invalid rolling history v2 migration live fence")


def _new_rolling_history_v2_migration_live_fence(
    message_source_generation: object,
    message_count: object,
    non_append_generation: object,
) -> RollingHistoryV2MigrationLiveFence:
    live_fence = object.__new__(RollingHistoryV2MigrationLiveFence)
    object.__setattr__(live_fence, "message_source_generation", message_source_generation)
    object.__setattr__(live_fence, "message_count", message_count)
    object.__setattr__(live_fence, "non_append_generation", non_append_generation)
    _validate_rolling_history_v2_migration_live_fence(live_fence)
    return live_fence


def _rolling_history_v2_migration_live_fence_value(
    live_fence: RollingHistoryV2MigrationLiveFence,
) -> dict:
    _validate_rolling_history_v2_migration_live_fence(live_fence)
    return {
        "message_source_generation": live_fence.message_source_generation,
        "message_count": live_fence.message_count,
        "non_append_generation": live_fence.non_append_generation,
    }


def _parse_rolling_history_v2_migration_live_fence(
    raw: object,
) -> RollingHistoryV2MigrationLiveFence:
    required = {
        "message_source_generation", "message_count", "non_append_generation",
    }
    if type(raw) is not dict or set(raw) != required:
        raise ValueError("Invalid persisted rolling history v2 migration live fence")
    return _new_rolling_history_v2_migration_live_fence(
        raw["message_source_generation"], raw["message_count"],
        raw["non_append_generation"],
    )


def _validate_rolling_history_v2_migration_live_append(
    baseline: RollingHistoryV2MigrationLiveFence,
    current: RollingHistoryV2MigrationLiveFence,
) -> None:
    _validate_rolling_history_v2_migration_live_fence(baseline)
    _validate_rolling_history_v2_migration_live_fence(current)
    if current.non_append_generation != baseline.non_append_generation:
        raise ValueError("Rolling history v2 migration source was modified or deleted")
    generation_delta = (
        current.message_source_generation - baseline.message_source_generation
    )
    count_delta = current.message_count - baseline.message_count
    if generation_delta < 0 or count_delta < 0 or generation_delta != count_delta:
        raise ValueError("Rolling history v2 migration live source is not append-only")


def _rolling_history_v2_migration_root_binding_body(
    binding: RollingHistoryV2MigrationRootBinding,
) -> dict:
    return {
        "migration_id": binding.migration_id,
        "canonical_namespace_digest": binding.canonical_namespace_digest,
        "scope_sha256": binding.scope_sha256,
        "mapping_sha256": binding.mapping_sha256,
        "source_generation_sha256": binding.source_generation_sha256,
        "live_fence": _rolling_history_v2_migration_live_fence_value(
            binding.live_fence,
        ),
        "branch_id": binding.branch_id,
        "root_revision_id": binding.root_revision_id,
        "root_revision_sha256": binding.root_revision_sha256,
        "root_history_revision": binding.root_history_revision,
        "root_history_head_sha256": binding.root_history_head_sha256,
    }


def _validate_rolling_history_v2_migration_root_binding(
    binding: object,
) -> None:
    if (type(binding) is not RollingHistoryV2MigrationRootBinding
            or type(binding.migration_id) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(binding.migration_id) is None
            or type(binding.canonical_namespace_digest) is not str
            or _HASH.fullmatch(binding.canonical_namespace_digest) is None
            or type(binding.scope_sha256) is not str
            or _HASH.fullmatch(binding.scope_sha256) is None
            or type(binding.mapping_sha256) is not str
            or _HASH.fullmatch(binding.mapping_sha256) is None
            or type(binding.source_generation_sha256) is not str
            or _HASH.fullmatch(binding.source_generation_sha256) is None
            or type(binding.live_fence) is not RollingHistoryV2MigrationLiveFence
            or type(binding.branch_id) is not str
            or _HASH.fullmatch(binding.branch_id) is None
            or type(binding.root_revision_id) is not str
            or _HASH.fullmatch(binding.root_revision_id) is None
            or type(binding.root_revision_sha256) is not str
            or _HASH.fullmatch(binding.root_revision_sha256) is None
            or type(binding.root_history_revision) is not int
            or binding.root_history_revision != 0
            or type(binding.root_history_head_sha256) is not str
            or _HASH.fullmatch(binding.root_history_head_sha256) is None
            or type(binding.binding_sha256) is not str
            or binding.binding_sha256 != _sha256(_canonical(
                _rolling_history_v2_migration_root_binding_body(binding),
            ))):
        raise ValueError("Invalid rolling history v2 migration root binding")
    _validate_rolling_history_v2_migration_live_fence(binding.live_fence)


def _validate_rolling_history_v2_migration_source_generation(
    generation: object,
) -> None:
    if (type(generation) is not RollingHistoryV2MigrationSourceGeneration
            or type(generation.migration_id) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(generation.migration_id) is None
            or type(generation.canonical_namespace_digest) is not str
            or _HASH.fullmatch(generation.canonical_namespace_digest) is None
            or type(generation.session_id) is not str or not generation.session_id
            or type(generation.principal_hash) is not str
            or _HASH.fullmatch(generation.principal_hash) is None
            or type(generation.delete_epoch) is not str
            or (generation.delete_epoch != ""
                and _REQUEST_ID.fullmatch(generation.delete_epoch) is None)
            or type(generation.incarnation_id) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(generation.incarnation_id) is None
            or type(generation.source_generation_sha256) is not str
            or _HASH.fullmatch(generation.source_generation_sha256) is None):
        raise ValueError("Invalid rolling history v2 migration source generation")


def _issue_rolling_history_v2_migration_host_value(
    value_type: type,
    fields: dict,
    mint: object,
) -> object:
    if mint not in (
        _ROLLING_HISTORY_V2_MIGRATION_BINDING_MINT,
        _ROLLING_HISTORY_V2_MIGRATION_PAGE_MINT,
    ):
        raise TypeError("Migration host values are internal")
    value = object.__new__(value_type)
    for field_name, field_value in fields.items():
        object.__setattr__(value, field_name, field_value)
    return value


def _bind_current_rolling_history_v2_migration_source_generation(
    scope: ContextEventScope,
    source_binding: object,
) -> RollingHistoryV2MigrationSourceGeneration:
    """Mint a typed source token from the host's current, owner-bound read.

    The caller must obtain ``source_binding`` from the legacy source reader in
    the same owner transaction. This function checks its exact structure and
    current owner fields; it does not query or authenticate the legacy tables.
    """
    _require_bound_scope(scope)
    from app.api.rolling_history_owner_scope import get_rolling_history_owner_identity

    identity = get_rolling_history_owner_identity()
    required = {
        "canonical_namespace_digest", "generation", "owner_delete_epoch",
        "owner_incarnation_id", "owner_principal_hash",
    }
    generation_keys = {
        "capture_chain_hash", "capture_cursor", "coverage_status", "migration_id",
        "ordering_version", "publication_status", "session_id",
        "source_message_revision", "total_count",
    }
    if (type(source_binding) is not dict or set(source_binding) != required
            or type(source_binding.get("generation")) is not dict
            or set(source_binding["generation"]) != generation_keys):
        raise ValueError("Invalid current rolling history v2 migration source binding")
    generation_value = source_binding["generation"]
    _legacy_source_valid_generation_fields(
        generation_value["migration_id"], generation_value["ordering_version"],
        generation_value["source_message_revision"],
    )
    if (type(generation_value["capture_chain_hash"]) is not str
            or _HASH.fullmatch(generation_value["capture_chain_hash"]) is None
            or type(generation_value["capture_cursor"]) is not int
            or generation_value["capture_cursor"] < 0
            or generation_value["coverage_status"] != "complete"
            or generation_value["publication_status"] != "complete"
            or type(generation_value["session_id"]) is not str
            or not generation_value["session_id"]
            or type(generation_value["total_count"]) is not int
            or not 0 <= generation_value["total_count"] <= _LEGACY_SOURCE_MAX_COUNT
            or generation_value["capture_cursor"] != generation_value["total_count"]
            or (generation_value["total_count"] == 0
                and generation_value["capture_chain_hash"] != _LEGACY_SOURCE_ZERO_HASH)):
        raise ValueError("Invalid current rolling history v2 migration generation")
    owner_fields = (
        source_binding["canonical_namespace_digest"],
        generation_value["session_id"],
        source_binding["owner_principal_hash"],
        source_binding["owner_delete_epoch"],
        source_binding["owner_incarnation_id"],
    )
    if (type(owner_fields[0]) is not str or _HASH.fullmatch(owner_fields[0]) is None
            or type(owner_fields[1]) is not str
            or type(owner_fields[2]) is not str or _HASH.fullmatch(owner_fields[2]) is None
            or type(owner_fields[3]) is not str
            or (owner_fields[3] != "" and _REQUEST_ID.fullmatch(owner_fields[3]) is None)
            or type(owner_fields[4]) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(owner_fields[4]) is None
            or owner_fields != (
                identity.canonical_namespace_digest,
                identity.session_id,
                identity.principal_hash,
                identity.delete_epoch,
                identity.incarnation_id,
            )
            or (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
            != (identity.session_id, identity.request_id, identity.principal_hash, identity.delete_epoch)):
        raise ValueError("Migration source generation is outside the current owner scope")

    token = RollingHistoryV2MigrationSourceGeneration.__new__(
        RollingHistoryV2MigrationSourceGeneration,
    )
    fields = {
        "migration_id": generation_value["migration_id"],
        "canonical_namespace_digest": owner_fields[0],
        "session_id": owner_fields[1],
        "principal_hash": owner_fields[2],
        "delete_epoch": owner_fields[3],
        "incarnation_id": owner_fields[4],
        "source_generation_sha256": _sha256(_canonical(source_binding)),
    }
    for field_name, field_value in fields.items():
        object.__setattr__(token, field_name, field_value)
    _validate_rolling_history_v2_migration_source_generation(token)
    return token


def _bind_verified_rolling_history_v2_migration_root(
    *,
    scope: ContextEventScope,
    migration_id: str,
    mapping_sha256: str,
    live_fence: RollingHistoryV2MigrationLiveFence,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
    branch: RollingHistoryV2Branch,
    revision: RollingHistoryV2Revision,
    head: RollingHistoryV2Head,
) -> RollingHistoryV2MigrationRootBinding:
    """Mint a migration root binding after the store verifies its durable map.

    The caller must re-read and fully validate the immutable migration-root
    mapping, source binding, root branch, revision, head and idempotency record
    in this same owner transaction before calling this function. The map itself
    is store-owned and is deliberately not treated as verified here.
    """
    _require_bound_scope(scope)
    from app.api.rolling_history_owner_scope import get_rolling_history_owner_identity

    identity = get_rolling_history_owner_identity()
    _validate_rolling_history_v2_migration_source_generation(source_generation)
    _validate_rolling_history_v2_migration_live_fence(live_fence)
    if (type(migration_id) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(migration_id) is None
            or migration_id != source_generation.migration_id
            or type(mapping_sha256) is not str or _HASH.fullmatch(mapping_sha256) is None
            or source_generation.canonical_namespace_digest != identity.canonical_namespace_digest
            or source_generation.session_id != identity.session_id
            or source_generation.principal_hash != identity.principal_hash
            or source_generation.delete_epoch != identity.delete_epoch
            or source_generation.incarnation_id != identity.incarnation_id):
        raise ValueError("Migration root binding is outside the current owner generation")
    _validate_rolling_history_v2_branch(branch, revision)
    _validate_rolling_history_v2_revision(revision)
    _validate_rolling_history_v2_head(head, revision)
    if (branch.branch_id != revision.branch_id
            or revision.operation != "root"
            or revision.scope_sha256 != _scope_digest(scope)
            or revision.history_revision != 0
            or revision.history_head_sha256 != _genesis_chain(scope)
            or revision.event_locators
            or head.history_revision != 0
            or head.history_head_sha256 != _genesis_chain(scope)):
        raise ValueError("Verified migration root does not identify the v2 genesis")

    fields = {
        "migration_id": migration_id,
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "scope_sha256": _scope_digest(scope),
        "mapping_sha256": mapping_sha256,
        "source_generation_sha256": source_generation.source_generation_sha256,
        "live_fence": live_fence,
        "branch_id": branch.branch_id,
        "root_revision_id": revision.revision_id,
        "root_revision_sha256": revision.revision_sha256,
        "root_history_revision": 0,
        "root_history_head_sha256": _genesis_chain(scope),
    }
    binding = _issue_rolling_history_v2_migration_host_value(
        RollingHistoryV2MigrationRootBinding,
        fields,
        _ROLLING_HISTORY_V2_MIGRATION_BINDING_MINT,
    )
    object.__setattr__(
        binding,
        "binding_sha256",
        _sha256(_canonical(_rolling_history_v2_migration_root_binding_body(binding))),
    )
    _validate_rolling_history_v2_migration_root_binding(binding)
    return binding


def _rolling_history_v2_migration_checkpoint_body(
    checkpoint: RollingHistoryV2MigrationCheckpoint,
) -> dict:
    return {
        "schema_version": checkpoint.schema_version,
        "migration_id": checkpoint.migration_id,
        "root_binding_sha256": checkpoint.root_binding_sha256,
        "source_generation_sha256": checkpoint.source_generation_sha256,
        "canonical_namespace_digest": checkpoint.canonical_namespace_digest,
        "live_fence": _rolling_history_v2_migration_live_fence_value(
            checkpoint.live_fence,
        ),
        "sequence": checkpoint.sequence,
        "chain_sha256": checkpoint.chain_sha256,
        "parent_checkpoint_sha256": checkpoint.parent_checkpoint_sha256,
    }


def _new_rolling_history_v2_migration_checkpoint(
    *,
    root_binding: RollingHistoryV2MigrationRootBinding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
    live_fence: RollingHistoryV2MigrationLiveFence,
    sequence: int,
    chain_sha256: str,
    parent_checkpoint_sha256: str | None,
) -> RollingHistoryV2MigrationCheckpoint:
    fields = {
        "schema_version": 3,
        "migration_id": root_binding.migration_id,
        "root_binding_sha256": root_binding.binding_sha256,
        "source_generation_sha256": source_generation.source_generation_sha256,
        "canonical_namespace_digest": root_binding.canonical_namespace_digest,
        "live_fence": live_fence,
        "sequence": sequence,
        "chain_sha256": chain_sha256,
        "parent_checkpoint_sha256": parent_checkpoint_sha256,
    }
    checkpoint_body = {
        **fields,
        "live_fence": _rolling_history_v2_migration_live_fence_value(live_fence),
    }
    fields["checkpoint_sha256"] = _sha256(_canonical(checkpoint_body))
    return _issue_rolling_history_v2_migration_host_value(
        RollingHistoryV2MigrationCheckpoint,
        fields,
        _ROLLING_HISTORY_V2_MIGRATION_BINDING_MINT,
    )


def _bind_persisted_rolling_history_v2_migration_checkpoint(
    *,
    scope: ContextEventScope,
    root_binding: RollingHistoryV2MigrationRootBinding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
    persisted_value: object,
) -> RollingHistoryV2MigrationCheckpoint:
    """Reconstitute an internally typed checkpoint read by the trusted store.

    The host store must read this value from its immutable owner-scoped key in
    the same transaction, after revalidating the root map and current source
    generation. The input is a persisted internal value, never request data.
    """
    _validate_rolling_history_v2_migration_owner_binding(
        scope, root_binding, source_generation,
    )
    required = {
        "schema_version", "migration_id", "root_binding_sha256",
        "source_generation_sha256", "canonical_namespace_digest", "sequence",
        "live_fence", "chain_sha256", "parent_checkpoint_sha256",
        "checkpoint_sha256",
    }
    if type(persisted_value) is not dict or set(persisted_value) != required:
        raise ValueError("Invalid persisted rolling history v2 migration checkpoint")
    persisted_fields = dict(persisted_value)
    persisted_fields["live_fence"] = _parse_rolling_history_v2_migration_live_fence(
        persisted_value["live_fence"],
    )
    checkpoint = _issue_rolling_history_v2_migration_host_value(
        RollingHistoryV2MigrationCheckpoint,
        persisted_fields,
        _ROLLING_HISTORY_V2_MIGRATION_BINDING_MINT,
    )
    _validate_rolling_history_v2_migration_checkpoint(
        checkpoint, root_binding, source_generation,
    )
    return checkpoint


def _validate_rolling_history_v2_migration_checkpoint(
    checkpoint: object,
    root_binding: RollingHistoryV2MigrationRootBinding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
) -> None:
    if (type(checkpoint) is not RollingHistoryV2MigrationCheckpoint
            or type(checkpoint.schema_version) is not int or checkpoint.schema_version != 3
            or type(checkpoint.migration_id) is not str
            or _LEGACY_SOURCE_GENERATION_ID.fullmatch(checkpoint.migration_id) is None
            or checkpoint.migration_id != root_binding.migration_id
            or type(checkpoint.root_binding_sha256) is not str
            or _HASH.fullmatch(checkpoint.root_binding_sha256) is None
            or checkpoint.root_binding_sha256 != root_binding.binding_sha256
            or type(checkpoint.source_generation_sha256) is not str
            or _HASH.fullmatch(checkpoint.source_generation_sha256) is None
            or checkpoint.source_generation_sha256 != source_generation.source_generation_sha256
            or type(checkpoint.canonical_namespace_digest) is not str
            or _HASH.fullmatch(checkpoint.canonical_namespace_digest) is None
            or checkpoint.canonical_namespace_digest != root_binding.canonical_namespace_digest
            or type(checkpoint.live_fence) is not RollingHistoryV2MigrationLiveFence
            or type(checkpoint.sequence) is not int
            or checkpoint.sequence < root_binding.root_history_revision
            or type(checkpoint.chain_sha256) is not str
            or _HASH.fullmatch(checkpoint.chain_sha256) is None
            or (checkpoint.parent_checkpoint_sha256 is not None
                and (type(checkpoint.parent_checkpoint_sha256) is not str
                     or _HASH.fullmatch(checkpoint.parent_checkpoint_sha256) is None))
            or (checkpoint.parent_checkpoint_sha256 is None
                and (checkpoint.sequence != root_binding.root_history_revision
                     or checkpoint.chain_sha256 != root_binding.root_history_head_sha256
                     or checkpoint.live_fence != root_binding.live_fence))
            or (checkpoint.parent_checkpoint_sha256 is not None
                and checkpoint.sequence < root_binding.root_history_revision)
            or type(checkpoint.checkpoint_sha256) is not str
            or checkpoint.checkpoint_sha256 != _sha256(_canonical(
                _rolling_history_v2_migration_checkpoint_body(checkpoint),
            ))):
        raise ValueError("Invalid rolling history v2 migration checkpoint")
    _validate_rolling_history_v2_migration_live_fence(checkpoint.live_fence)
    _validate_rolling_history_v2_migration_live_append(
        root_binding.live_fence, checkpoint.live_fence,
    )


def new_rolling_history_v2_migration_checkpoint(
    *,
    scope: ContextEventScope,
    root_binding: RollingHistoryV2MigrationRootBinding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
) -> RollingHistoryV2MigrationCheckpoint:
    """Start at the verified migration root's v1 history endpoint (sequence 0)."""
    _validate_rolling_history_v2_migration_owner_binding(
        scope, root_binding, source_generation,
    )
    checkpoint = _new_rolling_history_v2_migration_checkpoint(
        root_binding=root_binding,
        source_generation=source_generation,
        live_fence=root_binding.live_fence,
        sequence=root_binding.root_history_revision,
        chain_sha256=root_binding.root_history_head_sha256,
        parent_checkpoint_sha256=None,
    )
    _validate_rolling_history_v2_migration_checkpoint(
        checkpoint, root_binding, source_generation,
    )
    return checkpoint


def _validate_rolling_history_v2_migration_owner_binding(
    scope: ContextEventScope,
    root_binding: RollingHistoryV2MigrationRootBinding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
) -> None:
    _require_bound_scope(scope)
    from app.api.rolling_history_owner_scope import get_rolling_history_owner_identity

    identity = get_rolling_history_owner_identity()
    _validate_rolling_history_v2_migration_root_binding(root_binding)
    _validate_rolling_history_v2_migration_source_generation(source_generation)
    if (root_binding.migration_id != source_generation.migration_id
            or root_binding.canonical_namespace_digest != identity.canonical_namespace_digest
            or root_binding.canonical_namespace_digest != source_generation.canonical_namespace_digest
            or root_binding.scope_sha256 != _scope_digest(scope)
            or root_binding.source_generation_sha256 != source_generation.source_generation_sha256
            or (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
            != (identity.session_id, identity.request_id, identity.principal_hash, identity.delete_epoch)
            or (source_generation.session_id, source_generation.principal_hash,
                source_generation.delete_epoch, source_generation.incarnation_id)
            != (identity.session_id, identity.principal_hash,
                identity.delete_epoch, identity.incarnation_id)):
        raise ValueError("Rolling history v2 migration binding changed owner or source generation")


def _rolling_history_v2_migration_source_head(
    handle,
    scope: ContextEventScope,
) -> tuple[int, str]:
    """Read the persistent head and endpoint with indexed, constant-count reads."""
    prefix = _scope_prefix(scope)
    raw_head = _owner_history_read_setting(handle, prefix + "meta:head")
    if raw_head is None:
        sequence = 0
        chain_sha256 = _genesis_chain(scope)
    else:
        try:
            value = json.loads(raw_head)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Rolling history migration source head is unavailable") from exc
        if (type(value) is not dict
                or set(value) != {"schema_version", "kind", "scope", "last_sequence", "chain_sha256"}
                or type(value["schema_version"]) is not int
                or value["schema_version"] != _SCHEMA_VERSION
                or value["kind"] != "history_head"
                or not _scope_matches(value["scope"], scope)
                or type(value["last_sequence"]) is not int or value["last_sequence"] < 0
                or type(value["chain_sha256"]) is not str
                or _HASH.fullmatch(value["chain_sha256"]) is None
                or _canonical(value) != raw_head):
            raise ValueError("Invalid rolling history migration source head")
        sequence = value["last_sequence"]
        chain_sha256 = value["chain_sha256"]

    if sequence == 0:
        if chain_sha256 != _genesis_chain(scope):
            raise ValueError("Rolling history migration source genesis mismatch")
    else:
        event = _owner_history_load_event_at_sequence(
            handle, scope, prefix, sequence, verify_parent=True,
        )
        if event is None or event.reference.chain_sha256 != chain_sha256:
            raise ValueError("Rolling history migration source head endpoint mismatch")

    later_rows = _owner_history_rows(
        handle,
        "SELECT key FROM settings WHERE key > ? AND key < ? ORDER BY key LIMIT 1",
        (_event_key(prefix, sequence), prefix + "event;"),
        ("key",),
    )
    if later_rows:
        later_key = later_rows[0][0]
        suffix = later_key[len(prefix + "event:"):] if (
            type(later_key) is str and later_key.startswith(prefix + "event:")
        ) else ""
        if (not suffix.isdigit()
                or _format_sequence(int(suffix)) != suffix
                or int(suffix) <= sequence):
            raise ValueError("Invalid rolling history migration source event key")
        raise ValueError("Rolling history migration source contains rows beyond its persistent head")
    return sequence, chain_sha256


def _rolling_history_v2_migration_source_page_body(
    page: RollingHistoryV2MigrationSourcePage,
) -> dict:
    return {
        "migration_id": page.migration_id,
        "root_binding_sha256": page.root_binding_sha256,
        "source_generation_sha256": page.source_generation_sha256,
        "canonical_namespace_digest": page.canonical_namespace_digest,
        "prior_checkpoint_sha256": page.prior_checkpoint_sha256,
        "source_head_sequence": page.source_head_sequence,
        "source_head_chain_sha256": page.source_head_chain_sha256,
        "start_sequence": page.start_sequence,
        "start_chain_sha256": page.start_chain_sha256,
        "end_sequence": page.end_sequence,
        "end_chain_sha256": page.end_chain_sha256,
        "event_locators": [asdict(locator) for locator in page.event_locators],
        "checkpoint_sha256": page.checkpoint.checkpoint_sha256,
        "has_more": page.has_more,
    }


def read_rolling_history_v2_migration_source_page(
    handle,
    *,
    scope: ContextEventScope,
    root_binding: RollingHistoryV2MigrationRootBinding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
    checkpoint: RollingHistoryV2MigrationCheckpoint,
    current_live_fence: RollingHistoryV2MigrationLiveFence,
    max_events: int,
) -> RollingHistoryV2MigrationSourcePage:
    """Verify at most ``max_events`` contiguous v1 events after a trusted checkpoint.

    Call only from the trusted host store after it revalidates the durable
    migration-root mapping and current source generation in this same owner
    transaction. This helper authenticates v1 source rows and checkpoints; it
    does not validate or persist the store-owned root map or write a v2 branch.
    Each call performs O(k) event reads for k returned locators plus a constant
    number of indexed head/endpoint reads. It never resolves full v2 lineage.
    """
    _validate_rolling_history_v2_migration_owner_binding(
        scope, root_binding, source_generation,
    )
    _validate_rolling_history_v2_migration_checkpoint(
        checkpoint, root_binding, source_generation,
    )
    _validate_rolling_history_v2_migration_live_fence(current_live_fence)
    _validate_rolling_history_v2_migration_live_append(
        checkpoint.live_fence, current_live_fence,
    )
    if (type(max_events) is not int
            or not 1 <= max_events <= _ROLLING_HISTORY_V2_MIGRATION_PAGE_MAX_EVENTS):
        raise ValueError("Invalid rolling history v2 migration page bound")

    head_sequence, head_chain = _rolling_history_v2_migration_source_head(handle, scope)
    if checkpoint.sequence > head_sequence:
        raise ValueError("Rolling history migration source was truncated before its checkpoint")
    prefix = _scope_prefix(scope)
    if checkpoint.sequence == root_binding.root_history_revision:
        if checkpoint.chain_sha256 != root_binding.root_history_head_sha256:
            raise ValueError("Rolling history migration checkpoint does not match its root")
    else:
        prior = _owner_history_read_event_reference_at_sequence(
            handle, scope, prefix, checkpoint.sequence,
        )
        if prior.chain_sha256 != checkpoint.chain_sha256:
            raise ValueError("Rolling history migration checkpoint endpoint changed")
    if checkpoint.sequence == head_sequence and checkpoint.chain_sha256 != head_chain:
        raise ValueError("Rolling history migration source drifted at its head")

    end_sequence = min(head_sequence, checkpoint.sequence + max_events)
    locators: list[RollingHistoryV2EventLocator] = []
    previous_chain = checkpoint.chain_sha256
    for sequence in range(checkpoint.sequence + 1, end_sequence + 1):
        reference = _owner_history_read_event_reference_at_sequence(
            handle, scope, prefix, sequence,
            expected_previous_chain_sha256=previous_chain,
        )
        locators.append(RollingHistoryV2EventLocator(
            event_id=reference.event_id,
            request_id=reference.request_id,
            sequence=reference.sequence,
            version=reference.version,
            content_sha256=reference.content_sha256,
            chain_sha256=reference.chain_sha256,
        ))
        previous_chain = reference.chain_sha256

    if end_sequence == head_sequence and previous_chain != head_chain:
        raise ValueError("Rolling history migration source page does not reach its head")
    if _rolling_history_v2_migration_source_head(handle, scope) != (head_sequence, head_chain):
        raise ValueError("Rolling history migration source changed while reading a page")

    if locators:
        next_checkpoint = _new_rolling_history_v2_migration_checkpoint(
            root_binding=root_binding,
            source_generation=source_generation,
            live_fence=current_live_fence,
            sequence=end_sequence,
            chain_sha256=previous_chain,
            parent_checkpoint_sha256=checkpoint.checkpoint_sha256,
        )
    else:
        next_checkpoint = checkpoint
    page_fields = {
        "migration_id": root_binding.migration_id,
        "root_binding_sha256": root_binding.binding_sha256,
        "source_generation_sha256": source_generation.source_generation_sha256,
        "canonical_namespace_digest": root_binding.canonical_namespace_digest,
        "prior_checkpoint_sha256": checkpoint.checkpoint_sha256,
        "source_head_sequence": head_sequence,
        "source_head_chain_sha256": head_chain,
        "start_sequence": checkpoint.sequence,
        "start_chain_sha256": checkpoint.chain_sha256,
        "end_sequence": end_sequence,
        "end_chain_sha256": previous_chain,
        "event_locators": tuple(locators),
        "checkpoint": next_checkpoint,
        "has_more": end_sequence < head_sequence,
        "page_sha256": "",
    }
    page = _issue_rolling_history_v2_migration_host_value(
        RollingHistoryV2MigrationSourcePage,
        page_fields,
        _ROLLING_HISTORY_V2_MIGRATION_PAGE_MINT,
    )
    object.__setattr__(
        page,
        "page_sha256",
        _sha256(_canonical(_rolling_history_v2_migration_source_page_body(page))),
    )
    return page


def _validate_rolling_history_v2_migration_source_page(
    page: object,
    root_binding: RollingHistoryV2MigrationRootBinding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
) -> None:
    if (type(page) is not RollingHistoryV2MigrationSourcePage
            or page.migration_id != root_binding.migration_id
            or page.root_binding_sha256 != root_binding.binding_sha256
            or page.source_generation_sha256 != source_generation.source_generation_sha256
            or page.canonical_namespace_digest != root_binding.canonical_namespace_digest
            or type(page.prior_checkpoint_sha256) is not str
            or _HASH.fullmatch(page.prior_checkpoint_sha256) is None
            or type(page.source_head_sequence) is not int
            or page.source_head_sequence < 0
            or type(page.source_head_chain_sha256) is not str
            or _HASH.fullmatch(page.source_head_chain_sha256) is None
            or type(page.start_sequence) is not int or page.start_sequence < 0
            or type(page.start_chain_sha256) is not str
            or _HASH.fullmatch(page.start_chain_sha256) is None
            or type(page.end_sequence) is not int or page.end_sequence < page.start_sequence
            or type(page.end_chain_sha256) is not str
            or _HASH.fullmatch(page.end_chain_sha256) is None
            or type(page.has_more) is not bool
            or page.has_more != (page.end_sequence < page.source_head_sequence)
            or type(page.page_sha256) is not str
            or type(page.event_locators) is not tuple):
        raise ValueError("Invalid rolling history v2 migration source page")
    _rolling_history_v2_locator_tuple(page.event_locators)
    if (page.end_sequence - page.start_sequence != len(page.event_locators)
            or any(locator.sequence != page.start_sequence + offset
                   for offset, locator in enumerate(page.event_locators, start=1))
            or (page.event_locators
                and page.event_locators[-1].chain_sha256 != page.end_chain_sha256)
            or (not page.event_locators
                and page.end_chain_sha256 != page.start_chain_sha256)):
        raise ValueError("Rolling history v2 migration source page is not contiguous")
    _validate_rolling_history_v2_migration_checkpoint(
        page.checkpoint, root_binding, source_generation,
    )
    if (page.checkpoint.sequence != page.end_sequence
            or page.checkpoint.chain_sha256 != page.end_chain_sha256
            or (page.event_locators
                and page.checkpoint.parent_checkpoint_sha256 != page.prior_checkpoint_sha256)
            or (not page.event_locators
                and page.checkpoint.checkpoint_sha256 != page.prior_checkpoint_sha256)
            or page.page_sha256 != _sha256(_canonical(
                _rolling_history_v2_migration_source_page_body(page),
            ))):
        raise ValueError("Rolling history v2 migration source page commitment mismatch")


def validate_rolling_history_v2_migration_revision_step(
    *,
    scope: ContextEventScope,
    root_binding: RollingHistoryV2MigrationRootBinding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
    source_page: RollingHistoryV2MigrationSourcePage,
    parent: RollingHistoryV2Revision,
    revision: RollingHistoryV2Revision,
) -> tuple[RollingHistoryV2EventLocator, ...]:
    """Validate one bounded migration revision from its verified source page.

    The trusted store must first re-read the migration-root map and current
    branch head in this same owner transaction, then obtain ``source_page`` with
    :func:`read_rolling_history_v2_migration_source_page`. This step preserves
    the new revision's current request ID while treating each old v1 request ID
    only as provenance from those checked source rows. It returns only this
    page's locators and does not materialize the branch's complete visible list.
    """
    _validate_rolling_history_v2_migration_owner_binding(
        scope, root_binding, source_generation,
    )
    _validate_rolling_history_v2_migration_source_page(
        source_page, root_binding, source_generation,
    )
    from app.api.rolling_history_owner_scope import get_rolling_history_owner_identity

    identity = get_rolling_history_owner_identity()
    _validate_rolling_history_v2_revision(parent)
    _validate_rolling_history_v2_revision(revision)
    if (not source_page.event_locators
            or parent.branch_id != root_binding.branch_id
            or revision.branch_id != root_binding.branch_id
            or parent.scope_sha256 != root_binding.scope_sha256
            or revision.scope_sha256 != root_binding.scope_sha256
            or parent.history_revision != source_page.start_sequence
            or parent.history_head_sha256 != source_page.start_chain_sha256
            or revision.operation != "migration_append"
            or revision.representation != "delta"
            or revision.lineage_depth != 0
            or revision.revision_number != parent.revision_number + 1
            or revision.parent_revision_id != parent.revision_id
            or revision.parent_revision_sha256 != parent.revision_sha256
            or revision.history_revision != source_page.end_sequence
            or revision.history_head_sha256 != source_page.end_chain_sha256
            or revision.request_id != identity.request_id
            or (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
            != (identity.session_id, identity.request_id, identity.principal_hash, identity.delete_epoch)
            or revision.event_locators != source_page.event_locators):
        raise ValueError("Rolling history v2 migration revision does not match its verified source page")
    return source_page.event_locators


def _rolling_history_v2_branch_envelope(branch: RollingHistoryV2Branch) -> dict:
    return {
        "schema_version": branch.schema_version,
        "scope_sha256": branch.scope_sha256,
        "branch_id": branch.branch_id,
        "created_revision_id": branch.created_revision_id,
        "created_revision_sha256": branch.created_revision_sha256,
        "request_id": branch.request_id,
        "idempotency_id": branch.idempotency_id,
        "idempotency_key_sha256": branch.idempotency_key_sha256,
    }


def _validate_rolling_history_v2_branch(
    branch: RollingHistoryV2Branch,
    created_revision: RollingHistoryV2Revision,
) -> None:
    _validate_rolling_history_v2_revision(created_revision)
    if (type(branch) is not RollingHistoryV2Branch
            or type(branch.schema_version) is not int
            or branch.schema_version != _REVISION_LEDGER_V2_SCHEMA_VERSION
            or type(branch.scope_sha256) is not str or _HASH.fullmatch(branch.scope_sha256) is None
            or type(branch.branch_id) is not str or _HASH.fullmatch(branch.branch_id) is None
            or type(branch.created_revision_id) is not str
            or _HASH.fullmatch(branch.created_revision_id) is None
            or type(branch.created_revision_sha256) is not str
            or _HASH.fullmatch(branch.created_revision_sha256) is None
            or type(branch.request_id) is not str or not _valid_request_id(branch.request_id)
            or type(branch.idempotency_id) is not str or _HASH.fullmatch(branch.idempotency_id) is None
            or type(branch.idempotency_key_sha256) is not str
            or _HASH.fullmatch(branch.idempotency_key_sha256) is None
            or type(branch.branch_sha256) is not str or _HASH.fullmatch(branch.branch_sha256) is None
            or created_revision.operation not in ("root", "fork")
            or branch.scope_sha256 != created_revision.scope_sha256
            or branch.branch_id != created_revision.branch_id
            or branch.created_revision_id != created_revision.revision_id
            or branch.created_revision_sha256 != created_revision.revision_sha256
            or branch.request_id != created_revision.request_id
            or branch.idempotency_id != created_revision.idempotency_id
            or branch.idempotency_key_sha256 != created_revision.idempotency_key_sha256
            or branch.branch_sha256 != _sha256(_canonical(_rolling_history_v2_branch_envelope(branch)))):
        raise ValueError("Rolling history v2 branch binding mismatch")


def _rolling_history_v2_head_envelope(head: RollingHistoryV2Head) -> dict:
    return {
        "schema_version": head.schema_version,
        "scope_sha256": head.scope_sha256,
        "branch_id": head.branch_id,
        "revision_id": head.revision_id,
        "revision_sha256": head.revision_sha256,
        "revision_number": head.revision_number,
        "history_revision": head.history_revision,
        "history_head_sha256": head.history_head_sha256,
    }


def _validate_rolling_history_v2_head_token(head: RollingHistoryV2Head) -> None:
    if (type(head) is not RollingHistoryV2Head
            or type(head.schema_version) is not int
            or head.schema_version != _REVISION_LEDGER_V2_SCHEMA_VERSION
            or type(head.scope_sha256) is not str or _HASH.fullmatch(head.scope_sha256) is None
            or type(head.branch_id) is not str or _HASH.fullmatch(head.branch_id) is None
            or type(head.revision_id) is not str or _HASH.fullmatch(head.revision_id) is None
            or type(head.revision_sha256) is not str or _HASH.fullmatch(head.revision_sha256) is None
            or type(head.revision_number) is not int or head.revision_number < 1
            or type(head.history_revision) is not int or head.history_revision < 0
            or type(head.history_head_sha256) is not str
            or _HASH.fullmatch(head.history_head_sha256) is None
            or type(head.head_sha256) is not str or _HASH.fullmatch(head.head_sha256) is None
            or head.head_sha256 != _sha256(_canonical(_rolling_history_v2_head_envelope(head)))):
        raise ValueError("Invalid rolling history v2 head token")


def _validate_rolling_history_v2_head(
    head: RollingHistoryV2Head,
    revision: RollingHistoryV2Revision,
) -> None:
    _validate_rolling_history_v2_revision(revision)
    if (type(head) is not RollingHistoryV2Head
            or type(head.schema_version) is not int
            or head.schema_version != _REVISION_LEDGER_V2_SCHEMA_VERSION
            or type(head.scope_sha256) is not str or _HASH.fullmatch(head.scope_sha256) is None
            or type(head.branch_id) is not str or _HASH.fullmatch(head.branch_id) is None
            or type(head.revision_id) is not str or _HASH.fullmatch(head.revision_id) is None
            or type(head.revision_sha256) is not str or _HASH.fullmatch(head.revision_sha256) is None
            or type(head.revision_number) is not int or head.revision_number < 1
            or type(head.history_revision) is not int or head.history_revision < 0
            or type(head.history_head_sha256) is not str
            or _HASH.fullmatch(head.history_head_sha256) is None
            or type(head.head_sha256) is not str or _HASH.fullmatch(head.head_sha256) is None
            or head.scope_sha256 != revision.scope_sha256
            or head.branch_id != revision.branch_id
            or head.revision_id != revision.revision_id
            or head.revision_sha256 != revision.revision_sha256
            or head.revision_number != revision.revision_number
            or head.history_revision != revision.history_revision
            or head.history_head_sha256 != revision.history_head_sha256
            or head.head_sha256 != _sha256(_canonical(_rolling_history_v2_head_envelope(head)))):
        raise ValueError("Rolling history v2 head binding mismatch")


def _require_rolling_history_v2_expected_head(
    expected: RollingHistoryV2Head,
    actual: RollingHistoryV2Head,
) -> None:
    """Compare validated head tokens; this is not a database transaction or global CAS."""
    _validate_rolling_history_v2_head_token(expected)
    _validate_rolling_history_v2_head_token(actual)
    if expected != actual:
        raise ValueError("Rolling history v2 expected-head conflict")


def _rolling_history_v2_prepared_envelope(prepared: PreparedRollingHistoryV2Summary) -> dict:
    return {
        "schema_version": prepared.schema_version,
        "prepared_id": prepared.prepared_id,
        "scope_sha256": prepared.scope_sha256,
        "branch_id": prepared.branch_id,
        "request_id": prepared.request_id,
        "idempotency_id": prepared.idempotency_id,
        "idempotency_key_sha256": prepared.idempotency_key_sha256,
        "history_revision_id": prepared.history_revision_id,
        "history_revision_sha256": prepared.history_revision_sha256,
        "summary_sha256": prepared.summary_sha256,
    }


def _validate_prepared_rolling_history_v2_summary(
    prepared: PreparedRollingHistoryV2Summary,
    history_revision: RollingHistoryV2Revision,
) -> None:
    _validate_rolling_history_v2_revision(history_revision)
    if (type(prepared) is not PreparedRollingHistoryV2Summary
            or type(prepared.schema_version) is not int
            or prepared.schema_version != _REVISION_LEDGER_V2_SCHEMA_VERSION
            or type(prepared.prepared_id) is not str or _HASH.fullmatch(prepared.prepared_id) is None
            or type(prepared.scope_sha256) is not str or _HASH.fullmatch(prepared.scope_sha256) is None
            or type(prepared.branch_id) is not str or _HASH.fullmatch(prepared.branch_id) is None
            or type(prepared.request_id) is not str or not _valid_request_id(prepared.request_id)
            or type(prepared.idempotency_id) is not str or _HASH.fullmatch(prepared.idempotency_id) is None
            or type(prepared.idempotency_key_sha256) is not str
            or _HASH.fullmatch(prepared.idempotency_key_sha256) is None
            or type(prepared.history_revision_id) is not str
            or _HASH.fullmatch(prepared.history_revision_id) is None
            or type(prepared.history_revision_sha256) is not str
            or _HASH.fullmatch(prepared.history_revision_sha256) is None
            or type(prepared.summary_sha256) is not str or _HASH.fullmatch(prepared.summary_sha256) is None
            or type(prepared.prepared_sha256) is not str
            or _HASH.fullmatch(prepared.prepared_sha256) is None
            or prepared.prepared_id != prepared.idempotency_id
            or prepared.idempotency_id != _rolling_history_v2_idempotency_id(
                prepared.scope_sha256, prepared.request_id,
                prepared.idempotency_key_sha256,
            )
            or prepared.scope_sha256 != history_revision.scope_sha256
            or prepared.branch_id != history_revision.branch_id
            or prepared.history_revision_id != history_revision.revision_id
            or prepared.history_revision_sha256 != history_revision.revision_sha256
            or prepared.prepared_sha256 != _sha256(_canonical(_rolling_history_v2_prepared_envelope(prepared)))):
        raise ValueError("Prepared rolling history v2 summary binding mismatch")
