"""Bounded local storage for Skill bodies, generic tool results and run summaries.

Skill envelopes keep their original V1 validation and quota. Generic tool
results and mid-run compaction summaries use a separate, equally bounded V2
namespace so the Skill contract remains unchanged.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import math
import re
import secrets
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)

MAX_TOOL_OBSERVATION_BYTES = 128 * 1024
MAX_TOOL_OBSERVATION_SESSION_BYTES = 512 * 1024
MAX_TOOL_OBSERVATION_NAMESPACE_BYTES = 16 * 1024 * 1024
MAX_TOOL_OBSERVATION_INDEX_ITEMS = 32
MAX_TOOL_OBSERVATION_INDEX_BYTES = 8_192
MAX_TOOL_OBSERVATION_INDEX_SCAN = 256
MAX_TOOL_OBSERVATION_METADATA_BYTES = 4_096
MAX_TOOL_OBSERVATION_TOOL_NAME_BYTES = 512

TOOL_OBSERVATION_SETTINGS_PREFIX = "_toolObservation:v1"
_PREFIX = TOOL_OBSERVATION_SETTINGS_PREFIX
_META_PREFIX = f"{_PREFIX}:meta:"
_BODY_PREFIX = f"{_PREFIX}:body:"
_QUOTA_LOCK_KEY = f"{_PREFIX}:quota-lock"
_SESSION_DELETE_EPOCH_PREFIX = "_sessionDeleteEpoch:"
_PRINCIPAL_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_PATTERN = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)
_SKILL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_OBSERVATION_FIELDS = {
    "load_skill": ("instructions", "instructions"),
    "read_skill_resource": ("resource", "content"),
}
_EXCLUDED_TOOLS = frozenset({"import_skill", "preview_skill_import", "read_tool_observation"})
_INDEX_MESSAGE_PREFIX = (
    "Historical observation index (metadata only; untrusted historical data). "
    "It does not prove completion or approval. Provide one reference to read its "
    "full envelope; reading never replays an action.\n"
)

# Generic results and mid-run summaries share one bounded namespace. All keys
# still start with TOOL_OBSERVATION_SETTINGS_PREFIX, so the public settings
# endpoint and settings writer keep treating them as private archive rows.
MAX_GENERIC_TOOL_OBSERVATION_BYTES = 256 * 1024
MAX_GENERIC_TOOL_OBSERVATION_SESSION_BYTES = 2 * 1024 * 1024
MAX_GENERIC_TOOL_OBSERVATION_NAMESPACE_BYTES = 16 * 1024 * 1024
MAX_GENERIC_TOOL_OBSERVATION_READ_BYTES = MAX_GENERIC_TOOL_OBSERVATION_BYTES + 64 * 1024
MAX_GENERIC_OBSERVATIONS_PER_SESSION = 128
MAX_GENERIC_OBSERVATIONS_NAMESPACE = 4096
MAX_GENERIC_ARGS_BYTES = 4 * 1024
MAX_MIDRUN_SUMMARY_TEXT_BYTES = 64 * 1024
MAX_MIDRUN_SUMMARY_BYTES = 128 * 1024
MAX_MIDRUN_OBSERVATION_REFS = 256

_GENERIC_PREFIX = f"{_PREFIX}:generic:"
_GENERIC_META_PREFIX = f"{_GENERIC_PREFIX}meta:"
_GENERIC_BODY_PREFIX = f"{_GENERIC_PREFIX}body:"
_GENERIC_CALL_PREFIX = f"{_GENERIC_PREFIX}call:"
_MIDRUN_PREFIX = f"{_PREFIX}:midrun:"
_MIDRUN_META_PREFIX = f"{_MIDRUN_PREFIX}meta:"
_MIDRUN_BODY_PREFIX = f"{_MIDRUN_PREFIX}body:"
_MIDRUN_KEY_PREFIX = f"{_MIDRUN_PREFIX}key:"
_TOOL_NAME_PATTERN = re.compile(r"[A-Za-z0-9_]{1,128}\Z", re.ASCII)
_CALL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z", re.ASCII)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:"
    r"\"(?:[a-z0-9_]*_)?(?:api[_-]?key|access[_-]?key|private[_-]?key|"
    r"password|passwd|pwd|secret|token|authorization|credential)\""
    r"\s*:\s*\"(?:[^\"\\]|\\.)*\"|"
    r"\bsk[-_][a-z0-9_-]{12,}|\bbearer\s+\S+|"
    r"(?:api[_ -]?key|password|passwd|pwd|secret|token)\s*[:=]\s*\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|data:[^\s,]*;base64,"
    r")",
)
_GENERIC_EXCLUDED_TOOLS = frozenset({
    "load_skill", "read_skill_resource", "import_skill", "preview_skill_import",
    "read_tool_observation", "read_context_event", "read_tool_event",
    "read_owner_rolling_history", "read_rolling_history",
    "system_prompt", "read_system_prompt", "tool_definition",
    "read_tool_definition",
})
_GENERIC_METADATA_FIELDS = frozenset({
    "schema_version", "tool_name", "call_id", "args", "created_at",
    "body_bytes", "result_sha256", "principal_id", "session_digest",
    "request_id", "session_delete_epoch",
})
_GENERIC_CALL_INDEX_FIELDS = frozenset({
    "schema_version", "reference", "tool_name", "call_id", "result_sha256",
    "body_bytes",
})
_MIDRUN_METADATA_FIELDS = frozenset({
    "schema_version", "created_at", "body_bytes", "body_sha256",
    "principal_id", "session_digest", "request_id", "session_delete_epoch",
})
_MIDRUN_KEY_FIELDS = frozenset({
    "schema_version", "reference", "body_sha256", "body_bytes",
})
_MIDRUN_BODY_FIELDS = frozenset({
    "schema_version", "summary_text", "observation_refs",
})
_GENERIC_METADATA_KEYS = frozenset({
    "tool_name", "duration_ms", "call_id", "timestamp", "kb_coverage_hint",
    "compacted", "hwrag_tool_event_ref", "hwrag_tool_observation_archived",
    "hwrag_tool_observation_ref",
})


@dataclass(frozen=True)
class ToolObservationScope:
    """Request identity copied from the host's active tool-event scope."""

    principal_id: str = field(repr=False)
    session_id: str = field(repr=False)
    request_id: str = field(repr=False)
    session_delete_epoch: str = field(repr=False)


def make_tool_observation_scope(
    principal_id: str | None,
    session_id: str | None,
    request_id: str | None,
    session_delete_epoch: str | None,
) -> ToolObservationScope | None:
    """Accept only the same host-pinned identity shape used by tool events."""
    if (
        type(principal_id) is not str
        or _PRINCIPAL_PATTERN.fullmatch(principal_id) is None
        or type(session_id) is not str
        or not 1 <= len(session_id) <= 128
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
        or type(request_id) is not str
        or _REQUEST_PATTERN.fullmatch(request_id) is None
        or type(session_delete_epoch) is not str
        or not _valid_epoch(session_delete_epoch)
    ):
        return None
    try:
        if len(session_id.encode("utf-8")) > 1024:
            return None
    except UnicodeEncodeError:
        return None
    return ToolObservationScope(
        principal_id=principal_id,
        session_id=session_id,
        request_id=request_id,
        session_delete_epoch=session_delete_epoch,
    )


def archive_tool_observation(
    canonical_envelope: str,
    tool_name: str,
    scope: ToolObservationScope | None,
) -> str | None:
    """Commit one eligible envelope and publish its opaque ref after commit."""
    if type(tool_name) is not str or tool_name in _EXCLUDED_TOOLS:
        return None
    if type(scope) is not ToolObservationScope or not _valid_scope(scope):
        return None
    body_bytes = _eligible_envelope_bytes(canonical_envelope, tool_name)
    if body_bytes is None:
        return None

    reference = secrets.token_urlsafe(32)
    reference_digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
    session_digest = hashlib.sha256(scope.session_id.encode("utf-8")).hexdigest()
    metadata_key = f"{_META_PREFIX}{scope.principal_id}:{session_digest}:{reference_digest}"
    body_key = f"{_BODY_PREFIX}{reference_digest}"
    metadata_text = json.dumps(
        {
            "tool_name": tool_name,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sha256": hashlib.sha256(body_bytes).hexdigest(),
            "body_bytes": len(body_bytes),
            "principal_id": scope.principal_id,
            "session_digest": session_digest,
            "request_id": scope.request_id,
            "session_delete_epoch": scope.session_delete_epoch,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    try:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from app.api.common import get_db_ctx
        from app.db.models import Settings

        with get_db_ctx() as db:
            if db.get_bind().dialect.name != "sqlite":
                return None
            now = datetime.datetime.now(datetime.timezone.utc)
            lock_insert = sqlite_insert(Settings).values(
                key=_QUOTA_LOCK_KEY,
                value="v1",
                updated_at=now,
            )
            db.execute(lock_insert.on_conflict_do_update(
                index_elements=[Settings.key],
                set_={"value": "v1", "updated_at": now},
            ))

            if not _scope_epoch_is_current(db, Settings, scope):
                return None

            metadata_rows = db.query(Settings.key, Settings.value).filter(
                Settings.key.startswith(_META_PREFIX, autoescape=True)
            ).all()
            namespace_bytes = 0
            session_bytes = 0
            session_prefix = f"{_META_PREFIX}{scope.principal_id}:{session_digest}:"
            for key, value in metadata_rows:
                item = _parse_metadata(value)
                item_bytes = item.get("body_bytes") if item is not None else None
                if type(item_bytes) is not int or item_bytes <= 0:
                    return None
                namespace_bytes += item_bytes
                if key.startswith(session_prefix):
                    session_bytes += item_bytes
            if (
                namespace_bytes + len(body_bytes) > MAX_TOOL_OBSERVATION_NAMESPACE_BYTES
                or session_bytes + len(body_bytes) > MAX_TOOL_OBSERVATION_SESSION_BYTES
            ):
                return None

            duplicate = db.query(Settings.key).filter(
                Settings.key.in_((metadata_key, body_key))
            ).first()
            if duplicate is not None:
                return None
            db.add_all([
                Settings(key=body_key, value=canonical_envelope, updated_at=now),
                Settings(key=metadata_key, value=metadata_text, updated_at=now),
            ])
        # get_db_ctx commits on exit; only then may the caller expose the ref.
        return reference
    except Exception:
        logger.warning("tool observation archive unavailable")
        return None


def _validate_observation_envelope(
    canonical_envelope: str,
    tool_name: str,
) -> tuple[bytes, dict[str, Any]] | None:
    if type(canonical_envelope) is not str or not canonical_envelope:
        return None
    if tool_name not in _OBSERVATION_FIELDS:
        return None
    try:
        body_bytes = canonical_envelope.encode("utf-8")
        if len(body_bytes) > MAX_TOOL_OBSERVATION_BYTES:
            return None
        envelope = json.loads(canonical_envelope)
        if (
            type(envelope) is not dict
            or set(envelope) != {"success", "output", "data", "error", "metadata"}
            or _canonical_json(envelope) != canonical_envelope
            or envelope.get("success") is not True
            or envelope.get("error") is not None
            or type(envelope.get("output")) is not str
        ):
            return None

        metadata = envelope.get("metadata")
        data = envelope.get("data")
        expected_stage, body_field = _OBSERVATION_FIELDS[tool_name]
        expected_output = (
            "Skill instructions loaded" if tool_name == "load_skill" else "Skill resource loaded"
        )
        if (
            type(metadata) is not dict
            or set(metadata) != {"tool_name", "duration_ms", "call_id", "kb_coverage_hint"}
            or metadata.get("tool_name") != tool_name
            or type(metadata.get("duration_ms")) is not int
            or isinstance(metadata["duration_ms"], bool)
            or metadata["duration_ms"] < 0
            or type(metadata.get("call_id")) is not str
            or not 1 <= len(metadata["call_id"]) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in metadata["call_id"])
            or metadata.get("kb_coverage_hint") is not None
            or envelope["output"] != expected_output
            or type(data) is not dict
            or set(data) != (
                {"skill_id", "content_hash", "stage", "success", body_field}
                if tool_name == "load_skill"
                else {"skill_id", "content_hash", "path", "stage", "success", body_field}
            )
            or type(data.get("stage")) is not str
            or data["stage"] != expected_stage
            or data.get("success") is not True
            or type(data.get(body_field)) is not str
            or type(data.get("skill_id")) is not str
            or _SKILL_ID_PATTERN.fullmatch(data["skill_id"]) is None
            or type(data.get("content_hash")) is not str
            or _HASH_PATTERN.fullmatch(data["content_hash"]) is None
            or (
                tool_name == "read_skill_resource"
                and (
                    type(data.get("path")) is not str
                    or not 1 <= len(data["path"]) <= 1024
                )
            )
        ):
            return None
        return body_bytes, envelope
    except (UnicodeEncodeError, TypeError, ValueError):
        return None


def _eligible_envelope_bytes(canonical_envelope: str, tool_name: str) -> bytes | None:
    validated = _validate_observation_envelope(canonical_envelope, tool_name)
    return validated[0] if validated is not None else None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _valid_scope(scope: ToolObservationScope) -> bool:
    return make_tool_observation_scope(
        scope.principal_id,
        scope.session_id,
        scope.request_id,
        scope.session_delete_epoch,
    ) == scope


def _valid_epoch(value: str) -> bool:
    return value == "" or _REQUEST_PATTERN.fullmatch(value) is not None


def _session_epoch_key(session_id: str) -> str:
    session_key = session_id or "default"
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
    return f"{_SESSION_DELETE_EPOCH_PREFIX}{digest}"


def _scope_epoch_is_current(db: Any, settings_model: Any, scope: ToolObservationScope) -> bool:
    row = db.query(settings_model.value).filter(
        settings_model.key == _session_epoch_key(scope.session_id)
    ).first()
    current = row[0] if row is not None else ""
    return (
        type(current) is str
        and _valid_epoch(current)
        and current == scope.session_delete_epoch
    )


def _parse_metadata(value: Any) -> dict[str, Any] | None:
    if type(value) is not str:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, RecursionError):
        return None
    return parsed if type(parsed) is dict else None


def recover_tool_observation(
    scope: ToolObservationScope,
    reference: str,
    *,
    byte_budget: int = MAX_TOOL_OBSERVATION_BYTES,
) -> dict[str, Any] | None:
    """Recover one committed Skill observation under the current host identity."""
    if (
        type(scope) is not ToolObservationScope
        or not _valid_scope(scope)
        or type(reference) is not str
        or _REFERENCE_PATTERN.fullmatch(reference) is None
        or type(byte_budget) is not int
        or isinstance(byte_budget, bool)
        or not 1 <= byte_budget <= MAX_TOOL_OBSERVATION_BYTES
    ):
        return None

    reference_digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
    session_digest = hashlib.sha256(scope.session_id.encode("utf-8")).hexdigest()
    metadata_key = f"{_META_PREFIX}{scope.principal_id}:{session_digest}:{reference_digest}"
    body_key = f"{_BODY_PREFIX}{reference_digest}"
    expected_metadata_keys = {
        "tool_name", "created_at", "sha256", "body_bytes", "principal_id",
        "session_digest", "request_id", "session_delete_epoch",
    }

    try:
        from app.api.common import get_db_ctx
        from app.db.models import Settings

        with get_db_ctx() as db:
            if db.get_bind().dialect.name != "sqlite":
                return None
            if not _scope_epoch_is_current(db, Settings, scope):
                return None

            rows = db.query(Settings.key, Settings.value).filter(
                Settings.key.in_((metadata_key, body_key))
            ).all()
            if len(rows) != 2:
                return None
            paired = {key: value for key, value in rows}
            if set(paired) != {metadata_key, body_key}:
                return None

            metadata_text = paired[metadata_key]
            metadata = _parse_metadata(metadata_text)
            if (
                metadata is None
                or set(metadata) != expected_metadata_keys
                or _canonical_json(metadata) != metadata_text
                or type(metadata.get("tool_name")) is not str
                or metadata["tool_name"] not in _OBSERVATION_FIELDS
                or type(metadata.get("created_at")) is not str
                or not 1 <= len(metadata["created_at"]) <= 64
                or type(metadata.get("sha256")) is not str
                or _HASH_PATTERN.fullmatch(metadata["sha256"]) is None
                or type(metadata.get("body_bytes")) is not int
                or isinstance(metadata["body_bytes"], bool)
                or not 1 <= metadata["body_bytes"] <= MAX_TOOL_OBSERVATION_BYTES
                or metadata.get("principal_id") != scope.principal_id
                or metadata.get("session_digest") != session_digest
                or type(metadata.get("request_id")) is not str
                or _REQUEST_PATTERN.fullmatch(metadata["request_id"]) is None
                or metadata.get("session_delete_epoch") != scope.session_delete_epoch
            ):
                return None

            tool_name = metadata["tool_name"]
            envelope_text = paired[body_key]
            if type(envelope_text) is not str:
                return None
            validated = _validate_observation_envelope(envelope_text, tool_name)
            if validated is None:
                return None
            envelope_bytes, envelope = validated
            if (
                len(envelope_bytes) != metadata["body_bytes"]
                or hashlib.sha256(envelope_bytes).hexdigest() != metadata["sha256"]
            ):
                return None

            expected_stage, body_field = _OBSERVATION_FIELDS[tool_name]
            data = envelope["data"]
            recovered = {
                "tool_name": tool_name,
                "request_id": metadata["request_id"],
                "skill_id": data["skill_id"],
                "content_hash": data["content_hash"],
                "stage": expected_stage,
                "body_field": body_field,
                "envelope": envelope,
            }
            if len(_canonical_json(recovered).encode("utf-8")) > byte_budget:
                return None
            return recovered
    except Exception:
        logger.warning("tool observation recovery refused")
        return None


# ═══════════════════════════════════════════
# Generic tool observations (V2 namespace)
# ═══════════════════════════════════════════


def _normalize_generic_result(result: Any) -> tuple[str, bytes] | None:
    """Validate and canonicalize one full ToolResultEnvelope-shaped result."""
    expected_keys = {"success", "output", "data", "error", "metadata"}
    if type(result) is not dict or set(result) != expected_keys:
        return None
    if type(result.get("success")) is not bool or type(result.get("output")) is not str:
        return None
    if result.get("data") is not None and type(result["data"]) is not dict:
        return None
    error = result.get("error")
    if error is not None:
        if (
            type(error) is not dict
            or set(error) != {"error_type", "error_message", "suggestion", "retryable"}
            or type(error.get("error_type")) is not str
            or not 1 <= len(error["error_type"]) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in error["error_type"])
            or type(error.get("error_message")) is not str
            or type(error.get("suggestion")) is not str
            or type(error.get("retryable")) is not bool
        ):
            return None

    metadata = result.get("metadata")
    if (
        type(metadata) is not dict
        or not {"tool_name", "duration_ms", "call_id"} <= set(metadata)
        or not set(metadata) <= _GENERIC_METADATA_KEYS
        or type(metadata.get("tool_name")) is not str
        or _TOOL_NAME_PATTERN.fullmatch(metadata["tool_name"]) is None
        or type(metadata.get("duration_ms")) is not int
        or isinstance(metadata["duration_ms"], bool)
        or metadata["duration_ms"] < 0
        or type(metadata.get("call_id")) is not str
        or len(metadata["call_id"]) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in metadata["call_id"])
    ):
        return None
    if "timestamp" in metadata:
        timestamp = metadata["timestamp"]
        if type(timestamp) is str:
            if (
                not 1 <= len(timestamp) <= 128
                or any(ord(char) < 32 or ord(char) == 127 for char in timestamp)
            ):
                return None
        elif type(timestamp) is int:
            pass
        elif type(timestamp) is float:
            if not math.isfinite(timestamp):
                return None
        else:
            return None
    if "kb_coverage_hint" in metadata and metadata["kb_coverage_hint"] is not None and (
        type(metadata["kb_coverage_hint"]) is not str
        or len(metadata["kb_coverage_hint"]) > 4096
    ):
        return None
    if "compacted" in metadata and type(metadata["compacted"]) is not bool:
        return None
    if "hwrag_tool_observation_archived" in metadata and type(
        metadata["hwrag_tool_observation_archived"]
    ) is not bool:
        return None
    if "hwrag_tool_observation_ref" in metadata and (
        type(metadata["hwrag_tool_observation_ref"]) is not str
        or _REFERENCE_PATTERN.fullmatch(metadata["hwrag_tool_observation_ref"]) is None
    ):
        return None
    if "hwrag_tool_event_ref" in metadata:
        event_ref = metadata["hwrag_tool_event_ref"]
        if (
            type(event_ref) is not dict
            or set(event_ref) != {
                "batch_id", "request_id", "sequence", "version", "content_sha256",
            }
            or type(event_ref.get("batch_id")) is not str
            or _REQUEST_PATTERN.fullmatch(event_ref["batch_id"]) is None
            or type(event_ref.get("request_id")) is not str
            or _REQUEST_PATTERN.fullmatch(event_ref["request_id"]) is None
            or type(event_ref.get("sequence")) is not int
            or isinstance(event_ref["sequence"], bool)
            or not 1 <= event_ref["sequence"] <= 64
            or type(event_ref.get("version")) is not int
            or isinstance(event_ref["version"], bool)
            or event_ref["version"] != 2
            or type(event_ref.get("content_sha256")) is not str
            or _HASH_PATTERN.fullmatch(event_ref["content_sha256"]) is None
        ):
            return None

    try:
        text = _canonical_json(result)
        body = text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None
    if not 1 <= len(body) <= MAX_GENERIC_TOOL_OBSERVATION_BYTES:
        return None
    if _CREDENTIAL_PATTERN.search(text):
        return None
    return text, body


def _sanitize_generic_args(args: Any) -> tuple[dict[str, Any] | None, bool]:
    """Keep a small JSON copy, or omit it; invalid non-dict input is refused."""
    if args is None:
        return None, True
    if type(args) is not dict:
        return None, False
    try:
        text = _canonical_json(args)
        raw = text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None, False
    if len(raw) > MAX_GENERIC_ARGS_BYTES or _CREDENTIAL_PATTERN.search(text):
        # Arguments are optional; the result body may still be archived.
        return None, True
    return json.loads(text), True


def _generic_session_digest(scope: ToolObservationScope) -> str:
    return hashlib.sha256(scope.session_id.encode("utf-8")).hexdigest()


def _generic_metadata_key(scope: ToolObservationScope, reference_digest: str) -> str:
    return (
        f"{_GENERIC_META_PREFIX}{scope.principal_id}:"
        f"{_generic_session_digest(scope)}:{reference_digest}"
    )


def _generic_body_key(reference_digest: str) -> str:
    return f"{_GENERIC_BODY_PREFIX}{reference_digest}"


def _generic_call_key(
    scope: ToolObservationScope,
    *,
    call_id: str,
    session_digest: str,
) -> str:
    call_digest = hashlib.sha256(call_id.encode("utf-8")).hexdigest()
    return (
        f"{_GENERIC_CALL_PREFIX}{scope.principal_id}:{session_digest}:"
        f"{scope.request_id}:{scope.session_delete_epoch}:{call_digest}"
    )


def _parse_generic_call_index(
    value: Any,
    *,
    call_id: str,
) -> dict[str, Any] | None:
    if type(value) is not str:
        return None
    index = _parse_metadata(value)
    if (
        index is None
        or set(index) != _GENERIC_CALL_INDEX_FIELDS
        or _canonical_json(index) != value
        or type(index.get("schema_version")) is not int
        or isinstance(index["schema_version"], bool)
        or index["schema_version"] != 2
        or type(index.get("reference")) is not str
        or _REFERENCE_PATTERN.fullmatch(index["reference"]) is None
        or type(index.get("tool_name")) is not str
        or _TOOL_NAME_PATTERN.fullmatch(index["tool_name"]) is None
        or index.get("call_id") != call_id
        or type(index.get("result_sha256")) is not str
        or _HASH_PATTERN.fullmatch(index["result_sha256"]) is None
        or type(index.get("body_bytes")) is not int
        or isinstance(index["body_bytes"], bool)
        or not 1 <= index["body_bytes"] <= MAX_GENERIC_TOOL_OBSERVATION_BYTES
    ):
        return None
    return index


def _generic_quota_allows(
    db: Any,
    settings_model: Any,
    scope: ToolObservationScope,
    new_bytes: int,
) -> bool:
    """Count every generic/summary metadata row, failing closed on corruption."""
    session_digest = _generic_session_digest(scope)
    session_prefixes = (
        f"{_GENERIC_META_PREFIX}{scope.principal_id}:{session_digest}:",
        f"{_MIDRUN_META_PREFIX}{scope.principal_id}:{session_digest}:",
    )
    rows = []
    for prefix in (_GENERIC_META_PREFIX, _MIDRUN_META_PREFIX):
        rows.extend(db.query(settings_model.key, settings_model.value).filter(
            settings_model.key.startswith(prefix, autoescape=True)
        ).all())
    if len(rows) >= MAX_GENERIC_OBSERVATIONS_NAMESPACE:
        return False

    namespace_bytes = 0
    session_bytes = 0
    session_count = 0
    for key, value in rows:
        item = _parse_metadata(value)
        item_bytes = item.get("body_bytes") if item is not None else None
        if (
            type(item_bytes) is not int
            or isinstance(item_bytes, bool)
            or item_bytes <= 0
        ):
            return False
        namespace_bytes += item_bytes
        if key.startswith(session_prefixes):
            session_bytes += item_bytes
            session_count += 1
    if session_count >= MAX_GENERIC_OBSERVATIONS_PER_SESSION:
        return False
    return (
        namespace_bytes + new_bytes <= MAX_GENERIC_TOOL_OBSERVATION_NAMESPACE_BYTES
        and session_bytes + new_bytes <= MAX_GENERIC_TOOL_OBSERVATION_SESSION_BYTES
    )


def _read_generic_record(
    db: Any,
    scope: ToolObservationScope,
    reference: str,
    *,
    byte_budget: int | None,
) -> dict[str, Any] | None:
    """Validate one committed generic record against the host scope."""
    if type(scope) is not ToolObservationScope or not _valid_scope(scope):
        return None
    if type(reference) is not str or _REFERENCE_PATTERN.fullmatch(reference) is None:
        return None
    try:
        from app.db.models import Settings
    except Exception:
        return None

    if not _scope_epoch_is_current(db, Settings, scope):
        return None
    reference_digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
    session_digest = _generic_session_digest(scope)
    metadata = db.get(Settings, _generic_metadata_key(scope, reference_digest))
    body = db.get(Settings, _generic_body_key(reference_digest))
    if metadata is None or body is None:
        return None
    metadata_text = metadata.value
    body_text = body.value
    if type(metadata_text) is not str or type(body_text) is not str:
        return None

    parsed_metadata = _parse_metadata(metadata_text)
    if (
        parsed_metadata is None
        or set(parsed_metadata) != _GENERIC_METADATA_FIELDS
        or _canonical_json(parsed_metadata) != metadata_text
        or _CREDENTIAL_PATTERN.search(metadata_text)
        or type(parsed_metadata.get("schema_version")) is not int
        or isinstance(parsed_metadata["schema_version"], bool)
        or parsed_metadata["schema_version"] != 2
        or type(parsed_metadata.get("tool_name")) is not str
        or _TOOL_NAME_PATTERN.fullmatch(parsed_metadata["tool_name"]) is None
        or parsed_metadata["tool_name"] in _GENERIC_EXCLUDED_TOOLS
        or type(parsed_metadata.get("call_id")) is not str
        or _CALL_ID_PATTERN.fullmatch(parsed_metadata["call_id"]) is None
        or (
            parsed_metadata.get("args") is not None
            and type(parsed_metadata["args"]) is not dict
        )
        or type(parsed_metadata.get("created_at")) is not str
        or not 1 <= len(parsed_metadata["created_at"]) <= 64
        or type(parsed_metadata.get("body_bytes")) is not int
        or isinstance(parsed_metadata["body_bytes"], bool)
        or not 1 <= parsed_metadata["body_bytes"] <= MAX_GENERIC_TOOL_OBSERVATION_BYTES
        or type(parsed_metadata.get("result_sha256")) is not str
        or _HASH_PATTERN.fullmatch(parsed_metadata["result_sha256"]) is None
        or parsed_metadata.get("principal_id") != scope.principal_id
        or parsed_metadata.get("session_digest") != session_digest
        or parsed_metadata.get("request_id") != scope.request_id
        or parsed_metadata.get("session_delete_epoch") != scope.session_delete_epoch
    ):
        return None

    try:
        body_bytes = body_text.encode("utf-8")
        parsed_body = json.loads(body_text)
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None
    if (
        len(body_bytes) != parsed_metadata["body_bytes"]
        or hashlib.sha256(body_bytes).hexdigest() != parsed_metadata["result_sha256"]
    ):
        return None
    normalized = _normalize_generic_result(parsed_body)
    if normalized is None or normalized[0] != body_text:
        return None
    if (
        parsed_body["metadata"]["tool_name"] != parsed_metadata["tool_name"]
        or parsed_body["metadata"]["call_id"] not in ("", parsed_metadata["call_id"])
    ):
        return None

    recovered = {
        "tool_name": parsed_metadata["tool_name"],
        "call_id": parsed_metadata["call_id"],
        "request_id": parsed_metadata["request_id"],
        "created_at": parsed_metadata["created_at"],
        "result_sha256": parsed_metadata["result_sha256"],
        "args": parsed_metadata["args"],
        "envelope": parsed_body,
    }
    if byte_budget is not None:
        try:
            encoded = _canonical_json(recovered).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            return None
        if len(encoded) > byte_budget:
            return None
    return recovered


def archive_generic_tool_observation(
    scope: ToolObservationScope | None,
    *,
    tool_name: str,
    call_id: str,
    args: dict[str, Any] | None,
    result: dict[str, Any],
    max_bytes: int,
) -> str | None:
    """Archive one bounded generic tool result and publish an opaque ref.

    Shared interface for the compaction engine::

        archive_generic_tool_observation(
            scope, *, tool_name, call_id, args, result, max_bytes,
        ) -> str | None

    ``result`` must be a complete ``ToolResultEnvelope`` shape
    (``success/output/data/error/metadata``). Both successful and failed
    results are eligible. The body is canonical JSON, limited to
    ``min(max_bytes, 256 KiB)``; credential-shaped text refuses the whole
    archive, and credential-shaped ``args`` are omitted rather than stored.
    ``scope`` is the host-pinned ``ToolObservationScope``; no model-supplied
    identity is accepted. Retrying the same ``(scope, call_id, result)``
    returns the first reference. The same call with a different result is
    refused. The reference is returned only after the transaction commits and
    a full read-back verifies owner, session, request, delete epoch and hashes.
    Skill bodies, read/archive tools and system material are never archived
    here.
    """
    if type(scope) is not ToolObservationScope or not _valid_scope(scope):
        return None
    if (
        type(tool_name) is not str
        or _TOOL_NAME_PATTERN.fullmatch(tool_name) is None
        or tool_name in _GENERIC_EXCLUDED_TOOLS
    ):
        return None
    if type(call_id) is not str or _CALL_ID_PATTERN.fullmatch(call_id) is None:
        return None
    if (
        type(max_bytes) is not int
        or isinstance(max_bytes, bool)
        or not 1 <= max_bytes <= MAX_GENERIC_TOOL_OBSERVATION_BYTES
    ):
        return None

    normalized = _normalize_generic_result(result)
    if normalized is None:
        return None
    result_text, body_bytes = normalized
    if len(body_bytes) > max_bytes:
        return None
    if (
        result["metadata"]["tool_name"] != tool_name
        or result["metadata"]["call_id"] not in ("", call_id)
    ):
        return None
    args_value, args_ok = _sanitize_generic_args(args)
    if not args_ok:
        return None

    result_sha256 = hashlib.sha256(body_bytes).hexdigest()
    session_digest = _generic_session_digest(scope)
    call_key = _generic_call_key(
        scope, call_id=call_id, session_digest=session_digest,
    )
    reference = None
    try:
        from sqlalchemy import text

        from app.api.common import get_db_ctx
        from app.db.models import Settings

        with get_db_ctx() as db:
            if db.get_bind().dialect.name != "sqlite":
                return None
            db.execute(text("BEGIN IMMEDIATE"))
            if not _scope_epoch_is_current(db, Settings, scope):
                return None

            existing = db.get(Settings, call_key)
            if existing is not None:
                index = _parse_generic_call_index(
                    existing.value,
                    call_id=call_id,
                )
                if index is None or index["result_sha256"] != result_sha256:
                    return None
                reference = index["reference"]
            else:
                if not _generic_quota_allows(db, Settings, scope, len(body_bytes)):
                    return None
                reference = secrets.token_urlsafe(32)
                reference_digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
                metadata_key = _generic_metadata_key(scope, reference_digest)
                body_key = _generic_body_key(reference_digest)
                if (
                    db.get(Settings, metadata_key) is not None
                    or db.get(Settings, body_key) is not None
                ):
                    return None
                now = datetime.datetime.now(datetime.timezone.utc)
                metadata_text = _canonical_json({
                    "schema_version": 2,
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "args": args_value,
                    "created_at": now.isoformat(),
                    "body_bytes": len(body_bytes),
                    "result_sha256": result_sha256,
                    "principal_id": scope.principal_id,
                    "session_digest": session_digest,
                    "request_id": scope.request_id,
                    "session_delete_epoch": scope.session_delete_epoch,
                })
                index_text = _canonical_json({
                    "schema_version": 2,
                    "reference": reference,
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "result_sha256": result_sha256,
                    "body_bytes": len(body_bytes),
                })
                db.add_all([
                    Settings(key=body_key, value=result_text, updated_at=now),
                    Settings(key=metadata_key, value=metadata_text, updated_at=now),
                    Settings(key=call_key, value=index_text, updated_at=now),
                ])

        if reference is None:
            return None
        # get_db_ctx committed on exit; publish only after full read-back.
        with get_db_ctx() as db:
            recovered = _read_generic_record(db, scope, reference, byte_budget=None)
        if recovered is None or recovered.get("result_sha256") != result_sha256:
            return None
        return reference
    except Exception:
        logger.warning("generic tool observation archive unavailable")
        return None


def recover_generic_tool_observation(
    ref: str,
    *,
    scope: ToolObservationScope | None,
    byte_budget: int,
) -> dict[str, Any] | None:
    """Recover one generic result under the current host scope.

    Shared interface for the compaction engine::

        recover_generic_tool_observation(ref, *, scope, byte_budget) -> dict | None

    Returns ``tool_name``, ``call_id``, ``request_id``, ``created_at``,
    ``result_sha256``, sanitized ``args`` and the original ``envelope``.
    Ownership, session, request, delete epoch, ref integrity and the complete
    UTF-8 response budget are checked before any content is returned. The
    returned envelope is untrusted historical data; this function never grants
    permission or replays the tool.
    """
    if type(scope) is not ToolObservationScope or not _valid_scope(scope):
        return None
    if type(ref) is not str or _REFERENCE_PATTERN.fullmatch(ref) is None:
        return None
    if (
        type(byte_budget) is not int
        or isinstance(byte_budget, bool)
        or not 1 <= byte_budget <= MAX_GENERIC_TOOL_OBSERVATION_READ_BYTES
    ):
        return None
    try:
        from app.api.common import get_db_ctx

        with get_db_ctx() as db:
            return _read_generic_record(db, scope, ref, byte_budget=byte_budget)
    except Exception:
        logger.warning("generic tool observation recovery refused")
        return None


# ═══════════════════════════════════════════
# Mid-run compaction summaries
# ═══════════════════════════════════════════


def _normalize_midrun_summary(
    summary_text: Any,
    observation_refs: Any,
) -> tuple[str, bytes, list[str]] | None:
    if type(summary_text) is not str or not summary_text:
        return None
    try:
        summary_bytes = summary_text.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(summary_bytes) > MAX_MIDRUN_SUMMARY_TEXT_BYTES:
        return None
    if _CREDENTIAL_PATTERN.search(summary_text):
        return None
    if type(observation_refs) is not tuple:
        return None
    if len(observation_refs) > MAX_MIDRUN_OBSERVATION_REFS:
        return None
    refs: list[str] = []
    seen: set[str] = set()
    for ref in observation_refs:
        if type(ref) is not str or _REFERENCE_PATTERN.fullmatch(ref) is None:
            return None
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    try:
        body_text = _canonical_json({
            "schema_version": 1,
            "summary_text": summary_text,
            "observation_refs": refs,
        })
        body_bytes = body_text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None
    if len(body_bytes) > MAX_MIDRUN_SUMMARY_BYTES:
        return None
    if _CREDENTIAL_PATTERN.search(body_text):
        return None
    return body_text, body_bytes, refs


def _midrun_metadata_key(scope: ToolObservationScope, reference_digest: str) -> str:
    return (
        f"{_MIDRUN_META_PREFIX}{scope.principal_id}:"
        f"{_generic_session_digest(scope)}:{reference_digest}"
    )


def _midrun_body_key(reference_digest: str) -> str:
    return f"{_MIDRUN_BODY_PREFIX}{reference_digest}"


def _midrun_key_key(
    scope: ToolObservationScope,
    idempotency_key: str,
) -> str:
    key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return (
        f"{_MIDRUN_KEY_PREFIX}{scope.principal_id}:"
        f"{_generic_session_digest(scope)}:{scope.request_id}:"
        f"{scope.session_delete_epoch}:{key_digest}"
    )


def _parse_midrun_key(value: Any) -> dict[str, Any] | None:
    if type(value) is not str:
        return None
    index = _parse_metadata(value)
    if (
        index is None
        or set(index) != _MIDRUN_KEY_FIELDS
        or _canonical_json(index) != value
        or type(index.get("schema_version")) is not int
        or isinstance(index["schema_version"], bool)
        or index["schema_version"] != 1
        or type(index.get("reference")) is not str
        or _REFERENCE_PATTERN.fullmatch(index["reference"]) is None
        or type(index.get("body_sha256")) is not str
        or _HASH_PATTERN.fullmatch(index["body_sha256"]) is None
        or type(index.get("body_bytes")) is not int
        or isinstance(index["body_bytes"], bool)
        or not 1 <= index["body_bytes"] <= MAX_MIDRUN_SUMMARY_BYTES
    ):
        return None
    return index


def _read_midrun_record(
    db: Any,
    scope: ToolObservationScope,
    reference: str,
) -> dict[str, Any] | None:
    if type(scope) is not ToolObservationScope or not _valid_scope(scope):
        return None
    if type(reference) is not str or _REFERENCE_PATTERN.fullmatch(reference) is None:
        return None
    try:
        from app.db.models import Settings
    except Exception:
        return None

    if not _scope_epoch_is_current(db, Settings, scope):
        return None
    reference_digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
    metadata = db.get(Settings, _midrun_metadata_key(scope, reference_digest))
    body = db.get(Settings, _midrun_body_key(reference_digest))
    if metadata is None or body is None:
        return None
    metadata_text = metadata.value
    body_text = body.value
    if type(metadata_text) is not str or type(body_text) is not str:
        return None

    parsed_metadata = _parse_metadata(metadata_text)
    if (
        parsed_metadata is None
        or set(parsed_metadata) != _MIDRUN_METADATA_FIELDS
        or _canonical_json(parsed_metadata) != metadata_text
        or _CREDENTIAL_PATTERN.search(metadata_text)
        or type(parsed_metadata.get("schema_version")) is not int
        or isinstance(parsed_metadata["schema_version"], bool)
        or parsed_metadata["schema_version"] != 1
        or type(parsed_metadata.get("created_at")) is not str
        or not 1 <= len(parsed_metadata["created_at"]) <= 64
        or type(parsed_metadata.get("body_bytes")) is not int
        or isinstance(parsed_metadata["body_bytes"], bool)
        or not 1 <= parsed_metadata["body_bytes"] <= MAX_MIDRUN_SUMMARY_BYTES
        or type(parsed_metadata.get("body_sha256")) is not str
        or _HASH_PATTERN.fullmatch(parsed_metadata["body_sha256"]) is None
        or parsed_metadata.get("principal_id") != scope.principal_id
        or parsed_metadata.get("session_digest") != _generic_session_digest(scope)
        or parsed_metadata.get("request_id") != scope.request_id
        or parsed_metadata.get("session_delete_epoch") != scope.session_delete_epoch
    ):
        return None

    try:
        body_bytes = body_text.encode("utf-8")
        parsed_body = json.loads(body_text)
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None
    if (
        len(body_bytes) != parsed_metadata["body_bytes"]
        or hashlib.sha256(body_bytes).hexdigest() != parsed_metadata["body_sha256"]
        or type(parsed_body) is not dict
        or set(parsed_body) != _MIDRUN_BODY_FIELDS
        or type(parsed_body.get("schema_version")) is not int
        or isinstance(parsed_body["schema_version"], bool)
        or parsed_body["schema_version"] != 1
        or type(parsed_body.get("summary_text")) is not str
        or type(parsed_body.get("observation_refs")) is not list
        or _canonical_json(parsed_body) != body_text
    ):
        return None
    normalized = _normalize_midrun_summary(
        parsed_body["summary_text"], tuple(parsed_body["observation_refs"]),
    )
    if normalized is None or normalized[0] != body_text:
        return None
    return {
        "kind": "midrun_compaction_summary",
        "summary_text": parsed_body["summary_text"],
        "observation_refs": list(normalized[2]),
        "request_id": parsed_metadata["request_id"],
        "created_at": parsed_metadata["created_at"],
        "untrusted_history": True,
    }


def store_midrun_compaction_summary(
    scope: ToolObservationScope | None,
    *,
    summary_text: str,
    observation_refs: tuple[str, ...],
    idempotency_key: str,
) -> str | None:
    """Persist one bounded summary for this request and return an opaque ref.

    Shared interface for the compaction engine::

        store_midrun_compaction_summary(
            scope, *, summary_text, observation_refs, idempotency_key,
        ) -> str | None

    This is a request-scoped persistence record for the current run, not a
    next-turn checkpoint. ``observation_refs`` are the opaque generic tool
    observation references cited by the summary. The same ``idempotency_key``
    with the same content returns the first reference; the same key with
    different content is refused. Credential-shaped summaries are refused.
    The reference is returned only after commit and read-back under the same
    owner, session, request and delete epoch.
    """
    if type(scope) is not ToolObservationScope or not _valid_scope(scope):
        return None
    if type(idempotency_key) is not str:
        return None
    try:
        idempotency_bytes = idempotency_key.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if (
        not 1 <= len(idempotency_bytes) <= 256
        or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)
    ):
        return None
    normalized = _normalize_midrun_summary(summary_text, observation_refs)
    if normalized is None:
        return None
    body_text, body_bytes, refs = normalized
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    key_key = _midrun_key_key(scope, idempotency_key)
    reference = None
    try:
        from sqlalchemy import text

        from app.api.common import get_db_ctx
        from app.db.models import Settings

        with get_db_ctx() as db:
            if db.get_bind().dialect.name != "sqlite":
                return None
            db.execute(text("BEGIN IMMEDIATE"))
            if not _scope_epoch_is_current(db, Settings, scope):
                return None

            existing = db.get(Settings, key_key)
            if existing is not None:
                index = _parse_midrun_key(existing.value)
                if (
                    index is None
                    or index["body_sha256"] != body_sha256
                    or index["body_bytes"] != len(body_bytes)
                ):
                    return None
                reference = index["reference"]
            else:
                if not _generic_quota_allows(db, Settings, scope, len(body_bytes)):
                    return None
                reference = secrets.token_urlsafe(32)
                reference_digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
                metadata_key = _midrun_metadata_key(scope, reference_digest)
                body_key = _midrun_body_key(reference_digest)
                if (
                    db.get(Settings, metadata_key) is not None
                    or db.get(Settings, body_key) is not None
                ):
                    return None
                now = datetime.datetime.now(datetime.timezone.utc)
                metadata_text = _canonical_json({
                    "schema_version": 1,
                    "created_at": now.isoformat(),
                    "body_bytes": len(body_bytes),
                    "body_sha256": body_sha256,
                    "principal_id": scope.principal_id,
                    "session_digest": _generic_session_digest(scope),
                    "request_id": scope.request_id,
                    "session_delete_epoch": scope.session_delete_epoch,
                })
                index_text = _canonical_json({
                    "schema_version": 1,
                    "reference": reference,
                    "body_sha256": body_sha256,
                    "body_bytes": len(body_bytes),
                })
                db.add_all([
                    Settings(key=body_key, value=body_text, updated_at=now),
                    Settings(key=metadata_key, value=metadata_text, updated_at=now),
                    Settings(key=key_key, value=index_text, updated_at=now),
                ])

        if reference is None:
            return None
        with get_db_ctx() as db:
            recovered = _read_midrun_record(db, scope, reference)
        if (
            recovered is None
            or recovered.get("summary_text") != summary_text
            or recovered.get("observation_refs") != refs
        ):
            return None
        return reference
    except Exception:
        logger.warning("midrun compaction summary archive unavailable")
        return None


def read_midrun_compaction_summary(
    ref: str,
    *,
    scope: ToolObservationScope | None,
) -> dict[str, Any] | None:
    """Read one request-scoped summary; never a permission or replay grant.

    Shared interface for the compaction engine::

        read_midrun_compaction_summary(ref, *, scope) -> dict | None

    Returns ``kind``, ``summary_text``, ``observation_refs``, ``request_id``,
    ``created_at`` and ``untrusted_history``. Owner, session, request, delete
    epoch, reference integrity, canonical encoding and content limits are all
    verified. Missing or changed records return ``None``.
    """
    if type(scope) is not ToolObservationScope or not _valid_scope(scope):
        return None
    if type(ref) is not str or _REFERENCE_PATTERN.fullmatch(ref) is None:
        return None
    try:
        from app.api.common import get_db_ctx

        with get_db_ctx() as db:
            return _read_midrun_record(db, scope, ref)
    except Exception:
        logger.warning("midrun compaction summary recovery refused")
        return None


def _session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def list_tool_observations(
    scope: ToolObservationScope | None,
) -> str | None:
    """Return a bounded metadata-only index for the current scoped epoch."""
    if not _valid_scope(scope):
        return None

    session_digest = _session_digest(scope.session_id)
    metadata_prefix = f"{_META_PREFIX}{scope.principal_id}:{session_digest}:"
    try:
        from sqlalchemy import LargeBinary, cast, func

        from app.api.common import get_db_ctx
        from app.db.models import Settings

        indexed: list[dict[str, Any]] = []
        body_keys: set[str] = set()
        with get_db_ctx() as db:
            if db.get_bind().dialect.name != "sqlite":
                return None
            if not _scope_epoch_is_current(db, Settings, scope):
                return None

            rows = db.query(Settings.key, Settings.value).filter(
                Settings.key.startswith(metadata_prefix, autoescape=True)
            ).order_by(
                Settings.key.asc()
            ).limit(MAX_TOOL_OBSERVATION_INDEX_SCAN + 1).all()
            if len(rows) > MAX_TOOL_OBSERVATION_INDEX_SCAN:
                return None

            for key, value in rows:
                if type(key) is not str or not key.startswith(metadata_prefix):
                    return None
                reference_digest = key[len(metadata_prefix):]
                if _DIGEST_PATTERN.fullmatch(reference_digest) is None:
                    return None
                if type(value) is not str:
                    return None
                try:
                    metadata_bytes = value.encode("utf-8")
                except UnicodeEncodeError:
                    return None
                if len(metadata_bytes) > MAX_TOOL_OBSERVATION_METADATA_BYTES:
                    return None
                metadata = _parse_metadata(value)
                if metadata is None:
                    return None

                tool_name = metadata.get("tool_name")
                created_at = metadata.get("created_at")
                body_bytes = metadata.get("body_bytes")
                body_sha256 = metadata.get("sha256")
                request_id = metadata.get("request_id")
                metadata_principal = metadata.get("principal_id")
                metadata_session = metadata.get("session_digest")
                metadata_epoch = metadata.get("session_delete_epoch")
                if (
                    metadata_principal != scope.principal_id
                    or metadata_session != session_digest
                    or type(metadata_epoch) is not str
                    or not _valid_epoch(metadata_epoch)
                    or type(request_id) is not str
                    or _IDENTIFIER_PATTERN.fullmatch(request_id) is None
                    or type(tool_name) is not str
                    or not tool_name
                    or type(body_bytes) is not int
                    or body_bytes <= 0
                    or body_bytes > MAX_TOOL_OBSERVATION_BYTES
                    or type(body_sha256) is not str
                    or _DIGEST_PATTERN.fullmatch(body_sha256) is None
                    or type(created_at) is not str
                ):
                    return None
                try:
                    tool_name_bytes = tool_name.encode("utf-8")
                    created_at_bytes = created_at.encode("utf-8")
                    created_at_time = datetime.datetime.fromisoformat(created_at)
                except (UnicodeEncodeError, ValueError):
                    return None
                if (
                    not tool_name_bytes
                    or len(tool_name_bytes) > MAX_TOOL_OBSERVATION_TOOL_NAME_BYTES
                    or len(created_at_bytes) > 64
                    or created_at_time.tzinfo is None
                    or created_at_time.utcoffset() is None
                ):
                    return None

                reference: str | None = None
                if "reference" in metadata:
                    reference = metadata.get("reference")
                    if (
                        type(reference) is not str
                        or _REFERENCE_PATTERN.fullmatch(reference) is None
                        or hashlib.sha256(reference.encode("ascii")).hexdigest()
                        != reference_digest
                    ):
                        return None

                # Valid rows from a prior deletion epoch remain unreadable and
                # are omitted; legacy rows without a stored ref remain directly
                # readable but cannot be discovered from an index.
                if metadata_epoch != scope.session_delete_epoch or reference is None:
                    continue

                body_key = f"{_BODY_PREFIX}{reference_digest}"
                body_keys.add(body_key)
                indexed.append({
                    "reference": reference,
                    "tool_name": tool_name,
                    "created_at": created_at,
                    "created_at_time": created_at_time,
                    "body_bytes": body_bytes,
                    "sha256": body_sha256,
                    "body_key": body_key,
                })

            if len(indexed) > MAX_TOOL_OBSERVATION_INDEX_ITEMS:
                return None

            if body_keys:
                # Bound selected body sizes before loading them, then validate
                # the committed pair without returning body content in the index.
                body_length = func.length(cast(Settings.value, LargeBinary))
                body_rows = db.query(Settings.key, Settings.value).filter(
                    Settings.key.in_(tuple(sorted(body_keys))),
                    body_length > 0,
                    body_length <= MAX_TOOL_OBSERVATION_BYTES,
                ).all()
                found_body_keys: set[str] = set()
                body_values: dict[str, str] = {}
                for body_key, body in body_rows:
                    if (
                        type(body_key) is not str
                        or body_key not in body_keys
                        or body_key in found_body_keys
                        or type(body) is not str
                    ):
                        return None
                    found_body_keys.add(body_key)
                    body_values[body_key] = body
                if found_body_keys != body_keys:
                    return None
                for item in indexed:
                    body = body_values[item["body_key"]]
                    try:
                        encoded_body = body.encode("utf-8")
                        decoded_body = json.loads(body)
                    except (UnicodeEncodeError, TypeError, ValueError):
                        return None
                    if (
                        len(encoded_body) != item["body_bytes"]
                        or hashlib.sha256(encoded_body).hexdigest() != item["sha256"]
                        or type(decoded_body) is not dict
                    ):
                        return None

            if not _scope_epoch_is_current(db, Settings, scope):
                return None

        indexed.sort(
            key=lambda item: (item["created_at_time"], item["reference"]),
            reverse=True,
        )
        observations = [
            {
                "reference": item["reference"],
                "tool_name": item["tool_name"],
                "created_at": item["created_at"],
                "body_bytes": item["body_bytes"],
            }
            for item in indexed
        ]
        content = _INDEX_MESSAGE_PREFIX + json.dumps(
            {"observations": observations},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            content_bytes = content.encode("utf-8")
        except UnicodeEncodeError:
            return None
        if len(content_bytes) > MAX_TOOL_OBSERVATION_INDEX_BYTES:
            return None
        return content
    except Exception:
        logger.warning("tool observation index unavailable")
        return None


def purge_session_observations(db: Any, session_id: str) -> int:
    """Delete one session's observation metadata and bodies from Settings."""
    from app.db.models import Settings

    if type(session_id) is not str or not session_id:
        return 0
    session_digest = _session_digest(session_id)
    pattern = f"{_META_PREFIX}%:{session_digest}:%"
    rows = db.query(Settings.key).filter(Settings.key.like(pattern)).all()
    meta_keys: list[str] = []
    body_keys: list[str] = []
    for (key,) in rows:
        if type(key) is not str:
            continue
        digest = key.rsplit(":", 1)[-1]
        if _HASH_PATTERN.fullmatch(digest) is None:
            continue
        meta_keys.append(key)
        body_keys.append(f"{_BODY_PREFIX}{digest}")
    deleted = 0
    if meta_keys:
        deleted += db.query(Settings).filter(Settings.key.in_(meta_keys)).delete(
            synchronize_session=False,
        )
    if body_keys:
        deleted += db.query(Settings).filter(Settings.key.in_(body_keys)).delete(
            synchronize_session=False,
        )
    return deleted

def read_tool_observation(
    reference: str,
    scope: ToolObservationScope | None,
) -> str | None:
    """Read a prior envelope only for the same principal/session/current epoch."""
    if (
        type(reference) is not str
        or _REFERENCE_PATTERN.fullmatch(reference) is None
        or not _valid_scope(scope)
    ):
        return None

    reference_digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
    session_digest = _session_digest(scope.session_id)
    metadata_key = (
        f"{_META_PREFIX}{scope.principal_id}:{session_digest}:{reference_digest}"
    )
    body_key = f"{_BODY_PREFIX}{reference_digest}"
    try:
        from app.api.common import get_db_ctx
        from app.db.models import Settings

        with get_db_ctx() as db:
            if not _scope_epoch_is_current(db, Settings, scope):
                return None
            metadata_row = db.query(Settings.value).filter(
                Settings.key == metadata_key
            ).first()
            body_row = db.query(Settings.value).filter(
                Settings.key == body_key
            ).first()
            if metadata_row is None or body_row is None:
                return None
            metadata = _parse_metadata(metadata_row[0])
            body = body_row[0]
            if metadata is None or type(body) is not str:
                return None
            if (
                metadata.get("principal_id") != scope.principal_id
                or metadata.get("session_digest") != session_digest
                or metadata.get("session_delete_epoch") != scope.session_delete_epoch
                or (
                    "reference" in metadata
                    and metadata.get("reference") != reference
                )
                or type(metadata.get("request_id")) is not str
                or _IDENTIFIER_PATTERN.fullmatch(metadata["request_id"]) is None
                or type(metadata.get("tool_name")) is not str
                or type(metadata.get("body_bytes")) is not int
                or metadata["body_bytes"] > MAX_TOOL_OBSERVATION_BYTES
            ):
                return None
            body_bytes = body.encode("utf-8")
            if (
                len(body_bytes) != metadata["body_bytes"]
                or hashlib.sha256(body_bytes).hexdigest() != metadata.get("sha256")
            ):
                return None
            decoded = json.loads(body)
            if type(decoded) is not dict:
                return None
            return body
    except Exception:
        logger.warning("tool observation read unavailable")
        return None



