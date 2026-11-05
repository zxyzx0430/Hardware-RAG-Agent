"""Owner-bound capture and integrity-seal operations for legacy snapshots.

This module creates and reads generation manifests, captures bounded batches,
seals verified local archives, and can append a sealed owner-bound legacy batch
to rolling history. It does not acknowledge downstream model or graph
publication.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
import re

from app.api.rolling_history import (
    LegacySourceLocatorV1,
    LegacySourcePayloadV1,
    RollingHistoryReference,
    _append_idempotency_id,
    _batch_digest,
    _canonical,
    _encode_event,
    _event_chain_digest,
    _event_id_key,
    _event_key,
    _genesis_chain,
    _identity,
    _owner_history_head,
    _owner_history_insert_setting,
    _owner_history_load_event_at_sequence,
    _owner_history_read_setting,
    _owner_history_set_setting,
    _require_bound_scope,
    _scope_digest,
    _scope_prefix,
    _sha256,
    rolling_history_session_prefix,
    validate_legacy_source_locator_v1,
)
from app.api.context_events import ContextEventScope
from app.api.rolling_history_codec import (
    _CODEC_VERSION,
    _canonical_json,
    LegacyMessageSnapshotValidation,
    decode_legacy_message_snapshot,
    encode_legacy_message_snapshot,
    legacy_message_chain_hash,
    legacy_message_row_hash,
    validate_legacy_message_snapshot,
)
from app.api.rolling_history_owner_scope import (
    MaterializedRows,
    OperationMode,
    RollingHistoryOwnerIdentity,
    RollingHistoryOwnerScopeError,
    RollingHistorySqlHandle,
    ScopeOrigin,
    bootstrap_rolling_history_owner_scope,
    get_rolling_history_owner_identity,
    run_rolling_history_owner_operation,
)


_MIGRATION_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_INCARNATION_ID = _MIGRATION_ID
_ORDERING_VERSION = "created_at_nulls_first_id_asc_v1"
_ZERO_HASH = "0" * 64
_MAX_CAPTURE_BATCH_SIZE = 128
_DEFAULT_CAPTURE_BATCH_SIZE = 64
_SEAL_BATCH_SIZE = 64
_MAX_ORIGINAL_PAGE_SIZE = 64
_MAX_ORIGINAL_PAGE_BYTES = 256 * 1024
_MAX_ORIGINAL_ROW_CHUNK_BYTES = 64 * 1024
_MAX_LEGACY_APPEND_BATCH_SIZE = 64
_DEFAULT_LEGACY_APPEND_BATCH_SIZE = 32
_LEGACY_APPEND_NAMESPACE = "legacy-snapshot-append:v1:"
_LEGACY_APPEND_CHECKPOINT_NAMESPACE = "legacy-snapshot-checkpoint:v2:"
_LEGACY_APPEND_CHECKPOINT_SCHEMA = 3
_LEGACY_APPEND_CHECKPOINT_SCHEMA_V1 = 1
_LEGACY_APPEND_CHECKPOINT_ROOT_SCHEMA = 2
_LEGACY_APPEND_EXACT_RECEIPT_SCHEMA = 1
_COVERAGE_STATUSES = frozenset({"pending", "complete", "incomplete"})
_PUBLICATION_STATUSES = frozenset({"building", "complete", "incomplete"})
_LEGACY_MESSAGE_COLUMNS = (
    "id",
    "session_id",
    "role",
    "content",
    "sources",
    "tool_calls",
    "activity",
    "created_at",
)
_GENERATION_COLUMNS = (
    "migration_id",
    "session_id",
    "incarnation_id",
    "owner_principal_hash",
    "source_message_revision",
    "ordering_version",
    "capture_cursor",
    "total_count",
    "coverage_status",
    "publication_status",
    "capture_chain_hash",
)
_GENERATION_SELECT = (
    "SELECT migration_id, session_id, incarnation_id, owner_principal_hash, "
    "source_message_revision, ordering_version, capture_cursor, total_count, "
    "coverage_status, publication_status, capture_chain_hash "
    "FROM legacy_message_snapshot_generations WHERE migration_id = ?"
)
_SNAPSHOT_ROW_COLUMNS = (
    "migration_id",
    "sequence",
    "legacy_message_id",
    "source_message_revision",
    "canonical_envelope",
    "row_hash",
    "chain_hash",
    "capture_status",
    "capture_reason",
)


class LegacySnapshotGenerationConflict(RollingHistoryOwnerScopeError):
    """Sanitized failure for a missing, stale, or differently bound generation."""

    def __init__(self) -> None:
        super().__init__("Legacy message snapshot generation is unavailable")


@dataclass(frozen=True)
class LegacySnapshotGenerationStatus:
    """Public generation state without owner identity or transcript claims.

    Sealed coverage counts describe lossless per-row projections only. They do
    not establish a complete model conversation; archive-only rows are never
    included in ``text_projection_count``.
    """

    migration_id: str
    session_id: str
    source_message_revision: int
    ordering_version: str
    capture_cursor: int
    total_count: int
    capture_chain_hash: str
    coverage_status: str
    publication_status: str
    text_projection_count: int | None = None
    archive_only_count: int | None = None


@dataclass(frozen=True)
class LegacySnapshotOriginalPageRow:
    """One lossless original envelope and its stable source locator."""

    sequence: int
    locator: LegacySourceLocatorV1
    canonical_envelope_json: str


@dataclass(frozen=True)
class LegacySnapshotOriginalPage:
    """Bounded original rows from one freshly validated sealed prefix."""

    source_relation: str
    source_message_revision: int
    ordering_version: str
    start_sequence: int
    next_sequence: int
    total_count: int
    rows: tuple[LegacySnapshotOriginalPageRow, ...]


@dataclass(frozen=True)
class LegacySnapshotOriginalRowChunk:
    """One exact ASCII byte range from a freshly validated stored envelope.

    ``chunk_bytes`` maps one-to-one to the stored canonical envelope's ASCII
    encoding. ``complete`` marks only that this chunk reaches the envelope's
    end; it does not say that earlier chunks were received or reassembled.
    Callers must reassemble contiguous chunks from offset zero and verify the
    full envelope before any use.
    """

    source_relation: str
    source_message_revision: int
    ordering_version: str
    sequence: int
    locator: LegacySourceLocatorV1
    total_byte_length: int
    start_offset: int
    end_offset: int
    next_offset: int
    complete: bool
    full_envelope_sha256: str
    chunk_bytes: bytes


class LegacySnapshotSourceRelation(Enum):
    """Read-transaction result relating one sealed archive to live messages."""

    EXACT_CURRENT = "EXACT_CURRENT"
    APPEND_ONLY_PREFIX = "APPEND_ONLY_PREFIX"
    SUPERSEDED = "SUPERSEDED"


class LegacySnapshotPageBudgetExceeded(RollingHistoryOwnerScopeError):
    """A complete original page cannot fit within the fixed response budget."""

    def __init__(self, *, single_row: bool = False) -> None:
        target = "row" if single_row else "page"
        super().__init__(
            f"Legacy snapshot {target} exceeds the "
            f"{_MAX_ORIGINAL_PAGE_BYTES}-byte page budget"
        )


@dataclass(frozen=True)
class LegacySnapshotRollingAppendMapping:
    """One zero-based sealed source slot and its optional rolling event locator."""

    source_locator: LegacySourceLocatorV1
    classification: str
    event_reference: RollingHistoryReference | None


@dataclass(frozen=True)
class LegacySnapshotRollingAppendReceipt:
    """Committed progress for one bounded owner-side legacy append batch."""

    migration_id: str
    source_message_revision: int
    start_source_sequence: int
    next_source_sequence: int
    total_count: int
    source_chain_sha256: str
    rolling_head_sequence: int
    rolling_head_chain_sha256: str
    mappings: tuple[LegacySnapshotRollingAppendMapping, ...]


@dataclass(frozen=True)
class LegacySnapshotRollingAppendProgress:
    """One owner-verified readback of all committed source mappings."""

    state: str
    migration_id: str
    source_message_revision: int
    total_count: int
    next_source_sequence: int
    source_chain_sha256: str
    text_projection_count: int
    archive_only_count: int
    progress_rolling_head_sequence: int
    progress_rolling_head_chain_sha256: str
    progress_rolling_head_reference: dict[str, object] | None
    current_rolling_head_sequence: int
    current_rolling_head_chain_sha256: str
    mappings: tuple[LegacySnapshotRollingAppendMapping, ...]


@dataclass(frozen=True)
class LegacyMessageSourceFence:
    """Live Message fence read without opening a nested owner transaction."""

    message_source_generation: int
    message_count: int
    non_append_generation: int


@dataclass(frozen=True)
class LegacySnapshotRollingAppendProgressPage:
    """One bounded page backed by a checkpoint and same-DB source counter.

    ``message_source_generation`` is the exact integer read from the current
    owner's ``message_source_generations.generation`` row in this transaction.
    """

    state: str
    migration_id: str
    source_message_revision: int
    message_source_generation: int
    checkpoint_root_sha256: str
    total_count: int
    verified_source_cursor: int
    next_cursor: int
    has_more: bool
    page_has_more: bool
    source_chain_sha256: str
    text_projection_count: int
    archive_only_count: int
    progress_rolling_head_sequence: int
    progress_rolling_head_chain_sha256: str
    current_rolling_head_sequence: int
    current_rolling_head_chain_sha256: str
    mappings: tuple[LegacySnapshotRollingAppendMapping, ...]


@dataclass(frozen=True)
class LegacySnapshotAppendOutcome:
    """Host result for one exact append attempt or an unknown commit outcome."""

    outcome: str
    receipt: LegacySnapshotRollingAppendReceipt | None
    checkpoint_root_sha256: str | None
    message_source_generation: int | None


def start_legacy_snapshot_generation(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
) -> LegacySnapshotGenerationStatus:
    """Create or idempotently read one host-requested generation manifest.

    The migration ID is a retry key, not an authorization token. All owner
    identity values come from the authenticated user and bound host scope.
    Repeating this explicit start after the generation is sealed also creates
    or verifies its host-only rolling-append start marker.
    """
    _require_migration_id(migration_id)
    with bootstrap_rolling_history_owner_scope(user, origin):
        return run_rolling_history_owner_operation(
            user,
            OperationMode.WRITE,
            lambda handle: _start_in_transaction(handle, origin, migration_id),
        )


def get_legacy_snapshot_generation_status(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
) -> LegacySnapshotGenerationStatus:
    """Read one generation status after rechecking its owner binding."""
    _require_migration_id(migration_id)
    with bootstrap_rolling_history_owner_scope(user, origin):
        return run_rolling_history_owner_operation(
            user,
            OperationMode.READ,
            lambda handle: _status_in_transaction(handle, migration_id),
        )


def relate_legacy_snapshot_generation_to_source(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
) -> LegacySnapshotSourceRelation:
    """Compare a sealed generation with its owner-bound live source snapshot.

    The result describes only the read transaction performed here. It is not
    authorization to read archived bodies later; any future body read must
    revalidate the relation in the same read transaction as that access.
    """
    _require_migration_id(migration_id)
    with bootstrap_rolling_history_owner_scope(user, origin):
        return run_rolling_history_owner_operation(
            user,
            OperationMode.READ,
            lambda handle: _relate_generation_to_source_in_transaction(
                handle, migration_id,
            ),
        )


def read_legacy_snapshot_original_page(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    start_sequence: int,
    page_size: int = 32,
) -> LegacySnapshotOriginalPage:
    """Read a bounded, lossless page from a freshly validated sealed prefix.

    Owner, session incarnation, live-source relation, archive chain, requested
    row range, and each returned row are checked in one read transaction. The
    result intentionally contains no owner principal, session binding, or
    incarnation fields; original envelope fields remain lossless.
    """
    _require_migration_id(migration_id)
    _require_original_page_arguments(start_sequence, page_size)
    with bootstrap_rolling_history_owner_scope(user, origin):
        return run_rolling_history_owner_operation(
            user,
            OperationMode.READ,
            lambda handle: _read_original_page_in_transaction(
                handle, migration_id, start_sequence, page_size,
            ),
        )


def read_legacy_snapshot_original_row_chunk(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    sequence: int,
    start_offset: int,
    chunk_length: int,
) -> LegacySnapshotOriginalRowChunk:
    """Read one bounded chunk from a freshly validated legacy snapshot row.

    Each call materializes the full envelope in-process, proves its ASCII byte
    encoding, and validates the full sealed chain and live-source relation in
    the same owner-bound read transaction as the requested chunk; that full
    relation scan can hold a read transaction open for a long time. Calls do
    not share a transaction: source edits or owner-epoch changes can make a
    later chunk fail. Callers must verify a common locator, SHA-256, and byte
    length across every chunk, collect contiguous ranges from offset zero
    through the total byte length, and verify the full SHA-256 before use. The
    ``complete`` flag only marks that this chunk reaches the envelope's end;
    it does not certify that the caller has the full message. Incomplete bytes
    are not fit for a summarizer or model. Archive-only rows retain their
    locator's ``model_availability`` and produce no model body.
    """
    _require_migration_id(migration_id)
    _require_original_row_chunk_arguments(sequence, start_offset, chunk_length)
    with bootstrap_rolling_history_owner_scope(user, origin):
        return run_rolling_history_owner_operation(
            user,
            OperationMode.READ,
            lambda handle: _read_original_row_chunk_in_transaction(
                handle,
                migration_id,
                sequence,
                start_offset,
                chunk_length,
            ),
        )


def capture_legacy_message_snapshot_batch(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    expected_cursor: int,
    expected_chain_hash: str,
    batch_size: int = _DEFAULT_CAPTURE_BATCH_SIZE,
) -> LegacySnapshotGenerationStatus:
    """Capture one bounded, owner-bound batch of legacy Message rows.

    The migration ID identifies the generation and is only a correlation key.
    A stale cursor is rejected: without a batch ledger this module cannot prove
    that an older request is an exact replay of one committed batch.
    """
    _require_migration_id(migration_id)
    _require_capture_batch_arguments(expected_cursor, expected_chain_hash, batch_size)
    with bootstrap_rolling_history_owner_scope(user, origin):
        return run_rolling_history_owner_operation(
            user,
            OperationMode.WRITE,
            lambda handle: _capture_batch_in_transaction(
                handle,
                migration_id,
                expected_cursor,
                expected_chain_hash,
                batch_size,
            ),
        )


def seal_legacy_snapshot_generation(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
) -> LegacySnapshotGenerationStatus:
    """Seal a fully captured archive after verifying every source and row.

    The complete source-to-snapshot comparison, chain verification, and
    generation compare-and-swap run under one owner-bound ``BEGIN IMMEDIATE``
    transaction. This is a local archive-integrity state, not a model-context
    completeness or downstream publication acknowledgement.
    """
    _require_migration_id(migration_id)
    with bootstrap_rolling_history_owner_scope(user, origin):
        return run_rolling_history_owner_operation(
            user,
            OperationMode.WRITE,
            lambda handle: _seal_generation_in_transaction(handle, migration_id),
        )


def append_sealed_legacy_snapshot_batch(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    expected_source_message_revision: int,
    expected_cursor: int,
    expected_rolling_head_sequence: int,
    expected_rolling_head_chain_sha256: str,
    idempotency_key: str,
    batch_size: int = _DEFAULT_LEGACY_APPEND_BATCH_SIZE,
) -> LegacySnapshotRollingAppendReceipt:
    """Append only verified legacy plain text inside one owner write transaction.

    ``user`` must be the authenticated route's current user and ``origin`` must
    select its already-bound host scope. The caller supplies no event body,
    owner identity, or authorization decision. Legacy source sequence is
    zero-based and is stored separately from the one-based rolling event
    sequence. Archive-only rows retain their original locator and canonical
    envelope in an immutable Settings mapping, with no model-visible event.
    """
    _require_migration_id(migration_id)
    _require_legacy_append_arguments(
        expected_source_message_revision=expected_source_message_revision,
        expected_cursor=expected_cursor,
        expected_rolling_head_sequence=expected_rolling_head_sequence,
        expected_rolling_head_chain_sha256=expected_rolling_head_chain_sha256,
        idempotency_key=idempotency_key,
        batch_size=batch_size,
    )
    # The compatibility entry point reads the current root/generation first;
    # the append transaction rechecks both before touching any row.
    page = read_legacy_snapshot_append_progress_page(
        user, origin, migration_id, after_cursor=0, page_size=1,
    )
    outcome = append_sealed_legacy_snapshot_checkpoint_batch(
        user,
        origin,
        migration_id,
        expected_source_message_revision=expected_source_message_revision,
        expected_message_source_generation=page.message_source_generation,
        expected_checkpoint_root_sha256=page.checkpoint_root_sha256,
        expected_cursor=expected_cursor,
        expected_rolling_head_sequence=expected_rolling_head_sequence,
        expected_rolling_head_chain_sha256=expected_rolling_head_chain_sha256,
        idempotency_key=idempotency_key,
        batch_size=batch_size,
    )
    if outcome.outcome != "confirmed" or outcome.receipt is None:
        raise LegacySnapshotGenerationConflict()
    return outcome.receipt


def append_sealed_legacy_snapshot_checkpoint_batch(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    expected_source_message_revision: int,
    expected_message_source_generation: int,
    expected_checkpoint_root_sha256: str,
    expected_cursor: int,
    expected_rolling_head_sequence: int,
    expected_rolling_head_chain_sha256: str,
    idempotency_key: str,
    batch_size: int = _DEFAULT_LEGACY_APPEND_BATCH_SIZE,
) -> LegacySnapshotAppendOutcome:
    """Append one bounded source page using a verified source-generation checkpoint.

    The durable exact-batch receipt is read in the same owner transaction as
    checkpoint CAS. ``confirmed`` covers both a newly committed append and an
    exact retry; ``conflict`` means the requested root/cursor/batch differs;
    ``outcome_unknown`` asks the host to reread progress before retrying.
    """
    _require_migration_id(migration_id)
    _require_legacy_append_arguments(
        expected_source_message_revision=expected_source_message_revision,
        expected_cursor=expected_cursor,
        expected_rolling_head_sequence=expected_rolling_head_sequence,
        expected_rolling_head_chain_sha256=expected_rolling_head_chain_sha256,
        idempotency_key=idempotency_key,
        batch_size=batch_size,
    )
    if (type(expected_message_source_generation) is not int
            or expected_message_source_generation < 0
            or type(expected_checkpoint_root_sha256) is not str
            or _HASH.fullmatch(expected_checkpoint_root_sha256) is None):
        raise ValueError("Invalid legacy snapshot checkpoint binding")
    try:
        with bootstrap_rolling_history_owner_scope(user, origin):
            result = run_rolling_history_owner_operation(
                user,
                OperationMode.WRITE,
                lambda handle: _append_legacy_checkpoint_batch_in_transaction(
                    handle,
                    origin,
                    migration_id,
                    expected_source_message_revision=expected_source_message_revision,
                    expected_source_generation=expected_message_source_generation,
                    expected_checkpoint_root_sha256=expected_checkpoint_root_sha256,
                    expected_cursor=expected_cursor,
                    expected_rolling_head_sequence=expected_rolling_head_sequence,
                    expected_rolling_head_chain_sha256=expected_rolling_head_chain_sha256,
                    idempotency_key=idempotency_key,
                    batch_size=batch_size,
                ),
            )
    except LegacySnapshotGenerationConflict:
        return LegacySnapshotAppendOutcome("conflict", None, None, None)
    except RollingHistoryOwnerScopeError:
        # The owner wrapper sanitizes both pre-commit refusal and commit errors.
        # The host can distinguish them by rereading the stable checkpoint key.
        return LegacySnapshotAppendOutcome("outcome_unknown", None, None, None)
    return result


def read_legacy_snapshot_append_progress(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
) -> LegacySnapshotRollingAppendProgress:
    """Read all committed legacy append progress under its bound host owner.

    This is a host-only API with no route. The caller supplies only the
    migration correlation ID; owner, scope, and source bodies come from the
    authenticated host binding and sealed archive.
    """
    _require_migration_id(migration_id)
    with bootstrap_rolling_history_owner_scope(user, origin):
        return run_rolling_history_owner_operation(
            user,
            OperationMode.READ,
            lambda handle: _read_legacy_snapshot_append_progress_in_transaction(
                handle, origin, migration_id,
            ),
        )


def read_legacy_snapshot_append_progress_page(
    user: dict | None,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    after_cursor: int = 0,
    page_size: int = 32,
) -> LegacySnapshotRollingAppendProgressPage:
    """Read a bounded host-only page backed by a verified durable checkpoint.

    An older append state without a checkpoint is fully reconciled and upgraded
    in this same owner write transaction. Once upgraded, later calls validate
    only the current source-generation binding, checkpoint, and requested page.
    ``state`` says whether all source slots have been appended; ``has_more``
    says whether unprocessed source slots remain, while ``page_has_more`` says
    whether more committed mapping rows remain after this page.
    """
    _require_migration_id(migration_id)
    if (type(after_cursor) is not int or after_cursor < 0
            or type(page_size) is not int
            or not 1 <= page_size <= _MAX_LEGACY_APPEND_BATCH_SIZE):
        raise ValueError("Invalid legacy snapshot progress page")
    try:
        with bootstrap_rolling_history_owner_scope(user, origin):
            return run_rolling_history_owner_operation(
                user,
                OperationMode.WRITE,
                lambda handle: _read_legacy_checkpoint_progress_page_in_transaction(
                    handle, origin, migration_id,
                    after_cursor=after_cursor, page_size=page_size,
                ),
            )
    except LegacySnapshotGenerationConflict:
        raise
    except Exception:
        raise LegacySnapshotGenerationConflict() from None


def _require_migration_id(migration_id: object) -> None:
    if type(migration_id) is not str or _MIGRATION_ID.fullmatch(migration_id) is None:
        raise ValueError("Invalid legacy message snapshot migration ID")


def _require_capture_batch_arguments(
    expected_cursor: object,
    expected_chain_hash: object,
    batch_size: object,
) -> None:
    if (type(expected_cursor) is not int
            or expected_cursor < 0
            or expected_cursor > (1 << 63) - 1):
        raise ValueError("Invalid legacy message snapshot capture cursor")
    if (type(expected_chain_hash) is not str
            or _HASH.fullmatch(expected_chain_hash) is None
            or (expected_cursor == 0 and expected_chain_hash != _ZERO_HASH)):
        raise ValueError("Invalid legacy message snapshot capture chain hash")
    if (type(batch_size) is not int
            or not 1 <= batch_size <= _MAX_CAPTURE_BATCH_SIZE):
        raise ValueError("Invalid legacy message snapshot capture batch size")


def _require_original_page_arguments(start_sequence: object, page_size: object) -> None:
    if (type(start_sequence) is not int
            or start_sequence < 0
            or start_sequence > (1 << 63) - 1):
        raise ValueError("Invalid legacy message snapshot page start sequence")
    if (type(page_size) is not int
            or not 1 <= page_size <= _MAX_ORIGINAL_PAGE_SIZE):
        raise ValueError("Invalid legacy message snapshot page size")


def _require_original_row_chunk_arguments(
    sequence: object,
    start_offset: object,
    chunk_length: object,
) -> None:
    if (type(sequence) is not int
            or sequence < 0
            or sequence > (1 << 63) - 1):
        raise ValueError("Invalid legacy message snapshot row sequence")
    if (type(start_offset) is not int
            or start_offset < 0
            or start_offset > (1 << 63) - 1):
        raise ValueError("Invalid legacy message snapshot row chunk offset")
    if (type(chunk_length) is not int
            or not 1 <= chunk_length <= _MAX_ORIGINAL_ROW_CHUNK_BYTES):
        raise ValueError("Invalid legacy message snapshot row chunk length")


def _capture_batch_in_transaction(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    expected_cursor: int,
    expected_chain_hash: str,
    batch_size: int,
) -> LegacySnapshotGenerationStatus:
    identity = get_rolling_history_owner_identity()
    source_revision, total_count = _read_current_source(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None:
        raise LegacySnapshotGenerationConflict()
    status = _status_from_row(
        generation, identity, migration_id, source_revision, total_count,
    )
    if (_is_sealed_generation(status)
            or status.coverage_status != "pending"
            or status.publication_status != "building"
            or status.capture_cursor != expected_cursor
            or status.capture_chain_hash != expected_chain_hash):
        raise LegacySnapshotGenerationConflict()

    # A prior cursor is never appended to. No batch ledger exists to prove an
    # old request's exact committed range and parameters, so stale retries fail
    # closed instead of risking duplicate rows.
    cursor = status.capture_cursor
    if _has_snapshot_suffix(handle, migration_id, cursor):
        raise LegacySnapshotGenerationConflict()

    source_rows = _read_rows(
        handle,
        "SELECT id, session_id, role, content, sources, tool_calls, activity, created_at "
        "FROM messages WHERE session_id = ? "
        "ORDER BY (created_at IS NOT NULL) ASC, created_at ASC, id COLLATE BINARY ASC "
        "LIMIT ? OFFSET ?",
        (identity.session_id, batch_size, cursor),
        _LEGACY_MESSAGE_COLUMNS,
    )
    expected_row_count = min(batch_size, total_count - cursor)
    if len(source_rows) != expected_row_count:
        raise LegacySnapshotGenerationConflict()

    chain_hash = status.capture_chain_hash
    seen_message_ids: set[str] = set()
    for offset, source_row in enumerate(source_rows):
        fields = _decode_legacy_message_row(source_row, identity.session_id)
        legacy_message_id = fields["id"]
        if type(legacy_message_id) is not str or legacy_message_id in seen_message_ids:
            raise LegacySnapshotGenerationConflict()
        seen_message_ids.add(legacy_message_id)

        sequence = cursor + offset
        try:
            encoded = encode_legacy_message_snapshot(
                fields,
                sequence=sequence,
                legacy_message_id=legacy_message_id,
                source_message_revision=source_revision,
            )
        except Exception:
            raise LegacySnapshotGenerationConflict() from None
        if (encoded.capture_status != "captured"
                or encoded.sequence != sequence
                or encoded.legacy_message_id != legacy_message_id
                or encoded.source_message_revision != source_revision
                or type(encoded.canonical_envelope_json) is not str
                or type(encoded.row_sha256) is not str
                or encoded.capture_reason is not None):
            # An unavailable codec result never means that the original row is
            # absent. Roll back this whole batch and retain the Message row.
            raise LegacySnapshotGenerationConflict()

        try:
            validation = validate_legacy_message_snapshot(encoded.canonical_envelope_json)
            if validation.canonical_envelope_json != encoded.canonical_envelope_json:
                raise LegacySnapshotGenerationConflict()
            row_hash = legacy_message_row_hash(
                sequence=sequence,
                legacy_message_id=legacy_message_id,
                source_message_revision=source_revision,
                capture_status="captured",
                capture_reason=None,
                canonical_envelope_json=encoded.canonical_envelope_json,
            )
        except LegacySnapshotGenerationConflict:
            raise
        except Exception:
            raise LegacySnapshotGenerationConflict() from None
        if row_hash != encoded.row_sha256:
            raise LegacySnapshotGenerationConflict()

        previous_chain_hash = None if sequence == 0 else chain_hash
        try:
            next_chain_hash = legacy_message_chain_hash(
                sequence=sequence,
                previous_chain_sha256=previous_chain_hash,
                row_sha256=row_hash,
            )
            _validate_legacy_source_payload(
                migration_id=migration_id,
                ordering_version=status.ordering_version,
                source_message_revision=source_revision,
                sequence=sequence,
                legacy_message_id=legacy_message_id,
                row_hash=row_hash,
                chain_hash=next_chain_hash,
                canonical_envelope_json=encoded.canonical_envelope_json,
                model_status=validation.model_status,
                model_view=validation.model_view,
            )
        except LegacySnapshotGenerationConflict:
            raise
        except Exception:
            raise LegacySnapshotGenerationConflict() from None

        inserted = handle.execute(
            "INSERT INTO legacy_message_snapshot_rows ("
            "migration_id, sequence, legacy_message_id, source_message_revision, "
            "canonical_envelope, row_hash, chain_hash, capture_status, capture_reason"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 'captured', NULL)",
            (
                migration_id,
                sequence,
                legacy_message_id,
                source_revision,
                encoded.canonical_envelope_json,
                row_hash,
                next_chain_hash,
            ),
        )
        if type(inserted) is not int or inserted != 1:
            raise LegacySnapshotGenerationConflict()
        chain_hash = next_chain_hash

    verified_revision, verified_total_count = _read_current_source(handle, identity)
    if ((verified_revision, verified_total_count)
            != (source_revision, total_count)):
        raise LegacySnapshotGenerationConflict()

    next_cursor = cursor + len(source_rows)
    updated = handle.execute(
        "UPDATE legacy_message_snapshot_generations "
        "SET capture_cursor = ?, capture_chain_hash = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE migration_id = ? AND session_id = ? AND incarnation_id = ? "
        "AND owner_principal_hash = ? AND source_message_revision = ? "
        "AND ordering_version = ? AND total_count = ? AND capture_cursor = ? "
        "AND capture_chain_hash = ? AND coverage_status = ? AND publication_status = ?",
        (
            next_cursor,
            chain_hash,
            migration_id,
            identity.session_id,
            identity.incarnation_id,
            identity.principal_hash,
            source_revision,
            status.ordering_version,
            total_count,
            cursor,
            status.capture_chain_hash,
            status.coverage_status,
            status.publication_status,
        ),
    )
    if type(updated) is not int or updated != 1:
        raise LegacySnapshotGenerationConflict()

    updated_generation = _read_generation(handle, migration_id)
    if updated_generation is None:
        raise LegacySnapshotGenerationConflict()
    updated_status = _status_from_row(
        updated_generation,
        identity,
        migration_id,
        source_revision,
        total_count,
    )
    if (updated_status.capture_cursor != next_cursor
            or updated_status.capture_chain_hash != chain_hash
            or updated_status.coverage_status != "pending"
            or updated_status.publication_status != "building"):
        raise LegacySnapshotGenerationConflict()
    return updated_status


def _seal_generation_in_transaction(
    handle: RollingHistorySqlHandle,
    migration_id: str,
) -> LegacySnapshotGenerationStatus:
    identity = get_rolling_history_owner_identity()
    source_revision, total_count = _read_current_source(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None:
        raise LegacySnapshotGenerationConflict()
    status = _status_from_row(
        generation, identity, migration_id, source_revision, total_count,
    )

    if _is_sealed_generation(status):
        text_count, archive_only_count = _scan_sealed_snapshot_rows(handle, status)
        return _with_projection_counts(status, text_count, archive_only_count)

    if (status.coverage_status != "pending"
            or status.publication_status != "building"
            or status.capture_cursor != status.total_count
            or status.source_message_revision != source_revision
            or status.total_count != total_count):
        raise LegacySnapshotGenerationConflict()

    _require_seal_cardinality(
        handle,
        session_id=identity.session_id,
        migration_id=migration_id,
        expected_count=total_count,
    )
    text_count, archive_only_count, verified_chain_hash = (
        _verify_source_snapshot_batches(
            handle,
            session_id=identity.session_id,
            migration_id=migration_id,
            source_message_revision=status.source_message_revision,
            total_count=status.total_count,
            ordering_version=status.ordering_version,
        )
    )
    if verified_chain_hash != status.capture_chain_hash:
        raise LegacySnapshotGenerationConflict()

    verified_revision, verified_count = _read_current_source(handle, identity)
    if (verified_revision, verified_count) != (source_revision, total_count):
        raise LegacySnapshotGenerationConflict()

    updated = handle.execute(
        "UPDATE legacy_message_snapshot_generations "
        "SET coverage_status = 'complete', publication_status = 'complete', "
        "capture_chain_hash = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE migration_id = ? AND session_id = ? AND incarnation_id = ? "
        "AND owner_principal_hash = ? AND source_message_revision = ? "
        "AND ordering_version = ? AND capture_cursor = ? AND total_count = ? "
        "AND coverage_status = ? AND publication_status = ? "
        "AND capture_chain_hash = ?",
        (
            verified_chain_hash,
            migration_id,
            identity.session_id,
            identity.incarnation_id,
            identity.principal_hash,
            status.source_message_revision,
            status.ordering_version,
            status.capture_cursor,
            status.total_count,
            status.coverage_status,
            status.publication_status,
            status.capture_chain_hash,
        ),
    )
    if type(updated) is not int or updated != 1:
        raise LegacySnapshotGenerationConflict()

    sealed_generation = _read_generation(handle, migration_id)
    if sealed_generation is None:
        raise LegacySnapshotGenerationConflict()
    sealed_status = _status_from_row(
        sealed_generation, identity, migration_id, source_revision, total_count,
    )
    if (not _is_sealed_generation(sealed_status)
            or sealed_status.capture_cursor != total_count
            or sealed_status.capture_chain_hash != verified_chain_hash):
        raise LegacySnapshotGenerationConflict()

    final_revision, final_count = _read_current_source(handle, identity)
    if (final_revision, final_count) != (source_revision, total_count):
        raise LegacySnapshotGenerationConflict()
    return _with_projection_counts(sealed_status, text_count, archive_only_count)


def _require_seal_cardinality(
    handle: RollingHistorySqlHandle,
    *,
    session_id: str,
    migration_id: str,
    expected_count: int,
    check_source_count: bool = True,
) -> None:
    if check_source_count:
        source_counts = _read_rows(
            handle,
            "SELECT COUNT(*) AS row_count, COUNT(DISTINCT id) AS distinct_id_count "
            "FROM messages WHERE session_id = ?",
            (session_id,),
            ("row_count", "distinct_id_count"),
        )
        if (len(source_counts) != 1
                or type(source_counts[0][0]) is not int
                or type(source_counts[0][1]) is not int
                or source_counts[0] != (expected_count, expected_count)):
            raise LegacySnapshotGenerationConflict()

    snapshot_counts = _read_rows(
        handle,
        "SELECT COUNT(*) AS row_count, COUNT(DISTINCT sequence) AS distinct_sequence_count, "
        "COUNT(DISTINCT legacy_message_id) AS distinct_message_id_count "
        "FROM legacy_message_snapshot_rows WHERE migration_id = ?",
        (migration_id,),
        ("row_count", "distinct_sequence_count", "distinct_message_id_count"),
    )
    if (len(snapshot_counts) != 1
            or any(type(value) is not int for value in snapshot_counts[0])
            or snapshot_counts[0] != (expected_count, expected_count, expected_count)):
        raise LegacySnapshotGenerationConflict()


def _read_source_message_page(
    handle: RollingHistorySqlHandle,
    session_id: str,
    offset: int,
) -> tuple[tuple[object, ...], ...]:
    return _read_rows(
        handle,
        "SELECT id, session_id, role, content, sources, tool_calls, activity, created_at "
        "FROM messages WHERE session_id = ? "
        "ORDER BY (created_at IS NOT NULL) ASC, created_at ASC, id COLLATE BINARY ASC "
        "LIMIT ? OFFSET ?",
        (session_id, _SEAL_BATCH_SIZE, offset),
        _LEGACY_MESSAGE_COLUMNS,
    )


def _read_snapshot_page(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    offset: int,
) -> tuple[tuple[object, ...], ...]:
    return _read_rows(
        handle,
        "SELECT migration_id, sequence, legacy_message_id, source_message_revision, "
        "canonical_envelope, row_hash, chain_hash, capture_status, capture_reason "
        "FROM legacy_message_snapshot_rows WHERE migration_id = ? "
        "ORDER BY sequence ASC LIMIT ? OFFSET ?",
        (migration_id, _SEAL_BATCH_SIZE, offset),
        _SNAPSHOT_ROW_COLUMNS,
    )


def _verify_source_snapshot_batches(
    handle: RollingHistorySqlHandle,
    *,
    session_id: str,
    migration_id: str,
    source_message_revision: int,
    total_count: int,
    ordering_version: str,
) -> tuple[int, int, str]:
    text_projection_count = 0
    archive_only_count = 0
    chain_hash = _ZERO_HASH
    offset = 0

    while offset < total_count:
        expected_size = min(_SEAL_BATCH_SIZE, total_count - offset)
        source_rows = _read_source_message_page(handle, session_id, offset)
        snapshot_rows = _read_snapshot_page(handle, migration_id, offset)
        if len(source_rows) != expected_size or len(snapshot_rows) != expected_size:
            raise LegacySnapshotGenerationConflict()

        for page_index in range(expected_size):
            sequence = offset + page_index
            source_fields = _decode_legacy_message_row(
                source_rows[page_index], session_id,
            )
            (
                snapshot_fields,
                validation,
                legacy_message_id,
                canonical_envelope_json,
                row_hash,
                stored_chain_hash,
            ) = _validate_snapshot_storage_row(
                snapshot_rows[page_index],
                expected_migration_id=migration_id,
                expected_sequence=sequence,
                expected_source_message_revision=source_message_revision,
                expected_session_id=session_id,
            )
            if snapshot_fields != source_fields or legacy_message_id != source_fields["id"]:
                raise LegacySnapshotGenerationConflict()

            try:
                encoded = encode_legacy_message_snapshot(
                    source_fields,
                    sequence=sequence,
                    legacy_message_id=legacy_message_id,
                    source_message_revision=source_message_revision,
                )
                if (encoded.capture_status != "captured"
                        or encoded.sequence != sequence
                        or encoded.legacy_message_id != legacy_message_id
                        or encoded.source_message_revision != source_message_revision
                        or encoded.capture_reason is not None
                        or encoded.canonical_envelope_json != canonical_envelope_json
                        or encoded.row_sha256 != row_hash
                        or encoded.model_status != validation.model_status
                        or encoded.model_view != validation.model_view):
                    raise LegacySnapshotGenerationConflict()
                recomputed_row_hash = legacy_message_row_hash(
                    sequence=sequence,
                    legacy_message_id=legacy_message_id,
                    source_message_revision=source_message_revision,
                    capture_status="captured",
                    capture_reason=None,
                    canonical_envelope_json=canonical_envelope_json,
                )
                recomputed_chain_hash = legacy_message_chain_hash(
                    sequence=sequence,
                    previous_chain_sha256=None if sequence == 0 else chain_hash,
                    row_sha256=recomputed_row_hash,
                )
                _validate_legacy_source_payload(
                    migration_id=migration_id,
                    ordering_version=ordering_version,
                    source_message_revision=source_message_revision,
                    sequence=sequence,
                    legacy_message_id=legacy_message_id,
                    row_hash=recomputed_row_hash,
                    chain_hash=recomputed_chain_hash,
                    canonical_envelope_json=canonical_envelope_json,
                    model_status=validation.model_status,
                    model_view=validation.model_view,
                )
            except LegacySnapshotGenerationConflict:
                raise
            except Exception:
                raise LegacySnapshotGenerationConflict() from None

            if recomputed_row_hash != row_hash or recomputed_chain_hash != stored_chain_hash:
                raise LegacySnapshotGenerationConflict()
            if validation.model_status == "text_projection_available":
                if validation.model_view is None:
                    raise LegacySnapshotGenerationConflict()
                text_projection_count += 1
            elif validation.model_status == "archive_only":
                if validation.model_view is not None:
                    raise LegacySnapshotGenerationConflict()
                archive_only_count += 1
            else:
                raise LegacySnapshotGenerationConflict()
            chain_hash = recomputed_chain_hash

        offset += expected_size

    return text_projection_count, archive_only_count, chain_hash


def _scan_sealed_snapshot_rows(
    handle: RollingHistorySqlHandle,
    status: LegacySnapshotGenerationStatus,
) -> tuple[int, int]:
    _require_seal_cardinality(
        handle,
        session_id=status.session_id,
        migration_id=status.migration_id,
        expected_count=status.total_count,
        check_source_count=False,
    )
    text_projection_count = 0
    archive_only_count = 0
    chain_hash = _ZERO_HASH
    offset = 0

    while offset < status.total_count:
        expected_size = min(_SEAL_BATCH_SIZE, status.total_count - offset)
        snapshot_rows = _read_snapshot_page(handle, status.migration_id, offset)
        if len(snapshot_rows) != expected_size:
            raise LegacySnapshotGenerationConflict()

        for page_index, snapshot_row in enumerate(snapshot_rows):
            sequence = offset + page_index
            (
                _snapshot_fields,
                validation,
                legacy_message_id,
                canonical_envelope_json,
                row_hash,
                stored_chain_hash,
            ) = _validate_snapshot_storage_row(
                snapshot_row,
                expected_migration_id=status.migration_id,
                expected_sequence=sequence,
                expected_source_message_revision=status.source_message_revision,
                expected_session_id=status.session_id,
            )
            try:
                recomputed_chain_hash = legacy_message_chain_hash(
                    sequence=sequence,
                    previous_chain_sha256=None if sequence == 0 else chain_hash,
                    row_sha256=row_hash,
                )
                _validate_legacy_source_payload(
                    migration_id=status.migration_id,
                    ordering_version=status.ordering_version,
                    source_message_revision=status.source_message_revision,
                    sequence=sequence,
                    legacy_message_id=legacy_message_id,
                    row_hash=row_hash,
                    chain_hash=recomputed_chain_hash,
                    canonical_envelope_json=canonical_envelope_json,
                    model_status=validation.model_status,
                    model_view=validation.model_view,
                )
            except LegacySnapshotGenerationConflict:
                raise
            except Exception:
                raise LegacySnapshotGenerationConflict() from None

            if recomputed_chain_hash != stored_chain_hash:
                raise LegacySnapshotGenerationConflict()
            if validation.model_status == "text_projection_available":
                if validation.model_view is None:
                    raise LegacySnapshotGenerationConflict()
                text_projection_count += 1
            elif validation.model_status == "archive_only":
                if validation.model_view is not None:
                    raise LegacySnapshotGenerationConflict()
                archive_only_count += 1
            else:
                raise LegacySnapshotGenerationConflict()
            chain_hash = recomputed_chain_hash

        offset += expected_size

    if chain_hash != status.capture_chain_hash:
        raise LegacySnapshotGenerationConflict()
    return text_projection_count, archive_only_count


def _validate_snapshot_storage_row(
    row: tuple[object, ...],
    *,
    expected_migration_id: str,
    expected_sequence: int,
    expected_source_message_revision: int,
    expected_session_id: str,
) -> tuple[dict[str, object], LegacyMessageSnapshotValidation, str, str, str, str]:
    if type(row) is not tuple or len(row) != len(_SNAPSHOT_ROW_COLUMNS):
        raise LegacySnapshotGenerationConflict()
    (
        migration_id,
        sequence,
        legacy_message_id,
        source_message_revision,
        canonical_envelope_json,
        stored_row_hash,
        stored_chain_hash,
        capture_status,
        capture_reason,
    ) = row
    if (migration_id != expected_migration_id
            or type(sequence) is not int
            or sequence != expected_sequence
            or type(legacy_message_id) is not str
            or type(source_message_revision) is not int
            or source_message_revision != expected_source_message_revision
            or type(canonical_envelope_json) is not str
            or type(stored_row_hash) is not str
            or _HASH.fullmatch(stored_row_hash) is None
            or type(stored_chain_hash) is not str
            or _HASH.fullmatch(stored_chain_hash) is None
            or capture_status != "captured"
            or capture_reason is not None):
        raise LegacySnapshotGenerationConflict()

    try:
        validation = validate_legacy_message_snapshot(canonical_envelope_json)
        decoded_fields = decode_legacy_message_snapshot(canonical_envelope_json)
        if (validation.canonical_envelope_json != canonical_envelope_json
                or decoded_fields["id"] != legacy_message_id
                or decoded_fields["session_id"] != expected_session_id):
            raise LegacySnapshotGenerationConflict()
        recomputed_row_hash = legacy_message_row_hash(
            sequence=sequence,
            legacy_message_id=legacy_message_id,
            source_message_revision=source_message_revision,
            capture_status="captured",
            capture_reason=None,
            canonical_envelope_json=canonical_envelope_json,
        )
    except LegacySnapshotGenerationConflict:
        raise
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    if recomputed_row_hash != stored_row_hash:
        raise LegacySnapshotGenerationConflict()
    return (
        decoded_fields,
        validation,
        legacy_message_id,
        canonical_envelope_json,
        recomputed_row_hash,
        stored_chain_hash,
    )


def _is_sealed_generation(status: LegacySnapshotGenerationStatus) -> bool:
    return status.coverage_status == "complete" and status.publication_status == "complete"


def _with_projection_counts(
    status: LegacySnapshotGenerationStatus,
    text_projection_count: int,
    archive_only_count: int,
) -> LegacySnapshotGenerationStatus:
    if (type(text_projection_count) is not int
            or type(archive_only_count) is not int
            or text_projection_count < 0
            or archive_only_count < 0
            or text_projection_count + archive_only_count != status.total_count):
        raise LegacySnapshotGenerationConflict()
    return replace(
        status,
        text_projection_count=text_projection_count,
        archive_only_count=archive_only_count,
    )


def _status_with_sealed_projection_counts(
    handle: RollingHistorySqlHandle,
    status: LegacySnapshotGenerationStatus,
) -> LegacySnapshotGenerationStatus:
    if not _is_sealed_generation(status):
        return status
    text_count, archive_only_count = _scan_sealed_snapshot_rows(handle, status)
    return _with_projection_counts(status, text_count, archive_only_count)


def _has_snapshot_suffix(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    cursor: int,
) -> bool:
    rows = _read_rows(
        handle,
        "SELECT sequence FROM legacy_message_snapshot_rows "
        "WHERE migration_id = ? AND sequence >= ? LIMIT 1",
        (migration_id, cursor),
        ("sequence",),
    )
    return bool(rows)


def _decode_legacy_message_row(
    row: tuple[object, ...],
    expected_session_id: str,
) -> dict[str, object]:
    if type(row) is not tuple or len(row) != len(_LEGACY_MESSAGE_COLUMNS):
        raise LegacySnapshotGenerationConflict()
    fields = dict(zip(_LEGACY_MESSAGE_COLUMNS, row))
    if (type(fields["id"]) is not str
            or type(fields["session_id"]) is not str
            or fields["session_id"] != expected_session_id
            or type(fields["role"]) is not str
            or type(fields["content"]) is not str):
        raise LegacySnapshotGenerationConflict()
    for name in ("sources", "tool_calls", "activity"):
        fields[name] = _decode_sqlite_json_value(fields[name])
    fields["created_at"] = _decode_sqlite_datetime(fields["created_at"])
    return fields


def _decode_sqlite_json_value(value: object) -> object:
    # The SQLite JSON result processor configured by Column(JSON) uses
    # json.loads. Raw exec_driver_sql bypasses that processor, so reproduce it
    # explicitly for the text returned by SQLite; SQL NULL remains Python None.
    if value is None:
        return None
    if type(value) is not str:
        raise LegacySnapshotGenerationConflict()
    try:
        return json.loads(value)
    except (ValueError, TypeError, RecursionError):
        raise LegacySnapshotGenerationConflict() from None


def _decode_sqlite_datetime(value: object) -> datetime | None:
    # The engine uses SQLite's default DateTime handling without detect_types;
    # raw driver rows contain ISO text instead of SQLAlchemy's datetime value.
    if value is None:
        return None
    if type(value) is not str:
        raise LegacySnapshotGenerationConflict()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise LegacySnapshotGenerationConflict() from None


def _validate_legacy_source_payload(
    *,
    migration_id: str,
    ordering_version: str,
    source_message_revision: int,
    sequence: int,
    legacy_message_id: str,
    row_hash: str,
    chain_hash: str,
    canonical_envelope_json: str,
    model_status: str,
    model_view: object,
) -> None:
    model_role = getattr(model_view, "role", None)
    model_content = getattr(model_view, "content", None)
    locator = LegacySourceLocatorV1(
        schema_version=1,
        migration_id=migration_id,
        ordering_version=ordering_version,
        source_message_revision=source_message_revision,
        sequence=sequence,
        legacy_message_id=legacy_message_id,
        row_sha256=row_hash,
        chain_sha256=chain_hash,
        capture_status="captured",
        model_availability=model_status,
    )
    payload = LegacySourcePayloadV1(
        capture_reason=None,
        canonical_envelope_json=canonical_envelope_json,
        model_role=model_role,
        model_content=model_content,
    )
    validate_legacy_source_locator_v1(locator, payload)


def _legacy_append_start_marker_namespace(
    identity: RollingHistoryOwnerIdentity,
) -> str:
    return (
        rolling_history_session_prefix(identity.session_id)
        + "legacy-snapshot-start:v1:"
    )


def _read_legacy_append_start_marker(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
) -> tuple[str, str | None]:
    namespace = _legacy_append_start_marker_namespace(identity)
    marker_key = namespace + _scope_digest(scope) + ":" + migration_id
    suffix = ":" + migration_id
    rows = _read_rows(
        handle,
        "SELECT key, value FROM settings "
        "WHERE substr(key, 1, ?) = ? AND substr(key, -?) = ? "
        "ORDER BY key COLLATE BINARY ASC",
        (len(namespace), namespace, len(suffix), suffix),
        ("key", "value"),
    )
    marker_raw: str | None = None
    for key, raw in rows:
        if (type(key) is not str or type(raw) is not str
                or key != marker_key or marker_raw is not None):
            # A marker for this migration under another owner epoch or scope
            # is evidence of a changed binding, not permission to restart.
            raise LegacySnapshotGenerationConflict()
        marker_raw = raw
    return marker_key, marker_raw


def _require_no_legacy_append_evidence_under_other_scope(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
) -> None:
    session_prefix = rolling_history_session_prefix(identity.session_id)
    current_append_prefix = (
        _scope_prefix(scope) + _LEGACY_APPEND_NAMESPACE + migration_id + ":"
    )
    evidence_fragment = _LEGACY_APPEND_NAMESPACE + migration_id + ":"
    rows = _read_rows(
        handle,
        "SELECT key FROM settings "
        "WHERE substr(key, 1, ?) = ? AND instr(key, ?) > 0 "
        "ORDER BY key COLLATE BINARY ASC",
        (len(session_prefix), session_prefix, evidence_fragment),
        ("key",),
    )
    if any(
        type(row[0]) is not str or not row[0].startswith(current_append_prefix)
        for row in rows
    ):
        raise LegacySnapshotGenerationConflict()


def _legacy_append_start_marker_state(cursor: int, total_count: int) -> str:
    if cursor == total_count:
        return "complete"
    return "started" if cursor == 0 else "verified"


def _legacy_append_start_marker_value(
    *,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    status: LegacySnapshotGenerationStatus,
    initial_rolling_head: tuple[int, str],
    progress_value: dict[str, object],
    last_batch_sha256: str | None,
) -> dict[str, object]:
    cursor = progress_value["next_source_sequence"]
    return {
        "schema_version": 1,
        "kind": "legacy_snapshot_append_start_marker",
        "scope": _identity(scope),
        "migration_id": migration_id,
        "session_id": identity.session_id,
        "owner_principal_hash": identity.principal_hash,
        "incarnation_id": identity.incarnation_id,
        "delete_epoch": identity.delete_epoch,
        "source_message_revision": status.source_message_revision,
        "ordering_version": status.ordering_version,
        "total_count": status.total_count,
        "source_capture_chain_sha256": status.capture_chain_hash,
        "initial_rolling_head_sequence": initial_rolling_head[0],
        "initial_rolling_head_chain_sha256": initial_rolling_head[1],
        "state": _legacy_append_start_marker_state(cursor, status.total_count),
        "next_source_sequence": cursor,
        "source_chain_sha256": progress_value["source_chain_sha256"],
        "text_projection_count": progress_value["text_projection_count"],
        "archive_only_count": progress_value["archive_only_count"],
        "rolling_head_sequence": progress_value["rolling_head_sequence"],
        "rolling_head_chain_sha256": progress_value["rolling_head_chain_sha256"],
        "last_batch_sha256": last_batch_sha256,
        "progress_sha256": _sha256(_canonical(progress_value)),
    }


def _decode_legacy_append_start_marker(
    raw: str,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    migration_id: str,
) -> dict[str, object]:
    expected_keys = {
        "schema_version", "kind", "scope", "migration_id", "session_id",
        "owner_principal_hash", "incarnation_id", "delete_epoch",
        "source_message_revision", "ordering_version", "total_count",
        "source_capture_chain_sha256", "initial_rolling_head_sequence",
        "initial_rolling_head_chain_sha256", "state", "next_source_sequence",
        "source_chain_sha256", "text_projection_count", "archive_only_count",
        "rolling_head_sequence", "rolling_head_chain_sha256",
        "last_batch_sha256", "progress_sha256",
    }
    try:
        value = json.loads(raw)
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    cursor = value.get("next_source_sequence") if type(value) is dict else None
    expected_state = (
        _legacy_append_start_marker_state(cursor, status.total_count)
        if type(cursor) is int else None
    )
    if (
        type(value) is not dict
        or set(value) != expected_keys
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "legacy_snapshot_append_start_marker"
        or value["scope"] != _identity(scope)
        or value["migration_id"] != migration_id
        or value["session_id"] != identity.session_id
        or value["owner_principal_hash"] != identity.principal_hash
        or value["incarnation_id"] != identity.incarnation_id
        or type(value["delete_epoch"]) is not str
        or (value["delete_epoch"] != ""
            and _MIGRATION_ID.fullmatch(value["delete_epoch"]) is None)
        or value["delete_epoch"] != identity.delete_epoch
        or type(value["source_message_revision"]) is not int
        or value["source_message_revision"] != status.source_message_revision
        or value["ordering_version"] != status.ordering_version
        or type(value["total_count"]) is not int
        or value["total_count"] != status.total_count
        or value["source_capture_chain_sha256"] != status.capture_chain_hash
        or type(value["initial_rolling_head_sequence"]) is not int
        or value["initial_rolling_head_sequence"] < 0
        or type(value["initial_rolling_head_chain_sha256"]) is not str
        or _HASH.fullmatch(value["initial_rolling_head_chain_sha256"]) is None
        or (value["initial_rolling_head_sequence"] == 0
            and value["initial_rolling_head_chain_sha256"] != _genesis_chain(scope))
        or type(cursor) is not int
        or not 0 <= cursor <= status.total_count
        or value["state"] != expected_state
        or type(value["source_chain_sha256"]) is not str
        or _HASH.fullmatch(value["source_chain_sha256"]) is None
        or type(value["text_projection_count"]) is not int
        or value["text_projection_count"] < 0
        or type(value["archive_only_count"]) is not int
        or value["archive_only_count"] < 0
        or value["text_projection_count"] + value["archive_only_count"] != cursor
        or type(value["rolling_head_sequence"]) is not int
        or value["rolling_head_sequence"] < value["initial_rolling_head_sequence"]
        or type(value["rolling_head_chain_sha256"]) is not str
        or _HASH.fullmatch(value["rolling_head_chain_sha256"]) is None
        or (cursor == 0 and value["source_chain_sha256"] != _ZERO_HASH)
        or (cursor == 0 and value["rolling_head_sequence"]
            != value["initial_rolling_head_sequence"])
        or (cursor == 0 and value["rolling_head_chain_sha256"]
            != value["initial_rolling_head_chain_sha256"])
        or (cursor == 0 and value["last_batch_sha256"] is not None)
        or (cursor > 0 and (type(value["last_batch_sha256"]) is not str
                            or _HASH.fullmatch(value["last_batch_sha256"]) is None))
        or type(value["progress_sha256"]) is not str
        or _HASH.fullmatch(value["progress_sha256"]) is None
        or _canonical(value) != raw
    ):
        raise LegacySnapshotGenerationConflict()
    return value


def _legacy_append_marker_matches_progress(
    marker: dict[str, object],
    progress: dict[str, object],
    progress_raw: str,
) -> None:
    if (
        marker["next_source_sequence"] != progress["next_source_sequence"]
        or marker["source_chain_sha256"] != progress["source_chain_sha256"]
        or marker["text_projection_count"] != progress["text_projection_count"]
        or marker["archive_only_count"] != progress["archive_only_count"]
        or marker["rolling_head_sequence"] != progress["rolling_head_sequence"]
        or marker["rolling_head_chain_sha256"]
        != progress["rolling_head_chain_sha256"]
        or marker["progress_sha256"] != _sha256(progress_raw)
    ):
        raise LegacySnapshotGenerationConflict()


def _write_legacy_append_start_marker(
    handle: RollingHistorySqlHandle,
    key: str,
    previous_raw: str | None,
    value: dict[str, object],
) -> str:
    encoded = _canonical(value)
    if previous_raw is None:
        _owner_history_insert_setting(handle, key, encoded)
        return encoded
    updated = handle.execute(
        "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE key = ? AND value = ?",
        (encoded, key, previous_raw),
    )
    if type(updated) is not int or updated != 1:
        raise LegacySnapshotGenerationConflict()
    return encoded


def _start_legacy_append_marker_in_transaction(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
    status: LegacySnapshotGenerationStatus,
) -> None:
    scope, identity = _legacy_append_scope_and_identity(origin)
    session_rows = _read_rows(
        handle,
        "SELECT branch_from_session_id, branch_from_message_id "
        "FROM sessions WHERE id = ?",
        (identity.session_id,),
        ("branch_from_session_id", "branch_from_message_id"),
    )
    if (len(session_rows) != 1
            or session_rows[0][0] is not None
            or session_rows[0][1] is not None):
        raise LegacySnapshotGenerationConflict()

    source_revision, source_count = _read_current_source(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None:
        raise LegacySnapshotGenerationConflict()
    current_status = _status_from_row(
        generation, identity, migration_id, source_revision, source_count,
    )
    if (
        not _is_sealed_generation(current_status)
        or not _is_sealed_generation(status)
        or current_status.migration_id != status.migration_id
        or current_status.session_id != status.session_id
        or current_status.source_message_revision != status.source_message_revision
        or current_status.ordering_version != status.ordering_version
        or current_status.capture_cursor != status.capture_cursor
        or current_status.total_count != status.total_count
        or current_status.capture_chain_hash != status.capture_chain_hash
        or current_status.coverage_status != status.coverage_status
        or current_status.publication_status != status.publication_status
        or status.source_message_revision != source_revision
        or status.total_count != source_count
    ):
        raise LegacySnapshotGenerationConflict()

    relation = _relate_generation_to_source_in_transaction(handle, migration_id)
    if relation is not LegacySnapshotSourceRelation.EXACT_CURRENT:
        raise LegacySnapshotGenerationConflict()
    if _read_current_source(handle, identity) != (source_revision, source_count):
        raise LegacySnapshotGenerationConflict()

    marker_key, marker_raw = _read_legacy_append_start_marker(
        handle, scope, identity, migration_id,
    )
    history_prefix = _scope_prefix(scope)
    mapping_prefix = history_prefix + _LEGACY_APPEND_NAMESPACE + migration_id + ":"
    progress_key = mapping_prefix + "progress"
    namespace_rows = _read_settings_with_prefix(handle, mapping_prefix)
    if marker_raw is not None:
        _read_legacy_snapshot_append_progress_verified(
            handle, origin, migration_id,
        )
        return
    _require_no_legacy_append_evidence_under_other_scope(
        handle, scope, identity, migration_id,
    )
    if namespace_rows:
        # Progress, mapping, receipt, or an unknown append value without its
        # start marker is an evidence gap and cannot be adopted as new work.
        raise LegacySnapshotGenerationConflict()

    current_head = _owner_history_head(handle, scope, history_prefix)
    _verify_legacy_append_history_readback(
        handle,
        scope,
        history_prefix,
        current_head,
        {},
        {0: _genesis_chain(scope)},
    )
    progress_value = _legacy_append_progress_value(
        scope=scope,
        identity=identity,
        migration_id=migration_id,
        status=status,
        next_source_sequence=0,
        source_chain_sha256=_ZERO_HASH,
        text_projection_count=0,
        archive_only_count=0,
        rolling_head=current_head,
    )
    progress_raw = _canonical(progress_value)
    marker_value = _legacy_append_start_marker_value(
        scope=scope,
        identity=identity,
        migration_id=migration_id,
        status=status,
        initial_rolling_head=current_head,
        progress_value=progress_value,
        last_batch_sha256=None,
    )
    marker_encoded = _canonical(marker_value)
    if _owner_history_read_setting(handle, progress_key) is not None:
        raise LegacySnapshotGenerationConflict()
    _owner_history_insert_setting(handle, progress_key, progress_raw)
    _owner_history_insert_setting(handle, marker_key, marker_encoded)
    if (
        _owner_history_read_setting(handle, progress_key) != progress_raw
        or _owner_history_read_setting(handle, marker_key) != marker_encoded
    ):
        raise LegacySnapshotGenerationConflict()
    _decode_legacy_append_start_marker(
        marker_encoded, scope, identity, status, migration_id,
    )


def _start_in_transaction(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
) -> LegacySnapshotGenerationStatus:
    identity = get_rolling_history_owner_identity()
    source_revision, total_count = _read_current_source(handle, identity)
    existing = _read_generation(handle, migration_id)
    if existing is not None:
        status = _status_with_sealed_projection_counts(
            handle,
            _status_from_row(
                existing, identity, migration_id, source_revision, total_count,
            ),
        )
        if _is_sealed_generation(status):
            _start_legacy_append_marker_in_transaction(
                handle, origin, migration_id, status,
            )
        return status

    inserted = handle.execute(
        "INSERT INTO legacy_message_snapshot_generations ("
        "migration_id, session_id, incarnation_id, owner_principal_hash, "
        "source_message_revision, ordering_version, capture_cursor, total_count, "
        "coverage_status, publication_status, capture_chain_hash, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'pending', 'building', ?, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (
            migration_id,
            identity.session_id,
            identity.incarnation_id,
            identity.principal_hash,
            source_revision,
            _ORDERING_VERSION,
            total_count,
            _ZERO_HASH,
        ),
    )
    if type(inserted) is not int or inserted != 1:
        raise LegacySnapshotGenerationConflict()

    created = _read_generation(handle, migration_id)
    if created is None:
        raise LegacySnapshotGenerationConflict()
    return _status_from_row(
        created, identity, migration_id, source_revision, total_count,
    )


def _status_in_transaction(
    handle: RollingHistorySqlHandle,
    migration_id: str,
) -> LegacySnapshotGenerationStatus:
    identity = get_rolling_history_owner_identity()
    source_revision, total_count = _read_current_source(handle, identity)
    row = _read_generation(handle, migration_id)
    if row is None:
        raise LegacySnapshotGenerationConflict()
    return _status_with_sealed_projection_counts(
        handle,
        _status_from_row(
            row, identity, migration_id, source_revision, total_count,
        ),
    )


def _relate_generation_to_source_in_transaction(
    handle: RollingHistorySqlHandle,
    migration_id: str,
) -> LegacySnapshotSourceRelation:
    identity = get_rolling_history_owner_identity()
    source_revision, current_count = _read_current_source(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None:
        raise LegacySnapshotGenerationConflict()
    status = _status_from_row(
        generation, identity, migration_id, source_revision, current_count,
    )
    if not _is_sealed_generation(status):
        raise LegacySnapshotGenerationConflict()

    # Validate the complete archive chain before comparing it to the live
    # ordered source. This scan and the source comparison share one read txn.
    _scan_sealed_snapshot_rows(handle, status)

    prefix_matches = True
    offset = 0
    while offset < current_count:
        source_size = min(_SEAL_BATCH_SIZE, current_count - offset)
        source_rows = _read_source_message_page(handle, identity.session_id, offset)
        if len(source_rows) != source_size:
            raise LegacySnapshotGenerationConflict()

        archived_size = min(source_size, max(0, status.total_count - offset))
        snapshot_rows = (
            _read_snapshot_page(handle, migration_id, offset)
            if archived_size
            else ()
        )
        if len(snapshot_rows) != archived_size:
            raise LegacySnapshotGenerationConflict()

        for page_index, source_row in enumerate(source_rows):
            sequence = offset + page_index
            source_fields = _decode_legacy_message_row(
                source_row, identity.session_id,
            )
            row_revision = (
                status.source_message_revision
                if sequence < status.total_count
                else source_revision
            )
            encoded = encode_legacy_message_snapshot(
                source_fields,
                sequence=sequence,
                legacy_message_id=source_fields["id"],
                source_message_revision=row_revision,
            )
            if (encoded.capture_status != "captured"
                    or encoded.sequence != sequence
                    or encoded.legacy_message_id != source_fields["id"]
                    or encoded.source_message_revision != row_revision
                    or encoded.capture_reason is not None
                    or encoded.canonical_envelope_json is None):
                raise LegacySnapshotGenerationConflict()

            if sequence < status.total_count:
                snapshot_fields, _validation, legacy_message_id, envelope, _row_hash, _chain_hash = (
                    _validate_snapshot_storage_row(
                        snapshot_rows[page_index],
                        expected_migration_id=migration_id,
                        expected_sequence=sequence,
                        expected_source_message_revision=status.source_message_revision,
                        expected_session_id=identity.session_id,
                    )
                )
                if (source_fields != snapshot_fields
                        or legacy_message_id != source_fields["id"]
                        or encoded.canonical_envelope_json != envelope):
                    prefix_matches = False

        offset += source_size

    if not prefix_matches or current_count < status.total_count:
        return LegacySnapshotSourceRelation.SUPERSEDED
    if current_count == status.total_count:
        return LegacySnapshotSourceRelation.EXACT_CURRENT
    if current_count > status.total_count:
        return LegacySnapshotSourceRelation.APPEND_ONLY_PREFIX
    raise LegacySnapshotGenerationConflict()


def _read_original_page_in_transaction(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    start_sequence: int,
    page_size: int,
) -> LegacySnapshotOriginalPage:
    # This is deliberately the first business read in the callback. It verifies
    # the full sealed chain and compares the complete current source under the
    # same owner-bound read transaction that will return the requested page.
    relation = _relate_generation_to_source_in_transaction(handle, migration_id)
    if relation not in (
        LegacySnapshotSourceRelation.EXACT_CURRENT,
        LegacySnapshotSourceRelation.APPEND_ONLY_PREFIX,
    ):
        raise LegacySnapshotGenerationConflict()

    identity = get_rolling_history_owner_identity()
    source_revision, current_count = _read_current_source(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None:
        raise LegacySnapshotGenerationConflict()
    status = _status_from_row(
        generation, identity, migration_id, source_revision, current_count,
    )
    if not _is_sealed_generation(status):
        raise LegacySnapshotGenerationConflict()
    if start_sequence > status.total_count:
        raise LegacySnapshotGenerationConflict()

    expected_count = min(page_size, status.total_count - start_sequence)
    snapshot_rows = _read_original_snapshot_rows(
        handle, migration_id, start_sequence, expected_count,
    )
    if len(snapshot_rows) != expected_count:
        raise LegacySnapshotGenerationConflict()

    previous_chain_hash: str | None = None
    if expected_count and start_sequence:
        previous_rows = _read_rows(
            handle,
            "SELECT sequence, chain_hash FROM legacy_message_snapshot_rows "
            "WHERE migration_id = ? AND sequence = ?",
            (migration_id, start_sequence - 1),
            ("sequence", "chain_hash"),
        )
        if (len(previous_rows) != 1
                or type(previous_rows[0][0]) is not int
                or previous_rows[0][0] != start_sequence - 1
                or type(previous_rows[0][1]) is not str
                or _HASH.fullmatch(previous_rows[0][1]) is None):
            raise LegacySnapshotGenerationConflict()
        previous_chain_hash = previous_rows[0][1]

    page_rows: list[LegacySnapshotOriginalPageRow] = []
    for offset, snapshot_row in enumerate(snapshot_rows):
        sequence = start_sequence + offset
        (
            _snapshot_fields,
            validation,
            legacy_message_id,
            canonical_envelope_json,
            row_hash,
            stored_chain_hash,
        ) = _validate_snapshot_storage_row(
            snapshot_row,
            expected_migration_id=migration_id,
            expected_sequence=sequence,
            expected_source_message_revision=status.source_message_revision,
            expected_session_id=identity.session_id,
        )
        try:
            chain_hash = legacy_message_chain_hash(
                sequence=sequence,
                previous_chain_sha256=previous_chain_hash,
                row_sha256=row_hash,
            )
            if chain_hash != stored_chain_hash:
                raise LegacySnapshotGenerationConflict()
            model_view = validation.model_view
            locator = LegacySourceLocatorV1(
                schema_version=1,
                migration_id=migration_id,
                ordering_version=status.ordering_version,
                source_message_revision=status.source_message_revision,
                sequence=sequence,
                legacy_message_id=legacy_message_id,
                row_sha256=row_hash,
                chain_sha256=chain_hash,
                capture_status="captured",
                model_availability=validation.model_status,
            )
            payload = LegacySourcePayloadV1(
                capture_reason=None,
                canonical_envelope_json=canonical_envelope_json,
                model_role=None if model_view is None else model_view.role,
                model_content=None if model_view is None else model_view.content,
            )
            validate_legacy_source_locator_v1(locator, payload)
        except LegacySnapshotGenerationConflict:
            raise
        except Exception:
            raise LegacySnapshotGenerationConflict() from None

        page_row = LegacySnapshotOriginalPageRow(
            sequence=sequence,
            locator=locator,
            canonical_envelope_json=canonical_envelope_json,
        )
        single_row_size = _original_page_payload_size(
            relation=relation,
            source_message_revision=status.source_message_revision,
            ordering_version=status.ordering_version,
            start_sequence=sequence,
            next_sequence=sequence + 1,
            total_count=status.total_count,
            rows=(page_row,),
        )
        if single_row_size > _MAX_ORIGINAL_PAGE_BYTES:
            raise LegacySnapshotPageBudgetExceeded(single_row=True)

        page_rows.append(page_row)
        page_size_bytes = _original_page_payload_size(
            relation=relation,
            source_message_revision=status.source_message_revision,
            ordering_version=status.ordering_version,
            start_sequence=start_sequence,
            next_sequence=sequence + 1,
            total_count=status.total_count,
            rows=tuple(page_rows),
        )
        if page_size_bytes > _MAX_ORIGINAL_PAGE_BYTES:
            raise LegacySnapshotPageBudgetExceeded()
        previous_chain_hash = chain_hash

    next_sequence = start_sequence + len(page_rows)
    return LegacySnapshotOriginalPage(
        source_relation=relation.value,
        source_message_revision=status.source_message_revision,
        ordering_version=status.ordering_version,
        start_sequence=start_sequence,
        next_sequence=next_sequence,
        total_count=status.total_count,
        rows=tuple(page_rows),
    )


def _read_original_row_chunk_in_transaction(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    sequence: int,
    start_offset: int,
    chunk_length: int,
) -> LegacySnapshotOriginalRowChunk:
    # The complete archive chain and live-source relation are deliberately
    # verified before returning any bytes, in this same owner-bound read txn.
    relation = _relate_generation_to_source_in_transaction(handle, migration_id)
    if relation not in (
        LegacySnapshotSourceRelation.EXACT_CURRENT,
        LegacySnapshotSourceRelation.APPEND_ONLY_PREFIX,
    ):
        raise LegacySnapshotGenerationConflict()

    identity = get_rolling_history_owner_identity()
    source_revision, current_count = _read_current_source(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None:
        raise LegacySnapshotGenerationConflict()
    status = _status_from_row(
        generation, identity, migration_id, source_revision, current_count,
    )
    if not _is_sealed_generation(status) or sequence >= status.total_count:
        raise LegacySnapshotGenerationConflict()

    snapshot_rows = _read_original_snapshot_rows(handle, migration_id, sequence, 1)
    if len(snapshot_rows) != 1:
        raise LegacySnapshotGenerationConflict()
    (
        _snapshot_fields,
        validation,
        legacy_message_id,
        canonical_envelope_json,
        row_hash,
        stored_chain_hash,
    ) = _validate_snapshot_storage_row(
        snapshot_rows[0],
        expected_migration_id=migration_id,
        expected_sequence=sequence,
        expected_source_message_revision=status.source_message_revision,
        expected_session_id=identity.session_id,
    )

    previous_chain_hash: str | None = None
    if sequence:
        previous_rows = _read_rows(
            handle,
            "SELECT sequence, chain_hash FROM legacy_message_snapshot_rows "
            "WHERE migration_id = ? AND sequence = ?",
            (migration_id, sequence - 1),
            ("sequence", "chain_hash"),
        )
        if (len(previous_rows) != 1
                or type(previous_rows[0][0]) is not int
                or previous_rows[0][0] != sequence - 1
                or type(previous_rows[0][1]) is not str
                or _HASH.fullmatch(previous_rows[0][1]) is None):
            raise LegacySnapshotGenerationConflict()
        previous_chain_hash = previous_rows[0][1]

    try:
        chain_hash = legacy_message_chain_hash(
            sequence=sequence,
            previous_chain_sha256=previous_chain_hash,
            row_sha256=row_hash,
        )
        if chain_hash != stored_chain_hash:
            raise LegacySnapshotGenerationConflict()
        model_view = validation.model_view
        locator = LegacySourceLocatorV1(
            schema_version=1,
            migration_id=migration_id,
            ordering_version=status.ordering_version,
            source_message_revision=status.source_message_revision,
            sequence=sequence,
            legacy_message_id=legacy_message_id,
            row_sha256=row_hash,
            chain_sha256=chain_hash,
            capture_status="captured",
            model_availability=validation.model_status,
        )
        payload = LegacySourcePayloadV1(
            capture_reason=None,
            canonical_envelope_json=canonical_envelope_json,
            model_role=None if model_view is None else model_view.role,
            model_content=None if model_view is None else model_view.content,
        )
        validate_legacy_source_locator_v1(locator, payload)
        envelope_bytes = canonical_envelope_json.encode("ascii", errors="strict")
        if envelope_bytes.decode("ascii", errors="strict") != canonical_envelope_json:
            raise LegacySnapshotGenerationConflict()
    except LegacySnapshotGenerationConflict:
        raise
    except Exception:
        raise LegacySnapshotGenerationConflict() from None

    total_byte_length = len(envelope_bytes)
    if start_offset >= total_byte_length:
        raise LegacySnapshotGenerationConflict()
    end_offset = min(start_offset + chunk_length, total_byte_length)
    chunk_bytes = envelope_bytes[start_offset:end_offset]
    if not chunk_bytes:
        raise LegacySnapshotGenerationConflict()

    return LegacySnapshotOriginalRowChunk(
        source_relation=relation.value,
        source_message_revision=status.source_message_revision,
        ordering_version=status.ordering_version,
        sequence=sequence,
        locator=locator,
        total_byte_length=total_byte_length,
        start_offset=start_offset,
        end_offset=end_offset,
        next_offset=end_offset,
        complete=end_offset == total_byte_length,
        full_envelope_sha256=hashlib.sha256(envelope_bytes).hexdigest(),
        chunk_bytes=chunk_bytes,
    )


def _original_page_payload_size(
    *,
    relation: LegacySnapshotSourceRelation,
    source_message_revision: int,
    ordering_version: str,
    start_sequence: int,
    next_sequence: int,
    total_count: int,
    rows: tuple[LegacySnapshotOriginalPageRow, ...],
) -> int:
    payload = {
        "source_relation": relation.value,
        "source_message_revision": source_message_revision,
        "ordering_version": ordering_version,
        "start_sequence": start_sequence,
        "next_sequence": next_sequence,
        "total_count": total_count,
        "rows": [
            {
                "sequence": row.sequence,
                "locator": {
                    "schema_version": row.locator.schema_version,
                    "migration_id": row.locator.migration_id,
                    "ordering_version": row.locator.ordering_version,
                    "source_message_revision": row.locator.source_message_revision,
                    "sequence": row.locator.sequence,
                    "legacy_message_id": row.locator.legacy_message_id,
                    "row_sha256": row.locator.row_sha256,
                    "chain_sha256": row.locator.chain_sha256,
                    "capture_status": row.locator.capture_status,
                    "model_availability": row.locator.model_availability,
                },
                "canonical_envelope_json": row.canonical_envelope_json,
            }
            for row in rows
        ],
    }
    return len(json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))


def _read_original_snapshot_rows(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    start_sequence: int,
    count: int,
) -> tuple[tuple[object, ...], ...]:
    if count == 0:
        return ()
    return _read_rows(
        handle,
        "SELECT migration_id, sequence, legacy_message_id, source_message_revision, "
        "canonical_envelope, row_hash, chain_hash, capture_status, capture_reason "
        "FROM legacy_message_snapshot_rows WHERE migration_id = ? "
        "ORDER BY sequence ASC LIMIT ? OFFSET ?",
        (migration_id, count, start_sequence),
        _SNAPSHOT_ROW_COLUMNS,
    )


def _read_original_snapshot_rows_after(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    after_sequence: int,
    count: int,
) -> tuple[tuple[object, ...], ...]:
    """Read a fixed page by sealed sequence keyset, with no OFFSET scan."""
    if count == 0:
        return ()
    return _read_rows(
        handle,
        "SELECT migration_id, sequence, legacy_message_id, source_message_revision, "
        "canonical_envelope, row_hash, chain_hash, capture_status, capture_reason "
        "FROM legacy_message_snapshot_rows WHERE migration_id = ? AND sequence >= ? "
        "ORDER BY sequence ASC LIMIT ?",
        (migration_id, after_sequence, count),
        _SNAPSHOT_ROW_COLUMNS,
    )


def _read_current_source(
    handle: RollingHistorySqlHandle,
    identity: RollingHistoryOwnerIdentity,
) -> tuple[int, int]:
    sessions = _read_rows(
        handle,
        "SELECT message_revision FROM sessions WHERE id = ?",
        (identity.session_id,),
        ("message_revision",),
    )
    if len(sessions) != 1:
        raise LegacySnapshotGenerationConflict()
    source_revision = sessions[0][0]
    if type(source_revision) is not int or source_revision < 0:
        raise LegacySnapshotGenerationConflict()

    counts = _read_rows(
        handle,
        "SELECT COUNT(*) AS total_count FROM messages WHERE session_id = ?",
        (identity.session_id,),
        ("total_count",),
    )
    if len(counts) != 1:
        raise LegacySnapshotGenerationConflict()
    total_count = counts[0][0]
    if type(total_count) is not int or total_count < 0:
        raise LegacySnapshotGenerationConflict()
    return source_revision, total_count


def _read_generation(
    handle: RollingHistorySqlHandle,
    migration_id: str,
) -> tuple[object, ...] | None:
    rows = _read_rows(
        handle,
        _GENERATION_SELECT,
        (migration_id,),
        _GENERATION_COLUMNS,
    )
    if len(rows) > 1:
        raise LegacySnapshotGenerationConflict()
    return None if not rows else rows[0]


def _read_rows(
    handle: RollingHistorySqlHandle,
    statement: str,
    parameters: tuple[object, ...],
    columns: tuple[str, ...],
) -> tuple[tuple[object, ...], ...]:
    result = handle.execute(statement, parameters)
    if type(result) is not MaterializedRows or result.columns != columns:
        raise LegacySnapshotGenerationConflict()
    return result.rows


def _status_from_row(
    row: tuple[object, ...],
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    source_revision: int,
    total_count: int,
) -> LegacySnapshotGenerationStatus:
    if len(row) != len(_GENERATION_COLUMNS):
        raise LegacySnapshotGenerationConflict()

    (
        row_migration_id,
        session_id,
        incarnation_id,
        principal_hash,
        row_source_revision,
        ordering_version,
        capture_cursor,
        row_total_count,
        coverage_status,
        publication_status,
        capture_chain_hash,
    ) = row

    # A sealed manifest describes a historical source revision and count. Keep
    # it readable after ordinary appends while still enforcing the live owner
    # and incarnation binding above.
    is_sealed = coverage_status == "complete" and publication_status == "complete"
    if (
        row_migration_id != migration_id
        or session_id != identity.session_id
        or incarnation_id != identity.incarnation_id
        or principal_hash != identity.principal_hash
        or (not is_sealed and row_source_revision != source_revision)
        or ordering_version != _ORDERING_VERSION
        or (not is_sealed and row_total_count != total_count)
        or type(row_source_revision) is not int
        or row_source_revision < 0
        or type(capture_cursor) is not int
        or capture_cursor < 0
        or type(row_total_count) is not int
        or row_total_count < 0
        or capture_cursor > row_total_count
        or type(incarnation_id) is not str
        or _INCARNATION_ID.fullmatch(incarnation_id) is None
        or type(principal_hash) is not str
        or _HASH.fullmatch(principal_hash) is None
        or type(capture_chain_hash) is not str
        or _HASH.fullmatch(capture_chain_hash) is None
        or (capture_cursor == 0 and capture_chain_hash != _ZERO_HASH)
        or type(coverage_status) is not str
        or coverage_status not in _COVERAGE_STATUSES
        or type(publication_status) is not str
        or publication_status not in _PUBLICATION_STATUSES
        or (coverage_status == "complete" and capture_cursor != row_total_count)
        or (
            publication_status == "complete"
            and (
                capture_cursor != row_total_count
                or coverage_status not in {"complete", "incomplete"}
            )
        )
    ):
        raise LegacySnapshotGenerationConflict()

    return LegacySnapshotGenerationStatus(
        migration_id=migration_id,
        session_id=identity.session_id,
        source_message_revision=row_source_revision,
        ordering_version=_ORDERING_VERSION,
        capture_cursor=capture_cursor,
        total_count=row_total_count,
        capture_chain_hash=capture_chain_hash,
        coverage_status=coverage_status,
        publication_status=publication_status,
    )


@dataclass(frozen=True)
class _LegacyAppendSourceRow:
    locator: LegacySourceLocatorV1
    canonical_envelope_json: str
    classification: str
    event_payload: dict[str, object] | None
    encoded_event_payload: str | None


def _require_legacy_append_arguments(
    *,
    expected_source_message_revision: object,
    expected_cursor: object,
    expected_rolling_head_sequence: object,
    expected_rolling_head_chain_sha256: object,
    idempotency_key: object,
    batch_size: object,
) -> None:
    max_integer = (1 << 63) - 1
    if (type(expected_source_message_revision) is not int
            or expected_source_message_revision < 0
            or expected_source_message_revision > max_integer):
        raise ValueError("Invalid legacy snapshot source revision")
    if (type(expected_cursor) is not int or expected_cursor < 0
            or expected_cursor > max_integer):
        raise ValueError("Invalid legacy snapshot source cursor")
    if (type(expected_rolling_head_sequence) is not int
            or expected_rolling_head_sequence < 0
            or expected_rolling_head_sequence > max_integer):
        raise ValueError("Invalid rolling history expected head")
    if (type(expected_rolling_head_chain_sha256) is not str
            or _HASH.fullmatch(expected_rolling_head_chain_sha256) is None):
        raise ValueError("Invalid rolling history expected head")
    if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid legacy snapshot append idempotency key")
    try:
        idempotency_key.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("Invalid legacy snapshot append idempotency key") from None
    if (type(batch_size) is not int or not 1 <= batch_size <= _MAX_LEGACY_APPEND_BATCH_SIZE):
        raise ValueError("Invalid legacy snapshot append batch size")


def _legacy_append_scope_and_identity(
    origin: ScopeOrigin,
) -> tuple[ContextEventScope, RollingHistoryOwnerIdentity]:
    from app.api.compaction_archive import get_compaction_scope
    from app.api.tool_events import get_tool_event_scope

    if type(origin) is not ScopeOrigin:
        raise LegacySnapshotGenerationConflict()
    scope = (
        get_compaction_scope()
        if origin is ScopeOrigin.COMPACTION
        else get_tool_event_scope()
    )
    if type(scope) is not ContextEventScope:
        raise LegacySnapshotGenerationConflict()
    try:
        _require_bound_scope(scope)
        identity = get_rolling_history_owner_identity()
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    if (
        (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
        != (
            identity.session_id,
            identity.request_id,
            identity.principal_hash,
            identity.delete_epoch,
        )
    ):
        raise LegacySnapshotGenerationConflict()
    return scope, identity


def _append_sealed_legacy_snapshot_batch_in_transaction(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    expected_source_message_revision: int,
    expected_cursor: int,
    expected_rolling_head_sequence: int,
    expected_rolling_head_chain_sha256: str,
    idempotency_key: str,
    batch_size: int,
) -> LegacySnapshotRollingAppendReceipt:
    scope, identity = _legacy_append_scope_and_identity(origin)
    session_rows = _read_rows(
        handle,
        "SELECT branch_from_session_id, branch_from_message_id "
        "FROM sessions WHERE id = ?",
        (identity.session_id,),
        ("branch_from_session_id", "branch_from_message_id"),
    )
    if (len(session_rows) != 1
            or session_rows[0][0] is not None
            or session_rows[0][1] is not None):
        raise LegacySnapshotGenerationConflict()

    source_revision, source_count = _read_current_source(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None:
        raise LegacySnapshotGenerationConflict()
    status = _status_from_row(
        generation, identity, migration_id, source_revision, source_count,
    )
    if (
        not _is_sealed_generation(status)
        or status.source_message_revision != source_revision
        or source_revision != expected_source_message_revision
        or status.total_count != source_count
        or expected_cursor >= status.total_count
    ):
        raise LegacySnapshotGenerationConflict()

    # Revalidate the sealed source, live relation, marker, and every committed
    # append witness in this same owner write transaction before extending it.
    _read_legacy_snapshot_append_progress_verified(handle, origin, migration_id)
    marker_key, marker_raw = _read_legacy_append_start_marker(
        handle, scope, identity, migration_id,
    )
    if marker_raw is None:
        raise LegacySnapshotGenerationConflict()
    marker = _decode_legacy_append_start_marker(
        marker_raw, scope, identity, status, migration_id,
    )

    rows = _read_legacy_append_source_rows(
        handle,
        migration_id,
        expected_cursor,
        min(batch_size, status.total_count - expected_cursor),
        status,
    )
    if not rows:
        raise LegacySnapshotGenerationConflict()

    next_source_sequence = expected_cursor + len(rows)
    source_chain_before = _snapshot_chain_at_cursor(
        handle, migration_id, expected_cursor,
    )
    source_chain_after = rows[-1].locator.chain_sha256
    if expected_cursor == 0 and source_chain_before != _ZERO_HASH:
        raise LegacySnapshotGenerationConflict()

    caller_key_sha256 = _sha256(idempotency_key)
    rolling_idempotency_key = (
        f"legacy-snapshot:{migration_id}:{caller_key_sha256}"
    )
    idempotency_id = _append_idempotency_id(scope, rolling_idempotency_key)
    history_prefix = _scope_prefix(scope)
    mapping_prefix = (
        history_prefix + _LEGACY_APPEND_NAMESPACE + migration_id + ":"
    )
    progress_key = mapping_prefix + "progress"
    batch_receipt_key = mapping_prefix + "batch:" + idempotency_id
    rolling_idempotency_db_key = history_prefix + "idempotency:" + idempotency_id
    encoded_event_payloads = tuple(
        row.encoded_event_payload
        for row in rows
        if row.encoded_event_payload is not None
    )
    event_payloads = tuple(
        row.event_payload for row in rows if row.event_payload is not None
    )
    if (len(encoded_event_payloads) != len(event_payloads)
            or any(type(item) is not str for item in encoded_event_payloads)
            or any(type(item) is not dict for item in event_payloads)):
        raise LegacySnapshotGenerationConflict()
    rolling_batch_sha256 = _batch_digest(encoded_event_payloads)
    batch_sha256 = _legacy_append_batch_sha256(
        migration_id=migration_id,
        scope=scope,
        expected_source_message_revision=expected_source_message_revision,
        expected_cursor=expected_cursor,
        expected_rolling_head_sequence=expected_rolling_head_sequence,
        expected_rolling_head_chain_sha256=expected_rolling_head_chain_sha256,
        batch_size=batch_size,
        rows=rows,
        encoded_event_payloads=encoded_event_payloads,
    )

    current_head = _owner_history_head(handle, scope, history_prefix)
    existing_receipt_raw = _owner_history_read_setting(handle, batch_receipt_key)
    if existing_receipt_raw is not None:
        return _read_legacy_append_retry_in_transaction(
            handle,
            scope,
            identity,
            status,
            history_prefix,
            mapping_prefix,
            progress_key,
            batch_receipt_key,
            rolling_idempotency_db_key,
            existing_receipt_raw,
            idempotency_id,
            caller_key_sha256,
            batch_sha256,
            rolling_batch_sha256,
            encoded_event_payloads,
            event_payloads,
            rows,
            expected_source_message_revision,
            expected_cursor,
            expected_rolling_head_sequence,
            expected_rolling_head_chain_sha256,
            current_head,
        )

    progress_raw = _owner_history_read_setting(handle, progress_key)
    progress = (
        None
        if progress_raw is None
        else _decode_legacy_append_progress(
            progress_raw, scope, identity, status, migration_id,
        )
    )
    progress_cursor = 0 if progress is None else progress["next_source_sequence"]
    progress_chain = _ZERO_HASH if progress is None else progress["source_chain_sha256"]
    if (progress is None and marker["next_source_sequence"] != 0):
        raise LegacySnapshotGenerationConflict()
    if progress is not None:
        _legacy_append_marker_matches_progress(marker, progress, progress_raw)
    if (progress is not None
            and _snapshot_chain_at_cursor(
                handle, migration_id, progress_cursor,
            ) != progress_chain):
        raise LegacySnapshotGenerationConflict()
    if (expected_cursor != progress_cursor
            or marker["next_source_sequence"] != expected_cursor
            or source_chain_before != progress_chain
            or current_head != (
                expected_rolling_head_sequence,
                expected_rolling_head_chain_sha256,
            )):
        raise LegacySnapshotGenerationConflict()
    if progress is not None:
        _verify_legacy_append_progress_head(
            handle,
            scope,
            history_prefix,
            progress["rolling_head_sequence"],
            progress["rolling_head_chain_sha256"],
            current_head,
        )
    if _owner_history_read_setting(handle, rolling_idempotency_db_key) is not None:
        raise LegacySnapshotGenerationConflict()

    mapping_values = []
    mapping_keys = []
    for row in rows:
        key = mapping_prefix + "source:" + f"{row.locator.sequence:032d}"
        if _owner_history_read_setting(handle, key) is not None:
            raise LegacySnapshotGenerationConflict()
        mapping_keys.append(key)

    rolling_references, new_head = _append_legacy_rolling_events_in_transaction(
        handle,
        scope,
        history_prefix,
        idempotency_id,
        rolling_idempotency_db_key,
        event_payloads,
        encoded_event_payloads,
        current_head,
    )
    _verify_new_legacy_rolling_event_refs(
        handle,
        scope,
        history_prefix,
        idempotency_id,
        rolling_references,
        event_payloads,
        encoded_event_payloads,
    )
    references_by_source = _legacy_append_references_by_source(
        rows, rolling_references,
    )
    for row, key in zip(rows, mapping_keys, strict=True):
        mapping = _legacy_append_source_mapping(
            scope, migration_id, row, references_by_source[row.locator.sequence],
        )
        mapping_values.append(_canonical(mapping))
        _owner_history_insert_setting(handle, key, mapping_values[-1])

    previous_text_count = 0 if progress is None else progress["text_projection_count"]
    previous_archive_only_count = (
        0 if progress is None else progress["archive_only_count"]
    )
    text_count = sum(row.classification == "text_projection_available" for row in rows)
    archive_only_count = sum(row.classification == "archive_only" for row in rows)
    receipt = _legacy_append_batch_receipt_value(
        scope=scope,
        identity=identity,
        migration_id=migration_id,
        idempotency_id=idempotency_id,
        idempotency_key_sha256=caller_key_sha256,
        batch_sha256=batch_sha256,
        rolling_batch_sha256=rolling_batch_sha256,
        source_revision=status.source_message_revision,
        total_count=status.total_count,
        start_source_sequence=expected_cursor,
        next_source_sequence=next_source_sequence,
        source_chain_before=source_chain_before,
        source_chain_after=source_chain_after,
        previous_rolling_head=current_head,
        result_rolling_head=new_head,
        mappings=tuple(
            _legacy_append_mapping_receipt_value(
                row.locator,
                row.classification,
                references_by_source[row.locator.sequence],
            )
            for row in rows
        ),
    )
    receipt_raw = _canonical(receipt)
    _owner_history_insert_setting(handle, batch_receipt_key, receipt_raw)
    next_progress_value = _legacy_append_progress_value(
        scope=scope,
        identity=identity,
        migration_id=migration_id,
        status=status,
        next_source_sequence=next_source_sequence,
        source_chain_sha256=source_chain_after,
        text_projection_count=previous_text_count + text_count,
        archive_only_count=previous_archive_only_count + archive_only_count,
        rolling_head=new_head,
    )
    next_progress_raw = _canonical(next_progress_value)
    _write_legacy_append_progress(
        handle, progress_key, progress_raw, next_progress_value,
    )
    marker_value = _legacy_append_start_marker_value(
        scope=scope,
        identity=identity,
        migration_id=migration_id,
        status=status,
        initial_rolling_head=(
            marker["initial_rolling_head_sequence"],
            marker["initial_rolling_head_chain_sha256"],
        ),
        progress_value=next_progress_value,
        last_batch_sha256=batch_sha256,
    )
    marker_encoded = _write_legacy_append_start_marker(
        handle, marker_key, marker_raw, marker_value,
    )

    if _owner_history_read_setting(handle, batch_receipt_key) != receipt_raw:
        raise LegacySnapshotGenerationConflict()
    if new_head != _owner_history_head(handle, scope, history_prefix):
        raise LegacySnapshotGenerationConflict()
    for row, key, expected_raw in zip(rows, mapping_keys, mapping_values, strict=True):
        if _owner_history_read_setting(handle, key) != expected_raw:
            raise LegacySnapshotGenerationConflict()
    written_progress = _owner_history_read_setting(handle, progress_key)
    if written_progress is None:
        raise LegacySnapshotGenerationConflict()
    decoded_progress = _decode_legacy_append_progress(
        written_progress, scope, identity, status, migration_id,
    )
    written_marker = _owner_history_read_setting(handle, marker_key)
    if (written_progress != next_progress_raw
            or written_marker != marker_encoded
            or decoded_progress["next_source_sequence"] != next_source_sequence
            or decoded_progress["source_chain_sha256"] != source_chain_after):
        raise LegacySnapshotGenerationConflict()
    decoded_marker = _decode_legacy_append_start_marker(
        written_marker, scope, identity, status, migration_id,
    )
    _legacy_append_marker_matches_progress(
        decoded_marker, decoded_progress, written_progress,
    )
    return _legacy_append_receipt_from_value(receipt)


def _snapshot_chain_at_cursor(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    cursor: int,
) -> str:
    if cursor == 0:
        return _ZERO_HASH
    rows = _read_rows(
        handle,
        "SELECT sequence, chain_hash FROM legacy_message_snapshot_rows "
        "WHERE migration_id = ? AND sequence = ?",
        (migration_id, cursor - 1),
        ("sequence", "chain_hash"),
    )
    if (len(rows) != 1
            or type(rows[0][0]) is not int
            or rows[0][0] != cursor - 1
            or type(rows[0][1]) is not str
            or _HASH.fullmatch(rows[0][1]) is None):
        raise LegacySnapshotGenerationConflict()
    return rows[0][1]


def _read_legacy_append_source_rows(
    handle: RollingHistorySqlHandle,
    migration_id: str,
    start_sequence: int,
    count: int,
    status: LegacySnapshotGenerationStatus,
) -> tuple[_LegacyAppendSourceRow, ...]:
    snapshot_rows = _read_original_snapshot_rows_after(
        handle, migration_id, start_sequence, count,
    )
    if len(snapshot_rows) != count:
        raise LegacySnapshotGenerationConflict()

    previous_chain = (
        None
        if start_sequence == 0
        else _snapshot_chain_at_cursor(handle, migration_id, start_sequence)
    )
    result: list[_LegacyAppendSourceRow] = []
    for offset, snapshot_row in enumerate(snapshot_rows):
        sequence = start_sequence + offset
        (
            _snapshot_fields,
            validation,
            legacy_message_id,
            canonical_envelope_json,
            row_hash,
            stored_chain_hash,
        ) = _validate_snapshot_storage_row(
            snapshot_row,
            expected_migration_id=migration_id,
            expected_sequence=sequence,
            expected_source_message_revision=status.source_message_revision,
            expected_session_id=status.session_id,
        )
        try:
            verified_chain_hash = legacy_message_chain_hash(
                sequence=sequence,
                previous_chain_sha256=previous_chain,
                row_sha256=row_hash,
            )
            if verified_chain_hash != stored_chain_hash:
                raise LegacySnapshotGenerationConflict()
            model_view = validation.model_view
            locator = LegacySourceLocatorV1(
                schema_version=1,
                migration_id=migration_id,
                ordering_version=status.ordering_version,
                source_message_revision=status.source_message_revision,
                sequence=sequence,
                legacy_message_id=legacy_message_id,
                row_sha256=row_hash,
                chain_sha256=verified_chain_hash,
                capture_status="captured",
                model_availability=validation.model_status,
            )
            payload = LegacySourcePayloadV1(
                capture_reason=None,
                canonical_envelope_json=canonical_envelope_json,
                model_role=None if model_view is None else model_view.role,
                model_content=None if model_view is None else model_view.content,
            )
            validate_legacy_source_locator_v1(locator, payload)
        except LegacySnapshotGenerationConflict:
            raise
        except Exception:
            raise LegacySnapshotGenerationConflict() from None

        if validation.model_status == "text_projection_available":
            if (model_view is None or model_view.role not in {"user", "assistant"}
                    or type(model_view.content) is not str):
                raise LegacySnapshotGenerationConflict()
            event_type = (
                "user_text" if model_view.role == "user" else "assistant_text"
            )
            event_content = _canonical_json({
                "codec_version": _CODEC_VERSION,
                "content": model_view.content,
                # Legacy Message rows have no provider-visible name field.
                "name": None,
            })
            event_payload, encoded_event_payload = _encode_event({
                "event_type": event_type,
                "content": event_content,
            })
            result.append(_LegacyAppendSourceRow(
                locator=locator,
                canonical_envelope_json=canonical_envelope_json,
                classification="text_projection_available",
                event_payload=event_payload,
                encoded_event_payload=encoded_event_payload,
            ))
        elif validation.model_status == "archive_only":
            if model_view is not None:
                raise LegacySnapshotGenerationConflict()
            result.append(_LegacyAppendSourceRow(
                locator=locator,
                canonical_envelope_json=canonical_envelope_json,
                classification="archive_only",
                event_payload=None,
                encoded_event_payload=None,
            ))
        else:
            # The current seal protocol cannot seal capture-unavailable rows.
            # Treat any such value in a claimed sealed generation as corrupt.
            raise LegacySnapshotGenerationConflict()
        previous_chain = verified_chain_hash
    return tuple(result)


def _legacy_append_batch_sha256(
    *,
    migration_id: str,
    scope: ContextEventScope,
    expected_source_message_revision: int,
    expected_cursor: int,
    expected_rolling_head_sequence: int,
    expected_rolling_head_chain_sha256: str,
    batch_size: int,
    rows: tuple[_LegacyAppendSourceRow, ...],
    encoded_event_payloads: tuple[str, ...],
) -> str:
    value = {
        "schema_version": 1,
        "kind": "legacy_snapshot_append_batch",
        "scope": _identity(scope),
        "migration_id": migration_id,
        "source_message_revision": expected_source_message_revision,
        "start_source_sequence": expected_cursor,
        "expected_rolling_head_sequence": expected_rolling_head_sequence,
        "expected_rolling_head_chain_sha256": expected_rolling_head_chain_sha256,
        "batch_size": batch_size,
        "source_rows": [
            {
                "locator": _legacy_locator_value(row.locator),
                "classification": row.classification,
                "canonical_envelope_json": row.canonical_envelope_json,
            }
            for row in rows
        ],
        "encoded_event_payloads": list(encoded_event_payloads),
    }
    return _sha256(_canonical(value))


def _legacy_locator_value(locator: LegacySourceLocatorV1) -> dict[str, object]:
    return {
        "schema_version": locator.schema_version,
        "migration_id": locator.migration_id,
        "ordering_version": locator.ordering_version,
        "source_message_revision": locator.source_message_revision,
        "sequence": locator.sequence,
        "legacy_message_id": locator.legacy_message_id,
        "row_sha256": locator.row_sha256,
        "chain_sha256": locator.chain_sha256,
        "capture_status": locator.capture_status,
        "model_availability": locator.model_availability,
    }


def _legacy_rolling_reference_value(
    reference: RollingHistoryReference,
) -> dict[str, object]:
    return {
        "event_id": reference.event_id,
        "session_id": reference.session_id,
        "request_id": reference.request_id,
        "sequence": reference.sequence,
        "version": reference.version,
        "content_sha256": reference.content_sha256,
        "chain_sha256": reference.chain_sha256,
    }


def _legacy_append_mapping_receipt_value(
    locator: LegacySourceLocatorV1,
    classification: str,
    reference: RollingHistoryReference | None,
) -> dict[str, object]:
    return {
        "source_locator": _legacy_locator_value(locator),
        "classification": classification,
        "event_reference": (
            None if reference is None else _legacy_rolling_reference_value(reference)
        ),
    }


def _legacy_append_source_mapping(
    scope: ContextEventScope,
    migration_id: str,
    row: _LegacyAppendSourceRow,
    reference: RollingHistoryReference | None,
) -> dict[str, object]:
    if ((row.classification == "archive_only") != (reference is None)
            or row.classification not in {
                "archive_only", "text_projection_available",
            }):
        raise LegacySnapshotGenerationConflict()
    return {
        "schema_version": 1,
        "kind": "legacy_source_event_mapping",
        "scope": _identity(scope),
        "migration_id": migration_id,
        "source_locator": _legacy_locator_value(row.locator),
        "classification": row.classification,
        # Keep the exact lossless archive envelope beside the locator so an
        # archive-only mapping remains resolvable without a fabricated event.
        "canonical_envelope_json": row.canonical_envelope_json,
        "event_reference": (
            None if reference is None else _legacy_rolling_reference_value(reference)
        ),
    }


def _legacy_append_references_by_source(
    rows: tuple[_LegacyAppendSourceRow, ...],
    references: tuple[RollingHistoryReference, ...],
) -> dict[int, RollingHistoryReference | None]:
    expected_count = sum(
        row.classification == "text_projection_available" for row in rows
    )
    if len(references) != expected_count:
        raise LegacySnapshotGenerationConflict()
    result: dict[int, RollingHistoryReference | None] = {}
    offset = 0
    for row in rows:
        if row.classification == "archive_only":
            result[row.locator.sequence] = None
            continue
        if row.classification != "text_projection_available":
            raise LegacySnapshotGenerationConflict()
        result[row.locator.sequence] = references[offset]
        offset += 1
    if len(result) != len(rows):
        raise LegacySnapshotGenerationConflict()
    return result


def _append_legacy_rolling_events_in_transaction(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    history_prefix: str,
    idempotency_id: str,
    idempotency_db_key: str,
    event_payloads: tuple[dict[str, object], ...],
    encoded_event_payloads: tuple[str, ...],
    current_head: tuple[int, str],
) -> tuple[tuple[RollingHistoryReference, ...], tuple[int, str]]:
    if len(event_payloads) != len(encoded_event_payloads):
        raise LegacySnapshotGenerationConflict()
    if not event_payloads:
        return (), current_head
    if _owner_history_read_setting(handle, idempotency_db_key) is not None:
        raise LegacySnapshotGenerationConflict()

    last_sequence, previous_chain = current_head
    first_sequence = last_sequence + 1
    references: list[RollingHistoryReference] = []
    for offset, (payload, encoded_payload) in enumerate(
        zip(event_payloads, encoded_event_payloads, strict=True),
        start=1,
    ):
        validated_payload, canonical_payload = _encode_event(payload)
        if validated_payload != payload or canonical_payload != encoded_payload:
            raise LegacySnapshotGenerationConflict()
        sequence = last_sequence + offset
        event_id = _sha256(f"{idempotency_id}\0{offset}")
        content_sha256 = _sha256(encoded_payload)
        chain_sha256 = _event_chain_digest(
            scope, sequence, previous_chain, event_id, content_sha256,
        )
        reference = RollingHistoryReference(
            event_id,
            scope.session_id,
            scope.request_id,
            sequence,
            1,
            content_sha256,
            chain_sha256,
        )
        event_value = {
            "schema_version": 1,
            "kind": "event",
            "scope": _identity(scope),
            "request_id": scope.request_id,
            "sequence": sequence,
            "event_id": event_id,
            "idempotency_id": idempotency_id,
            "payload": payload,
            "content_sha256": content_sha256,
            "previous_chain_sha256": previous_chain,
            "chain_sha256": chain_sha256,
        }
        id_value = {
            "schema_version": 1,
            "kind": "event_id",
            "scope": _identity(scope),
            "event_id": event_id,
            "request_id": scope.request_id,
            "sequence": sequence,
            "content_sha256": content_sha256,
            "chain_sha256": chain_sha256,
        }
        event_key = _event_key(history_prefix, sequence)
        id_key = _event_id_key(history_prefix, event_id)
        if (_owner_history_read_setting(handle, event_key) is not None
                or _owner_history_read_setting(handle, id_key) is not None):
            raise LegacySnapshotGenerationConflict()
        _owner_history_insert_setting(handle, event_key, _canonical(event_value))
        _owner_history_insert_setting(handle, id_key, _canonical(id_value))
        references.append(reference)
        previous_chain = chain_sha256

    idempotency_value = {
        "schema_version": 1,
        "kind": "append_idempotency",
        "scope": _identity(scope),
        "idempotency_id": idempotency_id,
        "request_id": scope.request_id,
        "batch_sha256": _batch_digest(encoded_event_payloads),
        "first_sequence": first_sequence,
        "event_count": len(event_payloads),
    }
    _owner_history_insert_setting(
        handle, idempotency_db_key, _canonical(idempotency_value),
    )
    new_head = (last_sequence + len(event_payloads), previous_chain)
    head_value = {
        "schema_version": 1,
        "kind": "history_head",
        "scope": _identity(scope),
        "last_sequence": new_head[0],
        "chain_sha256": new_head[1],
    }
    _owner_history_set_setting(
        handle, history_prefix + "meta:head", _canonical(head_value),
    )
    return tuple(references), new_head


def _verify_new_legacy_rolling_event_refs(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    history_prefix: str,
    idempotency_id: str,
    references: tuple[RollingHistoryReference, ...],
    event_payloads: tuple[dict[str, object], ...],
    encoded_event_payloads: tuple[str, ...],
) -> None:
    if (len(references) != len(event_payloads)
            or len(references) != len(encoded_event_payloads)):
        raise LegacySnapshotGenerationConflict()
    for offset, (reference, expected_payload, encoded_payload) in enumerate(
        zip(references, event_payloads, encoded_event_payloads, strict=True),
        start=1,
    ):
        event = _owner_history_load_event_at_sequence(
            handle, scope, history_prefix, reference.sequence,
        )
        event_raw = _owner_history_read_setting(
            handle, _event_key(history_prefix, reference.sequence),
        )
        try:
            event_value = json.loads(event_raw)
        except Exception:
            raise LegacySnapshotGenerationConflict() from None
        if (event is None
                or event.reference != reference
                or event.reference.event_id != _sha256(
                    f"{idempotency_id}\0{offset}"
                )
                or event.reference.request_id != scope.request_id
                or event.skill_provenance is not None
                or event.event_type != expected_payload["event_type"]
                or event.content != expected_payload["content"]
                or _canonical(event.model_payload()) != encoded_payload
                or type(event_value) is not dict
                or event_value.get("idempotency_id") != idempotency_id):
            raise LegacySnapshotGenerationConflict()


def _legacy_append_batch_receipt_value(
    *,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    idempotency_id: str,
    idempotency_key_sha256: str,
    batch_sha256: str,
    rolling_batch_sha256: str,
    source_revision: int,
    total_count: int,
    start_source_sequence: int,
    next_source_sequence: int,
    source_chain_before: str,
    source_chain_after: str,
    previous_rolling_head: tuple[int, str],
    result_rolling_head: tuple[int, str],
    mappings: tuple[dict[str, object], ...],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "legacy_snapshot_append_receipt",
        "scope": _identity(scope),
        "migration_id": migration_id,
        "incarnation_id": identity.incarnation_id,
        "request_id": scope.request_id,
        "idempotency_id": idempotency_id,
        "idempotency_key_sha256": idempotency_key_sha256,
        "batch_sha256": batch_sha256,
        "rolling_batch_sha256": rolling_batch_sha256,
        "source_message_revision": source_revision,
        "total_count": total_count,
        "start_source_sequence": start_source_sequence,
        "next_source_sequence": next_source_sequence,
        "source_chain_before": source_chain_before,
        "source_chain_after": source_chain_after,
        "previous_rolling_head_sequence": previous_rolling_head[0],
        "previous_rolling_head_chain_sha256": previous_rolling_head[1],
        "result_rolling_head_sequence": result_rolling_head[0],
        "result_rolling_head_chain_sha256": result_rolling_head[1],
        "mappings": list(mappings),
    }


def _legacy_append_progress_value(
    *,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    status: LegacySnapshotGenerationStatus,
    next_source_sequence: int,
    source_chain_sha256: str,
    text_projection_count: int,
    archive_only_count: int,
    rolling_head: tuple[int, str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "legacy_snapshot_append_progress",
        "scope": _identity(scope),
        "migration_id": migration_id,
        "incarnation_id": identity.incarnation_id,
        "source_message_revision": status.source_message_revision,
        "total_count": status.total_count,
        "source_capture_chain_sha256": status.capture_chain_hash,
        "next_source_sequence": next_source_sequence,
        "source_chain_sha256": source_chain_sha256,
        "text_projection_count": text_projection_count,
        "archive_only_count": archive_only_count,
        "rolling_head_sequence": rolling_head[0],
        "rolling_head_chain_sha256": rolling_head[1],
    }


def _decode_legacy_append_progress(
    raw: str,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    migration_id: str,
) -> dict[str, object]:
    expected_keys = {
        "schema_version", "kind", "scope", "migration_id", "incarnation_id",
        "source_message_revision", "total_count", "source_capture_chain_sha256",
        "next_source_sequence", "source_chain_sha256", "text_projection_count",
        "archive_only_count", "rolling_head_sequence", "rolling_head_chain_sha256",
    }
    try:
        value = json.loads(raw)
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    if (
        type(value) is not dict
        or set(value) != expected_keys
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "legacy_snapshot_append_progress"
        or value["scope"] != _identity(scope)
        or value["migration_id"] != migration_id
        or value["incarnation_id"] != identity.incarnation_id
        or type(value["source_message_revision"]) is not int
        or value["source_message_revision"] != status.source_message_revision
        or type(value["total_count"]) is not int
        or value["total_count"] != status.total_count
        or value["source_capture_chain_sha256"] != status.capture_chain_hash
        or type(value["next_source_sequence"]) is not int
        or not 0 <= value["next_source_sequence"] <= status.total_count
        or type(value["source_chain_sha256"]) is not str
        or _HASH.fullmatch(value["source_chain_sha256"]) is None
        or type(value["text_projection_count"]) is not int
        or value["text_projection_count"] < 0
        or type(value["archive_only_count"]) is not int
        or value["archive_only_count"] < 0
        or value["text_projection_count"] + value["archive_only_count"]
        != value["next_source_sequence"]
        or type(value["rolling_head_sequence"]) is not int
        or value["rolling_head_sequence"] < 0
        or type(value["rolling_head_chain_sha256"]) is not str
        or _HASH.fullmatch(value["rolling_head_chain_sha256"]) is None
        or (value["next_source_sequence"] == 0
            and value["source_chain_sha256"] != _ZERO_HASH)
        or _canonical(value) != raw
    ):
        raise LegacySnapshotGenerationConflict()
    return value


def _write_legacy_append_progress(
    handle: RollingHistorySqlHandle,
    key: str,
    previous_raw: str | None,
    value: dict[str, object],
) -> None:
    encoded = _canonical(value)
    if previous_raw is None:
        _owner_history_insert_setting(handle, key, encoded)
        return
    updated = handle.execute(
        "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE key = ? AND value = ?",
        (encoded, key, previous_raw),
    )
    if type(updated) is not int or updated != 1:
        raise LegacySnapshotGenerationConflict()


def _verify_legacy_append_progress_head(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    history_prefix: str,
    progress_head_sequence: int,
    progress_head_chain_sha256: str,
    current_head: tuple[int, str],
) -> None:
    if (progress_head_sequence, progress_head_chain_sha256) != current_head:
        raise LegacySnapshotGenerationConflict()
    if progress_head_sequence == 0:
        expected_chain = _genesis_chain(scope)
    else:
        event = _owner_history_load_event_at_sequence(
            handle, scope, history_prefix, progress_head_sequence,
        )
        if event is None:
            raise LegacySnapshotGenerationConflict()
        expected_chain = event.reference.chain_sha256
    if expected_chain != progress_head_chain_sha256:
        raise LegacySnapshotGenerationConflict()


def _legacy_append_receipt_from_value(
    value: dict[str, object],
) -> LegacySnapshotRollingAppendReceipt:
    try:
        mappings = []
        for item in value["mappings"]:
            locator_value = item["source_locator"]
            locator = LegacySourceLocatorV1(**locator_value)
            event_value = item["event_reference"]
            reference = (
                None
                if event_value is None
                else RollingHistoryReference(**event_value)
            )
            mappings.append(LegacySnapshotRollingAppendMapping(
                source_locator=locator,
                classification=item["classification"],
                event_reference=reference,
            ))
        return LegacySnapshotRollingAppendReceipt(
            migration_id=value["migration_id"],
            source_message_revision=value["source_message_revision"],
            start_source_sequence=value["start_source_sequence"],
            next_source_sequence=value["next_source_sequence"],
            total_count=value["total_count"],
            source_chain_sha256=value["source_chain_after"],
            rolling_head_sequence=value["result_rolling_head_sequence"],
            rolling_head_chain_sha256=value[
                "result_rolling_head_chain_sha256"
            ],
            mappings=tuple(mappings),
        )
    except Exception:
        raise LegacySnapshotGenerationConflict() from None


def _decode_legacy_rolling_reference(
    value: object,
) -> RollingHistoryReference:
    expected_keys = {
        "event_id", "session_id", "request_id", "sequence", "version",
        "content_sha256", "chain_sha256",
    }
    if (type(value) is not dict or set(value) != expected_keys
            or type(value["event_id"]) is not str
            or _HASH.fullmatch(value["event_id"]) is None
            or type(value["session_id"]) is not str
            or not 1 <= len(value["session_id"]) <= 128
            or type(value["request_id"]) is not str
            or _MIGRATION_ID.fullmatch(value["request_id"]) is None
            or type(value["sequence"]) is not int
            or value["sequence"] < 1
            or type(value["version"]) is not int
            or value["version"] != 1
            or type(value["content_sha256"]) is not str
            or _HASH.fullmatch(value["content_sha256"]) is None
            or type(value["chain_sha256"]) is not str
            or _HASH.fullmatch(value["chain_sha256"]) is None):
        raise LegacySnapshotGenerationConflict()
    return RollingHistoryReference(
        value["event_id"],
        value["session_id"],
        value["request_id"],
        value["sequence"],
        value["version"],
        value["content_sha256"],
        value["chain_sha256"],
    )


def _read_legacy_append_retry_in_transaction(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    history_prefix: str,
    mapping_prefix: str,
    progress_key: str,
    batch_receipt_key: str,
    rolling_idempotency_db_key: str,
    receipt_raw: str,
    idempotency_id: str,
    caller_key_sha256: str,
    batch_sha256: str,
    rolling_batch_sha256: str,
    encoded_event_payloads: tuple[str, ...],
    event_payloads: tuple[dict[str, object], ...],
    rows: tuple[_LegacyAppendSourceRow, ...],
    expected_source_message_revision: int,
    expected_cursor: int,
    expected_rolling_head_sequence: int,
    expected_rolling_head_chain_sha256: str,
    current_head: tuple[int, str],
) -> LegacySnapshotRollingAppendReceipt:
    expected_keys = {
        "schema_version", "kind", "scope", "migration_id", "incarnation_id",
        "request_id", "idempotency_id", "idempotency_key_sha256", "batch_sha256",
        "rolling_batch_sha256", "source_message_revision", "total_count",
        "start_source_sequence", "next_source_sequence", "source_chain_before",
        "source_chain_after", "previous_rolling_head_sequence",
        "previous_rolling_head_chain_sha256", "result_rolling_head_sequence",
        "result_rolling_head_chain_sha256", "mappings",
    }
    try:
        receipt = json.loads(receipt_raw)
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    if (type(receipt) is not dict
            or set(receipt) != expected_keys
            or type(receipt["schema_version"]) is not int
            or receipt["schema_version"] != 1
            or receipt["kind"] != "legacy_snapshot_append_receipt"
            or receipt["scope"] != _identity(scope)
            or receipt["migration_id"] != status.migration_id
            or receipt["incarnation_id"] != identity.incarnation_id
            or type(receipt["request_id"]) is not str
            or _MIGRATION_ID.fullmatch(receipt["request_id"]) is None
            or receipt["idempotency_id"] != idempotency_id
            or receipt["idempotency_key_sha256"] != caller_key_sha256
            or receipt["batch_sha256"] != batch_sha256
            or receipt["rolling_batch_sha256"] != rolling_batch_sha256
            or type(receipt["source_message_revision"]) is not int
            or receipt["source_message_revision"] != expected_source_message_revision
            or type(receipt["total_count"]) is not int
            or receipt["total_count"] != status.total_count
            or type(receipt["start_source_sequence"]) is not int
            or receipt["start_source_sequence"] != expected_cursor
            or type(receipt["next_source_sequence"]) is not int
            or receipt["next_source_sequence"] != expected_cursor + len(rows)
            or type(receipt["source_chain_before"]) is not str
            or receipt["source_chain_before"] != _snapshot_chain_at_cursor(
                handle, status.migration_id, expected_cursor,
            )
            or type(receipt["source_chain_after"]) is not str
            or receipt["source_chain_after"] != rows[-1].locator.chain_sha256
            or type(receipt["previous_rolling_head_sequence"]) is not int
            or receipt["previous_rolling_head_sequence"]
            != expected_rolling_head_sequence
            or type(receipt["previous_rolling_head_chain_sha256"]) is not str
            or receipt["previous_rolling_head_chain_sha256"]
            != expected_rolling_head_chain_sha256
            or type(receipt["result_rolling_head_sequence"]) is not int
            or type(receipt["result_rolling_head_chain_sha256"]) is not str
            or type(receipt["mappings"]) is not list
            or len(receipt["mappings"]) != len(rows)
            or _canonical(receipt) != receipt_raw):
        raise LegacySnapshotGenerationConflict()

    references: list[RollingHistoryReference] = []
    for item in receipt["mappings"]:
        if (type(item) is not dict
                or set(item) != {"source_locator", "classification", "event_reference"}):
            raise LegacySnapshotGenerationConflict()
        if item["event_reference"] is not None:
            references.append(
                _decode_legacy_rolling_reference(item["event_reference"])
            )
    reference_tuple = tuple(references)
    if len(reference_tuple) != len(event_payloads):
        raise LegacySnapshotGenerationConflict()
    previous_head = (
        expected_rolling_head_sequence,
        expected_rolling_head_chain_sha256,
    )
    _verify_legacy_append_head_at_sequence(
        handle, scope, history_prefix, previous_head,
    )
    for offset, reference in enumerate(reference_tuple, start=1):
        if reference.sequence != expected_rolling_head_sequence + offset:
            raise LegacySnapshotGenerationConflict()
    references_by_source = _legacy_append_references_by_source(
        rows, reference_tuple,
    )
    expected_mappings = [
        _legacy_append_mapping_receipt_value(
            row.locator,
            row.classification,
            references_by_source[row.locator.sequence],
        )
        for row in rows
    ]
    if _canonical(receipt["mappings"]) != _canonical(expected_mappings):
        raise LegacySnapshotGenerationConflict()

    result_head = (
        receipt["result_rolling_head_sequence"],
        receipt["result_rolling_head_chain_sha256"],
    )
    expected_result_sequence = expected_rolling_head_sequence + len(reference_tuple)
    expected_result_chain = (
        expected_rolling_head_chain_sha256
        if not reference_tuple
        else reference_tuple[-1].chain_sha256
    )
    if result_head != (expected_result_sequence, expected_result_chain):
        raise LegacySnapshotGenerationConflict()
    if not reference_tuple and result_head != previous_head:
        raise LegacySnapshotGenerationConflict()
    if current_head[0] < result_head[0]:
        raise LegacySnapshotGenerationConflict()
    _verify_legacy_append_head_at_sequence(
        handle, scope, history_prefix, result_head,
    )

    if reference_tuple:
        idempotency_raw = _owner_history_read_setting(
            handle, rolling_idempotency_db_key,
        )
        try:
            idempotency_value = json.loads(idempotency_raw)
        except Exception:
            raise LegacySnapshotGenerationConflict() from None
        expected_idempotency = {
            "schema_version": 1,
            "kind": "append_idempotency",
            "scope": _identity(scope),
            "idempotency_id": idempotency_id,
            "request_id": receipt["request_id"],
            "batch_sha256": rolling_batch_sha256,
            "first_sequence": reference_tuple[0].sequence,
            "event_count": len(reference_tuple),
        }
        if (type(idempotency_value) is not dict
                or idempotency_value != expected_idempotency
                or _canonical(expected_idempotency) != idempotency_raw):
            raise LegacySnapshotGenerationConflict()
        for offset, (reference, expected_payload, encoded_payload) in enumerate(
            zip(reference_tuple, event_payloads, encoded_event_payloads, strict=True),
            start=1,
        ):
            event = _owner_history_load_event_at_sequence(
                handle, scope, history_prefix, reference.sequence,
            )
            event_raw = _owner_history_read_setting(
                handle, _event_key(history_prefix, reference.sequence),
            )
            try:
                event_value = json.loads(event_raw)
            except Exception:
                raise LegacySnapshotGenerationConflict() from None
            expected_event_id = _sha256(f"{idempotency_id}\0{offset}")
            if (event is None
                    or event.reference != reference
                    or event.reference.event_id != expected_event_id
                    or event.reference.request_id != receipt["request_id"]
                    or event.skill_provenance is not None
                    or event.event_type != expected_payload["event_type"]
                    or event.content != expected_payload["content"]
                    or _canonical(event.model_payload()) != encoded_payload
                    or type(event_value) is not dict
                    or event_value.get("idempotency_id") != idempotency_id):
                raise LegacySnapshotGenerationConflict()
    elif _owner_history_read_setting(handle, rolling_idempotency_db_key) is not None:
        raise LegacySnapshotGenerationConflict()

    for row in rows:
        key = mapping_prefix + "source:" + f"{row.locator.sequence:032d}"
        expected_raw = _canonical(_legacy_append_source_mapping(
            scope,
            status.migration_id,
            row,
            references_by_source[row.locator.sequence],
        ))
        if _owner_history_read_setting(handle, key) != expected_raw:
            raise LegacySnapshotGenerationConflict()

    progress_raw = _owner_history_read_setting(handle, progress_key)
    if progress_raw is None:
        raise LegacySnapshotGenerationConflict()
    progress = _decode_legacy_append_progress(
        progress_raw, scope, identity, status, status.migration_id,
    )
    if (progress["next_source_sequence"] < receipt["next_source_sequence"]
            or _snapshot_chain_at_cursor(
                handle,
                status.migration_id,
                progress["next_source_sequence"],
            ) != progress["source_chain_sha256"]
            or _snapshot_chain_at_cursor(
                handle, status.migration_id, receipt["next_source_sequence"],
            ) != receipt["source_chain_after"]):
        raise LegacySnapshotGenerationConflict()
    _verify_legacy_append_progress_head(
        handle,
        scope,
        history_prefix,
        progress["rolling_head_sequence"],
        progress["rolling_head_chain_sha256"],
        current_head,
    )
    return _legacy_append_receipt_from_value(receipt)


def _verify_legacy_append_head_at_sequence(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    history_prefix: str,
    head: tuple[int, str],
) -> None:
    sequence, chain_sha256 = head
    if sequence == 0:
        if chain_sha256 != _genesis_chain(scope):
            raise LegacySnapshotGenerationConflict()
        return
    event = _owner_history_load_event_at_sequence(
        handle, scope, history_prefix, sequence,
    )
    if event is None or event.reference.chain_sha256 != chain_sha256:
        raise LegacySnapshotGenerationConflict()


def _read_legacy_snapshot_append_progress_in_transaction(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
) -> LegacySnapshotRollingAppendProgress:
    try:
        return _read_legacy_snapshot_append_progress_verified(
            handle, origin, migration_id,
        )
    except LegacySnapshotGenerationConflict:
        raise
    except Exception:
        raise LegacySnapshotGenerationConflict() from None


def _read_legacy_snapshot_append_progress_verified(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
) -> LegacySnapshotRollingAppendProgress:
    scope, identity = _legacy_append_scope_and_identity(origin)
    session_rows = _read_rows(
        handle,
        "SELECT branch_from_session_id, branch_from_message_id "
        "FROM sessions WHERE id = ?",
        (identity.session_id,),
        ("branch_from_session_id", "branch_from_message_id"),
    )
    if (len(session_rows) != 1
            or session_rows[0][0] is not None
            or session_rows[0][1] is not None):
        raise LegacySnapshotGenerationConflict()

    source_revision, source_count = _read_current_source(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None:
        raise LegacySnapshotGenerationConflict()
    status = _status_from_row(
        generation, identity, migration_id, source_revision, source_count,
    )
    if (not _is_sealed_generation(status)
            or status.source_message_revision != source_revision
            or status.total_count != source_count):
        raise LegacySnapshotGenerationConflict()

    relation = _relate_generation_to_source_in_transaction(handle, migration_id)
    if relation is not LegacySnapshotSourceRelation.EXACT_CURRENT:
        raise LegacySnapshotGenerationConflict()
    if _read_current_source(handle, identity) != (source_revision, source_count):
        raise LegacySnapshotGenerationConflict()

    history_prefix = _scope_prefix(scope)
    mapping_prefix = (
        history_prefix + _LEGACY_APPEND_NAMESPACE + migration_id + ":"
    )
    _marker_key, marker_raw = _read_legacy_append_start_marker(
        handle, scope, identity, migration_id,
    )
    namespace_rows = _read_settings_with_prefix(handle, mapping_prefix)
    progress_raw: str | None = None
    mapping_raw_by_sequence: dict[int, str] = {}
    receipt_raw_by_id: dict[str, str] = {}
    for key, raw in namespace_rows:
        if type(key) is not str or type(raw) is not str or not key.startswith(mapping_prefix):
            raise LegacySnapshotGenerationConflict()
        suffix = key[len(mapping_prefix):]
        if suffix == "progress":
            if progress_raw is not None:
                raise LegacySnapshotGenerationConflict()
            progress_raw = raw
        elif suffix.startswith("source:"):
            sequence_text = suffix[len("source:"):]
            if (len(sequence_text) != 32
                    or re.fullmatch(r"[0-9]{32}", sequence_text, re.ASCII) is None):
                raise LegacySnapshotGenerationConflict()
            sequence = int(sequence_text)
            if (f"{sequence:032d}" != sequence_text
                    or sequence in mapping_raw_by_sequence):
                raise LegacySnapshotGenerationConflict()
            mapping_raw_by_sequence[sequence] = raw
        elif suffix.startswith("batch:"):
            idempotency_id = suffix[len("batch:"):]
            if (_HASH.fullmatch(idempotency_id) is None
                    or idempotency_id in receipt_raw_by_id):
                raise LegacySnapshotGenerationConflict()
            receipt_raw_by_id[idempotency_id] = raw
        else:
            # Any unknown value in this namespace could be a damaged record.
            raise LegacySnapshotGenerationConflict()

    current_head = _owner_history_head(handle, scope, history_prefix)
    if progress_raw is None:
        if marker_raw is None:
            _require_no_legacy_append_evidence_under_other_scope(
                handle, scope, identity, migration_id,
            )
            if mapping_raw_by_sequence or receipt_raw_by_id:
                raise LegacySnapshotGenerationConflict()
            history_rows = _read_settings_with_prefix(handle, history_prefix)
            if (status.total_count != 0
                    or current_head != (0, _genesis_chain(scope))
                    or history_rows):
                # Without a marker a nonempty source is unknown, including
                # pre-marker archives; absence is not proof it never started.
                raise LegacySnapshotGenerationConflict()
            return LegacySnapshotRollingAppendProgress(
                state="not_started",
                migration_id=migration_id,
                source_message_revision=status.source_message_revision,
                total_count=status.total_count,
                next_source_sequence=0,
                source_chain_sha256=_ZERO_HASH,
                text_projection_count=0,
                archive_only_count=0,
                progress_rolling_head_sequence=0,
                progress_rolling_head_chain_sha256=_genesis_chain(scope),
                current_rolling_head_sequence=0,
                current_rolling_head_chain_sha256=_genesis_chain(scope),
                mappings=(),
            )

        marker = _decode_legacy_append_start_marker(
            marker_raw, scope, identity, status, migration_id,
        )
        if marker["next_source_sequence"] != 0 or mapping_raw_by_sequence or receipt_raw_by_id:
            raise LegacySnapshotGenerationConflict()
        initial_head = (
            marker["initial_rolling_head_sequence"],
            marker["initial_rolling_head_chain_sha256"],
        )
        initial_progress = _legacy_append_progress_value(
            scope=scope,
            identity=identity,
            migration_id=migration_id,
            status=status,
            next_source_sequence=0,
            source_chain_sha256=_ZERO_HASH,
            text_projection_count=0,
            archive_only_count=0,
            rolling_head=initial_head,
        )
        if marker["progress_sha256"] != _sha256(_canonical(initial_progress)):
            raise LegacySnapshotGenerationConflict()
        _verify_legacy_append_history_readback(
            handle,
            scope,
            history_prefix,
            current_head,
            {},
            {0: _genesis_chain(scope), initial_head[0]: initial_head[1]},
        )
        _verify_legacy_append_progress_head(
            handle,
            scope,
            history_prefix,
            initial_head[0],
            initial_head[1],
            current_head,
        )
        return LegacySnapshotRollingAppendProgress(
            state=("complete" if status.total_count == 0 else "started"),
            migration_id=migration_id,
            source_message_revision=status.source_message_revision,
            total_count=status.total_count,
            next_source_sequence=0,
            source_chain_sha256=_ZERO_HASH,
            text_projection_count=0,
            archive_only_count=0,
            progress_rolling_head_sequence=initial_head[0],
            progress_rolling_head_chain_sha256=initial_head[1],
            current_rolling_head_sequence=current_head[0],
            current_rolling_head_chain_sha256=current_head[1],
            mappings=(),
        )

    if marker_raw is None:
        raise LegacySnapshotGenerationConflict()
    marker = _decode_legacy_append_start_marker(
        marker_raw, scope, identity, status, migration_id,
    )
    progress = _decode_legacy_append_progress(
        progress_raw, scope, identity, status, migration_id,
    )
    _legacy_append_marker_matches_progress(marker, progress, progress_raw)
    cursor = progress["next_source_sequence"]
    if cursor == 0:
        if (mapping_raw_by_sequence or receipt_raw_by_id
                or _snapshot_chain_at_cursor(handle, migration_id, cursor) != _ZERO_HASH):
            raise LegacySnapshotGenerationConflict()
        initial_head = (
            marker["initial_rolling_head_sequence"],
            marker["initial_rolling_head_chain_sha256"],
        )
        _verify_legacy_append_history_readback(
            handle,
            scope,
            history_prefix,
            current_head,
            {},
            {0: _genesis_chain(scope), initial_head[0]: initial_head[1]},
        )
        _verify_legacy_append_progress_head(
            handle,
            scope,
            history_prefix,
            initial_head[0],
            initial_head[1],
            current_head,
        )
        return LegacySnapshotRollingAppendProgress(
            state=("complete" if status.total_count == 0 else "started"),
            migration_id=migration_id,
            source_message_revision=status.source_message_revision,
            total_count=status.total_count,
            next_source_sequence=0,
            source_chain_sha256=_ZERO_HASH,
            text_projection_count=0,
            archive_only_count=0,
            progress_rolling_head_sequence=initial_head[0],
            progress_rolling_head_chain_sha256=initial_head[1],
            current_rolling_head_sequence=current_head[0],
            current_rolling_head_chain_sha256=current_head[1],
            mappings=(),
        )
    if (_snapshot_chain_at_cursor(handle, migration_id, cursor)
            != progress["source_chain_sha256"]):
        raise LegacySnapshotGenerationConflict()

    source_rows = _read_legacy_append_source_rows(
        handle, migration_id, 0, cursor, status,
    )
    if (len(source_rows) != cursor
            or len(mapping_raw_by_sequence) != cursor
            or not receipt_raw_by_id):
        raise LegacySnapshotGenerationConflict()

    mappings: list[LegacySnapshotRollingAppendMapping] = []
    references_by_source: dict[int, RollingHistoryReference | None] = {}
    for sequence, row in enumerate(source_rows):
        raw = mapping_raw_by_sequence.get(sequence)
        if raw is None:
            raise LegacySnapshotGenerationConflict()
        mapping_value = _decode_legacy_append_mapping(raw)
        reference_value = mapping_value["event_reference"]
        reference = (
            None
            if reference_value is None
            else _decode_legacy_rolling_reference(reference_value)
        )
        if _canonical(_legacy_append_source_mapping(
                scope, migration_id, row, reference,
        )) != raw:
            raise LegacySnapshotGenerationConflict()
        references_by_source[sequence] = reference
        mappings.append(LegacySnapshotRollingAppendMapping(
            source_locator=row.locator,
            classification=row.classification,
            event_reference=reference,
        ))
    if any(sequence >= cursor for sequence in mapping_raw_by_sequence):
        raise LegacySnapshotGenerationConflict()

    receipts = [
        _decode_legacy_append_receipt_for_progress(
            raw,
            receipt_id,
            scope,
            identity,
            status,
            migration_id,
        )
        for receipt_id, raw in receipt_raw_by_id.items()
    ]
    receipts.sort(key=lambda value: value["start_source_sequence"])
    checkpoints = {0: _genesis_chain(scope)}
    initial_head = (
        marker["initial_rolling_head_sequence"],
        marker["initial_rolling_head_chain_sha256"],
    )
    _record_legacy_progress_checkpoint(checkpoints, initial_head)
    expected_events: dict[int, tuple[RollingHistoryReference, str, str]] = {}
    source_cursor = 0
    source_chain = _ZERO_HASH
    previous_result_head = initial_head
    text_count = 0
    archive_only_count = 0
    for receipt in receipts:
        start = receipt["start_source_sequence"]
        next_sequence = receipt["next_source_sequence"]
        previous_head = (
            receipt["previous_rolling_head_sequence"],
            receipt["previous_rolling_head_chain_sha256"],
        )
        result_head = (
            receipt["result_rolling_head_sequence"],
            receipt["result_rolling_head_chain_sha256"],
        )
        if (start != source_cursor
                or receipt["source_chain_before"] != source_chain
                or previous_head[0] < previous_result_head[0]
                or (previous_head[0] == previous_result_head[0]
                    and previous_head[1] != previous_result_head[1])):
            raise LegacySnapshotGenerationConflict()

        batch_rows = source_rows[start:next_sequence]
        if len(batch_rows) != next_sequence - start:
            raise LegacySnapshotGenerationConflict()
        encoded_payloads = tuple(
            row.encoded_event_payload
            for row in batch_rows
            if row.encoded_event_payload is not None
        )
        event_payloads = tuple(
            row.event_payload
            for row in batch_rows
            if row.event_payload is not None
        )
        if (len(encoded_payloads) != len(event_payloads)
                or any(type(payload) is not str for payload in encoded_payloads)
                or any(type(payload) is not dict for payload in event_payloads)
                or receipt["rolling_batch_sha256"] != _batch_digest(encoded_payloads)):
            raise LegacySnapshotGenerationConflict()

        text_in_batch = 0
        archive_in_batch = 0
        receipt_mappings: list[dict[str, object]] = []
        event_offset = 0
        for row in batch_rows:
            reference = references_by_source[row.locator.sequence]
            receipt_mappings.append(_legacy_append_mapping_receipt_value(
                row.locator, row.classification, reference,
            ))
            if row.classification == "archive_only":
                archive_in_batch += 1
                if reference is not None:
                    raise LegacySnapshotGenerationConflict()
                continue
            if row.classification != "text_projection_available" or reference is None:
                raise LegacySnapshotGenerationConflict()
            event_offset += 1
            text_in_batch += 1
            if (
                reference.session_id != scope.session_id
                or reference.request_id != receipt["request_id"]
                or reference.sequence != previous_head[0] + event_offset
                or reference.event_id != _sha256(
                    f"{receipt['idempotency_id']}\0{event_offset}"
                )
                or reference.sequence in expected_events
                or row.encoded_event_payload is None
            ):
                raise LegacySnapshotGenerationConflict()
            expected_events[reference.sequence] = (
                reference,
                row.encoded_event_payload,
                receipt["idempotency_id"],
            )

        expected_result_chain = (
            previous_head[1]
            if text_in_batch == 0
            else expected_events[previous_head[0] + text_in_batch][0].chain_sha256
        )
        if (_canonical(receipt["mappings"]) != _canonical(receipt_mappings)
                or receipt["source_chain_after"]
                != batch_rows[-1].locator.chain_sha256
                or result_head[0] != previous_head[0] + text_in_batch
                or result_head[1] != expected_result_chain):
            raise LegacySnapshotGenerationConflict()

        batch_digest_valid = False
        for batch_size in range(1, _MAX_LEGACY_APPEND_BATCH_SIZE + 1):
            if min(batch_size, status.total_count - start) != len(batch_rows):
                continue
            candidate = _legacy_append_batch_sha256(
                migration_id=migration_id,
                scope=scope,
                expected_source_message_revision=status.source_message_revision,
                expected_cursor=start,
                expected_rolling_head_sequence=previous_head[0],
                expected_rolling_head_chain_sha256=previous_head[1],
                batch_size=batch_size,
                rows=batch_rows,
                encoded_event_payloads=encoded_payloads,
            )
            if candidate == receipt["batch_sha256"]:
                batch_digest_valid = True
                break
        if not batch_digest_valid:
            raise LegacySnapshotGenerationConflict()

        _verify_legacy_append_progress_idempotency(
            handle,
            history_prefix,
            scope,
            receipt,
            text_in_batch,
        )
        _record_legacy_progress_checkpoint(checkpoints, previous_head)
        _record_legacy_progress_checkpoint(checkpoints, result_head)
        source_cursor = next_sequence
        source_chain = receipt["source_chain_after"]
        previous_result_head = result_head
        text_count += text_in_batch
        archive_only_count += archive_in_batch

    if receipts[-1]["batch_sha256"] != marker["last_batch_sha256"]:
        raise LegacySnapshotGenerationConflict()

    progress_head = (
        progress["rolling_head_sequence"],
        progress["rolling_head_chain_sha256"],
    )
    _record_legacy_progress_checkpoint(checkpoints, progress_head)
    if (source_cursor != cursor
            or source_chain != progress["source_chain_sha256"]
            or text_count != progress["text_projection_count"]
            or archive_only_count != progress["archive_only_count"]
            or text_count + archive_only_count != cursor
            or previous_result_head != progress_head
            or current_head[0] < progress_head[0]):
        raise LegacySnapshotGenerationConflict()

    _verify_legacy_append_history_readback(
        handle,
        scope,
        history_prefix,
        current_head,
        expected_events,
        checkpoints,
    )
    return LegacySnapshotRollingAppendProgress(
        state=("complete" if cursor == status.total_count else "verified"),
        migration_id=migration_id,
        source_message_revision=status.source_message_revision,
        total_count=status.total_count,
        next_source_sequence=cursor,
        source_chain_sha256=source_chain,
        text_projection_count=text_count,
        archive_only_count=archive_only_count,
        progress_rolling_head_sequence=progress_head[0],
        progress_rolling_head_chain_sha256=progress_head[1],
        current_rolling_head_sequence=current_head[0],
        current_rolling_head_chain_sha256=current_head[1],
        mappings=tuple(mappings),
    )


def _read_settings_with_prefix(
    handle: RollingHistorySqlHandle,
    prefix: str,
) -> tuple[tuple[object, ...], ...]:
    return _read_rows(
        handle,
        "SELECT key, value FROM settings "
        "WHERE substr(key, 1, ?) = ? ORDER BY key COLLATE BINARY ASC",
        (len(prefix), prefix),
        ("key", "value"),
    )


def _decode_legacy_append_mapping(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    expected_keys = {
        "schema_version", "kind", "scope", "migration_id", "source_locator",
        "classification", "canonical_envelope_json", "event_reference",
    }
    if (type(value) is not dict
            or set(value) != expected_keys
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["kind"] != "legacy_source_event_mapping"
            or type(value["source_locator"]) is not dict
            or type(value["classification"]) is not str
            or type(value["canonical_envelope_json"]) is not str
            or (value["event_reference"] is not None
                and type(value["event_reference"]) is not dict)
            or _canonical(value) != raw):
        raise LegacySnapshotGenerationConflict()
    return value


def _decode_legacy_append_receipt_for_progress(
    raw: str,
    key_idempotency_id: str,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    migration_id: str,
) -> dict[str, object]:
    expected_keys = {
        "schema_version", "kind", "scope", "migration_id", "incarnation_id",
        "request_id", "idempotency_id", "idempotency_key_sha256", "batch_sha256",
        "rolling_batch_sha256", "source_message_revision", "total_count",
        "start_source_sequence", "next_source_sequence", "source_chain_before",
        "source_chain_after", "previous_rolling_head_sequence",
        "previous_rolling_head_chain_sha256", "result_rolling_head_sequence",
        "result_rolling_head_chain_sha256", "mappings",
    }
    try:
        value = json.loads(raw)
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    if (type(value) is not dict
            or set(value) != expected_keys
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["kind"] != "legacy_snapshot_append_receipt"
            or value["scope"] != _identity(scope)
            or value["migration_id"] != migration_id
            or value["incarnation_id"] != identity.incarnation_id
            or type(value["request_id"]) is not str
            or _MIGRATION_ID.fullmatch(value["request_id"]) is None
            or value["idempotency_id"] != key_idempotency_id
            or type(value["idempotency_key_sha256"]) is not str
            or _HASH.fullmatch(value["idempotency_key_sha256"]) is None
            or type(value["batch_sha256"]) is not str
            or _HASH.fullmatch(value["batch_sha256"]) is None
            or type(value["rolling_batch_sha256"]) is not str
            or _HASH.fullmatch(value["rolling_batch_sha256"]) is None
            or type(value["source_message_revision"]) is not int
            or value["source_message_revision"] != status.source_message_revision
            or type(value["total_count"]) is not int
            or value["total_count"] != status.total_count
            or type(value["start_source_sequence"]) is not int
            or value["start_source_sequence"] < 0
            or type(value["next_source_sequence"]) is not int
            or not value["start_source_sequence"] < value["next_source_sequence"]
            or value["next_source_sequence"] > status.total_count
            or value["next_source_sequence"] - value["start_source_sequence"]
            > _MAX_LEGACY_APPEND_BATCH_SIZE
            or type(value["source_chain_before"]) is not str
            or _HASH.fullmatch(value["source_chain_before"]) is None
            or type(value["source_chain_after"]) is not str
            or _HASH.fullmatch(value["source_chain_after"]) is None
            or type(value["previous_rolling_head_sequence"]) is not int
            or value["previous_rolling_head_sequence"] < 0
            or type(value["previous_rolling_head_chain_sha256"]) is not str
            or _HASH.fullmatch(value["previous_rolling_head_chain_sha256"]) is None
            or type(value["result_rolling_head_sequence"]) is not int
            or value["result_rolling_head_sequence"]
            < value["previous_rolling_head_sequence"]
            or type(value["result_rolling_head_chain_sha256"]) is not str
            or _HASH.fullmatch(value["result_rolling_head_chain_sha256"]) is None
            or type(value["mappings"]) is not list
            or len(value["mappings"])
            != value["next_source_sequence"] - value["start_source_sequence"]
            or _canonical(value) != raw):
        raise LegacySnapshotGenerationConflict()
    expected_id = _append_idempotency_id(
        scope,
        f"legacy-snapshot:{migration_id}:{value['idempotency_key_sha256']}",
    )
    if expected_id != key_idempotency_id:
        raise LegacySnapshotGenerationConflict()
    return value


def _record_legacy_progress_checkpoint(
    checkpoints: dict[int, str],
    checkpoint: tuple[int, str],
) -> None:
    sequence, chain_sha256 = checkpoint
    prior = checkpoints.get(sequence)
    if prior is not None and prior != chain_sha256:
        raise LegacySnapshotGenerationConflict()
    checkpoints[sequence] = chain_sha256


def _verify_legacy_append_progress_idempotency(
    handle: RollingHistorySqlHandle,
    history_prefix: str,
    scope: ContextEventScope,
    receipt: dict[str, object],
    event_count: int,
) -> None:
    key = history_prefix + "idempotency:" + receipt["idempotency_id"]
    raw = _owner_history_read_setting(handle, key)
    if event_count == 0:
        if raw is not None:
            raise LegacySnapshotGenerationConflict()
        return
    if raw is None:
        raise LegacySnapshotGenerationConflict()
    expected = {
        "schema_version": 1,
        "kind": "append_idempotency",
        "scope": _identity(scope),
        "idempotency_id": receipt["idempotency_id"],
        "request_id": receipt["request_id"],
        "batch_sha256": receipt["rolling_batch_sha256"],
        "first_sequence": receipt["previous_rolling_head_sequence"] + 1,
        "event_count": event_count,
    }
    if raw != _canonical(expected):
        raise LegacySnapshotGenerationConflict()


def _verify_legacy_append_history_readback(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    history_prefix: str,
    current_head: tuple[int, str],
    expected_events: dict[int, tuple[RollingHistoryReference, str, str]],
    checkpoints: dict[int, str],
) -> None:
    genesis = _genesis_chain(scope)
    if checkpoints.get(0) != genesis:
        raise LegacySnapshotGenerationConflict()
    event_prefix = history_prefix + "event:"
    event_rows = _read_settings_with_prefix(handle, event_prefix)
    if len(event_rows) != current_head[0]:
        raise LegacySnapshotGenerationConflict()

    observed_chain = genesis
    event_id_keys: list[str] = []
    matched_legacy_sequences: set[int] = set()
    for sequence, (key, raw) in enumerate(event_rows, start=1):
        if (key != _event_key(history_prefix, sequence)
                or type(raw) is not str):
            raise LegacySnapshotGenerationConflict()
        try:
            event = _owner_history_load_event_at_sequence(
                handle, scope, history_prefix, sequence, verify_parent=False,
            )
            event_value = json.loads(raw)
        except Exception:
            raise LegacySnapshotGenerationConflict() from None
        if (event is None
                or type(event_value) is not dict
                or event_value.get("previous_chain_sha256") != observed_chain):
            raise LegacySnapshotGenerationConflict()
        observed_chain = event.reference.chain_sha256
        event_id_keys.append(_event_id_key(history_prefix, event.reference.event_id))
        checkpoint_chain = checkpoints.get(sequence)
        if checkpoint_chain is not None and checkpoint_chain != observed_chain:
            raise LegacySnapshotGenerationConflict()

        expected = expected_events.get(sequence)
        if expected is not None:
            reference, encoded_payload, idempotency_id = expected
            if (event.reference != reference
                    or event.skill_provenance is not None
                    or _canonical(event.model_payload()) != encoded_payload
                    or event_value.get("idempotency_id") != idempotency_id):
                raise LegacySnapshotGenerationConflict()
            matched_legacy_sequences.add(sequence)

    if (observed_chain != current_head[1]
            or matched_legacy_sequences != set(expected_events)):
        raise LegacySnapshotGenerationConflict()

    id_prefix = history_prefix + "event-id:"
    id_rows = _read_settings_with_prefix(handle, id_prefix)
    expected_id_keys = sorted(event_id_keys)
    actual_id_keys = []
    for key, raw in id_rows:
        if type(key) is not str or type(raw) is not str:
            raise LegacySnapshotGenerationConflict()
        actual_id_keys.append(key)
    if sorted(actual_id_keys) != expected_id_keys:
        raise LegacySnapshotGenerationConflict()


def read_message_source_fence_in_transaction(
    handle: RollingHistorySqlHandle,
    session_id: str,
) -> LegacyMessageSourceFence:
    """Read one owner's exact live fence using the caller's transaction handle.

    Callers such as the rolling V2 root-map operation must invoke this from
    their existing owner-operation callback and pass that callback's handle;
    they must pass the session ID bound to that owner operation. This accessor
    never opens a nested connection or transaction.
    """
    identity = get_rolling_history_owner_identity()
    if type(session_id) is not str or session_id != identity.session_id:
        raise LegacySnapshotGenerationConflict()
    try:
        rows = _read_rows(
            handle,
            "SELECT generation AS message_source_generation, message_count, "
            "non_append_generation "
            "FROM message_source_generations "
            "WHERE session_id = ?",
            (session_id,),
            (
                "message_source_generation",
                "message_count",
                "non_append_generation",
            ),
        )
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    if (
        len(rows) != 1
        or any(type(value) is not int or value < 0 for value in rows[0])
    ):
        raise LegacySnapshotGenerationConflict()
    return LegacyMessageSourceFence(
        message_source_generation=rows[0][0],
        message_count=rows[0][1],
        non_append_generation=rows[0][2],
    )


def _read_message_source_generation(
    handle: RollingHistorySqlHandle,
    session_id: str,
) -> tuple[int, int]:
    """Compatibility reader for callers that bind the original two-field fence."""
    fence = _read_message_source_fence(handle, session_id)
    return fence.message_source_generation, fence.message_count


def _read_message_source_fence(
    handle: RollingHistorySqlHandle,
    session_id: str,
) -> LegacyMessageSourceFence:
    identity = get_rolling_history_owner_identity()
    if session_id != identity.session_id:
        raise LegacySnapshotGenerationConflict()
    return read_message_source_fence_in_transaction(handle, session_id)


def _refresh_message_source_order_fence(
    handle: RollingHistorySqlHandle,
    identity: RollingHistoryOwnerIdentity,
    expected_fence: LegacyMessageSourceFence,
) -> None:
    """Rebuild the per-session order maximum during one-time reconciliation."""
    if read_message_source_fence_in_transaction(
        handle, identity.session_id,
    ) != expected_fence:
        raise LegacySnapshotGenerationConflict()
    _live_revision, live_count = _read_current_source(handle, identity)
    if live_count != expected_fence.message_count:
        raise LegacySnapshotGenerationConflict()

    tail_rows = _read_rows(
        handle,
        "SELECT created_at, id FROM messages WHERE session_id = ? "
        "ORDER BY (created_at IS NOT NULL) DESC, created_at DESC, "
        "id COLLATE BINARY DESC LIMIT 1",
        (identity.session_id,),
        ("created_at", "id"),
    )
    if len(tail_rows) > 1:
        raise LegacySnapshotGenerationConflict()
    if tail_rows:
        tail_created_at, tail_message_id = tail_rows[0]
        if type(tail_message_id) is not str:
            raise LegacySnapshotGenerationConflict()
    else:
        if live_count != 0:
            raise LegacySnapshotGenerationConflict()
        tail_created_at = None
        tail_message_id = None

    dirty_rows = _read_rows(
        handle,
        "SELECT tail_order_dirty FROM message_source_generations "
        "WHERE session_id = ?",
        (identity.session_id,),
        ("tail_order_dirty",),
    )
    if (
        len(dirty_rows) != 1
        or type(dirty_rows[0][0]) is not int
        or dirty_rows[0][0] not in (0, 1)
    ):
        raise LegacySnapshotGenerationConflict()
    dirty_before = dirty_rows[0][0]
    updated = handle.execute(
        "UPDATE message_source_generations SET tail_order_created_at = ?, "
        "tail_order_message_id = ?, tail_order_dirty = 0 "
        "WHERE session_id = ? AND generation = ? AND message_count = ? "
        "AND non_append_generation = ? AND tail_order_dirty = ?",
        (
            tail_created_at,
            tail_message_id,
            identity.session_id,
            expected_fence.message_source_generation,
            expected_fence.message_count,
            expected_fence.non_append_generation,
            dirty_before,
        ),
    )
    if type(updated) is not int or updated != 1:
        raise LegacySnapshotGenerationConflict()
    observed_tail = _read_rows(
        handle,
        "SELECT tail_order_created_at, tail_order_message_id, tail_order_dirty "
        "FROM message_source_generations WHERE session_id = ?",
        (identity.session_id,),
        ("tail_order_created_at", "tail_order_message_id", "tail_order_dirty"),
    )
    if (
        read_message_source_fence_in_transaction(
            handle, identity.session_id,
        ) != expected_fence
        or len(observed_tail) != 1
        or observed_tail[0]
        != (tail_created_at, tail_message_id, 0)
    ):
        raise LegacySnapshotGenerationConflict()


def _read_current_source_revision(
    handle: RollingHistorySqlHandle,
    identity: RollingHistoryOwnerIdentity,
) -> int:
    """Read the O(1) session revision without counting the Message table."""
    rows = _read_rows(
        handle,
        "SELECT message_revision FROM sessions WHERE id = ?",
        (identity.session_id,),
        ("message_revision",),
    )
    if (len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] < 0):
        raise LegacySnapshotGenerationConflict()
    return rows[0][0]


def _require_legacy_root_session(
    handle: RollingHistorySqlHandle,
    identity: RollingHistoryOwnerIdentity,
) -> None:
    rows = _read_rows(
        handle,
        "SELECT branch_from_session_id, branch_from_message_id "
        "FROM sessions WHERE id = ?",
        (identity.session_id,),
        ("branch_from_session_id", "branch_from_message_id"),
    )
    if (len(rows) != 1 or rows[0][0] is not None or rows[0][1] is not None):
        raise LegacySnapshotGenerationConflict()


def _legacy_checkpoint_namespace(
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
) -> str:
    return (
        rolling_history_session_prefix(identity.session_id)
        + _LEGACY_APPEND_CHECKPOINT_NAMESPACE
        + identity.canonical_namespace_digest
        + ":"
        + migration_id
        + ":"
    )


def _legacy_checkpoint_root_sha256(
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    initial_rolling_head: tuple[int, str],
) -> str:
    root = {
        "schema_version": _LEGACY_APPEND_CHECKPOINT_ROOT_SCHEMA,
        "kind": "legacy_snapshot_append_checkpoint_root",
        "scope": _identity(scope),
        "owner_namespace_digest": identity.canonical_namespace_digest,
        "migration_id": status.migration_id,
        "source_message_revision": status.source_message_revision,
        "ordering_version": status.ordering_version,
        "total_count": status.total_count,
        "source_capture_chain_sha256": status.capture_chain_hash,
        "incarnation_id": identity.incarnation_id,
        "delete_epoch": identity.delete_epoch,
        "initial_rolling_head_sequence": initial_rolling_head[0],
        "initial_rolling_head_chain_sha256": initial_rolling_head[1],
    }
    return _sha256(_canonical(root))


def _legacy_checkpoint_root_sha256_v1(
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    source_generation: int,
    initial_rolling_head: tuple[int, str],
) -> str:
    root = {
        "schema_version": _LEGACY_APPEND_CHECKPOINT_SCHEMA_V1,
        "kind": "legacy_snapshot_append_checkpoint_root",
        "scope": _identity(scope),
        "owner_namespace_digest": identity.canonical_namespace_digest,
        "migration_id": status.migration_id,
        "source_message_revision": status.source_message_revision,
        "ordering_version": status.ordering_version,
        "total_count": status.total_count,
        "source_capture_chain_sha256": status.capture_chain_hash,
        "source_generation": source_generation,
        "incarnation_id": identity.incarnation_id,
        "delete_epoch": identity.delete_epoch,
        "initial_rolling_head_sequence": initial_rolling_head[0],
        "initial_rolling_head_chain_sha256": initial_rolling_head[1],
    }
    return _sha256(_canonical(root))


def _legacy_checkpoint_value(
    *,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    message_source_generation: int,
    message_count: int,
    non_append_generation: int,
    initial_rolling_head: tuple[int, str],
    next_source_sequence: int,
    source_chain_sha256: str,
    text_projection_count: int,
    archive_only_count: int,
    rolling_head: tuple[int, str],
    rolling_head_reference: dict[str, object] | None,
    last_batch_sha256: str | None,
    legacy_source_generation: int | None = None,
    legacy_checkpoint_root_sha256: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": _LEGACY_APPEND_CHECKPOINT_SCHEMA,
        "kind": "legacy_snapshot_append_checkpoint",
        "scope": _identity(scope),
        "owner_namespace_digest": identity.canonical_namespace_digest,
        "migration_id": status.migration_id,
        "source_message_revision": status.source_message_revision,
        "ordering_version": status.ordering_version,
        "total_count": status.total_count,
        "source_capture_chain_sha256": status.capture_chain_hash,
        "message_source_generation": message_source_generation,
        "message_count": message_count,
        "non_append_generation": non_append_generation,
        "legacy_source_generation": legacy_source_generation,
        "legacy_checkpoint_root_sha256": legacy_checkpoint_root_sha256,
        "incarnation_id": identity.incarnation_id,
        "delete_epoch": identity.delete_epoch,
        "initial_rolling_head_sequence": initial_rolling_head[0],
        "initial_rolling_head_chain_sha256": initial_rolling_head[1],
        "checkpoint_root_sha256": _legacy_checkpoint_root_sha256(
            scope, identity, status, initial_rolling_head,
        ),
        "next_source_sequence": next_source_sequence,
        "source_chain_sha256": source_chain_sha256,
        "text_projection_count": text_projection_count,
        "archive_only_count": archive_only_count,
        "rolling_head_sequence": rolling_head[0],
        "rolling_head_chain_sha256": rolling_head[1],
        "rolling_head_reference": rolling_head_reference,
        "last_batch_sha256": last_batch_sha256,
    }
    value["checkpoint_sha256"] = _sha256(_canonical(value))
    return value


def _decode_legacy_checkpoint(
    raw: str,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
) -> dict[str, object]:
    legacy_keys = {
        "schema_version", "kind", "scope", "owner_namespace_digest",
        "migration_id", "source_message_revision", "ordering_version",
        "total_count", "source_capture_chain_sha256", "source_generation",
        "incarnation_id", "delete_epoch", "initial_rolling_head_sequence",
        "initial_rolling_head_chain_sha256", "checkpoint_root_sha256",
        "next_source_sequence", "source_chain_sha256", "text_projection_count",
        "archive_only_count", "rolling_head_sequence",
        "rolling_head_chain_sha256", "rolling_head_reference",
        "last_batch_sha256", "checkpoint_sha256",
    }
    current_v2_keys = {
        "schema_version", "kind", "scope", "owner_namespace_digest",
        "migration_id", "source_message_revision", "ordering_version",
        "total_count", "source_capture_chain_sha256",
        "message_source_generation", "message_count", "legacy_source_generation",
        "legacy_checkpoint_root_sha256", "incarnation_id", "delete_epoch",
        "initial_rolling_head_sequence", "initial_rolling_head_chain_sha256",
        "checkpoint_root_sha256", "next_source_sequence", "source_chain_sha256",
        "text_projection_count", "archive_only_count", "rolling_head_sequence",
        "rolling_head_chain_sha256", "rolling_head_reference",
        "last_batch_sha256", "checkpoint_sha256",
    }
    current_keys = current_v2_keys | {"non_append_generation"}
    try:
        value = json.loads(raw)
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    if type(value) is not dict or set(value) not in (
        legacy_keys, current_v2_keys, current_keys,
    ):
        raise LegacySnapshotGenerationConflict()
    is_legacy = set(value) == legacy_keys
    is_v2 = set(value) == current_v2_keys
    is_current = set(value) == current_keys
    unsigned = {key: item for key, item in value.items() if key != "checkpoint_sha256"}
    cursor = value["next_source_sequence"]
    initial_sequence = value["initial_rolling_head_sequence"]
    head_sequence = value["rolling_head_sequence"]
    last_batch = value["last_batch_sha256"]
    head_reference_value = value["rolling_head_reference"]
    initial_head = (
        initial_sequence,
        value["initial_rolling_head_chain_sha256"],
    )
    legacy_source_generation = (
        value["source_generation"] if is_legacy
        else value["legacy_source_generation"]
    )
    legacy_checkpoint_root = (
        value["checkpoint_root_sha256"] if is_legacy
        else value["legacy_checkpoint_root_sha256"]
    )
    expected_root = (
        _legacy_checkpoint_root_sha256_v1(
            scope, identity, status, legacy_source_generation, initial_head,
        )
        if is_legacy
        else _legacy_checkpoint_root_sha256(scope, identity, status, initial_head)
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != (
            _LEGACY_APPEND_CHECKPOINT_SCHEMA_V1
            if is_legacy
            else 2
            if is_v2
            else _LEGACY_APPEND_CHECKPOINT_SCHEMA
        )
        or value["kind"] != "legacy_snapshot_append_checkpoint"
        or value["scope"] != _identity(scope)
        or value["owner_namespace_digest"] != identity.canonical_namespace_digest
        or value["migration_id"] != status.migration_id
        or type(value["source_message_revision"]) is not int
        or value["source_message_revision"] != status.source_message_revision
        or value["ordering_version"] != status.ordering_version
        or type(value["total_count"]) is not int
        or value["total_count"] != status.total_count
        or value["source_capture_chain_sha256"] != status.capture_chain_hash
        or (is_legacy and (
            type(legacy_source_generation) is not int
            or legacy_source_generation < 0
        ))
        or (not is_legacy and (
            type(value["message_source_generation"]) is not int
            or value["message_source_generation"] < 0
            or type(value["message_count"]) is not int
            or value["message_count"] < status.total_count
        ))
        or (is_current and (
            type(value["non_append_generation"]) is not int
            or value["non_append_generation"] < 0
        ))
        or value["incarnation_id"] != identity.incarnation_id
        or value["delete_epoch"] != identity.delete_epoch
        or type(initial_sequence) is not int
        or initial_sequence < 0
        or type(value["initial_rolling_head_chain_sha256"]) is not str
        or _HASH.fullmatch(value["initial_rolling_head_chain_sha256"]) is None
        or (initial_sequence == 0
            and value["initial_rolling_head_chain_sha256"] != _genesis_chain(scope))
        or type(value["checkpoint_root_sha256"]) is not str
        or value["checkpoint_root_sha256"] != expected_root
        or (not is_legacy and (
            (legacy_source_generation is None) != (legacy_checkpoint_root is None)
            or (legacy_source_generation is not None and (
                type(legacy_source_generation) is not int
                or legacy_source_generation < 0
            ))
            or (legacy_checkpoint_root is not None and (
                type(legacy_checkpoint_root) is not str
                or legacy_checkpoint_root != _legacy_checkpoint_root_sha256_v1(
                    scope, identity, status, legacy_source_generation, initial_head,
                )
            ))
        ))
        or type(cursor) is not int
        or not 0 <= cursor <= status.total_count
        or type(value["source_chain_sha256"]) is not str
        or _HASH.fullmatch(value["source_chain_sha256"]) is None
        or type(value["text_projection_count"]) is not int
        or value["text_projection_count"] < 0
        or type(value["archive_only_count"]) is not int
        or value["archive_only_count"] < 0
        or value["text_projection_count"] + value["archive_only_count"] != cursor
        or type(head_sequence) is not int
        or head_sequence < initial_sequence
        or type(value["rolling_head_chain_sha256"]) is not str
        or _HASH.fullmatch(value["rolling_head_chain_sha256"]) is None
        or (head_sequence == 0
            and value["rolling_head_chain_sha256"] != _genesis_chain(scope))
        or (head_sequence == 0 and head_reference_value is not None)
        or (head_sequence > 0 and type(head_reference_value) is not dict)
        or (head_sequence > 0 and (
            _decode_legacy_rolling_reference(head_reference_value).sequence
            != head_sequence
            or _decode_legacy_rolling_reference(head_reference_value).session_id
            != scope.session_id
            or _decode_legacy_rolling_reference(head_reference_value).chain_sha256
            != value["rolling_head_chain_sha256"]
        ))
        or (cursor == 0 and value["source_chain_sha256"] != _ZERO_HASH)
        or (cursor == 0 and head_sequence != initial_sequence)
        or (cursor == 0 and value["rolling_head_chain_sha256"]
            != value["initial_rolling_head_chain_sha256"])
        or (cursor == 0 and last_batch is not None)
        or (cursor > 0 and (type(last_batch) is not str
                            or _HASH.fullmatch(last_batch) is None))
        or type(value["checkpoint_sha256"]) is not str
        or value["checkpoint_sha256"] != _sha256(_canonical(unsigned))
        or _canonical(value) != raw
    ):
        raise LegacySnapshotGenerationConflict()
    if is_legacy or is_v2:
        upgraded = dict(value)
        if is_legacy:
            del upgraded["source_generation"]
            upgraded["message_source_generation"] = value["source_generation"]
            # A v1 checkpoint predates the persisted live message count.
            upgraded["message_count"] = status.total_count
            upgraded["legacy_source_generation"] = value["source_generation"]
            upgraded["legacy_checkpoint_root_sha256"] = value["checkpoint_root_sha256"]
            upgraded["checkpoint_root_sha256"] = _legacy_checkpoint_root_sha256(
                scope, identity, status, initial_head,
            )
        upgraded["non_append_generation"] = None
        upgraded["schema_version"] = _LEGACY_APPEND_CHECKPOINT_SCHEMA
        upgraded["checkpoint_sha256"] = _sha256(_canonical({
            key: item for key, item in upgraded.items()
            if key != "checkpoint_sha256"
        }))
        upgraded["_needs_upgrade"] = True
        return upgraded
    return value


def _legacy_exact_batch_receipt_key(
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    checkpoint_root_sha256: str,
    expected_cursor: int,
    batch_sha256: str,
) -> str:
    """Build a retry identity from sealed root and batch coordinates only."""
    exact_batch_identity = {
        "owner_namespace_digest": identity.canonical_namespace_digest,
        "migration_id": migration_id,
        "checkpoint_root_sha256": checkpoint_root_sha256,
        "expected_cursor": expected_cursor,
        "batch_sha256": batch_sha256,
    }
    return (
        _legacy_checkpoint_namespace(identity, migration_id)
        + "batch:"
        + _sha256(_canonical(exact_batch_identity))
    )


def _legacy_exact_batch_receipt_key_with_generation(
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    checkpoint_root_sha256: str,
    source_generation: int,
    expected_cursor: int,
    batch_sha256: str,
) -> str:
    """Rebuild the pre-v3 key only when reading and validating old receipts."""
    exact_batch_identity = {
        "owner_namespace_digest": identity.canonical_namespace_digest,
        "migration_id": migration_id,
        "checkpoint_root_sha256": checkpoint_root_sha256,
        "source_generation": source_generation,
        "expected_cursor": expected_cursor,
        "batch_sha256": batch_sha256,
    }
    return (
        _legacy_checkpoint_namespace(identity, migration_id)
        + "batch:"
        + _sha256(_canonical(exact_batch_identity))
    )


def _legacy_source_receipt_pointer_key(
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    source_sequence: int,
) -> str:
    return (
        _legacy_checkpoint_namespace(identity, migration_id)
        + "source-receipt:"
        + f"{source_sequence:032d}"
    )


def _legacy_exact_batch_receipt_value(
    *,
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    checkpoint_root_sha256: str,
    source_generation: int,
    expected_cursor: int,
    batch_sha256: str,
    legacy_receipt_key: str,
    legacy_receipt_raw: str,
    next_source_sequence: int,
    source_chain_after: str,
    previous_rolling_head: tuple[int, str],
    result_rolling_head: tuple[int, str],
) -> dict[str, object]:
    return {
        "schema_version": _LEGACY_APPEND_EXACT_RECEIPT_SCHEMA,
        "kind": "legacy_snapshot_append_exact_receipt",
        "owner_namespace_digest": identity.canonical_namespace_digest,
        "migration_id": migration_id,
        "checkpoint_root_sha256": checkpoint_root_sha256,
        "source_generation": source_generation,
        "expected_cursor": expected_cursor,
        "batch_sha256": batch_sha256,
        "legacy_receipt_key": legacy_receipt_key,
        "legacy_receipt_sha256": _sha256(legacy_receipt_raw),
        "next_source_sequence": next_source_sequence,
        "source_chain_after": source_chain_after,
        "previous_rolling_head_sequence": previous_rolling_head[0],
        "previous_rolling_head_chain_sha256": previous_rolling_head[1],
        "result_rolling_head_sequence": result_rolling_head[0],
        "result_rolling_head_chain_sha256": result_rolling_head[1],
    }


def _decode_legacy_exact_batch_receipt(
    raw: str,
    *,
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
    checkpoint_root_sha256: str,
    source_generation: int,
    expected_cursor: int,
    batch_sha256: str,
) -> dict[str, object]:
    expected_keys = {
        "schema_version", "kind", "owner_namespace_digest", "migration_id",
        "checkpoint_root_sha256", "source_generation", "expected_cursor",
        "batch_sha256", "legacy_receipt_key", "legacy_receipt_sha256",
        "next_source_sequence", "source_chain_after",
        "previous_rolling_head_sequence", "previous_rolling_head_chain_sha256",
        "result_rolling_head_sequence", "result_rolling_head_chain_sha256",
    }
    try:
        value = json.loads(raw)
    except Exception:
        raise LegacySnapshotGenerationConflict() from None
    if (
        type(value) is not dict
        or set(value) != expected_keys
        or type(value["schema_version"]) is not int
        or value["schema_version"] != _LEGACY_APPEND_EXACT_RECEIPT_SCHEMA
        or value["kind"] != "legacy_snapshot_append_exact_receipt"
        or value["owner_namespace_digest"] != identity.canonical_namespace_digest
        or value["migration_id"] != migration_id
        or value["checkpoint_root_sha256"] != checkpoint_root_sha256
        or type(value["source_generation"]) is not int
        or value["source_generation"] != source_generation
        or type(expected_cursor) is not int
        or expected_cursor < 0
        or type(batch_sha256) is not str
        or _HASH.fullmatch(batch_sha256) is None
        or type(value["expected_cursor"]) is not int
        or value["expected_cursor"] != expected_cursor
        or value["batch_sha256"] != batch_sha256
        or type(value["legacy_receipt_key"]) is not str
        or not value["legacy_receipt_key"].startswith(
            _scope_prefix(ContextEventScope(
                identity.session_id,
                identity.request_id,
                identity.principal_hash,
                identity.delete_epoch,
            )) + _LEGACY_APPEND_NAMESPACE + migration_id + ":batch:"
        )
        or type(value["legacy_receipt_sha256"]) is not str
        or _HASH.fullmatch(value["legacy_receipt_sha256"]) is None
        or type(value["next_source_sequence"]) is not int
        or not expected_cursor < value["next_source_sequence"]
        or value["next_source_sequence"] - expected_cursor
        > _MAX_LEGACY_APPEND_BATCH_SIZE
        or type(value["source_chain_after"]) is not str
        or _HASH.fullmatch(value["source_chain_after"]) is None
        or type(value["previous_rolling_head_sequence"]) is not int
        or value["previous_rolling_head_sequence"] < 0
        or type(value["previous_rolling_head_chain_sha256"]) is not str
        or _HASH.fullmatch(value["previous_rolling_head_chain_sha256"]) is None
        or type(value["result_rolling_head_sequence"]) is not int
        or value["result_rolling_head_sequence"]
        < value["previous_rolling_head_sequence"]
        or type(value["result_rolling_head_chain_sha256"]) is not str
        or _HASH.fullmatch(value["result_rolling_head_chain_sha256"]) is None
        or _canonical(value) != raw
    ):
        raise LegacySnapshotGenerationConflict()
    return value


def _write_legacy_checkpoint(
    handle: RollingHistorySqlHandle,
    key: str,
    previous_raw: str | None,
    value: dict[str, object],
) -> str:
    encoded = _canonical(value)
    if previous_raw is None:
        _owner_history_insert_setting(handle, key, encoded)
        return encoded
    updated = handle.execute(
        "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE key = ? AND value = ?",
        (encoded, key, previous_raw),
    )
    if type(updated) is not int or updated != 1:
        raise LegacySnapshotGenerationConflict()
    return encoded


def _legacy_checkpoint_state_matches_progress(
    checkpoint: dict[str, object],
    marker: dict[str, object],
    progress: dict[str, object],
    progress_raw: str,
) -> None:
    _legacy_append_marker_matches_progress(marker, progress, progress_raw)
    if (
        checkpoint["next_source_sequence"] != progress["next_source_sequence"]
        or checkpoint["source_chain_sha256"] != progress["source_chain_sha256"]
        or checkpoint["text_projection_count"] != progress["text_projection_count"]
        or checkpoint["archive_only_count"] != progress["archive_only_count"]
        or checkpoint["rolling_head_sequence"] != progress["rolling_head_sequence"]
        or checkpoint["rolling_head_chain_sha256"]
        != progress["rolling_head_chain_sha256"]
        or checkpoint["initial_rolling_head_sequence"]
        != marker["initial_rolling_head_sequence"]
        or checkpoint["initial_rolling_head_chain_sha256"]
        != marker["initial_rolling_head_chain_sha256"]
        or checkpoint["last_batch_sha256"] != marker["last_batch_sha256"]
    ):
        raise LegacySnapshotGenerationConflict()


def _reconcile_legacy_checkpoint_mappings(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    source_generation: int,
    checkpoint: dict[str, object],
    mapping_prefix: str,
) -> None:
    """One-time full reconciliation before upgrading a v1 checkpoint."""
    cursor = checkpoint["next_source_sequence"]
    for after_cursor in range(0, cursor, _MAX_ORIGINAL_PAGE_SIZE):
        _read_legacy_checkpoint_page_mappings(
            handle,
            scope,
            identity,
            status,
            source_generation,
            checkpoint,
            mapping_prefix,
            after_cursor,
            _MAX_ORIGINAL_PAGE_SIZE,
        )


def _rebind_legacy_checkpoint_fence(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    history_prefix: str,
    checkpoint_key: str,
    checkpoint_raw: str,
    checkpoint: dict[str, object],
    marker: dict[str, object],
    progress: dict[str, object],
    progress_raw: str,
    source_generation: int,
    message_count: int,
    non_append_generation: int,
) -> tuple[str, dict[str, object]]:
    previous_generation = checkpoint["message_source_generation"]
    previous_count = checkpoint["message_count"]
    previous_non_append_generation = checkpoint["non_append_generation"]
    if previous_non_append_generation is None:
        # Old checkpoint formats did not record ordering/update evidence. They
        # can be upgraded only when their original live fence is unchanged.
        raise LegacySnapshotGenerationConflict()
    if (source_generation, message_count, non_append_generation) == (
        previous_generation, previous_count, previous_non_append_generation,
    ):
        return checkpoint_raw, checkpoint

    generation_delta = source_generation - previous_generation
    count_delta = message_count - previous_count
    non_append_delta = non_append_generation - previous_non_append_generation
    if (
        generation_delta < 0
        or count_delta < 0
        or generation_delta != count_delta
        or non_append_delta != 0
    ):
        raise LegacySnapshotGenerationConflict()

    observed_head = _owner_history_head(handle, scope, history_prefix)
    if observed_head != (
        checkpoint["rolling_head_sequence"],
        checkpoint["rolling_head_chain_sha256"],
    ):
        raise LegacySnapshotGenerationConflict()
    _verify_legacy_append_progress_head(
        handle,
        scope,
        history_prefix,
        checkpoint["rolling_head_sequence"],
        checkpoint["rolling_head_chain_sha256"],
        observed_head,
    )
    next_checkpoint = _legacy_checkpoint_value(
        scope=scope,
        identity=identity,
        status=status,
        message_source_generation=source_generation,
        message_count=message_count,
        non_append_generation=non_append_generation,
        initial_rolling_head=(
            checkpoint["initial_rolling_head_sequence"],
            checkpoint["initial_rolling_head_chain_sha256"],
        ),
        next_source_sequence=checkpoint["next_source_sequence"],
        source_chain_sha256=checkpoint["source_chain_sha256"],
        text_projection_count=checkpoint["text_projection_count"],
        archive_only_count=checkpoint["archive_only_count"],
        rolling_head=(
            checkpoint["rolling_head_sequence"],
            checkpoint["rolling_head_chain_sha256"],
        ),
        rolling_head_reference=checkpoint["rolling_head_reference"],
        last_batch_sha256=checkpoint["last_batch_sha256"],
        legacy_source_generation=checkpoint["legacy_source_generation"],
        legacy_checkpoint_root_sha256=checkpoint["legacy_checkpoint_root_sha256"],
    )
    if next_checkpoint["checkpoint_root_sha256"] != checkpoint["checkpoint_root_sha256"]:
        raise LegacySnapshotGenerationConflict()
    next_raw = _write_legacy_checkpoint(
        handle, checkpoint_key, checkpoint_raw, next_checkpoint,
    )
    if _owner_history_read_setting(handle, checkpoint_key) != next_raw:
        raise LegacySnapshotGenerationConflict()
    if (
        _owner_history_head(handle, scope, history_prefix) != observed_head
        or _read_message_source_fence(handle, identity.session_id)
        != LegacyMessageSourceFence(
            source_generation, message_count, non_append_generation,
        )
    ):
        raise LegacySnapshotGenerationConflict()
    decoded = _decode_legacy_checkpoint(next_raw, scope, identity, status)
    _legacy_checkpoint_state_matches_progress(
        decoded, marker, progress, progress_raw,
    )
    return next_raw, decoded


def _status_for_checkpoint_operation(
    handle: RollingHistorySqlHandle,
    identity: RollingHistoryOwnerIdentity,
    migration_id: str,
) -> LegacySnapshotGenerationStatus:
    live_source_revision = _read_current_source_revision(handle, identity)
    generation = _read_generation(handle, migration_id)
    if generation is None or len(generation) != len(_GENERATION_COLUMNS):
        raise LegacySnapshotGenerationConflict()
    sealed_total_count = generation[7]
    if type(sealed_total_count) is not int or sealed_total_count < 0:
        raise LegacySnapshotGenerationConflict()
    status = _status_from_row(
        generation, identity, migration_id, live_source_revision, sealed_total_count,
    )
    if not _is_sealed_generation(status):
        raise LegacySnapshotGenerationConflict()
    return status


def _legacy_checkpoint_head_reference(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    history_prefix: str,
    head: tuple[int, str],
) -> dict[str, object] | None:
    sequence, chain_sha256 = head
    if sequence == 0:
        if chain_sha256 != _genesis_chain(scope):
            raise LegacySnapshotGenerationConflict()
        return None
    event = _owner_history_load_event_at_sequence(
        handle, scope, history_prefix, sequence,
    )
    if event is None or event.reference.chain_sha256 != chain_sha256:
        raise LegacySnapshotGenerationConflict()
    return _legacy_rolling_reference_value(event.reference)


def _upgrade_legacy_append_state_to_checkpoint(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    source_generation: int,
    message_count: int,
    non_append_generation: int,
    marker_key: str,
    marker_raw: str,
    mapping_prefix: str,
    progress_key: str,
    progress_raw: str | None,
    legacy_progress: LegacySnapshotRollingAppendProgress | None,
) -> tuple[dict[str, object], str, dict[str, object], str, dict[str, object], str]:
    """Create a durable checkpoint only after the legacy state is reconciled."""
    marker = _decode_legacy_append_start_marker(
        marker_raw, scope, identity, status, migration_id,
    )
    initial_head = (
        marker["initial_rolling_head_sequence"],
        marker["initial_rolling_head_chain_sha256"],
    )
    if progress_raw is None:
        if marker["next_source_sequence"] != 0:
            raise LegacySnapshotGenerationConflict()
        progress_value = _legacy_append_progress_value(
            scope=scope,
            identity=identity,
            migration_id=migration_id,
            status=status,
            next_source_sequence=0,
            source_chain_sha256=_ZERO_HASH,
            text_projection_count=0,
            archive_only_count=0,
            rolling_head=initial_head,
        )
        progress_raw = _canonical(progress_value)
        if marker["progress_sha256"] != _sha256(progress_raw):
            raise LegacySnapshotGenerationConflict()
        if _owner_history_read_setting(handle, progress_key) is not None:
            raise LegacySnapshotGenerationConflict()
        _owner_history_insert_setting(handle, progress_key, progress_raw)
    progress = _decode_legacy_append_progress(
        progress_raw, scope, identity, status, migration_id,
    )
    _legacy_append_marker_matches_progress(marker, progress, progress_raw)
    if legacy_progress is not None and (
        legacy_progress.next_source_sequence != progress["next_source_sequence"]
        or legacy_progress.source_chain_sha256 != progress["source_chain_sha256"]
        or legacy_progress.text_projection_count != progress["text_projection_count"]
        or legacy_progress.archive_only_count != progress["archive_only_count"]
        or legacy_progress.progress_rolling_head_sequence != progress["rolling_head_sequence"]
        or legacy_progress.progress_rolling_head_chain_sha256
        != progress["rolling_head_chain_sha256"]
        or len(legacy_progress.mappings) != progress["next_source_sequence"]
    ):
        raise LegacySnapshotGenerationConflict()

    checkpoint = _legacy_checkpoint_value(
        scope=scope,
        identity=identity,
        status=status,
        message_source_generation=source_generation,
        message_count=message_count,
        non_append_generation=non_append_generation,
        initial_rolling_head=initial_head,
        next_source_sequence=progress["next_source_sequence"],
        source_chain_sha256=progress["source_chain_sha256"],
        text_projection_count=progress["text_projection_count"],
        archive_only_count=progress["archive_only_count"],
        rolling_head=(
            progress["rolling_head_sequence"],
            progress["rolling_head_chain_sha256"],
        ),
        rolling_head_reference=_legacy_checkpoint_head_reference(
            handle,
            scope,
            _scope_prefix(scope),
            (
                progress["rolling_head_sequence"],
                progress["rolling_head_chain_sha256"],
            ),
        ),
        last_batch_sha256=marker["last_batch_sha256"],
    )
    root = checkpoint["checkpoint_root_sha256"]

    # Old receipts have already been fully reconciled by the caller. Install
    # exact, request-independent receipt keys in the same migration transaction
    # so a retry of a pre-checkpoint batch cannot replay its events.
    for key, raw in _read_settings_with_prefix(handle, mapping_prefix):
        if (type(key) is not str or type(raw) is not str
                or not key.startswith(mapping_prefix)):
            raise LegacySnapshotGenerationConflict()
        suffix = key[len(mapping_prefix):]
        if not suffix.startswith("batch:"):
            continue
        idempotency_id = suffix[len("batch:"):]
        if _HASH.fullmatch(idempotency_id) is None:
            raise LegacySnapshotGenerationConflict()
        receipt = _decode_legacy_append_receipt_for_progress(
            raw, idempotency_id, scope, identity, status, migration_id,
        )
        exact_key = _legacy_exact_batch_receipt_key(
            identity,
            migration_id,
            root,
            receipt["start_source_sequence"],
            receipt["batch_sha256"],
        )
        exact_value = _legacy_exact_batch_receipt_value(
            identity=identity,
            migration_id=migration_id,
            checkpoint_root_sha256=root,
            source_generation=source_generation,
            expected_cursor=receipt["start_source_sequence"],
            batch_sha256=receipt["batch_sha256"],
            legacy_receipt_key=key,
            legacy_receipt_raw=raw,
            next_source_sequence=receipt["next_source_sequence"],
            source_chain_after=receipt["source_chain_after"],
            previous_rolling_head=(
                receipt["previous_rolling_head_sequence"],
                receipt["previous_rolling_head_chain_sha256"],
            ),
            result_rolling_head=(
                receipt["result_rolling_head_sequence"],
                receipt["result_rolling_head_chain_sha256"],
            ),
        )
        if _owner_history_read_setting(handle, exact_key) is not None:
            raise LegacySnapshotGenerationConflict()
        _owner_history_insert_setting(handle, exact_key, _canonical(exact_value))
        if _owner_history_read_setting(handle, exact_key) != _canonical(exact_value):
            raise LegacySnapshotGenerationConflict()
        receipt_mappings = receipt["mappings"]
        start_sequence = receipt["start_source_sequence"]
        next_sequence = receipt["next_source_sequence"]
        if (type(receipt_mappings) is not list
                or len(receipt_mappings) != next_sequence - start_sequence):
            raise LegacySnapshotGenerationConflict()
        for offset, mapping in enumerate(receipt_mappings):
            if (type(mapping) is not dict
                    or type(mapping.get("source_locator")) is not dict
                    or mapping["source_locator"].get("sequence") != start_sequence + offset):
                raise LegacySnapshotGenerationConflict()
            pointer_key = _legacy_source_receipt_pointer_key(
                identity, migration_id, start_sequence + offset,
            )
            if _owner_history_read_setting(handle, pointer_key) is not None:
                raise LegacySnapshotGenerationConflict()
            _owner_history_insert_setting(handle, pointer_key, exact_key)
            if _owner_history_read_setting(handle, pointer_key) != exact_key:
                raise LegacySnapshotGenerationConflict()

    checkpoint_key = (
        _legacy_checkpoint_namespace(identity, migration_id) + "state"
    )
    if _owner_history_read_setting(handle, checkpoint_key) is not None:
        raise LegacySnapshotGenerationConflict()
    checkpoint_raw = _canonical(checkpoint)
    _owner_history_insert_setting(handle, checkpoint_key, checkpoint_raw)
    if _owner_history_read_setting(handle, checkpoint_key) != checkpoint_raw:
        raise LegacySnapshotGenerationConflict()
    decoded = _decode_legacy_checkpoint(
        checkpoint_raw, scope, identity, status,
    )
    _legacy_checkpoint_state_matches_progress(
        decoded, marker, progress, progress_raw,
    )
    return decoded, checkpoint_raw, marker, marker_raw, progress, progress_raw


def _load_or_upgrade_legacy_checkpoint(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
) -> tuple[
    ContextEventScope,
    RollingHistoryOwnerIdentity,
    LegacySnapshotGenerationStatus,
    int,
    str,
    str,
    str,
    str,
    dict[str, object],
    str,
    dict[str, object],
    str,
    str,
    str,
]:
    scope, identity = _legacy_append_scope_and_identity(origin)
    _require_legacy_root_session(handle, identity)
    source_fence = _read_message_source_fence(handle, identity.session_id)
    source_generation = source_fence.message_source_generation
    message_count = source_fence.message_count
    non_append_generation = source_fence.non_append_generation
    status = _status_for_checkpoint_operation(handle, identity, migration_id)
    history_prefix = _scope_prefix(scope)
    mapping_prefix = history_prefix + _LEGACY_APPEND_NAMESPACE + migration_id + ":"
    progress_key = mapping_prefix + "progress"
    checkpoint_key = _legacy_checkpoint_namespace(identity, migration_id) + "state"
    checkpoint_raw = _owner_history_read_setting(handle, checkpoint_key)

    if checkpoint_raw is not None:
        marker_key = (
            _legacy_append_start_marker_namespace(identity)
            + _scope_digest(scope)
            + ":"
            + migration_id
        )
        marker_raw = _owner_history_read_setting(handle, marker_key)
        checkpoint = _decode_legacy_checkpoint(
            checkpoint_raw, scope, identity, status,
        )
        observed_head_reference = _legacy_checkpoint_head_reference(
            handle,
            scope,
            history_prefix,
            (
                checkpoint["rolling_head_sequence"],
                checkpoint["rolling_head_chain_sha256"],
            ),
        )
        if observed_head_reference != checkpoint["rolling_head_reference"]:
            raise LegacySnapshotGenerationConflict()
        if marker_raw is None:
            raise LegacySnapshotGenerationConflict()
        marker = _decode_legacy_append_start_marker(
            marker_raw, scope, identity, status, migration_id,
        )
        progress_raw = _owner_history_read_setting(handle, progress_key)
        if progress_raw is None:
            raise LegacySnapshotGenerationConflict()
        progress = _decode_legacy_append_progress(
            progress_raw, scope, identity, status, migration_id,
        )
        _legacy_checkpoint_state_matches_progress(
            checkpoint, marker, progress, progress_raw,
        )
        if checkpoint.get("_needs_upgrade") is True:
            if (
                checkpoint["non_append_generation"] is not None
                or (checkpoint["message_source_generation"], checkpoint["message_count"])
                != (source_generation, message_count)
            ):
                # The old checkpoint has no mutation-kind/order baseline. A
                # changed fence cannot be rebound safely from count equality.
                raise LegacySnapshotGenerationConflict()
            _refresh_message_source_order_fence(
                handle,
                identity,
                LegacyMessageSourceFence(
                    source_generation, message_count, non_append_generation,
                ),
            )
            migration_head = _owner_history_head(handle, scope, history_prefix)
            _verify_legacy_append_progress_head(
                handle,
                scope,
                history_prefix,
                checkpoint["rolling_head_sequence"],
                checkpoint["rolling_head_chain_sha256"],
                migration_head,
            )
            _reconcile_legacy_checkpoint_mappings(
                handle,
                scope,
                identity,
                status,
                checkpoint["message_source_generation"],
                checkpoint,
                mapping_prefix,
            )
            upgraded_checkpoint = _legacy_checkpoint_value(
                scope=scope,
                identity=identity,
                status=status,
                message_source_generation=source_generation,
                message_count=message_count,
                non_append_generation=non_append_generation,
                initial_rolling_head=(
                    checkpoint["initial_rolling_head_sequence"],
                    checkpoint["initial_rolling_head_chain_sha256"],
                ),
                next_source_sequence=checkpoint["next_source_sequence"],
                source_chain_sha256=checkpoint["source_chain_sha256"],
                text_projection_count=checkpoint["text_projection_count"],
                archive_only_count=checkpoint["archive_only_count"],
                rolling_head=(
                    checkpoint["rolling_head_sequence"],
                    checkpoint["rolling_head_chain_sha256"],
                ),
                rolling_head_reference=checkpoint["rolling_head_reference"],
                last_batch_sha256=checkpoint["last_batch_sha256"],
                legacy_source_generation=checkpoint["legacy_source_generation"],
                legacy_checkpoint_root_sha256=checkpoint[
                    "legacy_checkpoint_root_sha256"
                ],
            )
            if upgraded_checkpoint["checkpoint_root_sha256"] != checkpoint[
                "checkpoint_root_sha256"
            ]:
                raise LegacySnapshotGenerationConflict()
            upgraded_raw = _write_legacy_checkpoint(
                handle, checkpoint_key, checkpoint_raw, upgraded_checkpoint,
            )
            if (
                _owner_history_read_setting(handle, checkpoint_key) != upgraded_raw
                or _owner_history_head(handle, scope, history_prefix) != migration_head
                or _read_message_source_fence(handle, identity.session_id)
                != LegacyMessageSourceFence(
                    source_generation, message_count, non_append_generation,
                )
            ):
                raise LegacySnapshotGenerationConflict()
            checkpoint_raw = upgraded_raw
            checkpoint = _decode_legacy_checkpoint(
                checkpoint_raw, scope, identity, status,
            )
            _legacy_checkpoint_state_matches_progress(
                checkpoint, marker, progress, progress_raw,
            )
        checkpoint_raw, checkpoint = _rebind_legacy_checkpoint_fence(
            handle,
            scope,
            identity,
            status,
            history_prefix,
            checkpoint_key,
            checkpoint_raw,
            checkpoint,
            marker,
            progress,
            progress_raw,
            source_generation,
            message_count,
            non_append_generation,
        )
        return (
            scope, identity, status, source_generation, history_prefix,
            mapping_prefix, checkpoint_key, checkpoint_raw, checkpoint,
            marker_key, marker, progress_key, progress_raw, marker_raw,
        )

    marker_key, marker_raw = _read_legacy_append_start_marker(
        handle, scope, identity, migration_id,
    )
    progress_raw = _owner_history_read_setting(handle, progress_key)
    checkpoint_namespace_rows = _read_settings_with_prefix(
        handle, _legacy_checkpoint_namespace(identity, migration_id),
    )
    if checkpoint_namespace_rows:
        # An orphan v2 receipt without its checkpoint is not adoptable.
        raise LegacySnapshotGenerationConflict()

    legacy_progress: LegacySnapshotRollingAppendProgress | None = None
    if marker_raw is None:
        # A new start gets the same full live-source and history reconciliation
        # as the old start path before an initial durable checkpoint is made.
        _start_legacy_append_marker_in_transaction(
            handle, origin, migration_id, status,
        )
        marker_key, marker_raw = _read_legacy_append_start_marker(
            handle, scope, identity, migration_id,
        )
        if marker_raw is None:
            raise LegacySnapshotGenerationConflict()
        progress_raw = _owner_history_read_setting(handle, progress_key)
    else:
        # This full verifier rechecks exact live-source relation and all legacy
        # mappings, receipts and rolling-chain witnesses in this same tx.
        legacy_progress = _read_legacy_snapshot_append_progress_verified(
            handle, origin, migration_id,
        )

    _refresh_message_source_order_fence(
        handle,
        identity,
        LegacyMessageSourceFence(
            source_generation, message_count, non_append_generation,
        ),
    )

    upgraded = _upgrade_legacy_append_state_to_checkpoint(
        handle,
        origin,
        migration_id,
        scope,
        identity,
        status,
        source_generation,
        message_count,
        non_append_generation,
        marker_key,
        marker_raw,
        mapping_prefix,
        progress_key,
        progress_raw,
        legacy_progress,
    )
    checkpoint, checkpoint_raw, marker, marker_raw, progress, progress_raw = upgraded
    return (
        scope, identity, status, source_generation, history_prefix,
        mapping_prefix, checkpoint_key, checkpoint_raw, checkpoint,
        marker_key, marker, progress_key, progress_raw, marker_raw,
    )


def _read_legacy_checkpoint_page_mappings(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    identity: RollingHistoryOwnerIdentity,
    status: LegacySnapshotGenerationStatus,
    source_generation: int,
    checkpoint: dict[str, object],
    mapping_prefix: str,
    after_cursor: int,
    page_size: int,
) -> tuple[LegacySnapshotRollingAppendMapping, ...]:
    verified_cursor = checkpoint["next_source_sequence"]
    if after_cursor > verified_cursor:
        raise LegacySnapshotGenerationConflict()
    count = min(page_size, verified_cursor - after_cursor)
    source_rows = _read_legacy_append_source_rows(
        handle, status.migration_id, after_cursor, count, status,
    )
    if len(source_rows) != count:
        raise LegacySnapshotGenerationConflict()
    checkpoint_prefix = _legacy_checkpoint_namespace(identity, status.migration_id)
    mappings: list[LegacySnapshotRollingAppendMapping] = []
    for row in source_rows:
        sequence = row.locator.sequence
        mapping_key = mapping_prefix + "source:" + f"{sequence:032d}"
        mapping_raw = _owner_history_read_setting(handle, mapping_key)
        if mapping_raw is None:
            raise LegacySnapshotGenerationConflict()
        mapping_value = _decode_legacy_append_mapping(mapping_raw)
        reference_value = mapping_value["event_reference"]
        reference = (
            None
            if reference_value is None
            else _decode_legacy_rolling_reference(reference_value)
        )
        expected_mapping = _legacy_append_source_mapping(
            scope, status.migration_id, row, reference,
        )
        if _canonical(expected_mapping) != mapping_raw:
            raise LegacySnapshotGenerationConflict()
        if row.classification == "archive_only":
            if reference is not None:
                raise LegacySnapshotGenerationConflict()
        else:
            if (row.classification != "text_projection_available"
                    or reference is None
                    or reference.sequence > checkpoint["rolling_head_sequence"]):
                raise LegacySnapshotGenerationConflict()
            event = _owner_history_load_event_at_sequence(
                handle, scope, _scope_prefix(scope), reference.sequence,
            )
            if (event is None or event.reference != reference
                    or event.skill_provenance is not None
                    or event.event_type != row.event_payload["event_type"]
                    or event.content != row.event_payload["content"]):
                raise LegacySnapshotGenerationConflict()

        pointer_key = _legacy_source_receipt_pointer_key(
            identity, status.migration_id, sequence,
        )
        exact_key = _owner_history_read_setting(handle, pointer_key)
        if (type(exact_key) is not str
                or not exact_key.startswith(checkpoint_prefix + "batch:")):
            raise LegacySnapshotGenerationConflict()
        exact_raw = _owner_history_read_setting(handle, exact_key)
        if exact_raw is None:
            raise LegacySnapshotGenerationConflict()
        try:
            untrusted_exact = json.loads(exact_raw)
        except Exception:
            raise LegacySnapshotGenerationConflict() from None
        if type(untrusted_exact) is not dict:
            raise LegacySnapshotGenerationConflict()
        receipt_root = untrusted_exact.get("checkpoint_root_sha256")
        receipt_generation = untrusted_exact.get("source_generation")
        allowed_roots = {checkpoint["checkpoint_root_sha256"]}
        if checkpoint["legacy_checkpoint_root_sha256"] is not None:
            allowed_roots.add(checkpoint["legacy_checkpoint_root_sha256"])
        if (
            type(receipt_root) is not str
            or _HASH.fullmatch(receipt_root) is None
            or receipt_root not in allowed_roots
            or type(receipt_generation) is not int
            or receipt_generation < 0
            or receipt_generation > source_generation
            or (
                receipt_root == checkpoint["legacy_checkpoint_root_sha256"]
                and receipt_generation != checkpoint["legacy_source_generation"]
            )
        ):
            raise LegacySnapshotGenerationConflict()
        exact = _decode_legacy_exact_batch_receipt(
            exact_raw,
            identity=identity,
            migration_id=status.migration_id,
            checkpoint_root_sha256=receipt_root,
            source_generation=receipt_generation,
            expected_cursor=untrusted_exact.get("expected_cursor"),
            batch_sha256=untrusted_exact.get("batch_sha256"),
        )
        stable_exact_key = _legacy_exact_batch_receipt_key(
            identity,
            status.migration_id,
            receipt_root,
            exact["expected_cursor"],
            exact["batch_sha256"],
        )
        legacy_exact_key = _legacy_exact_batch_receipt_key_with_generation(
            identity,
            status.migration_id,
            receipt_root,
            receipt_generation,
            exact["expected_cursor"],
            exact["batch_sha256"],
        )
        if (exact_key not in (stable_exact_key, legacy_exact_key)
                or not exact["expected_cursor"] <= sequence
                or sequence >= exact["next_source_sequence"]):
            raise LegacySnapshotGenerationConflict()
        legacy_receipt_key = exact["legacy_receipt_key"]
        if not legacy_receipt_key.startswith(mapping_prefix + "batch:"):
            raise LegacySnapshotGenerationConflict()
        legacy_receipt_id = legacy_receipt_key[len(mapping_prefix + "batch:"):]
        legacy_receipt_raw = _owner_history_read_setting(handle, legacy_receipt_key)
        if (legacy_receipt_raw is None
                or _sha256(legacy_receipt_raw) != exact["legacy_receipt_sha256"]):
            raise LegacySnapshotGenerationConflict()
        receipt = _decode_legacy_append_receipt_for_progress(
            legacy_receipt_raw,
            legacy_receipt_id,
            scope,
            identity,
            status,
            status.migration_id,
        )
        if (
            receipt["start_source_sequence"] != exact["expected_cursor"]
            or receipt["batch_sha256"] != exact["batch_sha256"]
            or receipt["next_source_sequence"] != exact["next_source_sequence"]
            or receipt["source_chain_after"] != exact["source_chain_after"]
            or receipt["previous_rolling_head_sequence"]
            != exact["previous_rolling_head_sequence"]
            or receipt["previous_rolling_head_chain_sha256"]
            != exact["previous_rolling_head_chain_sha256"]
            or receipt["result_rolling_head_sequence"]
            != exact["result_rolling_head_sequence"]
            or receipt["result_rolling_head_chain_sha256"]
            != exact["result_rolling_head_chain_sha256"]
        ):
            raise LegacySnapshotGenerationConflict()
        offset = sequence - receipt["start_source_sequence"]
        if (offset < 0 or offset >= len(receipt["mappings"])
                or _canonical(receipt["mappings"][offset])
                != _canonical(_legacy_append_mapping_receipt_value(
                    row.locator, row.classification, reference,
                ))):
            raise LegacySnapshotGenerationConflict()
        mappings.append(LegacySnapshotRollingAppendMapping(
            source_locator=row.locator,
            classification=row.classification,
            event_reference=reference,
        ))
    return tuple(mappings)


def _read_legacy_checkpoint_progress_page_in_transaction(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    after_cursor: int,
    page_size: int,
) -> LegacySnapshotRollingAppendProgressPage:
    (
        scope, identity, status, source_generation, history_prefix,
        mapping_prefix, _checkpoint_key, _checkpoint_raw, checkpoint,
        _marker_key, _marker, _progress_key, _progress_raw, _marker_raw,
    ) = _load_or_upgrade_legacy_checkpoint(handle, origin, migration_id)
    if _read_message_source_fence(handle, identity.session_id) != (
        LegacyMessageSourceFence(
            source_generation,
            checkpoint["message_count"],
            checkpoint["non_append_generation"],
        )
    ):
        raise LegacySnapshotGenerationConflict()
    mappings = _read_legacy_checkpoint_page_mappings(
        handle,
        scope,
        identity,
        status,
        source_generation,
        checkpoint,
        mapping_prefix,
        after_cursor,
        page_size,
    )
    current_head = _owner_history_head(handle, scope, history_prefix)
    _verify_legacy_append_progress_head(
        handle,
        scope,
        history_prefix,
        checkpoint["rolling_head_sequence"],
        checkpoint["rolling_head_chain_sha256"],
        current_head,
    )
    next_cursor = after_cursor + len(mappings)
    verified_cursor = checkpoint["next_source_sequence"]
    return LegacySnapshotRollingAppendProgressPage(
        state=("complete" if verified_cursor == status.total_count else "partial"),
        migration_id=migration_id,
        source_message_revision=status.source_message_revision,
        message_source_generation=source_generation,
        checkpoint_root_sha256=checkpoint["checkpoint_root_sha256"],
        total_count=status.total_count,
        verified_source_cursor=verified_cursor,
        next_cursor=next_cursor,
        has_more=verified_cursor < status.total_count,
        page_has_more=next_cursor < verified_cursor,
        source_chain_sha256=checkpoint["source_chain_sha256"],
        text_projection_count=checkpoint["text_projection_count"],
        archive_only_count=checkpoint["archive_only_count"],
        progress_rolling_head_sequence=checkpoint["rolling_head_sequence"],
        progress_rolling_head_chain_sha256=checkpoint["rolling_head_chain_sha256"],
        progress_rolling_head_reference=checkpoint["rolling_head_reference"],
        current_rolling_head_sequence=current_head[0],
        current_rolling_head_chain_sha256=current_head[1],
        mappings=mappings,
    )


def _append_legacy_checkpoint_batch_in_transaction(
    handle: RollingHistorySqlHandle,
    origin: ScopeOrigin,
    migration_id: str,
    *,
    expected_source_message_revision: int,
    expected_source_generation: int,
    expected_checkpoint_root_sha256: str,
    expected_cursor: int,
    expected_rolling_head_sequence: int,
    expected_rolling_head_chain_sha256: str,
    idempotency_key: str,
    batch_size: int,
) -> LegacySnapshotAppendOutcome:
    (
        scope, identity, status, source_generation, history_prefix,
        mapping_prefix, checkpoint_key, checkpoint_raw, checkpoint,
        marker_key, marker, progress_key, progress_raw, marker_raw,
    ) = _load_or_upgrade_legacy_checkpoint(handle, origin, migration_id)
    if (source_generation != expected_source_generation
            or status.source_message_revision != expected_source_message_revision
            or checkpoint["checkpoint_root_sha256"]
            != expected_checkpoint_root_sha256):
        raise LegacySnapshotGenerationConflict()
    if expected_cursor >= status.total_count:
        raise LegacySnapshotGenerationConflict()

    rows = _read_legacy_append_source_rows(
        handle,
        migration_id,
        expected_cursor,
        min(batch_size, status.total_count - expected_cursor),
        status,
    )
    if not rows:
        raise LegacySnapshotGenerationConflict()
    next_source_sequence = expected_cursor + len(rows)
    source_chain_before = _snapshot_chain_at_cursor(
        handle, migration_id, expected_cursor,
    )
    source_chain_after = rows[-1].locator.chain_sha256
    encoded_event_payloads = tuple(
        row.encoded_event_payload for row in rows
        if row.encoded_event_payload is not None
    )
    event_payloads = tuple(
        row.event_payload for row in rows if row.event_payload is not None
    )
    if (len(encoded_event_payloads) != len(event_payloads)
            or any(type(item) is not str for item in encoded_event_payloads)
            or any(type(item) is not dict for item in event_payloads)):
        raise LegacySnapshotGenerationConflict()
    rolling_batch_sha256 = _batch_digest(encoded_event_payloads)
    batch_sha256 = _legacy_append_batch_sha256(
        migration_id=migration_id,
        scope=scope,
        expected_source_message_revision=expected_source_message_revision,
        expected_cursor=expected_cursor,
        expected_rolling_head_sequence=expected_rolling_head_sequence,
        expected_rolling_head_chain_sha256=expected_rolling_head_chain_sha256,
        batch_size=batch_size,
        rows=rows,
        encoded_event_payloads=encoded_event_payloads,
    )
    checkpoint_root = checkpoint["checkpoint_root_sha256"]
    exact_receipt_key = _legacy_exact_batch_receipt_key(
        identity,
        migration_id,
        checkpoint_root,
        expected_cursor,
        batch_sha256,
    )
    current_head = _owner_history_head(handle, scope, history_prefix)
    exact_receipt_raw = _owner_history_read_setting(handle, exact_receipt_key)
    if exact_receipt_raw is None:
        pointer_key = _legacy_source_receipt_pointer_key(
            identity, migration_id, expected_cursor,
        )
        legacy_candidate_key = _owner_history_read_setting(handle, pointer_key)
        if legacy_candidate_key is not None:
            if (type(legacy_candidate_key) is not str
                    or not legacy_candidate_key.startswith(
                        _legacy_checkpoint_namespace(identity, migration_id) + "batch:"
                    )):
                raise LegacySnapshotGenerationConflict()
            legacy_candidate_raw = _owner_history_read_setting(
                handle, legacy_candidate_key,
            )
            if legacy_candidate_raw is None:
                raise LegacySnapshotGenerationConflict()
            try:
                legacy_candidate = json.loads(legacy_candidate_raw)
            except Exception:
                raise LegacySnapshotGenerationConflict() from None
            if type(legacy_candidate) is not dict:
                raise LegacySnapshotGenerationConflict()
            candidate_root = legacy_candidate.get("checkpoint_root_sha256")
            candidate_generation = legacy_candidate.get("source_generation")
            candidate_cursor = legacy_candidate.get("expected_cursor")
            candidate_batch_sha256 = legacy_candidate.get("batch_sha256")
            allowed_roots = {checkpoint_root}
            if checkpoint["legacy_checkpoint_root_sha256"] is not None:
                allowed_roots.add(checkpoint["legacy_checkpoint_root_sha256"])
            if (
                type(candidate_root) is not str
                or _HASH.fullmatch(candidate_root) is None
                or candidate_root not in allowed_roots
                or type(candidate_generation) is not int
                or candidate_generation < 0
                or candidate_generation > source_generation
                or (
                    candidate_root == checkpoint["legacy_checkpoint_root_sha256"]
                    and candidate_generation != checkpoint["legacy_source_generation"]
                )
                or type(candidate_cursor) is not int
                or candidate_cursor != expected_cursor
                or type(candidate_batch_sha256) is not str
                or _HASH.fullmatch(candidate_batch_sha256) is None
                or candidate_batch_sha256 != batch_sha256
                or legacy_candidate_key not in (
                    _legacy_exact_batch_receipt_key(
                        identity,
                        migration_id,
                        candidate_root,
                        expected_cursor,
                        batch_sha256,
                    ),
                    _legacy_exact_batch_receipt_key_with_generation(
                        identity,
                        migration_id,
                        candidate_root,
                        candidate_generation,
                        expected_cursor,
                        batch_sha256,
                    ),
                )
            ):
                raise LegacySnapshotGenerationConflict()
            exact_receipt_key = legacy_candidate_key
            exact_receipt_raw = legacy_candidate_raw

    if exact_receipt_raw is not None:
        try:
            untrusted_exact = json.loads(exact_receipt_raw)
        except Exception:
            raise LegacySnapshotGenerationConflict() from None
        if type(untrusted_exact) is not dict:
            raise LegacySnapshotGenerationConflict()
        receipt_root = untrusted_exact.get("checkpoint_root_sha256")
        receipt_generation = untrusted_exact.get("source_generation")
        allowed_roots = {checkpoint_root}
        if checkpoint["legacy_checkpoint_root_sha256"] is not None:
            allowed_roots.add(checkpoint["legacy_checkpoint_root_sha256"])
        if (
            type(receipt_root) is not str
            or _HASH.fullmatch(receipt_root) is None
            or receipt_root not in allowed_roots
            or type(receipt_generation) is not int
            or receipt_generation < 0
            or receipt_generation > source_generation
            or (
                receipt_root == checkpoint["legacy_checkpoint_root_sha256"]
                and receipt_generation != checkpoint["legacy_source_generation"]
            )
        ):
            raise LegacySnapshotGenerationConflict()
        exact_receipt = _decode_legacy_exact_batch_receipt(
            exact_receipt_raw,
            identity=identity,
            migration_id=migration_id,
            checkpoint_root_sha256=receipt_root,
            source_generation=receipt_generation,
            expected_cursor=expected_cursor,
            batch_sha256=batch_sha256,
        )
        stable_receipt_key = _legacy_exact_batch_receipt_key(
            identity,
            migration_id,
            receipt_root,
            expected_cursor,
            batch_sha256,
        )
        legacy_receipt_key = _legacy_exact_batch_receipt_key_with_generation(
            identity,
            migration_id,
            receipt_root,
            receipt_generation,
            expected_cursor,
            batch_sha256,
        )
        if (exact_receipt_key not in (stable_receipt_key, legacy_receipt_key)
                or checkpoint["next_source_sequence"]
                < exact_receipt["next_source_sequence"]
                or exact_receipt["source_chain_after"] != source_chain_after
                or exact_receipt["previous_rolling_head_sequence"]
                != expected_rolling_head_sequence
                or exact_receipt["previous_rolling_head_chain_sha256"]
                != expected_rolling_head_chain_sha256):
            raise LegacySnapshotGenerationConflict()
        legacy_receipt_key = exact_receipt["legacy_receipt_key"]
        if not legacy_receipt_key.startswith(mapping_prefix + "batch:"):
            raise LegacySnapshotGenerationConflict()
        legacy_receipt_id = legacy_receipt_key[len(mapping_prefix + "batch:"):]
        legacy_receipt_raw = _owner_history_read_setting(handle, legacy_receipt_key)
        if (legacy_receipt_raw is None
                or _sha256(legacy_receipt_raw) != exact_receipt["legacy_receipt_sha256"]):
            raise LegacySnapshotGenerationConflict()
        legacy_receipt = _decode_legacy_append_receipt_for_progress(
            legacy_receipt_raw,
            legacy_receipt_id,
            scope,
            identity,
            status,
            migration_id,
        )
        if (legacy_receipt["start_source_sequence"] != expected_cursor
                or legacy_receipt["batch_sha256"] != batch_sha256
                or legacy_receipt["next_source_sequence"]
                != exact_receipt["next_source_sequence"]):
            raise LegacySnapshotGenerationConflict()
        for row in rows:
            pointer_key = _legacy_source_receipt_pointer_key(
                identity, migration_id, row.locator.sequence,
            )
            if _owner_history_read_setting(handle, pointer_key) != exact_receipt_key:
                raise LegacySnapshotGenerationConflict()
        result = _read_legacy_append_retry_in_transaction(
            handle,
            scope,
            identity,
            status,
            history_prefix,
            mapping_prefix,
            progress_key,
            legacy_receipt_key,
            history_prefix + "idempotency:" + legacy_receipt_id,
            legacy_receipt_raw,
            legacy_receipt_id,
            legacy_receipt["idempotency_key_sha256"],
            batch_sha256,
            rolling_batch_sha256,
            encoded_event_payloads,
            event_payloads,
            rows,
            expected_source_message_revision,
            expected_cursor,
            expected_rolling_head_sequence,
            expected_rolling_head_chain_sha256,
            current_head,
        )
        return LegacySnapshotAppendOutcome(
            "confirmed", result, checkpoint_root, source_generation,
        )

    if checkpoint["next_source_sequence"] != expected_cursor:
        # An advanced cursor without its exact receipt is an ambiguous durable
        # outcome. Do not append again; let the host reread the checkpoint.
        if checkpoint["next_source_sequence"] > expected_cursor:
            return LegacySnapshotAppendOutcome(
                "outcome_unknown", None, checkpoint_root, source_generation,
            )
        raise LegacySnapshotGenerationConflict()
    if (source_chain_before != checkpoint["source_chain_sha256"]
            or current_head != (
                expected_rolling_head_sequence,
                expected_rolling_head_chain_sha256,
            )
            or current_head != (
                checkpoint["rolling_head_sequence"],
                checkpoint["rolling_head_chain_sha256"],
            )):
        raise LegacySnapshotGenerationConflict()

    caller_key_sha256 = _sha256(idempotency_key)
    rolling_idempotency_key = (
        f"legacy-snapshot:{migration_id}:{caller_key_sha256}"
    )
    idempotency_id = _append_idempotency_id(scope, rolling_idempotency_key)
    batch_receipt_key = mapping_prefix + "batch:" + idempotency_id
    rolling_idempotency_db_key = history_prefix + "idempotency:" + idempotency_id
    if (_owner_history_read_setting(handle, batch_receipt_key) is not None
            or _owner_history_read_setting(handle, rolling_idempotency_db_key) is not None):
        raise LegacySnapshotGenerationConflict()

    mapping_keys: list[str] = []
    for row in rows:
        key = mapping_prefix + "source:" + f"{row.locator.sequence:032d}"
        if _owner_history_read_setting(handle, key) is not None:
            raise LegacySnapshotGenerationConflict()
        if _owner_history_read_setting(
                handle,
                _legacy_source_receipt_pointer_key(
                    identity, migration_id, row.locator.sequence,
                ),
            ) is not None:
            raise LegacySnapshotGenerationConflict()
        mapping_keys.append(key)

    rolling_references, new_head = _append_legacy_rolling_events_in_transaction(
        handle,
        scope,
        history_prefix,
        idempotency_id,
        rolling_idempotency_db_key,
        event_payloads,
        encoded_event_payloads,
        current_head,
    )
    _verify_new_legacy_rolling_event_refs(
        handle,
        scope,
        history_prefix,
        idempotency_id,
        rolling_references,
        event_payloads,
        encoded_event_payloads,
    )
    references_by_source = _legacy_append_references_by_source(
        rows, rolling_references,
    )
    mapping_values: list[str] = []
    for row, key in zip(rows, mapping_keys, strict=True):
        mapping_raw = _canonical(_legacy_append_source_mapping(
            scope,
            migration_id,
            row,
            references_by_source[row.locator.sequence],
        ))
        _owner_history_insert_setting(handle, key, mapping_raw)
        mapping_values.append(mapping_raw)

    text_count = sum(row.classification == "text_projection_available" for row in rows)
    archive_only_count = sum(row.classification == "archive_only" for row in rows)
    receipt = _legacy_append_batch_receipt_value(
        scope=scope,
        identity=identity,
        migration_id=migration_id,
        idempotency_id=idempotency_id,
        idempotency_key_sha256=caller_key_sha256,
        batch_sha256=batch_sha256,
        rolling_batch_sha256=rolling_batch_sha256,
        source_revision=status.source_message_revision,
        total_count=status.total_count,
        start_source_sequence=expected_cursor,
        next_source_sequence=next_source_sequence,
        source_chain_before=source_chain_before,
        source_chain_after=source_chain_after,
        previous_rolling_head=current_head,
        result_rolling_head=new_head,
        mappings=tuple(
            _legacy_append_mapping_receipt_value(
                row.locator,
                row.classification,
                references_by_source[row.locator.sequence],
            )
            for row in rows
        ),
    )
    receipt_raw = _canonical(receipt)
    _owner_history_insert_setting(handle, batch_receipt_key, receipt_raw)

    next_progress_value = _legacy_append_progress_value(
        scope=scope,
        identity=identity,
        migration_id=migration_id,
        status=status,
        next_source_sequence=next_source_sequence,
        source_chain_sha256=source_chain_after,
        text_projection_count=checkpoint["text_projection_count"] + text_count,
        archive_only_count=checkpoint["archive_only_count"] + archive_only_count,
        rolling_head=new_head,
    )
    next_progress_raw = _canonical(next_progress_value)
    _write_legacy_append_progress(
        handle, progress_key, progress_raw, next_progress_value,
    )
    next_marker_value = _legacy_append_start_marker_value(
        scope=scope,
        identity=identity,
        migration_id=migration_id,
        status=status,
        initial_rolling_head=(
            marker["initial_rolling_head_sequence"],
            marker["initial_rolling_head_chain_sha256"],
        ),
        progress_value=next_progress_value,
        last_batch_sha256=batch_sha256,
    )
    next_marker_raw = _write_legacy_append_start_marker(
        handle, marker_key, marker_raw, next_marker_value,
    )
    next_checkpoint = _legacy_checkpoint_value(
        scope=scope,
        identity=identity,
        status=status,
        message_source_generation=source_generation,
        message_count=checkpoint["message_count"],
        non_append_generation=checkpoint["non_append_generation"],
        legacy_source_generation=checkpoint["legacy_source_generation"],
        legacy_checkpoint_root_sha256=checkpoint["legacy_checkpoint_root_sha256"],
        initial_rolling_head=(
            checkpoint["initial_rolling_head_sequence"],
            checkpoint["initial_rolling_head_chain_sha256"],
        ),
        next_source_sequence=next_source_sequence,
        source_chain_sha256=source_chain_after,
        text_projection_count=checkpoint["text_projection_count"] + text_count,
        archive_only_count=checkpoint["archive_only_count"] + archive_only_count,
        rolling_head=new_head,
        rolling_head_reference=_legacy_checkpoint_head_reference(
            handle, scope, history_prefix, new_head,
        ),
        last_batch_sha256=batch_sha256,
    )
    next_checkpoint_raw = _write_legacy_checkpoint(
        handle, checkpoint_key, checkpoint_raw, next_checkpoint,
    )
    exact_receipt = _legacy_exact_batch_receipt_value(
        identity=identity,
        migration_id=migration_id,
        checkpoint_root_sha256=checkpoint_root,
        source_generation=source_generation,
        expected_cursor=expected_cursor,
        batch_sha256=batch_sha256,
        legacy_receipt_key=batch_receipt_key,
        legacy_receipt_raw=receipt_raw,
        next_source_sequence=next_source_sequence,
        source_chain_after=source_chain_after,
        previous_rolling_head=current_head,
        result_rolling_head=new_head,
    )
    exact_receipt_raw = _canonical(exact_receipt)
    _owner_history_insert_setting(handle, exact_receipt_key, exact_receipt_raw)
    pointer_keys: list[str] = []
    for row in rows:
        pointer_key = _legacy_source_receipt_pointer_key(
            identity, migration_id, row.locator.sequence,
        )
        _owner_history_insert_setting(handle, pointer_key, exact_receipt_key)
        pointer_keys.append(pointer_key)

    if (_owner_history_read_setting(handle, batch_receipt_key) != receipt_raw
            or _owner_history_read_setting(handle, exact_receipt_key) != exact_receipt_raw
            or _owner_history_read_setting(handle, progress_key) != next_progress_raw
            or _owner_history_read_setting(handle, marker_key) != next_marker_raw
            or _owner_history_read_setting(handle, checkpoint_key) != next_checkpoint_raw
            or _owner_history_head(handle, scope, history_prefix) != new_head
            or _read_message_source_fence(handle, identity.session_id)
            != LegacyMessageSourceFence(
                source_generation,
                checkpoint["message_count"],
                checkpoint["non_append_generation"],
            )):
        raise LegacySnapshotGenerationConflict()
    for row, key, expected_raw, pointer_key in zip(
        rows, mapping_keys, mapping_values, pointer_keys, strict=True,
    ):
        if (_owner_history_read_setting(handle, key) != expected_raw
                or _owner_history_read_setting(handle, pointer_key) != exact_receipt_key):
            raise LegacySnapshotGenerationConflict()
    decoded_progress = _decode_legacy_append_progress(
        next_progress_raw, scope, identity, status, migration_id,
    )
    decoded_marker = _decode_legacy_append_start_marker(
        next_marker_raw, scope, identity, status, migration_id,
    )
    decoded_checkpoint = _decode_legacy_checkpoint(
        next_checkpoint_raw, scope, identity, status,
    )
    _legacy_checkpoint_state_matches_progress(
        decoded_checkpoint, decoded_marker, decoded_progress, next_progress_raw,
    )
    for sequence, reference in (
        (row.locator.sequence, references_by_source[row.locator.sequence])
        for row in rows
    ):
        if reference is not None:
            event = _owner_history_load_event_at_sequence(
                handle, scope, history_prefix, reference.sequence,
            )
            if event is None or event.reference != reference:
                raise LegacySnapshotGenerationConflict()
    return LegacySnapshotAppendOutcome(
        "confirmed",
        _legacy_append_receipt_from_value(receipt),
        checkpoint_root,
        source_generation,
    )
