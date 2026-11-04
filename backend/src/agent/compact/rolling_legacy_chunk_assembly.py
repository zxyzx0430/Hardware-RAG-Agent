"""Reassemble verified legacy snapshot row chunks without granting trust.

This module is pure host-side byte assembly. A successful result proves that
one complete canonical envelope matches the chunks' shared metadata and hash;
it does not prove that the source or owner binding is still current.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NoReturn

from app.api.legacy_snapshot_store import LegacySnapshotOriginalRowChunk
from app.api.rolling_history import (
    LegacySourceLocatorV1,
    LegacySourcePayloadV1,
    validate_legacy_source_locator_v1,
)
from app.api.rolling_history_codec import validate_legacy_message_snapshot


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SOURCE_RELATIONS = frozenset({"EXACT_CURRENT", "APPEND_ONLY_PREFIX"})


class LegacySnapshotChunkAssemblyError(ValueError):
    """A legacy snapshot row's chunks cannot be safely reassembled."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LegacySnapshotOriginalRowAssembly:
    """A complete archive envelope, not an authorization or freshness proof.

    ``locator.model_availability`` is retained. In particular, an
    ``archive_only`` row has no model projection in this result. Any future
    consumer must revalidate its owner, source, and epoch before using or
    saving the envelope; chunks can come from separate read transactions.
    """

    source_relation: str
    source_message_revision: int
    ordering_version: str
    sequence: int
    locator: LegacySourceLocatorV1
    total_byte_length: int
    full_envelope_sha256: str
    canonical_envelope_json: str

    @property
    def archive_only(self) -> bool:
        """Whether the retained locator forbids a model text projection."""
        return self.locator.model_availability == "archive_only"


def _reject(code: str) -> NoReturn:
    raise LegacySnapshotChunkAssemblyError(code)


def assemble_legacy_snapshot_original_row_chunks(
    chunks: Iterable[object],
) -> LegacySnapshotOriginalRowAssembly:
    """Assemble one exact-type row's contiguous ASCII envelope chunks.

    The input may be an iterable collected across independent read
    transactions. Every chunk must bind to the same locator, source relation,
    revision, ordering version, sequence, total length, and full-envelope hash.
    No partial bytes are returned or interpreted. The only size bound is the
    host process's available memory; allocation failure is reported closed.
    """
    try:
        iterator = iter(chunks)
    except Exception:
        _reject("invalid_chunks")

    header: tuple[str, LegacySourceLocatorV1, int, str, int, int, str] | None = None
    cursor = 0
    assembled = bytearray()

    while True:
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        except Exception:
            _reject("invalid_chunks")

        if type(chunk) is not LegacySnapshotOriginalRowChunk:
            _reject("invalid_chunk_type")

        locator = chunk.locator
        if type(locator) is not LegacySourceLocatorV1:
            _reject("invalid_locator")
        if (type(chunk.source_relation) is not str
                or chunk.source_relation not in _SOURCE_RELATIONS
                or type(chunk.source_message_revision) is not int
                or type(chunk.ordering_version) is not str
                or type(chunk.sequence) is not int
                or type(chunk.total_byte_length) is not int
                or chunk.total_byte_length <= 0
                or type(chunk.full_envelope_sha256) is not str
                or _SHA256.fullmatch(chunk.full_envelope_sha256) is None
                or type(chunk.start_offset) is not int
                or type(chunk.end_offset) is not int
                or type(chunk.next_offset) is not int
                or type(chunk.complete) is not bool
                or type(chunk.chunk_bytes) is not bytes
                or not chunk.chunk_bytes):
            _reject("invalid_chunk_fields")

        if (locator.capture_status != "captured"
                or chunk.source_message_revision != locator.source_message_revision
                or chunk.ordering_version != locator.ordering_version
                or chunk.sequence != locator.sequence):
            _reject("invalid_source_binding")

        current_header = (
            chunk.source_relation,
            locator,
            chunk.source_message_revision,
            chunk.ordering_version,
            chunk.sequence,
            chunk.total_byte_length,
            chunk.full_envelope_sha256,
        )
        if header is None:
            header = current_header
        elif current_header != header:
            _reject("mixed_source_chunks")

        if (chunk.start_offset != cursor
                or chunk.end_offset <= chunk.start_offset
                or chunk.end_offset > chunk.total_byte_length
                or chunk.end_offset != chunk.start_offset + len(chunk.chunk_bytes)
                or chunk.next_offset != chunk.end_offset
                or chunk.complete != (chunk.end_offset == chunk.total_byte_length)):
            _reject("invalid_chunk_range")

        try:
            assembled.extend(chunk.chunk_bytes)
        except (MemoryError, OverflowError):
            _reject("assembly_resource_limit")
        cursor = chunk.next_offset

    if header is None:
        _reject("empty_chunks")

    source_relation, locator, source_revision, ordering_version, sequence, total, expected_sha256 = header
    if cursor != total:
        _reject("incomplete_chunks")

    try:
        actual_sha256 = hashlib.sha256(assembled).hexdigest()
    except (MemoryError, OverflowError):
        _reject("assembly_resource_limit")
    if actual_sha256 != expected_sha256:
        _reject("envelope_hash_mismatch")

    try:
        canonical_envelope_json = assembled.decode("ascii", errors="strict")
    except (MemoryError, OverflowError):
        _reject("assembly_resource_limit")
    except UnicodeError:
        _reject("non_ascii_envelope")
    finally:
        assembled.clear()

    if len(canonical_envelope_json) != total:
        _reject("invalid_envelope_length")

    try:
        validation = validate_legacy_message_snapshot(canonical_envelope_json)
        if (validation.canonical_envelope_json != canonical_envelope_json
                or validation.model_status != locator.model_availability):
            _reject("invalid_envelope")

        if locator.model_availability == "archive_only":
            if validation.model_view is not None:
                _reject("invalid_archive_only_row")
            model_role = None
            model_content = None
        elif locator.model_availability == "text_projection_available":
            if validation.model_view is None:
                _reject("invalid_text_projection")
            model_role = validation.model_view.role
            model_content = validation.model_view.content
        else:
            _reject("invalid_model_availability")

        payload = LegacySourcePayloadV1(
            capture_reason=None,
            canonical_envelope_json=canonical_envelope_json,
            model_role=model_role,
            model_content=model_content,
        )
        validate_legacy_source_locator_v1(locator, payload)
    except LegacySnapshotChunkAssemblyError:
        raise
    except (MemoryError, OverflowError):
        _reject("assembly_resource_limit")
    except Exception:
        _reject("invalid_envelope")

    return LegacySnapshotOriginalRowAssembly(
        source_relation=source_relation,
        source_message_revision=source_revision,
        ordering_version=ordering_version,
        sequence=sequence,
        locator=locator,
        total_byte_length=total,
        full_envelope_sha256=actual_sha256,
        canonical_envelope_json=canonical_envelope_json,
    )
