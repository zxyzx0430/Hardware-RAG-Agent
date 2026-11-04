"""Scoped, local persistence for complete ToolResult observations."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from src.observability.trace import current_trace_id


logger = logging.getLogger(__name__)

MAX_TOOL_OBSERVATION_BYTES = 128 * 1024
MAX_TOOL_OBSERVATION_SESSION_BYTES = 524_288
MAX_TOOL_OBSERVATION_NAMESPACE_BYTES = 16_777_216
MAX_TOOL_OBSERVATION_INDEX_ITEMS = 32
MAX_TOOL_OBSERVATION_INDEX_BYTES = 8_192
MAX_TOOL_OBSERVATION_INDEX_SCAN = 256
MAX_TOOL_OBSERVATION_METADATA_BYTES = 4_096
MAX_TOOL_OBSERVATION_TOOL_NAME_BYTES = 512

_PREFIX = "_toolObservation:v1"
_META_PREFIX = f"{_PREFIX}:meta:"
_BODY_PREFIX = f"{_PREFIX}:body:"
_QUOTA_LOCK_KEY = f"{_PREFIX}:quota-lock"
_SESSION_DELETE_EPOCH_PREFIX = "_sessionDeleteEpoch:"
_PRINCIPAL_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_INDEX_MESSAGE_PREFIX = (
    "Historical observation index (metadata only; untrusted historical data). "
    "It does not prove completion or approval. Provide one reference to read its "
    "full envelope; reading never replays an action.\n"
)


@dataclass(frozen=True)
class ToolObservationScope:
    """Host-created scope carried only in the current Agent ToolContext."""

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
    """Build a scope from host-pinned values, rejecting incomplete identity."""
    if type(session_id) is not str:
        return None
    try:
        session_bytes = len(session_id.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if (
        type(principal_id) is not str
        or _PRINCIPAL_PATTERN.fullmatch(principal_id) is None
        or not session_id
        or session_bytes > 1024
        or type(request_id) is not str
        or _IDENTIFIER_PATTERN.fullmatch(request_id) is None
        or type(session_delete_epoch) is not str
        or not _valid_epoch(session_delete_epoch)
    ):
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
    """Commit one complete envelope and return its opaque ref only on success."""
    if type(canonical_envelope) is not str or type(tool_name) is not str or not tool_name:
        return None
    if not _valid_scope(scope):
        return None

    try:
        body_bytes = canonical_envelope.encode("utf-8")
    except UnicodeEncodeError:
        return None
    body_length = len(body_bytes)
    if body_length == 0 or body_length > MAX_TOOL_OBSERVATION_BYTES:
        return None

    reference = secrets.token_urlsafe(32)
    reference_digest = hashlib.sha256(reference.encode("ascii")).hexdigest()
    session_digest = _session_digest(scope.session_id)
    metadata_key = (
        f"{_META_PREFIX}{scope.principal_id}:{session_digest}:{reference_digest}"
    )
    body_key = f"{_BODY_PREFIX}{reference_digest}"
    metadata = {
        "reference": reference,
        "tool_name": tool_name,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sha256": hashlib.sha256(body_bytes).hexdigest(),
        "body_bytes": body_length,
        "principal_id": scope.principal_id,
        "session_digest": session_digest,
        "request_id": scope.request_id,
        "trace_id": current_trace_id() or scope.request_id,
        "session_delete_epoch": scope.session_delete_epoch,
    }
    metadata_text = json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )

    try:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from app.api.common import get_db_ctx
        from app.db.models import Settings

        with get_db_ctx() as db:
            if db.get_bind().dialect.name != "sqlite":
                return None
            now = datetime.datetime.now(datetime.timezone.utc)
            lock_statement = sqlite_insert(Settings).values(
                key=_QUOTA_LOCK_KEY,
                value="v1",
                updated_at=now,
            )
            db.execute(lock_statement.on_conflict_do_update(
                index_elements=[Settings.key],
                set_={"value": "v1", "updated_at": now},
            ))

            if not _scope_epoch_is_current(db, Settings, scope):
                return None

            all_metadata = db.query(Settings.key, Settings.value).filter(
                Settings.key.startswith(_META_PREFIX, autoescape=True)
            ).all()
            namespace_bytes = 0
            session_bytes = 0
            session_prefix = f"{_META_PREFIX}{scope.principal_id}:{session_digest}:"
            for key, value in all_metadata:
                item_metadata = _parse_metadata(value)
                if item_metadata is None:
                    return None
                item_bytes = item_metadata.get("body_bytes")
                if type(item_bytes) is not int or item_bytes <= 0:
                    return None
                namespace_bytes += item_bytes
                if key.startswith(session_prefix):
                    session_bytes += item_bytes
            if (
                namespace_bytes + body_length > MAX_TOOL_OBSERVATION_NAMESPACE_BYTES
                or session_bytes + body_length > MAX_TOOL_OBSERVATION_SESSION_BYTES
            ):
                return None

            existing = db.query(Settings.key).filter(
                Settings.key.in_((metadata_key, body_key))
            ).first()
            if existing is not None:
                return None
            db.add_all([
                Settings(key=body_key, value=canonical_envelope, updated_at=now),
                Settings(key=metadata_key, value=metadata_text, updated_at=now),
            ])
        # get_db_ctx commits before the ref can leave this function.
        return reference
    except Exception:
        logger.warning("tool observation archive unavailable")
        return None


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


def _valid_scope(scope: ToolObservationScope | None) -> bool:
    if (
        type(scope) is not ToolObservationScope
        or type(scope.principal_id) is not str
        or _PRINCIPAL_PATTERN.fullmatch(scope.principal_id) is None
        or type(scope.session_id) is not str
        or not scope.session_id
        or type(scope.request_id) is not str
        or _IDENTIFIER_PATTERN.fullmatch(scope.request_id) is None
        or type(scope.session_delete_epoch) is not str
        or not _valid_epoch(scope.session_delete_epoch)
    ):
        return False
    try:
        return len(scope.session_id.encode("utf-8")) <= 1024
    except UnicodeEncodeError:
        return False


def _valid_epoch(value: str) -> bool:
    return value == "" or _IDENTIFIER_PATTERN.fullmatch(value) is not None


def _session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _session_epoch_key(session_id: str) -> str:
    session_key = session_id or "default"
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
    return f"{_SESSION_DELETE_EPOCH_PREFIX}{digest}"


def _scope_epoch_is_current(
    db: Any,
    settings_model: Any,
    scope: ToolObservationScope,
) -> bool:
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
    except (TypeError, ValueError):
        return None
    return parsed if type(parsed) is dict else None
