"""Host-owned, process-local lifecycle for checkpoint writers.

This is deliberately narrower than a saver-wide or cross-process fence. A
lease is minted only for the exact graph returned by the trusted factory and
is bound to one validated request graph and its host epochs.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


_GRAPH_PROVENANCE_ATTR = "_hwrag_writer_graph_provenance"
_registry_lock = threading.RLock()
_owners: dict[str, "WriterLease"] = {}
_resetting: set[str] = set()
_current_lease: contextvars.ContextVar["WriterLease | None"] = contextvars.ContextVar(
    "hwrag_writer_lease", default=None,
)


class WriterLifecycleError(RuntimeError):
    """A writer operation cannot prove exclusive host ownership."""


@dataclass(frozen=True, slots=True)
class _GraphProvenance:
    graph_ref: weakref.ReferenceType[Any]
    saver: Any
    nonce: object = field(default_factory=object)


@dataclass(eq=False, slots=True)
class _NativeRun:
    graph: Any
    control: Any
    config_identity: tuple[str, str, str]
    drain_acknowledged: bool = False
    joined: bool = False


@dataclass(frozen=True, slots=True)
class _PauseReceipt:
    checkpoint_id: str
    next_nodes: tuple[str, ...]
    interrupt_fingerprints: tuple[str, ...]
    pending_call_fingerprints: tuple[tuple[str, str], ...]


def mint_graph_provenance(agent: Any) -> bool:
    """Mark one factory-created native graph; copies cannot inherit ownership."""
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.graph.state import CompiledStateGraph

        if type(agent) is not CompiledStateGraph:
            return False
        saver = agent.checkpointer
        if type(saver) not in (InMemorySaver, AsyncSqliteSaver):
            return False
        if getattr(agent, _GRAPH_PROVENANCE_ATTR, None) is not None:
            return False
        provenance = _GraphProvenance(weakref.ref(agent), saver)
        setattr(agent, _GRAPH_PROVENANCE_ATTR, provenance)
        return getattr(agent, _GRAPH_PROVENANCE_ATTR, None) is provenance
    except Exception:
        return False


def _graph_provenance(agent: Any) -> _GraphProvenance:
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.graph.state import CompiledStateGraph
    except Exception as exc:
        raise WriterLifecycleError("Native graph ownership is unavailable") from exc
    provenance = getattr(agent, _GRAPH_PROVENANCE_ATTR, None)
    if (type(agent) is not CompiledStateGraph or type(provenance) is not _GraphProvenance
            or provenance.graph_ref() is not agent or provenance.saver is not agent.checkpointer
            or type(provenance.saver) not in (InMemorySaver, AsyncSqliteSaver)):
        raise WriterLifecycleError("Unowned, copied, or unsupported graph writer")
    return provenance


def _config_identity(config: Any, binding: Any) -> tuple[str, str, str]:
    if type(config) is not dict or type(config.get("configurable")) is not dict:
        raise WriterLifecycleError("Writer config is not a native request config")
    conf = config["configurable"]
    if (conf.get("thread_id") != binding.graph_thread_id
            or conf.get("hwrag_request_graph_binding") != binding.as_config_value()
            or conf.get("checkpoint_ns", "") != ""
            or "checkpointer" in config):
        raise WriterLifecycleError("Writer config overrides its request graph binding")
    checkpoint_id = conf.get("checkpoint_id", "")
    if type(checkpoint_id) is not str:
        raise WriterLifecycleError("Invalid checkpoint config")
    return binding.graph_thread_id, "", checkpoint_id


def _pending_call_fingerprints(pending: Any) -> tuple[tuple[str, str], ...]:
    if type(pending) is not list or not 1 <= len(pending) <= 64:
        raise WriterLifecycleError("Invalid pending tool-call set")
    result: list[tuple[str, str]] = []
    budget = 64 * 1024
    for call in pending:
        if type(call) is not dict:
            raise WriterLifecycleError("Invalid pending tool call")
        call_id, name, args = call.get("call_id"), call.get("name"), call.get("args")
        if (type(call_id) is not str or not 1 <= len(call_id) <= 256
                or type(name) is not str or not name or type(args) is not dict):
            raise WriterLifecycleError("Invalid pending tool call identity")
        try:
            encoded = json.dumps(
                [name, args], ensure_ascii=False, sort_keys=True, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WriterLifecycleError("Pending tool arguments are not stable JSON") from exc
        budget -= len(encoded)
        if budget < 0:
            raise WriterLifecycleError("Pending tool-call receipt exceeds its size bound")
        result.append((call_id, hashlib.sha256(encoded).hexdigest()))
    if len({call_id for call_id, _ in result}) != len(result):
        raise WriterLifecycleError("Duplicate pending tool-call ID")
    return tuple(result)


def _interrupt_fingerprints(snapshot: Any) -> tuple[str, ...]:
    tasks = getattr(snapshot, "tasks", ()) or ()
    if type(tasks) not in (tuple, list):
        raise WriterLifecycleError("Checkpoint task interrupt state is not inspectable")
    result: list[str] = []
    for task in tasks:
        name = getattr(task, "name", None)
        interrupts = getattr(task, "interrupts", ()) or ()
        if type(name) is not str or not name or type(interrupts) not in (tuple, list):
            raise WriterLifecycleError("Checkpoint interrupts are not inspectable")
        task_interrupts = []
        for interrupt in interrupts:
            interrupt_id = getattr(interrupt, "id", None)
            if type(interrupt_id) is not str or not interrupt_id:
                raise WriterLifecycleError("Checkpoint interrupt identity is incomplete")
            value = getattr(interrupt, "value", None)
            namespace = getattr(interrupt, "ns", ()) or ()
            try:
                json.dumps(
                    [interrupt_id, value, namespace, getattr(interrupt, "when", None),
                     getattr(interrupt, "resumable", None)],
                    ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"),
                )
            except (TypeError, ValueError) as exc:
                raise WriterLifecycleError("Checkpoint interrupt is not stably fingerprintable") from exc
            task_interrupts.append([
                interrupt_id, value, namespace, getattr(interrupt, "when", None),
                getattr(interrupt, "resumable", None),
            ])
        try:
            encoded = json.dumps(
                [name, task_interrupts], ensure_ascii=False, sort_keys=True, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WriterLifecycleError("Checkpoint task state is not stably fingerprintable") from exc
        result.append(hashlib.sha256(encoded).hexdigest())
    return tuple(result)


def _make_pause_receipt(
    snapshot: Any, pending: list[dict], binding: Any, *, tools_node_name: str,
) -> _PauseReceipt:
    state_config = getattr(snapshot, "config", None)
    configurable = state_config.get("configurable") if type(state_config) is dict else None
    checkpoint_id = configurable.get("checkpoint_id") if type(configurable) is dict else None
    next_value = getattr(snapshot, "next", None)
    if (type(configurable) is not dict or configurable.get("thread_id") != binding.graph_thread_id
            or configurable.get("checkpoint_ns", "") != ""
            or type(checkpoint_id) is not str or not 1 <= len(checkpoint_id) <= 128
            or any(ord(char) < 33 or ord(char) == 127 for char in checkpoint_id)
            or type(next_value) not in (tuple, list)
            or any(type(node) is not str for node in next_value)
            or tools_node_name not in next_value):
        raise WriterLifecycleError("Paused checkpoint is not an exact tools-node checkpoint")
    return _PauseReceipt(
        checkpoint_id=checkpoint_id,
        next_nodes=tuple(next_value),
        interrupt_fingerprints=_interrupt_fingerprints(snapshot),
        pending_call_fingerprints=_pending_call_fingerprints(pending),
    )


def quarantine_paused_writer_owner(config: Any) -> None:
    """Invalidate a retained pause after a failed resume readback or epoch check."""
    thread_id = None
    binding = None
    try:
        from src.agent.request_context import request_graph_binding_from_config

        binding = request_graph_binding_from_config(config)
        thread_id = binding.graph_thread_id if binding is not None else None
    except Exception:
        pass
    if thread_id is None and type(config) is dict and type(config.get("configurable")) is dict:
        thread_id = config["configurable"].get("thread_id")
    if type(thread_id) is not str:
        return
    with _registry_lock:
        owner = _owners.get(thread_id)
        if owner is not None and owner.status == "paused" and (binding is None or owner.binding == binding):
            owner.quarantine()


@dataclass(eq=False, slots=True)
class WriterLease:
    graph: Any
    saver: Any
    binding: Any
    session_epoch: str
    memory_epoch: str | None
    validate_epochs: Callable[[], None]
    status: str = "active"
    active_run: _NativeRun | None = None
    last_joined_run: _NativeRun | None = None
    last_drained_run: _NativeRun | None = None
    continuation_config: dict | None = None
    host_write_active: bool = False
    pause_receipt: _PauseReceipt | None = None
    generation: int = 1

    def validate(self, agent: Any, config: Any) -> tuple[str, str, str]:
        if agent is not self.graph or agent.checkpointer is not self.saver:
            raise WriterLifecycleError("Writer lease used with a replaced graph or saver")
        _graph_provenance(agent)
        identity = _config_identity(config, self.binding)
        if self.status in {"quarantined", "closed"}:
            raise WriterLifecycleError("Writer lease is unresolved")
        try:
            self.validate_epochs()
        except Exception as exc:
            self.quarantine()
            raise WriterLifecycleError("Request epoch changed during writer lifetime") from exc
        return identity

    def start_run(self, agent: Any, config: Any) -> _NativeRun:
        identity = self.validate(agent, config)
        if self.active_run is not None or self.host_write_active:
            raise WriterLifecycleError("Overlapping native or host writer refused")
        if self.status == "paused":
            raise WriterLifecycleError("Paused request requires a newly acquired lease")
        if self.continuation_config is not None:
            expected = self.continuation_config
            actual = config
            if (type(actual) is not dict or actual != expected
                    or identity[2] != expected.get("configurable", {}).get("checkpoint_id")):
                raise WriterLifecycleError("Compaction continuation config did not match its acknowledgement")
            self.continuation_config = None
        try:
            from langgraph.runtime import RunControl
            control = RunControl()
        except Exception as exc:
            raise WriterLifecycleError("Cooperative native drain is unavailable") from exc
        run = _NativeRun(agent, control, identity)
        self.active_run = run
        self.status = "active"
        return run

    def request_drain(self, run: _NativeRun, reason: str) -> None:
        if self.active_run is not run or run.graph is not self.graph:
            raise WriterLifecycleError("Drain request does not own the active native run")
        self.validate(self.graph, {"configurable": {
            "thread_id": self.binding.graph_thread_id,
            "hwrag_request_graph_binding": self.binding.as_config_value(),
            "checkpoint_ns": run.config_identity[1],
            **({"checkpoint_id": run.config_identity[2]} if run.config_identity[2] else {}),
        }})
        run.control.request_drain(reason)

    def acknowledge_graph_drained(self, run: _NativeRun, error: BaseException) -> bool:
        if self.active_run is not run or not run.control.drain_requested:
            return False
        try:
            from langgraph.errors import GraphDrained
        except Exception:
            return False
        if not isinstance(error, GraphDrained):
            return False
        run.drain_acknowledged = True
        return True

    def finish_run(self, run: _NativeRun, *, joined: bool) -> None:
        if self.active_run is not run:
            self.quarantine()
            return
        self.active_run = None
        run.joined = joined
        if not joined:
            self.quarantine()
            return
        self.last_joined_run = run
        if run.drain_acknowledged:
            self.last_drained_run = run
        self.status = "idle"

    def assert_compaction_safe(self, agent: Any, config: Any) -> tuple[str, str, str]:
        identity = self.validate(agent, config)
        run = self.last_drained_run
        if (self.active_run is not None or self.host_write_active or run is None
                or not run.joined or not run.drain_acknowledged or run.graph is not agent
                or run.config_identity[:2] != identity[:2]):
            raise WriterLifecycleError("Compaction requires an acknowledged, joined GraphDrained run")
        return identity

    def authorize_continuation(self, config: dict) -> None:
        self.validate(self.graph, config)
        if (self.active_run is not None or self.host_write_active or self.last_drained_run is None
                or not self.last_drained_run.joined or not self.last_drained_run.drain_acknowledged):
            raise WriterLifecycleError("No drained run is available for continuation")
        self.continuation_config = config.copy()
        self.continuation_config["configurable"] = config["configurable"].copy()
        self.last_drained_run = None

    def begin_host_write(self, agent: Any, config: Any) -> None:
        self.validate(agent, config)
        if self.active_run is not None or self.host_write_active:
            raise WriterLifecycleError("Host update overlaps another writer")
        self.host_write_active = True

    def finish_host_write(self, *, acknowledged: bool) -> None:
        if not self.host_write_active:
            self.quarantine()
            return
        self.host_write_active = False
        if not acknowledged:
            self.quarantine()
        else:
            self.status = "idle"

    def mark_paused(
        self, agent: Any, config: Any, snapshot: Any, pending: list[dict],
        *, tools_node_name: str,
    ) -> None:
        self.validate(agent, config)
        receipt = _make_pause_receipt(
            snapshot, pending, self.binding, tools_node_name=tools_node_name,
        )
        with _registry_lock:
            if (_owners.get(self.binding.graph_thread_id) is not self
                    or self.active_run is not None or self.host_write_active or not pending
                    or self.last_joined_run is None or not self.last_joined_run.joined):
                raise WriterLifecycleError("Cannot pause with an active or unresolved writer")
            self.pause_receipt = receipt
            self.status = "paused"

    def quarantine(self) -> None:
        with _registry_lock:
            self.status = "quarantined"
            self.continuation_config = None
            self.pause_receipt = None


def acquire_writer_lease(
    agent: Any,
    config: dict,
    *,
    business_session_id: str,
    request_id: str,
    session_epoch: str,
    memory_epoch: str | None,
    validate_epochs: Callable[[], None],
    resume_snapshot: Any = None,
    pending_calls: list[dict] | None = None,
    tools_node_name: str | None = None,
) -> WriterLease:
    """Acquire or atomically replace an owner after exact paused-state readback."""
    from src.agent.request_context import request_graph_binding_from_config

    try:
        binding = request_graph_binding_from_config(config)
    except Exception as exc:
        quarantine_paused_writer_owner(config)
        raise WriterLifecycleError("Request graph binding could not be read") from exc
    if (binding is None or binding.business_session_id != business_session_id
            or binding.request_id != request_id or type(session_epoch) is not str
            or not callable(validate_epochs)):
        quarantine_paused_writer_owner(config)
        raise WriterLifecycleError("Request graph identity or epoch is not host-verified")
    if memory_epoch is not None and type(memory_epoch) is not str:
        quarantine_paused_writer_owner(config)
        raise WriterLifecycleError("Invalid memory epoch")
    try:
        provenance = _graph_provenance(agent)
        _config_identity(config, binding)
        validate_epochs()
        receipt = None
        if resume_snapshot is not None:
            if type(tools_node_name) is not str or not tools_node_name or pending_calls is None:
                raise WriterLifecycleError("Resume readback evidence is incomplete")
            receipt = _make_pause_receipt(
                resume_snapshot, pending_calls, binding, tools_node_name=tools_node_name,
            )
    except BaseException as exc:
        quarantine_paused_writer_owner(config)
        if isinstance(exc, WriterLifecycleError):
            raise
        raise WriterLifecycleError("Writer provenance, config, or epoch is invalid") from exc
    lease = WriterLease(agent, provenance.saver, binding, session_epoch, memory_epoch, validate_epochs)
    with _registry_lock:
        if any(
            owner.binding.business_session_id == business_session_id
            and owner.binding.graph_thread_id != binding.graph_thread_id
            and owner.status == "quarantined"
            for owner in _owners.values()
        ):
            raise WriterLifecycleError("Quarantined graph owner exists for this business session")
        if business_session_id in _resetting or binding.graph_thread_id in _resetting:
            previous = _owners.get(binding.graph_thread_id)
            if previous is not None and previous.status == "paused":
                previous.quarantine()
            raise WriterLifecycleError("Checkpoint reset is in progress")
        previous = _owners.get(binding.graph_thread_id)
        if previous is not None:
            try:
                validate_epochs()
                exact_takeover = (
                    previous.status == "paused"
                    and previous.binding == binding
                    and previous.saver is provenance.saver
                    and previous.session_epoch == session_epoch
                    and previous.memory_epoch == memory_epoch
                    and previous.active_run is None
                    and not previous.host_write_active
                    and previous.pause_receipt is not None
                    and receipt is not None
                    and previous.pause_receipt == receipt
                )
            except BaseException as exc:
                previous.quarantine()
                raise WriterLifecycleError("Paused owner epoch/readback could not be verified") from exc
            if not exact_takeover:
                if previous.status == "paused":
                    previous.quarantine()
                raise WriterLifecycleError("Another or unresolved graph owner exists")
            lease.generation = previous.generation + 1
            previous.quarantine()
        elif receipt is not None:
            raise WriterLifecycleError("Resume readback has no retained paused owner")
        _owners[binding.graph_thread_id] = lease
    return lease


@contextmanager
def bind_writer_lease(lease: WriterLease) -> Iterator[WriterLease]:
    token = _current_lease.set(lease)
    try:
        yield lease
    finally:
        _current_lease.reset(token)
        with _registry_lock:
            if _owners.get(lease.binding.graph_thread_id) is lease:
                if lease.status == "paused":
                    _owners[lease.binding.graph_thread_id] = lease
                elif lease.status == "quarantined" or lease.active_run is not None or lease.host_write_active:
                    lease.quarantine()
                    _owners[lease.binding.graph_thread_id] = lease
                else:
                    _owners.pop(lease.binding.graph_thread_id, None)
            # A newer generation already owns the key; old cleanup is a no-op.


def current_writer_lease(*, required: bool = True) -> WriterLease | None:
    lease = _current_lease.get()
    if lease is None and required:
        raise WriterLifecycleError("No host-owned writer lease is active")
    return lease


@contextmanager
def checkpoint_reset_guard(thread_id: str) -> Iterator[None]:
    """Reserve reset against active, paused, or unresolved request writers."""
    if type(thread_id) is not str or not thread_id:
        raise WriterLifecycleError("Invalid checkpoint reset target")
    with _registry_lock:
        if thread_id in _resetting:
            raise WriterLifecycleError("Checkpoint reset already in progress")
        owners = [lease for lease in _owners.values()
                  if lease.binding.business_session_id == thread_id
                  or lease.binding.graph_thread_id == thread_id]
        if owners:
            raise WriterLifecycleError("Checkpoint reset refused while a request owner exists")
        _resetting.add(thread_id)
    try:
        yield
    finally:
        with _registry_lock:
            _resetting.discard(thread_id)


async def join_owned_task(awaitable: Any) -> Any:
    """Shield and join a host-owned async operation before propagating cancel."""
    task = asyncio.create_task(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def join_native_exit_tasks(error: BaseException) -> None:
    """Join native exit tasks attached to cancellation exceptions, if present."""
    pending = list(error.args)
    seen: set[int] = set()
    tasks: list[asyncio.Task] = []
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, asyncio.Task):
            tasks.append(value)
        elif isinstance(value, (tuple, list, set)):
            pending.extend(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
    for task in tasks:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done():
                raise
        except BaseException:
            pass
