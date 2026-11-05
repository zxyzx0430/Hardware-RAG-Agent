"""Server-owned one-shot receipts for HITL tools-node pauses.

The frozen writer registry intentionally has no paused state.  A confirmation
card is instead bound to an app-level receipt keyed by host identity; resume
must present the exact receipt once before a writer lease is acquired.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from src.agent.compact.writer_lifecycle import WriterLifecycleError


TOOLS_NODE_NAME = "tools"
DEFAULT_TTL_SECONDS = 30 * 60
_MAX_RECEIPTS = 256

_LOCK = threading.Lock()
_RECEIPTS: dict[tuple[str, str, str], tuple[PauseReceipt, float]] = {}


@dataclass(frozen=True, slots=True)
class PauseReceipt:
    checkpoint_id: str
    next_nodes: tuple[str, ...]
    interrupt_fingerprints: tuple[str, ...]
    pending_call_fingerprints: tuple[tuple[str, str], ...]


def _pending_call_fingerprints(pending: Any) -> tuple[tuple[str, str], ...]:
    if type(pending) is not list or not 1 <= len(pending) <= 64:
        raise WriterLifecycleError("A pause requires one to sixty-four pending calls")
    result: list[tuple[str, str]] = []
    remaining = 64 * 1024
    for call in pending:
        if type(call) is not dict:
            raise WriterLifecycleError("Pending call is not inspectable")
        call_id = call.get("call_id")
        name = call.get("name")
        args = call.get("args")
        if (
            type(call_id) is not str or not 1 <= len(call_id) <= 256
            or type(name) is not str or not name or type(args) is not dict
        ):
            raise WriterLifecycleError("Pending call identity is incomplete")
        encoded = json.dumps(
            [name, args], ensure_ascii=False, sort_keys=True, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        remaining -= len(encoded)
        if remaining < 0:
            raise WriterLifecycleError("Pending call budget exceeded")
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
                    [
                        interrupt_id, value, namespace,
                        getattr(interrupt, "when", None),
                        getattr(interrupt, "resumable", None),
                    ],
                    ensure_ascii=False, sort_keys=True, allow_nan=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError) as exc:
                raise WriterLifecycleError(
                    "Checkpoint interrupt is not stably fingerprintable"
                ) from exc
            task_interrupts.append([
                interrupt_id, value, namespace,
                getattr(interrupt, "when", None),
                getattr(interrupt, "resumable", None),
            ])
        try:
            encoded = json.dumps(
                [name, task_interrupts], ensure_ascii=False, sort_keys=True,
                allow_nan=False, separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WriterLifecycleError(
                "Checkpoint task state is not stably fingerprintable"
            ) from exc
        result.append(hashlib.sha256(encoded).hexdigest())
    return tuple(result)


def make_pause_receipt(
    snapshot: Any, pending: Any, expected_thread_id: str,
    *, tools_node_name: str = TOOLS_NODE_NAME,
) -> PauseReceipt:
    state_config = getattr(snapshot, "config", None)
    configurable = state_config.get("configurable") if type(state_config) is dict else None
    checkpoint_id = configurable.get("checkpoint_id") if type(configurable) is dict else None
    next_value = getattr(snapshot, "next", None)
    if (
        type(configurable) is not dict
        or configurable.get("thread_id") != expected_thread_id
        or configurable.get("checkpoint_ns", "") != ""
        or type(checkpoint_id) is not str or not 1 <= len(checkpoint_id) <= 128
        or any(ord(char) < 33 or ord(char) == 127 for char in checkpoint_id)
        or type(next_value) not in (tuple, list)
        or any(type(node) is not str for node in next_value)
        or tools_node_name not in next_value
    ):
        raise WriterLifecycleError("Paused checkpoint is not an exact tools-node checkpoint")
    return PauseReceipt(
        checkpoint_id=checkpoint_id,
        next_nodes=tuple(next_value),
        interrupt_fingerprints=_interrupt_fingerprints(snapshot),
        pending_call_fingerprints=_pending_call_fingerprints(pending),
    )


def pause_key(identity: Any, session_epoch: str | None) -> tuple[str, str, str]:
    if (
        identity is None
        or type(identity.business_session_id) is not str
        or type(identity.request_id) is not str
        or type(session_epoch) is not str
    ):
        raise WriterLifecycleError("Pause identity is unavailable")
    return identity.business_session_id, identity.request_id, session_epoch


def record_pause(
    key: tuple[str, str, str], receipt: PauseReceipt,
    *, ttl: float = DEFAULT_TTL_SECONDS,
) -> None:
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        if len(_RECEIPTS) >= _MAX_RECEIPTS:
            raise WriterLifecycleError("Pause receipt capacity reached")
        _RECEIPTS[key] = (receipt, now + ttl)


def require_and_consume(
    key: tuple[str, str, str], receipt: PauseReceipt,
) -> None:
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        entry = _RECEIPTS.get(key)
        if entry is None or entry[0] != receipt:
            raise WriterLifecycleError("HITL confirmation is stale or was already consumed")
        del _RECEIPTS[key]


def require_and_consume_if_recorded(
    key: tuple[str, str, str], receipt: PauseReceipt,
) -> bool:
    """Consume a recorded receipt; legacy snapshots without one remain valid."""
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        entry = _RECEIPTS.get(key)
        if entry is None:
            return False
        if entry[0] != receipt:
            raise WriterLifecycleError(
                "HITL confirmation is stale or was already consumed"
            )
        del _RECEIPTS[key]
        return True


def clear_pause(key: tuple[str, str, str]) -> None:
    with _LOCK:
        _RECEIPTS.pop(key, None)


def _prune(now: float) -> None:
    for key in [key for key, (_, expires) in _RECEIPTS.items() if expires <= now]:
        _RECEIPTS.pop(key, None)
