"""Internal, bounded archive for explicitly sanitized text; not an HTTP tool."""

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy import LargeBinary, cast, func, text

from app.api.common import get_db_ctx
from app.api.session_epoch import SESSION_DELETE_EPOCH_PREFIX
from app.db.models import Session, Settings


CONTEXT_EVENT_PREFIX = "_contextEvent:"
_BATCH_BYTES = 32768
_SESSION_BYTES = 131072
_TOTAL_BYTES = 2097152
_MAX_BATCHES = 256
_CREDENTIAL = re.compile(
    r"(?i)(?:\bsk[-_][a-z0-9_-]{12,}|\bbearer\s+\S+|"
    r"(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|data:[^\s,]*;base64,)",
)
logger = logging.getLogger(__name__)
_HASH = re.compile(r"[0-9a-f]{64}")
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_SOURCE_METADATA_MAX_BYTES = 4096


@dataclass(frozen=True)
class ContextEventScope:
    """Trusted host identity and lifecycle snapshot, never model assertions."""

    session_id: str
    request_id: str
    principal_hash: str
    session_epoch: str


@dataclass(frozen=True)
class ContextEventV3Locator:
    """Exact body-backed V3 locator supplied by the rolling-history owner."""

    event_id: str
    request_id: str
    sequence: int
    version: int
    content_sha256: str
    chain_sha256: str


@dataclass(frozen=True)
class ContextEventSourceBinding:
    """Store-issued identity sidecar for one exact V3 source message.

    The event archive validates this contract and persists it atomically with
    the host event. It does not manufacture a source binding from event text.
    """

    schema_version: int
    canonical_namespace_digest: str
    scope_sha256: str
    delete_epoch: str
    request_id: str
    snapshot_sha256: str
    head_sha256: str
    source_fence_sha256: str
    recent_message_start_boundary: int
    locator: ContextEventV3Locator
    source_role: Literal["user", "assistant"]
    source_text_sha256: str
    source_metadata_json: str
    source_metadata_sha256: str
    source_identity_sha256: str
    binding_sha256: str


@dataclass(frozen=True)
class EventReference:
    batch_id: str
    session_id: str
    request_id: str
    sequence: int
    version: int
    content_sha256: str
    source_binding_sha256: str | None = None


@dataclass(frozen=True)
class ContextEventSourceBindingLookup:
    """Exact-source readback; missing legacy evidence is never ABSENT proof."""

    state: Literal["CONFIRMED", "CONFLICT", "UNKNOWN"]
    binding: ContextEventSourceBinding | None = None
    source_binding_sha256: str | None = None


class _SourceBindingUnavailable(Exception):
    pass


class _SourceBindingConflict(Exception):
    pass


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        allow_nan=False,
    )


def _sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if type(value) is str else value
    return hashlib.sha256(raw).hexdigest()


def _valid_hash(value: object) -> bool:
    return type(value) is str and _HASH.fullmatch(value) is not None


def _source_metadata_value(
    binding: ContextEventSourceBinding,
    scope: ContextEventScope,
) -> dict:
    raw = binding.source_metadata_json
    if (type(raw) is not str or not 1 <= len(raw.encode("utf-8", errors="strict"))
            <= _SOURCE_METADATA_MAX_BYTES):
        raise ValueError("Invalid V3 source metadata size")
    value = json.loads(raw)
    if type(value) is not dict or _canonical(value) != raw:
        raise ValueError("V3 source metadata is not canonical")

    legacy_fields = {
        "source_kind", "migration_id", "snapshot_id", "source_root_sha256",
        "source_sequence", "row_sha256", "source_chain_sha256", "message_id",
        "created_at", "message_event_sha256", "source_identity_sha256",
    }
    append_fields = {
        "message_id", "created_at", "message_event_sha256",
        "source_identity_sha256", "append_fence", "batch_count", "batch_index",
    }
    message_id = value.get("message_id")
    if (type(message_id) is not str or not 1 <= len(message_id) <= 128
            or any(ord(char) < 33 or ord(char) == 127 for char in message_id)
            or not _valid_hash(value.get("message_event_sha256"))
            or not _valid_hash(value.get("source_identity_sha256"))):
        raise ValueError("Invalid V3 source metadata identity")
    created_at = value.get("created_at")
    if (created_at is not None and (type(created_at) is not str
            or len(created_at.encode("utf-8", errors="strict")) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in created_at))):
        raise ValueError("Invalid V3 source metadata timestamp")

    if set(value) == legacy_fields:
        if (value["source_kind"] != "legacy_message_blob_snapshot_v1"
                or type(value["migration_id"]) is not str
                or _REQUEST_ID.fullmatch(value["migration_id"]) is None
                or type(value["snapshot_id"]) is not str
                or _REQUEST_ID.fullmatch(value["snapshot_id"]) is None
                or not _valid_hash(value["source_root_sha256"])
                or type(value["source_sequence"]) is not int
                or value["source_sequence"] < 0
                or not _valid_hash(value["row_sha256"])
                or not _valid_hash(value["source_chain_sha256"])):
            raise ValueError("Invalid legacy V3 source metadata")
        identity_kind = "rolling_history_legacy_message_snapshot_source"
    elif set(value) == append_fields:
        fence = value["append_fence"]
        fence_fields = {
            "message_source_generation", "message_count", "non_append_generation",
        }
        before = fence.get("before") if type(fence) is dict else None
        after = fence.get("after") if type(fence) is dict else None
        if (type(created_at) is not str
                or not 1 <= len(created_at.encode("utf-8", errors="strict")) <= 128
                or type(fence) is not dict or set(fence) != {"before", "after"}
                or type(before) is not dict or set(before) != fence_fields
                or type(after) is not dict or set(after) != fence_fields
                or any(type(item) is not int or item < 0
                       for item in (*before.values(), *after.values()))
                or type(value["batch_count"]) is not int or value["batch_count"] < 1
                or type(value["batch_index"]) is not int
                or not 1 <= value["batch_index"] <= value["batch_count"]
                or after["non_append_generation"] != before["non_append_generation"]
                or after["message_source_generation"] - before["message_source_generation"]
                != value["batch_count"]
                or after["message_count"] - before["message_count"]
                != value["batch_count"]):
            raise ValueError("Invalid append V3 source metadata")
        identity_kind = "rolling_history_chat_message_source"
    else:
        raise ValueError("Unsupported V3 source metadata schema")

    expected_identity = _sha256(_canonical({
        "kind": identity_kind,
        "canonical_namespace_digest": binding.canonical_namespace_digest,
        "session_id": scope.session_id,
        "message_id": message_id,
    }))
    if value["source_identity_sha256"] != expected_identity:
        raise ValueError("V3 source identity is not bound to this owner and session")
    return value


def _source_binding_value(
    binding: ContextEventSourceBinding,
    scope: ContextEventScope,
) -> dict:
    if type(binding) is not ContextEventSourceBinding:
        raise ValueError("A typed V3 source binding is required")
    locator = binding.locator
    if (type(locator) is not ContextEventV3Locator
            or type(locator.event_id) is not str or not _valid_hash(locator.event_id)
            or type(locator.request_id) is not str
            or _REQUEST_ID.fullmatch(locator.request_id) is None
            or type(locator.sequence) is not int or locator.sequence < 1
            or type(locator.version) is not int or locator.version != 3
            or not _valid_hash(locator.content_sha256)
            or not _valid_hash(locator.chain_sha256)
            or type(binding.schema_version) is not int or binding.schema_version != 1
            or not _valid_hash(binding.canonical_namespace_digest)
            or not _valid_hash(binding.scope_sha256)
            or binding.scope_sha256 != _sha256(_canonical(asdict(scope)))
            or binding.delete_epoch != scope.session_epoch
            or binding.request_id != scope.request_id
            or not _valid_hash(binding.snapshot_sha256)
            or not _valid_hash(binding.head_sha256)
            or not _valid_hash(binding.source_fence_sha256)
            or type(binding.recent_message_start_boundary) is not int
            or binding.recent_message_start_boundary < 1
            or type(binding.source_role) is not str
            or binding.source_role not in ("user", "assistant")
            or not _valid_hash(binding.source_text_sha256)
            or not _valid_hash(binding.source_metadata_sha256)
            or not _valid_hash(binding.source_identity_sha256)
            or not _valid_hash(binding.binding_sha256)):
        raise ValueError("Invalid or out-of-scope V3 source binding")
    metadata = _source_metadata_value(binding, scope)
    if (binding.source_metadata_sha256 != _sha256(binding.source_metadata_json)
            or binding.source_identity_sha256 != metadata["source_identity_sha256"]):
        raise ValueError("V3 source metadata digest mismatch")
    value = asdict(binding)
    expected_binding_sha256 = _sha256(_canonical({
        key: item for key, item in value.items() if key != "binding_sha256"
    }))
    if binding.binding_sha256 != expected_binding_sha256:
        raise ValueError("V3 source binding digest mismatch")
    return value


def _source_binding_from_value(
    value: object,
    scope: ContextEventScope,
) -> ContextEventSourceBinding:
    required = {
        "schema_version", "canonical_namespace_digest", "scope_sha256",
        "delete_epoch", "request_id", "snapshot_sha256", "head_sha256",
        "source_fence_sha256", "recent_message_start_boundary", "locator",
        "source_role", "source_text_sha256", "source_metadata_json",
        "source_metadata_sha256", "source_identity_sha256", "binding_sha256",
    }
    if type(value) is not dict or set(value) != required or type(value["locator"]) is not dict:
        raise ValueError("Invalid persisted V3 source binding")
    locator_fields = {
        "event_id", "request_id", "sequence", "version", "content_sha256",
        "chain_sha256",
    }
    if set(value["locator"]) != locator_fields:
        raise ValueError("Invalid persisted V3 locator")
    binding = ContextEventSourceBinding(
        schema_version=value["schema_version"],
        canonical_namespace_digest=value["canonical_namespace_digest"],
        scope_sha256=value["scope_sha256"],
        delete_epoch=value["delete_epoch"],
        request_id=value["request_id"],
        snapshot_sha256=value["snapshot_sha256"],
        head_sha256=value["head_sha256"],
        source_fence_sha256=value["source_fence_sha256"],
        recent_message_start_boundary=value["recent_message_start_boundary"],
        locator=ContextEventV3Locator(**value["locator"]),
        source_role=value["source_role"],
        source_text_sha256=value["source_text_sha256"],
        source_metadata_json=value["source_metadata_json"],
        source_metadata_sha256=value["source_metadata_sha256"],
        source_identity_sha256=value["source_identity_sha256"],
        binding_sha256=value["binding_sha256"],
    )
    _source_binding_value(binding, scope)
    return binding


def _source_binding_values(
    scope: ContextEventScope,
    events: list[dict],
    source_bindings: tuple[ContextEventSourceBinding, ...],
) -> tuple[dict, ...]:
    if (type(source_bindings) is not tuple or len(source_bindings) != len(events)
            or not source_bindings):
        raise ValueError("A complete ordered V3 source-binding tuple is required")
    values = []
    previous_sequence = 0
    common_header = None
    seen_locators = set()
    for event, binding in zip(events, source_bindings, strict=True):
        value = _source_binding_value(binding, scope)
        if (event["role"] != binding.source_role
                or _sha256(event["content"]) != binding.source_text_sha256
                or binding.locator.sequence <= previous_sequence):
            raise ValueError("Host event does not match its ordered V3 source binding")
        previous_sequence = binding.locator.sequence
        locator_key = (
            binding.locator.event_id, binding.locator.request_id,
            binding.locator.sequence, binding.locator.version,
            binding.locator.content_sha256, binding.locator.chain_sha256,
        )
        if locator_key in seen_locators:
            raise ValueError("Duplicate V3 source locator in host batch")
        seen_locators.add(locator_key)
        header = (
            binding.canonical_namespace_digest, binding.scope_sha256,
            binding.delete_epoch, binding.request_id, binding.snapshot_sha256,
            binding.head_sha256, binding.source_fence_sha256,
            binding.recent_message_start_boundary,
        )
        if common_header is None:
            common_header = header
        elif common_header != header:
            raise ValueError("V3 source-binding batch crosses snapshot or owner scope")
        values.append(value)
    return tuple(values)


def _source_bindings_from_batch(
    scope: ContextEventScope,
    raw_values: object,
    expected_count: int,
) -> tuple[ContextEventSourceBinding, ...]:
    if type(raw_values) is not list or len(raw_values) != expected_count:
        raise _SourceBindingConflict("V3 source-binding batch is incomplete")
    try:
        bindings = tuple(_source_binding_from_value(item, scope) for item in raw_values)
    except _SourceBindingConflict:
        raise
    except Exception as exc:
        raise _SourceBindingConflict("V3 source-binding batch is invalid") from exc
    previous_sequence = 0
    common_header = None
    seen_locators = set()
    for binding in bindings:
        if binding.locator.sequence <= previous_sequence:
            raise _SourceBindingConflict("V3 source bindings are not ordered")
        previous_sequence = binding.locator.sequence
        locator_key = (
            binding.locator.event_id, binding.locator.request_id,
            binding.locator.sequence, binding.locator.version,
            binding.locator.content_sha256, binding.locator.chain_sha256,
        )
        if locator_key in seen_locators:
            raise _SourceBindingConflict("V3 source locator is repeated")
        seen_locators.add(locator_key)
        header = (
            binding.canonical_namespace_digest, binding.scope_sha256,
            binding.delete_epoch, binding.request_id, binding.snapshot_sha256,
            binding.head_sha256, binding.source_fence_sha256,
            binding.recent_message_start_boundary,
        )
        if common_header is None:
            common_header = header
        elif common_header != header:
            raise _SourceBindingConflict("V3 source bindings cross snapshot or owner scope")
    return bindings


def _session_prefix(session_id: str) -> str:
    return CONTEXT_EVENT_PREFIX + hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ":"


def _validate_scope(db, scope: ContextEventScope) -> None:
    if (type(scope) is not ContextEventScope
            or type(scope.session_id) is not str or not 1 <= len(scope.session_id) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in scope.session_id)
            or type(scope.request_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", scope.request_id)
            or type(scope.principal_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", scope.principal_hash)
            or type(scope.session_epoch) is not str
            or (scope.session_epoch != "" and not re.fullmatch(r"[0-9a-f]{32}", scope.session_epoch))):
        raise ValueError("Invalid host context event scope")
    if db.get(Session, scope.session_id) is None:
        raise ValueError("Context event session is unavailable")
    key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(scope.session_id.encode("utf-8")).hexdigest()
    marker = db.get(Settings, key)
    epoch = "" if marker is None else marker.value
    if marker is not None and (type(epoch) is not str or not re.fullmatch(r"[0-9a-f]{32}", epoch)):
        raise ValueError("Context event lifecycle is unavailable")
    if epoch != scope.session_epoch:
        raise ValueError("Context event scope expired")


def _encode_events(events: list[dict]) -> tuple[str, ...]:
    if type(events) is not list or not 1 <= len(events) <= 64:
        raise ValueError("Context event count exceeded")
    encoded = []
    remaining = _BATCH_BYTES
    for event in events:
        if (type(event) is not dict or set(event) != {"role", "content"}
                or event["role"] not in ("user", "assistant")
                or type(event["content"]) is not str
                or len(event["content"]) > remaining
                or _CREDENTIAL.search(event["content"])):
            raise ValueError("Context events require sanitized plain text")
        record = json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        remaining -= len(record.encode("utf-8"))
        if remaining < 0:
            raise ValueError("Context event bytes exceeded")
        encoded.append(record)
    return tuple(encoded)


def persist_context_events(
    scope: ContextEventScope,
    events: list[dict],
    *,
    authorized: bool,
    source_bindings: tuple[ContextEventSourceBinding, ...] | None = None,
) -> tuple[EventReference, ...]:
    """Return references only after the whole batch commits.

    Omitting source_bindings preserves the legacy V1 archive contract. Exact
    compaction-source writes must pass one validated V3 binding per event.
    """
    if authorized is not True:
        raise ValueError("Context event permission denied")
    encoded = _encode_events(events)
    batch_id = uuid.uuid4().hex
    binding_values = None
    if source_bindings is None:
        value = _canonical({"version": 1, "scope": asdict(scope), "records": encoded})
    else:
        binding_values = _source_binding_values(scope, events, source_bindings)
        value = _canonical({
            "version": 2,
            "scope": asdict(scope),
            "records": encoded,
            "source_bindings": binding_values,
        })
    size = len(value.encode("utf-8"))
    if size > _BATCH_BYTES:
        raise ValueError("Context event envelope exceeded")
    with get_db_ctx() as db:
        # Serialize with session deletion; no archive can be inserted from a stale
        # generation after a concurrent delete transaction finishes.
        db.execute(text("BEGIN IMMEDIATE"))
        _validate_scope(db, scope)
        rows = db.query(Settings.key, func.length(cast(Settings.value, LargeBinary))).filter(
            Settings.key.startswith(CONTEXT_EVENT_PREFIX, autoescape=True),
        ).limit(_MAX_BATCHES + 1).all()
        prefix = _session_prefix(scope.session_id)
        if (len(rows) >= _MAX_BATCHES
                or sum(length or 0 for _, length in rows) + size > _TOTAL_BYTES
                or sum(length or 0 for key, length in rows if key.startswith(prefix)) + size > _SESSION_BYTES):
            raise ValueError("Context archive capacity exhausted")
        db.add(Settings(key=prefix + batch_id, value=value))
    return tuple(
        EventReference(
            batch_id, scope.session_id, scope.request_id, index, 1,
            hashlib.sha256(record.encode("utf-8")).hexdigest(),
            None if binding_values is None else binding_values[index - 1]["binding_sha256"],
        )
        for index, record in enumerate(encoded, start=1)
    )


def _read_batch_record(
    db,
    scope: ContextEventScope,
    reference: EventReference,
    byte_budget: int,
) -> tuple[dict, ContextEventSourceBinding | None]:
    _validate_scope(db, scope)
    if (type(reference) is not EventReference
            or type(reference.batch_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", reference.batch_id)
            or reference.session_id != scope.session_id or reference.request_id != scope.request_id
            or type(reference.version) is not int or reference.version != 1
            or type(reference.sequence) is not int or not 1 <= reference.sequence <= 64
            or type(reference.content_sha256) is not str
            or not _valid_hash(reference.content_sha256)
            or (reference.source_binding_sha256 is not None
                and not _valid_hash(reference.source_binding_sha256))):
        raise ValueError("Invalid context event reference")
    row = db.get(Settings, _session_prefix(scope.session_id) + reference.batch_id)
    if row is None or type(row.value) is not str or len(row.value.encode("utf-8")) > _BATCH_BYTES:
        raise ValueError("Context event is unavailable")
    batch = json.loads(row.value)
    common_fields = {"version", "scope", "records"}
    batch_version = batch.get("version") if type(batch) is dict else None
    expected_fields = (
        common_fields if batch_version == 1
        else common_fields | {"source_bindings"} if batch_version == 2
        else set()
    )
    if (type(batch) is not dict or set(batch) != expected_fields
            or type(batch_version) is not int or batch_version not in (1, 2)
            or batch["scope"] != asdict(scope) or type(batch["records"]) is not list
            or not 1 <= len(batch["records"]) <= 64):
        raise ValueError("Invalid context event batch")
    source_bindings = None
    if batch_version == 1:
        if reference.source_binding_sha256 is not None:
            raise _SourceBindingConflict("Legacy context event has no source-binding sidecar")
    else:
        source_bindings = _source_bindings_from_batch(
            scope, batch["source_bindings"], len(batch["records"]),
        )
    record = batch["records"][reference.sequence - 1]
    if (type(record) is not str or len(record.encode("utf-8")) > byte_budget
            or hashlib.sha256(record.encode("utf-8")).hexdigest() != reference.content_sha256):
        raise ValueError("Context event integrity or budget failure")
    event = json.loads(record)
    if _encode_events([event]) != (record,):
        raise ValueError("Noncanonical context event")
    source_binding = None if source_bindings is None else source_bindings[reference.sequence - 1]
    if source_binding is not None:
        if (event["role"] != source_binding.source_role
                or _sha256(event["content"]) != source_binding.source_text_sha256
                or (reference.source_binding_sha256 is not None
                    and reference.source_binding_sha256 != source_binding.binding_sha256)):
            raise _SourceBindingConflict("Context event source-binding readback changed")
    return event, source_binding


def _read_batch(db, scope: ContextEventScope, reference: EventReference, byte_budget: int) -> dict:
    """Read a V1 or V2 event; a supplied sidecar digest is always checked."""
    return _read_batch_record(db, scope, reference, byte_budget)[0]


def recover_context_event(scope: ContextEventScope, reference: EventReference, *, authorized: bool, byte_budget: int) -> dict | None:
    """Return a validated record or None, without executing it."""
    if authorized is not True or type(byte_budget) is not int or not 1 <= byte_budget <= _BATCH_BYTES:
        return None
    try:
        with get_db_ctx() as db:
            return _read_batch(db, scope, reference, byte_budget)
    except Exception:
        # Never log record contents, credentials, database error text or scope.
        logger.warning("Context event recovery refused")
        return None


def recover_context_event_source_binding(
    scope: ContextEventScope,
    reference: EventReference,
    *,
    authorized: bool,
) -> ContextEventSourceBindingLookup:
    """Read exact V3 provenance; legacy or unbound references stay UNKNOWN."""
    if (authorized is not True or type(scope) is not ContextEventScope
            or type(reference) is not EventReference
            or not _valid_hash(reference.source_binding_sha256)):
        return ContextEventSourceBindingLookup("UNKNOWN")
    try:
        with get_db_ctx() as db:
            _event, binding = _read_batch_record(
                db, scope, reference, _BATCH_BYTES,
            )
        if (binding is None or binding.binding_sha256 != reference.source_binding_sha256):
            return ContextEventSourceBindingLookup("UNKNOWN")
        return ContextEventSourceBindingLookup(
            "CONFIRMED", binding, binding.binding_sha256,
        )
    except _SourceBindingConflict:
        return ContextEventSourceBindingLookup("CONFLICT")
    except Exception:
        return ContextEventSourceBindingLookup("UNKNOWN")


def archive_before_replace(original, replacement, scope, *, authorized):
    """Preserve original object when durable evidence cannot be published."""
    if replacement is original:
        return original, ()
    try:
        references = persist_context_events(scope, original, authorized=authorized)
        return replacement, references
    except Exception:
        logger.warning("Context archive unavailable; preserving original input")
        return original, ()
