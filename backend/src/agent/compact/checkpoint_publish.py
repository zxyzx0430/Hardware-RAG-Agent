"""Fail-closed compaction publication against a specific latest checkpoint."""
import asyncio
import copy
import hashlib
import json
import logging
import os
import secrets
import types
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from langchain_core.messages import RemoveMessage
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, get_checkpoint_metadata
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

_GRAPH_FENCE_ISSUER = object()
_GRAPH_FENCE_MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
_GRAPH_FENCE_MAX_METADATA_BYTES = 1024 * 1024
_GRAPH_CHECKPOINT_HASH_FIELDS = (
    "thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id",
    "candidate_sha256",
)


@dataclass(frozen=True, slots=True)
class GraphApplyFenceContext:
    """Private request-local V2 permit context passed to one publisher call."""

    expected_head: Any
    publication_binding: Any
    attempt_id: str
    attempt_sha256: str
    candidate_sha256: str
    graph_thread_id: str
    checkpoint_ns: str
    pre_apply_checkpoint_id: str | None

    def __post_init__(self) -> None:
        binding_id = getattr(self.publication_binding, "binding_id", None)
        binding_sha256 = getattr(self.publication_binding, "binding_sha256", None)
        if (type(self.attempt_id) is not str or not 1 <= len(self.attempt_id) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in self.attempt_id)
                or type(self.attempt_sha256) is not str
                or len(self.attempt_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.attempt_sha256)
                or type(self.candidate_sha256) is not str
                or len(self.candidate_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.candidate_sha256)
                or type(binding_id) is not str or len(binding_id) != 64
                or any(char not in "0123456789abcdef" for char in binding_id)
                or type(binding_sha256) is not str or len(binding_sha256) != 64
                or any(char not in "0123456789abcdef" for char in binding_sha256)
                or type(self.graph_thread_id) is not str
                or not 1 <= len(self.graph_thread_id) <= 128
                or type(self.checkpoint_ns) is not str
                or len(self.checkpoint_ns) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in self.checkpoint_ns)
                or (self.pre_apply_checkpoint_id is not None
                    and (type(self.pre_apply_checkpoint_id) is not str
                         or not 1 <= len(self.pre_apply_checkpoint_id) <= 128
                         or any(ord(char) < 33 or ord(char) == 127
                                for char in self.pre_apply_checkpoint_id)))):
            raise ValueError("Invalid V2 graph-apply fence context")


@dataclass(frozen=True, slots=True)
class _GraphSaverFenceProof:
    """Ephemeral host capability minted only while the exact saver txn is held.

    ``checkpoint_id`` and ``parent_checkpoint_id`` always describe the same
    observed checkpoint row. ``pre_apply_checkpoint_id`` separately names the
    attempt's expected old head. The object is never serialized or retained.
    """

    saver: Any = field(repr=False, compare=False)
    connection: Any = field(repr=False, compare=False)
    graph_db_path: str
    graph_db_path_sha256: str
    transaction_nonce: str
    phase: Literal[
        "APPLY_PARENT_CHECKED", "OLD_HEAD_UNCHANGED", "CANDIDATE_EXACT_READBACK",
    ]
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str | None
    parent_checkpoint_id: str | None
    observed_candidate_sha256: str | None
    pre_apply_checkpoint_id: str | None
    target_checkpoint_id: str
    target_parent_checkpoint_id: str | None
    target_candidate_sha256: str
    binding_id: str
    binding_sha256: str
    attempt_id: str
    attempt_sha256: str
    observed_graph_checkpoint_identity_sha256: str
    target_graph_checkpoint_identity_sha256: str
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _GRAPH_FENCE_ISSUER:
            raise ValueError("Graph saver fence proof was not host-minted")
        identity = _graph_checkpoint_identity_value(
            self.thread_id, self.checkpoint_ns, self.checkpoint_id,
            self.parent_checkpoint_id, self.observed_candidate_sha256,
        )
        target_identity = _graph_checkpoint_identity_value(
            self.thread_id, self.checkpoint_ns, self.target_checkpoint_id,
            self.target_parent_checkpoint_id, self.target_candidate_sha256,
        )
        if (type(self.target_checkpoint_id) is not str
                or not 1 <= len(self.target_checkpoint_id) <= 128
                or (self.target_parent_checkpoint_id is not None
                    and (type(self.target_parent_checkpoint_id) is not str
                         or not 1 <= len(self.target_parent_checkpoint_id) <= 128))
                or type(self.target_candidate_sha256) is not str
                or len(self.target_candidate_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.target_candidate_sha256)
                or type(self.binding_id) is not str or len(self.binding_id) != 64
                or any(char not in "0123456789abcdef" for char in self.binding_id)
                or type(self.binding_sha256) is not str or len(self.binding_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.binding_sha256)
                or type(self.attempt_id) is not str
                or not 1 <= len(self.attempt_id) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in self.attempt_id)
                or type(self.attempt_sha256) is not str or len(self.attempt_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.attempt_sha256)
                or (self.observed_candidate_sha256 is not None
                    and (type(self.observed_candidate_sha256) is not str
                         or len(self.observed_candidate_sha256) != 64
                         or any(char not in "0123456789abcdef"
                                for char in self.observed_candidate_sha256)))
                or _graph_identity_sha256(identity)
                    != self.observed_graph_checkpoint_identity_sha256
                or _graph_identity_sha256(target_identity)
                    != self.target_graph_checkpoint_identity_sha256
                or _path_sha256(self.graph_db_path) != self.graph_db_path_sha256
                or type(self.transaction_nonce) is not str
                or not 32 <= len(self.transaction_nonce) <= 64
                or any(char not in "0123456789abcdef" for char in self.transaction_nonce)
                or self.phase not in (
                    "APPLY_PARENT_CHECKED", "OLD_HEAD_UNCHANGED", "CANDIDATE_EXACT_READBACK",
                )
                or self.target_parent_checkpoint_id != self.pre_apply_checkpoint_id
                or (self.phase == "APPLY_PARENT_CHECKED"
                    and self.checkpoint_id != self.pre_apply_checkpoint_id)
                or (self.phase == "OLD_HEAD_UNCHANGED"
                    and self.checkpoint_id != self.pre_apply_checkpoint_id)
                or (self.phase == "CANDIDATE_EXACT_READBACK"
                    and (self.checkpoint_id != self.target_checkpoint_id
                         or self.parent_checkpoint_id != self.target_parent_checkpoint_id
                         or self.observed_candidate_sha256
                            != self.target_candidate_sha256))):
            raise ValueError("Graph saver fence proof identity mismatch")


@dataclass(frozen=True, slots=True)
class _ObservedGraphCheckpoint:
    checkpoint_id: str | None
    parent_checkpoint_id: str | None
    checkpoint_bytes: int
    metadata_bytes: int
    has_conflicting_writes: bool
    candidate_sha256: str | None


def _graph_checkpoint_identity_value(
    thread_id: str, checkpoint_ns: str, checkpoint_id: str | None,
    parent_checkpoint_id: str | None, candidate_sha256: str | None,
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "checkpoint_ns": checkpoint_ns,
        "checkpoint_id": checkpoint_id,
        "parent_checkpoint_id": parent_checkpoint_id,
        "candidate_sha256": candidate_sha256,
    }


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(encoded).hexdigest()


def _graph_identity_sha256(identity: dict[str, Any]) -> str:
    if type(identity) is not dict or tuple(identity) != _GRAPH_CHECKPOINT_HASH_FIELDS:
        raise ValueError("Invalid graph checkpoint identity field order")
    return _canonical_sha256(identity)


def _path_sha256(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8", errors="strict")).hexdigest()


def _normalize_sqlite_path(path: Any) -> str:
    if not isinstance(path, (str, os.PathLike)):
        raise StaleCompaction("SQLite path is unavailable")
    value = os.fspath(path)
    if not value or value == ":memory:" or value.startswith("file:"):
        raise StaleCompaction("A persistent SQLite file path is required for V2 fencing")
    return os.path.normcase(os.path.realpath(os.path.abspath(value)))


def _require_v2_database_paths(saver: Any) -> tuple[str, str]:
    """Require the host DB and graph saver to use distinct persistent files."""
    if type(saver) is not AsyncSqliteSaver:
        raise StaleCompaction("V2 publication requires the native SQLite graph saver")
    try:
        from app.db.database import DB_PATH
        from src.agent.agent_factory import _SQLITE_CHECKPOINT_PATH, _global_checkpointer

        graph_path = _normalize_sqlite_path(_SQLITE_CHECKPOINT_PATH)
        owner_path = _normalize_sqlite_path(DB_PATH)
        if _global_checkpointer is not saver:
            raise StaleCompaction("V2 publication does not own the process graph saver")
        if graph_path == owner_path:
            raise StaleCompaction("V2 owner and graph databases must be separate files")
        if os.path.exists(graph_path) and os.path.exists(owner_path) \
                and os.path.samefile(graph_path, owner_path):
            raise StaleCompaction("V2 owner and graph databases resolve to the same file")
        return graph_path, owner_path
    except StaleCompaction:
        raise
    except Exception as exc:
        raise StaleCompaction("V2 database path separation is unverified") from exc


def _set_active_graph_fence_nonce(saver: Any) -> str:
    if (type(saver) is not AsyncSqliteSaver or saver.is_setup is not True
            or not saver.lock.locked() or saver.conn.in_transaction is not True
            or getattr(saver, "_active_graph_fence_nonce", None) is not None):
        raise StaleCompaction("Graph saver transaction cannot be fenced")
    nonce = secrets.token_hex(24)
    try:
        saver._active_graph_fence_nonce = nonce
    except Exception as exc:
        raise StaleCompaction("Graph saver does not accept a live fence nonce") from exc
    return nonce


def _clear_active_graph_fence_nonce(saver: Any, nonce: str | None) -> None:
    if nonce is not None and getattr(saver, "_active_graph_fence_nonce", None) == nonce:
        del saver._active_graph_fence_nonce


@dataclass(frozen=True, slots=True)
class _GraphSaverTransactionFence:
    saver: Any = field(repr=False, compare=False)
    connection: Any = field(repr=False, compare=False)
    graph_database_path: str
    owner_database_path: str
    transaction_nonce: str = field(repr=False, compare=False)

    def assert_active(self) -> None:
        if (type(self.saver) is not AsyncSqliteSaver
                or self.saver.conn is not self.connection
                or self.saver.is_setup is not True
                or self.saver.lock.locked() is not True
                or self.connection.in_transaction is not True
                or getattr(self.saver, "_active_graph_fence_nonce", None)
                    != self.transaction_nonce):
            raise StaleCompaction("Graph saver fence is no longer active")

    async def read_latest(
        self, thread_id: str, checkpoint_ns: str, *, include_candidate_digest: bool = False,
    ) -> _ObservedGraphCheckpoint:
        self.assert_active()
        if (type(thread_id) is not str or not thread_id
                or type(checkpoint_ns) is not str or len(checkpoint_ns) > 128):
            raise StaleCompaction("Invalid fenced graph checkpoint scope")
        if include_candidate_digest:
            async with self.connection.execute(
                "SELECT checkpoint_id, parent_checkpoint_id, length(checkpoint), length(metadata), "
                "CASE WHEN length(checkpoint)<=? THEN type END, "
                "CASE WHEN length(checkpoint)<=? THEN checkpoint END "
                "FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (_GRAPH_FENCE_MAX_CHECKPOINT_BYTES, _GRAPH_FENCE_MAX_CHECKPOINT_BYTES,
                 thread_id, checkpoint_ns),
            ) as cursor:
                row = await cursor.fetchone()
        else:
            async with self.connection.execute(
                "SELECT checkpoint_id, parent_checkpoint_id, length(checkpoint), length(metadata) "
                "FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (thread_id, checkpoint_ns),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            async with self.connection.execute(
                "SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns=? LIMIT 1",
                (thread_id, checkpoint_ns),
            ) as cursor:
                writes = await cursor.fetchone()
            return _ObservedGraphCheckpoint(None, None, 0, 0, writes is not None, None)
        checkpoint_id, parent_id, checkpoint_bytes, metadata_bytes = row[:4]
        if (type(checkpoint_id) is not str or not 1 <= len(checkpoint_id) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in checkpoint_id)
                or (parent_id is not None and (
                    type(parent_id) is not str or not 1 <= len(parent_id) <= 128
                    or any(ord(char) < 33 or ord(char) == 127 for char in parent_id)
                ))
                or type(checkpoint_bytes) is not int or checkpoint_bytes < 0
                or checkpoint_bytes > _GRAPH_FENCE_MAX_CHECKPOINT_BYTES
                or type(metadata_bytes) is not int or metadata_bytes < 0
                or metadata_bytes > _GRAPH_FENCE_MAX_METADATA_BYTES):
            raise StaleCompaction("Fenced graph checkpoint exceeds identity bounds")
        candidate_sha256 = None
        if include_candidate_digest:
            checkpoint_type, checkpoint_payload = row[4:]
            if (type(checkpoint_type) is not str
                    or not checkpoint_type or len(checkpoint_type) > 128
                    or type(checkpoint_payload) not in (bytes, bytearray)
                    or len(checkpoint_payload) != checkpoint_bytes):
                raise StaleCompaction("Candidate checkpoint payload is unavailable")
            try:
                checkpoint = self.saver.serde.loads_typed(
                    (checkpoint_type, bytes(checkpoint_payload)),
                )
                if type(checkpoint) is not dict or checkpoint.get("id") != checkpoint_id:
                    raise ValueError("Serialized checkpoint identity mismatch")
                values = checkpoint.get("channel_values") if type(checkpoint) is dict else None
                messages = values.get("messages") if type(values) is dict else None
                from app.api.compaction_checkpoint import _host_message_digest

                candidate_sha256 = _host_message_digest(messages)
            except Exception as exc:
                raise StaleCompaction("Candidate checkpoint digest is unavailable") from exc
        async with self.connection.execute(
            "SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? "
            "AND channel NOT IN ('__error__', '__interrupt__') LIMIT 1",
            (thread_id, checkpoint_ns, checkpoint_id),
        ) as cursor:
            conflict = await cursor.fetchone()
        self.assert_active()
        return _ObservedGraphCheckpoint(
            checkpoint_id, parent_id, checkpoint_bytes, metadata_bytes,
            conflict is not None, candidate_sha256,
        )

    def identity(
        self, thread_id: str, checkpoint_ns: str, observed: _ObservedGraphCheckpoint,
    ):
        self.assert_active()
        from app.api.rolling_revision_store import RollingHistoryV2GraphCheckpointIdentity

        if (observed.candidate_sha256 is not None
                and (type(observed.candidate_sha256) is not str
                     or len(observed.candidate_sha256) != 64
                     or any(char not in "0123456789abcdef"
                            for char in observed.candidate_sha256))):
            raise StaleCompaction("Invalid observed candidate digest")
        return RollingHistoryV2GraphCheckpointIdentity(
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=observed.checkpoint_id,
            parent_checkpoint_id=observed.parent_checkpoint_id,
            candidate_sha256=observed.candidate_sha256,
        )

    async def proof(
        self, *, phase: Literal[
            "APPLY_PARENT_CHECKED", "OLD_HEAD_UNCHANGED", "CANDIDATE_EXACT_READBACK",
        ], thread_id: str, checkpoint_ns: str, pre_apply_checkpoint_id: str | None,
        target_identity: Any, publication_binding: Any,
        attempt_id: str, attempt_sha256: str,
    ) -> _GraphSaverFenceProof:
        self.assert_active()
        from app.api.rolling_revision_store import (
            RollingHistoryV2GraphCheckpointIdentity,
            RollingHistoryV2SummaryPublicationBinding,
        )

        if (phase not in (
                    "APPLY_PARENT_CHECKED", "OLD_HEAD_UNCHANGED", "CANDIDATE_EXACT_READBACK",
                )
                or type(target_identity) is not RollingHistoryV2GraphCheckpointIdentity
                or type(publication_binding) is not RollingHistoryV2SummaryPublicationBinding
                or type(thread_id) is not str or not 1 <= len(thread_id) <= 128
                or type(checkpoint_ns) is not str or len(checkpoint_ns) > 128
                or target_identity.thread_id != thread_id
                or target_identity.checkpoint_ns != checkpoint_ns
                or target_identity.parent_checkpoint_id != pre_apply_checkpoint_id
                or type(target_identity.checkpoint_id) is not str
                or not 1 <= len(target_identity.checkpoint_id) <= 128
                or type(target_identity.candidate_sha256) is not str
                or len(target_identity.candidate_sha256) != 64
                or any(char not in "0123456789abcdef"
                       for char in target_identity.candidate_sha256)
                or target_identity.candidate_sha256
                    != publication_binding.host_candidate_sha256
                or (pre_apply_checkpoint_id is not None
                    and (type(pre_apply_checkpoint_id) is not str
                         or not 1 <= len(pre_apply_checkpoint_id) <= 128))
                or type(attempt_id) is not str or not 1 <= len(attempt_id) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in attempt_id)
                or type(attempt_sha256) is not str or len(attempt_sha256) != 64
                or any(char not in "0123456789abcdef" for char in attempt_sha256)):
            raise StaleCompaction("Invalid target graph checkpoint identity")

        observed_row = await self.read_latest(
            thread_id, checkpoint_ns,
            include_candidate_digest=phase == "CANDIDATE_EXACT_READBACK",
        )
        if observed_row.has_conflicting_writes:
            raise StaleCompaction("Pending graph writes prevent publication finalization")
        identity = self.identity(thread_id, checkpoint_ns, observed_row)
        if (phase in ("APPLY_PARENT_CHECKED", "OLD_HEAD_UNCHANGED")
                and identity.checkpoint_id != pre_apply_checkpoint_id):
            raise StaleCompaction("Graph parent changed before permit consumption")
        if (phase == "CANDIDATE_EXACT_READBACK"
                and (identity.checkpoint_id != target_identity.checkpoint_id
                     or identity.parent_checkpoint_id != target_identity.parent_checkpoint_id
                     or identity.candidate_sha256 != target_identity.candidate_sha256)):
            raise StaleCompaction("Graph candidate readback differs from target identity")
        identity_value = _graph_checkpoint_identity_value(
            identity.thread_id, identity.checkpoint_ns, identity.checkpoint_id,
            identity.parent_checkpoint_id, identity.candidate_sha256,
        )
        digest = _graph_identity_sha256(identity_value)
        target_identity_value = _graph_checkpoint_identity_value(
            target_identity.thread_id, target_identity.checkpoint_ns,
            target_identity.checkpoint_id, target_identity.parent_checkpoint_id,
            target_identity.candidate_sha256,
        )
        proof = _GraphSaverFenceProof(
            saver=self.saver,
            connection=self.connection,
            graph_db_path=self.graph_database_path,
            graph_db_path_sha256=_path_sha256(self.graph_database_path),
            transaction_nonce=self.transaction_nonce,
            phase=phase,
            thread_id=identity.thread_id,
            checkpoint_ns=identity.checkpoint_ns,
            checkpoint_id=identity.checkpoint_id,
            parent_checkpoint_id=identity.parent_checkpoint_id,
            observed_candidate_sha256=identity.candidate_sha256,
            pre_apply_checkpoint_id=pre_apply_checkpoint_id,
            target_checkpoint_id=target_identity.checkpoint_id,
            target_parent_checkpoint_id=target_identity.parent_checkpoint_id,
            target_candidate_sha256=target_identity.candidate_sha256,
            binding_id=publication_binding.binding_id,
            binding_sha256=publication_binding.binding_sha256,
            attempt_id=attempt_id,
            attempt_sha256=attempt_sha256,
            observed_graph_checkpoint_identity_sha256=digest,
            target_graph_checkpoint_identity_sha256=_graph_identity_sha256(
                target_identity_value,
            ),
            _issuer=_GRAPH_FENCE_ISSUER,
        )
        self.assert_active()
        return proof


async def _with_graph_saver_write_fence(saver: Any, operation: Callable) -> Any:
    """Run one recovery callback inside the shared saver lock and write txn.

    Recovery callbacks may commit a separate owner-database terminal marker.
    They must finish that commit before returning so the graph fence is not
    released while a stale permit can still appear current.
    """
    graph_path, owner_path = _require_v2_database_paths(saver)
    if not callable(operation):
        raise StaleCompaction("Graph fence callback is unavailable")
    async with saver.lock:
        if saver.is_setup is not True or saver.conn.in_transaction:
            raise StaleCompaction("Unready or foreign graph transaction")
        nonce = None
        try:
            async with saver.conn.execute("BEGIN IMMEDIATE"):
                pass
            nonce = _set_active_graph_fence_nonce(saver)
            fence = _GraphSaverTransactionFence(
                saver=saver,
                connection=saver.conn,
                graph_database_path=graph_path,
                owner_database_path=owner_path,
                transaction_nonce=nonce,
            )
            fence.assert_active()
            result = await operation(fence)
            fence.assert_active()
            await saver.conn.commit()
            return result
        except BaseException:
            if saver.conn.in_transaction:
                await saver.conn.rollback()
            raise
        finally:
            _clear_active_graph_fence_nonce(saver, nonce)


class StaleCompaction(RuntimeError):
    """The summarized checkpoint is no longer safe to replace."""


class _PublicationSaver(BaseCheckpointSaver):
    """One-update adapter; never mutate the live graph or its old pending writes.

    InMemorySaver compares and writes synchronously with no await between them;
    that is atomic among tasks on one event loop, not across arbitrary threads.
    AsyncSqliteSaver acknowledges only after its locked conditional insert and
    owned transaction commit complete. A started write without an ACK is unknown.
    """

    def __init__(self, saver, expected):
        super().__init__(serde=saver.serde)
        self.saver = saver
        self.expected = expected
        self.attempted = False
        self.ack_config = None

    def get_next_version(self, current, channel):
        return self.saver.get_next_version(current, channel)

    async def aget_tuple(self, config):
        if type(self.saver) is AsyncSqliteSaver:
            async with self.saver.lock:
                if self.saver.is_setup is not True or self.saver.conn.in_transaction:
                    raise StaleCompaction("Unready or foreign transaction")
        saved = await self.saver.aget_tuple(config)
        if saved is None or saved.checkpoint["id"] != self.expected:
            raise StaleCompaction("Checkpoint changed")
        return saved

    async def aput_writes(self, config, writes, task_id, task_path=""):
        # aupdate_state otherwise dirties the old checkpoint before aput succeeds.
        # This adapter permits messages plus the exact empty branch:to:model marker.
        if any(
            channel != "messages"
            and not (channel == "branch:to:model" and value is None)
            for channel, value in writes
        ):
            raise StaleCompaction("Unsupported checkpoint writes")

    async def aput(self, config, checkpoint, metadata, new_versions):
        if self.attempted or self.ack_config is not None:
            raise StaleCompaction("Publication adapter is one-shot")
        self.attempted = True
        if config["configurable"].get("checkpoint_id") != self.expected:
            raise StaleCompaction("Unexpected parent")
        if type(self.saver) is AsyncSqliteSaver:
            kind, payload = self.saver.serde.dumps_typed(checkpoint)
            encoded_meta = json.dumps(get_checkpoint_metadata(config, metadata), ensure_ascii=False).encode("utf-8")
            conf = config["configurable"]
            thread = str(conf["thread_id"])
            namespace = conf.get("checkpoint_ns", "")

            async with self.saver.lock:
                async def commit():
                    owned = False
                    fence_nonce = None
                    try:
                        if self.saver.is_setup is not True or self.saver.conn.in_transaction:
                            raise StaleCompaction("Unready or foreign transaction")
                        async with self.saver.conn.execute("BEGIN IMMEDIATE"):
                            owned = True
                        fence_nonce = _set_active_graph_fence_nonce(self.saver)
                        # One statement: another connection cannot insert between
                        # the latest-parent predicate and this checkpoint insert.
                        async with self.saver.conn.execute(
                            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
                            "parent_checkpoint_id, type, checkpoint, metadata) "
                            "SELECT ?, ?, ?, ?, ?, ?, ? WHERE "
                            "(SELECT checkpoint_id FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? "
                            "ORDER BY checkpoint_id DESC LIMIT 1)=? AND NOT EXISTS "
                            "(SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? "
                            "AND channel NOT IN ('__error__', '__interrupt__'))",
                            (thread, namespace, checkpoint["id"], self.expected, kind, payload,
                             encoded_meta, thread, namespace, self.expected,
                             thread, namespace, self.expected),
                        ) as cursor:
                            if cursor.rowcount != 1:
                                raise StaleCompaction("Checkpoint changed before publication")
                        await self.saver.conn.commit()
                        owned = False
                        self.ack_config = {"configurable": {
                            "thread_id": conf["thread_id"], "checkpoint_ns": namespace,
                            "checkpoint_id": checkpoint["id"],
                        }}
                    except BaseException:
                        if owned and self.saver.conn.in_transaction:
                            await self.saver.conn.rollback()
                            owned = False
                        raise
                    finally:
                        _clear_active_graph_fence_nonce(self.saver, fence_nonce)

                # Waiting for the lock remains cancellable with no write. Once
                # SQL starts, complete commit/rollback even on repeated cancel.
                task = asyncio.create_task(commit())
                await _join_initial_task(task)
            return {"configurable": {"thread_id": conf["thread_id"],
                    "checkpoint_ns": namespace, "checkpoint_id": checkpoint["id"]}}
        if type(self.saver) is not InMemorySaver:
            raise StaleCompaction("Unsupported saver")
        latest_config = {"configurable": {
            "thread_id": config["configurable"]["thread_id"],
            "checkpoint_ns": config["configurable"].get("checkpoint_ns", ""),
        }}
        # Exact native memory saver: no await between comparison and put.
        # Atomic only among tasks on this event loop, NOT arbitrary threads.
        latest = self.saver.get_tuple(latest_config)
        if latest is None or latest.checkpoint["id"] != self.expected:
            raise StaleCompaction("Checkpoint changed before publication")
        if any(channel not in ("__error__", "__interrupt__")
               for _task, channel, _value in latest.pending_writes or []):
            raise StaleCompaction("Concurrent pending writes")
        applied = self.saver.put(config, checkpoint, metadata, new_versions)
        self.ack_config = applied
        return applied


async def _run_host_record_transaction(operation, *args, **kwargs):
    """Propagate ContextVars and join DB work before passing cancellation on."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
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


def _host_publication_config(config, graph_thread_id, checkpoint_ns):
    """Bind a real save/read config; only normalize the native empty namespace."""
    if type(config) is not dict or type(config.get("configurable")) is not dict:
        return None
    conf = config["configurable"]
    namespace = conf.get("checkpoint_ns", "")
    checkpoint_id = conf.get("checkpoint_id")
    if (conf.get("thread_id") != graph_thread_id or namespace != checkpoint_ns
            or type(namespace) is not str or len(namespace) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in namespace)
            or type(checkpoint_id) is not str or not 1 <= len(checkpoint_id) <= 128
            or any(ord(char) < 33 or ord(char) == 127 for char in checkpoint_id)):
        return None
    # Both supported native savers use "" when checkpoint_ns is omitted. Never
    # fill a missing checkpoint ID or infer one from a latest-checkpoint read.
    return {**config, "configurable": {**conf, "checkpoint_ns": namespace}}


@dataclass(frozen=True, slots=True)
class _CompactionPublicationOutcome:
    """Typed graph publication ACK with host receipt state kept independent.

    ``status`` describes whether the graph replacement was applied. The host
    publication receipt is reported separately by ``receipt_status``.
    ``applied_config`` is the exact normalized saver acknowledgement; it may be
    present with OUTCOME_UNKNOWN when a later readback or continuation check
    could not finish. ``execution_config`` is only set after resumability is
    verified and the writer lease authorizes continuation.
    """

    status: Literal["APPLIED", "REFUSED", "OUTCOME_UNKNOWN"]
    execution_config: dict | None = None
    receipt_status: Literal["NOT_REQUESTED", "CONFIRMED", "UNKNOWN"] = "NOT_REQUESTED"
    applied_config: dict | None = None

    @property
    def graph_apply_status(self) -> Literal["APPLIED", "REFUSED", "OUTCOME_UNKNOWN"]:
        """Explicit alias for callers that need to distinguish graph from receipt."""
        return self.status

    @property
    def host_receipt_status(self) -> Literal["NOT_REQUESTED", "CONFIRMED", "UNKNOWN"]:
        """Explicit alias; receipt status never changes graph apply status."""
        return self.receipt_status


async def _publish_compacted_checkpoint_outcome(
    agent, config, original, compacted, *, writer_lease=None,
    host_context_scope=None, skills_mode="off",
) -> _CompactionPublicationOutcome:
    """Publish only after a joined cooperative drain; retain the exact save ack."""
    from src.agent.compact.writer_lifecycle import WriterLifecycleError
    from src.agent.async_join import join_owned_task
    from src.agent.writer_bridge import (
        assert_compaction_safe,
        assert_current,
        authorize_continuation,
        begin_host_write,
        current_request_binding,
        current_request_identity,
        current_saver,
        current_scope,
        finish_host_write,
        quarantine,
    )

    if current_request_binding(required=False) is None:
        return _CompactionPublicationOutcome("REFUSED")
    try:
        assert_compaction_safe(agent, config)
    except WriterLifecycleError:
        return _CompactionPublicationOutcome("REFUSED")
    saver = agent.checkpointer
    if saver is not current_saver() or type(saver) not in (InMemorySaver, AsyncSqliteSaver):
        return _CompactionPublicationOutcome("REFUSED")
    try:
        if type(saver) is AsyncSqliteSaver:
            async with saver.lock:
                if saver.is_setup is not True or saver.conn.in_transaction:
                    return _CompactionPublicationOutcome("REFUSED")
        assert_current(agent, config)
        snapshot = await agent.aget_state(config)
        assert_current(agent, config)
    except asyncio.CancelledError:
        quarantine()
        raise
    current = snapshot.values.get("messages", [])
    if not original or current != original:
        return _CompactionPublicationOutcome("REFUSED")
    ids = [message.id for message in original]
    if not all(isinstance(mid, str) and mid for mid in ids) or len(set(ids)) != len(ids):
        return _CompactionPublicationOutcome("REFUSED")
    expected = snapshot.config.get("configurable", {}).get("checkpoint_id")
    if type(expected) is not str or not expected:
        return _CompactionPublicationOutcome("REFUSED")
    checkpoint_ns = snapshot.config.get("configurable", {}).get("checkpoint_ns", "")
    if (snapshot.config.get("configurable", {}).get("thread_id") != current_scope().owner_key
            or checkpoint_ns != ""):
        return _CompactionPublicationOutcome("REFUSED")

    prepared = None
    graph_thread_id = current_scope().owner_key
    if host_context_scope is not None:
        try:
            from app.api.compaction_archive import get_compaction_scope
            from app.api.context_events import ContextEventScope
            from app.api.compaction_checkpoint import prepare_published_context

            if (type(host_context_scope) is ContextEventScope and skills_mode == "off"
                    and get_compaction_scope() == host_context_scope
                    and host_context_scope.session_id == current_request_identity().business_session_id
                    and host_context_scope.request_id == current_request_identity().request_id):
                assert_current(agent, config)
                prepared = await _run_host_record_transaction(
                    prepare_published_context, host_context_scope, original, compacted,
                    skills_mode=skills_mode, graph_thread_id=graph_thread_id,
                    checkpoint_ns=checkpoint_ns,
                )
                assert_current(agent, config)
        except asyncio.CancelledError:
            quarantine()
            raise
        except Exception:
            logger.warning("Host preparation unavailable; graph publication remains independent")

    adapter = _PublicationSaver(saver, expected)
    publishing = agent.copy({"checkpointer": adapter})
    applied_config = None
    update_error = None
    try:
        assert_compaction_safe(agent, config)
        begin_host_write()
        applied_config = await join_owned_task(publishing.aupdate_state(config, {"messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES), *compacted,
        ]}))
    except asyncio.CancelledError:
        quarantine()
        raise
    except StaleCompaction as exc:
        update_error = exc
    except Exception as exc:
        update_error = exc

    ack = adapter.ack_config
    if ack is None:
        if adapter.attempted or update_error is not None:
            quarantine()
            return _CompactionPublicationOutcome(
                "OUTCOME_UNKNOWN",
                receipt_status="UNKNOWN" if prepared is not None else "NOT_REQUESTED",
            )
        finish_host_write(acknowledged=False)
        return _CompactionPublicationOutcome("REFUSED")
    ack_normalized = _host_publication_config(ack, graph_thread_id, checkpoint_ns)
    reported = _host_publication_config(applied_config, graph_thread_id, checkpoint_ns)
    if ack_normalized is None or (reported is not None and reported != ack_normalized):
        quarantine()
        return _CompactionPublicationOutcome(
            "OUTCOME_UNKNOWN",
            receipt_status="UNKNOWN" if prepared is not None else "NOT_REQUESTED",
        )
    finish_host_write(acknowledged=True, applied_config=ack_normalized)
    execution_config = copy.deepcopy(config)
    execution_config["configurable"].update(ack_normalized["configurable"])
    try:
        assert_current(agent, execution_config)
        saved = await saver.aget_tuple(ack_normalized)
        assert_current(agent, execution_config)
        parent = (saved.parent_config or {}).get("configurable", {}).get("checkpoint_id") if saved else None
        if (saved is None or saved.checkpoint.get("id") != ack_normalized["configurable"]["checkpoint_id"]
                or parent != expected or saved.pending_writes):
            raise WriterLifecycleError("Exact replacement checkpoint readback did not match")
        observed = await agent.aget_state(execution_config)
        assert_current(agent, execution_config)
        observed_conf = observed.config.get("configurable", {})
        tasks = tuple(getattr(observed, "tasks", ()) or ())
        resumable = (
            observed_conf.get("thread_id") == graph_thread_id
            and observed_conf.get("checkpoint_id") == ack_normalized["configurable"]["checkpoint_id"]
            and observed.values.get("messages", []) == compacted
            and tuple(observed.next or ()) == ("model",)
            and len(tasks) == 1 and getattr(tasks[0], "name", None) == "model"
            and getattr(tasks[0], "error", None) is None
            and not getattr(tasks[0], "interrupts", ())
            and not getattr(observed, "interrupts", ())
        )
        if not resumable:
            quarantine()
            return _CompactionPublicationOutcome(
                "APPLIED",
                receipt_status="UNKNOWN" if prepared is not None else "NOT_REQUESTED",
                applied_config=copy.deepcopy(ack_normalized),
            )
        authorize_continuation(ack_normalized, execution_config)
    except asyncio.CancelledError:
        quarantine()
        raise
    except Exception:
        quarantine()
        return _CompactionPublicationOutcome(
            "OUTCOME_UNKNOWN",
            receipt_status="UNKNOWN" if prepared is not None else "NOT_REQUESTED",
            applied_config=copy.deepcopy(ack_normalized),
        )

    receipt_status = "NOT_REQUESTED"
    if prepared is not None:
        receipt_status = "UNKNOWN"
        try:
            from app.api.compaction_checkpoint import confirm_published_context

            head = await _run_host_record_transaction(
                confirm_published_context, host_context_scope, prepared,
                applied_config=ack_normalized, observed_config=observed.config,
                observed_messages=list(observed.values.get("messages", [])),
                skills_mode=skills_mode,
            )
            receipt_status = "CONFIRMED" if head is not None else "UNKNOWN"
        except asyncio.CancelledError:
            quarantine()
            raise
        except Exception:
            logger.warning("Graph applied without a confirmed host publication receipt")
    return _CompactionPublicationOutcome(
        "APPLIED", execution_config, receipt_status,
        applied_config=copy.deepcopy(ack_normalized),
    )


async def publish_compacted_checkpoint(
    agent, config, original, compacted, *, host_context_scope=None, skills_mode="off",
) -> bool:
    """Compatibility bool: True only when the applied graph is resumable."""
    outcome = await _publish_compacted_checkpoint_outcome(
        agent, config, original, compacted, host_context_scope=host_context_scope,
        skills_mode=skills_mode,
    )
    return outcome.status == "APPLIED" and outcome.execution_config is not None


# This capability is never serialized. In particular, copying its attribute to
# graph.copy() cannot authorize that new graph object.
_INITIAL_PROVENANCE_ATTR = "_hwrag_initial_publication_provenance"


@dataclass(frozen=True, slots=True)
class _InitialGraphProvenance:
    graph_ref: object
    saver: object
    snapshot: tuple
    keepalive: tuple


@dataclass(frozen=True, slots=True)
class _InitialPublicationResult:
    """Host-only outcome, never an instruction to retry raw input.

    APPLIED is graph acknowledgement plus exact readback, not consumption.
    receipt_status UNKNOWN includes confirm(None), exceptions and uncertainty;
    it does not mean definitely unregistered. Only CONFIRMED carries a head.
    save_ack_config is the exact private saver aput return after commit.
    applied_config is the real outer graph update return. execution_config
    adds only previously verified host routing to the save ack identity. Future
    callers must recheck epochs and use input=None; no caller is enabled here.
    """

    status: str
    reason: str
    save_ack_config: dict | None = None
    applied_config: dict | None = None
    execution_config: dict | None = None
    receipt_status: str = "UNKNOWN"
    head: object = None


def _initial_freeze(value, state, depth=0):
    """Bounded immutable field snapshot; do not traverse arbitrary tool/model objects."""
    if depth > 24 or state["count"] >= 8192:
        raise ValueError("Initial structure exceeds snapshot bounds")
    state["count"] += 1
    if value is state["graph"]:
        raise ValueError("Unexpected graph reference in initial structure")
    if type(value) in (str, int, float, bool, bytes, type(None)):
        return (type(value), value)
    identity = id(value)
    if identity in state["seen"]:
        return ("ref", identity)
    state["seen"].add(identity)
    state["keep"].append(value)
    if type(value) is dict:
        return (dict, identity, tuple(
            (_initial_freeze(k, state, depth + 1), _initial_freeze(v, state, depth + 1))
            for k, v in value.items()
        ))
    if type(value) in (list, tuple):
        return (type(value), identity, tuple(_initial_freeze(v, state, depth + 1) for v in value))
    if type(value) in (set, frozenset):
        return (type(value), identity, frozenset(_initial_freeze(v, state, depth + 1) for v in value))
    if type(value) is types.MethodType:
        return (types.MethodType, identity, id(value.__self__),
                _initial_freeze(value.__func__, state, depth + 1))
    if type(value) is types.FunctionType:
        return (types.FunctionType, identity, value.__code__, id(value.__globals__),
                _initial_freeze(value.__defaults__, state, depth + 1),
                _initial_freeze(value.__kwdefaults__, state, depth + 1),
                _initial_freeze(value.__dict__, state, depth + 1),
                tuple(_initial_freeze(cell.cell_contents, state, depth + 1)
                      for cell in value.__closure__ or ()))
    # These objects are not executed by the START update. Their identities are
    # retained to prevent id reuse; execution-relevant fields are added below.
    return (type(value), identity)


def _initial_graph_snapshot(agent):
    """Authenticate installed native START semantics, then snapshot mutable fields."""
    from langchain.agents import factory as lc_factory
    from langgraph._internal._runnable import RunnableCallable
    from langgraph._internal._typing import MISSING
    from langgraph.channels.binop import BinaryOperatorAggregate
    from langgraph.channels.ephemeral_value import EphemeralValue
    from langgraph.channels.last_value import LastValue
    from langgraph.channels.topic import Topic
    from langgraph.graph import state as native_state
    from langgraph.graph.message import add_messages
    from langgraph.pregel import _read, _write
    from langgraph._internal._constants import TASKS
    from langgraph.constants import START
    from typing_extensions import is_typeddict

    if (type(agent) is not CompiledStateGraph or type(agent.checkpointer) is not AsyncSqliteSaver
            or agent.config or agent.store is not None or agent.cache is not None
            or agent.builder.managed or agent.builder.branches.get(START)
            or any(START in starts for starts, _end in agent.builder.waiting_edges)
            or {end for start, end in agent.builder.edges if start == START} != {"model"}
            or any(name in vars(agent) for name in (
                "copy", "aupdate_state", "abulk_update_state", "_aprepare_state_snapshot",
                "_apply_checkpointer_allowlist", "_migrate_checkpoint", "aget_subgraphs"))):
        raise ValueError("Unsupported initial graph")
    start = agent.nodes[START]
    entry = agent.nodes["model"]
    if (type(start) is not _read.PregelNode or type(entry) is not _read.PregelNode
            or start.bound is not _read.DEFAULT_BOUND or start.mapper is not None
            or start.channels != START or start.triggers != [START]
            or start.subgraphs or entry.subgraphs or entry.mapper is not None
            or type(entry.bound) is not RunnableCallable):
        raise ValueError("Unsupported initial entry")
    model_codes = {c for c in lc_factory.create_agent.__code__.co_consts
                   if type(c) is types.CodeType and c.co_name in ("model_node", "amodel_node")}
    if (len(model_codes) != 2 or type(entry.bound.func) is not types.FunctionType
            or type(entry.bound.afunc) is not types.FunctionType
            or entry.bound.func.__code__ not in model_codes
            or entry.bound.afunc.__code__ not in model_codes
            or entry.bound.func.__globals__ is not vars(lc_factory)
            or entry.bound.afunc.__globals__ is not vars(lc_factory)):
        raise ValueError("Non-native model entry")
    semantic = []
    original_semantic = []
    for writers, collected in ((start.writers, original_semantic), (start.flat_writers, semantic)):
        for writer in writers:
            if (type(writer) is not _write.ChannelWrite
                    or writer.func.__self__ is not writer or writer.afunc.__self__ is not writer
                    or writer.func.__func__ is not _write.ChannelWrite._write
                    or writer.afunc.__func__ is not _write.ChannelWrite._awrite
                    or writer.kwargs or writer.trace):
                raise ValueError("Non-native START writer")
            collected.extend(writer.writes)
    if len(semantic) != 3 or original_semantic != semantic:
        raise ValueError("Unsupported START write semantics")
    update, control, branch = semantic
    update_codes = {c for c in CompiledStateGraph.attach_node.__code__.co_consts
                    if type(c) is types.CodeType and c.co_name == "_get_updates"}
    if (type(update) is not _write.ChannelWriteTupleEntry
            or type(update.mapper) is not types.FunctionType
            or update.mapper.__code__ not in update_codes
            or update.mapper.__globals__ is not vars(native_state)
            or update.value is not _write.PASSTHROUGH or update.static is not None
            or type(control) is not _write.ChannelWriteTupleEntry
            or control.mapper is not native_state._control_branch
            or control.value is not _write.PASSTHROUGH or control.static is not None
            or type(branch) is not _write.ChannelWriteEntry
            or branch.channel != native_state._CHANNEL_BRANCH_TO.format("model")
            or branch.value is not None or branch.mapper is not None or branch.skip_none):
        raise ValueError("Unsupported START mapper")
    closure = dict(zip(update.mapper.__code__.co_freevars,
                       (cell.cell_contents for cell in update.mapper.__closure__ or ())))
    if set(closure) != {"_get_updates", "output_keys"} or closure["_get_updates"] is not update.mapper or closure["output_keys"] != ["messages"]:
        raise ValueError("Unsupported START input schema")
    state = {"graph": agent, "count": 0, "seen": set(), "keep": []}
    parts = []
    # Snapshot actual schema definitions, not only schema class identities.
    schemas = agent.builder.schemas
    if set(schemas[agent.builder.input_schema]) != {"messages"}:
        raise ValueError("Unsupported initial input fields")
    for schema in schemas:
        if not is_typeddict(schema):
            raise ValueError("Unsupported initial schema type")
        parts.append(("schema", _initial_freeze(schema, state),
                      _initial_freeze(schema.__annotations__, state),
                      _initial_freeze(schema.__required_keys__, state),
                      _initial_freeze(schema.__optional_keys__, state)))
    parts.append(("schemas", _initial_freeze(schemas, state)))
    for name, channel in agent.channels.items():
        if type(channel) not in (BinaryOperatorAggregate, EphemeralValue, LastValue, Topic):
            raise ValueError("Unsupported initial channel")
        if name == "messages":
            if type(channel) is not BinaryOperatorAggregate or channel.operator is not add_messages or channel.value != []:
                raise ValueError("Unsupported messages reducer")
        elif type(channel) is BinaryOperatorAggregate:
            raise ValueError("Unexpected initial reducer")
        elif type(channel) is Topic:
            if name != TASKS or channel.accumulate or channel.values:
                raise ValueError("Unsupported task channel")
        elif channel.value is not MISSING:
            raise ValueError("Nonempty initial channel")
        fields = []
        for cls in type(channel).__mro__:
            for slot in getattr(cls, "__slots__", ()):
                if slot not in ("__weakref__", "__dict__") and hasattr(channel, slot):
                    fields.append((slot, _initial_freeze(getattr(channel, slot), state)))
        parts.append(("channel", name, type(channel), id(channel), tuple(fields),
                      tuple(getattr(type(channel), method) for method in (
                          "copy", "from_checkpoint", "update", "checkpoint", "get"))))
    for node in (start, entry):
        parts.append(("node", id(node), _initial_freeze(vars(node), state)))
        parts.append(("bound", _initial_freeze(vars(node.bound), state)))
    for writers in (start.writers, start.flat_writers):
        for writer in writers:
            parts.append(("writer", id(writer), _initial_freeze(vars(writer), state),
                          tuple((type(item), tuple(_initial_freeze(v, state) for v in item))
                                for item in writer.writes)))
    for name in ("nodes", "channels", "input_channels", "output_channels", "stream_channels",
                 "stream_mode", "stream_eager", "interrupt_before_nodes", "interrupt_after_nodes",
                 "trigger_to_nodes", "context_schema", "schema_to_mapper", "_state_mapper",
                 "_output_mapper", "_serde_allowlist", "stream_transformers", "name"):
        parts.append((name, _initial_freeze(getattr(agent, name), state)))
    for schema in (agent.builder.state_schema, agent.builder.input_schema, agent.builder.output_schema):
        parts.append(("builder-schema", id(schema)))
    saver = agent.checkpointer
    parts.append(("saver", id(saver), saver.is_setup, id(saver.conn), id(saver.lock), id(saver.serde),
                  type(saver).get_next_version, type(saver).setup, type(saver).aget_tuple,
                  type(saver).aput, type(saver).aput_writes,
                  type(saver.conn).execute, type(saver.conn).commit, type(saver.conn).rollback,
                  saver.conn.row_factory,
                  _initial_freeze(vars(saver.serde), state),
                  type(saver.serde).dumps_typed, type(saver.serde).loads_typed,
                  _write.ChannelWrite.do_write,
                  CompiledStateGraph.aupdate_state, CompiledStateGraph.abulk_update_state,
                  CompiledStateGraph._aprepare_state_snapshot, CompiledStateGraph.copy))
    return tuple(parts), tuple(state["keep"])


def _mint_initial_publication(agent, create_agent, middleware):
    """Called only by the fixed host factory; mint failure changes no agent semantics."""
    from langchain.agents import factory as lc_factory
    from langchain.agents.middleware.types import AgentMiddleware
    from src.agent.middleware.token_counter_middleware import TokenCounterMiddleware, token_counter_middleware

    if (create_agent is not lc_factory.create_agent or middleware is not token_counter_middleware
            or type(middleware) is not TokenCounterMiddleware
            or any(getattr(type(middleware), hook) is not getattr(AgentMiddleware, hook)
                   for hook in ("before_agent", "abefore_agent", "before_model", "abefore_model"))):
        return False
    try:
        snapshot, keepalive = _initial_graph_snapshot(agent)
        setattr(agent, _INITIAL_PROVENANCE_ATTR, _InitialGraphProvenance(
            weakref.ref(agent), agent.checkpointer, snapshot, keepalive,
        ))
        return True
    except Exception:
        return False


def _validate_initial_provenance(agent):
    record = getattr(agent, _INITIAL_PROVENANCE_ATTR, None)
    if (type(record) is not _InitialGraphProvenance or record.graph_ref() is not agent
            or record.saver is not agent.checkpointer):
        return False
    try:
        snapshot, _keepalive = _initial_graph_snapshot(agent)
        return snapshot == record.snapshot
    except Exception:
        return False


def _initial_host_config(config, scope):
    """Accept host scope, never client-shaped graph IDs as authentication."""
    from app.api.compaction_archive import get_compaction_scope
    from app.api.context_events import ContextEventScope
    from src.agent.request_context import REQUEST_GRAPH_BINDING_KEY, request_graph_binding_from_config

    if (type(scope) is not ContextEventScope or get_compaction_scope() != scope
            or type(config) is not dict or set(config) - {"configurable", "recursion_limit"}
            or type(config.get("configurable")) is not dict):
        return None
    conf = config["configurable"]
    if (set(conf) - {"thread_id", "checkpoint_ns", REQUEST_GRAPH_BINDING_KEY}
            or conf.get("checkpoint_ns", "") != ""
            or ("recursion_limit" in config and (type(config["recursion_limit"]) is not int or config["recursion_limit"] <= 0))):
        return None
    binding = request_graph_binding_from_config(config)
    if (binding is None or binding.business_session_id != scope.session_id
            or binding.request_id != scope.request_id
            or binding.graph_thread_id != "hwrag-request:" + scope.request_id):
        return None
    return copy.deepcopy(config)


async def _join_initial_task(task):
    """Never detach a started transaction, initialization or owned teardown."""
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


async def _initial_sql_commit(adapter, checkpoint, kind, payload, encoded_meta):
    """Caller owns native lock. A failed BEGIN never authorizes rollback."""
    saver = adapter.saver
    owned = False
    fence_nonce = None
    try:
        if saver.is_setup is not True or saver.conn.in_transaction:
            raise StaleCompaction("Unready or foreign transaction")
        async with saver.conn.execute("BEGIN IMMEDIATE"):
            owned = True
        fence_nonce = _set_active_graph_fence_nonce(saver)
        thread = adapter.thread
        async with saver.conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
            "parent_checkpoint_id, type, checkpoint, metadata) "
            "SELECT ?, '', ?, NULL, ?, ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM checkpoints WHERE thread_id=? AND checkpoint_ns='') "
            "AND NOT EXISTS (SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns='')",
            (thread, checkpoint["id"], kind, payload, encoded_meta, thread, thread),
        ) as cursor:
            if cursor.rowcount != 1:
                raise StaleCompaction("Initial root already exists")
        await saver.conn.commit()
        owned = False
        # This value is produced only after the awaited owned commit, not from
        # an event, a selected latest row, a UUID guess or a host trusted flag.
        ack = {"configurable": {"thread_id": thread, "checkpoint_ns": "",
                                "checkpoint_id": checkpoint["id"]}}
        adapter.state["ack"] = ack
        return ack
    except BaseException:
        if owned:
            await saver.conn.rollback()
        raise
    finally:
        _clear_active_graph_fence_nonce(saver, fence_nonce)


class _InitialPublicationSaver(BaseCheckpointSaver):
    """One START update, no setup or native-getter lock re-entry.

    with_allowlist may shallow-copy a saver; the shared state keeps one-shot
    and acknowledgement identity across those native serializer-only copies.
    The live graph always retains its original exact native saver.
    """

    def __init__(self, saver, thread, candidate_digest):
        super().__init__(serde=saver.serde)
        self.saver = saver
        self.thread = thread
        self.candidate_digest = candidate_digest
        self.state = {"read": False, "put": False, "attempted": False, "ack": None}

    def get_next_version(self, current, channel):
        return self.saver.get_next_version(current, channel)

    async def aget_tuple(self, config):
        conf = config["configurable"]
        if (self.state["read"] or conf.get("thread_id") != self.thread
                or conf.get("checkpoint_ns", "") != "" or conf.get("checkpoint_id")):
            raise StaleCompaction("Unsupported initial read")
        self.state["read"] = True
        async with self.saver.lock:
            if self.saver.is_setup is not True or self.saver.conn.in_transaction:
                raise StaleCompaction("Unready or foreign transaction")
            async with self.saver.conn.execute(
                "SELECT EXISTS(SELECT 1 FROM checkpoints WHERE thread_id=? AND checkpoint_ns='') "
                "OR EXISTS(SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns='')",
                (self.thread, self.thread),
            ) as cursor:
                row = await cursor.fetchone()
            if row != (0,):
                raise StaleCompaction("Initial root is not empty")
        return None

    async def aput_writes(self, config, writes, task_id, task_path=""):
        raise StaleCompaction("Initial intermediate or PUSH writes are unsupported")

    async def aput(self, config, checkpoint, metadata, new_versions):
        from app.api.compaction_checkpoint import _host_message_digest
        from langgraph.constants import START
        from langgraph.graph.state import _CHANNEL_BRANCH_TO

        if self.state["put"] or not self.state["read"]:
            raise StaleCompaction("Initial adapter is one-shot")
        self.state["put"] = True
        conf = config["configurable"]
        values = checkpoint["channel_values"]
        branch = _CHANNEL_BRANCH_TO.format("model")
        checkpoint_fields = {"v", "ts", "id", "channel_values", "channel_versions", "versions_seen", "updated_channels"}
        if (conf.get("thread_id") != self.thread or conf.get("checkpoint_ns", "") != ""
                or conf.get("checkpoint_id") or type(checkpoint.get("id")) is not str
                or not checkpoint["id"] or set(checkpoint) != checkpoint_fields
                or checkpoint["v"] != 4 or checkpoint["channel_versions"] != new_versions
                or checkpoint["versions_seen"] != {START: {}}
                or checkpoint["updated_channels"] is not None
                or set(values) != {"messages", branch}
                or values[branch] is not None
                or _host_message_digest(values["messages"]) != self.candidate_digest
                or set(new_versions) != {"messages", branch}
                or metadata.get("source") != "update" or metadata.get("step") != 0
                or metadata.get("parents") != {}):
            raise StaleCompaction("Unsupported initial checkpoint")
        kind, payload = self.serde.dumps_typed(checkpoint)
        encoded_meta = json.dumps(get_checkpoint_metadata(config, metadata), ensure_ascii=False).encode("utf-8")
        async with self.saver.lock:
            if self.saver.is_setup is not True or self.saver.conn.in_transaction:
                raise StaleCompaction("Unready or foreign transaction")
            self.state["attempted"] = True
            return await _join_initial_task(asyncio.create_task(
                _initial_sql_commit(self, checkpoint, kind, payload, encoded_meta),
            ))


async def _read_initial_checkpoint(saver, config):
    """Read only an acknowledged exact row; never call setup or select latest."""
    conf = config["configurable"]
    thread, namespace, checkpoint_id = conf["thread_id"], conf["checkpoint_ns"], conf["checkpoint_id"]
    async with saver.lock:
        if saver.is_setup is not True or saver.conn.in_transaction:
            raise StaleCompaction("Unready or foreign read transaction")
        async with saver.conn.execute(
            "SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
            "FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? "
            "AND NOT EXISTS (SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns=?) "
            "AND NOT EXISTS (SELECT 1 FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id<>?)",
            (thread, namespace, checkpoint_id, thread, namespace, thread, namespace, checkpoint_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or row[0] != checkpoint_id or row[1] is not None:
            raise StaleCompaction("Initial exact tuple unavailable")
        checkpoint = saver.serde.loads_typed((row[2], row[3]))
        metadata = json.loads(row[4])
        if checkpoint["id"] != checkpoint_id or metadata.get("source") != "update" or metadata.get("step") != 0 or metadata.get("parents") != {}:
            raise StaleCompaction("Initial tuple identity mismatch")
        return CheckpointTuple(config, checkpoint, metadata, None, [])


async def publish_initial_request_context(
    agent, config, scope, original, candidate, *, skills_mode="off",
):
    """Private initial-save protocol; called by the pre-send initial-context
    compression route. This static call site does not establish dynamic
    reachability or successful runtime execution.

    REFUSED is not permission to reuse this graph with raw input. Any possible
    write without complete acknowledged readback is OUTCOME_UNKNOWN: stop,
    never append input again or claim rollback. Cancellation joins started
    owned work before propagating. Head CAS is application-DB-local, not a
    graph writer fence. Bypassing the native lock is outside this protocol.
    """
    adapter = None
    try:
        from app.api.compaction_checkpoint import (
            HostPreparedContext, HostPublishedHead, _host_message_digest,
            prepare_published_context, confirm_published_context,
        )
        from langgraph.constants import START
        from src.agent.request_context import request_graph_binding_from_config

        host_config = _initial_host_config(config, scope)
        if skills_mode != "off" or host_config is None or not _validate_initial_provenance(agent):
            return _InitialPublicationResult("REFUSED", "scope_or_provenance")
        saver = agent.checkpointer
        if saver.is_setup is not True:
            return _InitialPublicationResult("REFUSED", "saver_unready")
        binding = request_graph_binding_from_config(host_config)
        digest = _host_message_digest(candidate)
        if digest is None:
            return _InitialPublicationResult("REFUSED", "unsupported_messages")
        prepared = await _run_host_record_transaction(
            prepare_published_context, scope, original, candidate,
            skills_mode=skills_mode, graph_thread_id=binding.graph_thread_id, checkpoint_ns="",
        )
        if type(prepared) is not HostPreparedContext:
            return _InitialPublicationResult("REFUSED", "strict_preparation")
        if (_initial_host_config(config, scope) != host_config
                or not _validate_initial_provenance(agent)
                or _host_message_digest(candidate) != digest):
            return _InitialPublicationResult("REFUSED", "changed_before_save")
        candidate_copy = copy.deepcopy(candidate)
        if _host_message_digest(candidate_copy) != digest:
            return _InitialPublicationResult("REFUSED", "changed_before_save")
        adapter = _InitialPublicationSaver(saver, binding.graph_thread_id, digest)
        publishing = agent.copy({"checkpointer": adapter})
        applied_config = await publishing.aupdate_state(
            host_config, {"messages": candidate_copy}, as_node=START,
        )
        applied = _host_publication_config(applied_config, binding.graph_thread_id, "")
        ack = adapter.state["ack"]
        if applied is None or ack is None or applied["configurable"] != ack["configurable"]:
            return _InitialPublicationResult("OUTCOME_UNKNOWN", "missing_exact_ack")
        saved = await _read_initial_checkpoint(saver, applied)
        # No native saver getter: it would invoke setup even for an exact ID.
        observed = await agent._aprepare_state_snapshot(applied, saved, apply_pending_writes=False)
        if (not _validate_initial_provenance(agent) or saved.pending_writes
                or observed.config["configurable"] != applied["configurable"]
                or observed.next != ("model",) or len(observed.tasks) != 1
                or observed.tasks[0].name != "model" or observed.tasks[0].error is not None
                or observed.tasks[0].interrupts or observed.interrupts
                or set(observed.values) != {"messages"}
                or _host_message_digest(observed.values["messages"]) != digest):
            return _InitialPublicationResult("OUTCOME_UNKNOWN", "exact_readback_mismatch")
        execution_config = copy.deepcopy(host_config)
        execution_config["configurable"].update(ack["configurable"])
        if (request_graph_binding_from_config(execution_config) != binding
                or execution_config["configurable"]["checkpoint_id"] != ack["configurable"]["checkpoint_id"]):
            return _InitialPublicationResult("OUTCOME_UNKNOWN", "execution_binding_mismatch")
        head = None
        try:
            head = await _run_host_record_transaction(
                confirm_published_context, scope, prepared,
                applied_config=applied, observed_config=observed.config,
                observed_messages=list(observed.values["messages"]), skills_mode=skills_mode,
            )
        except Exception:
            logger.warning("Initial graph applied; host receipt outcome unknown")
        confirmed = type(head) is HostPublishedHead
        return _InitialPublicationResult(
            status="APPLIED", reason="exact_ack_and_readback",
            save_ack_config=copy.deepcopy(ack),
            applied_config=copy.deepcopy(applied_config),
            execution_config=execution_config,
            receipt_status="CONFIRMED" if confirmed else "UNKNOWN",
            head=head if confirmed else None,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        attempted = adapter is not None and adapter.state["attempted"]
        return _InitialPublicationResult(
            "OUTCOME_UNKNOWN" if attempted else "REFUSED", "initial_protocol_unavailable",
        )
