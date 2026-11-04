"""Fail-closed mid-run compaction checkpoint replacement.

This module only replaces a checkpoint after the native graph run drained and
all of its owned tasks joined. It does not create or publish an initial START
checkpoint. Callers must pass a bound RunControl to ``astream`` and revalidate
the current request scope after every await.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from langchain_core.messages import RemoveMessage
from langgraph.checkpoint.base import BaseCheckpointSaver, get_checkpoint_metadata
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from .writer_lifecycle import (
    HostWriteOutcome,
    QuarantineReason,
    WriterLease,
    WriterLifecycleError,
    WriterLifecycleRegistry,
    WriterScope,
)


class StaleCompaction(RuntimeError):
    """The latest checkpoint no longer matches the state being compacted."""


@dataclass(frozen=True, slots=True)
class CheckpointPublicationOutcome:
    """Graph apply state and host receipt state are independent.

    ``applied_config`` is the exact saver acknowledgement when one was
    observed. ``execution_config`` is present only after readback and the
    resumable state have both been validated and the lease authorized the
    exact config object for one continuation run.
    """

    status: Literal["APPLIED", "REFUSED", "OUTCOME_UNKNOWN"]
    receipt_status: Literal["NOT_REQUESTED", "CONFIRMED", "UNKNOWN"] = "NOT_REQUESTED"
    applied_config: dict | None = None
    execution_config: dict | None = None

    def __post_init__(self) -> None:
        if self.status not in ("APPLIED", "REFUSED", "OUTCOME_UNKNOWN"):
            raise ValueError("invalid checkpoint publication status")
        if self.receipt_status not in ("NOT_REQUESTED", "CONFIRMED", "UNKNOWN"):
            raise ValueError("invalid host receipt status")
        if self.status != "APPLIED" and self.execution_config is not None:
            raise ValueError("only an applied checkpoint can authorize continuation")

    @property
    def graph_apply_status(self) -> Literal["APPLIED", "REFUSED", "OUTCOME_UNKNOWN"]:
        """Explicit name for the graph checkpoint result."""
        return self.status

    @property
    def host_receipt_status(self) -> Literal["NOT_REQUESTED", "CONFIRMED", "UNKNOWN"]:
        """Explicit name for the independent host-side receipt result."""
        return self.receipt_status


class _ConditionalPublicationSaver(BaseCheckpointSaver):
    """One-shot saver adapter with a conditional checkpoint replacement."""

    def __init__(self, saver: object, expected_checkpoint_id: str) -> None:
        super().__init__(serde=saver.serde)
        self.saver = saver
        self.expected_checkpoint_id = expected_checkpoint_id
        self.attempted = False
        self.write_started = False
        self.known_refusal = False
        self.owner_conflict = False
        self.ack_config: dict | None = None

    def get_next_version(self, current, channel):
        return self.saver.get_next_version(current, channel)

    async def aget_tuple(self, config):
        if type(self.saver) is AsyncSqliteSaver:
            if self.saver.is_setup is not True or self.saver.conn.in_transaction:
                self.known_refusal = True
                raise StaleCompaction("SQLite saver is unready or has a foreign transaction")
            # The native method acquires saver.lock itself. The conditional SQL
            # write rechecks the parent under that same lock and transaction.
            saved = await self.saver.aget_tuple(config)
        else:
            saved = await self.saver.aget_tuple(config)
        if (
            saved is None
            or saved.checkpoint.get("id") != self.expected_checkpoint_id
        ):
            self.known_refusal = True
            self.owner_conflict = True
            raise StaleCompaction("checkpoint changed before replacement")
        return saved

    async def aput_writes(self, config, writes, task_id, task_path=""):
        # LangGraph may attach only the messages edit marker to the old parent.
        # Refuse tool, push, or unrelated writes instead of mutating old work.
        if any(channel != "messages" for channel, _value in writes):
            self.known_refusal = True
            raise StaleCompaction("unsupported pending writes during compaction")

    async def aput(self, config, checkpoint, metadata, new_versions):
        if self.attempted:
            self.known_refusal = True
            raise StaleCompaction("publication adapter is one-shot")
        self.attempted = True
        conf = config.get("configurable") if type(config) is dict else None
        if (
            type(conf) is not dict
            or conf.get("checkpoint_id") != self.expected_checkpoint_id
        ):
            self.known_refusal = True
            raise StaleCompaction("unexpected checkpoint parent")

        if type(self.saver) is AsyncSqliteSaver:
            return await self._put_sqlite(config, checkpoint, metadata, new_versions)
        if type(self.saver) is not InMemorySaver:
            self.known_refusal = True
            raise StaleCompaction("unsupported checkpoint saver")

        latest_config = {
            "configurable": {
                "thread_id": conf["thread_id"],
                "checkpoint_ns": conf.get("checkpoint_ns", ""),
            }
        }
        # The exact native memory saver compares and writes synchronously with
        # no await between them. This protects one event loop, not foreign
        # threads or processes that bypass the writer registry.
        latest = self.saver.get_tuple(latest_config)
        if (
            latest is None
            or latest.checkpoint.get("id") != self.expected_checkpoint_id
            or any(
                channel not in ("__error__", "__interrupt__")
                for _task_id, channel, _value in latest.pending_writes or []
            )
        ):
            self.known_refusal = True
            self.owner_conflict = True
            raise StaleCompaction("latest checkpoint or pending writes changed")

        self.write_started = True
        applied = self.saver.put(config, checkpoint, metadata, new_versions)
        if type(applied) is not dict:
            raise StaleCompaction("memory saver returned no checkpoint acknowledgement")
        self.ack_config = applied
        return applied

    async def _put_sqlite(self, config, checkpoint, metadata, new_versions):
        saver = self.saver
        kind, payload = saver.serde.dumps_typed(checkpoint)
        encoded_metadata = json.dumps(
            get_checkpoint_metadata(config, metadata), ensure_ascii=False,
        ).encode("utf-8")
        conf = config["configurable"]
        thread_id = str(conf["thread_id"])
        checkpoint_ns = conf.get("checkpoint_ns", "")
        conditional_refusal = False

        async with saver.lock:
            async def commit_conditional_insert() -> dict:
                nonlocal conditional_refusal
                owned_transaction = False
                try:
                    if saver.is_setup is not True or saver.conn.in_transaction:
                        self.known_refusal = True
                        raise StaleCompaction(
                            "SQLite saver is unready or has a foreign transaction"
                        )
                    self.write_started = True
                    async with saver.conn.execute("BEGIN IMMEDIATE"):
                        owned_transaction = True
                    async with saver.conn.execute(
                        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
                        "parent_checkpoint_id, type, checkpoint, metadata) "
                        "SELECT ?, ?, ?, ?, ?, ?, ? WHERE "
                        "(SELECT checkpoint_id FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? "
                        "ORDER BY checkpoint_id DESC LIMIT 1)=? AND NOT EXISTS "
                        "(SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns=? "
                        "AND checkpoint_id=? AND channel NOT IN ('__error__', '__interrupt__'))",
                        (
                            thread_id,
                            checkpoint_ns,
                            checkpoint["id"],
                            self.expected_checkpoint_id,
                            kind,
                            payload,
                            encoded_metadata,
                            thread_id,
                            checkpoint_ns,
                            self.expected_checkpoint_id,
                            thread_id,
                            checkpoint_ns,
                            self.expected_checkpoint_id,
                        ),
                    ) as cursor:
                        if cursor.rowcount != 1:
                            conditional_refusal = True
                            self.owner_conflict = True
                            raise StaleCompaction(
                                "latest checkpoint changed before publication"
                            )
                    await saver.conn.commit()
                    owned_transaction = False
                    ack = {
                        "configurable": {
                            "thread_id": conf["thread_id"],
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": checkpoint["id"],
                        }
                    }
                    self.ack_config = ack
                    return ack
                except BaseException:
                    if owned_transaction and saver.conn.in_transaction:
                        try:
                            await saver.conn.rollback()
                        except BaseException:
                            self.known_refusal = False
                            raise
                    if conditional_refusal:
                        self.known_refusal = True
                    raise

            task = asyncio.create_task(commit_conditional_insert())
            return await _join_owned_task(task)


async def _join_owned_task(task: asyncio.Task):
    """Join a host-owned task before forwarding cancellation to its caller."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


def _normalized_checkpoint_config(
    config: object,
    *,
    graph_thread_id: str,
    checkpoint_ns: str,
) -> dict | None:
    """Accept only the exact saver config for this graph thread and namespace."""
    if type(config) is not dict or type(config.get("configurable")) is not dict:
        return None
    conf = config["configurable"]
    namespace = conf.get("checkpoint_ns", "")
    checkpoint_id = conf.get("checkpoint_id")
    if (
        conf.get("thread_id") != graph_thread_id
        or namespace != checkpoint_ns
        or type(namespace) is not str
        or len(namespace) > 128
        or any(ord(char) < 32 or ord(char) == 127 for char in namespace)
        or type(checkpoint_id) is not str
        or not 1 <= len(checkpoint_id) <= 128
        or any(ord(char) < 33 or ord(char) == 127 for char in checkpoint_id)
    ):
        return None
    return {
        **config,
        "configurable": {**conf, "checkpoint_ns": namespace},
    }


def _scope_from_context(
    registry: WriterLifecycleRegistry,
    lease: WriterLease,
    scope: WriterScope,
    current_scope: Callable[[], WriterScope],
) -> WriterScope:
    """Read and verify the host's authoritative scope at a transition point."""
    try:
        observed = current_scope()
    except asyncio.CancelledError:
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        raise
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        raise
    except Exception as exc:
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        raise WriterLifecycleError("authoritative scope could not be revalidated") from exc
    if inspect.isawaitable(observed):
        if inspect.iscoroutine(observed):
            observed.close()
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        raise WriterLifecycleError(
            "current scope provider must return WriterScope synchronously"
        )
    if not isinstance(observed, WriterScope):
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        raise WriterLifecycleError("current scope provider must return WriterScope")
    try:
        registry.assert_scope(lease, observed)
    except asyncio.CancelledError:
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        raise
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        raise
    except Exception as exc:
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        if isinstance(exc, WriterLifecycleError):
            raise
        raise WriterLifecycleError(
            "authoritative scope could not be verified"
        ) from exc
    return observed


def _quarantine(
    registry: WriterLifecycleRegistry,
    lease: WriterLease,
    scope: WriterScope,
    reason: QuarantineReason,
) -> None:
    try:
        registry.quarantine(lease, scope, reason=reason)
    except WriterLifecycleError:
        # Scope mismatch and an existing quarantine are already fail-closed.
        pass


async def publish_compacted_checkpoint(
    agent,
    config: dict,
    original_messages: list,
    compacted_messages: list,
    *,
    registry: WriterLifecycleRegistry,
    lease: WriterLease,
    scope: WriterScope,
    current_scope: Callable[[], WriterScope],
    receipt_callback: Callable[[dict, object], bool | Awaitable[bool]] | None = None,
) -> CheckpointPublicationOutcome:
    """Conditionally replace one drained checkpoint and prove its resumption.

    ``current_scope`` must synchronously rebuild a scope from authoritative
    request and epoch state. It is checked after every await. The request epoch
    helpers return ``""`` or a UUID-hex string; pass that string as ``scope.epoch``
    and return the latest string on each check. Exact token value is compared,
    so a fresh ``WriterScope`` instance is valid when graph, saver, and the
    identity-bearing ``request_scope`` token are unchanged. A ``None`` epoch
    means the authority is unavailable and must fail closed. Do not synthesize
    ``epoch_generation``; it is optional and only for a real counter source.
    ``receipt_callback`` is an optional host-side receipt operation. A receipt
    result never changes the graph apply status.
    """
    try:
        _scope_from_context(registry, lease, scope, current_scope)
        if scope.graph is not agent or scope.saver is not getattr(agent, "checkpointer", None):
            return CheckpointPublicationOutcome("REFUSED")
        if type(config) is not dict or type(config.get("configurable")) is not dict:
            return CheckpointPublicationOutcome("REFUSED")
        if config["configurable"].get("thread_id") != scope.owner_key:
            return CheckpointPublicationOutcome("REFUSED")
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        if checkpoint_ns != "" or type(checkpoint_ns) is not str:
            return CheckpointPublicationOutcome("REFUSED")
        if type(original_messages) is not list or not original_messages:
            return CheckpointPublicationOutcome("REFUSED")
        if type(compacted_messages) is not list or not compacted_messages:
            return CheckpointPublicationOutcome("REFUSED")
        saver = scope.saver
        if type(saver) not in (InMemorySaver, AsyncSqliteSaver):
            return CheckpointPublicationOutcome("REFUSED")
        registry.begin_host_write(lease, scope)
    except asyncio.CancelledError:
        _quarantine(
            registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
        raise
    except WriterLifecycleError:
        return CheckpointPublicationOutcome("REFUSED")

    host_write_reserved = True
    adapter: _ConditionalPublicationSaver | None = None
    applied_config: dict | None = None
    receipt_status: Literal["NOT_REQUESTED", "CONFIRMED", "UNKNOWN"] = (
        "NOT_REQUESTED" if receipt_callback is None else "UNKNOWN"
    )

    def mark_not_attempted() -> None:
        nonlocal host_write_reserved
        if host_write_reserved:
            registry.finish_host_write(
                lease,
                scope,
                outcome=HostWriteOutcome.NOT_ATTEMPTED,
            )
            host_write_reserved = False

    try:
        _scope_from_context(registry, lease, scope, current_scope)
        snapshot = await agent.aget_state(config)
        _scope_from_context(registry, lease, scope, current_scope)

        current_messages = snapshot.values.get("messages", [])
        if current_messages != original_messages:
            mark_not_attempted()
            return CheckpointPublicationOutcome("REFUSED")
        message_ids = [getattr(message, "id", None) for message in original_messages]
        if (
            not all(type(message_id) is str and message_id for message_id in message_ids)
            or len(set(message_ids)) != len(message_ids)
        ):
            mark_not_attempted()
            return CheckpointPublicationOutcome("REFUSED")

        expected_checkpoint_id = (
            snapshot.config.get("configurable", {}).get("checkpoint_id")
        )
        parent_config = _normalized_checkpoint_config(
            snapshot.config,
            graph_thread_id=scope.owner_key,
            checkpoint_ns=checkpoint_ns,
        )
        if (
            type(expected_checkpoint_id) is not str
            or not expected_checkpoint_id
            or parent_config is None
            or parent_config["configurable"].get("checkpoint_id")
            != expected_checkpoint_id
        ):
            mark_not_attempted()
            return CheckpointPublicationOutcome("REFUSED")

        adapter = _ConditionalPublicationSaver(saver, expected_checkpoint_id)
        publishing_graph = agent.copy({"checkpointer": adapter})
        update = {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *compacted_messages,
            ]
        }
        _scope_from_context(registry, lease, scope, current_scope)
        reported_config = await _join_owned_task(
            asyncio.create_task(
                publishing_graph.aupdate_state(parent_config, update)
            )
        )
        _scope_from_context(registry, lease, scope, current_scope)

        ack = adapter.ack_config
        ack_normalized = _normalized_checkpoint_config(
            ack,
            graph_thread_id=scope.owner_key,
            checkpoint_ns=checkpoint_ns,
        )
        reported_normalized = _normalized_checkpoint_config(
            reported_config,
            graph_thread_id=scope.owner_key,
            checkpoint_ns=checkpoint_ns,
        )
        if ack is None:
            if adapter.owner_conflict:
                mark_not_attempted()
                _quarantine(
                    registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
                return CheckpointPublicationOutcome("REFUSED")
            if adapter.write_started:
                _quarantine(
                    registry, lease, scope, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
                )
                host_write_reserved = False
                return CheckpointPublicationOutcome("OUTCOME_UNKNOWN")
            mark_not_attempted()
            if adapter.known_refusal:
                _quarantine(
                    registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
            return CheckpointPublicationOutcome("REFUSED")
        if (
            ack_normalized is None
            or reported_normalized is None
            or reported_normalized != ack_normalized
        ):
            _quarantine(
                registry, lease, scope, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
            )
            host_write_reserved = False
            return CheckpointPublicationOutcome(
                "OUTCOME_UNKNOWN",
                applied_config=copy.deepcopy(ack),
            )

        applied_config = copy.deepcopy(ack)
        registry.finish_host_write(
            lease,
            scope,
            outcome=HostWriteOutcome.ACKNOWLEDGED,
            applied_config=applied_config,
        )
        host_write_reserved = False

        execution_config = copy.deepcopy(config)
        execution_config["configurable"].update(
            ack_normalized["configurable"]
        )

        _scope_from_context(registry, lease, scope, current_scope)
        saved = await saver.aget_tuple(ack_normalized)
        _scope_from_context(registry, lease, scope, current_scope)
        saved_parent = (
            (saved.parent_config or {}).get("configurable", {}).get("checkpoint_id")
            if saved is not None
            else None
        )
        if (
            saved is None
            or saved.checkpoint.get("id")
            != ack_normalized["configurable"]["checkpoint_id"]
            or saved_parent != expected_checkpoint_id
            or saved.pending_writes
        ):
            raise StaleCompaction("exact saver readback did not match the acknowledged replacement")

        _scope_from_context(registry, lease, scope, current_scope)
        observed = await agent.aget_state(execution_config)
        _scope_from_context(registry, lease, scope, current_scope)
        observed_conf = observed.config.get("configurable", {})
        tasks = tuple(getattr(observed, "tasks", ()) or ())
        resumable = (
            observed_conf.get("thread_id") == scope.owner_key
            and observed_conf.get("checkpoint_ns", "") == checkpoint_ns
            and observed_conf.get("checkpoint_id")
            == ack_normalized["configurable"]["checkpoint_id"]
            and observed.values.get("messages", []) == compacted_messages
            and tuple(observed.next or ()) == ("model",)
            and len(tasks) == 1
            and getattr(tasks[0], "name", None) == "model"
            and getattr(tasks[0], "error", None) is None
            and not getattr(tasks[0], "interrupts", ())
            and not getattr(observed, "interrupts", ())
        )
        if not resumable:
            _quarantine(
                registry, lease, scope, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
            )
            return CheckpointPublicationOutcome(
                "APPLIED",
                applied_config=applied_config,
            )

        registry.authorize_continuation(
            lease,
            scope,
            applied_config=applied_config,
            execution_config=execution_config,
        )

        if receipt_callback is not None:
            try:
                _scope_from_context(registry, lease, scope, current_scope)
                receipt_result = receipt_callback(applied_config, observed)
                if inspect.isawaitable(receipt_result):
                    receipt_result = await receipt_result
                _scope_from_context(registry, lease, scope, current_scope)
                if receipt_result is not True:
                    _quarantine(
                        registry,
                        lease,
                        scope,
                        QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                    )
                    return CheckpointPublicationOutcome(
                        "APPLIED",
                        receipt_status="UNKNOWN",
                        applied_config=applied_config,
                    )
                receipt_status = "CONFIRMED"
            except asyncio.CancelledError:
                _quarantine(
                    registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
                raise
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                _quarantine(
                    registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
                raise
            except Exception:
                _quarantine(
                    registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
                return CheckpointPublicationOutcome(
                    "APPLIED",
                    receipt_status="UNKNOWN",
                    applied_config=applied_config,
                )

        return CheckpointPublicationOutcome(
            "APPLIED",
            receipt_status=receipt_status,
            applied_config=applied_config,
            execution_config=execution_config,
        )
    except asyncio.CancelledError:
        write_started = adapter is not None and adapter.write_started
        if host_write_reserved and not write_started:
            try:
                mark_not_attempted()
            except WriterLifecycleError:
                write_started = True
        _quarantine(
            registry,
            lease,
            scope,
            (
                QuarantineReason.WRITE_OUTCOME_UNKNOWN
                if write_started
                else QuarantineReason.CALLER_REPORTED_UNCERTAINTY
            ),
        )
        raise
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        _quarantine(
            registry, lease, scope, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
        )
        raise
    except Exception:
        if adapter is not None and adapter.ack_config is not None:
            _quarantine(
                registry, lease, scope, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
            )
            return CheckpointPublicationOutcome(
                "OUTCOME_UNKNOWN",
                applied_config=copy.deepcopy(adapter.ack_config),
            )
        if adapter is not None and adapter.known_refusal:
            try:
                mark_not_attempted()
            except WriterLifecycleError:
                _quarantine(
                    registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
            if adapter.owner_conflict:
                _quarantine(
                    registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
                )
            return CheckpointPublicationOutcome("REFUSED")
        if adapter is not None and adapter.write_started:
            _quarantine(
                registry, lease, scope, QuarantineReason.WRITE_OUTCOME_UNKNOWN,
            )
            return CheckpointPublicationOutcome("OUTCOME_UNKNOWN")
        try:
            mark_not_attempted()
        except WriterLifecycleError:
            _quarantine(
                registry, lease, scope, QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
            )
        return CheckpointPublicationOutcome("REFUSED")


__all__ = [
    "CheckpointPublicationOutcome",
    "StaleCompaction",
    "publish_compacted_checkpoint",
]
