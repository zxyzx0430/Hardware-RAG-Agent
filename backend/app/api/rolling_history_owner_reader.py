"""Owner-bound, read-only access to immutable rolling-history events.

This service accepts authenticated host identity from a route and reads only
while the real TOOL scope is bound. Historical bodies are untrusted data; this
module never executes old tool calls or reuses prior authorization.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Any, Literal

from app.api import rolling_history as _history
from app.api.context_events import ContextEventScope
from app.api.rolling_history import (
    RollingHistoryEvent,
    RollingHistoryReference,
    RollingHistorySkillProvenance,
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


_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 50
_MIN_CHUNK_BYTES = 4
_MAX_CHUNK_BYTES = 65536
_DEFAULT_CHUNK_BYTES = 8192
_PAGE_CURSOR_KIND = "rolling_history_owner_page"
_CHUNK_CURSOR_KIND = "rolling_history_owner_chunk"
_CURSOR_VERSION = 1
_UNAVAILABLE = "Owner rolling history is unavailable."
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_GENERATION = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SKILL_EVENT_TYPES = frozenset({"skill_instruction", "skill_resource"})
_PAGE_CURSOR_FIELDS = frozenset({
    "cursor_kind", "cursor_version", "session_sha256", "principal_hash",
    "delete_epoch", "incarnation_id", "namespace_digest", "head_sequence",
    "head_chain_sha256", "after_sequence", "after_chain_sha256", "cursor_sha256",
})
_CHUNK_CURSOR_FIELDS = frozenset({
    "cursor_kind", "cursor_version", "session_sha256", "principal_hash",
    "delete_epoch", "incarnation_id", "namespace_digest", "head_sequence",
    "head_chain_sha256", "event_id", "event_request_id", "event_sequence",
    "reference_version", "reference_content_sha256", "reference_chain_sha256",
    "body_sha256", "body_utf8_byte_length", "next_offset", "cursor_sha256",
})


class RollingHistoryOwnerReaderUnavailable(RuntimeError):
    """Sanitized failure for missing, stale, malformed, or unavailable history."""


@dataclass(frozen=True)
class RollingHistoryOwnerEvent:
    """Verified event locator and safe metadata; body text is read separately."""

    reference: RollingHistoryReference
    event_type: str
    content_utf8_byte_length: int
    has_attachments: bool
    skill_provenance_status: Literal["verified", "unknown", "not_applicable"]
    skill_provenance: RollingHistorySkillProvenance | None


@dataclass(frozen=True)
class RollingHistoryOwnerPage:
    """One bounded event page pinned to a single immutable history head."""

    events: tuple[RollingHistoryOwnerEvent, ...]
    next_cursor: dict[str, Any] | None
    head_sequence: int
    head_chain_sha256: str
    untrusted_historical_data: bool = True


@dataclass(frozen=True)
class RollingHistoryOwnerChunk:
    """One UTF-8-safe body fragment read through the authenticated owner scope."""

    reference: RollingHistoryReference
    event_type: str
    body_chunk: str
    start_offset: int
    end_offset: int
    total_utf8_bytes: int
    complete: bool
    next_cursor: dict[str, Any] | None
    skill_provenance_status: Literal["verified", "unknown", "not_applicable"]
    skill_provenance: RollingHistorySkillProvenance | None
    has_attachments: bool
    untrusted_historical_data: bool = True


@dataclass(frozen=True)
class RollingHistoryOwnerLocatorEvent:
    """Metadata-only event position backed by a persistent owner snapshot."""

    reference: str
    event_type: str
    content_utf8_byte_length: int
    has_attachments: bool
    skill_provenance_status: Literal["verified", "unknown", "not_applicable"]


@dataclass(frozen=True)
class RollingHistoryOwnerLocatorPage:
    """One metadata page and stable next position for a fixed v1 head."""

    snapshot_handle: str
    events: tuple[RollingHistoryOwnerLocatorEvent, ...]
    next_handle: str | None
    head_sequence: int
    head_chain_sha256: str
    untrusted_historical_data: bool = True


@dataclass(frozen=True)
class RollingHistoryOwnerLocatorChunk:
    """One verified, physically bounded body fragment from a stable locator."""

    reference: str
    event_type: str
    body_chunk: str
    start_offset: int
    end_offset: int
    total_utf8_bytes: int
    complete: bool
    next_handle: str | None
    skill_provenance_status: Literal["verified", "unknown", "not_applicable"]
    has_attachments: bool
    untrusted_historical_data: bool = True


def page_rolling_history_owner_events(
    authenticated_user: dict | None,
    *,
    cursor: dict[str, Any] | None = None,
    limit: int = _DEFAULT_PAGE_SIZE,
    expected_owner_identity: RollingHistoryOwnerIdentity | None = None,
) -> RollingHistoryOwnerPage:
    """Read a bounded page for the route's authenticated user and current TOOL scope.

    A cursor is only a continuation locator. The current TOOL scope and database
    owner binding are revalidated inside a read transaction on every call.
    A trusted host may also provide its captured owner identity to pin this read
    to the same session, request, principal, deletion epoch, and incarnation.
    Omitting the pin preserves legacy calls without that cross-call identity check.
    """
    if (type(limit) is not int or not 1 <= limit <= _MAX_PAGE_SIZE
            or (cursor is not None and type(cursor) is not dict)):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)

    def operation(handle, scope, identity):
        prefix = _history._scope_prefix(scope)
        head_sequence, head_chain = _read_verified_head(handle, scope, prefix)
        owner_binding = _owner_binding(scope, identity)
        cursor_key = _cursor_key(authenticated_user)

        if cursor is None:
            after_sequence = 0
            after_chain = _history._genesis_chain(scope)
        else:
            parsed = _parse_page_cursor(cursor, owner_binding, cursor_key)
            if (parsed["head_sequence"] != head_sequence
                    or parsed["head_chain_sha256"] != head_chain):
                raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
            after_sequence = parsed["after_sequence"]
            after_chain = parsed["after_chain_sha256"]
            anchor = _load_verified_event(
                handle, scope, prefix, after_sequence, verify_parent=True,
            )
            if (anchor is None
                    or anchor.reference.chain_sha256 != after_chain):
                raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)

        if after_sequence > head_sequence:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        count = min(limit, head_sequence - after_sequence)
        events: list[RollingHistoryOwnerEvent] = []
        last_chain = after_chain
        for sequence in range(after_sequence + 1, after_sequence + count + 1):
            event = _load_verified_event(
                handle, scope, prefix, sequence, verify_parent=True,
            )
            if event is None or event.reference.sequence != sequence:
                raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
            summary = _event_summary(event, scope, identity)
            events.append(summary)
            last_chain = event.reference.chain_sha256

        next_sequence = after_sequence + count
        next_cursor = None
        if next_sequence < head_sequence:
            next_cursor = _seal_cursor({
                "cursor_kind": _PAGE_CURSOR_KIND,
                "cursor_version": _CURSOR_VERSION,
                **owner_binding,
                "head_sequence": head_sequence,
                "head_chain_sha256": head_chain,
                "after_sequence": next_sequence,
                "after_chain_sha256": last_chain,
            }, cursor_key)
        return RollingHistoryOwnerPage(
            events=tuple(events),
            next_cursor=next_cursor,
            head_sequence=head_sequence,
            head_chain_sha256=head_chain,
        )

    return _run_owner_read(
        authenticated_user,
        operation,
        expected_owner_identity=expected_owner_identity,
    )


def read_rolling_history_owner_event_chunk(
    authenticated_user: dict | None,
    reference: RollingHistoryReference,
    *,
    cursor: dict[str, Any] | None = None,
    byte_budget: int = _DEFAULT_CHUNK_BYTES,
    expected_owner_identity: RollingHistoryOwnerIdentity | None = None,
) -> RollingHistoryOwnerChunk:
    """Read one byte-bounded chunk from an exact immutable event reference.

    Continuation cursors are bound to the reference, owner namespace, and
    history head. The event and complete body are revalidated in the same owner
    READ transaction before each chunk is returned.
    A trusted host may provide the captured owner identity to pin this read to
    the same session, request, principal, deletion epoch, and incarnation.
    Omitting the pin preserves legacy calls without that cross-call identity check.
    """
    if (type(byte_budget) is not int
            or not _MIN_CHUNK_BYTES <= byte_budget <= _MAX_CHUNK_BYTES
            or (cursor is not None and type(cursor) is not dict)):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)

    def operation(handle, scope, identity):
        if not _valid_reference(reference, scope):
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        prefix = _history._scope_prefix(scope)
        head_sequence, head_chain = _read_verified_head(handle, scope, prefix)
        owner_binding = _owner_binding(scope, identity)
        cursor_key = _cursor_key(authenticated_user)
        if reference.sequence > head_sequence:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)

        event = _load_verified_event(
            handle, scope, prefix, reference.sequence, verify_parent=True,
        )
        if event is None or event.reference != reference:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        try:
            body = event.content.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE) from None
        body_sha256 = hashlib.sha256(body).hexdigest()
        provenance_status, provenance = _skill_provenance(event, body, scope, identity)

        if cursor is None:
            start = 0
        else:
            parsed = _parse_chunk_cursor(cursor, owner_binding, reference, cursor_key)
            if (parsed["head_sequence"] != head_sequence
                    or parsed["head_chain_sha256"] != head_chain
                    or parsed["body_sha256"] != body_sha256
                    or parsed["body_utf8_byte_length"] != len(body)):
                raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
            start = parsed["next_offset"]

        if (start < 0 or start > len(body) or not _is_utf8_boundary(body, start)
                or (cursor is not None and start == len(body))):
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        end = min(start + byte_budget, len(body))
        while end < len(body) and end > start and body[end] & 0xC0 == 0x80:
            end -= 1
        if body and end <= start:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        try:
            body_chunk = body[start:end].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE) from None

        complete = end == len(body)
        next_cursor = None
        if not complete:
            if end <= start:
                raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
            next_cursor = _seal_cursor({
                "cursor_kind": _CHUNK_CURSOR_KIND,
                "cursor_version": _CURSOR_VERSION,
                **owner_binding,
                "head_sequence": head_sequence,
                "head_chain_sha256": head_chain,
                "event_id": reference.event_id,
                "event_request_id": reference.request_id,
                "event_sequence": reference.sequence,
                "reference_version": reference.version,
                "reference_content_sha256": reference.content_sha256,
                "reference_chain_sha256": reference.chain_sha256,
                "body_sha256": body_sha256,
                "body_utf8_byte_length": len(body),
                "next_offset": end,
            }, cursor_key)
        return RollingHistoryOwnerChunk(
            reference=reference,
            event_type=event.event_type,
            body_chunk=body_chunk,
            start_offset=start,
            end_offset=end,
            total_utf8_bytes=len(body),
            complete=complete,
            next_cursor=next_cursor,
            skill_provenance_status=provenance_status,
            skill_provenance=provenance,
            has_attachments=bool(event.attachments),
        )

    return _run_owner_read(
        authenticated_user,
        operation,
        expected_owner_identity=expected_owner_identity,
    )


def _run_owner_read(
    authenticated_user: dict | None,
    callback,
    *,
    expected_owner_identity: RollingHistoryOwnerIdentity | None = None,
):
    """Run a synchronous callback with the current authenticated TOOL scope."""
    if type(authenticated_user) is not dict:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    if (expected_owner_identity is not None
            and type(expected_owner_identity) is not RollingHistoryOwnerIdentity):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    try:
        with bootstrap_rolling_history_owner_scope(
            authenticated_user, ScopeOrigin.TOOL,
        ):
            scope = get_tool_event_scope()
            identity = get_rolling_history_owner_identity()
            _owner_binding(scope, identity)

            def read_operation(handle):
                if expected_owner_identity is None:
                    return callback(handle, scope, identity)

                # run_rolling_history_owner_operation reaches this callback only
                # after revalidating the owner binding inside its READ transaction.
                current_scope = get_tool_event_scope()
                current_identity = get_rolling_history_owner_identity()
                _owner_binding(current_scope, current_identity)
                _require_expected_owner_identity(
                    expected_owner_identity,
                    current_scope,
                    current_identity,
                )
                return callback(handle, current_scope, current_identity)

            return run_rolling_history_owner_operation(
                authenticated_user,
                OperationMode.READ,
                read_operation,
            )
    except RollingHistoryOwnerReaderUnavailable:
        raise
    except Exception:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE) from None


def _require_expected_owner_identity(
    expected: RollingHistoryOwnerIdentity,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
) -> None:
    """Require a trusted host identity pin to match the transaction's owner."""
    if type(expected) is not RollingHistoryOwnerIdentity:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)

    expected_fields = (
        expected.session_id,
        expected.request_id,
        expected.principal_hash,
        expected.delete_epoch,
        expected.incarnation_id,
        expected.canonical_namespace_digest,
    )
    current_fields = (
        identity.session_id,
        identity.request_id,
        identity.principal_hash,
        identity.delete_epoch,
        identity.incarnation_id,
        identity.canonical_namespace_digest,
    )
    if (any(type(value) is not str for value in expected_fields)
            or expected_fields != current_fields
            or expected.session_id != scope.session_id
            or expected.request_id != scope.request_id
            or expected.principal_hash != scope.principal_hash
            or expected.delete_epoch != scope.session_epoch):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)


def _owner_binding(
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
) -> dict[str, str]:
    if (type(scope) is not ContextEventScope
            or type(identity) is not RollingHistoryOwnerIdentity
            or type(scope.session_id) is not str
            or type(scope.request_id) is not str
            or _REQUEST_ID.fullmatch(scope.request_id) is None
            or type(scope.principal_hash) is not str
            or _HASH.fullmatch(scope.principal_hash) is None
            or type(scope.session_epoch) is not str
            or (scope.session_epoch != "" and _GENERATION.fullmatch(scope.session_epoch) is None)
            or identity.session_id != scope.session_id
            or identity.request_id != scope.request_id
            or identity.principal_hash != scope.principal_hash
            or identity.delete_epoch != scope.session_epoch
            or type(identity.incarnation_id) is not str
            or _GENERATION.fullmatch(identity.incarnation_id) is None
            or type(identity.canonical_namespace_digest) is not str
            or _HASH.fullmatch(identity.canonical_namespace_digest) is None):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    return {
        "session_sha256": hashlib.sha256(scope.session_id.encode("utf-8")).hexdigest(),
        "principal_hash": identity.principal_hash,
        "delete_epoch": identity.delete_epoch,
        "incarnation_id": identity.incarnation_id,
        "namespace_digest": identity.canonical_namespace_digest,
    }


def _historical_scope(
    owner_scope: ContextEventScope,
    raw_event: str,
    expected_sequence: int,
) -> ContextEventScope:
    try:
        value = json.loads(raw_event)
    except Exception:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE) from None
    if (type(value) is not dict
            or type(value.get("request_id")) is not str
            or _REQUEST_ID.fullmatch(value["request_id"]) is None
            or type(value.get("sequence")) is not int
            or value["sequence"] != expected_sequence):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    # Request ID is taken from the immutable event envelope. All owner and
    # namespace fields remain those of the current, transaction-validated scope.
    return ContextEventScope(
        owner_scope.session_id,
        value["request_id"],
        owner_scope.principal_hash,
        owner_scope.session_epoch,
    )


def _load_verified_event(
    handle,
    owner_scope: ContextEventScope,
    prefix: str,
    sequence: int,
    *,
    verify_parent: bool,
) -> RollingHistoryEvent | None:
    if type(sequence) is not int or sequence < 1:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    event_key = _history._event_key(prefix, sequence)
    raw_event = _history._owner_history_read_setting(handle, event_key)
    if raw_event is None:
        return None
    event_scope = _historical_scope(owner_scope, raw_event, sequence)
    event = _history._owner_history_load_event_row(
        handle,
        event_scope,
        prefix,
        raw_event,
        sequence,
        verify_parent=False,
    )
    if not verify_parent:
        return event

    try:
        value = json.loads(raw_event)
    except Exception:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE) from None
    previous_chain = value.get("previous_chain_sha256") if type(value) is dict else None
    if sequence == 1:
        expected_previous_chain = _history._genesis_chain(owner_scope)
    else:
        raw_parent = _history._owner_history_read_setting(
            handle,
            _history._event_key(prefix, sequence - 1),
        )
        if raw_parent is None:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        parent_scope = _historical_scope(owner_scope, raw_parent, sequence - 1)
        parent = _history._owner_history_load_event_row(
            handle,
            parent_scope,
            prefix,
            raw_parent,
            sequence - 1,
            verify_parent=False,
        )
        expected_previous_chain = parent.reference.chain_sha256
    if previous_chain != expected_previous_chain:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    return event


def _read_verified_head(
    handle,
    scope: ContextEventScope,
    prefix: str,
) -> tuple[int, str]:
    """Validate head metadata and latest row with per-event request bindings.

    The legacy head helper passes one request ID through its parent check. This
    reader resolves each immutable row's own request ID so Skill provenance from
    earlier requests remains verifiable in the same owner namespace.
    """
    head_raw = _history._owner_history_read_setting(handle, prefix + "meta:head")
    if head_raw is None:
        head_sequence = 0
        head_chain = _history._genesis_chain(scope)
    else:
        try:
            value = json.loads(head_raw)
        except Exception:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE) from None
        if (type(value) is not dict
                or set(value) != {
                    "schema_version", "kind", "scope", "last_sequence", "chain_sha256",
                }
                or type(value["schema_version"]) is not int
                or value["schema_version"] != _history._SCHEMA_VERSION
                or value["kind"] != "history_head"
                or value["scope"] != _history._identity(scope)
                or type(value["last_sequence"]) is not int
                or value["last_sequence"] < 0
                or type(value["chain_sha256"]) is not str
                or _HASH.fullmatch(value["chain_sha256"]) is None
                or _history._canonical(value) != head_raw):
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        head_sequence = value["last_sequence"]
        head_chain = value["chain_sha256"]

    event_prefix = prefix + "event:"
    latest_rows = _history._owner_history_rows(
        handle,
        "SELECT key FROM settings WHERE key >= ? AND key < ? ORDER BY key DESC LIMIT 1",
        (event_prefix, prefix + "event;"),
        ("key",),
    )
    if not latest_rows:
        observed_sequence = 0
        observed_chain = _history._genesis_chain(scope)
    else:
        latest_key = latest_rows[0][0]
        if type(latest_key) is not str or not latest_key.startswith(event_prefix):
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        suffix = latest_key[len(event_prefix):]
        if not suffix.isdigit():
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        observed_sequence = int(suffix)
        if _history._event_key(prefix, observed_sequence) != latest_key:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        event = _load_verified_event(
            handle, scope, prefix, observed_sequence, verify_parent=True,
        )
        if event is None:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        observed_chain = event.reference.chain_sha256

    if observed_sequence != head_sequence or observed_chain != head_chain:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    return head_sequence, head_chain


def _event_summary(
    event: RollingHistoryEvent,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
) -> RollingHistoryOwnerEvent:
    try:
        body = event.content.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE) from None
    status, provenance = _skill_provenance(event, body, scope, identity)
    return RollingHistoryOwnerEvent(
        reference=event.reference,
        event_type=event.event_type,
        content_utf8_byte_length=len(body),
        has_attachments=bool(event.attachments),
        skill_provenance_status=status,
        skill_provenance=provenance,
    )


def _skill_provenance(
    event: RollingHistoryEvent,
    body: bytes,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
) -> tuple[Literal["verified", "unknown", "not_applicable"], RollingHistorySkillProvenance | None]:
    if event.event_type not in _SKILL_EVENT_TYPES:
        if event.skill_provenance is not None:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
        return "not_applicable", None
    provenance = event.skill_provenance
    if provenance is None:
        return "unknown", None
    if type(provenance) is not RollingHistorySkillProvenance:
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    binding = provenance.request_binding
    if (provenance.content_sha256 != hashlib.sha256(body).hexdigest()
            or provenance.content_utf8_byte_length != len(body)
            or binding.principal_hash != identity.principal_hash
            or binding.session_id != scope.session_id
            or binding.request_id != event.reference.request_id
            or binding.session_delete_epoch != identity.delete_epoch):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    return "verified", provenance


def _valid_reference(
    reference: RollingHistoryReference,
    scope: ContextEventScope,
) -> bool:
    return (
        type(reference) is RollingHistoryReference
        and type(reference.event_id) is str
        and _HASH.fullmatch(reference.event_id) is not None
        and type(reference.session_id) is str
        and reference.session_id == scope.session_id
        and type(reference.request_id) is str
        and _REQUEST_ID.fullmatch(reference.request_id) is not None
        and type(reference.sequence) is int
        and reference.sequence >= 1
        and type(reference.version) is int
        and reference.version >= 1
        and type(reference.content_sha256) is str
        and _HASH.fullmatch(reference.content_sha256) is not None
        and type(reference.chain_sha256) is str
        and _HASH.fullmatch(reference.chain_sha256) is not None
    )


def _cursor_key(authenticated_user: dict) -> bytes:
    provider = authenticated_user.get("provider")
    api_key = authenticated_user.get("api_key")
    if (type(provider) is not str or not provider
            or type(api_key) is not str or not api_key):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    secret = provider.encode("utf-8") + b"\0" + api_key.encode("utf-8")
    return hashlib.sha256(b"rolling-history-owner-cursor-key-v1\0" + secret).digest()


def _cursor_digest(payload: dict[str, Any], key: bytes) -> str:
    encoded = _history._canonical(payload).encode("utf-8")
    return hmac.new(
        key,
        b"rolling-history-owner-reader-cursor-v1\0" + encoded,
        hashlib.sha256,
    ).hexdigest()


def _seal_cursor(payload: dict[str, Any], key: bytes) -> dict[str, Any]:
    return {**payload, "cursor_sha256": _cursor_digest(payload, key)}


def _open_cursor(
    raw: dict[str, Any],
    kind: str,
    fields: frozenset[str],
    key: bytes,
) -> dict[str, Any]:
    if (type(raw) is not dict or set(raw) != fields
            or raw.get("cursor_kind") != kind
            or type(raw.get("cursor_version")) is not int
            or raw["cursor_version"] != _CURSOR_VERSION
            or type(raw.get("cursor_sha256")) is not str
            or _HASH.fullmatch(raw["cursor_sha256"]) is None):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    payload = {key_name: value for key_name, value in raw.items() if key_name != "cursor_sha256"}
    if not hmac.compare_digest(_cursor_digest(payload, key), raw["cursor_sha256"]):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    return payload


def _parse_page_cursor(
    raw: dict[str, Any],
    owner_binding: dict[str, str],
    key: bytes,
) -> dict[str, Any]:
    payload = _open_cursor(raw, _PAGE_CURSOR_KIND, _PAGE_CURSOR_FIELDS, key)
    _validate_cursor_owner(payload, owner_binding)
    if (type(payload["head_sequence"]) is not int
            or payload["head_sequence"] < 1
            or type(payload["after_sequence"]) is not int
            or payload["after_sequence"] < 1
            or payload["after_sequence"] >= payload["head_sequence"]
            or type(payload["head_chain_sha256"]) is not str
            or _HASH.fullmatch(payload["head_chain_sha256"]) is None
            or type(payload["after_chain_sha256"]) is not str
            or _HASH.fullmatch(payload["after_chain_sha256"]) is None):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    return payload


def _parse_chunk_cursor(
    raw: dict[str, Any],
    owner_binding: dict[str, str],
    reference: RollingHistoryReference,
    key: bytes,
) -> dict[str, Any]:
    payload = _open_cursor(raw, _CHUNK_CURSOR_KIND, _CHUNK_CURSOR_FIELDS, key)
    _validate_cursor_owner(payload, owner_binding)
    if (type(payload["head_sequence"]) is not int
            or payload["head_sequence"] < reference.sequence
            or type(payload["head_chain_sha256"]) is not str
            or _HASH.fullmatch(payload["head_chain_sha256"]) is None
            or payload["event_id"] != reference.event_id
            or payload["event_request_id"] != reference.request_id
            or type(payload["event_sequence"]) is not int
            or payload["event_sequence"] != reference.sequence
            or type(payload["reference_version"]) is not int
            or payload["reference_version"] != reference.version
            or payload["reference_content_sha256"] != reference.content_sha256
            or payload["reference_chain_sha256"] != reference.chain_sha256
            or type(payload["body_sha256"]) is not str
            or _HASH.fullmatch(payload["body_sha256"]) is None
            or type(payload["body_utf8_byte_length"]) is not int
            or payload["body_utf8_byte_length"] <= 0
            or type(payload["next_offset"]) is not int
            or not 0 < payload["next_offset"] < payload["body_utf8_byte_length"]):
        raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)
    return payload


def _validate_cursor_owner(
    payload: dict[str, Any],
    owner_binding: dict[str, str],
) -> None:
    for name, expected in owner_binding.items():
        value = payload.get(name)
        if type(value) is not str or value != expected:
            raise RollingHistoryOwnerReaderUnavailable(_UNAVAILABLE)


def _is_utf8_boundary(body: bytes, offset: int) -> bool:
    return (
        type(offset) is int
        and 0 <= offset <= len(body)
        and (offset == len(body) or body[offset] & 0xC0 != 0x80)
    )


def open_rolling_history_owner_snapshot(
    authenticated_user: dict | None,
    *,
    snapshot_handle: str | None = None,
    expected_owner_identity: RollingHistoryOwnerIdentity,
) -> str:
    """Open or reopen a durable locator through the route's current TOOL owner."""
    from app.api.rolling_history_locator_store import open_owner_history_snapshot

    return open_owner_history_snapshot(
        authenticated_user,
        snapshot_handle=snapshot_handle,
        expected_owner_identity=expected_owner_identity,
    )


def page_rolling_history_owner_snapshot(
    authenticated_user: dict | None,
    snapshot_handle: str,
    *,
    limit: int,
    expected_owner_identity: RollingHistoryOwnerIdentity,
) -> RollingHistoryOwnerLocatorPage:
    """Read a stable metadata page, re-resolving its locator in the owner READ."""
    from app.api.rolling_history_locator_store import read_owner_history_page

    return read_owner_history_page(
        authenticated_user,
        snapshot_handle,
        limit=limit,
        expected_owner_identity=expected_owner_identity,
    )


def read_rolling_history_owner_locator_chunk(
    authenticated_user: dict | None,
    event_handle: str,
    *,
    cursor_handle: str | None = None,
    byte_budget: int = _DEFAULT_CHUNK_BYTES,
    expected_owner_identity: RollingHistoryOwnerIdentity,
) -> RollingHistoryOwnerLocatorChunk:
    """Read a verified body chunk without materializing the inline v1 event row."""
    from app.api.rolling_history_locator_store import read_owner_history_chunk

    return read_owner_history_chunk(
        authenticated_user,
        event_handle,
        cursor_handle=cursor_handle,
        byte_budget=byte_budget,
        expected_owner_identity=expected_owner_identity,
    )
