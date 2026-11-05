"""Compatibility facade over the frozen baseline writer registry.

Production code uses ``src.agent.writer_bridge`` directly.  This module keeps
the small test-facing API used by the context acceptance fixtures, but every
lease/handle is minted and owned by ``compact.writer_lifecycle``; there is no
second process-global owner registry here.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterator

from src.agent.async_join import join_native_exit_tasks, join_owned_task
from src.agent.compact.writer_lifecycle import (
    HostWriteOutcome,
    QuarantineReason,
    RunOutcome,
    WriterLifecycleError,
    WriterLifecycleRegistry,
    WriterLease as _BaselineLease,
    WriterRunHandle,
    WriterScope,
    bind_writer_scope,
)


_CURRENT_LEASE: ContextVar["CompatibleWriterLease | None"] = ContextVar(
    "compatible_writer_lease", default=None,
)
_REGISTRIES: dict[int, WriterLifecycleRegistry] = {}


def _registry_for_saver(saver: Any) -> WriterLifecycleRegistry:
    key = id(saver)
    registry = _REGISTRIES.get(key)
    if registry is None:
        registry = WriterLifecycleRegistry(known_savers=(saver,))
        _REGISTRIES[key] = registry
    return registry


@dataclass
class CompatibleRun:
    control: Any
    handle: WriterRunHandle
    graph: Any
    drain_acknowledged: bool = False
    joined: bool = False


@dataclass
class CompatibleWriterLease:
    registry: WriterLifecycleRegistry
    raw_lease: _BaselineLease
    scope: WriterScope
    validate_epochs: Any = field(default=lambda: None, repr=False)
    pause_receipt: Any = None
    status: str = "active"
    continuation_config: Any = None
    business_session_id: str = ""
    request_id: str = ""
    session_delete_epoch: str = ""

    def scope_provider(self) -> WriterScope:
        self.validate_epochs()
        return self.scope

    @property
    def saver(self) -> Any:
        return self.scope.saver

    @property
    def binding(self) -> Any:
        return SimpleNamespace(
            graph_thread_id=self.scope.owner_key,
            business_session_id="",
            request_id="",
        )

    def validate(self, agent: Any, config: Any) -> None:
        if agent is not self.scope.graph:
            raise WriterLifecycleError("Writer lease used with a replaced graph")
        self.registry.assert_scope(self.raw_lease, self.scope_provider())

    def start_run(self, agent: Any, config: Any) -> CompatibleRun:
        self.validate(agent, config)
        try:
            from langgraph.runtime import RunControl
        except Exception as exc:
            raise WriterLifecycleError("Cooperative native drain is unavailable") from exc
        control = RunControl()
        handle = self.registry.begin_run(
            self.raw_lease, self.scope_provider(), control,
            execution_config=config,
        )
        self.continuation_config = None
        return CompatibleRun(control=control, handle=handle, graph=agent)

    def request_drain(self, run: CompatibleRun, reason: str) -> bool:
        return self.registry.request_drain(
            self.raw_lease, self.scope_provider(), run.handle, reason,
        )

    def acknowledge_graph_drained(
        self, run: CompatibleRun, error: BaseException,
    ) -> bool:
        acknowledged = self.registry.acknowledge_graph_drained(
            self.raw_lease, self.scope_provider(), run.handle, error,
        )
        run.drain_acknowledged = acknowledged
        return acknowledged

    def finish_run(self, run: CompatibleRun, *, joined: bool) -> None:
        if not joined:
            self.quarantine()
            return
        self.registry.finish_run(
            self.raw_lease, self.scope_provider(), run.handle,
            joined=True, outcome=RunOutcome.KNOWN_TERMINAL,
        )
        run.joined = True
        self.status = "idle"

    def assert_compaction_safe(self, agent: Any, config: Any) -> WriterRunHandle:
        self.validate(agent, config)
        try:
            return self.registry.assert_compaction_safe(
                self.raw_lease, self.scope_provider(),
            )
        except Exception as exc:
            raise

    def authorize_continuation(self, config: dict) -> None:
        self.registry.authorize_continuation(
            self.raw_lease, self.scope_provider(),
            applied_config=config, execution_config=config,
        )

    def quarantine(self) -> None:
        self.status = "quarantined"
        import logging
        logging.getLogger(__name__).warning("DBG compat quarantine registry=%r scope=%r", self.registry, self.scope)
        try:
            self.registry.quarantine(
                self.raw_lease, self.scope_provider(),
                reason=QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
            )
        except BaseException:
            pass


def mint_graph_provenance(agent: Any) -> bool:
    """Compatibility validation; ownership is now proven by the scope facade."""
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.graph.state import CompiledStateGraph

        return (
            type(agent) is CompiledStateGraph
            and type(agent.checkpointer) in (InMemorySaver, AsyncSqliteSaver)
        )
    except Exception:
        return False


def acquire_writer_lease(
    agent: Any,
    config: dict,
    *,
    business_session_id: str,
    request_id: str,
    session_epoch: Any,
    memory_epoch: Any = None,
    validate_epochs: Any = lambda: None,
) -> CompatibleWriterLease:
    owner_key = config.get("configurable", {}).get("thread_id")
    saver = agent.checkpointer
    registry = _registry_for_saver(saver)
    if type(session_epoch) is str:
        epoch = (
            session_epoch
            if session_epoch == "" or (
                len(session_epoch) == 32
                and all(char in "0123456789abcdef" for char in session_epoch)
            )
            else hashlib.sha256(session_epoch.encode("utf-8")).hexdigest()[:32]
        )
    else:
        epoch = object()
    scope = WriterScope(
        owner_key=owner_key,
        graph=agent,
        saver=saver,
        request_scope=object(),
        epoch=epoch,
    )
    raw = registry.acquire(scope)
    return CompatibleWriterLease(
        registry=registry,
        raw_lease=raw,
        scope=scope,
        validate_epochs=validate_epochs,
        business_session_id=business_session_id,
        request_id=request_id,
        session_delete_epoch=(
            session_epoch if type(session_epoch) is str else ""
        ),
    )


@contextmanager
def bind_writer_lease(lease: CompatibleWriterLease) -> Iterator[CompatibleWriterLease]:
    from src.agent.writer_bridge import bind_request_identity

    token = _CURRENT_LEASE.set(lease)
    try:
        with bind_request_identity(
            lease.business_session_id,
            lease.request_id,
            lease.session_delete_epoch,
        ):
            with bind_writer_scope(
                lease.registry, lease.raw_lease, lease.scope,
                scope_provider=lease.scope_provider,
            ):
                yield lease
    finally:
        _CURRENT_LEASE.reset(token)


def current_writer_lease(*, required: bool = True) -> CompatibleWriterLease | None:
    lease = _CURRENT_LEASE.get()
    if lease is None and required:
        raise WriterLifecycleError("No host-owned writer lease is active")
    return lease


__all__ = [
    "CompatibleRun", "CompatibleWriterLease", "HostWriteOutcome",
    "WriterLifecycleError", "acquire_writer_lease", "bind_writer_lease",
    "current_writer_lease", "join_native_exit_tasks", "join_owned_task",
    "mint_graph_provenance",
]
