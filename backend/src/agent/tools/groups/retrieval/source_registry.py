"""Request-local, concurrency-safe IDs for retrieved evidence sources.

The registry stores only opaque evidence fingerprints and source IDs. Callers
may serialize it with :meth:`to_snapshot` and restore the same paused request
with :meth:`from_snapshot`; a new request should construct a fresh registry.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_SNAPSHOT_VERSION = 1
_SOURCE_ID_RE = re.compile(r"src([1-9][0-9]*)\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class InvalidSourceRegistrySnapshot(ValueError):
    """Raised when saved request-local source state is incompatible or corrupt."""


@dataclass(frozen=True)
class SourceRegistration:
    """Source ID allocated for one candidate and whether it is newly registered."""

    source_id: str
    is_new: bool


class RagSourceRegistry:
    """Allocate stable ``srcN`` IDs and deduplicate identical RAG evidence.

    ``register_many`` entries contain ``kb_id``, ``doc_id``, optional
    ``big_chunk_id``, ``small_chunk_id`` or ``chunk_index``, and surfaced parent
    and small chunk text under ``content`` and ``small_chunk_text``.
    """

    def __init__(self) -> None:
        self._next_id = 1
        self._identity_to_id: dict[str, str] = {}
        self._lock = threading.RLock()

    @property
    def next_id(self) -> int:
        """The next source number that will be allocated."""
        with self._lock:
            return self._next_id

    def register_many(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        context: Any | None = None,
    ) -> list[SourceRegistration]:
        """Atomically reuse or allocate IDs in input order.

        When ``context`` is provided, its ``source_counter`` is validated as a
        mirror before allocation and updated while the registry lock is held.
        Evidence lacking a complete identity is intentionally never deduped.
        """
        with self._lock:
            if context is not None:
                self._validate_counter_mirror(context)

            registrations: list[SourceRegistration] = []
            for entry in entries:
                identity = _evidence_identity(entry)
                existing_id = self._identity_to_id.get(identity) if identity else None
                if existing_id is not None:
                    registrations.append(SourceRegistration(existing_id, False))
                    continue

                source_id = f"src{self._next_id}"
                self._next_id += 1
                if identity:
                    self._identity_to_id[identity] = source_id
                registrations.append(SourceRegistration(source_id, True))
            if context is not None:
                setattr(context, "source_counter", self._next_id - 1)
            return registrations

    def allocate_ids(self, count: int, *, context: Any) -> list[str]:
        """Allocate distinct IDs atomically for non-deduplicated source results."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("count must be a non-negative integer")
        with self._lock:
            self._validate_counter_mirror(context)
            source_ids = [f"src{self._next_id + offset}" for offset in range(count)]
            self._next_id += count
            setattr(context, "source_counter", self._next_id - 1)
            return source_ids

    def _validate_counter_mirror(self, context: Any) -> None:
        """Reject stale or independently mutated legacy counter state."""
        value = getattr(context, "source_counter", 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidSourceRegistrySnapshot("request source counter is invalid")
        if value != self._next_id - 1:
            raise InvalidSourceRegistrySnapshot(
                "request source counter does not match registry state"
            )

    def to_snapshot(self) -> dict[str, Any]:
        """Return JSON-safe state without source text or raw document IDs."""
        with self._lock:
            entries = [
                {"identity_hash": identity, "source_id": source_id}
                for identity, source_id in self._identity_to_id.items()
            ]
            entries.sort(key=lambda item: int(item["source_id"][3:]))
            return {
                "version": _SNAPSHOT_VERSION,
                "next_id": self._next_id,
                "entries": entries,
            }

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> RagSourceRegistry:
        """Restore a snapshot, rejecting malformed state rather than guessing."""
        if not isinstance(snapshot, Mapping):
            raise InvalidSourceRegistrySnapshot("source registry snapshot must be a mapping")
        version = snapshot.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != _SNAPSHOT_VERSION
        ):
            raise InvalidSourceRegistrySnapshot("unsupported source registry snapshot version")

        next_id = snapshot.get("next_id")
        entries = snapshot.get("entries")
        if isinstance(next_id, bool) or not isinstance(next_id, int) or next_id < 1:
            raise InvalidSourceRegistrySnapshot("source registry next_id is invalid")
        if not isinstance(entries, list):
            raise InvalidSourceRegistrySnapshot("source registry entries are invalid")

        registry = cls()
        seen_source_ids: set[str] = set()
        for item in entries:
            if not isinstance(item, Mapping):
                raise InvalidSourceRegistrySnapshot("source registry entry is invalid")
            identity = item.get("identity_hash")
            source_id = item.get("source_id")
            if not isinstance(identity, str) or not _SHA256_RE.fullmatch(identity):
                raise InvalidSourceRegistrySnapshot("source registry identity hash is invalid")
            match = _SOURCE_ID_RE.fullmatch(source_id) if isinstance(source_id, str) else None
            if (
                match is None
                or identity in registry._identity_to_id
                or source_id in seen_source_ids
            ):
                raise InvalidSourceRegistrySnapshot("source registry IDs are invalid or duplicated")
            registry._identity_to_id[identity] = source_id
            seen_source_ids.add(source_id)

        highest_id = max((int(source_id[3:]) for source_id in seen_source_ids), default=0)
        if next_id <= highest_id:
            raise InvalidSourceRegistrySnapshot("source registry next_id would reuse a saved ID")
        registry._next_id = next_id
        return registry

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"RagSourceRegistry(version={_SNAPSHOT_VERSION}, "
                f"next_id={self._next_id}, entries={len(self._identity_to_id)})"
            )


_CONTEXT_REGISTRY_INIT_LOCK = threading.RLock()


def get_context_source_registry(context: Any) -> RagSourceRegistry:
    """Get the request-local registry, restoring a supported snapshot if needed.

    A missing registry is valid only for a new request whose mirror is zero.
    Resumed requests must carry both a compatible registry snapshot and its
    matching counter mirror; the helper never reconstructs state from the
    counter alone.
    """
    with _CONTEXT_REGISTRY_INIT_LOCK:
        value = getattr(context, "source_counter", 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidSourceRegistrySnapshot("request source counter is invalid")

        registry = getattr(context, "rag_source_registry", None)
        if registry is None:
            if value != 0:
                raise InvalidSourceRegistrySnapshot(
                    "request source registry is missing for a non-empty counter"
                )
            registry = RagSourceRegistry()
        elif isinstance(registry, Mapping):
            registry = RagSourceRegistry.from_snapshot(registry)
        elif not isinstance(registry, RagSourceRegistry):
            raise InvalidSourceRegistrySnapshot("request source registry has an unsupported type")

        if value != registry.next_id - 1:
            raise InvalidSourceRegistrySnapshot(
                "request source counter does not match registry state"
            )
        setattr(context, "rag_source_registry", registry)
        return registry


def _evidence_identity(entry: Mapping[str, Any]) -> str | None:
    """Fingerprint complete canonical evidence identity plus surfaced content."""
    kb_id = _identity_part(entry.get("kb_id"))
    doc_id = _identity_part(entry.get("doc_id"))
    big_chunk_id = _identity_part(entry.get("big_chunk_id"))
    small_chunk_id = _identity_part(entry.get("small_chunk_id"))
    chunk_index = entry.get("chunk_index")
    if chunk_index is not None and not isinstance(chunk_index, bool):
        chunk_index = str(chunk_index).strip()
    else:
        chunk_index = ""

    # KB + document + a stable small-chunk identity are required. A parent ID
    # alone is not sufficient because different small chunks can be distinct
    # useful evidence and must retain separate citations.
    if not kb_id or not doc_id or not (small_chunk_id or chunk_index):
        return None

    parent_text = entry.get("content")
    small_text = entry.get("small_chunk_text")
    if not isinstance(parent_text, str) or not isinstance(small_text, str):
        return None
    if not parent_text and not small_text:
        return None

    content_hash = hashlib.sha256(
        json.dumps(
            {"parent": parent_text, "small": small_text},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    identity_payload = {
        "kb_id": kb_id,
        "doc_id": doc_id,
        "big_chunk_id": big_chunk_id,
        "small_chunk_id": small_chunk_id,
        "chunk_index": chunk_index,
        "content_hash": content_hash,
    }
    return hashlib.sha256(
        json.dumps(
            identity_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _identity_part(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
