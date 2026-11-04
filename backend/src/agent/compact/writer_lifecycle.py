"""In-process ownership and cooperative-drain primitives for native writers.

This module is used by the current chat/SSE path for in-process host
coordination. It is not a complete native-writer fence: another process, an
unregistered writer, or code that bypasses this registry is outside its scope.

The host must validate the graph/saver pairing before constructing a
``WriterScope``. Scope references are then pinned by identity for the lease.
The owner key is only a registry lookup key and is never sufficient to
authenticate a graph, request, or epoch. The trusted host must revalidate the
authoritative current epoch before every acquisition. An epoch may be an
opaque identity object or the persistent empty/UUID-hex token returned by the
request epoch helpers. String tokens are compared by exact value; no monotonic
integer counter is implied. The registry pins the exact object references and
epoch token only for the active lease; it cannot prove first-seen scope
freshness or identify stale history after release.
"""

from __future__ import annotations

import threading
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Iterator


# Fixed per-registry guardrails: 1024 resident owner records and four times as
# many retained or live native controls. Non-weakrefable LangGraph RunControl
# objects are retained in bounded registry history, including after release.
_MAX_RESIDENT_OWNERS = 1024
_MAX_LIVE_NATIVE_CONTROL_REFS = 4096


class WriterLifecycleError(RuntimeError):
    """Base error for refused writer lifecycle transitions."""


class WriterCapacityError(WriterLifecycleError):
    """A bounded registry cannot safely accept another owner or run."""


class WriterScopeError(WriterLifecycleError):
    """A scope is incomplete, unsupported, or mismatches an active lease binding."""


class WriterOwnerConflictError(WriterLifecycleError):
    """An owner key already has an active or unresolved owner."""


class WriterLeaseError(WriterLifecycleError):
    """A lease is foreign, stale, revoked, or not the current capability."""


class WriterRunError(WriterLifecycleError):
    """A native run transition is concurrent, stale, or unconfirmed."""


class WriterQuarantinedError(WriterLifecycleError):
    """The owner is unresolved and cannot acquire, run, or release."""


class LeaseState(Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    RELEASED = "released"


class RunControlState(Enum):
    RUNNING = "running"
    DRAIN_REQUESTED = "drain_requested"
    JOINED = "joined"
    QUARANTINED = "quarantined"


class RunOutcome(Enum):
    """Whether the caller knows the native run reached a terminal outcome.

    ``KNOWN_TERMINAL`` says nothing about checkpoint persistence, pending
    writes, tool side effects, or whether replay is safe. Those are separate
    caller responsibilities. ``UNKNOWN`` always quarantines the owner.
    """

    KNOWN_TERMINAL = "known_terminal"
    UNKNOWN = "unknown"


class HostWriteOutcome(Enum):
    """Result of a host-owned checkpoint publication attempt."""

    NOT_ATTEMPTED = "not_attempted"
    ACKNOWLEDGED = "acknowledged"
    UNKNOWN = "unknown"


class CheckpointResetOutcome(Enum):
    """Result of a reserved, host-owned checkpoint reset transaction."""

    NOT_ATTEMPTED = "not_attempted"
    ACKNOWLEDGED = "acknowledged"
    UNKNOWN = "unknown"


class _HostStateUpdateOutcome(Enum):
    """Result of a serialized host state update and its caller-side readback."""

    NOT_ATTEMPTED = "not_attempted"
    ACKNOWLEDGED = "acknowledged"
    UNKNOWN = "unknown"


class QuarantineReason(Enum):
    BINDING_MISMATCH = "binding_mismatch"
    JOIN_UNCONFIRMED = "join_unconfirmed"
    RUN_OUTCOME_UNKNOWN = "run_outcome_unknown"
    TASK_OUTCOME_UNKNOWN = "task_outcome_unknown"
    WRITE_OUTCOME_UNKNOWN = "write_outcome_unknown"
    CALLER_REPORTED_UNCERTAINTY = "caller_reported_uncertainty"


_VALUE_TYPES = (bool, int, float, complex, str, bytes, tuple, frozenset)


def _require_identity_reference(name: str, value: object) -> None:
    if value is None or type(value) in _VALUE_TYPES:
        raise WriterScopeError(
            f"{name} must be a non-null identity-bearing object reference"
        )


def _is_persistent_epoch_token(value: object) -> bool:
    return type(value) is str and (
        value == ""
        or (
            len(value) == 32
            and all(char in "0123456789abcdef" for char in value)
        )
    )


@dataclass(frozen=True, slots=True, eq=False)
class WriterScope:
    """Exact host binding for one graph-thread owner and request epoch.

    ``owner_key`` is a stable registry key such as the graph thread identifier.
    ``graph``, ``saver``, and ``request_scope`` are compared with ``is``. An
    epoch supplied as an object is also compared with ``is``; an exact empty
    or lowercase 32-character UUID-hex string is compared by value. This lets
    a host rebuild ``WriterScope`` during synchronous revalidation from the
    persistent tokens returned by the request epoch helpers. ``epoch_generation``
    is an optional supplemental counter for hosts whose authority actually
    provides one; the current request epoch helpers do not, and callers must
    leave it unset rather than inventing a counter. The trusted host must
    revalidate the authoritative epoch before every acquisition and after
    awaits. The registry cannot prove first-seen freshness or identify stale
    history after release. ``request_scope`` remains an identity-bearing token
    and must be the same object across revalidation calls.
    """

    owner_key: str
    graph: object = field(repr=False)
    saver: object = field(repr=False)
    request_scope: object = field(repr=False)
    epoch: object = field(repr=False)
    epoch_generation: int | None = None

    def __post_init__(self) -> None:
        if type(self.owner_key) is not str or not self.owner_key.strip():
            raise WriterScopeError("owner_key must be a non-empty string")
        _require_identity_reference("graph", self.graph)
        _require_identity_reference("saver", self.saver)
        _require_identity_reference("request_scope", self.request_scope)
        if type(self.epoch) is str:
            if not _is_persistent_epoch_token(self.epoch):
                raise WriterScopeError(
                    "string epoch must be empty or a lowercase UUID hex token"
                )
        else:
            _require_identity_reference("epoch", self.epoch)
        if self.epoch_generation is not None and (
            type(self.epoch_generation) is not int or self.epoch_generation < 0
        ):
            raise WriterScopeError("epoch_generation must be a non-negative integer")

    def matches(self, other: object) -> bool:
        """Compare scope identity without invoking user-defined equality."""
        if not isinstance(other, WriterScope):
            return False
        same_epoch = (
            self.epoch == other.epoch
            if type(self.epoch) is str and type(other.epoch) is str
            else self.epoch is other.epoch
        )
        return (
            self.owner_key == other.owner_key
            and self.graph is other.graph
            and self.saver is other.saver
            and self.request_scope is other.request_scope
            and same_epoch
            and self.epoch_generation == other.epoch_generation
        )


class WriterLease:
    """Opaque, process-local capability minted and owned by one registry."""

    __slots__ = ("_registry_token", "_scope", "_state", "_run_generation")

    def __init__(self, registry_token: object, scope: WriterScope) -> None:
        self._registry_token = registry_token
        self._scope = scope
        self._state = LeaseState.ACTIVE
        self._run_generation = 0

    def __repr__(self) -> str:
        return "<WriterLease opaque>"

    def __reduce__(self):
        raise TypeError("WriterLease is process-local and cannot be serialized")

    def __reduce_ex__(self, _protocol: int):
        raise TypeError("WriterLease is process-local and cannot be serialized")

    def __copy__(self):
        raise TypeError("WriterLease is an identity capability and cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("WriterLease is an identity capability and cannot be copied")


class WriterRunHandle:
    """Host lifecycle handle bound to one native LangGraph run control.

    The host creates the native control for each run and passes it here. This
    handle forwards drain requests and reads drain state from that native
    object; it does not emulate a native drain with a local flag. This wrapper
    is not a LangGraph astream control argument; the adapter must pass the
    exact object exposed by ``native_control`` to the runner. Its local
    terminal state records only host-confirmed join or quarantine.
    """

    __slots__ = (
        "_registry_token",
        "_lease",
        "_generation",
        "_native_control",
        "_execution_config",
        "_drain_reason",
        "_drain_requested_by_registry",
        "_drain_acknowledged",
        "_joined",
        "_terminal_state",
        "_lock",
    )

    def __init__(
        self,
        registry_token: object,
        lease: WriterLease,
        generation: int,
        native_control: object,
        execution_config: object | None,
        lock: threading.RLock,
    ) -> None:
        self._registry_token = registry_token
        self._lease = lease
        self._generation = generation
        self._native_control = native_control
        self._execution_config = execution_config
        self._drain_reason: str | None = None
        self._drain_requested_by_registry = False
        self._drain_acknowledged = False
        self._joined = False
        self._terminal_state: RunControlState | None = None
        self._lock = lock

    @property
    def state(self) -> RunControlState:
        with self._lock:
            if self._terminal_state is not None:
                return self._terminal_state
            if self.drain_requested:
                return RunControlState.DRAIN_REQUESTED
            return RunControlState.RUNNING

    @property
    def drain_requested(self) -> bool:
        with self._lock:
            value = self._native_control.drain_requested
            if type(value) is not bool:
                raise WriterRunError("native control drain_requested must be a bool")
            return value

    @property
    def drain_reason(self) -> object:
        with self._lock:
            return self._native_control.drain_reason

    @property
    def native_control(self) -> object:
        """Return the exact native control to pass to the graph runner."""
        return self._native_control

    @property
    def execution_config(self) -> object | None:
        """Return the exact config bound to this run, if the host supplied it."""
        return self._execution_config

    @property
    def drain_acknowledged(self) -> bool:
        """Whether an actual GraphDrained exception was observed for this run."""
        with self._lock:
            return self._drain_acknowledged

    @property
    def joined(self) -> bool:
        """Whether the host explicitly confirmed every owned run task exited."""
        with self._lock:
            return self._joined

    def __repr__(self) -> str:
        return "<WriterRunHandle opaque>"

    def __reduce__(self):
        raise TypeError("WriterRunHandle is process-local and cannot be serialized")

    def __reduce_ex__(self, _protocol: int):
        raise TypeError("WriterRunHandle is process-local and cannot be serialized")


@dataclass(slots=True)
class _OwnerRecord:
    lease: WriterLease
    state: LeaseState = LeaseState.ACTIVE
    active_run: WriterRunHandle | None = None
    last_drained_run: WriterRunHandle | None = None
    host_write_active: bool = False
    host_state_update_token: object | None = None
    host_state_update_started: bool = False
    acknowledged_config: dict | None = None
    continuation_config: dict | None = None
    continuation_checkpoint: tuple[str, str, str] | None = None
    quarantine_reason: QuarantineReason | None = None


class WriterLifecycleRegistry:
    """Serialize registered writer ownership within one process.

    ``known_savers`` is an identity allowlist supplied by trusted host setup.
    An empty allowlist refuses every acquisition. No timeout or TTL transfers
    an active or quarantined owner; only the current lease capability can
    release an active owner after every native run has been confirmed joined.
    The trusted host must revalidate the authoritative current epoch before
    every acquisition and after awaits. The registry compares persistent epoch
    strings by exact value and object authorities by identity while a lease is
    active, but cannot prove first-seen scope freshness or identify stale
    history after release. Capacity limits resident ACTIVE and QUARANTINED
    records. A successful release removes its ACTIVE record and frees a slot;
    capacity refusal never evicts active, quarantined, or otherwise unresolved
    owner state.
    """

    def __init__(self, *, known_savers: Iterable[object]) -> None:
        try:
            savers = tuple(known_savers)
        except TypeError as exc:
            raise WriterScopeError("known_savers must be an iterable") from exc
        if any(saver is None or type(saver) in _VALUE_TYPES for saver in savers):
            raise WriterScopeError("known_savers must contain identity-bearing objects")

        self._known_savers = savers
        self._registry_token = object()
        self._owners: dict[str, _OwnerRecord] = {}
        # Weak references let the registry reject reuse while weakrefable
        # controls remain alive. LangGraph's slotted RunControl is not weakrefable,
        # so those controls are retained here across lease release. History is
        # bounded; at capacity the registry refuses new runs rather than evicting
        # one-use evidence.
        self._native_control_refs = []
        self._strong_native_controls: list[object] = []
        self._lock = threading.RLock()

    def acquire(self, scope: WriterScope) -> WriterLease:
        """Mint a lease after the trusted host revalidates the current epoch."""
        if not isinstance(scope, WriterScope):
            raise WriterScopeError("acquire requires a WriterScope")
        if not any(scope.saver is known for known in self._known_savers):
            raise WriterScopeError("saver is not in this registry's identity allowlist")

        with self._lock:
            current = self._owners.get(scope.owner_key)
            if current is not None:
                if current.state is LeaseState.QUARANTINED:
                    raise WriterQuarantinedError("owner is quarantined")
                raise WriterOwnerConflictError("owner key already has a lease")

            if len(self._owners) >= _MAX_RESIDENT_OWNERS:
                raise WriterCapacityError(
                    "resident owner capacity reached; refusing a new owner key"
                )

            lease = WriterLease(self._registry_token, scope)
            self._owners[scope.owner_key] = _OwnerRecord(lease=lease)
            return lease

    def assert_scope(self, lease: WriterLease, scope: WriterScope) -> None:
        """Recheck exact binding after an await or before a writer transition."""
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)

    def begin_run(
        self,
        lease: WriterLease,
        scope: WriterScope,
        native_control: object,
        *,
        execution_config: object | None = None,
    ) -> WriterRunHandle:
        """Bind one fresh native control to a host-owned run handle."""
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            if record.host_write_active or record.host_state_update_token is not None:
                raise WriterRunError("a host checkpoint publication is active")
            if record.acknowledged_config is not None:
                raise WriterRunError("checkpoint publication is not yet resumable")
            if (
                record.last_drained_run is not None
                and record.continuation_config is None
            ):
                raise WriterRunError(
                    "a drained run cannot restart without a validated checkpoint replacement"
                )
            if (
                record.continuation_config is not None
                and execution_config is not record.continuation_config
            ):
                raise WriterRunError(
                    "continuation must use the exact authorized execution config"
                )
            if record.continuation_config is not None:
                configurable = (
                    execution_config.get("configurable")
                    if type(execution_config) is dict
                    else None
                )
                expected_checkpoint = record.continuation_checkpoint
                if (
                    type(configurable) is not dict
                    or expected_checkpoint is None
                    or tuple(
                        configurable.get(key)
                        for key in ("thread_id", "checkpoint_ns", "checkpoint_id")
                    )
                    != expected_checkpoint
                ):
                    self._quarantine_record(
                        record, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                    )
                    raise WriterRunError(
                        "authorized execution config changed before continuation"
                    )
            if record.active_run is not None:
                raise WriterRunError("a native run is already active for this lease")
            self._validate_native_control(native_control)
            self._remember_native_control(native_control)

            lease._run_generation += 1
            handle = WriterRunHandle(
                self._registry_token,
                lease,
                lease._run_generation,
                native_control,
                execution_config,
                self._lock,
            )
            record.active_run = handle
            record.last_drained_run = None
            record.continuation_config = None
            record.continuation_checkpoint = None
            return handle

    def request_drain(
        self,
        lease: WriterLease,
        scope: WriterScope,
        handle: WriterRunHandle,
        reason: str,
    ) -> bool:
        """Forward a drain request to the current run's native control.

        The handle's state and reason remain derived from native properties.
        A failed or unreadable native request quarantines the owner because its
        effect is uncertain. A drain request is not a join or save ack.
        """
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            self._verify_run_handle(record, lease, handle)
            if type(reason) is not str or not reason:
                raise WriterRunError("drain reason must be a non-empty string")
            try:
                if handle.drain_requested:
                    return False
                handle._native_control.request_drain(reason)
                # Read the native properties after forwarding so a broken
                # native contract cannot be mistaken for a successful request.
                if handle.drain_requested is not True:
                    raise WriterRunError(
                        "native control did not confirm the drain request"
                    )
                if handle.drain_reason != reason:
                    raise WriterRunError(
                        "native control did not retain the requested drain reason"
                    )
                handle._drain_reason = reason
                handle._drain_requested_by_registry = True
            except BaseException:
                self._quarantine_record(
                    record,
                    QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
                raise
            return True

    def acknowledge_graph_drained(
        self,
        lease: WriterLease,
        scope: WriterScope,
        handle: WriterRunHandle,
        error: BaseException,
    ) -> bool:
        """Record drain only for LangGraph's actual GraphDrained exception.

        A drain request alone is not evidence that the graph reached a
        superstep boundary. Missing LangGraph support or an inconsistent
        control state quarantines this owner; other exceptions are not drain
        acknowledgements.
        """
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            self._verify_run_handle(record, lease, handle)
            try:
                from langgraph.errors import GraphDrained
            except Exception as exc:
                self._quarantine_record(
                    record, QuarantineReason.RUN_OUTCOME_UNKNOWN,
                )
                raise WriterRunError(
                    "LangGraph GraphDrained type is unavailable"
                ) from exc

            if not isinstance(error, GraphDrained):
                return False
            try:
                requested = handle.drain_requested
            except BaseException:
                self._quarantine_record(
                    record, QuarantineReason.RUN_OUTCOME_UNKNOWN,
                )
                raise
            actual_reason = getattr(error, "reason", None)
            if (
                requested is not True
                or not handle._drain_requested_by_registry
                or actual_reason != handle._drain_reason
                or actual_reason != handle.drain_reason
            ):
                self._quarantine_record(
                    record, QuarantineReason.RUN_OUTCOME_UNKNOWN,
                )
                raise WriterRunError(
                    "GraphDrained reason does not match this run's native drain request"
                )
            handle._drain_acknowledged = True
            return True

    def finish_run(
        self,
        lease: WriterLease,
        scope: WriterScope,
        handle: WriterRunHandle,
        *,
        joined: bool,
        outcome: RunOutcome,
    ) -> None:
        """Record caller-confirmed native join; never performs the join itself.

        ``joined=True`` must mean the caller has joined every task owned by this
        native run. Unknown join or run outcome quarantines the owner. Even a
        known terminal run is not a checkpoint acknowledgement or permission to
        replay its input or side effects.
        """
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            self._verify_run_handle(record, lease, handle)

            if joined is not True:
                self._quarantine_record(record, QuarantineReason.JOIN_UNCONFIRMED)
                raise WriterRunError("native run was not explicitly confirmed joined")
            if outcome is RunOutcome.UNKNOWN:
                self._quarantine_record(record, QuarantineReason.RUN_OUTCOME_UNKNOWN)
                raise WriterRunError("native run outcome is unknown; owner quarantined")
            if outcome is not RunOutcome.KNOWN_TERMINAL:
                self._quarantine_record(record, QuarantineReason.RUN_OUTCOME_UNKNOWN)
                raise WriterRunError("invalid native run outcome; owner quarantined")

            handle._terminal_state = RunControlState.JOINED
            handle._joined = True
            record.active_run = None
            record.last_drained_run = (
                handle if handle._drain_acknowledged else None
            )

    def assert_compaction_safe(
        self,
        lease: WriterLease,
        scope: WriterScope,
    ) -> WriterRunHandle:
        """Require a joined native GraphDrained run before checkpoint replacement."""
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            run = record.last_drained_run
            if (
                record.active_run is not None
                or record.host_write_active
                or record.host_state_update_token is not None
                or record.acknowledged_config is not None
                or record.continuation_config is not None
                or run is None
                or not run._joined
                or not run._drain_acknowledged
                or run._terminal_state is not RunControlState.JOINED
            ):
                raise WriterRunError(
                    "checkpoint replacement requires a joined GraphDrained run"
                )
            return run

    def begin_host_write(
        self,
        lease: WriterLease,
        scope: WriterScope,
    ) -> None:
        """Reserve the owner for one compaction checkpoint publication."""
        with self._lock:
            self.assert_compaction_safe(lease, scope)
            record = self._current_record(lease)
            if record.host_write_active:
                raise WriterRunError("a host checkpoint publication is active")
            record.host_write_active = True

    def _begin_host_state_update(
        self,
        lease: WriterLease,
        scope: WriterScope,
    ) -> object:
        """Reserve an idle owner for one serialized host checkpoint update."""
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            if (
                record.active_run is not None
                or record.host_write_active
                or record.host_state_update_token is not None
                or record.acknowledged_config is not None
                or record.continuation_config is not None
                or record.continuation_checkpoint is not None
                or record.last_drained_run is not None
            ):
                raise WriterRunError(
                    "host state update requires an idle, unresolved-free owner"
                )
            token = object()
            record.host_state_update_token = token
            record.host_state_update_started = False
            return token

    def begin_checkpoint_reset(
        self,
        lease: WriterLease,
        scope: WriterScope,
    ) -> object:
        """Reserve one idle owner for an atomic host checkpoint reset.

        The shared reservation excludes ``begin_run`` and other host state
        updates for this exact lease. The caller still must prove the saver
        transaction and complete checkpoint/write readback before ACK.
        """
        return self._begin_host_state_update(lease, scope)

    def mark_checkpoint_reset_started(
        self,
        lease: WriterLease,
        scope: WriterScope,
        token: object,
    ) -> None:
        """Mark the reset reservation before scheduling its owned task."""
        self._mark_host_state_update_started(lease, scope, token)

    def finish_checkpoint_reset(
        self,
        lease: WriterLease,
        scope: WriterScope,
        token: object,
        *,
        outcome: CheckpointResetOutcome,
    ) -> None:
        """Release a no-op reset, ACK a fully verified commit, or quarantine.

        ACK is caller-certified only after the owned transaction committed
        and exact readback found no checkpoint or pending-write rows for the
        leased thread across namespaces.
        """
        if not isinstance(outcome, CheckpointResetOutcome):
            self._finish_host_state_update(
                lease,
                scope,
                token,
                outcome=_HostStateUpdateOutcome.UNKNOWN,
            )
            return
        mapped = {
            CheckpointResetOutcome.NOT_ATTEMPTED: _HostStateUpdateOutcome.NOT_ATTEMPTED,
            CheckpointResetOutcome.ACKNOWLEDGED: _HostStateUpdateOutcome.ACKNOWLEDGED,
            CheckpointResetOutcome.UNKNOWN: _HostStateUpdateOutcome.UNKNOWN,
        }[outcome]
        self._finish_host_state_update(lease, scope, token, outcome=mapped)

    def _mark_host_state_update_started(
        self,
        lease: WriterLease,
        scope: WriterScope,
        token: object,
    ) -> None:
        """Mark the reservation before scheduling its owned async write task."""
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            if record.host_state_update_token is not token or record.host_state_update_started:
                self._quarantine_record(record, QuarantineReason.WRITE_OUTCOME_UNKNOWN)
                raise WriterRunError("host state update reservation is stale")
            record.host_state_update_started = True

    def _finish_host_state_update(
        self,
        lease: WriterLease,
        scope: WriterScope,
        token: object,
        *,
        outcome: _HostStateUpdateOutcome,
    ) -> None:
        """Release a known reservation or quarantine an unconfirmed write.

        ``ACKNOWLEDGED`` is valid only after the caller-specific exact readback
        has completed (for checkpoint publication, the returned config and
        denial state; for reset, the committed empty thread state). The
        registry serializes that interval; it does not perform the readback.
        """
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            if record.host_state_update_token is not token:
                self._quarantine_record(record, QuarantineReason.WRITE_OUTCOME_UNKNOWN)
                raise WriterRunError("host state update reservation is stale")
            if outcome is _HostStateUpdateOutcome.UNKNOWN:
                self._quarantine_record(record, QuarantineReason.WRITE_OUTCOME_UNKNOWN)
                raise WriterRunError("host state update outcome is unknown")
            if outcome is _HostStateUpdateOutcome.NOT_ATTEMPTED:
                if record.host_state_update_started:
                    self._quarantine_record(record, QuarantineReason.WRITE_OUTCOME_UNKNOWN)
                    raise WriterRunError("started host state update cannot be unattempted")
            elif outcome is _HostStateUpdateOutcome.ACKNOWLEDGED:
                if not record.host_state_update_started:
                    self._quarantine_record(record, QuarantineReason.WRITE_OUTCOME_UNKNOWN)
                    raise WriterRunError("unstarted host state update cannot be acknowledged")
            else:
                self._quarantine_record(record, QuarantineReason.WRITE_OUTCOME_UNKNOWN)
                raise WriterRunError("invalid host state update outcome")
            record.host_state_update_token = None
            record.host_state_update_started = False

    def finish_host_write(
        self,
        lease: WriterLease,
        scope: WriterScope,
        *,
        outcome: HostWriteOutcome,
        applied_config: dict | None = None,
    ) -> None:
        """Record no write, an exact saver ACK, or an unresolved write outcome.

        ``ACKNOWLEDGED`` is only a saver-level ACK. It does not authorize graph
        continuation; the checkpoint publisher must read back that exact config
        and validate a resumable state before calling ``authorize_continuation``.
        """
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            if not record.host_write_active:
                self._quarantine_record(
                    record, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                )
                raise WriterRunError("no host checkpoint publication is active")
            if outcome is HostWriteOutcome.UNKNOWN:
                self._quarantine_record(
                    record, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                )
                raise WriterRunError("checkpoint write outcome is unknown")
            if outcome is HostWriteOutcome.NOT_ATTEMPTED:
                if applied_config is not None:
                    self._quarantine_record(
                        record, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                    )
                    raise WriterRunError(
                        "an unattempted write cannot have a saver acknowledgement"
                    )
                record.host_write_active = False
                return
            if outcome is not HostWriteOutcome.ACKNOWLEDGED:
                self._quarantine_record(
                    record, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                )
                raise WriterRunError("invalid host checkpoint outcome")
            if type(applied_config) is not dict:
                self._quarantine_record(
                    record, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                )
                raise WriterRunError("saver acknowledgement config is missing")
            record.host_write_active = False
            record.acknowledged_config = applied_config

    def authorize_continuation(
        self,
        lease: WriterLease,
        scope: WriterScope,
        *,
        applied_config: dict,
        execution_config: dict,
    ) -> None:
        """Bind the exact resumable config after saver ACK and readback proof."""
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            if (
                record.host_write_active
                or record.acknowledged_config is not applied_config
                or record.last_drained_run is None
                or not record.last_drained_run._joined
                or not record.last_drained_run._drain_acknowledged
                or type(execution_config) is not dict
            ):
                self._quarantine_record(
                    record, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                )
                raise WriterRunError(
                    "continuation lacks the exact acknowledged checkpoint proof"
                )
            applied_conf = applied_config.get("configurable")
            execution_conf = execution_config.get("configurable")
            if (
                type(applied_conf) is not dict
                or type(execution_conf) is not dict
                or any(
                    type(applied_conf.get(key)) is not str
                    for key in ("thread_id", "checkpoint_ns", "checkpoint_id")
                )
                or any(
                    execution_conf.get(key) != applied_conf.get(key)
                    for key in ("thread_id", "checkpoint_ns", "checkpoint_id")
                )
            ):
                self._quarantine_record(
                    record, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                )
                raise WriterRunError(
                    "execution config does not point at the acknowledged checkpoint"
                )
            record.continuation_config = execution_config
            record.continuation_checkpoint = (
                applied_conf["thread_id"],
                applied_conf["checkpoint_ns"],
                applied_conf["checkpoint_id"],
            )
            record.acknowledged_config = None
            record.last_drained_run = None

    def quarantine(
        self,
        lease: WriterLease,
        scope: WriterScope,
        *,
        reason: QuarantineReason,
    ) -> None:
        """Permanently block this owner after an unresolved task/run/write.

        This registry has no timeout-based recovery or public unquarantine
        operation. A mismatching scope automatically quarantines the current
        lease before raising.
        """
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            if not isinstance(reason, QuarantineReason):
                self._quarantine_record(
                    record, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
                raise WriterLifecycleError("quarantine requires a known reason")
            self._quarantine_record(record, reason)

    def release(self, lease: WriterLease, scope: WriterScope) -> None:
        """Release only the exact current lease, with no active/unresolved run."""
        with self._lock:
            record = self._current_record(lease)
            self._verify_scope(record, scope)
            if record.state is LeaseState.QUARANTINED:
                raise WriterQuarantinedError("quarantined owners cannot be released")
            if record.active_run is not None:
                raise WriterRunError("cannot release while a native run is active")
            if (
                record.host_write_active
                or record.host_state_update_token is not None
                or record.acknowledged_config is not None
                or record.continuation_config is not None
            ):
                raise WriterRunError(
                    "cannot release while checkpoint publication is unresolved"
                )

            record.state = LeaseState.RELEASED
            lease._state = LeaseState.RELEASED
            self._owners.pop(scope.owner_key)

    def _current_record(self, lease: WriterLease) -> _OwnerRecord:
        if not isinstance(lease, WriterLease):
            raise WriterLeaseError("operation requires a WriterLease capability")
        if lease._registry_token is not self._registry_token:
            raise WriterLeaseError("lease belongs to another registry")
        record = self._owners.get(lease._scope.owner_key)
        if record is None or record.lease is not lease:
            raise WriterLeaseError("lease is stale or no longer owns this key")
        if record.state is LeaseState.QUARANTINED:
            raise WriterQuarantinedError("owner is quarantined")
        if record.state is not LeaseState.ACTIVE:
            raise WriterLeaseError("lease is not active")
        return record

    def _verify_scope(
        self,
        record: _OwnerRecord,
        scope: WriterScope,
    ) -> None:
        if not isinstance(scope, WriterScope) or not record.lease._scope.matches(scope):
            self._quarantine_record(record, QuarantineReason.BINDING_MISMATCH)
            raise WriterScopeError("scope identity differs from the leased binding")

    @staticmethod
    def _validate_native_control(native_control: object) -> None:
        """Require the native LangGraph drain contract without emulating it."""
        try:
            from langgraph.runtime import RunControl

            if not isinstance(native_control, RunControl):
                raise WriterRunError(
                    "native control must be a LangGraph RunControl instance"
                )
            request_drain = getattr(native_control, "request_drain")
            drain_requested = getattr(native_control, "drain_requested")
            getattr(native_control, "drain_reason")
        except WriterRunError:
            raise
        except Exception as exc:
            raise WriterRunError(
                "native control must expose request_drain, drain_requested, and drain_reason"
            ) from exc
        if not callable(request_drain):
            raise WriterRunError("native control request_drain must be callable")
        if type(drain_requested) is not bool:
            raise WriterRunError("native control drain_requested must be a bool")
        if drain_requested:
            raise WriterRunError("a drained native control cannot start a new run")

    def _remember_native_control(
        self,
        native_control: object,
    ) -> None:
        """Reject reuse and retain bounded evidence for each native control."""
        live_refs = []
        for native_ref in self._native_control_refs:
            prior_control = native_ref()
            if prior_control is native_control:
                raise WriterRunError("native control has already been used for a run")
            if prior_control is not None:
                live_refs.append(native_ref)
        self._native_control_refs = live_refs

        if any(control is native_control for control in self._strong_native_controls):
            raise WriterRunError("native control has already been used for a run")
        strong_control_count = len(self._strong_native_controls)

        if len(live_refs) + strong_control_count >= _MAX_LIVE_NATIVE_CONTROL_REFS:
            raise WriterCapacityError(
                "retained native control capacity reached; refusing a new run"
            )

        try:
            native_ref = weakref.ref(native_control)
        except TypeError as exc:
            # LangGraph RunControl is slotted without __weakref__. Retain the
            # exact object in bounded registry history so it cannot be reused
            # after a normal finish or lease release.
            try:
                from langgraph.runtime import RunControl
            except Exception:
                raise WriterRunError(
                    "native controls must support weak references"
                ) from exc
            if isinstance(native_control, RunControl):
                self._strong_native_controls.append(native_control)
                return
            raise WriterRunError(
                "native controls must support weak references"
            ) from exc
        self._native_control_refs.append(native_ref)

    def _verify_run_handle(
        self,
        record: _OwnerRecord,
        lease: WriterLease,
        handle: WriterRunHandle,
    ) -> None:
        if (
            not isinstance(handle, WriterRunHandle)
            or handle._registry_token is not self._registry_token
            or handle._lease is not lease
            or record.active_run is not handle
            or handle._terminal_state is not None
        ):
            raise WriterRunError("run handle is foreign, stale, or already consumed")

    @staticmethod
    def _quarantine_record(
        record: _OwnerRecord,
        reason: QuarantineReason,
    ) -> None:
        record.state = LeaseState.QUARANTINED
        record.quarantine_reason = reason
        record.host_write_active = False
        record.host_state_update_token = None
        record.host_state_update_started = False
        record.acknowledged_config = None
        record.continuation_config = None
        record.continuation_checkpoint = None
        if record.active_run is not None:
            record.active_run._terminal_state = RunControlState.QUARANTINED


__all__ = [
    "LeaseState",
    "HostWriteOutcome",
    "CheckpointResetOutcome",
    "QuarantineReason",
    "RunControlState",
    "RunOutcome",
    "WriterCapacityError",
    "WriterLease",
    "WriterLifecycleError",
    "WriterLifecycleRegistry",
    "WriterOwnerConflictError",
    "WriterQuarantinedError",
    "WriterRunError",
    "WriterRunHandle",
    "WriterScope",
    "WriterScopeError",
    "WriterBindingContext",
    "bind_writer_scope",
    "current_writer_binding",
    "revalidate_writer_binding",
]


@dataclass(frozen=True, slots=True)
class WriterBindingContext:
    """Context-bound registry capability and the request scope it authenticates."""

    registry: WriterLifecycleRegistry = field(repr=False)
    lease: WriterLease = field(repr=False)
    scope: WriterScope = field(repr=False)
    scope_provider: Callable[[], WriterScope] = field(repr=False)


_current_writer_binding: ContextVar[WriterBindingContext | None] = ContextVar(
    "current_writer_binding", default=None,
)


@contextmanager
def bind_writer_scope(
    registry: WriterLifecycleRegistry,
    lease: WriterLease,
    scope: WriterScope,
    *,
    scope_provider: Callable[[], WriterScope],
) -> Iterator[WriterBindingContext]:
    """Bind the request's exact lease/scope for downstream host code.

    The trusted host still supplies a fresh authoritative scope to
    ``revalidate_writer_binding`` after each await; the context variable alone
    does not validate epoch freshness.
    """
    if not callable(scope_provider):
        raise WriterScopeError("trusted scope provider is required")
    registry.assert_scope(lease, scope)
    binding = WriterBindingContext(registry, lease, scope, scope_provider)
    token = _current_writer_binding.set(binding)
    try:
        yield binding
    finally:
        _current_writer_binding.reset(token)


def current_writer_binding(*, required: bool = True) -> WriterBindingContext | None:
    """Return the current request binding, if one was explicitly installed."""
    binding = _current_writer_binding.get()
    if binding is None and required:
        raise WriterLeaseError("no host-owned writer binding is active")
    return binding


def revalidate_writer_binding(
    current_scope: WriterScope,
    *,
    binding: WriterBindingContext | None = None,
) -> None:
    """Revalidate an authoritative current scope against the pinned lease."""
    active = binding if binding is not None else current_writer_binding()
    if active is None:
        raise WriterLeaseError("no host-owned writer binding is active")
    active.registry.assert_scope(active.lease, current_scope)
