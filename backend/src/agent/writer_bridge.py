"""Adapt the frozen baseline writer registry for the context pipeline.

The baseline ``compact.writer_lifecycle`` owns graph/saver provenance, epoch
fencing, drain/join, host write and continuation authorization.  This module
is intentionally a thin bridge: it never keeps a second owner registry.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from src.agent.compact.writer_lifecycle import (
    HostWriteOutcome,
    QuarantineReason,
    RunOutcome,
    WriterBindingContext,
    WriterLease,
    WriterLifecycleError,
    WriterRunError,
    WriterRunHandle,
    WriterScope,
    current_writer_binding,
)


@dataclass(frozen=True, slots=True)
class WriterRequestIdentity:
    business_session_id: str
    request_id: str
    session_delete_epoch: str = ""


_REQUEST_IDENTITY: ContextVar[WriterRequestIdentity | None] = ContextVar(
    "writer_request_identity", default=None,
)


@contextmanager
def bind_request_identity(
    business_session_id: str, request_id: str, session_delete_epoch: str = "",
) -> Iterator[WriterRequestIdentity]:
    """Bind the host request identity used by publication checks."""
    identity = WriterRequestIdentity(
        business_session_id, request_id, session_delete_epoch,
    )
    token = _REQUEST_IDENTITY.set(identity)
    try:
        yield identity
    finally:
        _REQUEST_IDENTITY.reset(token)


def current_request_identity(
    *, required: bool = True,
) -> WriterRequestIdentity | None:
    identity = _REQUEST_IDENTITY.get()
    if identity is None and required:
        raise WriterLifecycleError("No host request identity is active")
    return identity


def current_request_binding(
    *, required: bool = True,
) -> WriterBindingContext | None:
    return current_writer_binding(required=required)


def _scope_from_config(binding: WriterBindingContext, config: Any) -> WriterScope:
    scope = binding.scope_provider()
    configurable = config.get("configurable") if type(config) is dict else None
    thread_id = configurable.get("thread_id") if type(configurable) is dict else None
    if thread_id != scope.owner_key:
        raise WriterLifecycleError("Writer scope does not own this graph thread")
    binding.registry.assert_scope(binding.lease, scope)
    return scope


def assert_current(agent: Any, config: Any) -> WriterScope:
    """Re-read the route epoch authority and authenticate the lease."""
    binding = current_request_binding()
    scope = _scope_from_config(binding, config)
    if scope.graph is not agent:
        raise WriterLifecycleError("Writer lease used with a replaced graph")
    return scope


def current_scope() -> WriterScope:
    binding = current_request_binding()
    scope = binding.scope_provider()
    binding.registry.assert_scope(binding.lease, scope)
    return scope


def current_saver() -> Any:
    return current_scope().saver


def begin_native_run(agent: Any, config: Any) -> WriterRunHandle:
    """Create one native RunControl and register it with the baseline owner."""
    binding = current_request_binding()
    scope = assert_current(agent, config)
    try:
        from langgraph.runtime import RunControl
    except Exception as exc:
        raise WriterLifecycleError("Cooperative native drain is unavailable") from exc
    control = RunControl()
    return binding.registry.begin_run(
        binding.lease, scope, control, execution_config=config,
    )


def request_drain(handle: WriterRunHandle, reason: str) -> bool:
    binding = current_request_binding()
    scope = current_scope()
    return binding.registry.request_drain(
        binding.lease, scope, handle, reason,
    )


def acknowledge_drained(handle: WriterRunHandle, error: BaseException) -> bool:
    binding = current_request_binding()
    scope = current_scope()
    return binding.registry.acknowledge_graph_drained(
        binding.lease, scope, handle, error,
    )


def finish_native_run(
    handle: WriterRunHandle, *, joined: bool, had_error: bool,
) -> None:
    """Finish/join a native run; unresolved outcomes quarantine, never raise."""
    binding = current_request_binding(required=False)
    if binding is None:
        return
    try:
        scope = current_scope()
    except BaseException:
        return
    if joined and not had_error:
        try:
            binding.registry.finish_run(
                binding.lease, scope, handle,
                joined=True, outcome=RunOutcome.KNOWN_TERMINAL,
            )
        except BaseException:
            _quarantine_with(binding, scope, QuarantineReason.RUN_OUTCOME_UNKNOWN)
    else:
        _quarantine_with(binding, scope, QuarantineReason.JOIN_UNCONFIRMED)


def assert_compaction_safe(agent: Any, config: Any) -> WriterRunHandle:
    binding = current_request_binding()
    scope = assert_current(agent, config)
    return binding.registry.assert_compaction_safe(binding.lease, scope)


def begin_host_write() -> None:
    binding = current_request_binding()
    binding.registry.begin_host_write(binding.lease, current_scope())


def finish_host_write(
    *, acknowledged: bool, applied_config: dict | None = None,
) -> None:
    binding = current_request_binding()
    binding.registry.finish_host_write(
        binding.lease,
        current_scope(),
        outcome=(
            HostWriteOutcome.ACKNOWLEDGED
            if acknowledged else HostWriteOutcome.NOT_ATTEMPTED
        ),
        applied_config=applied_config if acknowledged else None,
    )


def authorize_continuation(
    applied_config: dict, execution_config: dict,
) -> None:
    binding = current_request_binding()
    binding.registry.authorize_continuation(
        binding.lease,
        current_scope(),
        applied_config=applied_config,
        execution_config=execution_config,
    )


def _quarantine_with(
    binding: WriterBindingContext, scope: WriterScope, reason: QuarantineReason,
) -> None:
    try:
        binding.registry.quarantine(binding.lease, scope, reason=reason)
    except BaseException:
        pass


def quarantine(reason: QuarantineReason | None = None) -> None:
    binding = current_request_binding(required=False)
    if binding is None:
        return
    try:
        scope = binding.scope_provider()
        binding.registry.assert_scope(binding.lease, scope)
    except BaseException:
        scope = binding.scope
    _quarantine_with(
        binding, scope, reason or QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
    )
