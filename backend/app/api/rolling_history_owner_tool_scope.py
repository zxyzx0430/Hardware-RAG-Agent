"""Host-only async capability for owner-bound rolling-history reads.

A trusted host must enter this scope while its TOOL archive ContextVar is bound,
using the authenticated user supplied by the host. The yielded capability only
exposes bounded page and chunk reads; it never accepts an owner identity from a
model or client. Historical bodies remain untrusted data.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Literal, TypeVar

from app.api.context_events import ContextEventScope
from app.api.rolling_history import RollingHistoryReference
from app.api.rolling_history_owner_reader import (
    RollingHistoryOwnerChunk,
    RollingHistoryOwnerEvent,
    RollingHistoryOwnerLocatorChunk,
    RollingHistoryOwnerLocatorEvent,
    RollingHistoryOwnerLocatorPage,
    RollingHistoryOwnerPage,
    open_rolling_history_owner_snapshot,
    page_rolling_history_owner_events,
    page_rolling_history_owner_snapshot,
    read_rolling_history_owner_event_chunk,
    read_rolling_history_owner_locator_chunk,
)
from app.api.rolling_history_owner_scope import (
    RollingHistoryOwnerIdentity,
    ScopeOrigin,
    bootstrap_rolling_history_owner_scope,
    get_rolling_history_owner_identity,
)
from app.api.tool_events import get_tool_event_scope


_UNAVAILABLE = "Owner rolling history tool capability is unavailable."
_OPAQUE_HANDLE_LENGTH = 43
_OPAQUE_HANDLE_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)
_OPAQUE_MAX_READS = 512
_OPAQUE_MAX_PAGE_EVENT_BUDGET = 4096
_OPAQUE_MAX_CHUNK_BYTE_BUDGET = 1024 * 1024
_OPAQUE_MAX_CHUNK_CALL_BYTES = 65536
_OPAQUE_MAX_HANDLE_COUNT = 4096
_OPAQUE_MAX_HANDLE_STATE_BYTES = 1024 * 1024
_OPAQUE_MAX_ENTRY_BYTES = 2048
_OPAQUE_ENTRY_OVERHEAD_BYTES = 256
_PAGE_CURSOR_KIND = "rolling_history_owner_page"
_CHUNK_CURSOR_KIND = "rolling_history_owner_chunk"
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
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_OpaqueHandleKind = Literal["reference", "page_cursor", "chunk_cursor"]
_T = TypeVar("_T")


class RollingHistoryOwnerToolScopeUnavailable(PermissionError):
    """Sanitized failure for missing, revoked, or stale host tool authority."""


def _unavailable() -> RollingHistoryOwnerToolScopeUnavailable:
    return RollingHistoryOwnerToolScopeUnavailable(_UNAVAILABLE)


@dataclass(frozen=True)
class _OpaqueHandleEntry:
    kind: _OpaqueHandleKind
    scope_fields: tuple[str, str, str, str]
    owner_fields: tuple[str, str, str, str, str, str]
    reference: RollingHistoryReference | None
    raw_cursor: dict[str, Any] | None
    accounted_bytes: int


@dataclass(frozen=True)
class RollingHistoryOwnerToolEvent:
    """Identity-free event metadata with a host-mapped opaque reference."""

    reference: str
    event_type: str
    content_utf8_byte_length: int
    has_attachments: bool
    skill_provenance_status: Literal["verified", "unknown", "not_applicable"]


@dataclass(frozen=True)
class RollingHistoryOwnerToolPage:
    """Model-facing page; its continuation and event locators are opaque handles."""

    snapshot_handle: str
    events: tuple[RollingHistoryOwnerToolEvent, ...]
    next_cursor: str | None
    untrusted_historical_data: bool = True


@dataclass(frozen=True)
class RollingHistoryOwnerToolChunk:
    """Model-facing chunk with opaque locators and no owner-bound references."""

    reference: str
    event_type: str
    body_chunk: str
    start_offset: int
    end_offset: int
    total_utf8_bytes: int
    complete: bool
    next_cursor: str | None
    skill_provenance_status: Literal["verified", "unknown", "not_applicable"]
    has_attachments: bool
    untrusted_historical_data: bool = True


def _scope_fields(scope: ContextEventScope) -> tuple[str, str, str, str]:
    if type(scope) is not ContextEventScope:
        raise _unavailable()
    fields = (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
    if any(type(value) is not str for value in fields):
        raise _unavailable()
    return fields


def _identity_matches_scope(
    identity: RollingHistoryOwnerIdentity,
    scope_fields: tuple[str, str, str, str],
) -> bool:
    if type(identity) is not RollingHistoryOwnerIdentity:
        return False
    identity_fields = (
        identity.session_id,
        identity.request_id,
        identity.principal_hash,
        identity.delete_epoch,
    )
    return (
        all(type(value) is str for value in identity_fields)
        and identity_fields == scope_fields
        and type(identity.incarnation_id) is str
        and type(identity.canonical_namespace_digest) is str
    )


def _owner_identity_fields(
    identity: RollingHistoryOwnerIdentity,
) -> tuple[str, str, str, str, str, str] | None:
    fields = (
        identity.session_id,
        identity.request_id,
        identity.principal_hash,
        identity.delete_epoch,
        identity.incarnation_id,
        identity.canonical_namespace_digest,
    )
    if any(type(value) is not str for value in fields):
        return None
    return fields


def _valid_opaque_handle(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == _OPAQUE_HANDLE_LENGTH
        and _OPAQUE_HANDLE_RE.fullmatch(value) is not None
    )


def _reference_matches_scope(
    reference: RollingHistoryReference,
    scope_fields: tuple[str, str, str, str],
) -> bool:
    return (
        type(reference) is RollingHistoryReference
        and type(reference.event_id) is str
        and _HASH.fullmatch(reference.event_id) is not None
        and type(reference.session_id) is str
        and reference.session_id == scope_fields[0]
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


def _reference_payload(reference: RollingHistoryReference) -> dict[str, Any]:
    return {
        "event_id": reference.event_id,
        "session_id": reference.session_id,
        "request_id": reference.request_id,
        "sequence": reference.sequence,
        "version": reference.version,
        "content_sha256": reference.content_sha256,
        "chain_sha256": reference.chain_sha256,
    }


def _reader_cursor_fields(kind: str) -> frozenset[str] | None:
    if kind == "page_cursor":
        return _PAGE_CURSOR_FIELDS
    if kind == "chunk_cursor":
        return _CHUNK_CURSOR_FIELDS
    return None


def _copy_reader_cursor(raw_cursor: Any, kind: str) -> dict[str, Any] | None:
    expected_fields = _reader_cursor_fields(kind)
    expected_kind = _PAGE_CURSOR_KIND if kind == "page_cursor" else _CHUNK_CURSOR_KIND
    if (
        expected_fields is None
        or type(raw_cursor) is not dict
        or set(raw_cursor) != expected_fields
        or raw_cursor.get("cursor_kind") != expected_kind
    ):
        return None
    for value in raw_cursor.values():
        if type(value) is int:
            if abs(value) > 2**63 - 1:
                return None
        elif type(value) is str:
            if len(value) > 512:
                return None
        else:
            return None
    try:
        encoded = json.dumps(
            raw_cursor,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        return None
    if len(encoded) > _OPAQUE_MAX_ENTRY_BYTES:
        return None
    return dict(raw_cursor)


class _SharedReadState:
    """Revocable authority shared by ContextVar copies and worker threads."""

    __slots__ = (
        "lock",
        "revoked",
        "authenticated_user",
        "scope",
        "scope_fields",
        "owner_identity",
        "opaque_handles",
        "opaque_handle_bytes",
        "opaque_reads",
        "opaque_page_event_budget",
        "opaque_chunk_byte_budget",
    )

    def __init__(
        self,
        authenticated_user: dict,
        scope: ContextEventScope,
        scope_fields: tuple[str, str, str, str],
        owner_identity: RollingHistoryOwnerIdentity,
    ) -> None:
        self.lock = Lock()
        self.revoked = False
        self.authenticated_user: dict | None = authenticated_user
        self.scope: ContextEventScope | None = scope
        self.scope_fields = scope_fields
        self.owner_identity: RollingHistoryOwnerIdentity | None = owner_identity
        self.opaque_handles: dict[str, _OpaqueHandleEntry] = {}
        self.opaque_handle_bytes = 0
        self.opaque_reads = 0
        self.opaque_page_event_budget = 0
        self.opaque_chunk_byte_budget = 0

    def snapshot_for_read(
        self,
    ) -> tuple[dict, RollingHistoryOwnerIdentity]:
        """Linearize read admission against close and recheck the host scope."""
        with self.lock:
            return self._snapshot_locked()

    def _snapshot_locked(
        self,
    ) -> tuple[dict, RollingHistoryOwnerIdentity]:
        if self.revoked or _ACTIVE_OWNER_TOOL_STATE.get() is not self:
            raise _unavailable()
        authenticated_user = self.authenticated_user
        scope = self.scope
        owner_identity = self.owner_identity
        if (
            type(authenticated_user) is not dict
            or scope is None
            or owner_identity is None
        ):
            raise _unavailable()

        current_scope = get_tool_event_scope()
        if (
            current_scope is not scope
            or _scope_fields(current_scope) != self.scope_fields
            or not _identity_matches_scope(owner_identity, self.scope_fields)
        ):
            raise _unavailable()
        return authenticated_user, owner_identity

    def _entry_matches_locked(
        self,
        entry: _OpaqueHandleEntry,
        *,
        kind: _OpaqueHandleKind,
        owner_identity: RollingHistoryOwnerIdentity,
        reference: RollingHistoryReference | None,
    ) -> bool:
        owner_fields = _owner_identity_fields(owner_identity)
        return (
            type(entry) is _OpaqueHandleEntry
            and owner_fields is not None
            and entry.kind == kind
            and entry.scope_fields == self.scope_fields
            and entry.owner_fields == owner_fields
            and entry.reference == reference
        )

    def admit_page_opaque_read(
        self,
        cursor_handle: str | None,
        limit: int,
    ) -> tuple[dict, RollingHistoryOwnerIdentity, dict[str, Any] | None]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise _unavailable()
        with self.lock:
            authenticated_user, owner_identity = self._snapshot_locked()
            raw_cursor = None
            if cursor_handle is not None:
                if not _valid_opaque_handle(cursor_handle):
                    raise _unavailable()
                entry = self.opaque_handles.get(cursor_handle)
                if (
                    entry is None
                    or not self._entry_matches_locked(
                        entry,
                        kind="page_cursor",
                        owner_identity=owner_identity,
                        reference=None,
                    )
                    or type(entry.raw_cursor) is not dict
                ):
                    raise _unavailable()
                raw_cursor = dict(entry.raw_cursor)

            if (
                self.opaque_reads >= _OPAQUE_MAX_READS
                or self.opaque_page_event_budget + limit
                > _OPAQUE_MAX_PAGE_EVENT_BUDGET
            ):
                raise _unavailable()
            self.opaque_reads += 1
            self.opaque_page_event_budget += limit
            return authenticated_user, owner_identity, raw_cursor

    def admit_chunk_opaque_read(
        self,
        reference_handle: str,
        cursor_handle: str | None,
        byte_budget: int,
    ) -> tuple[
        dict,
        RollingHistoryOwnerIdentity,
        RollingHistoryReference,
        dict[str, Any] | None,
    ]:
        if (
            type(byte_budget) is not int
            or not 4 <= byte_budget <= _OPAQUE_MAX_CHUNK_CALL_BYTES
        ):
            raise _unavailable()
        if not _valid_opaque_handle(reference_handle):
            raise _unavailable()
        with self.lock:
            authenticated_user, owner_identity = self._snapshot_locked()
            reference_entry = self.opaque_handles.get(reference_handle)
            if (
                reference_entry is None
                or not self._entry_matches_locked(
                    reference_entry,
                    kind="reference",
                    owner_identity=owner_identity,
                    reference=reference_entry.reference,
                )
                or type(reference_entry.reference) is not RollingHistoryReference
                or not _reference_matches_scope(
                    reference_entry.reference,
                    self.scope_fields,
                )
            ):
                raise _unavailable()
            reference = reference_entry.reference

            raw_cursor = None
            if cursor_handle is not None:
                if not _valid_opaque_handle(cursor_handle):
                    raise _unavailable()
                cursor_entry = self.opaque_handles.get(cursor_handle)
                if (
                    cursor_entry is None
                    or not self._entry_matches_locked(
                        cursor_entry,
                        kind="chunk_cursor",
                        owner_identity=owner_identity,
                        reference=reference,
                    )
                    or type(cursor_entry.raw_cursor) is not dict
                ):
                    raise _unavailable()
                raw_cursor = dict(cursor_entry.raw_cursor)

            if (
                self.opaque_reads >= _OPAQUE_MAX_READS
                or self.opaque_chunk_byte_budget + byte_budget
                > _OPAQUE_MAX_CHUNK_BYTE_BUDGET
            ):
                raise _unavailable()
            self.opaque_reads += 1
            self.opaque_chunk_byte_budget += byte_budget
            return authenticated_user, owner_identity, reference, raw_cursor

    def _entry_size(
        self,
        handle: str,
        *,
        kind: _OpaqueHandleKind,
        scope_fields: tuple[str, str, str, str],
        owner_fields: tuple[str, str, str, str, str, str],
        reference: RollingHistoryReference | None,
        raw_cursor: dict[str, Any] | None,
    ) -> int:
        payload = {
            "handle": handle,
            "kind": kind,
            "scope_fields": scope_fields,
            "owner_fields": owner_fields,
            "reference": None if reference is None else _reference_payload(reference),
            "raw_cursor": raw_cursor,
        }
        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise _unavailable() from None
        return len(encoded) + _OPAQUE_ENTRY_OVERHEAD_BYTES

    def _new_handle_locked(self, reserved: set[str]) -> str:
        for _ in range(8):
            handle = secrets.token_urlsafe(32)
            if (
                _valid_opaque_handle(handle)
                and handle not in self.opaque_handles
                and handle not in reserved
            ):
                reserved.add(handle)
                return handle
        raise _unavailable()

    def store_opaque_handles(
        self,
        references: tuple[RollingHistoryReference, ...],
        *,
        cursor_kind: Literal["page_cursor", "chunk_cursor"] | None = None,
        cursor_reference: RollingHistoryReference | None = None,
        raw_cursor: dict[str, Any] | None = None,
    ) -> tuple[tuple[str, ...], str | None]:
        with self.lock:
            _, owner_identity = self._snapshot_locked()
            owner_fields = _owner_identity_fields(owner_identity)
            if owner_fields is None or type(references) is not tuple:
                raise _unavailable()

            plans: list[
                tuple[
                    _OpaqueHandleKind,
                    RollingHistoryReference | None,
                    dict[str, Any] | None,
                ]
            ] = []
            for reference in references:
                if not _reference_matches_scope(reference, self.scope_fields):
                    raise _unavailable()
                plans.append(("reference", reference, None))

            if raw_cursor is not None:
                if cursor_kind not in ("page_cursor", "chunk_cursor"):
                    raise _unavailable()
                cursor_copy = _copy_reader_cursor(raw_cursor, cursor_kind)
                if cursor_copy is None:
                    raise _unavailable()
                if cursor_kind == "page_cursor":
                    if cursor_reference is not None:
                        raise _unavailable()
                elif not _reference_matches_scope(
                    cursor_reference,
                    self.scope_fields,
                ):
                    raise _unavailable()
                plans.append((cursor_kind, cursor_reference, cursor_copy))
            elif cursor_kind is not None or cursor_reference is not None:
                raise _unavailable()

            if (
                len(self.opaque_handles) + len(plans) > _OPAQUE_MAX_HANDLE_COUNT
            ):
                raise _unavailable()

            reserved: set[str] = set()
            staged: list[tuple[str, _OpaqueHandleEntry]] = []
            added_bytes = 0
            for kind, reference, cursor_copy in plans:
                handle = self._new_handle_locked(reserved)
                size = self._entry_size(
                    handle,
                    kind=kind,
                    scope_fields=self.scope_fields,
                    owner_fields=owner_fields,
                    reference=reference,
                    raw_cursor=cursor_copy,
                )
                if size > _OPAQUE_MAX_ENTRY_BYTES:
                    raise _unavailable()
                added_bytes += size
                if (
                    self.opaque_handle_bytes + added_bytes
                    > _OPAQUE_MAX_HANDLE_STATE_BYTES
                ):
                    raise _unavailable()
                staged.append((
                    handle,
                    _OpaqueHandleEntry(
                        kind=kind,
                        scope_fields=self.scope_fields,
                        owner_fields=owner_fields,
                        reference=reference,
                        raw_cursor=cursor_copy,
                        accounted_bytes=size,
                    ),
                ))

            for handle, entry in staged:
                self.opaque_handles[handle] = entry
            self.opaque_handle_bytes += added_bytes
            reference_handles = tuple(
                handle for handle, entry in staged if entry.kind == "reference"
            )
            cursor_handle = next(
                (handle for handle, entry in staged if entry.kind != "reference"),
                None,
            )
            return reference_handles, cursor_handle

    def revoke(self) -> None:
        """Prevent new admissions and discard state without waiting on SQL work."""
        with self.lock:
            self.revoked = True
            self.authenticated_user = None
            self.scope = None
            self.scope_fields = ()
            self.owner_identity = None
            self.opaque_handles.clear()
            self.opaque_handle_bytes = 0
            self.opaque_reads = 0
            self.opaque_page_event_budget = 0
            self.opaque_chunk_byte_budget = 0


_ACTIVE_OWNER_TOOL_STATE: ContextVar[_SharedReadState | None] = ContextVar(
    "rolling_history_owner_tool_state",
    default=None,
)


class RollingHistoryOwnerToolCapability:
    """Async, read-only access bound to one verified TOOL owner identity."""

    __slots__ = ("__state",)

    def __init__(self, state: _SharedReadState) -> None:
        self.__state = state

    def close(self) -> None:
        """Revoke this capability immediately; safe from another thread."""
        self.__state.revoke()

    def revoke(self) -> None:
        """Explicit alias for close, for host cancellation and shutdown paths."""
        self.close()

    async def read_page(
        self,
        *,
        cursor: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> RollingHistoryOwnerPage:
        """Read one existing bounded metadata page for this captured owner."""
        state = self.__state
        return await self._run_read(
            state,
            lambda: _read_page_for_state(state, cursor, limit),
            RollingHistoryOwnerPage,
        )

    async def read_chunk(
        self,
        reference: RollingHistoryReference,
        *,
        cursor: dict[str, Any] | None = None,
        byte_budget: int = 8192,
    ) -> RollingHistoryOwnerChunk:
        """Read one existing UTF-8 byte-bounded chunk for this captured owner."""
        state = self.__state
        return await self._run_read(
            state,
            lambda: _read_chunk_for_state(state, reference, cursor, byte_budget),
            RollingHistoryOwnerChunk,
        )

    async def read_page_opaque(
        self,
        *,
        cursor: str,
        limit: int = 50,
    ) -> RollingHistoryOwnerToolPage:
        """Read a persistent snapshot position through the current TOOL owner."""
        state = self.__state
        result = await self._run_tool_operation(
            state,
            lambda: _page_snapshot_for_state(state, cursor, limit),
            RollingHistoryOwnerLocatorPage,
        )
        if (
            not _valid_opaque_handle(result.snapshot_handle)
            or type(result.events) is not tuple
            or len(result.events) > limit
            or (
                result.next_handle is not None
                and not _valid_opaque_handle(result.next_handle)
            )
        ):
            raise _unavailable()

        projected_events: list[RollingHistoryOwnerToolEvent] = []
        for event in result.events:
            if (
                type(event) is not RollingHistoryOwnerLocatorEvent
                or type(event.event_type) is not str
                or len(event.event_type) > 128
                or type(event.content_utf8_byte_length) is not int
                or event.content_utf8_byte_length < 0
                or type(event.has_attachments) is not bool
                or event.skill_provenance_status
                not in ("verified", "unknown", "not_applicable")
                or not _valid_opaque_handle(event.reference)
            ):
                raise _unavailable()
            projected_events.append(RollingHistoryOwnerToolEvent(
                reference=event.reference,
                event_type=event.event_type,
                content_utf8_byte_length=event.content_utf8_byte_length,
                has_attachments=event.has_attachments,
                skill_provenance_status=event.skill_provenance_status,
            ))
        state.snapshot_for_read()
        return RollingHistoryOwnerToolPage(
            snapshot_handle=result.snapshot_handle,
            events=tuple(projected_events),
            next_cursor=result.next_handle,
        )

    async def open_snapshot_opaque(self, snapshot_handle: str | None = None) -> str:
        """Open or idempotently reopen a stable snapshot locator."""
        state = self.__state
        return await self._run_tool_operation(
            state,
            lambda: _open_snapshot_for_state(state, snapshot_handle),
            str,
            require_untrusted=False,
        )

    async def read_chunk_opaque(
        self,
        reference: str,
        *,
        cursor: str | None = None,
        byte_budget: int = 8192,
    ) -> RollingHistoryOwnerToolChunk:
        """Read one bounded chunk using persistent event and chunk locators."""
        state = self.__state
        result = await self._run_tool_operation(
            state,
            lambda: _read_locator_chunk_for_state(
                state,
                reference,
                cursor,
                byte_budget,
            ),
            RollingHistoryOwnerLocatorChunk,
        )
        if (
            result.reference != reference
            or not _valid_opaque_handle(result.reference)
            or (cursor is not None and not _valid_opaque_handle(cursor))
            or type(result.event_type) is not str
            or len(result.event_type) > 128
            or type(result.body_chunk) is not str
            or type(result.start_offset) is not int
            or result.start_offset < 0
            or type(result.end_offset) is not int
            or result.end_offset < result.start_offset
            or type(result.total_utf8_bytes) is not int
            or result.total_utf8_bytes < result.end_offset
            or result.end_offset - result.start_offset > byte_budget
            or type(result.complete) is not bool
            or result.skill_provenance_status
            not in ("verified", "unknown", "not_applicable")
            or type(result.has_attachments) is not bool
            or (
                result.next_handle is not None
                and not _valid_opaque_handle(result.next_handle)
            )
        ):
            raise _unavailable()
        state.snapshot_for_read()
        return RollingHistoryOwnerToolChunk(
            reference=reference,
            event_type=result.event_type,
            body_chunk=result.body_chunk,
            start_offset=result.start_offset,
            end_offset=result.end_offset,
            total_utf8_bytes=result.total_utf8_bytes,
            complete=result.complete,
            next_cursor=result.next_handle,
            skill_provenance_status=result.skill_provenance_status,
            has_attachments=result.has_attachments,
        )

    async def _run_tool_operation(
        self,
        state: _SharedReadState,
        operation: Callable[[], _T],
        expected_type: type[_T],
        *,
        require_untrusted: bool = True,
    ) -> _T:
        state.snapshot_for_read()
        try:
            result = await asyncio.to_thread(operation)
        except asyncio.CancelledError:
            state.revoke()
            raise
        except Exception:
            raise _unavailable() from None
        state.snapshot_for_read()
        if type(result) is not expected_type:
            raise _unavailable()
        if require_untrusted and getattr(result, "untrusted_historical_data", None) is not True:
            raise _unavailable()
        state.snapshot_for_read()
        return result

    async def _run_read(
        self,
        state: _SharedReadState,
        operation: Callable[[], _T],
        expected_type: type[_T],
    ) -> _T:
        return await self._run_tool_operation(
            state,
            operation,
            expected_type,
            require_untrusted=True,
        )


def get_current_rolling_history_owner_tool_capability() -> RollingHistoryOwnerToolCapability:
    """Return a capability for the currently verified host TOOL scope.

    This getter intentionally accepts no identity, session, or epoch input.
    It only wraps the existing ContextVar state after that shared revocation
    state confirms the current TOOL scope and captured owner identity still
    match. The returned wrapper shares that state, so later revocation is
    observed by every read through it.
    """
    state = _ACTIVE_OWNER_TOOL_STATE.get()
    if type(state) is not _SharedReadState:
        raise _unavailable()
    try:
        state.snapshot_for_read()
    except Exception:
        raise _unavailable() from None
    return RollingHistoryOwnerToolCapability(state)


def _read_page_for_state(
    state: _SharedReadState,
    cursor: dict[str, Any] | None,
    limit: int,
) -> RollingHistoryOwnerPage:
    authenticated_user, owner_identity = state.snapshot_for_read()
    result = page_rolling_history_owner_events(
        authenticated_user,
        cursor=cursor,
        limit=limit,
        expected_owner_identity=owner_identity,
    )
    state.snapshot_for_read()
    return result


def _read_chunk_for_state(
    state: _SharedReadState,
    reference: RollingHistoryReference,
    cursor: dict[str, Any] | None,
    byte_budget: int,
) -> RollingHistoryOwnerChunk:
    authenticated_user, owner_identity = state.snapshot_for_read()
    result = read_rolling_history_owner_event_chunk(
        authenticated_user,
        reference,
        cursor=cursor,
        byte_budget=byte_budget,
        expected_owner_identity=owner_identity,
    )
    state.snapshot_for_read()
    return result


def _open_snapshot_for_state(
    state: _SharedReadState,
    snapshot_handle: str | None,
) -> str:
    authenticated_user, owner_identity = state.snapshot_for_read()
    result = open_rolling_history_owner_snapshot(
        authenticated_user,
        snapshot_handle=snapshot_handle,
        expected_owner_identity=owner_identity,
    )
    state.snapshot_for_read()
    return result


def _page_snapshot_for_state(
    state: _SharedReadState,
    snapshot_handle: str,
    limit: int,
) -> RollingHistoryOwnerLocatorPage:
    authenticated_user, owner_identity = state.snapshot_for_read()
    result = page_rolling_history_owner_snapshot(
        authenticated_user,
        snapshot_handle,
        limit=limit,
        expected_owner_identity=owner_identity,
    )
    state.snapshot_for_read()
    return result


def _read_locator_chunk_for_state(
    state: _SharedReadState,
    event_handle: str,
    cursor_handle: str | None,
    byte_budget: int,
) -> RollingHistoryOwnerLocatorChunk:
    authenticated_user, owner_identity = state.snapshot_for_read()
    result = read_rolling_history_owner_locator_chunk(
        authenticated_user,
        event_handle,
        cursor_handle=cursor_handle,
        byte_budget=byte_budget,
        expected_owner_identity=owner_identity,
    )
    state.snapshot_for_read()
    return result


@contextmanager
def rolling_history_owner_tool_scope(
    authenticated_user: dict | None,
) -> Iterator[RollingHistoryOwnerToolCapability]:
    """Yield a revocable capability from the current verified TOOL scope.

    The host must keep this context around its async tool operation. The active
    ContextEventScope is read only from the host ContextVar; callers cannot pass
    a session, principal, request, deletion epoch, incarnation, or origin. Entry
    bootstraps and validates the database owner, then captures its immutable
    identity before yielding. Every page/chunk read pins that identity through
    the expected_owner_identity argument inside the reader's READ transaction.

    Closing refuses later read admissions. An already admitted synchronous
    reader is not interrupted; worker and caller checks reject its result when
    they observe revocation before return. A delivered result cannot be recalled.
    """
    if _ACTIVE_OWNER_TOOL_STATE.get() is not None:
        raise _unavailable()
    if type(authenticated_user) is not dict:
        raise _unavailable()

    try:
        with bootstrap_rolling_history_owner_scope(
            authenticated_user,
            ScopeOrigin.TOOL,
        ):
            scope = get_tool_event_scope()
            scope_fields = _scope_fields(scope)
            owner_identity = get_rolling_history_owner_identity()
            if not _identity_matches_scope(owner_identity, scope_fields):
                raise _unavailable()
    except Exception:
        raise _unavailable() from None

    state = _SharedReadState(
        authenticated_user=authenticated_user,
        scope=scope,
        scope_fields=scope_fields,
        owner_identity=owner_identity,
    )
    capability = RollingHistoryOwnerToolCapability(state)
    token = _ACTIVE_OWNER_TOOL_STATE.set(state)
    try:
        yield capability
    finally:
        try:
            state.revoke()
        finally:
            _ACTIVE_OWNER_TOOL_STATE.reset(token)
