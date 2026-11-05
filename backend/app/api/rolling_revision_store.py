"""Host-only persistence for rolling-history v2 branches and local selection.

This module stores immutable revision and branch records, plus an owner-bound
active-branch pointer, in SQLite's existing Settings table. Pointer writes are
reported confirmed only after an owner-bound readback; they do not confirm graph
publication, a host publication receipt, or assistant completion.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
import json
import os
import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from app.api.compaction_checkpoint import HostPublicationBindingReceiptLookup
    from src.agent.compact.rolling_summary_ledger_bridge import (
        RollingSummaryLedgerBridgePayload,
    )

from app.api.context_events import (
    ContextEventScope,
    ContextEventSourceBinding,
    ContextEventV3Locator,
    _source_binding_value,
)
from app.api.legacy_snapshot_store import (
    LegacySnapshotGenerationConflict,
    LegacySnapshotSourceRelation,
    _is_sealed_generation as _legacy_is_sealed_generation,
    _read_current_source as _legacy_read_current_source,
    _read_generation as _legacy_read_generation,
    _relate_generation_to_source_in_transaction as _legacy_relate_generation_to_source_in_transaction,
    _require_migration_id as _legacy_require_migration_id,
    _status_from_row as _legacy_status_from_row,
    read_message_source_fence_in_transaction,
)
from app.api.rolling_history import (
    AttachmentReference,
    PreparedRollingHistoryV2Summary,
    RollingHistoryChunkCursor,
    RollingHistoryEventChunk,
    RollingHistoryBodyChunkCursor,
    RollingHistoryBodyEventChunk,
    RollingHistoryBodyEventInput,
    RollingHistorySourceMessageWriteReceipt,
    RollingHistoryReference,
    RollingHistoryV2Branch,
    RollingHistoryV2EventLocator,
    RollingHistoryV2Head,
    RollingHistoryV2LocatorIndex,
    RollingHistoryV2MigrationCheckpoint,
    RollingHistoryV2MigrationLiveFence,
    RollingHistoryV2MigrationSourceGeneration,
    RollingHistoryV2MigrationSourcePage,
    RollingHistoryV2Revision,
    _bind_persisted_rolling_history_v2_migration_checkpoint,
    _bind_current_rolling_history_v2_migration_source_generation,
    _bind_verified_rolling_history_v2_migration_root,
    _append_rolling_history_v2_revision_events,
    _canonical,
    _event_id_mapping,
    _append_idempotency_id,
    _append_rolling_history_legacy_message_snapshot_event_in_transaction,
    _body_event_batch_digest,
    _body_id_for_event,
    _batch_digest,
    _encode_body_event_input,
    _encode_event,
    _format_sequence,
    _genesis_chain,
    _owner_history_head,
    _owner_history_read_event_reference_at_sequence,
    _owner_history_read_setting_bounded,
    _parse_attachment,
    _split_event_text,
    _require_owner_history_handle,
    _load_event_at_sequence,
    _new_rolling_history_v2_migration_live_fence,
    _parse_rolling_history_v2_migration_live_fence,
    _rolling_history_v2_migration_checkpoint_body,
    _rolling_history_v2_branch_envelope,
    _rolling_history_v2_head_envelope,
    _rolling_history_v2_idempotency_id,
    _rolling_history_v2_lineage_commitment_sha256,
    _rolling_history_v2_lineage_root_sha256,
    _rolling_history_v2_prepared_envelope,
    _rolling_history_v2_migration_live_fence_value,
    _rolling_history_v2_revision_envelope,
    _rolling_history_v2_empty_locator_index_root,
    _validate_rolling_history_v2_locator_index,
    _rolling_history_v2_locator_tuple,
    _scope_digest,
    _scope_matches,
    _scope_prefix,
    _sha256,
    append_rolling_history_in_transaction,
    _event_manifest_key,
    _validate_body_backed_event_manifest,
    _validate_rolling_history_v2_migration_live_append,
    _validate_rolling_history_v2_migration_source_page,
    _validate_rolling_history_v2_branch,
    _validate_rolling_history_v2_head,
    _validate_rolling_history_v2_head_token,
    _validate_rolling_history_v2_lineage,
    _validate_body_chunk_cursor,
    _validate_prepared_rolling_history_v2_summary,
    _validate_rolling_history_v2_revision,
    new_rolling_history_v2_migration_checkpoint,
    read_rolling_history_v2_migration_source_page,
    read_rolling_history_event_chunk_in_transaction,
    read_rolling_history_event_body_chunk_in_transaction,
    rolling_history_session_prefix,
    _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE,
    _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT,
    MAX_BODY_EVENT_CONTENT_UTF8_BYTES,
    MAX_BODY_EVENT_METADATA_UTF8_BYTES,
)
from app.api.rolling_history_owner_scope import (
    MaterializedRows,
    OperationMode,
    RollingHistoryOwnerScopeError,
    RollingHistorySqlHandle,
    ScopeOrigin,
    _current_scope,
    bootstrap_rolling_history_owner_scope,
    get_rolling_history_owner_identity,
    run_legacy_message_migration_owner_write_operation,
    run_rolling_history_owner_operation,
)
from app.db.models import Settings


_STORE_SCHEMA_VERSION = 2
_GROUNDED_SUMMARY_SCHEMA_VERSION = 1
_GROUNDED_SUMMARY_KIND = "rolling_history_v2_prepared_grounded_summary"
_GROUNDED_SUMMARY_SOURCE_KIND = "rolling_history_v2_prepared_grounded_summary_source_manifest"
_SUMMARY_LEDGER_SCHEMA_VERSION = 1
_SUMMARY_LEDGER_KIND = "rolling_history_v2_prepared_summary_ledger"
_SUMMARY_LEDGER_SOURCE_KIND = "rolling_history_v2_prepared_summary_ledger_source_manifest"
_SUMMARY_LEDGER_ACTIONS = frozenset({"replace", "complete", "revoke"})
_SUMMARY_LEDGER_LOCATOR_BYTE_LIMIT = 1024
_SUMMARY_LEDGER_LOCATOR_FIELDS = (
    "event_id", "session_id", "request_id", "sequence", "version",
    "content_sha256", "chain_sha256",
)
_GROUNDED_SUMMARY_REFERENCE_FIELDS = (
    "event_id", "session_id", "request_id", "sequence", "version",
    "content_sha256", "chain_sha256",
)
_BOUND_HEAD_TOKEN_VERSION = 1
_ACTIVE_BRANCH_POINTER_SCHEMA_VERSION = 1
_MIGRATION_ROOT_MAP_V1_SCHEMA_VERSION = 1
_MIGRATION_ROOT_MAP_V1_KIND = "rolling_history_v2_migration_root_mapping"
_MIGRATION_ROOT_MAP_V2_SCHEMA_VERSION = 2
_MIGRATION_ROOT_MAP_V2_KIND = "rolling_history_v2_migration_root_mapping_v2"
_MIGRATION_ROOT_MAP_V3_SCHEMA_VERSION = 3
_MIGRATION_ROOT_MAP_V3_KIND = "rolling_history_v2_migration_root_mapping_v3"
_MIGRATION_CHECKPOINT_STATE_SCHEMA_VERSION = 3
_MIGRATION_CHECKPOINT_STATE_KIND = "rolling_history_v2_migration_checkpoint_head_v3"
_MIGRATION_CHECKPOINT_RECORD_KIND = "rolling_history_v2_migration_checkpoint_v3"
_LEGACY_MESSAGE_BLOB_MIGRATION_SCHEMA_VERSION = 1
_LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION = 2
_LEGACY_MESSAGE_BLOB_MIGRATION_KIND = "rolling_history_v2_legacy_message_blob_migration_v2"
_LEGACY_MESSAGE_BLOB_SNAPSHOT_ROOT_KIND = "legacy_message_blob_snapshot_root_v2"
_LEGACY_MESSAGE_BLOB_CAPTURE_HEAD_KIND = "rolling_history_v2_legacy_message_blob_capture_head"
_LEGACY_MESSAGE_BLOB_CAPTURE_PAGE_KIND = "rolling_history_v2_legacy_message_blob_capture_page"
_LEGACY_MESSAGE_BLOB_SOURCE_ROW_KIND = "rolling_history_v2_legacy_message_blob_source_row"
_LEGACY_MESSAGE_BLOB_SOURCE_CHAIN_KIND = "rolling_history_v2_legacy_message_blob_source_chain"
_LEGACY_MESSAGE_BLOB_MIGRATION_ROOT_KIND = "rolling_history_v2_legacy_message_blob_migration_root_v2"
_LEGACY_MESSAGE_BLOB_CHECKPOINT_HEAD_KIND = "rolling_history_v2_legacy_message_blob_checkpoint_head_v2"
_LEGACY_MESSAGE_BLOB_CHECKPOINT_RECORD_KIND = "rolling_history_v2_legacy_message_blob_checkpoint_v2"
_LEGACY_MESSAGE_BLOB_SOURCE_MAPPING_KIND = "rolling_history_v2_legacy_message_blob_source_mapping_v2"
_LEGACY_MESSAGE_BLOB_CAPTURE_ROWS = 32
_LEGACY_MESSAGE_BLOB_CAPTURE_BODY_BYTES = 1024 * 1024
_LEGACY_MESSAGE_BLOB_SOURCE_ZERO_CHAIN = "0" * 64
_MIGRATION_ROOT_MAP_RELATIONS = frozenset({"EXACT_CURRENT", "APPEND_ONLY_PREFIX"})
_MAX_LINEAGE_DEPTH = 64
_LOCATOR_INDEX_SCHEMA_VERSION = 1
_LOCATOR_INDEX_KIND = "rolling_history_v2_locator_index_node"
_LOCATOR_PAGE_KIND = "rolling_history_v2_locator_page"
_LOCATOR_PAGE_API_VERSION = 2
_MAX_ACTIVE_BRANCH_POINTER_REVISION = 2**63 - 1
_PUBLICATION_SNAPSHOT_MAX_PAGES = 64
_PUBLICATION_SNAPSHOT_MAX_EVENTS = 8192
_PUBLICATION_SNAPSHOT_MAX_EVENT_BYTES = 1024 * 1024
_PUBLICATION_SNAPSHOT_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_PUBLICATION_SNAPSHOT_MAX_HEAD_REVISION = 4096
_COMPACTION_SOURCE_BINDING_MAX_MESSAGES = 64
_COMPACTION_SOURCE_BINDING_MAX_ROWS = 128
_PUBLICATION_SNAPSHOT_EVENT_CHUNK_BYTES = 8192
_PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES = 8192
_PUBLICATION_LOCATOR_NODE_MAX_BYTES = 4096
_PUBLICATION_LOCATOR_PAGE_MAX_BYTES = 65536
_PUBLICATION_BUNDLE_MAX_RECORD_BYTES = 1024 * 1024
_PUBLICATION_BINDING_LEGACY_SCHEMA_VERSION = 1
_PUBLICATION_BINDING_SCHEMA_VERSION = 2
_PUBLICATION_LIFECYCLE_SCHEMA_VERSION = 2
_PUBLICATION_HOST_CANDIDATE_SCHEMA_VERSION = 2
_PUBLICATION_BINDING_KIND = "rolling_history_v2_summary_publication_binding"
_PUBLICATION_BUNDLE_KIND = "rolling_history_v2_summary_publication_bundle"
_PUBLICATION_CONFIRMATION_KIND = "rolling_history_v2_summary_publication_confirmation"
_PUBLICATION_LIFECYCLE_KIND = "rolling_history_v2_summary_publication_lifecycle"
_PUBLICATION_ATTEMPT_KIND = "rolling_history_v2_summary_publication_apply_attempt"
_PUBLICATION_NOT_APPLIED_KIND = "rolling_history_v2_summary_publication_not_applied"
_PUBLICATION_ABANDONED_KIND = "rolling_history_v2_summary_publication_prepared_abandoned"
_PUBLICATION_HOST_MAX_TAIL_MESSAGES = 8192
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_POINTER_OPERATION_ID = re.compile(r"[0-9a-f]{32}\Z")
_STORE_BINDING: ContextVar["_StoreBinding | None"] = ContextVar(
    "rolling_history_revision_store_binding", default=None,
)
_NO_IDEMPOTENCY = object()


class RollingHistoryV2Conflict(ValueError):
    """An expected-head, pointer, or idempotency token conflicts with stored state."""


class RollingHistoryV2MigrationRequired(ValueError):
    """A legacy v1 history or v2 namespace requires explicit migration."""

    code = "migration_required"

    def __init__(self):
        super().__init__(self.code)


@dataclass(frozen=True)
class _BoundRollingHistoryV2Head:
    """Host-issued expected-head token tied to one owner namespace."""

    wrapper_version: int
    canonical_namespace_digest: str
    head: RollingHistoryV2Head
    wrapper_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2StoreResult:
    """One locally committed revision and its host-side visible event prefix."""

    branch: RollingHistoryV2Branch
    revision: RollingHistoryV2Revision
    head: _BoundRollingHistoryV2Head
    visible_event_locators: tuple[RollingHistoryV2EventLocator, ...]
    locator_index: RollingHistoryV2LocatorIndex | None = None


@dataclass(frozen=True)
class RollingHistoryV2BodyAppendResult:
    """Atomic body/history/index append result, provisional until outer commit."""

    references: tuple[RollingHistoryReference, ...]
    branch: RollingHistoryV2Branch
    revision: RollingHistoryV2Revision
    head: _BoundRollingHistoryV2Head
    locator_index: RollingHistoryV2LocatorIndex
    branch_id: str
    revision_id: str
    revision_sha256: str
    head_sha256: str
    source_fence: RollingHistoryV2MigrationLiveFence


@dataclass(frozen=True)
class RollingHistoryV2CompactionSourceBindingResult:
    """Exact owner-verified V3 source bindings for one compaction prefix."""

    snapshot_sha256: str
    head_sha256: str
    source_fence_sha256: str
    recent_message_start_boundary: int
    source_bindings: tuple[ContextEventSourceBinding, ...]


@dataclass(frozen=True)
class RollingHistoryV2LegacyMessageSnapshotPageResult:
    """One owner-confirmed capture page for a sealed-message migration source."""

    outcome: str
    snapshot_state: object | None
    page_receipt: object | None


@dataclass(frozen=True)
class RollingHistoryV2LegacyMessageMigrationResult:
    """One migration checkpoint and the exact v2 head committed beside it."""

    outcome: str
    migration_id: str
    snapshot_id: str
    source_root_sha256: str | None
    next_source_sequence: int | None
    checkpoint_sha256: str | None
    branch_id: str | None
    revision_id: str | None
    head_sha256: str | None
    has_more: bool | None


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummaryRecord:
    """Committed PREPARED summary plus the exact host-verified source manifest."""

    schema_version: int
    state: str
    canonical_namespace_digest: str
    payload_sha256: str
    prepared: PreparedRollingHistoryV2Summary
    summary: str
    branch: RollingHistoryV2Branch
    revision: RollingHistoryV2Revision
    expected_head: RollingHistoryV2Head
    source_event_locators: tuple[RollingHistoryV2EventLocator, ...]
    source_manifest_json: str
    source_sha256: str
    prepared_record_sha256: str
    record_json: str


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummarySourceBody:
    """One revalidated model-visible message body and its source locator."""

    locator: RollingHistoryV2EventLocator
    body: str
    body_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummaryClaimSource:
    """One exact summary quote mapped to its immutable event locator."""

    field: str
    claim_index: int
    quote: str
    locator: RollingHistoryV2EventLocator
    body_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummaryResolutionSource:
    """One resolution's exact prior and later source quotes and host roles."""

    field: str
    action: str
    prior_quote: str
    prior_source_role: str
    prior_locator: RollingHistoryV2EventLocator
    evidence_quote: str
    evidence_source_role: str
    evidence_locator: RollingHistoryV2EventLocator


@dataclass(frozen=True)
class RollingHistoryV2PreparedGroundedSummaryRecord:
    """Versioned PREPARED rolling summary with source and boundary commitments.

    Exact-source anchoring does not establish semantic correctness or truth.
    PREPARED is durable local state, not publication, acknowledgement, or proof
    that any action was completed.
    """

    schema_version: int
    state: str
    canonical_namespace_digest: str
    payload_sha256: str
    prepared_id: str
    summary: "RollingSummary"
    recent_message_start_boundary: int
    branch: RollingHistoryV2Branch
    revision: RollingHistoryV2Revision
    expected_head: RollingHistoryV2Head
    source_event_locators: tuple[RollingHistoryV2EventLocator, ...]
    source_bodies: tuple[RollingHistoryV2PreparedSummarySourceBody, ...]
    claim_sources: tuple[RollingHistoryV2PreparedSummaryClaimSource, ...]
    source_sha256: str
    prepared_record_sha256: str
    record_json: str
    resolution_sources: tuple[RollingHistoryV2PreparedSummaryResolutionSource, ...] = ()


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummaryLedgerLocator:
    """Full bridge locator, including the session bound by the host scope."""

    event_id: str
    session_id: str
    request_id: str
    sequence: int
    version: int
    content_sha256: str
    chain_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummaryLedgerSourceBody:
    """One owner-revalidated source body used by a prepared ledger entry."""

    locator: RollingHistoryV2PreparedSummaryLedgerLocator
    role: str
    body: str
    body_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummaryLedgerResolution:
    """One exact later human source attached to an archived claim."""

    action: str
    evidence_text: str
    evidence_locator: RollingHistoryV2PreparedSummaryLedgerLocator
    evidence_sequence: int
    evidence_source_role: str
    inherited_from_previous_summary: bool


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummaryLedgerEntry:
    """One source candidate and its host-revalidated role and update relation."""

    source_sequence: int
    source_locator: RollingHistoryV2PreparedSummaryLedgerLocator
    source_role: str
    field: str | None
    quote: str | None
    resolution: RollingHistoryV2PreparedSummaryLedgerResolution | None


@dataclass(frozen=True)
class RollingHistoryV2PreparedSummaryLedgerRecord:
    """Independent, versioned PREPARED evidence bound to one verified revision.

    PREPARED records prove persistence and source readback only. They do not
    confirm graph publication, acknowledgement, or assistant completion.
    """

    schema_version: int
    state: str
    canonical_namespace_digest: str
    payload_sha256: str
    prepared_id: str
    entries: tuple[RollingHistoryV2PreparedSummaryLedgerEntry, ...]
    branch: RollingHistoryV2Branch
    revision: RollingHistoryV2Revision
    expected_head: RollingHistoryV2Head
    source_bodies: tuple[RollingHistoryV2PreparedSummaryLedgerSourceBody, ...]
    source_manifest_json: str
    source_sha256: str
    prepared_record_sha256: str
    record_json: str


@dataclass(frozen=True)
class _SummaryLedgerEntryValue:
    """Normalized bridge candidate fields before host source verification."""

    source_sequence: int
    source_locator: RollingHistoryV2PreparedSummaryLedgerLocator
    field: str | None
    quote: str | None
    resolution_action: str | None
    evidence_text: str | None
    evidence_locator: RollingHistoryV2PreparedSummaryLedgerLocator | None
    evidence_sequence: int | None
    evidence_inherited_from_previous_summary: bool | None


@dataclass(frozen=True)
class _RawRollingHistoryV2StoreResult:
    """Internal result carrying the persisted v2 head before host binding."""

    branch: RollingHistoryV2Branch
    revision: RollingHistoryV2Revision
    head: RollingHistoryV2Head
    visible_event_locators: tuple[RollingHistoryV2EventLocator, ...]
    locator_index: RollingHistoryV2LocatorIndex | None = None
    lineage_proof: "RollingHistoryV2LineageProof | None" = None


@dataclass(frozen=True)
class RollingHistoryV2LocatorPageCursor:
    """Continuation bound to one immutable branch revision and index root."""

    cursor_version: int
    branch_id: str
    revision_id: str
    revision_sha256: str
    head_sha256: str
    index_root_sha256: str
    pin_sha256: str
    source_fence_sha256: str
    next_page_index: int
    cursor_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2LocatorIndexProofStep:
    """One bounded radix node on a locator page's root-to-leaf proof."""

    level: int
    child_index: int
    children: tuple[str | None, ...]


@dataclass(frozen=True)
class RollingHistoryV2LineageProof:
    """Logarithmic membership proofs for the branch creator and pinned revision."""

    schema_version: int
    branch_id: str
    lineage_root_sha256: str
    lineage_index: RollingHistoryV2LocatorIndex
    creator_revision: RollingHistoryV2Revision
    creator_revision_id: str
    creator_revision_sha256: str
    creator_lineage_commitment_sha256: str
    creator_lineage_locator: RollingHistoryV2EventLocator
    creator_page_locators: tuple[RollingHistoryV2EventLocator, ...]
    creator_index_proof: tuple[RollingHistoryV2LocatorIndexProofStep, ...]
    revision_number: int
    revision: RollingHistoryV2Revision
    revision_id: str
    revision_sha256: str
    revision_lineage_commitment_sha256: str
    revision_lineage_locator: RollingHistoryV2EventLocator
    revision_page_locators: tuple[RollingHistoryV2EventLocator, ...]
    revision_index_proof: tuple[RollingHistoryV2LocatorIndexProofStep, ...]
    parent_revision_id: str | None
    parent_revision_sha256: str | None
    parent_revision: RollingHistoryV2Revision | None
    parent_lineage_commitment_sha256: str | None
    parent_revision_lineage_commitment_sha256: str | None
    parent_lineage_locator: RollingHistoryV2EventLocator | None
    parent_page_locators: tuple[RollingHistoryV2EventLocator, ...] | None
    parent_index_proof: tuple[RollingHistoryV2LocatorIndexProofStep, ...]
    proof_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2LocatorPagePin:
    """Immutable head/index/fence identity captured by the first page read."""

    schema_version: int
    canonical_namespace_digest: str
    branch_id: str
    head: RollingHistoryV2Head
    index: RollingHistoryV2LocatorIndex
    source_fence: RollingHistoryV2MigrationLiveFence
    pin_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2LocatorPageResult:
    """One verified locator page from an owner-bound READ transaction."""

    branch: RollingHistoryV2Branch
    head: RollingHistoryV2Head
    index: RollingHistoryV2LocatorIndex
    page_index: int
    locators: tuple[RollingHistoryV2EventLocator, ...]
    proof: tuple[RollingHistoryV2LocatorIndexProofStep, ...]
    lineage_proof: RollingHistoryV2LineageProof
    next_cursor: RollingHistoryV2LocatorPageCursor | None
    pin: RollingHistoryV2LocatorPagePin
    source_fence: RollingHistoryV2MigrationLiveFence


@dataclass(frozen=True)
class RollingHistoryV2PublicationPageProof:
    """One bounded page proof retained by a complete publication snapshot."""

    page_index: int
    locators: tuple[RollingHistoryV2EventLocator, ...]
    index_proof: tuple[RollingHistoryV2LocatorIndexProofStep, ...]
    lineage_proof_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PublicationEvent:
    """A complete, verified V2 event with the proof needed to bind it."""

    locator: RollingHistoryV2EventLocator
    page_index: int
    event_type: str
    role: Literal["human", "assistant", "tool"]
    attachments: tuple[AttachmentReference, ...]
    body: str
    body_utf8_byte_length: int
    body_sha256: str
    payload_sha256: str
    event_chain_sha256: str
    text_sha256: str
    body_manifest_sha256: str | None
    event_manifest_sha256: str | None
    body_chunk_sha256s: tuple[str, ...]
    event_chunk_sha256s: tuple[str, ...]


@dataclass(frozen=True)
class _PublicationBodyEventMetadata:
    event_type: str
    attachments: tuple[AttachmentReference, ...]
    payload_sha256: str
    text_sha256: str
    event_chain_sha256: str
    event_manifest_sha256: str
    body_manifest_sha256: str
    body_utf8_byte_length: int
    body_chunk_count: int
    body_chain_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PublicationSnapshot:
    """Bounded model-source snapshot pinned to one owner and active V2 head."""

    canonical_namespace_digest: str
    scope_sha256: str
    delete_epoch: str
    request_id: str
    active_pointer: "RollingHistoryV2ActiveBranchPointer"
    head: RollingHistoryV2Head
    bound_head: _BoundRollingHistoryV2Head
    page_pin: RollingHistoryV2LocatorPagePin
    page_proofs: tuple[RollingHistoryV2PublicationPageProof, ...]
    events: tuple[RollingHistoryV2PublicationEvent, ...]
    total_body_utf8_bytes: int
    snapshot_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PreparedRecordBinding:
    """Exact immutable PREPARED record commitment carried by a composite."""

    prepared_id: str
    prepared_record_sha256: str
    payload_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2HostCandidateCommitment:
    """Trusted adapter commitment to host coverage, candidate, and source crosswalk."""

    schema_version: int
    coverage_normalizer: Literal["plain-text-v1"]
    prefix_count: int
    prefix_sha256: str
    tail_count: int
    tail_sha256: str
    candidate_sha256: str
    source_crosswalk_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2SummaryPublicationBinding:
    """Owner-bound immutable association of the V2 summary, ledger, and host candidate."""

    schema_version: int
    binding_id: str
    binding_sha256: str
    idempotency_key_sha256: str
    canonical_namespace_digest: str
    scope_sha256: str
    delete_epoch: str
    request_id: str
    active_pointer: "RollingHistoryV2ActiveBranchPointer"
    expected_head: RollingHistoryV2Head
    source_fence: RollingHistoryV2MigrationLiveFence
    snapshot_sha256: str
    recent_message_start_boundary: int
    grounded_summary: RollingHistoryV2PreparedRecordBinding
    summary_ledger: RollingHistoryV2PreparedRecordBinding
    host_coverage_normalizer: Literal["plain-text-v1"]
    host_prefix_count: int
    host_prefix_sha256: str
    host_tail_count: int
    host_tail_sha256: str
    host_candidate_sha256: str
    # Legacy v1 bindings decode as UNKNOWN and cannot enter a publication path.
    host_source_crosswalk_sha256: str | None


@dataclass(frozen=True)
class RollingHistoryV2PublicationBundle:
    """Two readback-confirmed PREPARED records and their immutable binding."""

    state: Literal["PREPARED"]
    grounded_summary: RollingHistoryV2PreparedGroundedSummaryRecord
    summary_ledger: RollingHistoryV2PreparedSummaryLedgerRecord
    binding: RollingHistoryV2SummaryPublicationBinding


@dataclass(frozen=True)
class RollingHistoryV2GraphCheckpointIdentity:
    """Protocol identity for one exact graph checkpoint and candidate commitment.

    candidate_sha256 is the host's canonical message-list digest. It is not a
    digest of LangGraph's serialized checkpoint payload.
    """

    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str | None
    parent_checkpoint_id: str | None
    candidate_sha256: str | None


@dataclass(frozen=True)
class RollingHistoryV2GraphApplyAuthorization:
    """Single-use authorization consumed inside a fenced GraphSaver aput."""

    attempt_id: str
    attempt_sha256: str
    binding_id: str
    binding_sha256: str
    graph_checkpoint_identity_sha256: str
    candidate_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PublicationNotApplied:
    """Auditable close after a fenced read proved the old graph tuple remains."""

    state: Literal["NOT_APPLIED"]
    binding_id: str
    binding_sha256: str
    attempt_id: str
    graph_checkpoint_identity_sha256: str
    host_scan_sha256: str
    terminal_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2PublicationRecovery:
    """Readback state; it deliberately never exposes a PUBLISHED state."""

    state: Literal[
        "NOT_FOUND", "PREPARED", "APPLYING_UNKNOWN", "NOT_APPLIED",
        "HOST_RECEIPT_CONFIRMED", "STORE_CONFIRMATION_RECORDED",
        "PREPARED_ABANDONED", "UNTRACKED_PREPARED", "CONFLICT", "UNKNOWN",
    ]
    binding: RollingHistoryV2SummaryPublicationBinding | None = None
    bundle: RollingHistoryV2PublicationBundle | None = None
    expected_head: _BoundRollingHistoryV2Head | None = None
    host_candidate_commitment: RollingHistoryV2HostCandidateCommitment | None = None
    host_lookup: "HostPublicationBindingReceiptLookup | None" = None
    lifecycle_sha256: str | None = None
    attempt_id: str | None = None
    graph_checkpoint_identity: RollingHistoryV2GraphCheckpointIdentity | None = None
    graph_checkpoint_identity_sha256: str | None = None
    terminal_sha256: str | None = None
    confirmation_sha256: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class RollingHistoryV2SummaryPublicationConfirmation:
    """Exact host graph receipt accepted for one current V2 active head."""

    state: Literal["PUBLISHED"]
    binding_id: str
    binding_sha256: str
    expected_head_sha256: str
    host_receipt_id: str
    host_receipt_sha256: str
    host_coverage_normalizer: Literal["plain-text-v1"]
    host_prefix_count: int
    host_prefix_sha256: str
    host_tail_count: int
    host_tail_sha256: str
    host_candidate_sha256: str
    host_source_crosswalk_sha256: str
    graph_checkpoint_identity_sha256: str
    graph_checkpoint_id: str
    graph_parent_checkpoint_id: str | None
    confirmation_sha256: str


@dataclass(frozen=True)
class _StoreBinding:
    user: dict | None
    origin: ScopeOrigin
    scope: ContextEventScope


@dataclass(frozen=True)
class _ConflictResult:
    message: str


@dataclass(frozen=True)
class _MigrationRequiredResult:
    pass


@dataclass(frozen=True)
class _PublicationRecoverySeed:
    binding_raw: str | None
    summary_raw: str | None
    ledger_raw: str | None
    lifecycle_raw: str | None
    attempt_raw: str | None
    not_applied_raw: str | None
    abandoned_raw: str | None
    confirmation_raw: str | None


@dataclass(frozen=True)
class _PublicationRecoveryReadback:
    binding: RollingHistoryV2SummaryPublicationBinding
    bundle: RollingHistoryV2PublicationBundle
    expected_head: _BoundRollingHistoryV2Head
    lifecycle_sha256: str
    attempt: dict | None
    not_applied: dict | None
    abandoned: dict | None
    confirmation: dict | None


@dataclass(frozen=True)
class _MissingEventChunkResult:
    pass


@dataclass(frozen=True)
class RollingHistoryV2MigrationSourcePageResult:
    """A resumable page is exposed only after its owner WRITE operation commits."""

    outcome: str
    source_page: RollingHistoryV2MigrationSourcePage | None


@dataclass(frozen=True)
class RollingHistoryV2ActiveBranchPointer:
    """Host-issued CAS token for one owner namespace's local branch selection."""

    state: str
    canonical_namespace_digest: str
    pointer_revision: int
    branch_id: str | None
    operation_id: str | None
    pointer_sha256: str


@dataclass(frozen=True)
class RollingHistoryV2ActiveBranchPointerResult:
    """Readback result for a local active-branch pointer operation.

    outcome is readback_confirmed only when the owner-bound read observed the
    complete pointer state; outcome_unknown carries no pointer, branch, or head.
    A confirmed pointer records only local branch selection. It is not a graph
    acknowledgement, host publication receipt, or assistant-completed proof.
    """

    outcome: str
    pointer: RollingHistoryV2ActiveBranchPointer | None
    branch: RollingHistoryV2Branch | None
    head: _BoundRollingHistoryV2Head | None


@dataclass(frozen=True)
class _ActiveBranchPointerRead:
    pointer: RollingHistoryV2ActiveBranchPointer
    record_json: str | None
    branch: RollingHistoryV2Branch | None
    head: _BoundRollingHistoryV2Head | None


@dataclass(frozen=True)
class _ActiveBranchPointerWrite:
    pointer: RollingHistoryV2ActiveBranchPointer
    record_json: str


class _SettingsReader:
    """Adapt the restricted SQL handle to rolling_history's read-only helpers."""

    __slots__ = ("_handle",)

    def __init__(self, handle: RollingHistorySqlHandle):
        self._handle = handle

    def get(self, model, key: str):
        if model is not Settings:
            raise ValueError("Unsupported rolling history row type")
        rows = _select_rows(
            self._handle,
            "SELECT value FROM settings WHERE key = ?",
            (key,),
            ("value",),
        )
        if not rows:
            return None
        return SimpleNamespace(value=rows[0][0])


def _read_current_migration_live_fence(
    handle: RollingHistorySqlHandle,
    session_id: str,
) -> RollingHistoryV2MigrationLiveFence:
    """Read the mutable-message fence through the same owner transaction."""
    value = read_message_source_fence_in_transaction(handle, session_id)
    try:
        return _new_rolling_history_v2_migration_live_fence(
            value.message_source_generation,
            value.message_count,
            value.non_append_generation,
        )
    except AttributeError as exc:
        raise ValueError("Transactional message source fence is unavailable") from exc


@contextmanager
def rolling_history_v2_owner_scope(
    user: dict | None,
    origin: ScopeOrigin,
) -> Iterator[None]:
    """Bind store calls to a host route's current user and archive scope.

    ``user`` must be the value returned by the authenticated host route's
    ``current_user`` dependency. The scope is obtained from the selected host
    ContextVar; callers cannot supply a session, principal, epoch, or scope.
    """
    if type(origin) is not ScopeOrigin or _STORE_BINDING.get() is not None:
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    with bootstrap_rolling_history_owner_scope(user, origin):
        scope = _current_scope(origin)
        token = _STORE_BINDING.set(_StoreBinding(user=user, origin=origin, scope=scope))
        try:
            yield None
        finally:
            _STORE_BINDING.reset(token)


def create_rolling_history_v2_root(
    *,
    idempotency_key: str,
) -> RollingHistoryV2StoreResult:
    """Create an empty branch only at the verified v1 genesis head."""
    binding = _current_binding()
    scope = binding.scope
    idempotency_id, key_sha256 = _idempotency_context(scope, idempotency_key)
    payload_sha256 = _sha256(_canonical({"operation": "root"}))
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle):
        replay = _read_idempotent_result(
            handle, scope, namespace, idempotency_id, key_sha256,
            payload_sha256, "root",
        )
        if isinstance(replay, _ConflictResult):
            return replay
        if replay is not _NO_IDEMPOTENCY:
            genesis_chain = _genesis_chain(scope)
            if (replay.revision.operation != "root"
                    or replay.revision.history_revision != 0
                    or replay.revision.history_head_sha256 != genesis_chain):
                return _MigrationRequiredResult()
            return replay

        v1_prefix = _scope_prefix(scope)
        history_revision, history_head_sha256 = _read_v1_history_head(
            handle, scope, v1_prefix,
        )
        genesis_chain = _genesis_chain(scope)
        if history_revision != 0 or history_head_sha256 != genesis_chain:
            return _MigrationRequiredResult()
        _verify_history_endpoint(
            handle, scope, v1_prefix, 0, genesis_chain,
        )
        branch_id = _sha256(_canonical({
            "schema_version": _STORE_SCHEMA_VERSION,
            "kind": "rolling_history_v2_root_branch",
            "scope_sha256": _scope_digest(scope),
            "request_id": scope.request_id,
            "idempotency_id": idempotency_id,
        }))
        revision = _new_revision(
            handle=handle,
            namespace=namespace,
            scope=scope,
            branch_id=branch_id,
            idempotency_id=idempotency_id,
            idempotency_key_sha256=key_sha256,
            operation="root",
            representation="snapshot",
            parent=None,
            history_revision=0,
            history_head_sha256=genesis_chain,
            lineage_depth=0,
            event_locators=(),
        )
        branch = _new_branch(scope, revision)
        head = _new_head(revision)
        result = _RawRollingHistoryV2StoreResult(
            branch, revision, head, (), revision.locator_index,
        )
        _persist_new_branch(handle, namespace, result)
        _persist_idempotency(
            handle, scope, namespace, idempotency_id, key_sha256,
            payload_sha256, "root", result,
        )
        return result

    return _finish_operation(binding, OperationMode.WRITE, operation)


def create_rolling_history_v2_migration_root(
    *,
    migration_id: str,
) -> RollingHistoryV2StoreResult:
    """Create or revalidate one stable root for a sealed legacy generation."""
    _legacy_require_migration_id(migration_id)
    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)
    identity = get_rolling_history_owner_identity()
    mapping_key = namespace + "migration-root:v3:" + migration_id
    v2_mapping_key = namespace + "migration-root:v2:" + migration_id
    legacy_mapping_key = namespace + "migration-root:" + migration_id

    def operation(handle: RollingHistorySqlHandle):
        try:
            current_live_fence = _read_current_migration_live_fence(
                handle, identity.session_id,
            )
        except LegacySnapshotGenerationConflict:
            return _ConflictResult("Message source generation is unavailable")

        source = _read_legacy_migration_source_binding(handle, migration_id)
        if source is None:
            return _ConflictResult("Legacy snapshot migration source conflicts")
        source_binding, source_relation = source
        source_sha256 = _sha256(_canonical(source_binding))

        v1_prefix = _scope_prefix(scope)
        history_revision, history_head_sha256 = _read_v1_history_head(
            handle, scope, v1_prefix,
        )
        _verify_history_endpoint(
            handle, scope, v1_prefix, history_revision, history_head_sha256,
        )

        existing_mapping = _read_setting(handle, mapping_key)
        if existing_mapping is not None:
            root = _read_verified_migration_root_mapping(
                handle,
                scope,
                namespace,
                identity,
                migration_id,
                source_binding,
                existing_mapping,
                mapping_version=3,
                current_live_fence=current_live_fence,
            )
            mapping_value = _decode_canonical_object(
                existing_mapping, "rolling history v3 migration root mapping",
            )
            live_fence = _parse_rolling_history_v2_migration_live_fence(
                mapping_value["live_fence"],
            )
            source_generation = _bind_current_rolling_history_v2_migration_source_generation(
                scope, source_binding,
            )
            root_binding = _bind_verified_rolling_history_v2_migration_root(
                scope=scope,
                migration_id=migration_id,
                mapping_sha256=mapping_value["mapping_sha256"],
                live_fence=live_fence,
                source_generation=source_generation,
                branch=root.branch,
                revision=root.revision,
                head=root.head,
            )
            checkpoint, _ = _read_migration_checkpoint_head(
                handle, namespace, migration_id, scope, root_binding,
                source_generation,
            )
            _validate_rolling_history_v2_migration_live_append(
                checkpoint.live_fence, current_live_fence,
            )
            source_page = read_rolling_history_v2_migration_source_page(
                handle,
                scope=scope,
                root_binding=root_binding,
                source_generation=source_generation,
                checkpoint=checkpoint,
                current_live_fence=current_live_fence,
                max_events=1,
            )
            _validate_rolling_history_v2_migration_source_page(
                source_page, root_binding, source_generation,
            )
            if _read_current_migration_live_fence(handle, identity.session_id) != current_live_fence:
                raise ValueError("Rolling history migration live fence changed during verification")
            return root

        checkpoint_head_key = _migration_checkpoint_head_key(namespace, migration_id)
        if _read_setting(handle, checkpoint_head_key) is not None:
            raise ValueError("Rolling history v2 migration checkpoint exists without its root map")

        baseline_live_fence = current_live_fence
        old_mapping = _read_setting(handle, v2_mapping_key)
        if old_mapping is not None:
            root = _read_verified_migration_root_mapping(
                handle,
                scope,
                namespace,
                identity,
                migration_id,
                source_binding,
                old_mapping,
                mapping_version=2,
                current_live_fence=current_live_fence,
            )
            old_value = _decode_canonical_object(
                old_mapping, "rolling history v2 migration root mapping",
            )
            baseline_live_fence = _parse_rolling_history_v2_migration_live_fence(
                old_value["live_fence"],
            )
        else:
            legacy_mapping = _read_setting(handle, legacy_mapping_key)
            if legacy_mapping is not None:
                root = _read_verified_migration_root_mapping(
                    handle, scope, namespace, identity, migration_id,
                    source_binding, legacy_mapping, mapping_version=1,
                )
            else:
                genesis_chain = _genesis_chain(scope)
                if history_revision != 0 or history_head_sha256 != genesis_chain:
                    return _MigrationRequiredResult()
                idempotency_key = _migration_root_idempotency_key(migration_id)
                idempotency_id, key_sha256 = _idempotency_context(scope, idempotency_key)
                payload_sha256 = _migration_root_payload_sha256(
                    identity.canonical_namespace_digest, migration_id, source_sha256,
                )
                replay = _read_idempotent_result(
                    handle, scope, namespace, idempotency_id, key_sha256,
                    payload_sha256, "root",
                )
                if isinstance(replay, _ConflictResult):
                    return replay
                if replay is not _NO_IDEMPOTENCY:
                    return _ConflictResult("Migration root mapping is missing")
                branch_id = _migration_root_branch_id(
                    identity.canonical_namespace_digest, migration_id, source_sha256,
                )
                revision = _new_revision(
                    handle=handle,
                    namespace=namespace,
                    scope=scope,
                    branch_id=branch_id,
                    idempotency_id=idempotency_id,
                    idempotency_key_sha256=key_sha256,
                    operation="root",
                    representation="snapshot",
                    parent=None,
                    history_revision=0,
                    history_head_sha256=genesis_chain,
                    lineage_depth=0,
                    event_locators=(),
                )
                root = _RawRollingHistoryV2StoreResult(
                    _new_branch(scope, revision), revision, _new_head(revision), (),
                    revision.locator_index,
                )
                _persist_new_branch(handle, namespace, root)
                _persist_idempotency(
                    handle, scope, namespace, idempotency_id, key_sha256,
                    payload_sha256, "root", root,
                )

        _validate_rolling_history_v2_migration_live_append(
            baseline_live_fence, current_live_fence,
        )
        if _read_current_migration_live_fence(handle, identity.session_id) != current_live_fence:
            raise ValueError("Rolling history migration live fence changed during upgrade")
        mapping = _migration_root_mapping_value(
            identity.canonical_namespace_digest,
            migration_id,
            source_binding,
            source_relation.value,
            baseline_live_fence,
            root,
        )
        encoded_mapping = _canonical(mapping)
        _persist_immutable(handle, mapping_key, encoded_mapping)
        readback = _read_setting(handle, mapping_key)
        if readback != encoded_mapping:
            raise ValueError("Rolling history v3 migration root readback mismatch")
        verified_root = _read_verified_migration_root_mapping(
            handle, scope, namespace, identity, migration_id,
            source_binding, readback, mapping_version=3,
            current_live_fence=current_live_fence,
        )
        mapping_value = _decode_canonical_object(
            readback, "rolling history v3 migration root mapping",
        )
        source_generation = _bind_current_rolling_history_v2_migration_source_generation(
            scope, source_binding,
        )
        root_binding = _bind_verified_rolling_history_v2_migration_root(
            scope=scope,
            migration_id=migration_id,
            mapping_sha256=mapping_value["mapping_sha256"],
            live_fence=baseline_live_fence,
            source_generation=source_generation,
            branch=verified_root.branch,
            revision=verified_root.revision,
            head=verified_root.head,
        )
        checkpoint = new_rolling_history_v2_migration_checkpoint(
            scope=scope,
            root_binding=root_binding,
            source_generation=source_generation,
        )
        _persist_initial_migration_checkpoint(
            handle, namespace, migration_id, scope, root_binding,
            source_generation, checkpoint,
        )
        return verified_root

    return _finish_operation(binding, OperationMode.WRITE, operation)


def _publish_migration_source_page_to_index(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    migration_id: str,
    root: _RawRollingHistoryV2StoreResult,
    source_page: RollingHistoryV2MigrationSourcePage,
) -> None:
    if not source_page.event_locators:
        return
    record_key = (
        namespace + "migration-index-page:" + migration_id + ":"
        + source_page.page_sha256
    )
    existing_raw = _read_setting(handle, record_key)
    if existing_raw is not None:
        record = _decode_canonical_object(
            existing_raw, "rolling history v2 indexed migration page",
        )
        required = {
            "schema_version", "kind", "migration_id", "page_sha256",
            "branch_id", "revision_id", "revision_sha256", "end_sequence",
            "end_chain_sha256",
        }
        if (set(record) != required
                or record["schema_version"] != _LOCATOR_INDEX_SCHEMA_VERSION
                or record["kind"] != "rolling_history_v2_indexed_migration_page"
                or record["migration_id"] != migration_id
                or record["page_sha256"] != source_page.page_sha256
                or record["branch_id"] != root.branch.branch_id
                or record["end_sequence"] != source_page.end_sequence
                or record["end_chain_sha256"] != source_page.end_chain_sha256):
            raise ValueError("Rolling history v2 migration page index mapping mismatch")
        indexed_revision = _read_revision(handle, namespace, record["revision_id"])
        if (indexed_revision.revision_sha256 != record["revision_sha256"]
                or indexed_revision.schema_version != 3
                or indexed_revision.branch_id != root.branch.branch_id
                or indexed_revision.history_revision < source_page.end_sequence):
            raise ValueError("Rolling history v2 migration page revision is unavailable")
        return

    current = _read_indexed_head_for_page(
        handle, scope, namespace, root.branch.branch_id,
    )
    if isinstance(current, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    if ((current.revision.history_revision, current.revision.history_head_sha256)
            != (source_page.start_sequence, source_page.start_chain_sha256)):
        raise ValueError("Rolling history v2 migration page does not continue its indexed root")
    index = _append_locator_index(
        handle, namespace, current.locator_index, source_page.event_locators,
    )
    idempotency_key_sha256 = _sha256(_canonical({
        "kind": "rolling_history_v2_migration_page",
        "migration_id": migration_id,
        "page_sha256": source_page.page_sha256,
    }))
    idempotency_id = _rolling_history_v2_idempotency_id(
        _scope_digest(scope), scope.request_id, idempotency_key_sha256,
    )
    revision = _new_revision(
        handle=handle,
        namespace=namespace,
        scope=scope,
        branch_id=current.branch.branch_id,
        idempotency_id=idempotency_id,
        idempotency_key_sha256=idempotency_key_sha256,
        operation="migration_append",
        representation="delta",
        parent=current.revision,
        history_revision=source_page.end_sequence,
        history_head_sha256=source_page.end_chain_sha256,
        lineage_depth=0,
        event_locators=(),
        locator_index=index,
    )
    result = _RawRollingHistoryV2StoreResult(
        current.branch, revision, _new_head(revision), (), index,
    )
    _persist_transition(handle, namespace, result)
    record = {
        "schema_version": _LOCATOR_INDEX_SCHEMA_VERSION,
        "kind": "rolling_history_v2_indexed_migration_page",
        "migration_id": migration_id,
        "page_sha256": source_page.page_sha256,
        "branch_id": current.branch.branch_id,
        "revision_id": revision.revision_id,
        "revision_sha256": revision.revision_sha256,
        "end_sequence": source_page.end_sequence,
        "end_chain_sha256": source_page.end_chain_sha256,
    }
    _persist_immutable(handle, record_key, _canonical(record))


def continue_rolling_history_v2_migration(
    *,
    migration_id: str,
    expected_checkpoint_sha256: str,
    max_events: int,
) -> RollingHistoryV2MigrationSourcePageResult:
    """Read one bounded migration page and CAS its durable continuation state.

    The expected checkpoint makes retries after an unknown commit outcome
    idempotent: if the stored head is its direct child, the page is rebuilt and
    returned only after the same owner WRITE transaction revalidates it.
    """
    _legacy_require_migration_id(migration_id)
    if (type(expected_checkpoint_sha256) is not str
            or _HASH.fullmatch(expected_checkpoint_sha256) is None):
        raise ValueError("Invalid expected migration checkpoint")
    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)
    identity = get_rolling_history_owner_identity()
    mapping_key = namespace + "migration-root:v3:" + migration_id

    def operation(handle: RollingHistorySqlHandle):
        try:
            current_live_fence = _read_current_migration_live_fence(
                handle, identity.session_id,
            )
        except LegacySnapshotGenerationConflict:
            return _ConflictResult("Message source generation is unavailable")
        source = _read_legacy_migration_source_binding(handle, migration_id)
        if source is None:
            return _ConflictResult("Legacy snapshot migration source conflicts")
        source_binding, _source_relation = source
        raw_mapping = _read_setting(handle, mapping_key)
        if raw_mapping is None:
            return _MigrationRequiredResult()
        root = _read_verified_migration_root_mapping(
            handle,
            scope,
            namespace,
            identity,
            migration_id,
            source_binding,
            raw_mapping,
            mapping_version=3,
            current_live_fence=current_live_fence,
        )
        map_value = _decode_canonical_object(
            raw_mapping, "rolling history v3 migration root mapping",
        )
        root_fence = _parse_rolling_history_v2_migration_live_fence(
            map_value["live_fence"],
        )
        source_generation = _bind_current_rolling_history_v2_migration_source_generation(
            scope, source_binding,
        )
        root_binding = _bind_verified_rolling_history_v2_migration_root(
            scope=scope,
            migration_id=migration_id,
            mapping_sha256=map_value["mapping_sha256"],
            live_fence=root_fence,
            source_generation=source_generation,
            branch=root.branch,
            revision=root.revision,
            head=root.head,
        )
        checkpoint, state_raw = _read_migration_checkpoint_head(
            handle,
            namespace,
            migration_id,
            scope,
            root_binding,
            source_generation,
        )
        _validate_rolling_history_v2_migration_live_append(
            checkpoint.live_fence, current_live_fence,
        )

        v1_prefix = _scope_prefix(scope)
        current_sequence, current_chain = _read_v1_history_head(
            handle, scope, v1_prefix,
        )
        _verify_history_endpoint(
            handle, scope, v1_prefix, current_sequence, current_chain,
        )

        replaying_committed_child = (
            expected_checkpoint_sha256 != checkpoint.checkpoint_sha256
            and checkpoint.parent_checkpoint_sha256 == expected_checkpoint_sha256
        )
        if (expected_checkpoint_sha256 != checkpoint.checkpoint_sha256
                and not replaying_committed_child):
            return _ConflictResult("Migration checkpoint head conflict")
        if replaying_committed_child:
            prior = _read_migration_checkpoint_record(
                handle,
                namespace,
                migration_id,
                expected_checkpoint_sha256,
                scope,
                root_binding,
                source_generation,
            )
            event_count = checkpoint.sequence - prior.sequence
            if event_count < 1:
                raise ValueError("Committed migration checkpoint has no source page")
            page_fence = checkpoint.live_fence
            _validate_rolling_history_v2_migration_live_append(
                page_fence, current_live_fence,
            )
            source_page = read_rolling_history_v2_migration_source_page(
                handle,
                scope=scope,
                root_binding=root_binding,
                source_generation=source_generation,
                checkpoint=prior,
                current_live_fence=page_fence,
                max_events=event_count,
            )
            _validate_rolling_history_v2_migration_source_page(
                source_page, root_binding, source_generation,
            )
            if source_page.checkpoint != checkpoint:
                raise ValueError("Committed migration checkpoint page does not match its head")
        else:
            source_page = read_rolling_history_v2_migration_source_page(
                handle,
                scope=scope,
                root_binding=root_binding,
                source_generation=source_generation,
                checkpoint=checkpoint,
                current_live_fence=current_live_fence,
                max_events=max_events,
            )
            _validate_rolling_history_v2_migration_source_page(
                source_page, root_binding, source_generation,
            )
            if source_page.checkpoint.checkpoint_sha256 != checkpoint.checkpoint_sha256:
                _persist_migration_checkpoint_transition(
                    handle,
                    namespace,
                    migration_id,
                    scope,
                    root_binding,
                    source_generation,
                    checkpoint,
                    state_raw,
                    source_page.checkpoint,
                )

        _publish_migration_source_page_to_index(
            handle, scope, namespace, migration_id, root, source_page,
        )
        if _read_current_migration_live_fence(handle, identity.session_id) != current_live_fence:
            raise ValueError("Rolling history migration live fence changed during continuation")
        return source_page

    try:
        page = _finish_operation(binding, OperationMode.WRITE, operation)
    except RollingHistoryOwnerScopeError:
        return RollingHistoryV2MigrationSourcePageResult(
            outcome="outcome_unknown",
            source_page=None,
        )
    return RollingHistoryV2MigrationSourcePageResult(
        outcome="commit_confirmed",
        source_page=page,
    )


def _read_legacy_migration_source_binding(
    handle: RollingHistorySqlHandle,
    migration_id: str,
):
    identity = get_rolling_history_owner_identity()
    try:
        source_revision, current_count = _legacy_read_current_source(
            handle, identity,
        )
        generation = _legacy_read_generation(handle, migration_id)
        if generation is None:
            return None
        status = _legacy_status_from_row(
            generation,
            identity,
            migration_id,
            source_revision,
            current_count,
        )
        if not _legacy_is_sealed_generation(status):
            return None
        relation = _legacy_relate_generation_to_source_in_transaction(
            handle, migration_id,
        )
    except LegacySnapshotGenerationConflict:
        return None
    if relation not in (
        LegacySnapshotSourceRelation.EXACT_CURRENT,
        LegacySnapshotSourceRelation.APPEND_ONLY_PREFIX,
    ):
        return None
    return ({
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "generation": {
            "capture_chain_hash": status.capture_chain_hash,
            "capture_cursor": status.capture_cursor,
            "coverage_status": status.coverage_status,
            "migration_id": status.migration_id,
            "ordering_version": status.ordering_version,
            "publication_status": status.publication_status,
            "session_id": status.session_id,
            "source_message_revision": status.source_message_revision,
            "total_count": status.total_count,
        },
        "owner_delete_epoch": identity.delete_epoch,
        "owner_incarnation_id": identity.incarnation_id,
        "owner_principal_hash": identity.principal_hash,
    }, relation)


def _migration_root_idempotency_key(migration_id: str) -> str:
    return "rolling-history-v2-migration-root:" + migration_id


def _migration_root_payload_sha256(
    canonical_namespace_digest: str,
    migration_id: str,
    source_sha256: str,
) -> str:
    return _sha256(_canonical({
        "canonical_namespace_digest": canonical_namespace_digest,
        "migration_id": migration_id,
        "operation": "migration_root",
        "source_sha256": source_sha256,
    }))


def _migration_root_branch_id(
    canonical_namespace_digest: str,
    migration_id: str,
    source_sha256: str,
) -> str:
    return _sha256(_canonical({
        "canonical_namespace_digest": canonical_namespace_digest,
        "kind": "rolling_history_v2_migration_root_branch",
        "migration_id": migration_id,
        "schema_version": _STORE_SCHEMA_VERSION,
        "source_sha256": source_sha256,
    }))


def _migration_root_mapping_value(
    canonical_namespace_digest: str,
    migration_id: str,
    source_binding: dict,
    source_relation: str,
    live_fence: RollingHistoryV2MigrationLiveFence,
    root: _RawRollingHistoryV2StoreResult,
) -> dict:
    body = {
        "canonical_namespace_digest": canonical_namespace_digest,
        "kind": _MIGRATION_ROOT_MAP_V3_KIND,
        "live_fence": _rolling_history_v2_migration_live_fence_value(live_fence),
        "migration_id": migration_id,
        "root": {
            "branch_id": root.branch.branch_id,
            "head": _head_value(root.head),
            "revision_id": root.revision.revision_id,
            "revision_sha256": root.revision.revision_sha256,
        },
        "schema_version": _MIGRATION_ROOT_MAP_V3_SCHEMA_VERSION,
        "source_binding": source_binding,
        "source_relation": source_relation,
        "source_sha256": _sha256(_canonical(source_binding)),
    }
    return {**body, "mapping_sha256": _sha256(_canonical(body))}


def _read_verified_migration_root_mapping(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    migration_id: str,
    source_binding: dict,
    raw: str,
    *,
    mapping_version: int,
    current_live_fence: RollingHistoryV2MigrationLiveFence | None = None,
) -> _RawRollingHistoryV2StoreResult:
    if mapping_version not in (1, 2, 3):
        raise ValueError("Unsupported rolling history v2 migration root mapping version")
    value = _decode_canonical_object(raw, "rolling history v2 migration root mapping")
    required_v1 = {
        "canonical_namespace_digest", "kind", "mapping_sha256", "migration_id",
        "root", "schema_version", "source_binding", "source_relation",
        "source_sha256",
    }
    required_v2 = required_v1 | {"live_fence"}
    required = required_v1 if mapping_version == 1 else required_v2
    expected_schema_version = {
        1: _MIGRATION_ROOT_MAP_V1_SCHEMA_VERSION,
        2: _MIGRATION_ROOT_MAP_V2_SCHEMA_VERSION,
        3: _MIGRATION_ROOT_MAP_V3_SCHEMA_VERSION,
    }[mapping_version]
    expected_kind = {
        1: _MIGRATION_ROOT_MAP_V1_KIND,
        2: _MIGRATION_ROOT_MAP_V2_KIND,
        3: _MIGRATION_ROOT_MAP_V3_KIND,
    }[mapping_version]
    body = {key: item for key, item in value.items() if key != "mapping_sha256"}
    if (
        set(value) != required
        or type(value["schema_version"]) is not int
        or value["schema_version"] != expected_schema_version
        or value["kind"] != expected_kind
        or value["canonical_namespace_digest"] != identity.canonical_namespace_digest
        or value["migration_id"] != migration_id
        or value["source_binding"] != source_binding
        or type(value["source_relation"]) is not str
        or value["source_relation"] not in _MIGRATION_ROOT_MAP_RELATIONS
        or value["source_sha256"] != _sha256(_canonical(source_binding))
        or type(value["mapping_sha256"]) is not str
        or _HASH.fullmatch(value["mapping_sha256"]) is None
        or value["mapping_sha256"] != _sha256(_canonical(body))
    ):
        raise ValueError("Invalid rolling history v2 migration root mapping")

    live_fence = None
    if mapping_version in (2, 3):
        live_fence = _parse_rolling_history_v2_migration_live_fence(
            value["live_fence"],
        )
        if type(current_live_fence) is not RollingHistoryV2MigrationLiveFence:
            raise ValueError("Current rolling history v2 migration live fence is unavailable")
        _validate_rolling_history_v2_migration_live_append(
            live_fence, current_live_fence,
        )

    root_value = value["root"]
    if type(root_value) is not dict or set(root_value) != {
        "branch_id", "head", "revision_id", "revision_sha256",
    }:
        raise ValueError("Invalid rolling history v2 migration root reference")
    head = _decode_head(root_value["head"])
    source_sha256 = value["source_sha256"]
    expected_branch_id = _migration_root_branch_id(
        identity.canonical_namespace_digest, migration_id, source_sha256,
    )
    if (
        root_value["branch_id"] != expected_branch_id
        or head.branch_id != expected_branch_id
        or head.revision_id != root_value["revision_id"]
        or head.revision_sha256 != root_value["revision_sha256"]
        or head.history_revision != 0
        or head.history_head_sha256 != _genesis_chain(scope)
    ):
        raise ValueError("Rolling history v2 migration root reference mismatch")

    result = _read_result_for_head(handle, scope, namespace, head)
    if (
        result.branch.branch_id != expected_branch_id
        or result.revision.operation != "root"
        or result.revision.history_revision != 0
        or result.revision.history_head_sha256 != _genesis_chain(scope)
        or result.revision.event_locators
        or result.revision.revision_id != root_value["revision_id"]
        or result.revision.revision_sha256 != root_value["revision_sha256"]
    ):
        raise ValueError("Rolling history v2 migration root is unavailable")

    idempotency_key = _migration_root_idempotency_key(migration_id)
    root_scope = ContextEventScope(
        session_id=scope.session_id,
        request_id=result.revision.request_id,
        principal_hash=scope.principal_hash,
        session_epoch=scope.session_epoch,
    )
    idempotency_id, key_sha256 = _idempotency_context(root_scope, idempotency_key)
    if (
        result.revision.idempotency_id != idempotency_id
        or result.revision.idempotency_key_sha256 != key_sha256
    ):
        raise ValueError("Rolling history v2 migration root idempotency mismatch")
    payload_sha256 = _migration_root_payload_sha256(
        identity.canonical_namespace_digest, migration_id, source_sha256,
    )
    replay = _read_idempotent_result(
        handle,
        root_scope,
        namespace,
        idempotency_id,
        key_sha256,
        payload_sha256,
        "root",
    )
    if (
        replay is _NO_IDEMPOTENCY
        or isinstance(replay, (_ConflictResult, _MigrationRequiredResult))
        or replay != result
    ):
        raise ValueError("Rolling history v2 migration root idempotency is unavailable")

    if mapping_version in (2, 3):
        source_generation = _bind_current_rolling_history_v2_migration_source_generation(
            scope, source_binding,
        )
        _bind_verified_rolling_history_v2_migration_root(
            scope=scope,
            migration_id=migration_id,
            mapping_sha256=value["mapping_sha256"],
            live_fence=live_fence,
            source_generation=source_generation,
            branch=result.branch,
            revision=result.revision,
            head=result.head,
        )
    return result


def _migration_checkpoint_head_key(namespace: str, migration_id: str) -> str:
    return namespace + "migration-checkpoint:v3:" + migration_id + ":head"


def _migration_checkpoint_record_key(
    namespace: str,
    migration_id: str,
    checkpoint_sha256: str,
) -> str:
    return (
        namespace + "migration-checkpoint:v3:" + migration_id
        + ":record:" + checkpoint_sha256
    )


def _migration_checkpoint_record_value(
    checkpoint: RollingHistoryV2MigrationCheckpoint,
) -> str:
    checkpoint_value = {
        **_rolling_history_v2_migration_checkpoint_body(checkpoint),
        "checkpoint_sha256": checkpoint.checkpoint_sha256,
    }
    body = {
        "kind": _MIGRATION_CHECKPOINT_RECORD_KIND,
        "checkpoint": checkpoint_value,
    }
    return _canonical({**body, "record_sha256": _sha256(_canonical(body))})


def _migration_checkpoint_head_value(
    migration_id: str,
    root_binding,
    checkpoint: RollingHistoryV2MigrationCheckpoint,
) -> str:
    body = {
        "schema_version": _MIGRATION_CHECKPOINT_STATE_SCHEMA_VERSION,
        "kind": _MIGRATION_CHECKPOINT_STATE_KIND,
        "migration_id": migration_id,
        "root_mapping_sha256": root_binding.mapping_sha256,
        "checkpoint_sha256": checkpoint.checkpoint_sha256,
        "sequence": checkpoint.sequence,
        "chain_sha256": checkpoint.chain_sha256,
        "parent_checkpoint_sha256": checkpoint.parent_checkpoint_sha256,
        "live_fence": _rolling_history_v2_migration_live_fence_value(
            checkpoint.live_fence,
        ),
    }
    return _canonical({**body, "state_sha256": _sha256(_canonical(body))})


def _read_migration_checkpoint_record(
    handle: RollingHistorySqlHandle,
    namespace: str,
    migration_id: str,
    checkpoint_sha256: str,
    scope: ContextEventScope,
    root_binding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
) -> RollingHistoryV2MigrationCheckpoint:
    raw = _read_setting(
        handle,
        _migration_checkpoint_record_key(
            namespace, migration_id, checkpoint_sha256,
        ),
    )
    if raw is None:
        raise ValueError("Rolling history v2 migration checkpoint record is missing")
    value = _decode_canonical_object(raw, "rolling history v2 migration checkpoint")
    if (set(value) != {"kind", "checkpoint", "record_sha256"}
            or value["kind"] != _MIGRATION_CHECKPOINT_RECORD_KIND
            or type(value["checkpoint"]) is not dict
            or type(value["record_sha256"]) is not str
            or _HASH.fullmatch(value["record_sha256"]) is None):
        raise ValueError("Invalid rolling history v2 migration checkpoint record")
    body = {key: value[key] for key in ("kind", "checkpoint")}
    if value["record_sha256"] != _sha256(_canonical(body)):
        raise ValueError("Rolling history v2 migration checkpoint record hash mismatch")
    checkpoint = _bind_persisted_rolling_history_v2_migration_checkpoint(
        scope=scope,
        root_binding=root_binding,
        source_generation=source_generation,
        persisted_value=value["checkpoint"],
    )
    if checkpoint.checkpoint_sha256 != checkpoint_sha256:
        raise ValueError("Rolling history v2 migration checkpoint key mismatch")
    return checkpoint


def _read_migration_checkpoint_head(
    handle: RollingHistorySqlHandle,
    namespace: str,
    migration_id: str,
    scope: ContextEventScope,
    root_binding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
) -> tuple[RollingHistoryV2MigrationCheckpoint, str]:
    state_key = _migration_checkpoint_head_key(namespace, migration_id)
    raw = _read_setting(handle, state_key)
    if raw is None:
        raise ValueError("Rolling history v2 migration checkpoint head is missing")
    value = _decode_canonical_object(raw, "rolling history v2 migration checkpoint head")
    required = {
        "schema_version", "kind", "migration_id", "root_mapping_sha256",
        "checkpoint_sha256", "sequence", "chain_sha256",
        "parent_checkpoint_sha256", "live_fence", "state_sha256",
    }
    body = {key: item for key, item in value.items() if key != "state_sha256"}
    if (set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _MIGRATION_CHECKPOINT_STATE_SCHEMA_VERSION
            or value["kind"] != _MIGRATION_CHECKPOINT_STATE_KIND
            or value["migration_id"] != migration_id
            or value["root_mapping_sha256"] != root_binding.mapping_sha256
            or type(value["checkpoint_sha256"]) is not str
            or _HASH.fullmatch(value["checkpoint_sha256"]) is None
            or type(value["sequence"]) is not int or value["sequence"] < 0
            or type(value["chain_sha256"]) is not str
            or _HASH.fullmatch(value["chain_sha256"]) is None
            or (value["parent_checkpoint_sha256"] is not None
                and (type(value["parent_checkpoint_sha256"]) is not str
                     or _HASH.fullmatch(value["parent_checkpoint_sha256"]) is None))
            or type(value["state_sha256"]) is not str
            or value["state_sha256"] != _sha256(_canonical(body))
            or _canonical(value) != raw):
        raise ValueError("Invalid rolling history v2 migration checkpoint head")
    state_fence = _parse_rolling_history_v2_migration_live_fence(
        value["live_fence"],
    )
    checkpoint = _read_migration_checkpoint_record(
        handle,
        namespace,
        migration_id,
        value["checkpoint_sha256"],
        scope,
        root_binding,
        source_generation,
    )
    if (checkpoint.sequence != value["sequence"]
            or checkpoint.chain_sha256 != value["chain_sha256"]
            or checkpoint.parent_checkpoint_sha256 != value["parent_checkpoint_sha256"]
            or checkpoint.live_fence != state_fence):
        raise ValueError("Rolling history v2 migration checkpoint head mismatch")
    if checkpoint.parent_checkpoint_sha256 is not None:
        parent = _read_migration_checkpoint_record(
            handle,
            namespace,
            migration_id,
            checkpoint.parent_checkpoint_sha256,
            scope,
            root_binding,
            source_generation,
        )
        _validate_rolling_history_v2_migration_live_append(
            parent.live_fence, checkpoint.live_fence,
        )
        if (parent.sequence > checkpoint.sequence
                or (parent.sequence == checkpoint.sequence
                    and parent.chain_sha256 != checkpoint.chain_sha256)):
            raise ValueError("Rolling history v2 migration checkpoint parent mismatch")
    return checkpoint, raw


def _persist_initial_migration_checkpoint(
    handle: RollingHistorySqlHandle,
    namespace: str,
    migration_id: str,
    scope: ContextEventScope,
    root_binding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
    checkpoint: RollingHistoryV2MigrationCheckpoint,
) -> None:
    record_key = _migration_checkpoint_record_key(
        namespace, migration_id, checkpoint.checkpoint_sha256,
    )
    record_value = _migration_checkpoint_record_value(checkpoint)
    _persist_immutable(handle, record_key, record_value)
    if _read_setting(handle, record_key) != record_value:
        raise ValueError("Rolling history v2 migration checkpoint readback mismatch")
    state_key = _migration_checkpoint_head_key(namespace, migration_id)
    state_value = _migration_checkpoint_head_value(
        migration_id, root_binding, checkpoint,
    )
    if _read_setting(handle, state_key) is not None:
        raise ValueError("Rolling history v2 migration checkpoint head already exists")
    result = handle.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (state_key, state_value),
    )
    if type(result) is not int or result != 1:
        raise ValueError("Rolling history v2 migration checkpoint head CAS failed")
    persisted = _read_setting(handle, state_key)
    if persisted != state_value:
        raise ValueError("Rolling history v2 migration checkpoint head readback mismatch")
    verified, verified_raw = _read_migration_checkpoint_head(
        handle,
        namespace,
        migration_id,
        scope,
        root_binding,
        source_generation,
    )
    if verified != checkpoint or verified_raw != state_value:
        raise ValueError("Rolling history v2 migration checkpoint head verification failed")


def _persist_migration_checkpoint_transition(
    handle: RollingHistorySqlHandle,
    namespace: str,
    migration_id: str,
    scope: ContextEventScope,
    root_binding,
    source_generation: RollingHistoryV2MigrationSourceGeneration,
    expected_checkpoint: RollingHistoryV2MigrationCheckpoint,
    expected_state_raw: str,
    next_checkpoint: RollingHistoryV2MigrationCheckpoint,
) -> str:
    if (next_checkpoint.parent_checkpoint_sha256
            != expected_checkpoint.checkpoint_sha256):
        raise ValueError("Rolling history v2 migration checkpoint parent CAS mismatch")
    _validate_rolling_history_v2_migration_live_append(
        expected_checkpoint.live_fence, next_checkpoint.live_fence,
    )
    record_key = _migration_checkpoint_record_key(
        namespace, migration_id, next_checkpoint.checkpoint_sha256,
    )
    record_value = _migration_checkpoint_record_value(next_checkpoint)
    _persist_immutable(handle, record_key, record_value)
    if _read_setting(handle, record_key) != record_value:
        raise ValueError("Rolling history v2 migration checkpoint record readback mismatch")

    state_key = _migration_checkpoint_head_key(namespace, migration_id)
    current_checkpoint, current_state_raw = _read_migration_checkpoint_head(
        handle,
        namespace,
        migration_id,
        scope,
        root_binding,
        source_generation,
    )
    if (current_checkpoint != expected_checkpoint
            or current_state_raw != expected_state_raw):
        raise ValueError("Rolling history v2 migration checkpoint head CAS conflict")
    next_state_raw = _migration_checkpoint_head_value(
        migration_id, root_binding, next_checkpoint,
    )
    result = handle.execute(
        "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE key = ? AND value = ?",
        (next_state_raw, state_key, expected_state_raw),
    )
    if type(result) is not int or result != 1:
        raise ValueError("Rolling history v2 migration checkpoint head CAS failed")
    readback = _read_setting(handle, state_key)
    if readback != next_state_raw:
        raise ValueError("Rolling history v2 migration checkpoint head readback mismatch")
    verified, verified_raw = _read_migration_checkpoint_head(
        handle,
        namespace,
        migration_id,
        scope,
        root_binding,
        source_generation,
    )
    if verified != next_checkpoint or verified_raw != next_state_raw:
        raise ValueError("Rolling history v2 migration checkpoint head verification failed")
    return next_state_raw


def _legacy_message_blob_migration_prefix(
    namespace: str,
    migration_id: str,
    snapshot_id: str,
) -> str:
    return (
        namespace + "legacy-message-blob-migration:v1:"
        + migration_id + ":" + snapshot_id + ":"
    )


def _legacy_message_blob_source_fence_from_snapshot(state) -> RollingHistoryV2MigrationLiveFence:
    required_fields = (
        "source_generation", "source_message_count", "source_non_append_generation",
    )
    try:
        values = tuple(getattr(state, field) for field in required_fields)
    except AttributeError as exc:
        raise ValueError("Legacy Message BLOB snapshot fence is unavailable") from exc
    return _new_rolling_history_v2_migration_live_fence(*values)


def _legacy_message_blob_validate_snapshot_state(
    state,
    *,
    session_id: str,
    migration_id: str,
    snapshot_id: str,
    require_sealed: bool,
) -> dict:
    required = {
        "migration_id", "snapshot_id", "session_id", "source_generation",
        "source_message_count", "source_non_append_generation", "high_water_created_at",
        "high_water_message_id", "capture_cursor", "capture_message_id",
        "capture_body_offset", "capture_body_chain_sha256", "capture_chain_sha256", "status",
    }
    try:
        value = {field: getattr(state, field) for field in required}
    except AttributeError as exc:
        raise ValueError("Legacy Message BLOB snapshot state is unavailable") from exc
    _legacy_message_blob_source_fence_from_snapshot(state)
    if (value["migration_id"] != migration_id
            or value["snapshot_id"] != snapshot_id
            or value["session_id"] != session_id
            or type(value["capture_cursor"]) is not int
            or value["capture_cursor"] < 0
            or type(value["capture_body_offset"]) is not int
            or value["capture_body_offset"] < 0
            or type(value["capture_chain_sha256"]) is not str
            or _HASH.fullmatch(value["capture_chain_sha256"]) is None
            or type(value["capture_body_chain_sha256"]) is not str
            or _HASH.fullmatch(value["capture_body_chain_sha256"]) is None
            or value["status"] not in {"building", "sealed"}
            or (value["high_water_created_at"] is not None
                and type(value["high_water_created_at"]) is not str)
            or (value["high_water_message_id"] is not None
                and type(value["high_water_message_id"]) is not str)
            or (value["capture_message_id"] is not None
                and type(value["capture_message_id"]) is not str)):
        raise ValueError("Invalid legacy Message BLOB snapshot state")
    if require_sealed and (
        value["status"] != "sealed"
        or value["capture_cursor"] != value["source_message_count"]
        or value["capture_message_id"] is not None
        or value["capture_body_offset"] != 0
    ):
        raise ValueError("Legacy Message BLOB snapshot is not fully sealed")
    return value


def _legacy_message_blob_capture_head_key(prefix: str) -> str:
    return prefix + "capture-head"


def _legacy_message_blob_capture_head_value(
    migration_id: str,
    snapshot_id: str,
    session_id: str,
    *,
    cursor: int,
    message_id: str | None,
    body_offset: int,
    body_chain_sha256: str,
    capture_chain_sha256: str,
    status: str,
) -> str:
    body = {
        "schema_version": _LEGACY_MESSAGE_BLOB_MIGRATION_SCHEMA_VERSION,
        "kind": _LEGACY_MESSAGE_BLOB_CAPTURE_HEAD_KIND,
        "migration_id": migration_id,
        "snapshot_id": snapshot_id,
        "session_id": session_id,
        "cursor": cursor,
        "message_id": message_id,
        "body_offset": body_offset,
        "body_chain_sha256": body_chain_sha256,
        "capture_chain_sha256": capture_chain_sha256,
        "status": status,
    }
    return _canonical({**body, "state_sha256": _sha256(_canonical(body))})


def _legacy_message_blob_capture_page_value(receipt, *, max_rows: int, max_body_bytes: int) -> dict:
    row_identities = []
    for row in receipt.rows:
        row_identities.append({
            "sequence": row.sequence,
            "message_id": row.message_id,
            "row_sha256": row.row_hash,
            "status": row.status,
        })
    body = {
        "schema_version": _LEGACY_MESSAGE_BLOB_MIGRATION_SCHEMA_VERSION,
        "kind": _LEGACY_MESSAGE_BLOB_CAPTURE_PAGE_KIND,
        "migration_id": receipt.migration_id,
        "snapshot_id": receipt.snapshot_id,
        "request_cursor": receipt.request_cursor,
        "request_message_id": receipt.request_message_id,
        "request_body_offset": receipt.request_body_offset,
        "response_cursor": receipt.response_cursor,
        "response_message_id": receipt.response_message_id,
        "response_body_offset": receipt.response_body_offset,
        "response_body_chain_sha256": receipt.response_body_chain_sha256,
        "response_capture_chain_sha256": receipt.response_capture_chain_sha256,
        "row_count": receipt.row_count,
        "body_bytes_captured": receipt.body_bytes_captured,
        "status": receipt.status,
        "max_rows": max_rows,
        "max_body_bytes": max_body_bytes,
        "rows": row_identities,
    }
    return {**body, "page_sha256": _sha256(_canonical(body))}


def _legacy_message_blob_source_chain(
    previous_chain_sha256: str,
    sequence: int,
    row_sha256: str,
) -> str:
    if (type(previous_chain_sha256) is not str
            or _HASH.fullmatch(previous_chain_sha256) is None
            or type(sequence) is not int
            or not 0 <= sequence < (1 << 64)
            or type(row_sha256) is not str
            or _HASH.fullmatch(row_sha256) is None):
        raise ValueError("Invalid legacy Message snapshot source chain input")
    return _sha256(
        b"legacy-message-blob-snapshot-chain-v1\0"
        + bytes.fromhex(previous_chain_sha256)
        + sequence.to_bytes(8, "big")
        + bytes.fromhex(row_sha256)
    )


def _legacy_message_blob_source_row_key(prefix: str, sequence: int) -> str:
    return prefix + "source-row:" + _format_sequence(sequence)


def _legacy_message_blob_source_chain_key(prefix: str, sequence: int) -> str:
    return prefix + "source-chain:" + _format_sequence(sequence)


def _legacy_message_blob_source_chain_value(
    migration_id: str,
    snapshot_id: str,
    sequence: int,
    row_sha256: str,
    chain_sha256: str,
) -> str:
    body = {
        "schema_version": _LEGACY_MESSAGE_BLOB_MIGRATION_SCHEMA_VERSION,
        "kind": _LEGACY_MESSAGE_BLOB_SOURCE_CHAIN_KIND,
        "migration_id": migration_id,
        "snapshot_id": snapshot_id,
        "sequence": sequence,
        "row_sha256": row_sha256,
        "chain_sha256": chain_sha256,
    }
    return _canonical({**body, "record_sha256": _sha256(_canonical(body))})


def _legacy_message_blob_capture_head_matches(
    raw: str | None,
    *,
    migration_id: str,
    snapshot_id: str,
    session_id: str,
) -> dict | None:
    if raw is None:
        return None
    value = _decode_canonical_object(raw, "legacy Message BLOB migration capture head")
    required = {
        "schema_version", "kind", "migration_id", "snapshot_id", "session_id",
        "cursor", "message_id", "body_offset", "body_chain_sha256",
        "capture_chain_sha256", "status", "state_sha256",
    }
    body = {key: value[key] for key in value if key != "state_sha256"}
    if (set(value) != required
            or value["schema_version"] != _LEGACY_MESSAGE_BLOB_MIGRATION_SCHEMA_VERSION
            or value["kind"] != _LEGACY_MESSAGE_BLOB_CAPTURE_HEAD_KIND
            or value["migration_id"] != migration_id
            or value["snapshot_id"] != snapshot_id
            or value["session_id"] != session_id
            or type(value["cursor"]) is not int or value["cursor"] < 0
            or (value["message_id"] is not None and type(value["message_id"]) is not str)
            or type(value["body_offset"]) is not int or value["body_offset"] < 0
            or type(value["body_chain_sha256"]) is not str
            or _HASH.fullmatch(value["body_chain_sha256"]) is None
            or type(value["capture_chain_sha256"]) is not str
            or _HASH.fullmatch(value["capture_chain_sha256"]) is None
            or value["status"] not in {"building", "sealed"}
            or type(value["state_sha256"]) is not str
            or value["state_sha256"] != _sha256(_canonical(body))):
        raise ValueError("Invalid legacy Message BLOB migration capture head")
    return value


def _legacy_message_blob_write_capture_head(
    handle: RollingHistorySqlHandle,
    key: str,
    expected_raw: str | None,
    next_raw: str,
) -> None:
    if expected_raw is None:
        changed = handle.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, next_raw),
        )
    else:
        changed = handle.execute(
            "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE key = ? AND value = ?",
            (next_raw, key, expected_raw),
        )
    if type(changed) is not int or changed != 1 or _read_setting(handle, key) != next_raw:
        raise RollingHistoryV2Conflict("Legacy Message BLOB capture checkpoint CAS failed")


def capture_rolling_history_v2_legacy_message_snapshot_page(
    *,
    user: dict | None,
    session_id: str,
    expected_incarnation_id: str,
    migration_id: str,
    snapshot_id: str,
    expected_cursor: int,
    expected_message_id: str | None,
    expected_body_offset: int,
    expected_body_chain_sha256: str,
    expected_capture_chain_sha256: str,
    max_rows: int = _LEGACY_MESSAGE_BLOB_CAPTURE_ROWS,
    max_body_bytes: int = _LEGACY_MESSAGE_BLOB_CAPTURE_BODY_BYTES,
) -> RollingHistoryV2LegacyMessageSnapshotPageResult:
    """Capture one bounded legacy Message page and persist its source receipt.

    The snapshot API, live fence reader, capture receipt, and this module's
    resume records all use the same owner WRITE handle and transaction.
    """
    _legacy_require_migration_id(migration_id)
    _legacy_require_migration_id(snapshot_id)
    if (type(session_id) is not str or not session_id
            or type(expected_incarnation_id) is not str
            or _POINTER_OPERATION_ID.fullmatch(expected_incarnation_id) is None
            or type(expected_cursor) is not int or expected_cursor < 0
            or (expected_message_id is not None and type(expected_message_id) is not str)
            or type(expected_body_offset) is not int or expected_body_offset < 0
            or type(max_rows) is not int or not 1 <= max_rows <= _LEGACY_MESSAGE_BLOB_CAPTURE_ROWS
            or type(max_body_bytes) is not int
            or not 8192 <= max_body_bytes <= _LEGACY_MESSAGE_BLOB_CAPTURE_BODY_BYTES
            or type(expected_body_chain_sha256) is not str
            or _HASH.fullmatch(expected_body_chain_sha256) is None
            or type(expected_capture_chain_sha256) is not str
            or _HASH.fullmatch(expected_capture_chain_sha256) is None):
        raise ValueError("Invalid legacy Message BLOB snapshot capture request")

    def operation(handle: RollingHistorySqlHandle):
        from app.db.database import (
            begin_legacy_message_blob_snapshot_in_transaction,
            capture_legacy_message_blob_snapshot_page_in_transaction,
        )

        identity = get_rolling_history_owner_identity()
        if identity.session_id != session_id or identity.incarnation_id != expected_incarnation_id:
            raise RollingHistoryOwnerScopeError("Legacy Message migration owner is unavailable")
        scope = _current_scope(ScopeOrigin.LEGACY_MESSAGE_MIGRATION)
        namespace = _namespace(scope)
        prefix = _legacy_message_blob_migration_prefix(namespace, migration_id, snapshot_id)
        before = _read_current_migration_live_fence(handle, session_id)
        state = begin_legacy_message_blob_snapshot_in_transaction(
            handle,
            session_id=session_id,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
        )
        state_value = _legacy_message_blob_validate_snapshot_state(
            state,
            session_id=session_id,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            require_sealed=False,
        )
        snapshot_fence = _legacy_message_blob_source_fence_from_snapshot(state)
        _validate_rolling_history_v2_migration_live_append(snapshot_fence, before)

        page_key = prefix + "capture-page:" + _sha256(_canonical({
            "cursor": expected_cursor,
            "message_id": expected_message_id,
            "body_offset": expected_body_offset,
            "body_chain_sha256": expected_body_chain_sha256,
            "capture_chain_sha256": expected_capture_chain_sha256,
            "max_rows": max_rows,
            "max_body_bytes": max_body_bytes,
        }))
        existing_page_raw = _read_setting(handle, page_key)
        capture_head_key = _legacy_message_blob_capture_head_key(prefix)
        capture_head_raw = _read_setting(handle, capture_head_key)
        capture_head = _legacy_message_blob_capture_head_matches(
            capture_head_raw,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            session_id=session_id,
        )
        if capture_head is None:
            if (state_value["capture_cursor"] != 0
                    or state_value["capture_message_id"] is not None
                    or state_value["capture_body_offset"] != 0
                    or state_value["capture_body_chain_sha256"]
                    != _LEGACY_MESSAGE_BLOB_SOURCE_ZERO_CHAIN
                    or state_value["capture_chain_sha256"]
                    != _LEGACY_MESSAGE_BLOB_SOURCE_ZERO_CHAIN):
                raise ValueError(
                    "Legacy Message snapshot needs capture receipts from its beginning",
                )
            initial = _legacy_message_blob_capture_head_value(
                migration_id,
                snapshot_id,
                session_id,
                cursor=0,
                message_id=None,
                body_offset=0,
                body_chain_sha256=_LEGACY_MESSAGE_BLOB_SOURCE_ZERO_CHAIN,
                capture_chain_sha256=_LEGACY_MESSAGE_BLOB_SOURCE_ZERO_CHAIN,
                status="building",
            )
            _legacy_message_blob_write_capture_head(handle, capture_head_key, None, initial)
            capture_head_raw = initial
            capture_head = _legacy_message_blob_capture_head_matches(
                initial,
                migration_id=migration_id,
                snapshot_id=snapshot_id,
                session_id=session_id,
            )
        snapshot_position = (
            state_value["capture_cursor"],
            state_value["capture_message_id"],
            state_value["capture_body_offset"],
            state_value["capture_body_chain_sha256"],
            state_value["capture_chain_sha256"],
            state_value["status"],
        )
        capture_head_position = (
            capture_head["cursor"],
            capture_head["message_id"],
            capture_head["body_offset"],
            capture_head["body_chain_sha256"],
            capture_head["capture_chain_sha256"],
            capture_head["status"],
        )
        if snapshot_position != capture_head_position:
            raise ValueError("Legacy Message snapshot and capture checkpoint disagree")
        requested_state = (
            expected_cursor,
            expected_message_id,
            expected_body_offset,
            expected_body_chain_sha256,
            expected_capture_chain_sha256,
        )
        current_state = (
            capture_head["cursor"],
            capture_head["message_id"],
            capture_head["body_offset"],
            capture_head["body_chain_sha256"],
            capture_head["capture_chain_sha256"],
        )
        is_next_page = requested_state == current_state
        if not is_next_page and existing_page_raw is None:
            raise ValueError("Legacy Message snapshot capture checkpoint changed")
        if (not is_next_page
                and expected_cursor > 0
                and expected_capture_chain_sha256
                != _legacy_message_blob_read_source_chain(
                    handle, prefix, migration_id, snapshot_id, expected_cursor - 1,
                )):
            raise ValueError("Legacy Message snapshot replay chain does not match its prefix")

        receipt = capture_legacy_message_blob_snapshot_page_in_transaction(
            handle,
            session_id=session_id,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            expected_cursor=expected_cursor,
            expected_message_id=expected_message_id,
            expected_body_offset=expected_body_offset,
            expected_body_chain_sha256=expected_body_chain_sha256,
            expected_capture_chain_sha256=expected_capture_chain_sha256,
            max_rows=max_rows,
            max_body_bytes=max_body_bytes,
        )
        if (receipt.request_cursor != expected_cursor
                or receipt.request_message_id != expected_message_id
                or receipt.request_body_offset != expected_body_offset
                or type(receipt.response_cursor) is not int
                or receipt.response_cursor < expected_cursor
                or receipt.response_cursor > state_value["source_message_count"]
                or type(receipt.response_message_id) not in (str, type(None))
                or type(receipt.response_body_offset) is not int
                or receipt.response_body_offset < 0
                or type(receipt.response_body_chain_sha256) is not str
                or _HASH.fullmatch(receipt.response_body_chain_sha256) is None
                or type(receipt.response_capture_chain_sha256) is not str
                or _HASH.fullmatch(receipt.response_capture_chain_sha256) is None
                or type(receipt.row_count) is not int
                or type(receipt.rows) is not tuple
                or receipt.row_count != len(receipt.rows)
                or type(receipt.body_bytes_captured) is not int
                or receipt.body_bytes_captured < 0
                or receipt.status not in {"building", "sealed"}):
            raise ValueError("Legacy Message snapshot capture receipt is invalid")

        previous_chain = expected_capture_chain_sha256
        completed_rows = []
        active_rows = []
        active_row_seen = False
        for row in receipt.rows:
            if (row.migration_id != migration_id
                    or row.snapshot_id != snapshot_id
                    or type(row.sequence) is not int
                    or row.sequence < expected_cursor
                    or type(row.message_id) is not str
                    or not row.message_id
                    or type(row.role) is not str
                    or (row.created_at is not None and type(row.created_at) is not str)
                    or type(row.metadata_json) is not str
                    or len(row.metadata_json.encode("utf-8")) > 256 * 1024
                    or type(row.body_id) is not str
                    or type(row.body_byte_length) is not int
                    or row.body_byte_length < 0
                    or row.status not in {"sealed", "capturing"}):
                raise ValueError("Legacy Message snapshot row receipt is invalid")
            if row.status == "capturing":
                if (row.sequence != receipt.response_cursor
                        or row.row_hash is not None
                        or row.body_sha256 is not None
                        or active_row_seen):
                    raise ValueError("Legacy Message snapshot active row is inconsistent")
                active_row_seen = True
                active_rows.append(row)
                continue
            if active_row_seen:
                raise ValueError("Legacy Message snapshot row order is inconsistent")
            expected_sequence = expected_cursor + len(completed_rows)
            if (row.sequence != expected_sequence
                    or type(row.row_hash) is not str
                    or _HASH.fullmatch(row.row_hash) is None
                    or type(row.body_sha256) is not str
                    or _HASH.fullmatch(row.body_sha256) is None):
                raise ValueError("Legacy Message snapshot completed row is inconsistent")
            previous_chain = _legacy_message_blob_source_chain(
                previous_chain, row.sequence, row.row_hash,
            )
            row_value = asdict(row)
            _persist_immutable(
                handle,
                _legacy_message_blob_source_row_key(prefix, row.sequence),
                _canonical(row_value),
            )
            _persist_immutable(
                handle,
                _legacy_message_blob_source_chain_key(prefix, row.sequence),
                _legacy_message_blob_source_chain_value(
                    migration_id, snapshot_id, row.sequence, row.row_hash, previous_chain,
                ),
            )
            completed_rows.append(row)
        completed_count = receipt.response_cursor - expected_cursor
        if (completed_count != len(completed_rows)
                or receipt.row_count != len(completed_rows) + len(active_rows)
                or (active_rows and active_rows[0].message_id != receipt.response_message_id)
                or (active_rows
                    and receipt.response_body_offset > active_rows[0].body_byte_length)
                or (not active_rows and receipt.response_message_id is not None)
                or (not active_rows and receipt.response_body_offset != 0)
                or (receipt.status == "sealed"
                    and (receipt.response_message_id is not None
                         or receipt.response_body_offset != 0
                         or receipt.response_cursor != state_value["source_message_count"]))
                or previous_chain != receipt.response_capture_chain_sha256):
            raise ValueError("Legacy Message snapshot capture chain is discontinuous")

        page_value = _legacy_message_blob_capture_page_value(
            receipt, max_rows=max_rows, max_body_bytes=max_body_bytes,
        )
        encoded_page = _canonical(page_value)
        if existing_page_raw is not None and existing_page_raw != encoded_page:
            raise ValueError("Legacy Message snapshot capture receipt changed")
        _persist_immutable(handle, page_key, encoded_page)
        if _read_setting(handle, page_key) != encoded_page:
            raise ValueError("Legacy Message snapshot capture receipt readback mismatch")

        if is_next_page:
            next_head = _legacy_message_blob_capture_head_value(
                migration_id,
                snapshot_id,
                session_id,
                cursor=receipt.response_cursor,
                message_id=receipt.response_message_id,
                body_offset=receipt.response_body_offset,
                body_chain_sha256=receipt.response_body_chain_sha256,
                capture_chain_sha256=receipt.response_capture_chain_sha256,
                status=receipt.status,
            )
            _legacy_message_blob_write_capture_head(
                handle, capture_head_key, capture_head_raw, next_head,
            )

        after = _read_current_migration_live_fence(handle, session_id)
        if (state_value["status"] == "building"
                and (before != snapshot_fence or after != snapshot_fence)):
            raise ValueError("Legacy Message source fence changed during snapshot capture")
        _validate_rolling_history_v2_migration_live_append(snapshot_fence, after)
        state_after = begin_legacy_message_blob_snapshot_in_transaction(
            handle,
            session_id=session_id,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
        )
        state_after_value = _legacy_message_blob_validate_snapshot_state(
            state_after,
            session_id=session_id,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            require_sealed=False,
        )
        if _legacy_message_blob_source_fence_from_snapshot(state_after) != snapshot_fence:
            raise ValueError("Legacy Message snapshot source fence changed during capture")
        persisted_capture_head = _legacy_message_blob_capture_head_matches(
            _read_setting(handle, capture_head_key),
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            session_id=session_id,
        )
        if persisted_capture_head is None:
            raise ValueError("Legacy Message snapshot capture checkpoint is unavailable")
        persisted_snapshot_position = (
            state_after_value["capture_cursor"],
            state_after_value["capture_message_id"],
            state_after_value["capture_body_offset"],
            state_after_value["capture_body_chain_sha256"],
            state_after_value["capture_chain_sha256"],
            state_after_value["status"],
        )
        persisted_head_position = (
            persisted_capture_head["cursor"],
            persisted_capture_head["message_id"],
            persisted_capture_head["body_offset"],
            persisted_capture_head["body_chain_sha256"],
            persisted_capture_head["capture_chain_sha256"],
            persisted_capture_head["status"],
        )
        if persisted_snapshot_position != persisted_head_position:
            raise ValueError("Legacy Message snapshot capture checkpoint readback mismatch")
        if is_next_page and persisted_snapshot_position != (
                receipt.response_cursor,
                receipt.response_message_id,
                receipt.response_body_offset,
                receipt.response_body_chain_sha256,
                receipt.response_capture_chain_sha256,
                receipt.status,
        ):
            raise ValueError("Legacy Message snapshot page does not match its persisted state")
        return RollingHistoryV2LegacyMessageSnapshotPageResult(
            outcome="commit_confirmed",
            snapshot_state=state_after,
            page_receipt=receipt,
        )

    try:
        return run_legacy_message_migration_owner_write_operation(
            user, session_id, expected_incarnation_id, operation,
        )
    except RollingHistoryOwnerScopeError:
        return RollingHistoryV2LegacyMessageSnapshotPageResult(
            outcome="outcome_unknown",
            snapshot_state=None,
            page_receipt=None,
        )


def _legacy_message_blob_read_source_chain(
    handle: RollingHistorySqlHandle,
    prefix: str,
    migration_id: str,
    snapshot_id: str,
    sequence: int,
) -> str:
    raw = _read_setting(handle, _legacy_message_blob_source_chain_key(prefix, sequence))
    if raw is None:
        raise ValueError("Legacy Message snapshot source chain is unavailable")
    value = _decode_canonical_object(raw, "legacy Message snapshot source chain")
    required = {
        "schema_version", "kind", "migration_id", "snapshot_id", "sequence",
        "row_sha256", "chain_sha256", "record_sha256",
    }
    body = {key: value[key] for key in value if key != "record_sha256"}
    if (set(value) != required
            or value["schema_version"] != _LEGACY_MESSAGE_BLOB_MIGRATION_SCHEMA_VERSION
            or value["kind"] != _LEGACY_MESSAGE_BLOB_SOURCE_CHAIN_KIND
            or value["migration_id"] != migration_id
            or value["snapshot_id"] != snapshot_id
            or value["sequence"] != sequence
            or type(value["row_sha256"]) is not str or _HASH.fullmatch(value["row_sha256"]) is None
            or type(value["chain_sha256"]) is not str or _HASH.fullmatch(value["chain_sha256"]) is None
            or value["record_sha256"] != _sha256(_canonical(body))
            or raw != _canonical(value)):
        raise ValueError("Invalid legacy Message snapshot source chain record")
    return value["chain_sha256"]


def _legacy_message_blob_source_root_fields(state_value: dict) -> dict:
    """Return immutable source identity fields, excluding mutable fence counters."""
    return {
        "schema_version": _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION,
        "kind": _LEGACY_MESSAGE_BLOB_SNAPSHOT_ROOT_KIND,
        "migration_id": state_value["migration_id"],
        "snapshot_id": state_value["snapshot_id"],
        "session_id": state_value["session_id"],
        "ordering_version": "created_at_nulls_first_id_asc_v1",
        "high_water_created_at": state_value["high_water_created_at"],
        "high_water_message_id": state_value["high_water_message_id"],
        "capture_chain_sha256": state_value["capture_chain_sha256"],
    }


def _legacy_message_blob_source_fence_value(state_value: dict) -> dict:
    """Keep mutable live-source counters as independent snapshot evidence."""
    return {
        "message_source_generation": state_value["source_generation"],
        "message_count": state_value["source_message_count"],
        "non_append_generation": state_value["source_non_append_generation"],
    }


def _legacy_message_blob_root_key(prefix: str) -> str:
    return prefix + "root:v2"


def _legacy_message_blob_checkpoint_head_key(prefix: str) -> str:
    return prefix + "checkpoint-head:v2"


def _legacy_message_blob_checkpoint_record_key(prefix: str, checkpoint_sha256: str) -> str:
    return prefix + "checkpoint:v2:" + checkpoint_sha256


def _legacy_message_blob_source_id(
    migration_id: str,
    snapshot_id: str,
    source_root_sha256: str,
) -> str:
    return _sha256(_canonical({
        "kind": _LEGACY_MESSAGE_BLOB_MIGRATION_KIND,
        "migration_id": migration_id,
        "snapshot_id": snapshot_id,
        "source_root_sha256": source_root_sha256,
    }))


def _legacy_message_blob_read_source_row(
    handle: RollingHistorySqlHandle,
    prefix: str,
    migration_id: str,
    snapshot_id: str,
    sequence: int,
) -> dict:
    raw = _read_setting(handle, _legacy_message_blob_source_row_key(prefix, sequence))
    if raw is None:
        raise ValueError("Legacy Message snapshot source row receipt is unavailable")
    value = _decode_canonical_object(raw, "legacy Message snapshot source row receipt")
    required = {
        "migration_id", "snapshot_id", "sequence", "message_id", "role",
        "created_at", "metadata_json", "body_id", "body_byte_length",
        "body_sha256", "row_hash", "status",
    }
    if (set(value) != required
            or value["migration_id"] != migration_id
            or value["snapshot_id"] != snapshot_id
            or type(value["sequence"]) is not int or value["sequence"] != sequence
            or type(value["message_id"]) is not str or not value["message_id"]
            or len(value["message_id"]) > 512
            or type(value["role"]) is not str
            or (value["created_at"] is not None and type(value["created_at"]) is not str)
            or type(value["metadata_json"]) is not str
            or len(value["metadata_json"].encode("utf-8")) > 256 * 1024
            or type(value["body_id"]) is not str
            or _POINTER_OPERATION_ID.fullmatch(value["body_id"]) is None
            or type(value["body_byte_length"]) is not int
            or value["body_byte_length"] < 0
            or type(value["body_sha256"]) is not str
            or _HASH.fullmatch(value["body_sha256"]) is None
            or type(value["row_hash"]) is not str
            or _HASH.fullmatch(value["row_hash"]) is None
            or value["status"] != "sealed"
            or raw != _canonical(value)):
        raise ValueError("Invalid legacy Message snapshot source row receipt")
    return value


def _legacy_message_blob_source_rows_complete(
    handle: RollingHistorySqlHandle,
    prefix: str,
    state_value: dict,
) -> None:
    count = state_value["source_message_count"]
    previous_chain = _LEGACY_MESSAGE_BLOB_SOURCE_ZERO_CHAIN
    for sequence in range(count):
        row = _legacy_message_blob_read_source_row(
            handle,
            prefix,
            state_value["migration_id"],
            state_value["snapshot_id"],
            sequence,
        )
        previous_chain = _legacy_message_blob_source_chain(
            previous_chain, sequence, row["row_hash"],
        )
        if _legacy_message_blob_read_source_chain(
            handle,
            prefix,
            state_value["migration_id"],
            state_value["snapshot_id"],
            sequence,
        ) != previous_chain:
            raise ValueError("Legacy Message snapshot source prefix chain mismatch")
    if previous_chain != state_value["capture_chain_sha256"]:
        raise ValueError("Legacy Message snapshot source root chain mismatch")


def _legacy_message_blob_reject_old_migration_mapping(
    handle: RollingHistorySqlHandle,
    namespace: str,
    migration_id: str,
    prefix: str,
) -> None:
    old_keys = (
        namespace + "migration-root:v3:" + migration_id,
        namespace + "migration-root:v2:" + migration_id,
        namespace + "migration-root:" + migration_id,
        _migration_checkpoint_head_key(namespace, migration_id),
        prefix + "root",
        prefix + "checkpoint-head",
    )
    if any(_read_setting(handle, key) is not None for key in old_keys):
        raise RollingHistoryV2MigrationRequired()


def _legacy_message_blob_validate_root_mapping(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    root_raw: str,
    source_snapshot: dict,
    source_fence: RollingHistoryV2MigrationLiveFence,
    source_root_sha256: str,
    source_id: str,
) -> tuple[dict, _RawRollingHistoryV2StoreResult]:
    root = _decode_canonical_object(root_raw, "legacy Message BLOB migration root")
    required = {
        "schema_version", "kind", "migration_id", "snapshot_id",
        "canonical_namespace_digest", "source_root_sha256", "source_id",
        "source_snapshot", "source_fence", "branch_id", "root_revision_id", "root_revision_sha256",
        "root_head", "root_branch_sha256", "mapping_sha256",
    }
    body = {key: root[key] for key in root if key != "mapping_sha256"}
    if (set(root) != required
            or type(root["schema_version"]) is not int
            or root["schema_version"] != _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION
            or root["kind"] != _LEGACY_MESSAGE_BLOB_MIGRATION_ROOT_KIND
            or root["migration_id"] != source_snapshot["migration_id"]
            or root["snapshot_id"] != source_snapshot["snapshot_id"]
            or root["canonical_namespace_digest"]
            != get_rolling_history_owner_identity().canonical_namespace_digest
            or root["source_root_sha256"] != source_root_sha256
            or root["source_id"] != source_id
            or root["source_snapshot"] != source_snapshot
            or _parse_rolling_history_v2_migration_live_fence(
                root["source_fence"],
            ) != source_fence
            or any(type(root[key]) is not str or _HASH.fullmatch(root[key]) is None
                   for key in (
                       "branch_id", "root_revision_id", "root_revision_sha256",
                       "root_branch_sha256", "mapping_sha256",
                   ))
            or type(root["root_head"]) is not dict
            or root["mapping_sha256"] != _sha256(_canonical(body))
            or root_raw != _canonical(root)):
        raise ValueError("Legacy Message BLOB migration root mapping mismatch")
    revision = _read_revision(handle, namespace, root["root_revision_id"])
    branch_raw = _read_setting(handle, namespace + "branch:" + root["branch_id"])
    if branch_raw is None:
        raise ValueError("Legacy Message BLOB migration root branch is unavailable")
    branch = _decode_branch(branch_raw)
    root_head = _decode_head(root["root_head"])
    _validate_rolling_history_v2_branch(branch, revision)
    _validate_rolling_history_v2_head(root_head, revision)
    genesis_chain = _genesis_chain(scope)
    if (revision.operation != "root"
            or revision.history_revision != 0
            or revision.history_head_sha256 != genesis_chain
            or revision.event_locators
            or revision.branch_id != root["branch_id"]
            or revision.revision_sha256 != root["root_revision_sha256"]
            or branch.branch_sha256 != root["root_branch_sha256"]
            or root_head.branch_id != root["branch_id"]
            or root_head.revision_id != revision.revision_id
            or root_head.history_head_sha256 != genesis_chain):
        raise ValueError("Legacy Message BLOB migration root branch binding mismatch")
    current = _read_indexed_head_for_page(handle, scope, namespace, root["branch_id"])
    if isinstance(current, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    if current.branch != branch:
        raise ValueError("Legacy Message BLOB migration branch changed")
    return root, current


def _legacy_message_blob_root_mapping_value(
    *,
    identity,
    source_snapshot: dict,
    source_fence: RollingHistoryV2MigrationLiveFence,
    source_root_sha256: str,
    source_id: str,
    branch: RollingHistoryV2Branch,
    revision: RollingHistoryV2Revision,
    head: RollingHistoryV2Head,
) -> dict:
    body = {
        "schema_version": _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION,
        "kind": _LEGACY_MESSAGE_BLOB_MIGRATION_ROOT_KIND,
        "migration_id": source_snapshot["migration_id"],
        "snapshot_id": source_snapshot["snapshot_id"],
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "source_root_sha256": source_root_sha256,
        "source_id": source_id,
        "source_snapshot": source_snapshot,
        "source_fence": _rolling_history_v2_migration_live_fence_value(
            source_fence,
        ),
        "branch_id": branch.branch_id,
        "root_revision_id": revision.revision_id,
        "root_revision_sha256": revision.revision_sha256,
        "root_head": _head_value(head),
        "root_branch_sha256": branch.branch_sha256,
    }
    return {**body, "mapping_sha256": _sha256(_canonical(body))}


def _legacy_message_blob_checkpoint_body(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "checkpoint_sha256"}


def _legacy_message_blob_checkpoint_record_value(body: dict) -> dict:
    return {
        **body,
        "checkpoint_sha256": _sha256(_canonical(body)),
    }


def _legacy_message_blob_checkpoint_head_value(checkpoint: dict) -> str:
    body = {
        "schema_version": _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION,
        "kind": _LEGACY_MESSAGE_BLOB_CHECKPOINT_HEAD_KIND,
        "migration_id": checkpoint["migration_id"],
        "snapshot_id": checkpoint["snapshot_id"],
        "source_root_sha256": checkpoint["source_root_sha256"],
        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "parent_checkpoint_sha256": checkpoint["parent_checkpoint_sha256"],
        "next_source_sequence": checkpoint["next_source_sequence"],
        "source_chain_sha256": checkpoint["source_chain_sha256"],
        "live_fence": checkpoint["live_fence"],
        "v2_head_sha256": checkpoint["v2_head"]["head_sha256"],
    }
    return _canonical({**body, "state_sha256": _sha256(_canonical(body))})


def _legacy_message_blob_persist_initial_checkpoint(
    handle: RollingHistorySqlHandle,
    prefix: str,
    checkpoint: dict,
) -> str:
    checkpoint = _legacy_message_blob_checkpoint_record_value(checkpoint)
    record_value = _canonical(checkpoint)
    record_key = _legacy_message_blob_checkpoint_record_key(
        prefix, checkpoint["checkpoint_sha256"],
    )
    _persist_immutable(handle, record_key, record_value)
    if _read_setting(handle, record_key) != record_value:
        raise ValueError("Legacy Message migration checkpoint record readback mismatch")
    head_key = _legacy_message_blob_checkpoint_head_key(prefix)
    head_value = _legacy_message_blob_checkpoint_head_value(checkpoint)
    if _read_setting(handle, head_key) is not None:
        raise ValueError("Legacy Message migration checkpoint head already exists")
    _legacy_message_blob_write_capture_head(handle, head_key, None, head_value)
    if _read_setting(handle, head_key) != head_value:
        raise ValueError("Legacy Message migration checkpoint head readback mismatch")
    return head_value


def _legacy_message_blob_read_checkpoint_record(
    handle: RollingHistorySqlHandle,
    prefix: str,
    checkpoint_sha256: str,
    root: dict,
    *,
    verify_parent: bool = True,
) -> dict:
    raw = _read_setting(
        handle, _legacy_message_blob_checkpoint_record_key(prefix, checkpoint_sha256),
    )
    if raw is None:
        raise ValueError("Legacy Message migration checkpoint record is unavailable")
    value = _decode_canonical_object(raw, "legacy Message migration checkpoint")
    required = {
        "schema_version", "kind", "migration_id", "snapshot_id", "source_id",
        "source_root_sha256", "source_fence", "live_fence", "next_source_sequence",
        "source_chain_sha256", "parent_checkpoint_sha256", "source_page_sha256",
        "event_locators", "v2_head", "checkpoint_sha256",
    }
    body = _legacy_message_blob_checkpoint_body(value)
    if (set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION
            or value["kind"] != _LEGACY_MESSAGE_BLOB_CHECKPOINT_RECORD_KIND
            or value["migration_id"] != root["migration_id"]
            or value["snapshot_id"] != root["snapshot_id"]
            or value["source_id"] != root["source_id"]
            or value["source_root_sha256"] != root["source_root_sha256"]
            or value["checkpoint_sha256"] != checkpoint_sha256
            or checkpoint_sha256 != _sha256(_canonical(body))
            or type(value["source_fence"]) is not dict
            or value["source_fence"] != root["source_fence"]
            or type(value["live_fence"]) is not dict
            or type(value["next_source_sequence"]) is not int
            or not 0 <= value["next_source_sequence"] <= root["source_fence"]["message_count"]
            or type(value["source_chain_sha256"]) is not str
            or _HASH.fullmatch(value["source_chain_sha256"]) is None
            or (value["parent_checkpoint_sha256"] is not None
                and (type(value["parent_checkpoint_sha256"]) is not str
                     or _HASH.fullmatch(value["parent_checkpoint_sha256"]) is None))
            or (value["source_page_sha256"] is not None
                and (type(value["source_page_sha256"]) is not str
                     or _HASH.fullmatch(value["source_page_sha256"]) is None))
            or type(value["event_locators"]) is not list
            or type(value["v2_head"]) is not dict
            or raw != _canonical(value)):
        raise ValueError("Invalid legacy Message migration checkpoint record")
    source_fence = _parse_rolling_history_v2_migration_live_fence(value["source_fence"])
    live_fence = _parse_rolling_history_v2_migration_live_fence(value["live_fence"])
    _validate_rolling_history_v2_migration_live_append(source_fence, live_fence)
    head = _decode_head(value["v2_head"])
    if (head.branch_id != root["branch_id"]
            or type(value["event_locators"]) is not list
            or any(type(item) is not dict for item in value["event_locators"])):
        raise ValueError("Legacy Message migration checkpoint v2-head binding mismatch")
    next_sequence = value["next_source_sequence"]
    if next_sequence == 0:
        if value["source_chain_sha256"] != _LEGACY_MESSAGE_BLOB_SOURCE_ZERO_CHAIN:
            raise ValueError("Legacy Message migration checkpoint source genesis mismatch")
    elif _legacy_message_blob_read_source_chain(
        handle,
        prefix,
        root["migration_id"],
        root["snapshot_id"],
        next_sequence - 1,
    ) != value["source_chain_sha256"]:
        raise ValueError("Legacy Message migration checkpoint source prefix mismatch")
    if verify_parent and value["parent_checkpoint_sha256"] is not None:
        parent = _legacy_message_blob_read_checkpoint_record(
            handle, prefix, value["parent_checkpoint_sha256"], root,
            verify_parent=False,
        )
        parent_live = _parse_rolling_history_v2_migration_live_fence(parent["live_fence"])
        _validate_rolling_history_v2_migration_live_append(parent_live, live_fence)
        if (parent["next_source_sequence"] > next_sequence
                or (parent["next_source_sequence"] == next_sequence
                    and parent["source_chain_sha256"] != value["source_chain_sha256"])):
            raise ValueError("Legacy Message migration checkpoint parent prefix mismatch")
    return value


def _legacy_message_blob_read_checkpoint_head(
    handle: RollingHistorySqlHandle,
    prefix: str,
    root: dict,
) -> tuple[dict, str]:
    head_key = _legacy_message_blob_checkpoint_head_key(prefix)
    raw = _read_setting(handle, head_key)
    if raw is None:
        raise ValueError("Legacy Message migration checkpoint head is unavailable")
    value = _decode_canonical_object(raw, "legacy Message migration checkpoint head")
    required = {
        "schema_version", "kind", "migration_id", "snapshot_id", "source_root_sha256",
        "checkpoint_sha256", "parent_checkpoint_sha256", "next_source_sequence",
        "source_chain_sha256", "live_fence", "v2_head_sha256", "state_sha256",
    }
    body = {key: item for key, item in value.items() if key != "state_sha256"}
    if (set(value) != required
            or value["schema_version"] != _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION
            or value["kind"] != _LEGACY_MESSAGE_BLOB_CHECKPOINT_HEAD_KIND
            or value["migration_id"] != root["migration_id"]
            or value["snapshot_id"] != root["snapshot_id"]
            or value["source_root_sha256"] != root["source_root_sha256"]
            or type(value["checkpoint_sha256"]) is not str
            or _HASH.fullmatch(value["checkpoint_sha256"]) is None
            or type(value["next_source_sequence"]) is not int
            or type(value["source_chain_sha256"]) is not str
            or _HASH.fullmatch(value["source_chain_sha256"]) is None
            or type(value["v2_head_sha256"]) is not str
            or _HASH.fullmatch(value["v2_head_sha256"]) is None
            or value["state_sha256"] != _sha256(_canonical(body))
            or raw != _canonical(value)):
        raise ValueError("Invalid legacy Message migration checkpoint head")
    checkpoint = _legacy_message_blob_read_checkpoint_record(
        handle, prefix, value["checkpoint_sha256"], root,
    )
    if (_legacy_message_blob_checkpoint_head_value(checkpoint) != raw
            or checkpoint["next_source_sequence"] != value["next_source_sequence"]
            or checkpoint["source_chain_sha256"] != value["source_chain_sha256"]
            or checkpoint["v2_head"]["head_sha256"] != value["v2_head_sha256"]
            or checkpoint["parent_checkpoint_sha256"] != value["parent_checkpoint_sha256"]):
        raise ValueError("Legacy Message migration checkpoint head mismatch")
    return checkpoint, raw


def _legacy_message_blob_persist_checkpoint_transition(
    handle: RollingHistorySqlHandle,
    prefix: str,
    root: dict,
    expected_checkpoint: dict,
    expected_head_raw: str,
    next_checkpoint: dict,
) -> str:
    if next_checkpoint["parent_checkpoint_sha256"] != expected_checkpoint["checkpoint_sha256"]:
        raise ValueError("Legacy Message migration checkpoint parent CAS mismatch")
    expected_live = _parse_rolling_history_v2_migration_live_fence(
        expected_checkpoint["live_fence"],
    )
    next_live = _parse_rolling_history_v2_migration_live_fence(
        next_checkpoint["live_fence"],
    )
    _validate_rolling_history_v2_migration_live_append(expected_live, next_live)
    next_checkpoint = _legacy_message_blob_checkpoint_record_value(next_checkpoint)
    record_key = _legacy_message_blob_checkpoint_record_key(
        prefix, next_checkpoint["checkpoint_sha256"],
    )
    encoded_record = _canonical(next_checkpoint)
    _persist_immutable(handle, record_key, encoded_record)
    if _read_setting(handle, record_key) != encoded_record:
        raise ValueError("Legacy Message migration checkpoint readback mismatch")
    current, current_raw = _legacy_message_blob_read_checkpoint_head(handle, prefix, root)
    if (current != expected_checkpoint or current_raw != expected_head_raw):
        raise RollingHistoryV2Conflict("Legacy Message migration checkpoint head CAS conflict")
    next_head_raw = _legacy_message_blob_checkpoint_head_value(next_checkpoint)
    _legacy_message_blob_write_capture_head(
        handle,
        _legacy_message_blob_checkpoint_head_key(prefix),
        expected_head_raw,
        next_head_raw,
    )
    verified, verified_raw = _legacy_message_blob_read_checkpoint_head(handle, prefix, root)
    if verified != next_checkpoint or verified_raw != next_head_raw:
        raise ValueError("Legacy Message migration checkpoint head readback mismatch")
    return verified_raw


def _legacy_message_blob_capture_is_complete(
    handle: RollingHistorySqlHandle,
    prefix: str,
    state_value: dict,
) -> None:
    raw = _read_setting(handle, _legacy_message_blob_capture_head_key(prefix))
    capture_head = _legacy_message_blob_capture_head_matches(
        raw,
        migration_id=state_value["migration_id"],
        snapshot_id=state_value["snapshot_id"],
        session_id=state_value["session_id"],
    )
    if (capture_head is None
            or capture_head["status"] != "sealed"
            or capture_head["cursor"] != state_value["source_message_count"]
            or capture_head["message_id"] is not None
            or capture_head["body_offset"] != 0
            or capture_head["body_chain_sha256"]
            != state_value["capture_body_chain_sha256"]
            or capture_head["capture_chain_sha256"] != state_value["capture_chain_sha256"]
            or capture_head["status"] != state_value["status"]
            or state_value["capture_cursor"] != state_value["source_message_count"]):
        raise ValueError("Legacy Message snapshot capture receipt prefix is incomplete")
    _legacy_message_blob_source_rows_complete(handle, prefix, state_value)


def _legacy_message_blob_new_root_and_checkpoint(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    source_snapshot: dict,
    source_fence: RollingHistoryV2MigrationLiveFence,
    source_root_sha256: str,
    source_id: str,
    live_fence: RollingHistoryV2MigrationLiveFence,
    prefix: str,
) -> tuple[dict, dict, str]:
    identity = get_rolling_history_owner_identity()
    root_key = _legacy_message_blob_root_key(prefix)
    checkpoint_head_key = _legacy_message_blob_checkpoint_head_key(prefix)
    if (_read_setting(handle, root_key) is not None
            or _read_setting(handle, checkpoint_head_key) is not None):
        raise RollingHistoryV2Conflict("Legacy Message migration root or checkpoint already exists")
    _legacy_message_blob_reject_old_migration_mapping(
        handle, namespace, source_snapshot["migration_id"], prefix,
    )
    genesis_chain = _genesis_chain(scope)
    source_prefix = _scope_prefix(scope)
    history_sequence, history_chain = _owner_history_head(handle, scope, source_prefix)
    if history_sequence != 0 or history_chain != genesis_chain:
        raise RollingHistoryV2MigrationRequired()

    branch_id = _sha256(_canonical({
        "kind": "rolling_history_v2_legacy_message_blob_branch",
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "source_id": source_id,
    }))
    if (_read_setting(handle, namespace + "branch:" + branch_id) is not None
            or _read_setting(handle, namespace + "head:" + branch_id) is not None):
        raise ValueError("Legacy Message migration branch exists without its root mapping")
    root_key_sha256 = _sha256(_canonical({
        "kind": _LEGACY_MESSAGE_BLOB_MIGRATION_ROOT_KIND,
        "source_id": source_id,
    }))
    root_idempotency_id = _rolling_history_v2_idempotency_id(
        _scope_digest(scope), scope.request_id, root_key_sha256,
    )
    revision = _new_revision(
        handle=handle,
        namespace=namespace,
        scope=scope,
        branch_id=branch_id,
        idempotency_id=root_idempotency_id,
        idempotency_key_sha256=root_key_sha256,
        operation="root",
        representation="snapshot",
        parent=None,
        history_revision=0,
        history_head_sha256=genesis_chain,
        lineage_depth=0,
        event_locators=(),
    )
    branch = _new_branch(scope, revision)
    head = _new_head(revision)
    root_result = _RawRollingHistoryV2StoreResult(
        branch, revision, head, (), revision.locator_index,
    )
    _persist_new_branch(handle, namespace, root_result)
    root_payload_sha256 = _sha256(_canonical({
        "operation": _LEGACY_MESSAGE_BLOB_MIGRATION_ROOT_KIND,
        "source_id": source_id,
        "source_root_sha256": source_root_sha256,
    }))
    _persist_idempotency(
        handle,
        scope,
        namespace,
        root_idempotency_id,
        root_key_sha256,
        root_payload_sha256,
        "root",
        root_result,
    )
    root = _legacy_message_blob_root_mapping_value(
        identity=identity,
        source_snapshot=source_snapshot,
        source_fence=source_fence,
        source_root_sha256=source_root_sha256,
        source_id=source_id,
        branch=branch,
        revision=revision,
        head=head,
    )
    encoded_root = _canonical(root)
    _persist_immutable(handle, root_key, encoded_root)
    if _read_setting(handle, root_key) != encoded_root:
        raise ValueError("Legacy Message migration root mapping readback mismatch")
    root, current = _legacy_message_blob_validate_root_mapping(
        handle,
        scope,
        namespace,
        encoded_root,
        source_snapshot,
        source_fence,
        source_root_sha256,
        source_id,
    )
    if current.head != head or current.revision != revision:
        raise ValueError("Legacy Message migration root head readback mismatch")

    checkpoint_body = {
        "schema_version": _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION,
        "kind": _LEGACY_MESSAGE_BLOB_CHECKPOINT_RECORD_KIND,
        "migration_id": source_snapshot["migration_id"],
        "snapshot_id": source_snapshot["snapshot_id"],
        "source_id": source_id,
        "source_root_sha256": source_root_sha256,
        "source_fence": _rolling_history_v2_migration_live_fence_value(
            source_fence,
        ),
        "live_fence": _rolling_history_v2_migration_live_fence_value(live_fence),
        "next_source_sequence": 0,
        "source_chain_sha256": _LEGACY_MESSAGE_BLOB_SOURCE_ZERO_CHAIN,
        "parent_checkpoint_sha256": None,
        "source_page_sha256": None,
        "event_locators": [],
        "v2_head": _head_value(head),
    }
    record = _legacy_message_blob_checkpoint_record_value(checkpoint_body)
    encoded_record = _canonical(record)
    _persist_immutable(
        handle,
        _legacy_message_blob_checkpoint_record_key(prefix, record["checkpoint_sha256"]),
        encoded_record,
    )
    if _read_setting(
        handle,
        _legacy_message_blob_checkpoint_record_key(prefix, record["checkpoint_sha256"]),
    ) != encoded_record:
        raise ValueError("Legacy Message migration initial checkpoint readback mismatch")
    encoded_head = _legacy_message_blob_checkpoint_head_value(record)
    _legacy_message_blob_write_capture_head(handle, checkpoint_head_key, None, encoded_head)
    verified_checkpoint, verified_head = _legacy_message_blob_read_checkpoint_head(
        handle, prefix, root,
    )
    if verified_checkpoint != record or verified_head != encoded_head:
        raise ValueError("Legacy Message migration initial checkpoint verification failed")
    return root, verified_checkpoint, verified_head


def _legacy_message_blob_read_verified_body(
    handle: RollingHistorySqlHandle,
    *,
    session_id: str,
    migration_id: str,
    snapshot_id: str,
    row: dict,
) -> str:
    from app.db.database import read_legacy_message_blob_snapshot_body_chunk_in_transaction

    if row["body_byte_length"] > 1_048_576:
        raise ValueError("Legacy Message body exceeds the rolling-history event capacity")
    body = bytearray()
    offset = 0
    empty_read = row["body_byte_length"] == 0
    while offset < row["body_byte_length"] or empty_read:
        empty_read = False
        chunk = read_legacy_message_blob_snapshot_body_chunk_in_transaction(
            handle,
            session_id=session_id,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            message_id=row["message_id"],
            row_hash=row["row_hash"],
            byte_offset=offset,
            max_bytes=8192,
        )
        if (chunk.migration_id != migration_id
                or chunk.snapshot_id != snapshot_id
                or chunk.message_id != row["message_id"]
                or chunk.row_hash != row["row_hash"]
                or chunk.byte_offset != offset
                or type(chunk.chunk_bytes) is not bytes
                or type(chunk.chunk_sha256) is not str
                or chunk.chunk_sha256 != _sha256(chunk.chunk_bytes)
                or chunk.body_byte_length != row["body_byte_length"]
                or chunk.body_sha256 != row["body_sha256"]
                or chunk.next_offset != offset + len(chunk.chunk_bytes)
                or (row["body_byte_length"] > 0 and not chunk.chunk_bytes)
                or len(chunk.chunk_bytes) > 8192):
            raise ValueError("Legacy Message snapshot body chunk is discontinuous")
        body.extend(chunk.chunk_bytes)
        offset = chunk.next_offset
    body_bytes = bytes(body)
    if (len(body_bytes) != row["body_byte_length"]
            or _sha256(body_bytes) != row["body_sha256"]):
        raise ValueError("Legacy Message snapshot body seal does not match its source row")
    try:
        return body_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Legacy Message snapshot body is not valid UTF-8") from exc


def _legacy_message_blob_source_event_ids(
    source_id: str,
    row: dict,
) -> tuple[str, str]:
    event_source_id = _sha256(_canonical({
        "kind": "rolling_history_v2_legacy_message_blob_event_source_v2",
        "source_id": source_id,
        "sequence": row["sequence"],
        "message_id": row["message_id"],
        "row_sha256": row["row_hash"],
    }))
    event_id = _sha256(b"legacy-message-blob-event-v2\0" + bytes.fromhex(event_source_id))
    return event_source_id, event_id


def _legacy_message_blob_source_mapping_key(prefix: str, message_id: str) -> str:
    return prefix + "source-map:v2:" + _sha256(message_id)


def _legacy_message_blob_source_mapping_value(
    *,
    migration_id: str,
    snapshot_id: str,
    source_id: str,
    source_root_sha256: str,
    row: dict,
    source_chain_sha256: str,
    event_source_id: str,
    reference: RollingHistoryReference,
    event_manifest_sha256: str,
) -> str:
    body = {
        "schema_version": _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION,
        "kind": _LEGACY_MESSAGE_BLOB_SOURCE_MAPPING_KIND,
        "migration_id": migration_id,
        "snapshot_id": snapshot_id,
        "source_id": source_id,
        "source_root_sha256": source_root_sha256,
        "source_sequence": row["sequence"],
        "message_id": row["message_id"],
        "row_sha256": row["row_hash"],
        "source_chain_sha256": source_chain_sha256,
        "event_source_id": event_source_id,
        "locator": asdict(RollingHistoryV2EventLocator(
            event_id=reference.event_id,
            request_id=reference.request_id,
            sequence=reference.sequence,
            version=reference.version,
            content_sha256=reference.content_sha256,
            chain_sha256=reference.chain_sha256,
        )),
        "event_manifest_sha256": event_manifest_sha256,
    }
    return _canonical({**body, "record_sha256": _sha256(_canonical(body))})


def _legacy_message_blob_checkpoint_result(
    outcome: str,
    migration_id: str,
    snapshot_id: str,
    root: dict,
    checkpoint: dict,
    has_more: bool,
) -> RollingHistoryV2LegacyMessageMigrationResult:
    head = _decode_head(checkpoint["v2_head"])
    return RollingHistoryV2LegacyMessageMigrationResult(
        outcome=outcome,
        migration_id=migration_id,
        snapshot_id=snapshot_id,
        source_root_sha256=root["source_root_sha256"],
        next_source_sequence=checkpoint["next_source_sequence"],
        checkpoint_sha256=checkpoint["checkpoint_sha256"],
        branch_id=head.branch_id,
        revision_id=head.revision_id,
        head_sha256=head.head_sha256,
        has_more=has_more,
    )


def continue_rolling_history_v2_legacy_message_migration(
    *,
    user: dict | None,
    session_id: str,
    expected_incarnation_id: str,
    migration_id: str,
    snapshot_id: str,
    expected_checkpoint_sha256: str | None,
    max_events: int = _LEGACY_MESSAGE_BLOB_CAPTURE_ROWS,
) -> RollingHistoryV2LegacyMessageMigrationResult:
    """Create or resume a bounded v2 migration from one sealed BLOB snapshot.

    Snapshot validation, body reads, event writes, locator-index revisions,
    v2-head CAS, and migration checkpoint CAS all share one owner WRITE
    transaction. A missing expected checkpoint creates only the immutable root
    and genesis checkpoint; callers then resume with the returned checkpoint.
    """
    _legacy_require_migration_id(migration_id)
    _legacy_require_migration_id(snapshot_id)
    if (type(session_id) is not str or not session_id
            or type(expected_incarnation_id) is not str
            or _POINTER_OPERATION_ID.fullmatch(expected_incarnation_id) is None
            or (expected_checkpoint_sha256 is not None
                and (type(expected_checkpoint_sha256) is not str
                     or _HASH.fullmatch(expected_checkpoint_sha256) is None))
            or type(max_events) is not int
            or not 1 <= max_events <= _LEGACY_MESSAGE_BLOB_CAPTURE_ROWS):
        raise ValueError("Invalid legacy Message migration continuation request")

    def operation(handle: RollingHistorySqlHandle):
        from app.db.database import begin_legacy_message_blob_snapshot_in_transaction

        identity = get_rolling_history_owner_identity()
        if identity.session_id != session_id or identity.incarnation_id != expected_incarnation_id:
            raise RollingHistoryOwnerScopeError("Legacy Message migration owner is unavailable")
        scope = _current_scope(ScopeOrigin.LEGACY_MESSAGE_MIGRATION)
        namespace = _namespace(scope)
        session_namespace = _v2_session_namespace_prefix(scope)
        if _has_rows_outside_v2_namespace(handle, session_namespace, namespace):
            raise RollingHistoryV2MigrationRequired()
        prefix = _legacy_message_blob_migration_prefix(namespace, migration_id, snapshot_id)

        live_before = _read_current_migration_live_fence(handle, session_id)
        state = begin_legacy_message_blob_snapshot_in_transaction(
            handle,
            session_id=session_id,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
        )
        state_value = _legacy_message_blob_validate_snapshot_state(
            state,
            session_id=session_id,
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            require_sealed=True,
        )
        source_snapshot = _legacy_message_blob_source_root_fields(state_value)
        source_fence_value = _legacy_message_blob_source_fence_value(state_value)
        source_fence = _legacy_message_blob_source_fence_from_snapshot(state)
        source_root_sha256 = _sha256(_canonical(source_snapshot))
        source_id = _legacy_message_blob_source_id(
            migration_id, snapshot_id, source_root_sha256,
        )
        _validate_rolling_history_v2_migration_live_append(source_fence, live_before)
        _legacy_message_blob_capture_is_complete(handle, prefix, state_value)
        _legacy_message_blob_reject_old_migration_mapping(
            handle, namespace, migration_id, prefix,
        )

        root_key = _legacy_message_blob_root_key(prefix)
        root_raw = _read_setting(handle, root_key)
        if root_raw is None:
            if expected_checkpoint_sha256 is not None:
                raise RollingHistoryV2Conflict("Legacy Message migration root is not initialized")
            root, checkpoint, _head_raw = _legacy_message_blob_new_root_and_checkpoint(
                handle,
                scope,
                namespace,
                source_snapshot,
                source_fence,
                source_root_sha256,
                source_id,
                live_before,
                prefix,
            )
            live_after = _read_current_migration_live_fence(handle, session_id)
            if live_after != live_before:
                raise RollingHistoryV2Conflict("Message source changed during migration root creation")
            return _legacy_message_blob_checkpoint_result(
                "checkpoint_created",
                migration_id,
                snapshot_id,
                root,
                checkpoint,
                state_value["source_message_count"] > 0,
            )

        root, current_branch = _legacy_message_blob_validate_root_mapping(
            handle,
            scope,
            namespace,
            root_raw,
            source_snapshot,
            source_fence,
            source_root_sha256,
            source_id,
        )
        checkpoint, checkpoint_head_raw = _legacy_message_blob_read_checkpoint_head(
            handle, prefix, root,
        )
        checkpoint_sha256 = checkpoint["checkpoint_sha256"]
        if expected_checkpoint_sha256 is None:
            return _legacy_message_blob_checkpoint_result(
                "checkpoint_required",
                migration_id,
                snapshot_id,
                root,
                checkpoint,
                checkpoint["next_source_sequence"] < state_value["source_message_count"],
            )
        if expected_checkpoint_sha256 != checkpoint_sha256:
            if checkpoint["parent_checkpoint_sha256"] == expected_checkpoint_sha256:
                parent = _legacy_message_blob_read_checkpoint_record(
                    handle,
                    prefix,
                    expected_checkpoint_sha256,
                    root,
                    verify_parent=False,
                )
                parent_head = _decode_head(parent["v2_head"])
                current_head = checkpoint["v2_head"]
                revision = current_branch.revision
                if (revision.parent_revision_id != parent_head.revision_id
                        or revision.parent_revision_sha256 != parent_head.revision_sha256
                        or current_branch.head != _decode_head(current_head)):
                    raise RollingHistoryV2Conflict(
                        "Unknown migration commit does not match its checkpoint parent",
                    )
                return _legacy_message_blob_checkpoint_result(
                    "outcome_unknown",
                    migration_id,
                    snapshot_id,
                    root,
                    checkpoint,
                    checkpoint["next_source_sequence"] < state_value["source_message_count"],
                )
            raise RollingHistoryV2Conflict("Legacy Message migration checkpoint head conflict")

        live_checkpoint = _parse_rolling_history_v2_migration_live_fence(
            checkpoint["live_fence"],
        )
        _validate_rolling_history_v2_migration_live_append(live_checkpoint, live_before)
        expected_head = _decode_head(checkpoint["v2_head"])
        if (current_branch.head != expected_head
                or current_branch.revision.history_revision != expected_head.history_revision
                or current_branch.revision.history_head_sha256 != expected_head.history_head_sha256):
            raise RollingHistoryV2Conflict("Legacy Message migration v2 head conflicts with checkpoint")
        source_prefix = _scope_prefix(scope)
        history_sequence, history_chain = _owner_history_head(
            handle, scope, source_prefix,
        )
        if (history_sequence != expected_head.history_revision
                or history_chain != expected_head.history_head_sha256):
            raise RollingHistoryV2Conflict("Legacy Message migration source history prefix changed")

        start_sequence = checkpoint["next_source_sequence"]
        start_chain = checkpoint["source_chain_sha256"]
        if start_sequence >= state_value["source_message_count"]:
            live_after = _read_current_migration_live_fence(handle, session_id)
            if live_after != live_before:
                raise RollingHistoryV2Conflict("Message source changed during migration resume")
            return _legacy_message_blob_checkpoint_result(
                "complete",
                migration_id,
                snapshot_id,
                root,
                checkpoint,
                False,
            )

        appended_locators: list[RollingHistoryV2EventLocator] = []
        source_rows: list[dict] = []
        source_event_ids: list[str] = []
        source_chain_sha256 = start_chain
        for sequence in range(
            start_sequence,
            min(state_value["source_message_count"], start_sequence + max_events),
        ):
            row = _legacy_message_blob_read_source_row(
                handle, prefix, migration_id, snapshot_id, sequence,
            )
            source_chain_sha256 = _legacy_message_blob_source_chain(
                source_chain_sha256, sequence, row["row_hash"],
            )
            if _legacy_message_blob_read_source_chain(
                handle, prefix, migration_id, snapshot_id, sequence,
            ) != source_chain_sha256:
                raise ValueError("Legacy Message migration source prefix chain mismatch")
            body_text = _legacy_message_blob_read_verified_body(
                handle,
                session_id=session_id,
                migration_id=migration_id,
                snapshot_id=snapshot_id,
                row=row,
            )
            event_source_id, event_id = _legacy_message_blob_source_event_ids(source_id, row)
            source_mapping_key = _legacy_message_blob_source_mapping_key(prefix, row["message_id"])
            if _read_setting(handle, source_mapping_key) is not None:
                raise RollingHistoryV2Conflict("Legacy Message source ID already has a migration mapping")
            reference = _append_rolling_history_legacy_message_snapshot_event_in_transaction(
                handle,
                scope,
                row_metadata=row,
                body_text=body_text,
                expected_incarnation_id=identity.incarnation_id,
                source_root_sha256=source_root_sha256,
                source_chain_sha256=source_chain_sha256,
                idempotency_id=event_source_id,
                event_id=event_id,
            )
            if (reference.version != 3
                    or reference.sequence != expected_head.history_revision + len(appended_locators) + 1
                    or reference.request_id != scope.request_id):
                raise ValueError("Legacy Message migration body event locator is invalid")
            manifest_raw = _read_setting(
                handle, _event_manifest_key(source_prefix, reference.event_id),
            )
            if manifest_raw is None:
                raise ValueError("Legacy Message migration event manifest is unavailable")
            manifest = _validate_body_backed_event_manifest(
                manifest_raw, scope, reference.sequence,
            )
            source_metadata = manifest["source_metadata"]
            if (source_metadata["source_kind"] != "legacy_message_blob_snapshot_v1"
                    or source_metadata["source_root_sha256"] != source_root_sha256
                    or source_metadata["source_sequence"] != sequence
                    or source_metadata["row_sha256"] != row["row_hash"]
                    or source_metadata["source_chain_sha256"] != source_chain_sha256):
                raise ValueError("Legacy Message migration event source binding mismatch")
            locator = RollingHistoryV2EventLocator(
                event_id=reference.event_id,
                request_id=reference.request_id,
                sequence=reference.sequence,
                version=reference.version,
                content_sha256=reference.content_sha256,
                chain_sha256=reference.chain_sha256,
            )
            mapping_value = _legacy_message_blob_source_mapping_value(
                migration_id=migration_id,
                snapshot_id=snapshot_id,
                source_id=source_id,
                source_root_sha256=source_root_sha256,
                row=row,
                source_chain_sha256=source_chain_sha256,
                event_source_id=event_source_id,
                reference=reference,
                event_manifest_sha256=_sha256(manifest_raw),
            )
            _persist_immutable(handle, source_mapping_key, mapping_value)
            if _read_setting(handle, source_mapping_key) != mapping_value:
                raise ValueError("Legacy Message migration source mapping readback mismatch")
            source_rows.append(row)
            source_event_ids.append(event_source_id)
            appended_locators.append(locator)

        current_history_sequence, current_history_chain = _owner_history_head(
            handle, scope, source_prefix,
        )
        if (current_history_sequence != appended_locators[-1].sequence
                or current_history_chain != appended_locators[-1].chain_sha256):
            raise ValueError("Legacy Message migration event source head readback mismatch")

        locator_index = _append_locator_index(
            handle,
            namespace,
            current_branch.locator_index,
            tuple(appended_locators),
        )
        end_sequence = start_sequence + len(source_rows)
        page_sha256 = _sha256(_canonical({
            "kind": _LEGACY_MESSAGE_BLOB_MIGRATION_KIND,
            "migration_id": migration_id,
            "snapshot_id": snapshot_id,
            "source_id": source_id,
            "source_root_sha256": source_root_sha256,
            "start_sequence": start_sequence,
            "start_chain_sha256": start_chain,
            "end_sequence": end_sequence,
            "end_chain_sha256": source_chain_sha256,
            "rows": [
                {
                    "sequence": row["sequence"],
                    "message_id": row["message_id"],
                    "row_sha256": row["row_hash"],
                    "event_source_id": source_event_ids[offset],
                    "locator": asdict(appended_locators[offset]),
                }
                for offset, row in enumerate(source_rows)
            ],
        }))
        idempotency_key_sha256 = _sha256(_canonical({
            "kind": _LEGACY_MESSAGE_BLOB_MIGRATION_KIND,
            "migration_id": migration_id,
            "snapshot_id": snapshot_id,
            "source_root_sha256": source_root_sha256,
            "prior_checkpoint_sha256": checkpoint_sha256,
            "source_page_sha256": page_sha256,
        }))
        revision_idempotency_id = _rolling_history_v2_idempotency_id(
            _scope_digest(scope), scope.request_id, idempotency_key_sha256,
        )
        revision = _new_revision(
            handle=handle,
            namespace=namespace,
            scope=scope,
            branch_id=root["branch_id"],
            idempotency_id=revision_idempotency_id,
            idempotency_key_sha256=idempotency_key_sha256,
            operation="migration_append",
            representation="delta",
            parent=current_branch.revision,
            history_revision=current_history_sequence,
            history_head_sha256=current_history_chain,
            lineage_depth=0,
            event_locators=(),
            locator_index=locator_index,
        )
        next_head = _new_head(revision)
        result = _RawRollingHistoryV2StoreResult(
            current_branch.branch, revision, next_head, (), locator_index,
        )
        _persist_transition_with_head_cas(
            handle, namespace, current_branch.head, result,
        )
        _persist_idempotency(
            handle,
            scope,
            namespace,
            revision_idempotency_id,
            idempotency_key_sha256,
            _sha256(_canonical({
                "operation": "legacy_message_blob_migration_append",
                "source_page_sha256": page_sha256,
            })),
            "migration_append",
            result,
        )
        indexed_after = _read_indexed_head_for_page(
            handle, scope, namespace, root["branch_id"],
        )
        if (isinstance(indexed_after, _MigrationRequiredResult)
                or indexed_after.revision != revision
                or indexed_after.head != next_head
                or indexed_after.locator_index != locator_index):
            raise RollingHistoryV2Conflict("Legacy Message migration v2 head readback mismatch")

        next_checkpoint_body = {
            "schema_version": _LEGACY_MESSAGE_BLOB_IDENTITY_SCHEMA_VERSION,
            "kind": _LEGACY_MESSAGE_BLOB_CHECKPOINT_RECORD_KIND,
            "migration_id": migration_id,
            "snapshot_id": snapshot_id,
            "source_id": source_id,
            "source_root_sha256": source_root_sha256,
            "source_fence": source_fence_value,
            "live_fence": _rolling_history_v2_migration_live_fence_value(live_before),
            "next_source_sequence": end_sequence,
            "source_chain_sha256": source_chain_sha256,
            "parent_checkpoint_sha256": checkpoint_sha256,
            "source_page_sha256": page_sha256,
            "event_locators": [asdict(locator) for locator in appended_locators],
            "v2_head": _head_value(next_head),
        }
        encoded_next_head = _legacy_message_blob_persist_checkpoint_transition(
            handle,
            prefix,
            root,
            checkpoint,
            checkpoint_head_raw,
            next_checkpoint_body,
        )
        next_checkpoint, checked_head_raw = _legacy_message_blob_read_checkpoint_head(
            handle, prefix, root,
        )
        live_after = _read_current_migration_live_fence(handle, session_id)
        _validate_rolling_history_v2_migration_live_append(
            _parse_rolling_history_v2_migration_live_fence(next_checkpoint["live_fence"]),
            live_after,
        )
        if checked_head_raw != encoded_next_head or live_after != live_before:
            raise RollingHistoryV2Conflict("Message source or migration checkpoint changed before commit")
        return _legacy_message_blob_checkpoint_result(
            "commit_confirmed",
            migration_id,
            snapshot_id,
            root,
            next_checkpoint,
            end_sequence < state_value["source_message_count"],
        )

    try:
        return run_legacy_message_migration_owner_write_operation(
            user, session_id, expected_incarnation_id, operation,
        )
    except RollingHistoryOwnerScopeError:
        return RollingHistoryV2LegacyMessageMigrationResult(
            outcome="outcome_unknown",
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            source_root_sha256=None,
            next_source_sequence=None,
            checkpoint_sha256=None,
            branch_id=None,
            revision_id=None,
            head_sha256=None,
            has_more=None,
        )


def fork_rolling_history_v2_branch(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    idempotency_key: str,
) -> RollingHistoryV2StoreResult:
    """Create a branch that inherits or snapshots the expected parent prefix."""
    _validate_bound_head_token(expected_head)
    binding = _current_binding()
    scope = binding.scope
    _require_head_scope(expected_head, scope)
    idempotency_id, key_sha256 = _idempotency_context(scope, idempotency_key)
    payload_sha256 = _sha256(_canonical({
        "operation": "fork",
        "expected_head": asdict(expected_head.head),
    }))
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle):
        replay = _read_idempotent_result(
            handle, scope, namespace, idempotency_id, key_sha256,
            payload_sha256, "fork",
        )
        if replay is not _NO_IDEMPOTENCY:
            return replay

        parent_result = _read_indexed_head_for_page(
            handle, scope, namespace, expected_head.head.branch_id,
        )
        if isinstance(parent_result, _MigrationRequiredResult):
            return parent_result
        if parent_result.head != expected_head.head:
            return _ConflictResult("Rolling history v2 expected-head conflict")
        parent = parent_result.revision
        branch_id = _sha256(_canonical({
            "schema_version": _STORE_SCHEMA_VERSION,
            "kind": "rolling_history_v2_fork_branch",
            "scope_sha256": _scope_digest(scope),
            "request_id": scope.request_id,
            "idempotency_id": idempotency_id,
            "parent_revision_id": parent.revision_id,
        }))
        representation = "inherit"
        revision = _new_revision(
            handle=handle,
            namespace=namespace,
            scope=scope,
            branch_id=branch_id,
            idempotency_id=idempotency_id,
            idempotency_key_sha256=key_sha256,
            operation="fork",
            representation=representation,
            parent=parent,
            history_revision=parent.history_revision,
            history_head_sha256=parent.history_head_sha256,
            lineage_depth=0,
            event_locators=(),
            locator_index=parent_result.locator_index,
        )
        branch = _new_branch(scope, revision)
        head = _new_head(revision)
        result = _RawRollingHistoryV2StoreResult(
            branch, revision, head, (), revision.locator_index,
        )
        _persist_new_branch(handle, namespace, result)
        _persist_idempotency(
            handle, scope, namespace, idempotency_id, key_sha256,
            payload_sha256, "fork", result,
        )
        return result

    return _finish_operation(
        binding, OperationMode.WRITE, operation, expected_head=expected_head,
    )


def cut_rolling_history_v2_branch(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    retain_count: int,
    idempotency_key: str,
) -> RollingHistoryV2StoreResult:
    """Keep exactly the first ``retain_count`` visible locators on a branch."""
    _validate_bound_head_token(expected_head)
    if type(retain_count) is not int or retain_count < 0:
        raise ValueError("Invalid rolling history v2 cut length")
    binding = _current_binding()
    scope = binding.scope
    _require_head_scope(expected_head, scope)
    idempotency_id, key_sha256 = _idempotency_context(scope, idempotency_key)
    payload_sha256 = _sha256(_canonical({
        "operation": "cut",
        "expected_head": asdict(expected_head.head),
        "retain_count": retain_count,
    }))
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle):
        replay = _read_idempotent_result(
            handle, scope, namespace, idempotency_id, key_sha256,
            payload_sha256, "cut",
        )
        if replay is not _NO_IDEMPOTENCY:
            return replay

        current = _read_indexed_head_for_page(
            handle, scope, namespace, expected_head.head.branch_id,
        )
        if isinstance(current, _MigrationRequiredResult):
            return current
        if current.head != expected_head.head:
            return _ConflictResult("Rolling history v2 expected-head conflict")
        current_index = current.locator_index
        _validate_rolling_history_v2_locator_index(current_index)
        if retain_count > current_index.locator_count:
            raise ValueError("Rolling history v2 cut exceeds the visible prefix")
        cut_index = _truncate_locator_index(
            handle, namespace, current_index, retain_count,
        )
        revision = _new_revision(
            handle=handle,
            namespace=namespace,
            scope=scope,
            branch_id=current.branch.branch_id,
            idempotency_id=idempotency_id,
            idempotency_key_sha256=key_sha256,
            operation="cut",
            representation="snapshot",
            parent=current.revision,
            history_revision=current.revision.history_revision,
            history_head_sha256=current.revision.history_head_sha256,
            lineage_depth=0,
            event_locators=(),
            locator_index=cut_index,
        )
        head = _new_head(revision)
        result = _RawRollingHistoryV2StoreResult(
            current.branch, revision, head, (), cut_index,
        )
        _persist_transition(handle, namespace, result)
        _persist_idempotency(
            handle, scope, namespace, idempotency_id, key_sha256,
            payload_sha256, "cut", result,
        )
        return result

    return _finish_operation(
        binding, OperationMode.WRITE, operation, expected_head=expected_head,
    )


def append_rolling_history_v2_body_events_in_transaction(
    handle: RollingHistorySqlHandle,
    session_id: str,
    scope: ContextEventScope,
    event: RollingHistoryBodyEventInput,
    *,
    idempotency_key: str,
    write_source_message: Callable[
        [RollingHistorySqlHandle, str, RollingHistoryBodyEventInput],
        RollingHistorySourceMessageWriteReceipt,
    ],
) -> RollingHistoryV2BodyAppendResult:
    """Write one Chat source message and publish its verified body/index atomically.

    The supplied handle is already owner-validated by the enclosing WRITE
    callback. The source CRUD callback must use that exact handle/session, write
    no more than the supplied message, and return a typed receipt. This helper
    never begins, commits, or rolls back a transaction.
    """
    _require_owner_history_handle(handle, scope, OperationMode.WRITE)
    if (type(session_id) is not str or session_id != scope.session_id
            or type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid rolling history v2 body append")
    if type(event) is not RollingHistoryBodyEventInput or not callable(write_source_message):
        raise ValueError("Invalid rolling history source message append")
    payload, encoded_payload = _encode_body_event_input(event)
    message_event_sha256 = _message_source_event_sha256(event)
    source_payload_sha256 = _message_source_payload_sha256(event)
    store_idempotency_id, key_sha256 = _idempotency_context(scope, idempotency_key)
    payload_sha256 = _sha256(_canonical({
        "operation": "append_body",
        "message_id": event.message_id,
        "message_event_sha256": message_event_sha256,
        "source_payload_sha256": source_payload_sha256,
        "event_payload_sha256": _sha256(encoded_payload),
    }))
    identity = get_rolling_history_owner_identity()
    namespace = _namespace(scope)

    # Replay the exact rolling append before invoking Message CRUD. The owner
    # fence still brackets this read-only retry and must not move.
    source_fence_before = _read_current_migration_live_fence(handle, session_id)
    replay = _read_idempotent_result(
        handle, scope, namespace, store_idempotency_id, key_sha256,
        payload_sha256, "append_body",
    )
    if isinstance(replay, _ConflictResult):
        raise RollingHistoryV2Conflict(replay.message)
    if replay is not _NO_IDEMPOTENCY:
        source_mapping = _read_message_source_mapping(
            handle, scope, namespace, identity, event,
            message_event_sha256=message_event_sha256,
            source_payload_sha256=source_payload_sha256,
        )
        if (source_mapping is None
                or source_mapping["revision_id"] != replay.revision.revision_id
                or source_mapping["revision_sha256"] != replay.revision.revision_sha256
                or source_mapping["head"] != _head_value(replay.head)):
            raise RollingHistoryV2Conflict("Rolling history append receipt is unavailable")
        _verify_message_source_locator_membership(
            handle, namespace, replay, source_mapping,
        )
        source_fence_after = _read_current_migration_live_fence(handle, session_id)
        if source_fence_after != source_fence_before:
            raise RollingHistoryV2Conflict("Message source changed during rolling append retry")
        _validate_source_message_retry_fence(source_mapping, source_fence_after)
        return _body_append_result_from_source_mapping(
            source_mapping, replay, identity.canonical_namespace_digest,
        )

    message_mapping = _read_message_source_mapping(
        handle, scope, namespace, identity, event,
        message_event_sha256=message_event_sha256,
        source_payload_sha256=source_payload_sha256,
    )
    if message_mapping is not None:
        source_fence_before = _read_current_migration_live_fence(handle, session_id)
        receipt = write_source_message(handle, session_id, event)
        if (type(receipt) is not RollingHistorySourceMessageWriteReceipt
                or receipt.message_id != event.message_id
                or receipt.payload_sha256 != source_payload_sha256
                or receipt.outcome != "existing_exact"):
            raise RollingHistoryV2Conflict("Existing message source receipt conflicts")
        source_fence_after = _read_current_migration_live_fence(handle, session_id)
        if source_fence_after != source_fence_before:
            raise RollingHistoryV2Conflict("Exact message retry changed the source fence")
        _validate_source_message_retry_fence(message_mapping, source_fence_after)
        stored_result = _read_result_for_head(
            handle, scope, namespace, _decode_head(_canonical(message_mapping["head"])),
        )
        if (stored_result.revision.revision_id != message_mapping["revision_id"]
                or stored_result.revision.revision_sha256 != message_mapping["revision_sha256"]):
            raise RollingHistoryV2Conflict("Existing message source revision is unavailable")
        _verify_message_source_locator_membership(
            handle, namespace, stored_result, message_mapping,
        )
        if _read_current_migration_live_fence(handle, session_id) != source_fence_after:
            raise RollingHistoryV2Conflict("Message source changed during exact retry")
        return _body_append_result_from_source_mapping(
            message_mapping, stored_result, identity.canonical_namespace_digest,
        )

    active_before = _read_active_indexed_branch_pointer(
        handle, scope, namespace, identity.canonical_namespace_digest,
    )
    if (active_before.pointer.state != "active"
            or active_before.pointer.branch_id is None
            or active_before.head is None
            or active_before.branch is None):
        raise RollingHistoryV2MigrationRequired()
    current = _read_indexed_head_for_page(
        handle, scope, namespace, active_before.pointer.branch_id,
    )
    if isinstance(current, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    if (current.head != active_before.head.head
            or current.branch != active_before.branch):
        raise RollingHistoryV2Conflict("Active rolling history head changed before append")

    source_prefix = _scope_prefix(scope)
    parent_history_revision, parent_history_head = _owner_history_head(
        handle, scope, source_prefix,
    )
    parent = current.revision
    _verify_history_endpoint(
        handle, scope, source_prefix,
        parent.history_revision, parent.history_head_sha256,
    )
    if (parent_history_revision != parent.history_revision
            or parent_history_head != parent.history_head_sha256):
        raise RollingHistoryV2Conflict(
            "Rolling history has an unindexed source tail; reconcile it before body append",
        )

    source_fence_before = _read_current_migration_live_fence(handle, session_id)
    receipt = write_source_message(handle, session_id, event)
    if (type(receipt) is not RollingHistorySourceMessageWriteReceipt
            or receipt.message_id != event.message_id
            or receipt.payload_sha256 != source_payload_sha256
            or receipt.outcome not in {"inserted", "existing_exact"}):
        raise RollingHistoryV2Conflict("Message source write returned a conflicting receipt")
    source_fence_after = _read_current_migration_live_fence(handle, session_id)
    _validate_rolling_history_v2_migration_live_append(
        source_fence_before, source_fence_after,
    )
    if receipt.outcome != "inserted":
        raise RollingHistoryV2Conflict(
            "An existing Message row has no verified rolling-history locator",
        )
    if (source_fence_after.message_source_generation
            - source_fence_before.message_source_generation != 1
            or source_fence_after.message_count - source_fence_before.message_count != 1):
        raise RollingHistoryV2Conflict("Chat append did not add exactly one source Message")

    source_identity_sha256 = _message_source_identity_sha256(
        identity, session_id, event.message_id,
    )
    source_metadata = {
        "message_id": event.message_id,
        "created_at": event.created_at,
        "message_event_sha256": message_event_sha256,
        "source_identity_sha256": source_identity_sha256,
        "append_fence": {
            "before": _rolling_history_v2_migration_live_fence_value(source_fence_before),
            "after": _rolling_history_v2_migration_live_fence_value(source_fence_after),
        },
        "batch_count": 1,
        "batch_index": 1,
    }
    references = append_rolling_history_in_transaction(
        handle,
        scope,
        [payload],
        idempotency_key=idempotency_key,
        event_source_metadata=(source_metadata,),
    )
    if (len(references) != 1
            or references[0].version != 3
            or references[0].request_id != scope.request_id):
        raise ValueError("Rolling history body writer returned an invalid reference batch")

    reference = references[0]
    expected_sequence = parent_history_revision + 1
    stored_reference = _owner_history_read_event_reference_at_sequence(
        handle,
        scope,
        source_prefix,
        expected_sequence,
        expected_previous_chain_sha256=parent_history_head,
    )
    if stored_reference != reference:
        raise ValueError("Rolling history body writer source mapping mismatch")
    locator = RollingHistoryV2EventLocator(
        event_id=reference.event_id,
        request_id=reference.request_id,
        sequence=reference.sequence,
        version=reference.version,
        content_sha256=reference.content_sha256,
        chain_sha256=reference.chain_sha256,
    )

    history_revision = parent_history_revision + 1
    index = _append_locator_index(
        handle, namespace, current.locator_index, (locator,),
    )
    revision = _new_revision(
        handle=handle,
        namespace=namespace,
        scope=scope,
        branch_id=current.branch.branch_id,
        idempotency_id=store_idempotency_id,
        idempotency_key_sha256=key_sha256,
        operation="append_body",
        representation="delta",
        parent=parent,
        history_revision=history_revision,
        history_head_sha256=reference.chain_sha256,
        lineage_depth=0,
        event_locators=(),
        locator_index=index,
    )
    head = _new_head(revision)
    result = _RawRollingHistoryV2StoreResult(
        current.branch, revision, head, (), index,
    )
    _persist_transition_with_head_cas(
        handle, namespace, current.head, result,
    )
    _persist_idempotency(
        handle, scope, namespace, store_idempotency_id, key_sha256,
        payload_sha256, "append_body", result,
    )
    page_index = (current.locator_index.locator_count // _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE)
    event_manifest_raw, too_large = _owner_history_read_setting_bounded(
        handle, _event_manifest_key(source_prefix, reference.event_id), 4096,
    )
    if too_large or event_manifest_raw is None:
        raise ValueError("Rolling history body event manifest is unavailable")
    event_manifest = _validate_body_backed_event_manifest(
        event_manifest_raw, scope, reference.sequence,
    )
    message_source_mapping = _new_message_source_mapping(
        identity=identity,
        scope=scope,
        event=event,
        source_payload_sha256=source_payload_sha256,
        message_event_sha256=message_event_sha256,
        source_identity_sha256=source_identity_sha256,
        reference=reference,
        event_manifest=event_manifest,
        event_manifest_sha256=_sha256(event_manifest_raw),
        branch=result.branch,
        revision=result.revision,
        head=result.head,
        page_index=page_index,
        source_fence_before=source_fence_before,
        source_fence_after=source_fence_after,
    )
    encoded_message_source_mapping = _canonical(message_source_mapping)
    if len(encoded_message_source_mapping.encode("utf-8")) > 4096:
        raise ValueError("Rolling history message source locator exceeds byte budget")
    _persist_immutable(
        handle,
        _message_source_id_key(namespace, event.message_id),
        encoded_message_source_mapping,
    )

    final_source_fence = _read_current_migration_live_fence(handle, session_id)
    if final_source_fence != source_fence_after:
        raise RollingHistoryV2Conflict("Message source changed during Chat append")
    active_after = _read_active_indexed_branch_pointer(
        handle, scope, namespace, identity.canonical_namespace_digest,
    )
    if (active_after.pointer != active_before.pointer
            or active_after.pointer.branch_id != result.branch.branch_id
            or active_after.head is None
            or active_after.head.head != result.head):
        raise RollingHistoryV2Conflict("Active rolling history head changed during Chat append")
    readback = _read_indexed_head_for_page(
        handle, scope, namespace, result.branch.branch_id,
    )
    if (isinstance(readback, _MigrationRequiredResult)
            or readback.revision != result.revision
            or readback.head != result.head
            or readback.locator_index != index):
        raise RollingHistoryV2Conflict("Rolling history Chat append readback mismatch")
    return RollingHistoryV2BodyAppendResult(
        references=references,
        branch=result.branch,
        revision=result.revision,
        head=_new_bound_head(result.head, identity.canonical_namespace_digest),
        locator_index=index,
        branch_id=result.branch.branch_id,
        revision_id=result.revision.revision_id,
        revision_sha256=result.revision.revision_sha256,
        head_sha256=result.head.head_sha256,
        source_fence=source_fence_after,
    )


def _message_source_id_key(namespace: str, message_id: str) -> str:
    return namespace + "message-source-id:" + _sha256(message_id)


def _message_source_payload_sha256(event: RollingHistoryBodyEventInput) -> str:
    return _sha256(_canonical({
        "message_event": event.message_event,
        "created_at": event.created_at,
    }))


def _message_source_event_sha256(event: RollingHistoryBodyEventInput) -> str:
    return _sha256(_canonical(event.message_event))


def _message_source_identity_sha256(
    identity,
    session_id: str,
    message_id: str,
) -> str:
    return _sha256(_canonical({
        "kind": "rolling_history_chat_message_source",
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "session_id": session_id,
        "message_id": message_id,
    }))


def _new_message_source_mapping(
    *,
    identity,
    scope: ContextEventScope,
    event: RollingHistoryBodyEventInput,
    source_payload_sha256: str,
    message_event_sha256: str,
    source_identity_sha256: str,
    reference: RollingHistoryReference,
    event_manifest: dict,
    event_manifest_sha256: str,
    branch: RollingHistoryV2Branch,
    revision: RollingHistoryV2Revision,
    head: RollingHistoryV2Head,
    page_index: int,
    source_fence_before: RollingHistoryV2MigrationLiveFence,
    source_fence_after: RollingHistoryV2MigrationLiveFence,
) -> dict:
    body = {
        "schema_version": _LOCATOR_INDEX_SCHEMA_VERSION,
        "kind": "rolling_history_v2_message_source_locator",
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "scope_sha256": _sha256(_canonical(asdict(scope))),
        "session_id": scope.session_id,
        "request_id": scope.request_id,
        "message_id": event.message_id,
        "created_at": event.created_at,
        "message_event_sha256": message_event_sha256,
        "source_payload_sha256": source_payload_sha256,
        "source_identity_sha256": source_identity_sha256,
        "locator": asdict(RollingHistoryV2EventLocator(
            event_id=reference.event_id,
            request_id=reference.request_id,
            sequence=reference.sequence,
            version=reference.version,
            content_sha256=reference.content_sha256,
            chain_sha256=reference.chain_sha256,
        )),
        "event_manifest_sha256": event_manifest_sha256,
        "body_manifest_sha256": event_manifest["body_manifest_sha256"],
        "branch_id": branch.branch_id,
        "revision_id": revision.revision_id,
        "revision_sha256": revision.revision_sha256,
        "head": _head_value(head),
        "page_index": page_index,
        "source_fence_before": _rolling_history_v2_migration_live_fence_value(
            source_fence_before,
        ),
        "source_fence_after": _rolling_history_v2_migration_live_fence_value(
            source_fence_after,
        ),
    }
    return {**body, "record_sha256": _sha256(_canonical(body))}


def _read_message_source_mapping(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    event: RollingHistoryBodyEventInput,
    *,
    message_event_sha256: str,
    source_payload_sha256: str,
) -> dict | None:
    raw, too_large = _owner_history_read_setting_bounded(
        handle, _message_source_id_key(namespace, event.message_id), 4096,
    )
    if too_large:
        raise ValueError("Rolling history message source locator exceeds byte budget")
    if raw is None:
        return None
    value = _decode_canonical_object(raw, "rolling history message source locator")
    source_request_id = value.get("request_id")
    if (type(source_request_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", source_request_id) is None):
        raise RollingHistoryV2Conflict("Conflicting rolling history message source identity")
    # The locator binds the append request; compaction uses a later request ID.
    source_scope = replace(scope, request_id=source_request_id)
    required = {
        "schema_version", "kind", "canonical_namespace_digest", "scope_sha256",
        "session_id", "request_id", "message_id", "created_at", "message_event_sha256",
        "source_payload_sha256", "source_identity_sha256", "locator",
        "event_manifest_sha256", "body_manifest_sha256", "branch_id",
        "revision_id", "revision_sha256", "head", "page_index",
        "source_fence_before", "source_fence_after", "record_sha256",
    }
    body = {key: value[key] for key in value if key != "record_sha256"}
    if (set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _LOCATOR_INDEX_SCHEMA_VERSION
            or value["kind"] != "rolling_history_v2_message_source_locator"
            or value["canonical_namespace_digest"] != identity.canonical_namespace_digest
            or value["scope_sha256"] != _sha256(_canonical(asdict(source_scope)))
            or value["session_id"] != scope.session_id
            or type(value["request_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["request_id"]) is None
            or value["message_id"] != event.message_id
            or value["created_at"] != event.created_at
            or value["message_event_sha256"] != message_event_sha256
            or value["source_payload_sha256"] != source_payload_sha256
            or value["source_identity_sha256"] != _message_source_identity_sha256(
                identity, scope.session_id, event.message_id,
            )
            or type(value["locator"]) is not dict
            or type(value["head"]) is not dict
            or type(value["page_index"]) is not int or value["page_index"] < 0
            or any(type(value[key]) is not str or _HASH.fullmatch(value[key]) is None
                   for key in (
                       "event_manifest_sha256", "body_manifest_sha256",
                       "branch_id", "revision_id", "revision_sha256",
                       "record_sha256",
                   ))
            or value["record_sha256"] != _sha256(_canonical(body))
            or raw != _canonical(value)):
        raise RollingHistoryV2Conflict("Conflicting rolling history message source identity")
    try:
        locator = RollingHistoryV2EventLocator(**value["locator"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid rolling history message source locator") from exc
    if (locator.version != 3
            or re.fullmatch(r"[0-9a-f]{32}", locator.request_id) is None
            or type(value["head"]) is not dict):
        raise ValueError("Invalid rolling history message source locator")
    source_prefix = _scope_prefix(scope)
    previous_chain = (
        _genesis_chain(scope)
        if locator.sequence == 1
        else _owner_history_read_event_reference_at_sequence(
            handle, scope, source_prefix, locator.sequence - 1,
        ).chain_sha256
    )
    reference = _owner_history_read_event_reference_at_sequence(
        handle,
        scope,
        source_prefix,
        locator.sequence,
        expected_previous_chain_sha256=previous_chain,
    )
    expected_reference = RollingHistoryReference(
        locator.event_id, scope.session_id, locator.request_id, locator.sequence,
        locator.version, locator.content_sha256, locator.chain_sha256,
    )
    if reference != expected_reference:
        raise ValueError("Rolling history message source locator binding mismatch")
    manifest_raw, manifest_too_large = _owner_history_read_setting_bounded(
        handle, _event_manifest_key(source_prefix, locator.event_id), 4096,
    )
    if manifest_too_large or manifest_raw is None:
        raise ValueError("Rolling history message source event manifest is unavailable")
    manifest = _validate_body_backed_event_manifest(
        manifest_raw, scope, locator.sequence,
    )
    metadata = manifest["source_metadata"]
    before_fence = _parse_rolling_history_v2_migration_live_fence(
        value["source_fence_before"],
    )
    after_fence = _parse_rolling_history_v2_migration_live_fence(
        value["source_fence_after"],
    )
    try:
        _validate_rolling_history_v2_migration_live_append(before_fence, after_fence)
    except ValueError as exc:
        raise ValueError("Rolling history message source fence proof is invalid") from exc
    if (value["event_manifest_sha256"] != _sha256(manifest_raw)
            or value["body_manifest_sha256"] != manifest["body_manifest_sha256"]
            or metadata["message_id"] != event.message_id
            or metadata["created_at"] != event.created_at
            or metadata["message_event_sha256"] != message_event_sha256
            or metadata["source_identity_sha256"] != value["source_identity_sha256"]
            or metadata["append_fence"] != {
                "before": _rolling_history_v2_migration_live_fence_value(before_fence),
                "after": _rolling_history_v2_migration_live_fence_value(after_fence),
            }
            or metadata["batch_count"] != 1
            or metadata["batch_index"] != 1
            or manifest["payload_sha256"] != _sha256(_encode_body_event_input(event)[1])):
        raise RollingHistoryV2Conflict("Rolling history message source payload mismatch")
    return value


def _validate_source_message_retry_fence(
    source_mapping: dict,
    current: RollingHistoryV2MigrationLiveFence,
) -> None:
    previous = _parse_rolling_history_v2_migration_live_fence(
        source_mapping["source_fence_after"],
    )
    try:
        _validate_rolling_history_v2_migration_live_append(previous, current)
    except ValueError as exc:
        raise RollingHistoryV2Conflict("Message source changed since the exact append") from exc


def _verify_message_source_locator_membership(
    handle: RollingHistorySqlHandle,
    namespace: str,
    result: _RawRollingHistoryV2StoreResult,
    source_mapping: dict,
) -> None:
    locator = RollingHistoryV2EventLocator(**source_mapping["locator"])
    if (result.locator_index is None
            or result.branch.branch_id != source_mapping["branch_id"]
            or result.revision.revision_id != source_mapping["revision_id"]
            or result.revision.revision_sha256 != source_mapping["revision_sha256"]
            or _head_value(result.head) != source_mapping["head"]):
        raise RollingHistoryV2MigrationRequired()
    locators, _proof = _read_indexed_locator_page(
        handle,
        namespace,
        result.locator_index,
        source_mapping["page_index"],
        include_proof=True,
    )
    if locator not in locators:
        raise RollingHistoryV2Conflict("Message source locator is not in its committed revision")


def _body_append_result_from_source_mapping(
    source_mapping: dict,
    result: _RawRollingHistoryV2StoreResult,
    canonical_namespace_digest: str,
) -> RollingHistoryV2BodyAppendResult:
    locator = RollingHistoryV2EventLocator(**source_mapping["locator"])
    reference = RollingHistoryReference(
        locator.event_id, source_mapping["session_id"], locator.request_id,
        locator.sequence, locator.version, locator.content_sha256,
        locator.chain_sha256,
    )
    return RollingHistoryV2BodyAppendResult(
        references=(reference,),
        branch=result.branch,
        revision=result.revision,
        head=_new_bound_head(result.head, canonical_namespace_digest),
        locator_index=result.locator_index or _empty_locator_index(),
        branch_id=result.branch.branch_id,
        revision_id=result.revision.revision_id,
        revision_sha256=result.revision.revision_sha256,
        head_sha256=result.head.head_sha256,
        source_fence=_parse_rolling_history_v2_migration_live_fence(
            source_mapping["source_fence_after"],
        ),
    )


def append_rolling_history_v2_events(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    source_event_ids: tuple[str, ...],
    idempotency_key: str,
) -> RollingHistoryV2StoreResult:
    """Append the complete v1 event tail after the branch's recorded history head.

    The IDs are selectors only. Every selected source row and event-ID binding is
    re-read and verified inside the same owner-bound transaction. The supplied
    IDs must cover the entire current v1 tail contiguously, in sequence order.
    """
    _validate_bound_head_token(expected_head)
    if (type(source_event_ids) is not tuple or not source_event_ids
            or any(type(value) is not str or _HASH.fullmatch(value) is None
                   for value in source_event_ids)
            or len(set(source_event_ids)) != len(source_event_ids)):
        raise ValueError("Invalid rolling history v2 source event IDs")
    binding = _current_binding()
    scope = binding.scope
    _require_head_scope(expected_head, scope)
    idempotency_id, key_sha256 = _idempotency_context(scope, idempotency_key)
    payload_sha256 = _sha256(_canonical({
        "operation": "append",
        "expected_head": asdict(expected_head.head),
        "source_event_ids": list(source_event_ids),
    }))
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle):
        replay = _read_idempotent_result(
            handle, scope, namespace, idempotency_id, key_sha256,
            payload_sha256, "append",
        )
        if replay is not _NO_IDEMPOTENCY:
            return replay

        current = _read_indexed_head_for_page(
            handle, scope, namespace, expected_head.head.branch_id,
        )
        if isinstance(current, _MigrationRequiredResult):
            return current
        if current.head != expected_head.head:
            return _ConflictResult("Rolling history v2 expected-head conflict")

        v1_prefix = _scope_prefix(scope)
        current_history_revision, current_history_head = _read_v1_history_head(
            handle, scope, v1_prefix,
        )
        parent = current.revision
        _verify_history_endpoint(
            handle, scope, v1_prefix,
            parent.history_revision, parent.history_head_sha256,
        )
        if current_history_revision < parent.history_revision:
            raise ValueError("Rolling history v2 source history moved backwards")
        expected_event_count = current_history_revision - parent.history_revision
        if expected_event_count != len(source_event_ids):
            raise ValueError("Rolling history v2 append must cover the complete source tail")

        appended: list[RollingHistoryV2EventLocator] = []
        previous_chain = parent.history_head_sha256
        for offset, event_id in enumerate(source_event_ids, start=1):
            expected_sequence = parent.history_revision + offset
            reference = _owner_history_read_event_reference_at_sequence(
                handle,
                scope,
                v1_prefix,
                expected_sequence,
                expected_previous_chain_sha256=previous_chain,
            )
            if reference.event_id != event_id:
                raise ValueError("Rolling history v2 source event is unavailable")
            if reference.request_id != scope.request_id:
                raise ValueError("Rolling history v2 append source request mismatch")
            appended.append(RollingHistoryV2EventLocator(
                event_id=reference.event_id,
                request_id=reference.request_id,
                sequence=reference.sequence,
                version=reference.version,
                content_sha256=reference.content_sha256,
                chain_sha256=reference.chain_sha256,
            ))
            previous_chain = reference.chain_sha256

        appended_locators = tuple(appended)
        index = _append_locator_index(
            handle, namespace, current.locator_index, appended_locators,
        )

        revision = _new_revision(
            handle=handle,
            namespace=namespace,
            scope=scope,
            branch_id=current.branch.branch_id,
            idempotency_id=idempotency_id,
            idempotency_key_sha256=key_sha256,
            operation="append",
            representation="delta",
            parent=parent,
            history_revision=current_history_revision,
            history_head_sha256=current_history_head,
            lineage_depth=0,
            event_locators=(),
            locator_index=index,
        )
        head = _new_head(revision)
        result = _RawRollingHistoryV2StoreResult(
            current.branch, revision, head, (), index,
        )
        _persist_transition(handle, namespace, result)
        _persist_idempotency(
            handle, scope, namespace, idempotency_id, key_sha256,
            payload_sha256, "append", result,
        )
        return result

    return _finish_operation(
        binding, OperationMode.WRITE, operation, expected_head=expected_head,
    )


def _locator_page_cursor_value(cursor: RollingHistoryV2LocatorPageCursor) -> dict:
    return {
        "cursor_version": cursor.cursor_version,
        "branch_id": cursor.branch_id,
        "revision_id": cursor.revision_id,
        "revision_sha256": cursor.revision_sha256,
        "head_sha256": cursor.head_sha256,
        "index_root_sha256": cursor.index_root_sha256,
        "pin_sha256": cursor.pin_sha256,
        "source_fence_sha256": cursor.source_fence_sha256,
        "next_page_index": cursor.next_page_index,
    }


def _new_locator_page_cursor(
    *,
    pin: RollingHistoryV2LocatorPagePin,
    source_fence_sha256: str,
    next_page_index: int,
) -> RollingHistoryV2LocatorPageCursor:
    values = {
        "cursor_version": _LOCATOR_PAGE_API_VERSION,
        "branch_id": pin.branch_id,
        "revision_id": pin.head.revision_id,
        "revision_sha256": pin.head.revision_sha256,
        "head_sha256": pin.head.head_sha256,
        "index_root_sha256": pin.index.root_sha256,
        "pin_sha256": pin.pin_sha256,
        "source_fence_sha256": source_fence_sha256,
        "next_page_index": next_page_index,
    }
    return RollingHistoryV2LocatorPageCursor(
        **values,
        cursor_sha256=_sha256(_canonical(values)),
    )


def _validate_locator_page_cursor(cursor: object) -> None:
    if type(cursor) is not RollingHistoryV2LocatorPageCursor:
        raise ValueError("Invalid rolling history v2 locator page cursor")
    value = _locator_page_cursor_value(cursor)
    if (type(cursor.cursor_version) is not int
            or cursor.cursor_version != _LOCATOR_PAGE_API_VERSION
            or any(type(value[name]) is not str or _HASH.fullmatch(value[name]) is None
                   for name in (
                       "branch_id", "revision_id", "revision_sha256", "head_sha256",
                       "index_root_sha256", "pin_sha256", "source_fence_sha256",
                   ))
            or type(cursor.next_page_index) is not int or cursor.next_page_index < 0
            or type(cursor.cursor_sha256) is not str
            or _HASH.fullmatch(cursor.cursor_sha256) is None
            or cursor.cursor_sha256 != _sha256(_canonical(value))):
        raise ValueError("Invalid rolling history v2 locator page cursor")


def _locator_page_pin_value(pin: RollingHistoryV2LocatorPagePin) -> dict:
    return {
        "schema_version": pin.schema_version,
        "canonical_namespace_digest": pin.canonical_namespace_digest,
        "branch_id": pin.branch_id,
        "head": asdict(pin.head),
        "index": asdict(pin.index),
        "source_fence": _rolling_history_v2_migration_live_fence_value(pin.source_fence),
    }


def _new_locator_page_pin(
    canonical_namespace_digest: str,
    result: _RawRollingHistoryV2StoreResult,
    source_fence: RollingHistoryV2MigrationLiveFence,
) -> RollingHistoryV2LocatorPagePin:
    values = {
        "schema_version": _LOCATOR_PAGE_API_VERSION,
        "canonical_namespace_digest": canonical_namespace_digest,
        "branch_id": result.branch.branch_id,
        "head": result.head,
        "index": result.locator_index,
        "source_fence": source_fence,
    }
    provisional = RollingHistoryV2LocatorPagePin(
        **values,
        pin_sha256="0" * 64,
    )
    return RollingHistoryV2LocatorPagePin(
        **values,
        pin_sha256=_sha256(_canonical(_locator_page_pin_value(provisional))),
    )


def _validate_locator_page_pin(pin: object) -> None:
    if (type(pin) is not RollingHistoryV2LocatorPagePin
            or type(pin.schema_version) is not int
            or pin.schema_version != _LOCATOR_PAGE_API_VERSION
            or type(pin.canonical_namespace_digest) is not str
            or _HASH.fullmatch(pin.canonical_namespace_digest) is None
            or type(pin.branch_id) is not str or _HASH.fullmatch(pin.branch_id) is None
            or type(pin.head) is not RollingHistoryV2Head
            or type(pin.source_fence) is not RollingHistoryV2MigrationLiveFence
            or any(type(value) is not int or value < 0 for value in (
                pin.source_fence.message_source_generation,
                pin.source_fence.message_count,
                pin.source_fence.non_append_generation,
            ))
            or type(pin.pin_sha256) is not str or _HASH.fullmatch(pin.pin_sha256) is None):
        raise ValueError("Invalid rolling history v2 locator page pin")
    _validate_rolling_history_v2_head_token(pin.head)
    _validate_rolling_history_v2_locator_index(pin.index)
    if (pin.head.branch_id != pin.branch_id
            or pin.pin_sha256 != _sha256(_canonical(_locator_page_pin_value(pin)))):
        raise ValueError("Rolling history v2 locator page pin commitment mismatch")


def _read_indexed_head_for_page(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    branch_id: str,
) -> _RawRollingHistoryV2StoreResult | _MigrationRequiredResult:
    head_raw = _read_bounded_setting(
        handle, namespace + "head:" + branch_id, 4096,
    )
    if head_raw is None:
        raise ValueError("Rolling history v2 branch head is unavailable")
    head = _decode_head(head_raw)
    if head.branch_id != branch_id or head.scope_sha256 != _scope_digest(scope):
        raise ValueError("Rolling history v2 branch head binding mismatch")

    marker_raw = _read_bounded_setting(
        handle, namespace + "revision-index:" + head.revision_id, 4096,
    )
    if marker_raw is None:
        return _MigrationRequiredResult()
    marker = _decode_canonical_object(marker_raw, "rolling history v2 indexed revision marker")
    marker_fields = {
        "schema_version", "kind", "revision_id", "revision_sha256",
        "locator_index", "lineage_index",
    }
    if (set(marker) != marker_fields
            or type(marker["schema_version"]) is not int
            or marker["schema_version"] != _LOCATOR_INDEX_SCHEMA_VERSION
            or marker["kind"] != "rolling_history_v2_indexed_revision_marker"
            or marker["revision_id"] != head.revision_id
            or marker["revision_sha256"] != head.revision_sha256):
        raise ValueError("Rolling history v2 indexed revision marker mismatch")
    index = _decode_locator_index(marker["locator_index"])
    lineage_index = _decode_locator_index(marker["lineage_index"])

    revision_raw = _read_bounded_setting(
        handle, namespace + "revision:" + head.revision_id, 2048,
    )
    if revision_raw is None:
        return _MigrationRequiredResult()
    revision = _decode_revision(revision_raw)
    if (revision.schema_version != 3 or revision.locator_index != index
            or revision.lineage_index != lineage_index):
        return _MigrationRequiredResult()
    _validate_rolling_history_v2_head(head, revision)

    branch_raw = _read_bounded_setting(
        handle, namespace + "branch:" + branch_id, 4096,
    )
    if branch_raw is None:
        raise ValueError("Rolling history v2 branch is unavailable")
    branch = _decode_branch(branch_raw)
    if (branch.scope_sha256 != _scope_digest(scope)
            or branch.branch_id != branch_id
            or branch.branch_sha256 != _sha256(_canonical(_rolling_history_v2_branch_envelope(branch)))):
        raise ValueError("Rolling history v2 branch binding mismatch")
    lineage_proof = _read_indexed_revision_lineage_proof(
        handle, namespace, branch, revision,
    )
    return _RawRollingHistoryV2StoreResult(
        branch, revision, head, (), index, lineage_proof,
    )


def _read_pinned_result_for_page(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    pin: RollingHistoryV2LocatorPagePin,
    canonical_namespace_digest: str,
) -> _RawRollingHistoryV2StoreResult | _MigrationRequiredResult:
    _validate_locator_page_pin(pin)
    if (pin.canonical_namespace_digest != canonical_namespace_digest
            or pin.head.scope_sha256 != _scope_digest(scope)):
        return _MigrationRequiredResult()
    marker_raw = _read_bounded_setting(
        handle, namespace + "revision-index:" + pin.head.revision_id, 4096,
    )
    if marker_raw is None:
        return _MigrationRequiredResult()
    marker = _decode_canonical_object(marker_raw, "rolling history v2 indexed revision marker")
    if (set(marker) != {
            "schema_version", "kind", "revision_id", "revision_sha256", "locator_index",
        "lineage_index",
        }
            or marker["schema_version"] != _LOCATOR_INDEX_SCHEMA_VERSION
            or marker["kind"] != "rolling_history_v2_indexed_revision_marker"
            or marker["revision_id"] != pin.head.revision_id
            or marker["revision_sha256"] != pin.head.revision_sha256
            or _decode_locator_index(marker["locator_index"]) != pin.index):
        raise ValueError("Rolling history v2 pinned revision marker mismatch")
    marker_lineage_index = _decode_locator_index(marker["lineage_index"])
    revision_raw = _read_bounded_setting(
        handle, namespace + "revision:" + pin.head.revision_id, 2048,
    )
    if revision_raw is None:
        return _MigrationRequiredResult()
    revision = _decode_revision(revision_raw)
    if (revision.schema_version != 3
            or revision.locator_index != pin.index
            or revision.lineage_index != marker_lineage_index
            or revision.branch_id != pin.branch_id):
        return _MigrationRequiredResult()
    _validate_rolling_history_v2_head(pin.head, revision)
    branch_raw = _read_bounded_setting(
        handle, namespace + "branch:" + pin.branch_id, 4096,
    )
    if branch_raw is None:
        raise ValueError("Rolling history v2 branch is unavailable")
    branch = _decode_branch(branch_raw)
    if (branch.scope_sha256 != _scope_digest(scope)
            or branch.branch_id != pin.branch_id
            or branch.branch_sha256 != _sha256(_canonical(_rolling_history_v2_branch_envelope(branch)))):
        raise ValueError("Rolling history v2 pinned branch binding mismatch")
    lineage_proof = _read_indexed_revision_lineage_proof(
        handle, namespace, branch, revision,
    )
    return _RawRollingHistoryV2StoreResult(
        branch, revision, pin.head, (), pin.index, lineage_proof,
    )


def read_rolling_history_v2_locator_page(
    branch_id: str | None = None,
    *,
    pin: RollingHistoryV2LocatorPagePin | None = None,
    cursor: RollingHistoryV2LocatorPageCursor | None = None,
) -> RollingHistoryV2LocatorPageResult:
    """Read one fixed-size locator page from a pinned immutable revision.

    Each call uses one restricted owner handle, rechecks the mutable-message
    source fence, reads one locator page and its radix proof, and verifies the
    branch creator, direct parent, and pinned revision with at most four
    fixed-size lineage page reads and logarithmic radix paths. A first call captures the current
    head; later calls pass the returned pin and cursor, so an append does not
    move the continuation to a newer head. There is no total-page, history, or
    generation cap.
    """
    if pin is None:
        if (type(branch_id) is not str or _HASH.fullmatch(branch_id) is None
                or cursor is not None):
            raise ValueError("Initial locator page read requires a branch ID without a cursor")
    else:
        _validate_locator_page_pin(pin)
        if (branch_id is not None and branch_id != pin.branch_id):
            raise ValueError("Locator page branch does not match its pin")
        branch_id = pin.branch_id
    if cursor is not None:
        _validate_locator_page_cursor(cursor)
    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)
    identity = get_rolling_history_owner_identity()

    def operation(handle: RollingHistorySqlHandle):
        try:
            source_fence = _read_current_migration_live_fence(
                handle, identity.session_id,
            )
        except LegacySnapshotGenerationConflict:
            return _ConflictResult("Message source generation is unavailable")
        if pin is None:
            result = _read_indexed_head_for_page(
                handle, scope, namespace, branch_id,
            )
        else:
            result = _read_pinned_result_for_page(
                handle, scope, namespace, pin,
                identity.canonical_namespace_digest,
            )
        if isinstance(result, _MigrationRequiredResult):
            return result
        index = result.locator_index
        _validate_rolling_history_v2_locator_index(index)
        if pin is None:
            page_pin = _new_locator_page_pin(
                identity.canonical_namespace_digest, result, source_fence,
            )
        else:
            page_pin = pin
            original_fence = page_pin.source_fence
            if (source_fence.non_append_generation != original_fence.non_append_generation
                    or source_fence.message_count < original_fence.message_count
                    or source_fence.message_source_generation
                    < original_fence.message_source_generation):
                return _ConflictResult("Pinned locator source is no longer append-only")
        fence_sha256 = _sha256(_canonical(
            _rolling_history_v2_migration_live_fence_value(page_pin.source_fence),
        ))
        if cursor is None:
            page_index = 0
        else:
            if (cursor.branch_id != result.branch.branch_id
                    or cursor.revision_id != result.revision.revision_id
                    or cursor.revision_sha256 != result.revision.revision_sha256
                    or cursor.head_sha256 != result.head.head_sha256
                    or cursor.index_root_sha256 != index.root_sha256
                    or cursor.pin_sha256 != page_pin.pin_sha256
                    or cursor.source_fence_sha256 != fence_sha256
                    or cursor.next_page_index >= index.page_count):
                return _ConflictResult("Rolling history v2 locator page cursor is stale")
            page_index = cursor.next_page_index

        if index.page_count == 0:
            if cursor is not None:
                return _ConflictResult("Rolling history v2 locator page cursor is stale")
            locators: tuple[RollingHistoryV2EventLocator, ...] = ()
            proof: tuple[RollingHistoryV2LocatorIndexProofStep, ...] = ()
            page_index = 0
        else:
            locators, proof = _read_indexed_locator_page(
                handle, namespace, index, page_index, include_proof=True,
            )
            source_prefix = _scope_prefix(scope)
            reader = _SettingsReader(handle)
            for locator in locators:
                mapping = _event_id_mapping(reader, scope, source_prefix, locator.event_id)
                if (mapping is None
                        or mapping["request_id"] != locator.request_id
                        or mapping["sequence"] != locator.sequence
                        or mapping["content_sha256"] != locator.content_sha256
                        or mapping["chain_sha256"] != locator.chain_sha256):
                    raise ValueError("Rolling history v2 locator source mapping mismatch")

        if _read_current_migration_live_fence(handle, identity.session_id) != source_fence:
            return _ConflictResult("Message source generation changed during page read")
        next_cursor = None
        if page_index + 1 < index.page_count:
            next_cursor = _new_locator_page_cursor(
                pin=page_pin,
                source_fence_sha256=fence_sha256,
                next_page_index=page_index + 1,
            )
        return RollingHistoryV2LocatorPageResult(
            branch=result.branch,
            head=result.head,
            index=index,
            page_index=page_index,
            locators=locators,
            proof=proof,
            lineage_proof=result.lineage_proof or _read_indexed_revision_lineage_proof(
                handle, namespace, result.branch, result.revision,
            ),
            next_cursor=next_cursor,
            pin=page_pin,
            source_fence=source_fence,
        )

    result = _finish_operation(binding, OperationMode.READ, operation)
    if type(result) is not RollingHistoryV2LocatorPageResult:
        raise RollingHistoryOwnerScopeError("Rolling history locator page is unavailable")
    return result


def read_rolling_history_v2_event_body_chunk(
    *,
    pin: RollingHistoryV2LocatorPagePin,
    page_index: int,
    locator: RollingHistoryV2EventLocator,
    cursor: RollingHistoryBodyChunkCursor | None = None,
    max_bytes: int = 8192,
) -> RollingHistoryBodyEventChunk | None:
    """Read one body-store chunk for one locator proven inside an immutable page pin.

    Every call revalidates the pinned revision, branch lineage proof, exact
    locator-page membership, source mapping, and live source fence in one owner
    READ transaction. It asks the body store for at most three rows and 8192
    bytes and never assembles the full body.
    """
    _validate_locator_page_pin(pin)
    if (type(page_index) is not int or page_index < 0
            or type(locator) is not RollingHistoryV2EventLocator
            or type(max_bytes) is not int or max_bytes != 8192):
        raise ValueError("Invalid rolling history v2 body page read")
    if cursor is not None:
        _validate_body_chunk_cursor(cursor)
        if (cursor.page_pin_sha256 != pin.pin_sha256
                or cursor.page_index != page_index
                or cursor.locator != locator):
            raise ValueError("Rolling history body cursor does not match its pinned locator")

    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)
    identity = get_rolling_history_owner_identity()
    if pin.canonical_namespace_digest != identity.canonical_namespace_digest:
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")

    def operation(handle: RollingHistorySqlHandle):
        try:
            source_fence = _read_current_migration_live_fence(
                handle, identity.session_id,
            )
        except LegacySnapshotGenerationConflict:
            return _ConflictResult("Message source generation is unavailable")
        if (source_fence.non_append_generation != pin.source_fence.non_append_generation
                or source_fence.message_count < pin.source_fence.message_count
                or source_fence.message_source_generation
                < pin.source_fence.message_source_generation):
            return _ConflictResult("Pinned body source is no longer append-only")
        result = _read_pinned_result_for_page(
            handle, scope, namespace, pin,
            identity.canonical_namespace_digest,
        )
        if isinstance(result, _MigrationRequiredResult):
            return result
        if (result.branch.branch_id != pin.branch_id
                or result.head != pin.head
                or result.locator_index != pin.index
                or page_index >= pin.index.page_count):
            return _ConflictResult("Pinned body page is unavailable")
        page_locators, _proof = _read_indexed_locator_page(
            handle, namespace, pin.index, page_index, include_proof=True,
        )
        if locator not in page_locators:
            return _ConflictResult("Event locator is not a member of the pinned page")
        if locator.version != 3:
            raise RollingHistoryV2MigrationRequired()
        mapping = _event_id_mapping(
            _SettingsReader(handle), scope, _scope_prefix(scope), locator.event_id,
        )
        if (mapping is None
                or mapping["request_id"] != locator.request_id
                or mapping["sequence"] != locator.sequence
                or mapping["content_sha256"] != locator.content_sha256
                or mapping["chain_sha256"] != locator.chain_sha256):
            raise ValueError("Pinned body locator source mapping mismatch")
        lineage_proof = result.lineage_proof
        if (lineage_proof is None
                or lineage_proof.lineage_root_sha256 != result.revision.lineage_root_sha256):
            raise ValueError("Pinned body lineage proof is unavailable")
        chunk = read_rolling_history_event_body_chunk_in_transaction(
            handle,
            scope,
            identity.session_id,
            page_pin_sha256=pin.pin_sha256,
            page_index=page_index,
            locator=locator,
            branch_id=pin.branch_id,
            revision_id=pin.head.revision_id,
            revision_sha256=pin.head.revision_sha256,
            head_sha256=pin.head.head_sha256,
            lineage_root_sha256=result.revision.lineage_root_sha256,
            lineage_proof_sha256=lineage_proof.proof_sha256,
            cursor=cursor,
            max_rows=3,
            max_bytes=max_bytes,
        )
        if _read_current_migration_live_fence(handle, identity.session_id) != source_fence:
            return _ConflictResult("Message source generation changed during body read")
        return _MissingEventChunkResult() if chunk is None else chunk

    result = _finish_operation(binding, OperationMode.READ, operation)
    if type(result) is _MissingEventChunkResult:
        return None
    if type(result) is not RollingHistoryBodyEventChunk:
        raise RollingHistoryOwnerScopeError("Rolling history body chunk is unavailable")
    return result


def read_rolling_history_v2_event_chunk(
    *,
    event_id: str | None = None,
    cursor: RollingHistoryChunkCursor | None = None,
    max_rows: int = 3,
    max_bytes: int = 24576,
) -> RollingHistoryEventChunk | None:
    """Read a fixed-size source chunk under the same owner READ handle and fence.

    The event body reader is capped at three Settings rows per call; the source
    fence is read before and after it on this same restricted transaction handle.
    """
    binding = _current_binding()
    scope = binding.scope
    identity = get_rolling_history_owner_identity()

    def operation(handle: RollingHistorySqlHandle):
        try:
            fence = _read_current_migration_live_fence(handle, identity.session_id)
        except LegacySnapshotGenerationConflict:
            return _ConflictResult("Message source generation is unavailable")
        chunk = read_rolling_history_event_chunk_in_transaction(
            handle,
            scope,
            identity.session_id,
            event_id=event_id,
            cursor=cursor,
            max_rows=max_rows,
            max_bytes=max_bytes,
        )
        if _read_current_migration_live_fence(handle, identity.session_id) != fence:
            return _ConflictResult("Message source generation changed during chunk read")
        return _MissingEventChunkResult() if chunk is None else chunk

    result = _finish_operation(binding, OperationMode.READ, operation)
    if type(result) is _MissingEventChunkResult:
        return None
    if type(result) is not RollingHistoryEventChunk:
        raise RollingHistoryOwnerScopeError("Rolling history event chunk is unavailable")
    return result


def _publication_snapshot_payload(snapshot: RollingHistoryV2PublicationSnapshot) -> dict:
    value = asdict(snapshot)
    value.pop("snapshot_sha256", None)
    return value


def _publication_snapshot_digest(snapshot: RollingHistoryV2PublicationSnapshot) -> str:
    return _sha256(_canonical(_publication_snapshot_payload(snapshot)))


def _publication_role(event_type: str) -> str:
    roles = {
        "user_text": "human",
        "assistant_text": "assistant",
        "tool_call": "assistant",
        "tool_result": "tool",
    }
    role = roles.get(event_type)
    if role is None:
        raise ValueError("Unsupported event type in V2 publication snapshot")
    return role


def _read_v2_publication_body_event_metadata(
    *,
    pin: RollingHistoryV2LocatorPagePin,
    page_index: int,
    locator: RollingHistoryV2EventLocator,
) -> _PublicationBodyEventMetadata:
    if locator.version != 3:
        raise ValueError("V2 body metadata requires a body-backed locator")
    binding = _current_binding()
    scope = binding.scope
    identity = get_rolling_history_owner_identity()

    def operation(handle: RollingHistorySqlHandle, current_identity, namespace):
        if current_identity != identity:
            return _MigrationRequiredResult()
        try:
            source_fence = _read_current_migration_live_fence(
                handle, identity.session_id,
            )
        except LegacySnapshotGenerationConflict:
            return _ConflictResult("Message source generation is unavailable")
        pinned_fence = pin.source_fence
        if (source_fence.non_append_generation != pinned_fence.non_append_generation
                or source_fence.message_count < pinned_fence.message_count
                or source_fence.message_source_generation
                < pinned_fence.message_source_generation):
            return _ConflictResult("Pinned body source is no longer append-only")

        current = _read_pinned_result_for_page(
            handle, scope, namespace, pin,
            identity.canonical_namespace_digest,
        )
        if isinstance(current, _MigrationRequiredResult):
            return current
        if (current.head != pin.head or current.branch.branch_id != pin.branch_id
                or current.locator_index != pin.index
                or current.lineage_proof is None
                or page_index < 0 or page_index >= pin.index.page_count):
            return _ConflictResult("Pinned body event page is unavailable")
        page_locators, _proof = _read_indexed_locator_page(
            handle, namespace, pin.index, page_index, include_proof=True,
        )
        if locator not in page_locators:
            return _ConflictResult("Body event locator is not a pinned page member")
        mapping = _event_id_mapping(
            _SettingsReader(handle), scope, _scope_prefix(scope), locator.event_id,
        )
        if (mapping is None
                or mapping["request_id"] != locator.request_id
                or mapping["sequence"] != locator.sequence
                or mapping["content_sha256"] != locator.content_sha256
                or mapping["chain_sha256"] != locator.chain_sha256):
            raise ValueError("Body event source mapping does not match its locator")

        raw, too_large = _owner_history_read_setting_bounded(
            handle, _event_manifest_key(_scope_prefix(scope), locator.event_id), 4096,
        )
        if too_large or raw is None:
            raise ValueError("V2 body event manifest is unavailable or oversized")
        manifest = _validate_body_backed_event_manifest(
            raw, scope, locator.sequence,
        )
        if (manifest["event_id"] != locator.event_id
                or manifest["request_id"] != locator.request_id
                or manifest["content_sha256"] != locator.content_sha256
                or manifest["chain_sha256"] != locator.chain_sha256
                or manifest["body_id"] != _body_id_for_event(locator.event_id)):
            raise ValueError("V2 body event manifest does not match its locator")
        body_manifest_value = {
            "body_id": manifest["body_id"],
            "session_id": scope.session_id,
            "byte_length": manifest["body_byte_length"],
            "sha256": manifest["body_sha256"],
            "chunk_size": manifest["body_chunk_size"],
            "chunk_count": manifest["body_chunk_count"],
            "final_chain_sha256": manifest["body_chain_sha256"],
            "status": "writer_verified",
            "encoding": "utf-8",
        }
        body_manifest_sha256 = _sha256(_canonical(body_manifest_value))
        if body_manifest_sha256 != manifest["body_manifest_sha256"]:
            raise ValueError("V2 body event store-manifest digest mismatch")
        attachments = tuple(_parse_attachment(item) for item in manifest["attachments"])
        if _read_current_migration_live_fence(handle, identity.session_id) != source_fence:
            return _ConflictResult("Message source generation changed during metadata read")
        return _PublicationBodyEventMetadata(
            event_type=manifest["event_type"],
            attachments=attachments,
            payload_sha256=manifest["payload_sha256"],
            text_sha256=manifest["text_sha256"],
            event_chain_sha256=manifest["chain_sha256"],
            event_manifest_sha256=_sha256(raw),
            body_manifest_sha256=body_manifest_sha256,
            body_utf8_byte_length=manifest["body_byte_length"],
            body_chunk_count=manifest["body_chunk_count"],
            body_chain_sha256=manifest["body_chain_sha256"],
        )

    result = _finish_publication_operation(
        binding, OperationMode.READ, operation,
    )
    if type(result) is not _PublicationBodyEventMetadata:
        raise RollingHistoryOwnerScopeError("V2 body event metadata is unavailable")
    return result


def _read_v2_publication_event(
    *,
    pin: RollingHistoryV2LocatorPagePin,
    page_index: int,
    locator: RollingHistoryV2EventLocator,
    max_event_bytes: int,
    remaining_total_bytes: int,
) -> tuple[RollingHistoryV2PublicationEvent, int]:
    event_chunk_sha256s: list[str] = []
    body_chunk_sha256s: list[str] = []
    body_manifest_sha256 = None
    event_manifest_sha256 = None
    if locator.version == 3:
        metadata = _read_v2_publication_body_event_metadata(
            pin=pin, page_index=page_index, locator=locator,
        )
        if (metadata.body_utf8_byte_length > max_event_bytes
                or metadata.body_utf8_byte_length > remaining_total_bytes):
            raise ValueError("V2 publication snapshot byte budget exceeded")
        event_type = metadata.event_type
        attachments = metadata.attachments
        payload_sha256 = metadata.payload_sha256
        text_sha256 = metadata.text_sha256
        chain_sha256 = metadata.event_chain_sha256
        event_manifest_sha256 = metadata.event_manifest_sha256
        body_manifest_sha256 = metadata.body_manifest_sha256
        if (metadata.body_utf8_byte_length == 0
                and metadata.text_sha256 != _sha256(b"")):
            raise ValueError("V2 empty body hash proof mismatch")
        body_parts: list[bytes] = []
        expected_body_offset = 0
        body_cursor = None
        while expected_body_offset < metadata.body_utf8_byte_length:
            body_chunk = read_rolling_history_v2_event_body_chunk(
                pin=pin,
                page_index=page_index,
                locator=locator,
                cursor=body_cursor,
                max_bytes=_PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES,
            )
            if body_chunk is None:
                raise ValueError("V2 publication body chunk is unavailable")
            if (body_chunk.locator != locator
                    or body_chunk.page_pin_sha256 != pin.pin_sha256
                    or body_chunk.page_index != page_index
                    or body_chunk.start_offset != expected_body_offset
                    or type(body_chunk.content_bytes) is not bytes
                    or not 1 <= len(body_chunk.content_bytes) <= _PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES
                    or body_chunk.end_offset - body_chunk.start_offset != len(body_chunk.content_bytes)
                    or body_chunk.end_offset > metadata.body_utf8_byte_length
                    or body_chunk.payload_sha256 != payload_sha256
                    or body_chunk.event_chain_sha256 != locator.chain_sha256
                    or body_chunk.text_sha256 != metadata.text_sha256
                    or body_chunk.body_manifest_sha256 != body_manifest_sha256
                    or _sha256(body_chunk.content_bytes) != body_chunk.body_chunk_sha256):
                raise ValueError("V2 publication body chunk proof mismatch")
            expected_body_offset = body_chunk.end_offset
            body_parts.append(body_chunk.content_bytes)
            body_chunk_sha256s.append(body_chunk.body_chunk_sha256)
            body_cursor = body_chunk.next_cursor
            if body_cursor is None:
                break
            if body_cursor.next_offset != expected_body_offset:
                raise ValueError("V2 publication body cursor did not advance")
        body_bytes = b"".join(body_parts)
        if (expected_body_offset != metadata.body_utf8_byte_length
                or len(body_bytes) != metadata.body_utf8_byte_length
                or len(body_chunk_sha256s) != metadata.body_chunk_count
                or (body_chunk_sha256s and body_chunk.body_chain_sha256
                    != metadata.body_chain_sha256)):
            raise ValueError("V2 publication body chunk stream is incomplete")
    elif locator.version == 2:
        event_cursor = None
        event_chunks: list[str] = []
        expected_offset = 0
        event_type = None
        attachments = None
        payload_sha256 = None
        text_sha256 = None
        chain_sha256 = None
        while True:
            chunk = read_rolling_history_v2_event_chunk(
                event_id=locator.event_id if event_cursor is None else None,
                cursor=event_cursor,
                max_rows=3,
                max_bytes=_PUBLICATION_SNAPSHOT_EVENT_CHUNK_BYTES,
            )
            if chunk is None:
                raise ValueError("V2 publication event chunk is unavailable")
            if (chunk.event_id != locator.event_id
                    or chunk.payload_sha256 != locator.content_sha256
                    or chunk.chain_sha256 != locator.chain_sha256
                    or type(chunk.start_offset) is not int
                    or type(chunk.end_offset) is not int
                    or chunk.start_offset != expected_offset
                    or type(chunk.content_chunk) is not str
                    or type(chunk.attachments) is not tuple
                    or any(type(item) is not AttachmentReference for item in chunk.attachments)):
                raise ValueError("V2 publication event chunk does not match its locator")
            encoded_chunk = chunk.content_chunk.encode("utf-8", errors="strict")
            if chunk.end_offset - chunk.start_offset != len(encoded_chunk):
                raise ValueError("V2 publication event chunk offset mismatch")
            if event_type is None:
                event_type = chunk.event_type
                attachments = chunk.attachments
                payload_sha256 = chunk.payload_sha256
                text_sha256 = chunk.text_sha256
                chain_sha256 = chunk.chain_sha256
            elif (event_type != chunk.event_type or attachments != chunk.attachments
                    or payload_sha256 != chunk.payload_sha256
                    or text_sha256 != chunk.text_sha256
                    or chain_sha256 != chunk.chain_sha256):
                raise ValueError("V2 publication event metadata changed between chunks")
            expected_offset = chunk.end_offset
            if (expected_offset > max_event_bytes
                    or expected_offset > remaining_total_bytes):
                raise ValueError("V2 publication snapshot byte budget exceeded")
            event_chunks.append(chunk.content_chunk)
            event_chunk_sha256s.append(_sha256(encoded_chunk))
            event_cursor = chunk.next_cursor
            if event_cursor is None:
                break
            if event_cursor.next_offset != expected_offset or not encoded_chunk:
                raise ValueError("V2 publication event cursor did not advance")
        body_bytes = "".join(event_chunks).encode("utf-8", errors="strict")
    else:
        raise ValueError("Legacy V2 locator versions are not publication-readable")

    try:
        body = body_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("V2 publication event body is not valid UTF-8") from exc
    body_sha256 = _sha256(body_bytes)
    payload_sha256_from_body = _sha256(_canonical({
        "event_type": event_type,
        "content": body,
        "attachments": [asdict(item) for item in attachments],
    }))
    if (event_type is None or type(attachments) is not tuple
            or type(text_sha256) is not str or text_sha256 != body_sha256
            or type(payload_sha256) is not str
            or payload_sha256_from_body != payload_sha256
            or (locator.version == 2 and payload_sha256 != locator.content_sha256)
            or chain_sha256 != locator.chain_sha256):
        raise ValueError("V2 publication event hash proof mismatch")
    if len(body_bytes) > max_event_bytes or len(body_bytes) > remaining_total_bytes:
        raise ValueError("V2 publication snapshot byte budget exceeded")
    event = RollingHistoryV2PublicationEvent(
        locator=locator,
        page_index=page_index,
        event_type=event_type,
        role=_publication_role(event_type),
        attachments=attachments,
        body=body,
        body_utf8_byte_length=len(body_bytes),
        body_sha256=body_sha256,
        payload_sha256=payload_sha256,
        event_chain_sha256=chain_sha256,
        text_sha256=text_sha256,
        body_manifest_sha256=body_manifest_sha256,
        event_manifest_sha256=event_manifest_sha256,
        body_chunk_sha256s=tuple(body_chunk_sha256s),
        event_chunk_sha256s=tuple(event_chunk_sha256s),
    )
    return event, len(body_bytes)


def read_rolling_history_v2_publication_snapshot(
    *,
    max_pages: int = _PUBLICATION_SNAPSHOT_MAX_PAGES,
    max_events: int = _PUBLICATION_SNAPSHOT_MAX_EVENTS,
    max_event_bytes: int = _PUBLICATION_SNAPSHOT_MAX_EVENT_BYTES,
    max_total_bytes: int = _PUBLICATION_SNAPSHOT_MAX_TOTAL_BYTES,
    max_head_revision_number: int = _PUBLICATION_SNAPSHOT_MAX_HEAD_REVISION,
) -> RollingHistoryV2PublicationSnapshot:
    """Capture a complete, byte-bounded snapshot of the current active V2 head.

    Every page and body read is owner-bound and pinned to the exact head returned
    by the indexed active-pointer read. Old/unindexed revisions and any changed
    source fence or head fail closed before a snapshot is returned.
    """
    limits = (
        (max_pages, _PUBLICATION_SNAPSHOT_MAX_PAGES),
        (max_events, _PUBLICATION_SNAPSHOT_MAX_EVENTS),
        (max_event_bytes, _PUBLICATION_SNAPSHOT_MAX_EVENT_BYTES),
        (max_total_bytes, _PUBLICATION_SNAPSHOT_MAX_TOTAL_BYTES),
        (max_head_revision_number, _PUBLICATION_SNAPSHOT_MAX_HEAD_REVISION),
    )
    if any(type(value) is not int or value < 1 or value > ceiling
           for value, ceiling in limits):
        raise ValueError("Invalid V2 publication snapshot limits")

    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)

    active_read = _finish_active_branch_pointer_operation(
        binding,
        OperationMode.READ,
        lambda handle, identity, operation_namespace: _read_active_indexed_branch_pointer(
            handle, scope, operation_namespace,
            identity.canonical_namespace_digest,
        ),
    )
    active = _active_branch_pointer_result(active_read, "readback_confirmed")
    identity = get_rolling_history_owner_identity()
    if (active.outcome != "readback_confirmed"
            or active.pointer is None or active.pointer.state != "active"
            or active.pointer.branch_id is None
            or active.branch is None or active.head is None
            or active.head.canonical_namespace_digest != identity.canonical_namespace_digest
            or active.head.head.branch_id != active.pointer.branch_id):
        raise RollingHistoryOwnerScopeError("Active rolling history head is unavailable")

    initial_page = read_rolling_history_v2_locator_page(
        branch_id=active.pointer.branch_id,
    )
    pin = initial_page.pin
    expected_head = active.head.head
    if (initial_page.page_index != 0
            or initial_page.head != expected_head
            or pin.head != expected_head
            or pin.branch_id != active.pointer.branch_id
            or pin.canonical_namespace_digest != identity.canonical_namespace_digest
            or pin.index.page_count > max_pages
            or pin.index.locator_count > max_events
            or expected_head.revision_number > max_head_revision_number
            or pin.index.page_count < 1
            or pin.index.locator_count < 1):
        raise ValueError("Active V2 head exceeds publication snapshot limits")

    pages: list[RollingHistoryV2PublicationPageProof] = []
    events: list[RollingHistoryV2PublicationEvent] = []
    total_bytes = 0
    page = initial_page
    page_cursor = None
    last_page_cursor = None
    expected_sequence = 1
    while True:
        if (page.page_index != len(pages)
                or page.pin != pin or page.head != expected_head
                or page.source_fence != pin.source_fence
                or page.lineage_proof.proof_sha256 == ""):
            raise ValueError("V2 publication page does not match its pinned head")
        if len(pages) >= max_pages:
            raise ValueError("V2 publication page budget exceeded")
        if len(events) + len(page.locators) > max_events:
            raise ValueError("V2 publication event budget exceeded")
        pages.append(RollingHistoryV2PublicationPageProof(
            page_index=page.page_index,
            locators=page.locators,
            index_proof=page.proof,
            lineage_proof_sha256=page.lineage_proof.proof_sha256,
        ))
        for locator in page.locators:
            if (type(locator) is not RollingHistoryV2EventLocator
                    or locator.sequence != expected_sequence):
                raise ValueError("V2 publication source sequence is not continuous")
            event, event_bytes = _read_v2_publication_event(
                pin=pin,
                page_index=page.page_index,
                locator=locator,
                max_event_bytes=max_event_bytes,
                remaining_total_bytes=max_total_bytes - total_bytes,
            )
            events.append(event)
            total_bytes += event_bytes
            expected_sequence += 1
        if page.next_cursor is None:
            break
        if len(pages) >= max_pages:
            raise ValueError("V2 publication page budget exceeded")
        page_cursor = page.next_cursor
        last_page_cursor = page_cursor
        page = read_rolling_history_v2_locator_page(pin=pin, cursor=page_cursor)

    if (len(pages) != pin.index.page_count
            or len(events) != pin.index.locator_count
            or expected_sequence != pin.index.locator_count + 1
            or total_bytes > max_total_bytes):
        raise ValueError("V2 publication snapshot is incomplete")

    # Re-read the last pinned page to make sure its source fence did not advance
    # while body chunks were being collected.
    tail_page = read_rolling_history_v2_locator_page(
        pin=pin, cursor=last_page_cursor,
    )
    if (tail_page.page_index != len(pages) - 1
            or tail_page.page_index != (0 if last_page_cursor is None else last_page_cursor.next_page_index)
            or tail_page.pin != pin or tail_page.head != expected_head
            or tail_page.source_fence != pin.source_fence):
        raise ValueError("V2 publication source fence changed during snapshot")

    tail_read = _finish_active_branch_pointer_operation(
        binding,
        OperationMode.READ,
        lambda handle, current_identity, operation_namespace: _read_active_indexed_branch_pointer(
            handle, scope, operation_namespace,
            current_identity.canonical_namespace_digest,
        ),
    )
    tail_active = _active_branch_pointer_result(tail_read, "readback_confirmed")
    if (tail_active.pointer != active.pointer
            or tail_active.branch != active.branch
            or tail_active.head is None
            or tail_active.head.head != expected_head
            or tail_active.head.canonical_namespace_digest
            != identity.canonical_namespace_digest):
        raise RollingHistoryV2Conflict("Active V2 head changed during publication snapshot")

    snapshot = RollingHistoryV2PublicationSnapshot(
        canonical_namespace_digest=identity.canonical_namespace_digest,
        scope_sha256=_scope_digest(scope),
        delete_epoch=identity.delete_epoch,
        request_id=scope.request_id,
        active_pointer=active.pointer,
        head=expected_head,
        bound_head=active.head,
        page_pin=pin,
        page_proofs=tuple(pages),
        events=tuple(events),
        total_body_utf8_bytes=total_bytes,
        snapshot_sha256="0" * 64,
    )
    return replace(
        snapshot,
        snapshot_sha256=_publication_snapshot_digest(snapshot),
    )


def _publication_snapshot_source_maps(
    snapshot: RollingHistoryV2PublicationSnapshot,
    scope: ContextEventScope,
    identity,
) -> tuple[dict[int, str], dict[int, str], tuple[RollingHistoryV2EventLocator, ...]]:
    if (type(snapshot) is not RollingHistoryV2PublicationSnapshot
            or snapshot.canonical_namespace_digest != identity.canonical_namespace_digest
            or snapshot.scope_sha256 != _scope_digest(scope)
            or snapshot.delete_epoch != identity.delete_epoch
            or snapshot.request_id != scope.request_id
            or type(snapshot.active_pointer) is not RollingHistoryV2ActiveBranchPointer
            or type(snapshot.head) is not RollingHistoryV2Head
            or type(snapshot.bound_head) is not _BoundRollingHistoryV2Head
            or type(snapshot.page_pin) is not RollingHistoryV2LocatorPagePin
            or type(snapshot.page_proofs) is not tuple
            or type(snapshot.events) is not tuple
            or type(snapshot.total_body_utf8_bytes) is not int
            or snapshot.total_body_utf8_bytes < 0
            or snapshot.total_body_utf8_bytes > _PUBLICATION_SNAPSHOT_MAX_TOTAL_BYTES
            or type(snapshot.snapshot_sha256) is not str
            or _HASH.fullmatch(snapshot.snapshot_sha256) is None
            or snapshot.snapshot_sha256 != _publication_snapshot_digest(snapshot)):
        raise ValueError("Invalid V2 publication snapshot binding")
    _validate_active_branch_pointer_token(snapshot.active_pointer)
    _validate_rolling_history_v2_head_token(snapshot.head)
    _validate_bound_head_token(snapshot.bound_head)
    _validate_locator_page_pin(snapshot.page_pin)
    if (snapshot.active_pointer.state != "active"
            or snapshot.active_pointer.canonical_namespace_digest
            != identity.canonical_namespace_digest
            or snapshot.bound_head.head != snapshot.head
            or snapshot.bound_head.canonical_namespace_digest
            != identity.canonical_namespace_digest
            or snapshot.active_pointer.branch_id != snapshot.head.branch_id
            or snapshot.page_pin.head != snapshot.head
            or snapshot.page_pin.branch_id != snapshot.head.branch_id
            or snapshot.page_pin.canonical_namespace_digest
            != identity.canonical_namespace_digest
            or snapshot.page_pin.index.page_count != len(snapshot.page_proofs)
            or snapshot.page_pin.index.locator_count != len(snapshot.events)
            or not 1 <= len(snapshot.page_proofs) <= _PUBLICATION_SNAPSHOT_MAX_PAGES
            or not 1 <= len(snapshot.events) <= _PUBLICATION_SNAPSHOT_MAX_EVENTS
            or snapshot.head.revision_number > _PUBLICATION_SNAPSHOT_MAX_HEAD_REVISION):
        raise ValueError("V2 publication snapshot head or limits mismatch")

    body_by_sequence: dict[int, str] = {}
    role_by_sequence: dict[int, str] = {}
    locators: list[RollingHistoryV2EventLocator] = []
    event_index = 0
    total_bytes = 0
    expected_sequence = 1
    for page_index, page in enumerate(snapshot.page_proofs):
        if (type(page) is not RollingHistoryV2PublicationPageProof
                or page.page_index != page_index
                or type(page.locators) is not tuple
                or type(page.index_proof) is not tuple
                or type(page.lineage_proof_sha256) is not str
                or _HASH.fullmatch(page.lineage_proof_sha256) is None):
            raise ValueError("Invalid V2 publication page proof")
        for locator in page.locators:
            if (type(locator) is not RollingHistoryV2EventLocator
                    or locator.sequence != expected_sequence
                    or type(locator.event_id) is not str or _HASH.fullmatch(locator.event_id) is None
                    or type(locator.request_id) is not str
                    or re.fullmatch(r"[0-9a-f]{32}", locator.request_id) is None
                    or type(locator.version) is not int or locator.version not in (2, 3)
                    or type(locator.content_sha256) is not str
                    or _HASH.fullmatch(locator.content_sha256) is None
                    or type(locator.chain_sha256) is not str
                    or _HASH.fullmatch(locator.chain_sha256) is None
                    or event_index >= len(snapshot.events)):
                raise ValueError("Invalid or discontinuous V2 publication locator")
            event = snapshot.events[event_index]
            if (type(event) is not RollingHistoryV2PublicationEvent
                    or event.locator != locator or event.page_index != page_index
                    or type(event.event_type) is not str
                    or event.role != _publication_role(event.event_type)
                    or type(event.attachments) is not tuple
                    or any(type(item) is not AttachmentReference for item in event.attachments)
                    or type(event.body) is not str
                    or type(event.body_utf8_byte_length) is not int
                    or type(event.body_sha256) is not str
                    or _HASH.fullmatch(event.body_sha256) is None
                    or type(event.payload_sha256) is not str
                    or _HASH.fullmatch(event.payload_sha256) is None
                    or event.event_chain_sha256 != locator.chain_sha256
                    or event.text_sha256 != event.body_sha256
                    or type(event.body_chunk_sha256s) is not tuple
                    or type(event.event_chunk_sha256s) is not tuple
                    or any(type(value) is not str or _HASH.fullmatch(value) is None
                           for value in event.body_chunk_sha256s + event.event_chunk_sha256s)):
                raise ValueError("Invalid V2 publication event data")
            body_bytes = event.body.encode("utf-8", errors="strict")
            expected_event_chunk_sha256s = tuple(
                _sha256(chunk_text.encode("utf-8", errors="strict"))
                for _start, _end, chunk_text in _split_event_text(event.body)
            )
            if locator.version == 2 and not body_bytes:
                expected_event_chunk_sha256s = (_sha256(b""),)
            payload_sha256 = _sha256(_canonical({
                "event_type": event.event_type,
                "content": event.body,
                "attachments": [asdict(item) for item in event.attachments],
            }))
            # V3 locator.content_sha256 commits the event-manifest core. Its
            # payload_sha256 is separate and is joined to the locator through
            # sequence marker and ID mapping revalidation in the WRITE below.
            proof_shape_valid = (
                locator.version == 3
                and type(event.event_manifest_sha256) is str
                and _HASH.fullmatch(event.event_manifest_sha256) is not None
                and type(event.body_manifest_sha256) is str
                and _HASH.fullmatch(event.body_manifest_sha256) is not None
                and not event.event_chunk_sha256s
                and len(event.body_chunk_sha256s)
                == (event.body_utf8_byte_length + _PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES - 1)
                // _PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES
                and event.body_chunk_sha256s == tuple(
                    _sha256(body_bytes[offset:offset + _PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES])
                    for offset in range(0, len(body_bytes), _PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES)
                )
            ) or (
                locator.version == 2
                and event.event_manifest_sha256 is None
                and event.body_manifest_sha256 is None
                and not event.body_chunk_sha256s
                and bool(event.event_chunk_sha256s)
                and event.event_chunk_sha256s == expected_event_chunk_sha256s
            )
            if (len(body_bytes) != event.body_utf8_byte_length
                    or len(body_bytes) > _PUBLICATION_SNAPSHOT_MAX_EVENT_BYTES
                    or _sha256(body_bytes) != event.body_sha256
                    or payload_sha256 != event.payload_sha256
                    or (locator.version == 2
                        and event.payload_sha256 != locator.content_sha256)
                    or not proof_shape_valid):
                raise ValueError("V2 publication body or payload proof mismatch")
            body_by_sequence[expected_sequence] = event.body
            role_by_sequence[expected_sequence] = event.role
            locators.append(locator)
            total_bytes += len(body_bytes)
            if total_bytes > _PUBLICATION_SNAPSHOT_MAX_TOTAL_BYTES:
                raise ValueError("V2 publication snapshot byte budget exceeded")
            expected_sequence += 1
            event_index += 1
    if (event_index != len(snapshot.events)
            or len(locators) != snapshot.page_pin.index.locator_count
            or total_bytes != snapshot.total_body_utf8_bytes):
        raise ValueError("V2 publication snapshot is incomplete")
    return body_by_sequence, role_by_sequence, tuple(locators)


def _revalidate_publication_snapshot_in_transaction(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    snapshot: RollingHistoryV2PublicationSnapshot,
    expected_head: _BoundRollingHistoryV2Head,
) -> _RawRollingHistoryV2StoreResult | _ConflictResult | _MigrationRequiredResult:
    maps = _publication_snapshot_source_maps(snapshot, scope, identity)
    locators = maps[2]
    if (expected_head != snapshot.bound_head
            or expected_head.head != snapshot.head
            or expected_head.canonical_namespace_digest
            != identity.canonical_namespace_digest):
        return _MigrationRequiredResult()
    active = _read_active_indexed_branch_pointer(
        handle, scope, namespace, identity.canonical_namespace_digest,
    )
    if (active.pointer != snapshot.active_pointer
            or active.pointer.state != "active"
            or active.pointer.branch_id != expected_head.head.branch_id
            or active.head is None or active.head.head != expected_head.head):
        return _ConflictResult("V2 publication active pointer or head changed")
    current = _read_indexed_head_for_page(
        handle, scope, namespace, expected_head.head.branch_id,
    )
    if isinstance(current, _MigrationRequiredResult):
        return current
    if (current.head != expected_head.head
            or active.branch != current.branch
            or active.head.head != current.head
            or current.locator_index != snapshot.page_pin.index
            or current.lineage_proof is None
            or any(page.lineage_proof_sha256 != current.lineage_proof.proof_sha256
                   for page in snapshot.page_proofs)):
        return _ConflictResult("V2 publication indexed head changed")
    for page in snapshot.page_proofs:
        actual_locators, actual_proof = _read_indexed_locator_page(
            handle, namespace, current.locator_index, page.page_index,
            include_proof=True,
        )
        if actual_locators != page.locators or actual_proof != page.index_proof:
            return _ConflictResult("V2 publication locator page proof changed")
    event_by_locator = {event.locator: event for event in snapshot.events}
    prefix = _scope_prefix(scope)
    from app.db.database import read_message_body_chunk_in_transaction

    for page in snapshot.page_proofs:
        for locator in page.locators:
            event = event_by_locator.get(locator)
            if event is None:
                return _ConflictResult("V2 publication event snapshot is incomplete")
            previous_chain_sha256 = (
                _genesis_chain(scope)
                if locator.sequence == 1
                else locators[locator.sequence - 2].chain_sha256
            )
            reference = _owner_history_read_event_reference_at_sequence(
                handle,
                scope,
                prefix,
                locator.sequence,
                expected_previous_chain_sha256=previous_chain_sha256,
            )
            reference_locator = RollingHistoryV2EventLocator(
                event_id=reference.event_id,
                request_id=reference.request_id,
                sequence=reference.sequence,
                version=reference.version,
                content_sha256=reference.content_sha256,
                chain_sha256=reference.chain_sha256,
            )
            if reference_locator != locator:
                return _ConflictResult("V2 publication event source mapping changed")

            if locator.version == 2:
                cursor = None
                offset = 0
                chunk_hashes: list[str] = []
                body_parts: list[str] = []
                first_chunk = True
                while True:
                    chunk = read_rolling_history_event_chunk_in_transaction(
                        handle,
                        scope,
                        identity.session_id,
                        event_id=locator.event_id if first_chunk else None,
                        cursor=cursor,
                        max_rows=3,
                        max_bytes=_PUBLICATION_SNAPSHOT_EVENT_CHUNK_BYTES,
                    )
                    first_chunk = False
                    if chunk is None:
                        return _ConflictResult("V2 publication event chunk is unavailable")
                    chunk_bytes = chunk.content_chunk.encode("utf-8", errors="strict")
                    if (chunk.event_id != locator.event_id
                            or chunk.payload_sha256 != locator.content_sha256
                            or chunk.chain_sha256 != locator.chain_sha256
                            or chunk.start_offset != offset
                            or chunk.end_offset - chunk.start_offset != len(chunk_bytes)
                            or chunk.event_type != event.event_type
                            or chunk.attachments != event.attachments
                            or chunk.text_sha256 != event.text_sha256
                            or _sha256(chunk_bytes) != (
                                event.event_chunk_sha256s[len(chunk_hashes)]
                                if len(chunk_hashes) < len(event.event_chunk_sha256s)
                                else ""
                            )):
                        return _ConflictResult("V2 publication event chunk proof changed")
                    offset = chunk.end_offset
                    body_parts.append(chunk.content_chunk)
                    chunk_hashes.append(_sha256(chunk_bytes))
                    cursor = chunk.next_cursor
                    if cursor is None:
                        break
                    if cursor.next_offset != offset or not chunk_bytes:
                        return _ConflictResult("V2 publication event chunk cursor changed")
                if (offset != event.body_utf8_byte_length
                        or tuple(chunk_hashes) != event.event_chunk_sha256s
                        or "".join(body_parts) != event.body):
                    return _ConflictResult("V2 publication event body readback changed")
                continue

            if locator.version != 3:
                return _ConflictResult("Unsupported V2 publication locator version")
            manifest_raw, too_large = _owner_history_read_setting_bounded(
                handle,
                _event_manifest_key(prefix, locator.event_id),
                4096,
            )
            if too_large or manifest_raw is None:
                return _ConflictResult("V2 publication body event manifest changed")
            manifest = _validate_body_backed_event_manifest(
                manifest_raw, scope, locator.sequence,
            )
            attachments = tuple(
                _parse_attachment(item) for item in manifest["attachments"]
            )
            body_store_manifest_sha256 = _sha256(_canonical({
                "body_id": manifest["body_id"],
                "session_id": scope.session_id,
                "byte_length": manifest["body_byte_length"],
                "sha256": manifest["body_sha256"],
                "chunk_size": manifest["body_chunk_size"],
                "chunk_count": manifest["body_chunk_count"],
                "final_chain_sha256": manifest["body_chain_sha256"],
                "status": "writer_verified",
                "encoding": "utf-8",
            }))
            if (event.event_manifest_sha256 != _sha256(manifest_raw)
                    or manifest["event_id"] != locator.event_id
                    or manifest["request_id"] != locator.request_id
                    or manifest["sequence"] != locator.sequence
                    or manifest["content_sha256"] != locator.content_sha256
                    or event.event_type != manifest["event_type"]
                    or event.attachments != attachments
                    or event.payload_sha256 != manifest["payload_sha256"]
                    or event.text_sha256 != manifest["text_sha256"]
                    or event.body_sha256 != manifest["body_sha256"]
                    or event.body_utf8_byte_length != manifest["body_byte_length"]
                    or event.event_chain_sha256 != manifest["chain_sha256"]
                    or event.body_manifest_sha256 != body_store_manifest_sha256
                    or body_store_manifest_sha256 != manifest["body_manifest_sha256"]):
                return _ConflictResult("V2 publication body event proof changed")

            expected_body = event.body.encode("utf-8", errors="strict")
            if (len(expected_body) != manifest["body_byte_length"]
                    or _sha256(expected_body) != manifest["body_sha256"]
                    or len(event.body_chunk_sha256s) != manifest["body_chunk_count"]):
                return _ConflictResult("V2 publication body event snapshot changed")
            body_parts: list[bytes] = []
            previous_body_chain_sha256 = None
            for chunk_index in range(manifest["body_chunk_count"]):
                byte_offset = chunk_index * manifest["body_chunk_size"]
                body_chunk = read_message_body_chunk_in_transaction(
                    handle,
                    session_id=identity.session_id,
                    body_id=manifest["body_id"],
                    byte_offset=byte_offset,
                    max_rows=3,
                    max_bytes=_PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES,
                )
                expected_length = min(
                    _PUBLICATION_SNAPSHOT_BODY_CHUNK_BYTES,
                    manifest["body_byte_length"] - byte_offset,
                )
                if (body_chunk is None
                        or body_chunk.body_id != manifest["body_id"]
                        or body_chunk.byte_offset != byte_offset
                        or body_chunk.next_offset != byte_offset + expected_length
                        or body_chunk.chunk_length != expected_length
                        or len(body_chunk.chunk_bytes) != expected_length
                        or body_chunk.manifest_byte_length != manifest["body_byte_length"]
                        or body_chunk.manifest_sha256 != manifest["body_sha256"]
                        or body_chunk.manifest_chunk_count != manifest["body_chunk_count"]
                        or body_chunk.manifest_chain_sha256 != manifest["body_chain_sha256"]
                        or (previous_body_chain_sha256 is not None
                            and body_chunk.previous_chain_sha256
                            != previous_body_chain_sha256)
                        or _sha256(body_chunk.chunk_bytes)
                        != event.body_chunk_sha256s[chunk_index]
                        or body_chunk.chunk_bytes
                        != expected_body[byte_offset:byte_offset + expected_length]):
                    return _ConflictResult("V2 publication body chunk proof changed")
                previous_body_chain_sha256 = body_chunk.chain_sha256
                body_parts.append(body_chunk.chunk_bytes)
            if (b"".join(body_parts) != expected_body
                    or (manifest["body_chunk_count"] > 0
                        and previous_body_chain_sha256 != manifest["body_chain_sha256"])
                    or (manifest["body_chunk_count"] == 0
                        and (expected_body or event.body_chunk_sha256s))):
                return _ConflictResult("V2 publication body chunk stream changed")
    try:
        source_fence = _read_current_migration_live_fence(
            handle, identity.session_id,
        )
    except LegacySnapshotGenerationConflict:
        return _ConflictResult("V2 publication source fence is unavailable")
    if source_fence != snapshot.page_pin.source_fence:
        return _ConflictResult("V2 publication source fence changed")
    return replace(current, visible_event_locators=locators)


def _compaction_source_message_id(value: object) -> bool:
    return (type(value) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", value, re.ASCII)
            is not None)


def _read_bounded_compaction_source_rows(
    handle: RollingHistorySqlHandle,
    session_id: str,
    message_ids: tuple[str, ...],
) -> dict[str, tuple[object, ...]]:
    if (type(message_ids) is not tuple
            or not 1 <= len(message_ids) <= _COMPACTION_SOURCE_BINDING_MAX_ROWS
            or any(not _compaction_source_message_id(value) for value in message_ids)
            or len(set(message_ids)) != len(message_ids)):
        raise RollingHistoryV2Conflict("Invalid compaction source message IDs")
    placeholders = ", ".join("?" for _ in message_ids)
    rows = _select_rows(
        handle,
        "SELECT id, role, "
        "CASE WHEN length(CAST(content AS BLOB)) <= ? THEN content END AS content, "
        "CASE WHEN sources IS NULL OR length(CAST(sources AS BLOB)) <= ? "
        "THEN sources END AS sources, "
        "CASE WHEN tool_calls IS NULL OR length(CAST(tool_calls AS BLOB)) <= ? "
        "THEN tool_calls END AS tool_calls, "
        "CASE WHEN activity IS NULL OR length(CAST(activity AS BLOB)) <= ? "
        "THEN activity END AS activity, created_at, "
        "length(CAST(content AS BLOB)) AS content_bytes, "
        "length(CAST(sources AS BLOB)) AS sources_bytes, "
        "length(CAST(tool_calls AS BLOB)) AS tool_calls_bytes, "
        "length(CAST(activity AS BLOB)) AS activity_bytes "
        f"FROM messages WHERE session_id = ? AND id IN ({placeholders}) "
        "ORDER BY id LIMIT ?",
        (
            MAX_BODY_EVENT_CONTENT_UTF8_BYTES,
            MAX_BODY_EVENT_METADATA_UTF8_BYTES,
            MAX_BODY_EVENT_METADATA_UTF8_BYTES,
            MAX_BODY_EVENT_METADATA_UTF8_BYTES,
            session_id,
            *message_ids,
            len(message_ids) + 1,
        ),
        (
            "id", "role", "content", "sources", "tool_calls", "activity",
            "created_at", "content_bytes", "sources_bytes", "tool_calls_bytes",
            "activity_bytes",
        ),
    )
    if len(rows) != len(message_ids):
        raise RollingHistoryV2Conflict("Compaction source rows are incomplete")
    by_id: dict[str, tuple[object, ...]] = {}
    for row in rows:
        message_id = row[0]
        if type(message_id) is not str or message_id in by_id:
            raise RollingHistoryV2Conflict("Compaction source rows are ambiguous")
        by_id[message_id] = row
    if set(by_id) != set(message_ids):
        raise RollingHistoryV2Conflict("Compaction source rows are out of scope")
    return by_id


def _decode_bounded_compaction_source_json(value: object, byte_length: object) -> object:
    if byte_length is None:
        if value is not None:
            raise RollingHistoryV2Conflict("Compaction source metadata length is unavailable")
        return None
    if (type(byte_length) is not int or byte_length < 0
            or byte_length > MAX_BODY_EVENT_METADATA_UTF8_BYTES
            or type(value) is not str):
        raise RollingHistoryV2Conflict("Compaction source metadata exceeds its byte budget")
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RollingHistoryV2Conflict("Compaction source metadata is invalid") from exc


def _require_compaction_source_fence_extension(
    source_mapping: dict,
    current_fence,
) -> None:
    after = source_mapping.get("source_fence_after")
    expected_fields = {
        "message_source_generation", "message_count", "non_append_generation",
    }
    if (type(after) is not dict or set(after) != expected_fields
            or any(type(value) is not int or value < 0 for value in after.values())):
        raise RollingHistoryV2Conflict("Compaction source append fence is unavailable")
    generation_delta = current_fence.message_source_generation - after["message_source_generation"]
    count_delta = current_fence.message_count - after["message_count"]
    if (after["non_append_generation"] != current_fence.non_append_generation
            or generation_delta < 0 or count_delta < 0
            or generation_delta != count_delta):
        raise RollingHistoryV2Conflict("Compaction source changed after its exact append")


def _new_compaction_context_source_binding(
    *,
    scope: ContextEventScope,
    identity,
    snapshot: RollingHistoryV2PublicationSnapshot,
    source_fence_sha256: str,
    recent_message_start_boundary: int,
    event: RollingHistoryBodyEventInput,
    source_mapping: dict,
) -> ContextEventSourceBinding:
    locator_value = source_mapping["locator"]
    locator = RollingHistoryV2EventLocator(**locator_value)
    source_metadata = {
        "message_id": event.message_id,
        "created_at": event.created_at,
        "message_event_sha256": _message_source_event_sha256(event),
        "source_identity_sha256": source_mapping["source_identity_sha256"],
        "append_fence": {
            "before": source_mapping["source_fence_before"],
            "after": source_mapping["source_fence_after"],
        },
        "batch_count": 1,
        "batch_index": 1,
    }
    source_metadata_json = _canonical(source_metadata)
    binding_value = {
        "schema_version": 1,
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "scope_sha256": _sha256(_canonical(asdict(scope))),
        "delete_epoch": identity.delete_epoch,
        "request_id": scope.request_id,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "head_sha256": snapshot.head.head_sha256,
        "source_fence_sha256": source_fence_sha256,
        "recent_message_start_boundary": recent_message_start_boundary,
        "locator": asdict(ContextEventV3Locator(
            event_id=locator.event_id,
            request_id=locator.request_id,
            sequence=locator.sequence,
            version=locator.version,
            content_sha256=locator.content_sha256,
            chain_sha256=locator.chain_sha256,
        )),
        "source_role": event.message_event["role"],
        "source_text_sha256": _sha256(event.body_text),
        "source_metadata_json": source_metadata_json,
        "source_metadata_sha256": _sha256(source_metadata_json),
        "source_identity_sha256": source_mapping["source_identity_sha256"],
    }
    return ContextEventSourceBinding(
        schema_version=binding_value["schema_version"],
        canonical_namespace_digest=binding_value["canonical_namespace_digest"],
        scope_sha256=binding_value["scope_sha256"],
        delete_epoch=binding_value["delete_epoch"],
        request_id=binding_value["request_id"],
        snapshot_sha256=binding_value["snapshot_sha256"],
        head_sha256=binding_value["head_sha256"],
        source_fence_sha256=binding_value["source_fence_sha256"],
        recent_message_start_boundary=binding_value["recent_message_start_boundary"],
        locator=ContextEventV3Locator(**binding_value["locator"]),
        source_role=binding_value["source_role"],
        source_text_sha256=binding_value["source_text_sha256"],
        source_metadata_json=binding_value["source_metadata_json"],
        source_metadata_sha256=binding_value["source_metadata_sha256"],
        source_identity_sha256=binding_value["source_identity_sha256"],
        binding_sha256=_sha256(_canonical(binding_value)),
    )


def read_rolling_history_v2_compaction_source_bindings(
    *,
    source_message_ids: tuple[str, ...],
    archive_prefix_message_count: int,
    inherited_binding_hints: tuple[ContextEventSourceBinding, ...] = (),
) -> RollingHistoryV2CompactionSourceBindingResult:
    """Re-authenticate the current message run and its archived V3 prefix."""
    if (type(source_message_ids) is not tuple
            or not 1 <= len(source_message_ids) <= _COMPACTION_SOURCE_BINDING_MAX_ROWS
            or any(not _compaction_source_message_id(value) for value in source_message_ids)
            or len(set(source_message_ids)) != len(source_message_ids)
            or type(archive_prefix_message_count) is not int
            or not 1 <= archive_prefix_message_count <= len(source_message_ids)
            or archive_prefix_message_count > _COMPACTION_SOURCE_BINDING_MAX_MESSAGES
            or type(inherited_binding_hints) is not tuple
            or len(inherited_binding_hints) > _COMPACTION_SOURCE_BINDING_MAX_MESSAGES
            or archive_prefix_message_count + len(inherited_binding_hints)
            > _COMPACTION_SOURCE_BINDING_MAX_MESSAGES
            or len(source_message_ids) + len(inherited_binding_hints)
            > _COMPACTION_SOURCE_BINDING_MAX_ROWS):
        raise ValueError("Invalid compaction source binding request")

    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)
    snapshot = read_rolling_history_v2_publication_snapshot()

    inherited: list[tuple[str, ContextEventSourceBinding]] = []
    for hint in inherited_binding_hints:
        if type(hint) is not ContextEventSourceBinding or hint.locator.version != 3:
            raise RollingHistoryV2Conflict("Inherited V3 source binding is unavailable")
        hint_scope = replace(scope, request_id=hint.request_id)
        try:
            _source_binding_value(hint, hint_scope)
            metadata = json.loads(hint.source_metadata_json)
        except Exception as exc:
            raise RollingHistoryV2Conflict("Inherited V3 source binding is invalid") from exc
        if (hint.canonical_namespace_digest != get_rolling_history_owner_identity().canonical_namespace_digest
                or hint.delete_epoch != get_rolling_history_owner_identity().delete_epoch
                or type(metadata) is not dict
                or not _compaction_source_message_id(metadata.get("message_id"))
                or hint.recent_message_start_boundary <= hint.locator.sequence):
            raise RollingHistoryV2Conflict("Inherited V3 source binding is out of scope")
        inherited.append((metadata["message_id"], hint))

    combined_ids = tuple(message_id for message_id, _hint in inherited) + source_message_ids
    if (len(set(combined_ids)) != len(combined_ids)
            or len(combined_ids) > _COMPACTION_SOURCE_BINDING_MAX_ROWS):
        raise RollingHistoryV2Conflict("Compaction source prefix is ambiguous")
    identity = get_rolling_history_owner_identity()
    if scope.session_id != identity.session_id:
        raise RollingHistoryOwnerScopeError("Rolling history owner session scope is unavailable")
    if scope.session_epoch != identity.delete_epoch:
        raise RollingHistoryOwnerScopeError("Rolling history owner deletion epoch is unavailable")

    def operation(handle: RollingHistorySqlHandle):
        try:
            source_fence = read_message_source_fence_in_transaction(
                handle, scope.session_id,
            )
        except Exception:
            return _ConflictResult("Current message source fence is unavailable")
        try:
            current = _revalidate_publication_snapshot_in_transaction(
                handle, scope, namespace, identity, snapshot, snapshot.bound_head,
            )
        except Exception:
            return _ConflictResult("V3 publication snapshot could not be revalidated")
        if type(current) is not _RawRollingHistoryV2StoreResult:
            return current
        try:
            body_by_sequence, role_by_sequence, locators = _publication_snapshot_source_maps(
                snapshot, scope, identity,
            )
        except Exception:
            return _ConflictResult("V3 publication source map is unavailable")
        if source_fence.message_count != len(combined_ids):
            return _ConflictResult("Current message count does not match the full source carrier")
        if not combined_ids:
            return _ConflictResult("Compaction source prefix is empty")
        try:
            rows_by_id = _read_bounded_compaction_source_rows(
                handle, scope.session_id, combined_ids,
            )
        except Exception:
            return _ConflictResult("Current compaction source rows are unavailable")

        new_bindings: list[ContextEventSourceBinding] = []
        boundary = len(inherited) + archive_prefix_message_count + 1
        if boundary > len(locators) + 1:
            return _ConflictResult("V3 recent-message boundary exceeds the active head")
        source_fence_sha256 = _sha256(_canonical(asdict(source_fence)))
        hint_by_message_id = {message_id: hint for message_id, hint in inherited}
        for expected_sequence, message_id in enumerate(combined_ids, start=1):
            row = rows_by_id.get(message_id)
            if row is None:
                return _ConflictResult("Current compaction source row is missing")
            (_row_id, role, body_text, sources_raw, tool_calls_raw, activity_raw,
             created_at, body_bytes, sources_bytes, tool_calls_bytes, activity_bytes) = row
            if (type(role) is not str or role not in ("user", "assistant")
                    or type(body_bytes) is not int or body_bytes < 0
                    or body_bytes > MAX_BODY_EVENT_CONTENT_UTF8_BYTES
                    or type(body_text) is not str
                    or type(created_at) is not str
                    or not 1 <= len(created_at.encode("utf-8", errors="strict")) <= 128):
                return _ConflictResult("Current compaction source row is not bindable")
            try:
                message_event = {
                    "message_id": message_id,
                    "expected_incarnation_id": identity.incarnation_id,
                    "role": role,
                    "content": body_text,
                    "sources": _decode_bounded_compaction_source_json(
                        sources_raw, sources_bytes,
                    ),
                    "tool_calls": _decode_bounded_compaction_source_json(
                        tool_calls_raw, tool_calls_bytes,
                    ),
                    "activity": _decode_bounded_compaction_source_json(
                        activity_raw, activity_bytes,
                    ),
                }
                event = RollingHistoryBodyEventInput(
                    message_event=message_event,
                    body_text=body_text,
                    created_at=created_at,
                    event_type="user_text" if role == "user" else "assistant_text",
                )
                source_mapping = _read_message_source_mapping(
                    handle,
                    scope,
                    namespace,
                    identity,
                    event,
                    message_event_sha256=_message_source_event_sha256(event),
                    source_payload_sha256=_message_source_payload_sha256(event),
                )
            except Exception:
                return _ConflictResult("V3 owner source mapping is unavailable")
            if source_mapping is None:
                return _ConflictResult("V3 owner source mapping is missing")
            try:
                _require_compaction_source_fence_extension(source_mapping, source_fence)
                locator = RollingHistoryV2EventLocator(**source_mapping["locator"])
            except Exception:
                return _ConflictResult("V3 owner source mapping is invalid")
            observed_role = role_by_sequence.get(expected_sequence)
            if observed_role == "human":
                observed_role = "user"
            if (locator.version != 3 or locator.sequence != expected_sequence
                    or expected_sequence > len(locators)
                    or locators[expected_sequence - 1] != locator
                    or body_by_sequence.get(expected_sequence) != body_text
                    or observed_role != role):
                return _ConflictResult("V3 owner source is not an exact adjacent prefix")
            try:
                original_head = _decode_head(_canonical(source_mapping["head"]))
                original_result = _read_result_for_head(
                    handle, scope, namespace, original_head,
                )
                _verify_message_source_locator_membership(
                    handle, namespace, original_result, source_mapping,
                )
            except Exception:
                return _ConflictResult("V3 source is not in its original committed head")

            hint = hint_by_message_id.get(message_id)
            if hint is not None:
                if (hint.locator != ContextEventV3Locator(
                        event_id=locator.event_id,
                        request_id=locator.request_id,
                        sequence=locator.sequence,
                        version=locator.version,
                        content_sha256=locator.content_sha256,
                        chain_sha256=locator.chain_sha256,
                    )
                        or hint.source_role != role
                        or hint.source_text_sha256 != _sha256(body_text)):
                    return _ConflictResult("Inherited V3 source hint no longer matches its owner row")
                continue
            source_index = expected_sequence - len(inherited) - 1
            if source_index < archive_prefix_message_count:
                try:
                    new_bindings.append(_new_compaction_context_source_binding(
                        scope=scope,
                        identity=identity,
                        snapshot=snapshot,
                        source_fence_sha256=source_fence_sha256,
                        recent_message_start_boundary=boundary,
                        event=event,
                        source_mapping=source_mapping,
                    ))
                except Exception:
                    return _ConflictResult("V3 compaction source binding could not be issued")

        try:
            final_source_fence = read_message_source_fence_in_transaction(
                handle, scope.session_id,
            )
        except Exception:
            return _ConflictResult("Current message source fence changed")
        if final_source_fence != source_fence:
            return _ConflictResult("Current message source fence changed")
        if len(new_bindings) != archive_prefix_message_count:
            return _ConflictResult("V3 compaction source bindings are incomplete")
        return RollingHistoryV2CompactionSourceBindingResult(
            snapshot_sha256=snapshot.snapshot_sha256,
            head_sha256=snapshot.head.head_sha256,
            source_fence_sha256=source_fence_sha256,
            recent_message_start_boundary=boundary,
            source_bindings=tuple(new_bindings),
        )

    return _finish_operation(
        binding,
        OperationMode.READ,
        operation,
        expected_head=snapshot.bound_head,
    )


def read_rolling_history_v2_branch_head(
    branch_id: str,
) -> RollingHistoryV2StoreResult:
    """Read and verify a branch head and its complete visible source prefix."""
    if type(branch_id) is not str or _HASH.fullmatch(branch_id) is None:
        raise ValueError("Invalid rolling history v2 branch ID")
    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle):
        return _read_current_head(handle, scope, namespace, branch_id)

    return _finish_operation(binding, OperationMode.READ, operation)


def read_rolling_history_v2_active_branch_pointer() -> RollingHistoryV2ActiveBranchPointerResult:
    """Read the verified local branch selection for the current host owner."""
    binding = _current_binding()
    scope = binding.scope
    result = _finish_active_branch_pointer_operation(
        binding,
        OperationMode.READ,
        lambda handle, identity, namespace: _read_active_branch_pointer(
            handle, scope, namespace, identity.canonical_namespace_digest,
        ),
    )
    return _active_branch_pointer_result(result, "readback_confirmed")


def create_rolling_history_v2_active_branch_pointer(
    *,
    expected_pointer: RollingHistoryV2ActiveBranchPointer,
    target_expected_head: _BoundRollingHistoryV2Head,
) -> RollingHistoryV2ActiveBranchPointerResult:
    """Select the first verified branch only from an explicit unset pointer.

    The owner-bound unset snapshot is rechecked in the same WRITE transaction
    that validates the target head and creates the pointer and initialization
    marker. A successful return requires a separate committed readback.
    """
    return _select_rolling_history_v2_active_branch_pointer(
        expected_pointer=expected_pointer,
        target_expected_head=target_expected_head,
        expected_state="unset",
    )


def switch_rolling_history_v2_active_branch_pointer(
    *,
    expected_pointer: RollingHistoryV2ActiveBranchPointer,
    target_expected_head: _BoundRollingHistoryV2Head,
) -> RollingHistoryV2ActiveBranchPointerResult:
    """Switch local branch selection using pointer and target-head CAS tokens.

    A successful return requires a separate committed readback. If that
    readback cannot confirm this exact pointer operation, the result is
    outcome_unknown and carries no usable pointer token.
    """
    return _select_rolling_history_v2_active_branch_pointer(
        expected_pointer=expected_pointer,
        target_expected_head=target_expected_head,
        expected_state="active",
    )


def prepare_rolling_history_v2_summary(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    summary: str,
    idempotency_key: str,
) -> RollingHistoryV2PreparedSummaryRecord:
    """Durably prepare an exact summary without advancing or publishing a head.

    The source manifest is derived from the branch's verified visible prefix.
    The returned value is read back in a separate owner-bound transaction after
    the write transaction commits.
    """
    _validate_bound_head_token(expected_head)
    if type(summary) is not str:
        raise ValueError("Invalid rolling history v2 summary")
    binding = _current_binding()
    scope = binding.scope
    _require_head_scope(expected_head, scope)
    prepared_id, key_sha256 = _idempotency_context(scope, idempotency_key)
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle):
        identity = get_rolling_history_owner_identity()
        active_pointer = _read_active_branch_pointer(
            handle, scope, namespace, identity.canonical_namespace_digest,
        )
        if (active_pointer.pointer.state != "active"
                or active_pointer.pointer.branch_id != expected_head.head.branch_id):
            return _ConflictResult(
                "Rolling history v2 prepared-summary branch is not active",
            )

        current = _read_current_head(
            handle, scope, namespace, expected_head.head.branch_id,
        )
        if current.head != expected_head.head:
            return _ConflictResult("Rolling history v2 expected-head conflict")

        value = _prepared_summary_record_value(
            scope, identity.canonical_namespace_digest, current,
            summary, prepared_id, key_sha256,
        )
        encoded = _canonical(value)
        key = namespace + "prepared_summary:" + prepared_id
        existing = _read_setting(handle, key)
        if existing is not None:
            replay = _decode_prepared_summary_record(
                handle, scope, namespace, identity, existing,
                expected_prepared_id=prepared_id, current=current,
            )
            if isinstance(replay, _ConflictResult):
                return replay
            if (replay.prepared.idempotency_key_sha256 != key_sha256
                    or replay.payload_sha256 != value["payload_sha256"]):
                return _ConflictResult(
                    "Rolling history v2 prepared-summary idempotency conflict",
                )
            if replay.record_json != encoded:
                raise ValueError("Rolling history v2 prepared-summary retry mismatch")
            return replay

        _persist_immutable(handle, key, encoded)
        persisted = _read_setting(handle, key)
        if persisted != encoded:
            raise ValueError("Rolling history v2 prepared summary is unavailable")
        return _decode_prepared_summary_record(
            handle, scope, namespace, identity, persisted,
            expected_prepared_id=prepared_id, current=current,
        )

    committed = _finish_operation(
        binding, OperationMode.WRITE, operation, expected_head=expected_head,
    )
    return read_rolling_history_v2_prepared_summary(
        committed.prepared.prepared_id,
    )


def read_rolling_history_v2_prepared_summary(
    prepared_id: str,
) -> RollingHistoryV2PreparedSummaryRecord:
    """Read and fully revalidate a committed PREPARED summary under its owner."""
    if type(prepared_id) is not str or _HASH.fullmatch(prepared_id) is None:
        raise ValueError("Invalid rolling history v2 prepared summary ID")
    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle):
        identity = get_rolling_history_owner_identity()
        raw = _read_setting(
            handle, namespace + "prepared_summary:" + prepared_id,
        )
        if raw is None:
            return _ConflictResult("Rolling history v2 prepared summary is unavailable")
        return _decode_prepared_summary_record(
            handle, scope, namespace, identity, raw,
            expected_prepared_id=prepared_id,
        )

    return _finish_operation(binding, OperationMode.READ, operation)


def prepare_rolling_history_v2_grounded_summary(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    summary: "RollingSummary",
    recent_message_start_boundary: int,
    idempotency_key: str,
) -> RollingHistoryV2PreparedGroundedSummaryRecord:
    """Persist a versioned summary snapshot with source quotes and boundaries.

    Every claim is checked against a message body decoded from the active
    revision's verified event history. The committed record is read back in a
    separate owner-bound transaction. PREPARED does not publish a head or
    confirm downstream acknowledgement or completion.
    """
    _validate_bound_head_token(expected_head)
    summary_value = _rolling_summary_to_value(summary, recent_message_start_boundary)
    binding = _current_binding()
    scope = binding.scope
    _require_head_scope(expected_head, scope)
    prepared_id, key_sha256 = _grounded_summary_idempotency_context(
        scope, idempotency_key,
    )
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle, identity, operation_namespace):
        active_pointer = _read_active_branch_pointer(
            handle, scope, operation_namespace,
            identity.canonical_namespace_digest,
        )
        if (active_pointer.pointer.state != "active"
                or active_pointer.pointer.branch_id != expected_head.head.branch_id):
            return _ConflictResult(
                "Rolling history v2 grounded-summary branch is not active",
            )

        current = _read_current_head(
            handle, scope, operation_namespace, expected_head.head.branch_id,
        )
        if current.head != expected_head.head:
            return _ConflictResult("Rolling history v2 expected-head conflict")
        if (active_pointer.branch != current.branch
                or active_pointer.head is None
                or active_pointer.head.head != current.head):
            raise ValueError("Rolling history v2 active-branch pointer result mismatch")

        message_bodies, message_roles = _read_visible_rolling_message_sources(
            handle, scope, current.visible_event_locators,
        )
        source_manifest = _grounded_summary_source_manifest(
            scope, identity.canonical_namespace_digest, current,
            summary_value, recent_message_start_boundary, message_bodies,
            message_roles,
        )
        value = _grounded_summary_record_value(
            scope, identity.canonical_namespace_digest, current,
            summary_value, recent_message_start_boundary, source_manifest,
            prepared_id, key_sha256,
        )
        encoded = _canonical(value)
        key = operation_namespace + "prepared_grounded_summary:" + prepared_id
        existing = _read_setting(handle, key)
        if existing is not None:
            replay = _decode_grounded_summary_record(
                handle, scope, operation_namespace, identity, existing,
                expected_prepared_id=prepared_id, current=current,
            )
            if isinstance(replay, _ConflictResult):
                return replay
            if (replay.record_json != encoded
                    or replay.payload_sha256 != value["payload_sha256"]):
                return _ConflictResult(
                    "Rolling history v2 grounded-summary idempotency conflict",
                )
            return replay

        _persist_immutable(handle, key, encoded)
        persisted = _read_setting(handle, key)
        if persisted != encoded:
            raise ValueError("Rolling history v2 grounded summary is unavailable")
        return _decode_grounded_summary_record(
            handle, scope, operation_namespace, identity, persisted,
            expected_prepared_id=prepared_id, current=current,
        )

    committed = _finish_grounded_summary_operation(
        binding, OperationMode.WRITE, operation, expected_head=expected_head,
    )
    return read_rolling_history_v2_grounded_summary(
        committed.prepared_id,
    )


def read_rolling_history_v2_grounded_summary(
    prepared_id: str,
) -> RollingHistoryV2PreparedGroundedSummaryRecord:
    """Read and revalidate a prepared summary under the current owner scope."""
    if type(prepared_id) is not str or _HASH.fullmatch(prepared_id) is None:
        raise ValueError("Invalid rolling history v2 grounded-summary ID")
    binding = _current_binding()
    scope = binding.scope
    namespace = _namespace(scope)

    def operation(handle: RollingHistorySqlHandle, identity, operation_namespace):
        raw = _read_setting(
            handle, operation_namespace + "prepared_grounded_summary:" + prepared_id,
        )
        if raw is None:
            return _ConflictResult(
                "Rolling history v2 grounded summary is unavailable",
            )
        return _decode_grounded_summary_record(
            handle, scope, operation_namespace, identity, raw,
            expected_prepared_id=prepared_id,
        )

    return _finish_grounded_summary_operation(
        binding, OperationMode.READ, operation,
    )


def prepare_rolling_history_v2_summary_ledger(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    payload: object,
    idempotency_key: str,
) -> RollingHistoryV2PreparedSummaryLedgerRecord:
    """Persist bridge candidates after revalidating them against visible events.

    Candidate identity strings are not authorization inputs. The current host
    owner scope and expected head select the namespace and revision; every
    candidate and evidence locator is then matched to that revision's verified
    visible event prefix before an immutable PREPARED record is written. The
    result is returned only after a separate owner-bound readback.
    """
    _validate_bound_head_token(expected_head)
    entries = _summary_ledger_entries_from_payload(payload)
    binding = _current_binding()
    scope = binding.scope
    _require_head_scope(expected_head, scope)
    prepared_id, key_sha256 = _summary_ledger_idempotency_context(
        scope, idempotency_key,
    )

    def operation(handle: RollingHistorySqlHandle, identity, operation_namespace):
        active_pointer = _read_active_branch_pointer(
            handle, scope, operation_namespace,
            identity.canonical_namespace_digest,
        )
        if (active_pointer.pointer.state != "active"
                or active_pointer.pointer.branch_id != expected_head.head.branch_id):
            return _ConflictResult(
                "Rolling history v2 summary-ledger branch is not active",
            )

        current = _read_current_head(
            handle, scope, operation_namespace, expected_head.head.branch_id,
        )
        if current.head != expected_head.head:
            return _ConflictResult("Rolling history v2 expected-head conflict")
        if (active_pointer.branch != current.branch
                or active_pointer.head is None
                or active_pointer.head.head != current.head):
            raise ValueError("Rolling history v2 active-branch pointer result mismatch")

        body_by_sequence, role_by_sequence = _read_visible_rolling_message_sources(
            handle, scope, current.visible_event_locators,
        )
        source_manifest = _summary_ledger_source_manifest(
            scope, identity.canonical_namespace_digest, current, entries,
            body_by_sequence, role_by_sequence,
        )
        value = _summary_ledger_record_value(
            scope, identity.canonical_namespace_digest, current,
            source_manifest, prepared_id, key_sha256,
        )
        encoded = _canonical(value)
        key = operation_namespace + "prepared_summary_ledger:" + prepared_id
        existing = _read_setting(handle, key)
        if existing is not None:
            replay = _decode_summary_ledger_record(
                handle, scope, operation_namespace, identity, existing,
                expected_prepared_id=prepared_id, current=current,
            )
            if isinstance(replay, _ConflictResult):
                return replay
            if (replay.record_json != encoded
                    or replay.payload_sha256 != value["payload_sha256"]):
                return _ConflictResult(
                    "Rolling history v2 summary-ledger idempotency conflict",
                )
            return replay

        _persist_immutable(handle, key, encoded)
        persisted = _read_setting(handle, key)
        if persisted != encoded:
            raise ValueError("Rolling history v2 prepared summary ledger is unavailable")
        return _decode_summary_ledger_record(
            handle, scope, operation_namespace, identity, persisted,
            expected_prepared_id=prepared_id, current=current,
        )

    committed = _finish_summary_ledger_operation(
        binding, OperationMode.WRITE, operation, expected_head=expected_head,
    )
    return read_rolling_history_v2_summary_ledger(committed.prepared_id)


def read_rolling_history_v2_summary_ledger(
    prepared_id: str,
) -> RollingHistoryV2PreparedSummaryLedgerRecord:
    """Read and fully revalidate a PREPARED summary ledger under its owner."""
    if type(prepared_id) is not str or _HASH.fullmatch(prepared_id) is None:
        raise ValueError("Invalid rolling history v2 prepared summary-ledger ID")
    binding = _current_binding()
    scope = binding.scope

    def operation(handle: RollingHistorySqlHandle, identity, operation_namespace):
        raw = _read_setting(
            handle, operation_namespace + "prepared_summary_ledger:" + prepared_id,
        )
        if raw is None:
            return _ConflictResult(
                "Rolling history v2 prepared summary ledger is unavailable",
            )
        return _decode_summary_ledger_record(
            handle, scope, operation_namespace, identity, raw,
            expected_prepared_id=prepared_id,
        )

    return _finish_summary_ledger_operation(
        binding, OperationMode.READ, operation,
    )


def _publication_binding_digest(value: dict) -> str:
    return _sha256(_canonical({
        key: item for key, item in value.items()
        if key != "binding_sha256"
    }))


def _publication_bundle_prepared_id(
    kind: str,
    scope_sha256: str,
    request_id: str,
    idempotency_key_sha256: str,
) -> str:
    if kind not in (_GROUNDED_SUMMARY_KIND, _SUMMARY_LEDGER_KIND):
        raise ValueError("Invalid V2 publication PREPARED record kind")
    return _sha256(_canonical({
        "kind": kind,
        "bundle_kind": _PUBLICATION_BUNDLE_KIND,
        "scope_sha256": scope_sha256,
        "request_id": request_id,
        "idempotency_key_sha256": idempotency_key_sha256,
    }))


def _publication_binding_value(
    *,
    binding_id: str,
    idempotency_key_sha256: str,
    identity,
    scope: ContextEventScope,
    snapshot: RollingHistoryV2PublicationSnapshot,
    expected_head: RollingHistoryV2Head,
    recent_message_start_boundary: int,
    grounded_summary: RollingHistoryV2PreparedRecordBinding,
    summary_ledger: RollingHistoryV2PreparedRecordBinding,
    host_candidate_commitment: RollingHistoryV2HostCandidateCommitment,
) -> dict:
    if (type(host_candidate_commitment.source_crosswalk_sha256) is not str
            or _HASH.fullmatch(host_candidate_commitment.source_crosswalk_sha256) is None):
        raise ValueError("V2 publication requires a complete host source crosswalk digest")
    value = {
        "schema_version": _PUBLICATION_BINDING_SCHEMA_VERSION,
        "kind": _PUBLICATION_BINDING_KIND,
        "state": "PREPARED",
        "binding_id": binding_id,
        "idempotency_key_sha256": idempotency_key_sha256,
        "canonical_namespace_digest": identity.canonical_namespace_digest,
        "scope_sha256": _scope_digest(scope),
        "delete_epoch": identity.delete_epoch,
        "request_id": scope.request_id,
        "active_pointer": asdict(snapshot.active_pointer),
        "expected_head": _head_value(expected_head),
        "source_fence": _rolling_history_v2_migration_live_fence_value(
            snapshot.page_pin.source_fence,
        ),
        "snapshot_sha256": snapshot.snapshot_sha256,
        "recent_message_start_boundary": recent_message_start_boundary,
        "grounded_summary": asdict(grounded_summary),
        "summary_ledger": asdict(summary_ledger),
        "host_coverage_normalizer": host_candidate_commitment.coverage_normalizer,
        "host_prefix_count": host_candidate_commitment.prefix_count,
        "host_prefix_sha256": host_candidate_commitment.prefix_sha256,
        "host_tail_count": host_candidate_commitment.tail_count,
        "host_tail_sha256": host_candidate_commitment.tail_sha256,
        "host_candidate_sha256": host_candidate_commitment.candidate_sha256,
        "host_source_crosswalk_sha256": host_candidate_commitment.source_crosswalk_sha256,
    }
    return {**value, "binding_sha256": _publication_binding_digest(value)}


def _publication_binding_from_value(value: dict) -> RollingHistoryV2SummaryPublicationBinding:
    required = {
        "schema_version", "kind", "state", "binding_id",
        "idempotency_key_sha256", "canonical_namespace_digest", "scope_sha256",
        "delete_epoch", "request_id", "active_pointer", "expected_head",
        "source_fence", "snapshot_sha256", "recent_message_start_boundary",
        "grounded_summary", "summary_ledger", "host_candidate_sha256",
        "host_coverage_normalizer", "host_prefix_count", "host_prefix_sha256",
        "host_tail_count", "host_tail_sha256", "host_source_crosswalk_sha256",
        "binding_sha256",
    }
    legacy = (type(value) is dict
              and type(value.get("schema_version")) is int
              and value["schema_version"] == _PUBLICATION_BINDING_LEGACY_SCHEMA_VERSION)
    legacy_required = required - {"host_source_crosswalk_sha256"}
    if (type(value) is not dict
            or (legacy and set(value) != legacy_required)
            or (not legacy and set(value) != required)
            or type(value["schema_version"]) is not int
            or value["schema_version"] not in (
                _PUBLICATION_BINDING_LEGACY_SCHEMA_VERSION,
                _PUBLICATION_BINDING_SCHEMA_VERSION,
            )
            or value["kind"] != _PUBLICATION_BINDING_KIND
            or value["state"] != "PREPARED"
            or any(type(value[field]) is not str or _HASH.fullmatch(value[field]) is None
                   for field in (
                       "binding_id", "idempotency_key_sha256",
                       "canonical_namespace_digest", "scope_sha256",
                       "snapshot_sha256", "host_candidate_sha256",
                       "binding_sha256",
                   ))
            or type(value["delete_epoch"]) is not str
            or (value["delete_epoch"] != ""
                and re.fullmatch(r"[0-9a-f]{32}", value["delete_epoch"]) is None)
            or type(value["request_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["request_id"]) is None
            or type(value["recent_message_start_boundary"]) is not int
            or value["recent_message_start_boundary"] < 1
            or value["host_coverage_normalizer"] != "plain-text-v1"
            or type(value["host_prefix_count"]) is not int
            or not 1 <= value["host_prefix_count"] <= 64
            or type(value["host_tail_count"]) is not int
            or not 1 <= value["host_tail_count"] <= _PUBLICATION_HOST_MAX_TAIL_MESSAGES
            or any(type(value[field]) is not str or _HASH.fullmatch(value[field]) is None
                   for field in ("host_prefix_sha256", "host_tail_sha256"))
            or (not legacy and (
                type(value["host_source_crosswalk_sha256"]) is not str
                or _HASH.fullmatch(value["host_source_crosswalk_sha256"]) is None
            ))
            or type(value["active_pointer"]) is not dict
            or type(value["expected_head"]) is not dict
            or type(value["source_fence"]) is not dict
            or type(value["grounded_summary"]) is not dict
            or type(value["summary_ledger"]) is not dict
            or value["binding_sha256"] != _publication_binding_digest(value)):
        raise ValueError("Invalid V2 summary publication binding record")
    expected_binding_id = _sha256(_canonical({
        "kind": _PUBLICATION_BINDING_KIND,
        "scope_sha256": value["scope_sha256"],
        "request_id": value["request_id"],
        "idempotency_key_sha256": value["idempotency_key_sha256"],
    }))
    if value["binding_id"] != expected_binding_id:
        raise ValueError("V2 publication binding ID commitment mismatch")
    expected_summary_id = _publication_bundle_prepared_id(
        _GROUNDED_SUMMARY_KIND,
        value["scope_sha256"],
        value["request_id"],
        value["idempotency_key_sha256"],
    )
    expected_ledger_id = _publication_bundle_prepared_id(
        _SUMMARY_LEDGER_KIND,
        value["scope_sha256"],
        value["request_id"],
        value["idempotency_key_sha256"],
    )
    if (value["grounded_summary"].get("prepared_id") != expected_summary_id
            or value["summary_ledger"].get("prepared_id") != expected_ledger_id):
        raise ValueError("V2 publication binding does not reference bundle PREPARED IDs")
    pointer = RollingHistoryV2ActiveBranchPointer(**value["active_pointer"])
    _validate_active_branch_pointer_token(pointer)
    head = _decode_head(_canonical(value["expected_head"]))
    _validate_rolling_history_v2_head_token(head)
    source_fence = RollingHistoryV2MigrationLiveFence(**value["source_fence"])
    if (any(type(item) is not int or item < 0 for item in (
            source_fence.message_source_generation,
            source_fence.message_count,
            source_fence.non_append_generation,
    ))
            or pointer.state != "active"
            or pointer.branch_id != head.branch_id
            or pointer.canonical_namespace_digest
            != value["canonical_namespace_digest"]
            or head.scope_sha256 != value["scope_sha256"]):
        raise ValueError("Invalid V2 publication binding head")
    if (set(value["grounded_summary"]) != {
            "prepared_id", "prepared_record_sha256", "payload_sha256",
        }
            or set(value["summary_ledger"]) != {
                "prepared_id", "prepared_record_sha256", "payload_sha256",
            }):
        raise ValueError("Invalid V2 publication prepared-record binding")
    grounded_summary = RollingHistoryV2PreparedRecordBinding(
        **value["grounded_summary"],
    )
    summary_ledger = RollingHistoryV2PreparedRecordBinding(
        **value["summary_ledger"],
    )
    for reference in (grounded_summary, summary_ledger):
        if any(type(item) is not str or _HASH.fullmatch(item) is None for item in (
                reference.prepared_id,
                reference.prepared_record_sha256,
                reference.payload_sha256,
        )):
            raise ValueError("Invalid V2 publication prepared-record digest")
    return RollingHistoryV2SummaryPublicationBinding(
        schema_version=value["schema_version"],
        binding_id=value["binding_id"],
        binding_sha256=value["binding_sha256"],
        idempotency_key_sha256=value["idempotency_key_sha256"],
        canonical_namespace_digest=value["canonical_namespace_digest"],
        scope_sha256=value["scope_sha256"],
        delete_epoch=value["delete_epoch"],
        request_id=value["request_id"],
        active_pointer=pointer,
        expected_head=head,
        source_fence=source_fence,
        snapshot_sha256=value["snapshot_sha256"],
        recent_message_start_boundary=value["recent_message_start_boundary"],
        grounded_summary=grounded_summary,
        summary_ledger=summary_ledger,
        host_coverage_normalizer=value["host_coverage_normalizer"],
        host_prefix_count=value["host_prefix_count"],
        host_prefix_sha256=value["host_prefix_sha256"],
        host_tail_count=value["host_tail_count"],
        host_tail_sha256=value["host_tail_sha256"],
        host_candidate_sha256=value["host_candidate_sha256"],
        host_source_crosswalk_sha256=(
            None if legacy else value["host_source_crosswalk_sha256"]
        ),
    )


def _publication_value_binding(value: dict) -> RollingHistoryV2PreparedRecordBinding:
    return RollingHistoryV2PreparedRecordBinding(
        prepared_id=value["prepared_id"],
        prepared_record_sha256=value["prepared_record_sha256"],
        payload_sha256=value["payload_sha256"],
    )


def _host_candidate_commitment_from_publication_binding(
    binding: RollingHistoryV2SummaryPublicationBinding,
) -> RollingHistoryV2HostCandidateCommitment:
    if (binding.schema_version != _PUBLICATION_BINDING_SCHEMA_VERSION
            or type(binding.host_source_crosswalk_sha256) is not str
            or _HASH.fullmatch(binding.host_source_crosswalk_sha256) is None):
        raise ValueError("Publication binding has no current source crosswalk commitment")
    return RollingHistoryV2HostCandidateCommitment(
        schema_version=_PUBLICATION_HOST_CANDIDATE_SCHEMA_VERSION,
        coverage_normalizer=binding.host_coverage_normalizer,
        prefix_count=binding.host_prefix_count,
        prefix_sha256=binding.host_prefix_sha256,
        tail_count=binding.host_tail_count,
        tail_sha256=binding.host_tail_sha256,
        candidate_sha256=binding.host_candidate_sha256,
        source_crosswalk_sha256=binding.host_source_crosswalk_sha256,
    )


def _publication_summary_record_from_value(
    value: dict,
    record_json: str,
    branch: RollingHistoryV2Branch,
    revision: RollingHistoryV2Revision,
    expected_head: RollingHistoryV2Head,
) -> RollingHistoryV2PreparedGroundedSummaryRecord:
    summary, _summary_value = _decode_grounded_summary_value(
        value["summary"], allow_v2_v3_locators=True,
    )
    source = value["source_manifest"]
    source_bodies = tuple(
        RollingHistoryV2PreparedSummarySourceBody(
            locator=_grounded_summary_locator_from_value(item["locator"]),
            body=item["body"],
            body_sha256=item["body_sha256"],
        )
        for item in source["sources"]
    )
    claim_sources = tuple(
        RollingHistoryV2PreparedSummaryClaimSource(
            field=item["field"],
            claim_index=item["claim_index"],
            quote=item["quote"],
            locator=_grounded_summary_locator_from_value(item["locator"]),
            body_sha256=item["body_sha256"],
        )
        for item in source["claim_sources"]
    )
    resolution_sources = tuple(
        RollingHistoryV2PreparedSummaryResolutionSource(
            field=item["field"],
            action=item["action"],
            prior_quote=item["prior_source"]["quote"],
            prior_source_role=item["prior_source"]["role"],
            prior_locator=_grounded_summary_locator_from_value(
                item["prior_source"]["locator"],
            ),
            evidence_quote=item["evidence_source"]["quote"],
            evidence_source_role=item["evidence_source"]["role"],
            evidence_locator=_grounded_summary_locator_from_value(
                item["evidence_source"]["locator"],
            ),
        )
        for item in source.get("resolution_sources", [])
    )
    return RollingHistoryV2PreparedGroundedSummaryRecord(
        schema_version=value["schema_version"],
        state=value["state"],
        canonical_namespace_digest=value["canonical_namespace_digest"],
        payload_sha256=value["payload_sha256"],
        prepared_id=value["prepared_id"],
        summary=summary,
        recent_message_start_boundary=value["recent_message_start_boundary"],
        branch=branch,
        revision=revision,
        expected_head=expected_head,
        source_event_locators=tuple(item.locator for item in source_bodies),
        source_bodies=source_bodies,
        claim_sources=claim_sources,
        source_sha256=value["source_sha256"],
        prepared_record_sha256=value["prepared_record_sha256"],
        record_json=record_json,
        resolution_sources=resolution_sources,
    )


def _publication_ledger_record_from_value(
    value: dict,
    record_json: str,
    branch: RollingHistoryV2Branch,
    revision: RollingHistoryV2Revision,
    expected_head: RollingHistoryV2Head,
) -> RollingHistoryV2PreparedSummaryLedgerRecord:
    entries, source_bodies = _summary_ledger_record_entries(
        value["source_manifest"], allow_v2_v3_locators=True,
    )
    return RollingHistoryV2PreparedSummaryLedgerRecord(
        schema_version=value["schema_version"],
        state=value["state"],
        canonical_namespace_digest=value["canonical_namespace_digest"],
        payload_sha256=value["payload_sha256"],
        prepared_id=value["prepared_id"],
        entries=entries,
        branch=branch,
        revision=revision,
        expected_head=expected_head,
        source_bodies=source_bodies,
        source_manifest_json=_canonical(value["source_manifest"]),
        source_sha256=value["source_sha256"],
        prepared_record_sha256=value["prepared_record_sha256"],
        record_json=record_json,
    )


def _validate_publication_prepared_rows(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    current: _RawRollingHistoryV2StoreResult,
    binding: RollingHistoryV2SummaryPublicationBinding,
) -> tuple[dict, str, dict, str]:
    summary_raw = _read_publication_record(
        handle,
        namespace + "prepared_grounded_summary:" + binding.grounded_summary.prepared_id,
    )
    ledger_raw = _read_publication_record(
        handle,
        namespace + "prepared_summary_ledger:" + binding.summary_ledger.prepared_id,
    )
    if summary_raw is None or ledger_raw is None:
        raise ValueError("V2 publication PREPARED record is unavailable or oversized")
    summary_value = _decode_canonical_object(
        summary_raw, "V2 publication grounded summary",
    )
    ledger_value = _decode_canonical_object(
        ledger_raw, "V2 publication summary ledger",
    )
    summary_required = {
        "schema_version", "kind", "state", "scope_sha256",
        "canonical_namespace_digest", "request_id", "prepared_id",
        "idempotency_id", "idempotency_key_sha256", "payload_sha256",
        "branch_id", "revision_id", "revision_sha256", "history_revision",
        "history_head_sha256", "branch", "revision", "expected_head",
        "summary", "summary_sha256", "recent_message_start_boundary",
        "source_manifest", "source_sha256", "prepared_record_sha256",
    }
    ledger_required = {
        "schema_version", "kind", "state", "scope_sha256",
        "canonical_namespace_digest", "request_id", "prepared_id",
        "idempotency_id", "idempotency_key_sha256", "payload_sha256",
        "branch_id", "revision_id", "revision_sha256", "history_revision",
        "history_head_sha256", "branch", "revision", "expected_head",
        "source_manifest", "source_sha256", "prepared_record_sha256",
    }
    head_value = _head_value(binding.expected_head)
    common = (
        "scope_sha256", "canonical_namespace_digest", "request_id",
        "branch_id", "revision_id", "revision_sha256", "history_revision",
        "history_head_sha256", "branch", "revision", "expected_head",
    )
    for record, expected, kind, schema_version, is_summary in (
        (summary_value, binding.grounded_summary, _GROUNDED_SUMMARY_KIND,
         _GROUNDED_SUMMARY_SCHEMA_VERSION, True),
        (ledger_value, binding.summary_ledger, _SUMMARY_LEDGER_KIND,
         _SUMMARY_LEDGER_SCHEMA_VERSION, False),
    ):
        if (set(record) != (summary_required if is_summary else ledger_required)
                or record["state"] != "PREPARED"
                or type(record["schema_version"]) is not int
                or record["schema_version"] != schema_version
                or record["kind"] != kind
                or record["prepared_id"] != expected.prepared_id
                or record["idempotency_id"] != expected.prepared_id
                or record["idempotency_key_sha256"]
                != binding.idempotency_key_sha256
                or record["scope_sha256"] != binding.scope_sha256
                or record["canonical_namespace_digest"] != binding.canonical_namespace_digest
                or record["request_id"] != binding.request_id
                or record["expected_head"] != head_value
                or record["payload_sha256"] != expected.payload_sha256
                or record["prepared_record_sha256"] != expected.prepared_record_sha256
                or record["prepared_record_sha256"] != (
                    _grounded_summary_record_digest(record)
                    if is_summary
                    else _summary_ledger_record_digest(record)
                )
                or any(record[field] != (
                    _scope_digest(scope) if field == "scope_sha256"
                    else identity.canonical_namespace_digest if field == "canonical_namespace_digest"
                    else scope.request_id if field == "request_id"
                    else head_value[field] if field == "expected_head"
                    else binding.expected_head.branch_id if field == "branch_id"
                    else binding.expected_head.revision_id if field == "revision_id"
                    else binding.expected_head.revision_sha256 if field == "revision_sha256"
                    else binding.expected_head.history_revision if field == "history_revision"
                    else binding.expected_head.history_head_sha256 if field == "history_head_sha256"
                    else _branch_value(current.branch) if field == "branch"
                    else _revision_value(current.revision) if field == "revision"
                    else None
                ) for field in common)):
            raise ValueError("V2 publication PREPARED record binding mismatch")
        if (type(record["idempotency_key_sha256"]) is not str
                or _HASH.fullmatch(record["idempotency_key_sha256"]) is None
                or type(record["source_manifest"]) is not dict
                or type(record["source_sha256"]) is not str
                or _HASH.fullmatch(record["source_sha256"]) is None
                or record["source_sha256"] != _sha256(_canonical(record["source_manifest"]))):
            raise ValueError("Invalid V2 publication PREPARED source manifest")

    if (type(summary_value["recent_message_start_boundary"]) is not int
            or summary_value["recent_message_start_boundary"]
            != binding.recent_message_start_boundary):
        raise ValueError("V2 publication summary boundary mismatch")
    summary, summary_data = _decode_grounded_summary_value(
        summary_value["summary"], allow_v2_v3_locators=True,
    )
    if (summary.covered_through + 1 != binding.recent_message_start_boundary
            or summary_value["summary_sha256"] != _sha256(_canonical(summary_data))
            or summary_value["payload_sha256"] != _grounded_summary_payload_sha256(
                binding.expected_head,
                binding.grounded_summary.prepared_id,
                summary_value["idempotency_key_sha256"],
                summary_data,
                binding.recent_message_start_boundary,
                summary_value["source_manifest"],
            )):
        raise ValueError("V2 publication grounded-summary digest mismatch")

    ledger_manifest = ledger_value["source_manifest"]
    if (type(ledger_manifest) is not dict
            or ledger_manifest.get("schema_version") != _SUMMARY_LEDGER_SCHEMA_VERSION
            or ledger_manifest.get("kind") != _SUMMARY_LEDGER_SOURCE_KIND
            or ledger_manifest.get("visible_event_count") != current.locator_index.locator_count
            or ledger_manifest.get("expected_head") != head_value
            or ledger_manifest.get("scope_sha256") != binding.scope_sha256
            or ledger_manifest.get("canonical_namespace_digest")
            != binding.canonical_namespace_digest
            or ledger_manifest.get("request_id") != binding.request_id):
        raise ValueError("V2 publication summary-ledger manifest binding mismatch")
    entries = _summary_ledger_entries_from_manifest_value(
        ledger_manifest.get("entries"), allow_empty=True,
        allow_v2_v3_locators=True,
    )
    if (ledger_value["payload_sha256"] != _summary_ledger_payload_sha256(
                current,
                binding.summary_ledger.prepared_id,
                ledger_value["idempotency_key_sha256"],
                ledger_manifest,
            )):
        raise ValueError("V2 publication summary-ledger digest mismatch")
    return summary_value, summary_raw, ledger_value, ledger_raw


def _publication_binding_from_readback(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    binding_id: str,
    expected_binding_sha256: str,
    *,
    snapshot: RollingHistoryV2PublicationSnapshot | None = None,
) -> RollingHistoryV2SummaryPublicationBinding | _ConflictResult | _MigrationRequiredResult:
    raw = _read_publication_record(
        handle, namespace + "summary_publication_binding:" + binding_id,
    )
    if raw is None:
        return _ConflictResult("V2 publication binding is unavailable")
    value = _decode_canonical_object(raw, "V2 summary publication binding")
    binding = _publication_binding_from_value(value)
    if (binding.schema_version != _PUBLICATION_BINDING_SCHEMA_VERSION
            or type(binding.host_source_crosswalk_sha256) is not str
            or _HASH.fullmatch(binding.host_source_crosswalk_sha256) is None):
        return _ConflictResult(
            "Legacy V2 publication binding has no source crosswalk commitment",
        )
    if (binding.binding_id != binding_id
            or binding.binding_sha256 != expected_binding_sha256
            or binding.scope_sha256 != _scope_digest(scope)
            or binding.request_id != scope.request_id
            or binding.delete_epoch != identity.delete_epoch
            or binding.canonical_namespace_digest != identity.canonical_namespace_digest):
        return _MigrationRequiredResult()
    if snapshot is not None:
        if type(snapshot) is not RollingHistoryV2PublicationSnapshot:
            return _ConflictResult("V2 publication source snapshot is unavailable")
        revalidated = _revalidate_publication_snapshot_in_transaction(
            handle, scope, namespace, identity, snapshot,
            _new_bound_head(binding.expected_head, identity.canonical_namespace_digest),
        )
        if type(revalidated) in (_ConflictResult, _MigrationRequiredResult):
            return revalidated
    active = _read_active_indexed_branch_pointer(
        handle, scope, namespace, identity.canonical_namespace_digest,
    )
    if (active.pointer != binding.active_pointer
            or active.pointer.state != "active"
            or active.head is None
            or active.head.head != binding.expected_head):
        return _ConflictResult("V2 publication active head changed")
    current = _read_indexed_head_for_page(
        handle, scope, namespace, binding.expected_head.branch_id,
    )
    if isinstance(current, _MigrationRequiredResult):
        return current
    if (current.head != binding.expected_head
            or active.branch != current.branch
            or current.locator_index.locator_count > _PUBLICATION_SNAPSHOT_MAX_EVENTS
            or current.locator_index.page_count > _PUBLICATION_SNAPSHOT_MAX_PAGES):
        return _ConflictResult("V2 publication indexed head changed")
    try:
        source_fence = _read_current_migration_live_fence(
            handle, identity.session_id,
        )
    except LegacySnapshotGenerationConflict:
        return _ConflictResult("V2 publication source fence is unavailable")
    if source_fence != binding.source_fence:
        return _ConflictResult("V2 publication source fence changed")
    _validate_publication_prepared_rows(
        handle, scope, namespace, identity, current, binding,
    )
    return binding


def _finish_publication_operation(
    binding: _StoreBinding,
    mode: OperationMode,
    callback,
    *,
    expected_head: _BoundRollingHistoryV2Head | None = None,
):
    namespace = _namespace(binding.scope)
    session_namespace = _v2_session_namespace_prefix(binding.scope)

    def guarded_callback(handle: RollingHistorySqlHandle):
        identity = get_rolling_history_owner_identity()
        if expected_head is not None:
            _validate_bound_head_token(expected_head)
            if expected_head.canonical_namespace_digest != identity.canonical_namespace_digest:
                return _MigrationRequiredResult()
        if (_namespace(binding.scope) != namespace
                or _has_rows_outside_v2_namespace(handle, session_namespace, namespace)):
            return _MigrationRequiredResult()
        result = callback(handle, identity, namespace)
        if type(result) in (
                RollingHistoryV2PublicationBundle,
                RollingHistoryV2SummaryPublicationBinding,
                RollingHistoryV2SummaryPublicationConfirmation,
                RollingHistoryV2GraphApplyAuthorization,
                RollingHistoryV2PublicationNotApplied,
                RollingHistoryV2PublicationRecovery,
                _PublicationRecoverySeed,
                _PublicationRecoveryReadback,
                _PublicationBodyEventMetadata,
                _ConflictResult, _MigrationRequiredResult):
            return result
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")

    result = run_rolling_history_owner_operation(
        binding.user, mode, guarded_callback,
    )
    if isinstance(result, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    if isinstance(result, _ConflictResult):
        raise RollingHistoryV2Conflict(result.message)
    if type(result) not in (
            RollingHistoryV2PublicationBundle,
            RollingHistoryV2SummaryPublicationBinding,
            RollingHistoryV2SummaryPublicationConfirmation,
            RollingHistoryV2GraphApplyAuthorization,
            RollingHistoryV2PublicationNotApplied,
            RollingHistoryV2PublicationRecovery,
            _PublicationRecoverySeed,
            _PublicationRecoveryReadback,
            _PublicationBodyEventMetadata):
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    return result


def _read_publication_record(
    handle: RollingHistorySqlHandle,
    key: str,
) -> str | None:
    rows = _select_rows(
        handle,
        "SELECT substr(value, 1, ?) AS value_prefix, length(value) AS value_length FROM settings WHERE key = ?",
        (_PUBLICATION_BUNDLE_MAX_RECORD_BYTES + 1, key),
        ("value_prefix", "value_length"),
    )
    if len(rows) > 1:
        raise ValueError("Duplicate V2 publication record")
    if not rows:
        return None
    prefix, value_length = rows[0]
    if (type(prefix) is not str or type(value_length) is not int
            or value_length < 0
            or value_length > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES
            or len(prefix.encode("utf-8", errors="strict"))
            > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES):
        raise ValueError("V2 publication record exceeds byte budget")
    return prefix


def _write_publication_immutable(handle: RollingHistorySqlHandle, key: str, encoded: str) -> None:
    if len(encoded.encode("utf-8", errors="strict")) > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES:
        raise ValueError("V2 publication record exceeds byte budget")
    existing = _read_publication_record(handle, key)
    if existing is not None:
        if existing != encoded:
            raise RollingHistoryV2Conflict("V2 publication idempotency conflict")
        return
    rowcount = handle.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, encoded),
    )
    if type(rowcount) is not int or rowcount != 1:
        raise ValueError("V2 publication immutable write is unavailable")
    if _read_publication_record(handle, key) != encoded:
        raise ValueError("V2 publication immutable readback mismatch")


def _publication_lifecycle_key(namespace: str, binding_id: str) -> str:
    return namespace + "summary_publication_lifecycle:" + binding_id


def _publication_attempt_key(namespace: str, binding_id: str) -> str:
    return namespace + "summary_publication_apply_attempt:" + binding_id


def _publication_not_applied_key(namespace: str, binding_id: str) -> str:
    return namespace + "summary_publication_not_applied:" + binding_id


def _publication_abandoned_key(namespace: str, binding_id: str) -> str:
    return namespace + "summary_publication_prepared_abandoned:" + binding_id


def _publication_lifecycle_value(
    binding: RollingHistoryV2SummaryPublicationBinding,
) -> dict:
    if (type(binding.host_source_crosswalk_sha256) is not str
            or _HASH.fullmatch(binding.host_source_crosswalk_sha256) is None):
        raise ValueError("V2 publication lifecycle requires a source crosswalk digest")
    value = {
        "schema_version": _PUBLICATION_LIFECYCLE_SCHEMA_VERSION,
        "kind": _PUBLICATION_LIFECYCLE_KIND,
        "state": "PREPARED",
        "binding_id": binding.binding_id,
        "binding_sha256": binding.binding_sha256,
        "idempotency_key_sha256": binding.idempotency_key_sha256,
        "canonical_namespace_digest": binding.canonical_namespace_digest,
        "scope_sha256": binding.scope_sha256,
        "delete_epoch": binding.delete_epoch,
        "request_id": binding.request_id,
        "expected_head": _head_value(binding.expected_head),
        "source_fence": _rolling_history_v2_migration_live_fence_value(
            binding.source_fence,
        ),
        "snapshot_sha256": binding.snapshot_sha256,
        "grounded_summary": asdict(binding.grounded_summary),
        "summary_ledger": asdict(binding.summary_ledger),
        "host_coverage_normalizer": binding.host_coverage_normalizer,
        "host_prefix_count": binding.host_prefix_count,
        "host_prefix_sha256": binding.host_prefix_sha256,
        "host_tail_count": binding.host_tail_count,
        "host_tail_sha256": binding.host_tail_sha256,
        "host_candidate_sha256": binding.host_candidate_sha256,
        "host_source_crosswalk_sha256": binding.host_source_crosswalk_sha256,
    }
    return {**value, "lifecycle_sha256": _sha256(_canonical(value))}


def _publication_lifecycle_readback(
    handle: RollingHistorySqlHandle,
    namespace: str,
    binding: RollingHistoryV2SummaryPublicationBinding,
) -> tuple[dict, str] | None:
    raw = _read_publication_record(
        handle, _publication_lifecycle_key(namespace, binding.binding_id),
    )
    if raw is None:
        return None
    value = _decode_canonical_object(raw, "V2 publication lifecycle")
    expected = _publication_lifecycle_value(binding)
    if value != expected:
        raise ValueError("V2 publication lifecycle binding mismatch")
    return value, _sha256(raw)


def _graph_checkpoint_identity_value(
    identity: RollingHistoryV2GraphCheckpointIdentity,
    scope: ContextEventScope,
    *,
    require_candidate: bool,
) -> dict:
    if (type(identity) is not RollingHistoryV2GraphCheckpointIdentity
            or type(identity.thread_id) is not str
            or identity.thread_id != "hwrag-request:" + scope.request_id
            or type(identity.checkpoint_ns) is not str
            or len(identity.checkpoint_ns) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in identity.checkpoint_ns)
            or (identity.checkpoint_id is not None and (
                type(identity.checkpoint_id) is not str
                or not 1 <= len(identity.checkpoint_id) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in identity.checkpoint_id)
            ))
            or (identity.parent_checkpoint_id is not None and (
                type(identity.parent_checkpoint_id) is not str
                or not 1 <= len(identity.parent_checkpoint_id) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in identity.parent_checkpoint_id)
            ))
            or (require_candidate and (
                identity.checkpoint_id is None
                or type(identity.candidate_sha256) is not str
                or _HASH.fullmatch(identity.candidate_sha256) is None
            ))
            or (identity.candidate_sha256 is not None and (
                type(identity.candidate_sha256) is not str
                or _HASH.fullmatch(identity.candidate_sha256) is None
            ))):
        raise ValueError("Invalid V2 graph checkpoint identity")
    return asdict(identity)


def _graph_checkpoint_identity_sha256(
    identity: RollingHistoryV2GraphCheckpointIdentity,
    scope: ContextEventScope,
) -> str:
    value = _graph_checkpoint_identity_value(
        identity, scope, require_candidate=True,
    )
    return _sha256(_canonical(value))


def _publication_attempt_value(
    binding: RollingHistoryV2SummaryPublicationBinding,
    identity: RollingHistoryV2GraphCheckpointIdentity,
    scope: ContextEventScope,
    owner_identity,
) -> dict:
    graph_identity_sha256 = _graph_checkpoint_identity_sha256(identity, scope)
    if identity.parent_checkpoint_id is None:
        pre_apply_checkpoint_id = None
    else:
        pre_apply_checkpoint_id = identity.parent_checkpoint_id
    attempt_id = _sha256(_canonical({
        "kind": _PUBLICATION_ATTEMPT_KIND,
        "binding_id": binding.binding_id,
        "binding_sha256": binding.binding_sha256,
        "graph_checkpoint_identity_sha256": graph_identity_sha256,
    }))
    value = {
        "schema_version": 1,
        "kind": _PUBLICATION_ATTEMPT_KIND,
        "state": "APPLYING_UNKNOWN",
        "attempt_id": attempt_id,
        "binding_id": binding.binding_id,
        "binding_sha256": binding.binding_sha256,
        "idempotency_key_sha256": binding.idempotency_key_sha256,
        "canonical_namespace_digest": owner_identity.canonical_namespace_digest,
        "scope_sha256": _scope_digest(scope),
        "delete_epoch": owner_identity.delete_epoch,
        "request_id": scope.request_id,
        "expected_head": _head_value(binding.expected_head),
        "graph_checkpoint_identity": asdict(identity),
        "graph_checkpoint_identity_sha256": graph_identity_sha256,
        "pre_apply_checkpoint_id": pre_apply_checkpoint_id,
    }
    return {**value, "attempt_sha256": _sha256(_canonical(value))}


def _publication_attempt_readback(
    handle: RollingHistorySqlHandle,
    namespace: str,
    binding: RollingHistoryV2SummaryPublicationBinding,
    owner_identity,
    scope: ContextEventScope,
) -> dict | None:
    raw = _read_publication_record(
        handle, _publication_attempt_key(namespace, binding.binding_id),
    )
    if raw is None:
        return None
    value = _decode_canonical_object(raw, "V2 publication apply attempt")
    required = {
        "schema_version", "kind", "state", "attempt_id", "binding_id",
        "binding_sha256", "idempotency_key_sha256", "canonical_namespace_digest",
        "scope_sha256", "delete_epoch", "request_id", "expected_head",
        "graph_checkpoint_identity", "graph_checkpoint_identity_sha256",
        "pre_apply_checkpoint_id", "attempt_sha256",
    }
    if (set(value) != required or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["kind"] != _PUBLICATION_ATTEMPT_KIND
            or value["state"] != "APPLYING_UNKNOWN"
            or any(value[field] != expected for field, expected in (
                ("binding_id", binding.binding_id),
                ("binding_sha256", binding.binding_sha256),
                ("idempotency_key_sha256", binding.idempotency_key_sha256),
                ("canonical_namespace_digest", owner_identity.canonical_namespace_digest),
                ("scope_sha256", _scope_digest(scope)),
                ("delete_epoch", owner_identity.delete_epoch),
                ("request_id", scope.request_id),
                ("expected_head", _head_value(binding.expected_head)),
            ))
            or type(value["attempt_id"]) is not str
            or _HASH.fullmatch(value["attempt_id"]) is None
            or type(value["graph_checkpoint_identity"]) is not dict
            or type(value["graph_checkpoint_identity_sha256"]) is not str
            or _HASH.fullmatch(value["graph_checkpoint_identity_sha256"]) is None
            or type(value["pre_apply_checkpoint_id"]) not in (str, type(None))
            or type(value["attempt_sha256"]) is not str
            or value["attempt_sha256"] != _sha256(_canonical({
                key: item for key, item in value.items() if key != "attempt_sha256"
            }))):
        raise ValueError("Invalid V2 publication apply attempt")
    identity = RollingHistoryV2GraphCheckpointIdentity(
        **value["graph_checkpoint_identity"],
    )
    identity_sha256 = _graph_checkpoint_identity_sha256(identity, scope)
    expected = _publication_attempt_value(binding, identity, scope, owner_identity)
    if (value != expected
            or value["graph_checkpoint_identity_sha256"] != identity_sha256
            or value["pre_apply_checkpoint_id"] != identity.parent_checkpoint_id):
        raise ValueError("V2 publication attempt identity mismatch")
    return value


def _publication_host_lookup_in_owner_transaction(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    binding: RollingHistoryV2SummaryPublicationBinding,
):
    from app.api.compaction_checkpoint import (
        _lookup_current_published_context_binding_receipt_in_transaction,
    )
    return _lookup_current_published_context_binding_receipt_in_transaction(
        scope,
        expected_binding_id=binding.binding_id,
        expected_binding_sha256=binding.binding_sha256,
        expected_candidate_sha256=binding.host_candidate_sha256,
        expected_source_crosswalk_sha256=binding.host_source_crosswalk_sha256,
        skills_mode="off",
        owner_sql_handle=handle,
    )


def _normalized_sqlite_file_path(value: object) -> str:
    if (type(value) is not str or not value or value == ":memory:"
            or value.startswith("file:")):
        raise ValueError("Graph saver requires a file-backed SQLite database")
    return os.path.normcase(os.path.realpath(os.path.abspath(value)))


def _validate_graph_saver_fence_proof(
    proof: object,
    *,
    phase: str,
    binding: RollingHistoryV2SummaryPublicationBinding,
    identity: RollingHistoryV2GraphCheckpointIdentity,
    attempt: dict,
    scope: ContextEventScope,
) -> None:
    from src.agent.compact.checkpoint_publish import _GraphSaverFenceProof
    if type(proof) is not _GraphSaverFenceProof:
        raise ValueError("A live GraphSaver fence proof is required")
    if (type(proof).__module__ != "src.agent.compact.checkpoint_publish"
            or proof.phase != phase
            or proof.binding_id != binding.binding_id
            or proof.binding_sha256 != binding.binding_sha256
            or proof.attempt_id != attempt["attempt_id"]
            or proof.attempt_sha256 != attempt["attempt_sha256"]
            or proof.target_checkpoint_id != identity.checkpoint_id
            or proof.target_parent_checkpoint_id != identity.parent_checkpoint_id
            or proof.target_candidate_sha256 != identity.candidate_sha256
            or proof.target_graph_checkpoint_identity_sha256
            != attempt["graph_checkpoint_identity_sha256"]
            or proof.pre_apply_checkpoint_id != identity.parent_checkpoint_id
            or proof.thread_id != identity.thread_id
            or proof.checkpoint_ns != identity.checkpoint_ns):
        raise ValueError("GraphSaver fence proof does not match the publication attempt")
    saver = proof.saver
    connection = proof.connection
    lock = getattr(saver, "lock", None)
    locked = getattr(lock, "locked", None)
    if (getattr(saver, "conn", None) is not connection
            or not callable(locked) or locked() is not True
            or getattr(connection, "in_transaction", None) is not True
            or proof.transaction_nonce != getattr(saver, "_active_graph_fence_nonce", None)
            or type(proof.transaction_nonce) is not str
            or re.fullmatch(r"[0-9a-f]{32,64}", proof.transaction_nonce) is None):
        raise ValueError("GraphSaver fence is no longer active")
    graph_path = _normalized_sqlite_file_path(proof.graph_db_path)
    if (proof.graph_db_path != graph_path
            or proof.graph_db_path_sha256 != _sha256(graph_path)):
        raise ValueError("GraphSaver database path proof mismatch")
    from app.db.database import DB_PATH
    app_path = _normalized_sqlite_file_path(str(DB_PATH))
    if graph_path == app_path:
        raise ValueError("GraphSaver and owner stores must use separate databases")
    observed = RollingHistoryV2GraphCheckpointIdentity(
        thread_id=proof.thread_id,
        checkpoint_ns=proof.checkpoint_ns,
        checkpoint_id=proof.checkpoint_id,
        parent_checkpoint_id=proof.parent_checkpoint_id,
        candidate_sha256=proof.observed_candidate_sha256,
    )
    observed_value = _graph_checkpoint_identity_value(
        observed, scope, require_candidate=False,
    )
    if proof.observed_graph_checkpoint_identity_sha256 != _sha256(
            _canonical(observed_value)):
        raise ValueError("GraphSaver observed checkpoint proof mismatch")
    if phase in ("APPLY_PARENT_CHECKED", "OLD_HEAD_UNCHANGED"):
        if proof.checkpoint_id != identity.parent_checkpoint_id:
            raise ValueError("GraphSaver pre-apply checkpoint changed")
    elif phase == "CANDIDATE_EXACT_READBACK":
        if (proof.checkpoint_id != identity.checkpoint_id
                or proof.parent_checkpoint_id != identity.parent_checkpoint_id
                or proof.observed_candidate_sha256 != identity.candidate_sha256):
            raise ValueError("GraphSaver candidate readback mismatch")
    else:
        raise ValueError("Unsupported GraphSaver fence phase")


def _publication_lifecycle_is_complete(
    handle: RollingHistorySqlHandle,
    namespace: str,
    binding: RollingHistoryV2SummaryPublicationBinding,
) -> tuple[dict, str]:
    lifecycle = _publication_lifecycle_readback(handle, namespace, binding)
    if lifecycle is None:
        raise ValueError("V2 publication bundle has no durable lifecycle marker")
    return lifecycle


def _publication_bundle_from_readback(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    binding: RollingHistoryV2SummaryPublicationBinding,
    current: _RawRollingHistoryV2StoreResult,
) -> RollingHistoryV2PublicationBundle:
    summary_value, summary_raw, ledger_value, ledger_raw = (
        _validate_publication_prepared_rows(
            handle, scope, namespace, identity, current, binding,
        )
    )
    return RollingHistoryV2PublicationBundle(
        state="PREPARED",
        grounded_summary=_publication_summary_record_from_value(
            summary_value, summary_raw, current.branch, current.revision,
            binding.expected_head,
        ),
        summary_ledger=_publication_ledger_record_from_value(
            ledger_value, ledger_raw, current.branch, current.revision,
            binding.expected_head,
        ),
        binding=binding,
    )


def prepare_rolling_history_v2_publication_bundle(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    snapshot: RollingHistoryV2PublicationSnapshot,
    summary: "RollingSummary",
    ledger_payload: "RollingSummaryLedgerBridgePayload",
    host_candidate_commitment: RollingHistoryV2HostCandidateCommitment,
    recent_message_start_boundary: int,
    idempotency_key: str,
) -> RollingHistoryV2PublicationBundle:
    """Atomically prepare the grounded summary, ledger, and composite binding.

    This entry point uses a bundle-specific idempotency namespace, so a legacy
    independently prepared row is never silently promoted into a publication.
    All three immutable values are re-read and digest-checked before the owner
    WRITE transaction commits.
    """
    _validate_bound_head_token(expected_head)
    if (type(snapshot) is not RollingHistoryV2PublicationSnapshot
            or snapshot.bound_head != expected_head):
        raise ValueError("V2 publication requires the snapshot's store-issued bound head")
    if (type(recent_message_start_boundary) is not int
            or recent_message_start_boundary < 1
            or type(host_candidate_commitment) is not RollingHistoryV2HostCandidateCommitment
            or type(host_candidate_commitment.schema_version) is not int
            or host_candidate_commitment.schema_version
            != _PUBLICATION_HOST_CANDIDATE_SCHEMA_VERSION
            or host_candidate_commitment.coverage_normalizer != "plain-text-v1"
            or type(host_candidate_commitment.prefix_count) is not int
            or not 1 <= host_candidate_commitment.prefix_count <= 64
            or type(host_candidate_commitment.tail_count) is not int
            or not 1 <= host_candidate_commitment.tail_count <= _PUBLICATION_HOST_MAX_TAIL_MESSAGES
            or any(type(value) is not str or _HASH.fullmatch(value) is None
                   for value in (
                       host_candidate_commitment.prefix_sha256,
                       host_candidate_commitment.tail_sha256,
                       host_candidate_commitment.candidate_sha256,
                       host_candidate_commitment.source_crosswalk_sha256,
                   ))
            or type(idempotency_key) is not str
            or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid V2 publication bundle input")
    binding = _current_binding()
    scope = binding.scope
    _require_head_scope(expected_head, scope)
    summary_value = _rolling_summary_to_value(
        summary, recent_message_start_boundary, allow_v2_v3_locators=True,
    )
    if summary_value["covered_through"] + 1 != recent_message_start_boundary:
        raise ValueError("Invalid V2 publication summary boundary")
    ledger_entries = _summary_ledger_entries_from_payload(
        ledger_payload, allow_empty=True, allow_v2_v3_locators=True,
    )
    source_maps = _publication_snapshot_source_maps(
        snapshot, scope, get_rolling_history_owner_identity(),
    )
    body_by_sequence, role_by_sequence, snapshot_locators = source_maps
    identity = get_rolling_history_owner_identity()
    base_key_sha256 = _sha256(idempotency_key)
    summary_key_sha256 = base_key_sha256
    ledger_key_sha256 = base_key_sha256
    summary_prepared_id = _publication_bundle_prepared_id(
        _GROUNDED_SUMMARY_KIND,
        _scope_digest(scope),
        scope.request_id,
        base_key_sha256,
    )
    ledger_prepared_id = _publication_bundle_prepared_id(
        _SUMMARY_LEDGER_KIND,
        _scope_digest(scope),
        scope.request_id,
        base_key_sha256,
    )
    binding_id = _sha256(_canonical({
        "kind": _PUBLICATION_BINDING_KIND,
        "scope_sha256": _scope_digest(scope),
        "request_id": scope.request_id,
        "idempotency_key_sha256": base_key_sha256,
    }))

    def operation(handle: RollingHistorySqlHandle, current_identity, namespace):
        if current_identity != identity:
            return _MigrationRequiredResult()
        current = _revalidate_publication_snapshot_in_transaction(
            handle, scope, namespace, current_identity, snapshot, expected_head,
        )
        if type(current) in (_ConflictResult, _MigrationRequiredResult):
            return current
        if (current.head != expected_head.head
                or len(snapshot_locators) != current.locator_index.locator_count):
            return _ConflictResult("V2 publication expected-head conflict")
        summary_manifest = _grounded_summary_source_manifest(
            scope, current_identity.canonical_namespace_digest, current,
            summary_value, recent_message_start_boundary,
            body_by_sequence, role_by_sequence,
            allow_v2_v3_locators=True,
        )
        ledger_manifest = _summary_ledger_source_manifest(
            scope, current_identity.canonical_namespace_digest, current,
            ledger_entries, body_by_sequence, role_by_sequence,
        )
        summary_record_value = _grounded_summary_record_value(
            scope, current_identity.canonical_namespace_digest, current,
            summary_value, recent_message_start_boundary, summary_manifest,
            summary_prepared_id, summary_key_sha256,
        )
        ledger_record_value = _summary_ledger_record_value(
            scope, current_identity.canonical_namespace_digest, current,
            ledger_manifest, ledger_prepared_id, ledger_key_sha256,
        )
        summary_json = _canonical(summary_record_value)
        ledger_json = _canonical(ledger_record_value)
        if (len(summary_json.encode("utf-8")) > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES
                or len(ledger_json.encode("utf-8")) > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES):
            raise ValueError("V2 publication PREPARED record size exceeded")
        summary_reference = _publication_value_binding(summary_record_value)
        ledger_reference = _publication_value_binding(ledger_record_value)
        binding_value = _publication_binding_value(
            binding_id=binding_id,
            idempotency_key_sha256=base_key_sha256,
            identity=current_identity,
            scope=scope,
            snapshot=snapshot,
            expected_head=current.head,
            recent_message_start_boundary=recent_message_start_boundary,
            grounded_summary=summary_reference,
            summary_ledger=ledger_reference,
            host_candidate_commitment=host_candidate_commitment,
        )
        binding_json = _canonical(binding_value)
        if len(binding_json.encode("utf-8")) > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES:
            raise ValueError("V2 publication binding size exceeded")
        expected_binding = _publication_binding_from_value(binding_value)
        lifecycle_value = _publication_lifecycle_value(expected_binding)
        lifecycle_json = _canonical(lifecycle_value)
        if len(lifecycle_json.encode("utf-8")) > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES:
            raise ValueError("V2 publication lifecycle size exceeded")
        summary_record_key = namespace + "prepared_grounded_summary:" + summary_prepared_id
        ledger_record_key = namespace + "prepared_summary_ledger:" + ledger_prepared_id
        binding_record_key = namespace + "summary_publication_binding:" + binding_id
        lifecycle_record_key = _publication_lifecycle_key(namespace, binding_id)
        planned = (
            (summary_record_key, summary_json),
            (ledger_record_key, ledger_json),
            (binding_record_key, binding_json),
            (lifecycle_record_key, lifecycle_json),
        )
        existing_by_key = {
            key: _read_publication_record(handle, key) for key, _encoded in planned
        }
        any_existing = any(value is not None for value in existing_by_key.values())
        if any_existing and existing_by_key[lifecycle_record_key] is None:
            return _ConflictResult("V2 publication bundle is untracked PREPARED data")
        if (existing_by_key[lifecycle_record_key] is not None
                and any(existing_by_key[key] is None for key in (
                    summary_record_key, ledger_record_key, binding_record_key,
                ))):
            return _ConflictResult("V2 publication lifecycle is incomplete")
        if any(_read_publication_record(handle, key) is not None for key in (
                _publication_attempt_key(namespace, binding_id),
                _publication_not_applied_key(namespace, binding_id),
                _publication_abandoned_key(namespace, binding_id),
                namespace + "summary_publication_confirmation:" + binding_id,
        )):
            return _ConflictResult("V2 publication attempt is already terminal or consumed")
        for key, encoded in planned:
            existing = _read_publication_record(handle, key)
            if existing is not None and existing != encoded:
                return _ConflictResult("V2 publication bundle idempotency conflict")
        for key, encoded in planned[:2]:
            _write_publication_immutable(handle, key, encoded)
        summary_readback = _read_publication_record(handle, summary_record_key)
        ledger_readback = _read_publication_record(handle, ledger_record_key)
        if summary_readback != summary_json or ledger_readback != ledger_json:
            raise ValueError("V2 publication PREPARED readback mismatch")
        summary_value_readback = _decode_canonical_object(
            summary_readback, "V2 publication grounded summary",
        )
        ledger_value_readback = _decode_canonical_object(
            ledger_readback, "V2 publication summary ledger",
        )
        _validate_publication_prepared_rows(
            handle, scope, namespace, current_identity, current,
            expected_binding,
        )
        _write_publication_immutable(handle, binding_record_key, binding_json)
        binding_readback = _read_publication_record(handle, binding_record_key)
        if binding_readback != binding_json:
            raise ValueError("V2 publication binding readback mismatch")
        _write_publication_immutable(handle, lifecycle_record_key, lifecycle_json)
        lifecycle_readback = _read_publication_record(handle, lifecycle_record_key)
        if lifecycle_readback != lifecycle_json:
            raise ValueError("V2 publication lifecycle readback mismatch")
        binding_value_readback = _decode_canonical_object(
            binding_readback, "V2 summary publication binding",
        )
        readback_binding = _publication_binding_from_value(binding_value_readback)
        if (summary_value_readback != summary_record_value
                or ledger_value_readback != ledger_record_value
                or readback_binding != expected_binding
                or expected_binding.binding_sha256 != binding_value["binding_sha256"]
                or _publication_lifecycle_readback(
                    handle, namespace, readback_binding,
                ) != (lifecycle_value, _sha256(lifecycle_json))):
            raise ValueError("V2 publication bundle field readback mismatch")
        _validate_publication_prepared_rows(
            handle, scope, namespace, current_identity, current,
            readback_binding,
        )
        summary_record = _publication_summary_record_from_value(
            summary_value_readback, summary_readback,
            current.branch, current.revision, current.head,
        )
        ledger_record = _publication_ledger_record_from_value(
            ledger_value_readback, ledger_readback,
            current.branch, current.revision, current.head,
        )
        return RollingHistoryV2PublicationBundle(
            state="PREPARED",
            grounded_summary=summary_record,
            summary_ledger=ledger_record,
            binding=expected_binding,
        )

    result = _finish_publication_operation(
        binding,
        OperationMode.WRITE,
        operation,
        expected_head=expected_head,
    )
    if (result.binding.snapshot_sha256 != snapshot.snapshot_sha256
            or result.binding.host_candidate_sha256
            != host_candidate_commitment.candidate_sha256
            or result.binding.host_coverage_normalizer
            != host_candidate_commitment.coverage_normalizer
            or result.binding.host_prefix_count != host_candidate_commitment.prefix_count
            or result.binding.host_prefix_sha256 != host_candidate_commitment.prefix_sha256
            or result.binding.host_tail_count != host_candidate_commitment.tail_count
            or result.binding.host_tail_sha256 != host_candidate_commitment.tail_sha256
            or result.binding.host_source_crosswalk_sha256
            != host_candidate_commitment.source_crosswalk_sha256):
        raise RollingHistoryV2Conflict("V2 publication bundle result mismatch")
    return result


def read_rolling_history_v2_publication_binding(
    binding_id: str,
    *,
    expected_binding_sha256: str,
) -> RollingHistoryV2SummaryPublicationBinding:
    """Read a V2 publication binding and revalidate both PREPARED records."""
    if (type(binding_id) is not str or _HASH.fullmatch(binding_id) is None
            or type(expected_binding_sha256) is not str
            or _HASH.fullmatch(expected_binding_sha256) is None):
        raise ValueError("Invalid V2 publication binding handle")
    store_binding = _current_binding()
    snapshot = read_rolling_history_v2_publication_snapshot()

    def operation(handle: RollingHistorySqlHandle, identity, namespace):
        result = _publication_binding_from_readback(
            handle, store_binding.scope, namespace, identity,
            binding_id, expected_binding_sha256,
            snapshot=snapshot,
        )
        if type(result) in (_ConflictResult, _MigrationRequiredResult):
            return result
        if _publication_lifecycle_readback(handle, namespace, result) is None:
            return _ConflictResult("V2 publication bundle has no lifecycle marker")
        return result

    return _finish_publication_operation(
        store_binding, OperationMode.READ, operation,
    )


def recover_rolling_history_v2_publication_bundle(
    *,
    idempotency_key: str,
) -> RollingHistoryV2PublicationRecovery:
    """Inspect one durable publication without granting another graph write."""
    if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid V2 publication idempotency key")
    store_binding = _current_binding()
    scope = store_binding.scope
    base_key_sha256 = _sha256(idempotency_key)
    binding_id = _sha256(_canonical({
        "kind": _PUBLICATION_BINDING_KIND,
        "scope_sha256": _scope_digest(scope),
        "request_id": scope.request_id,
        "idempotency_key_sha256": base_key_sha256,
    }))
    summary_id = _publication_bundle_prepared_id(
        _GROUNDED_SUMMARY_KIND, _scope_digest(scope), scope.request_id,
        base_key_sha256,
    )
    ledger_id = _publication_bundle_prepared_id(
        _SUMMARY_LEDGER_KIND, _scope_digest(scope), scope.request_id,
        base_key_sha256,
    )

    def read_seed(handle, namespace):
        return _PublicationRecoverySeed(
            binding_raw=_read_publication_record(
                handle, namespace + "summary_publication_binding:" + binding_id,
            ),
            summary_raw=_read_publication_record(
                handle, namespace + "prepared_grounded_summary:" + summary_id,
            ),
            ledger_raw=_read_publication_record(
                handle, namespace + "prepared_summary_ledger:" + ledger_id,
            ),
            lifecycle_raw=_read_publication_record(
                handle, _publication_lifecycle_key(namespace, binding_id),
            ),
            attempt_raw=_read_publication_record(
                handle, _publication_attempt_key(namespace, binding_id),
            ),
            not_applied_raw=_read_publication_record(
                handle, _publication_not_applied_key(namespace, binding_id),
            ),
            abandoned_raw=_read_publication_record(
                handle, _publication_abandoned_key(namespace, binding_id),
            ),
            confirmation_raw=_read_publication_record(
                handle, namespace + "summary_publication_confirmation:" + binding_id,
            ),
        )

    seed = _finish_publication_operation(
        store_binding,
        OperationMode.READ,
        lambda handle, _identity, namespace: read_seed(handle, namespace),
    )
    if not any(asdict(seed).values()):
        return RollingHistoryV2PublicationRecovery(state="NOT_FOUND")
    if (seed.binding_raw is None or seed.summary_raw is None or seed.ledger_raw is None
            or seed.lifecycle_raw is None):
        return RollingHistoryV2PublicationRecovery(
            state="UNTRACKED_PREPARED",
            error="A complete lifecycle-bound PREPARED bundle is unavailable",
        )
    try:
        snapshot = read_rolling_history_v2_publication_snapshot()
    except Exception:
        return RollingHistoryV2PublicationRecovery(
            state="UNKNOWN", error="A fresh bounded V2 source snapshot is unavailable",
        )

    def operation(handle: RollingHistorySqlHandle, identity, namespace):
        current_seed = read_seed(handle, namespace)
        if (current_seed.binding_raw is None or current_seed.summary_raw is None
                or current_seed.ledger_raw is None or current_seed.lifecycle_raw is None):
            return RollingHistoryV2PublicationRecovery(
                state="UNTRACKED_PREPARED",
                error="A complete lifecycle-bound PREPARED bundle is unavailable",
            )
        binding_value = None
        try:
            binding_value = _decode_canonical_object(
                current_seed.binding_raw, "V2 publication binding",
            )
            binding = _publication_binding_from_value(binding_value)
            if binding.binding_id != binding_id:
                return RollingHistoryV2PublicationRecovery(state="CONFLICT")
            if binding.schema_version == _PUBLICATION_BINDING_LEGACY_SCHEMA_VERSION:
                return RollingHistoryV2PublicationRecovery(
                    state="UNKNOWN",
                    binding=binding,
                    error="Legacy publication binding has no source crosswalk commitment",
                )
            checked_binding = _publication_binding_from_readback(
                handle, scope, namespace, identity, binding_id,
                binding.binding_sha256, snapshot=snapshot,
            )
            if type(checked_binding) is _ConflictResult:
                return RollingHistoryV2PublicationRecovery(
                    state="CONFLICT", binding=binding, error=checked_binding.message,
                )
            if type(checked_binding) is _MigrationRequiredResult:
                return RollingHistoryV2PublicationRecovery(
                    state="UNKNOWN", binding=binding,
                )
            lifecycle = _publication_lifecycle_readback(
                handle, namespace, checked_binding,
            )
            if lifecycle is None:
                return RollingHistoryV2PublicationRecovery(
                    state="UNTRACKED_PREPARED", binding=checked_binding,
                )
            lifecycle_value, lifecycle_sha256 = lifecycle
            if (_canonical(lifecycle_value) != current_seed.lifecycle_raw):
                return RollingHistoryV2PublicationRecovery(
                    state="CONFLICT", binding=checked_binding,
                )
            current = _read_indexed_head_for_page(
                handle, scope, namespace, checked_binding.expected_head.branch_id,
            )
            if type(current) is _MigrationRequiredResult:
                return RollingHistoryV2PublicationRecovery(
                    state="UNKNOWN", binding=checked_binding,
                    lifecycle_sha256=lifecycle_sha256,
                )
            bundle = _publication_bundle_from_readback(
                handle, scope, namespace, identity, checked_binding, current,
            )
            host_lookup = _publication_host_lookup_in_owner_transaction(
                handle, scope, checked_binding,
            )
            candidate_commitment = _host_candidate_commitment_from_publication_binding(
                checked_binding,
            )
            attempt = _publication_attempt_readback(
                handle, namespace, checked_binding, identity, scope,
            )
            graph_identity = None
            graph_identity_sha256 = None
            if attempt is not None:
                graph_identity = RollingHistoryV2GraphCheckpointIdentity(
                    **attempt["graph_checkpoint_identity"],
                )
                graph_identity_sha256 = attempt["graph_checkpoint_identity_sha256"]
            shared = {
                "binding": checked_binding,
                "bundle": bundle,
                "expected_head": _new_bound_head(
                    checked_binding.expected_head,
                    identity.canonical_namespace_digest,
                ),
                "host_candidate_commitment": candidate_commitment,
                "host_lookup": host_lookup,
                "lifecycle_sha256": lifecycle_sha256,
                "attempt_id": None if attempt is None else attempt["attempt_id"],
                "graph_checkpoint_identity": graph_identity,
                "graph_checkpoint_identity_sha256": graph_identity_sha256,
            }
            if host_lookup.state == "UNKNOWN":
                return RollingHistoryV2PublicationRecovery(
                    state="UNKNOWN", **shared,
                )
            if host_lookup.state == "CONFLICT":
                return RollingHistoryV2PublicationRecovery(
                    state="CONFLICT", **shared,
                )
            if sum(value is not None for value in (
                    current_seed.not_applied_raw,
                    current_seed.abandoned_raw,
                    current_seed.confirmation_raw,
            )) > 1:
                return RollingHistoryV2PublicationRecovery(
                    state="CONFLICT", **shared,
                )

            if current_seed.not_applied_raw is not None:
                terminal = _decode_canonical_object(
                    current_seed.not_applied_raw,
                    "V2 publication NOT_APPLIED terminal",
                )
                terminal_sha256 = terminal.get("terminal_sha256")
                if (attempt is None or set(terminal) != {
                        "schema_version", "kind", "state", "binding_id",
                        "binding_sha256", "attempt_id", "attempt_sha256",
                        "graph_checkpoint_identity_sha256", "host_scan_sha256",
                        "observed_graph_checkpoint", "terminal_sha256",
                    }
                        or terminal["schema_version"] != 1
                        or terminal["kind"] != _PUBLICATION_NOT_APPLIED_KIND
                        or terminal["state"] != "NOT_APPLIED"
                        or terminal["binding_id"] != checked_binding.binding_id
                        or terminal["binding_sha256"] != checked_binding.binding_sha256
                        or terminal["attempt_id"] != attempt["attempt_id"]
                        or terminal["attempt_sha256"] != attempt["attempt_sha256"]
                        or terminal["graph_checkpoint_identity_sha256"] != graph_identity_sha256
                        or terminal["host_scan_sha256"] != host_lookup.scan_sha256
                        or type(terminal["observed_graph_checkpoint"]) is not dict
                        or set(terminal["observed_graph_checkpoint"]) != {
                            "thread_id", "checkpoint_ns", "checkpoint_id",
                            "parent_checkpoint_id", "candidate_sha256",
                        }
                        or terminal["observed_graph_checkpoint"]["thread_id"]
                        != graph_identity.thread_id
                        or terminal["observed_graph_checkpoint"]["checkpoint_ns"]
                        != graph_identity.checkpoint_ns
                        or terminal["observed_graph_checkpoint"]["checkpoint_id"]
                        != attempt["pre_apply_checkpoint_id"]
                        or terminal["terminal_sha256"] != _sha256(_canonical({
                            key: value for key, value in terminal.items()
                            if key != "terminal_sha256"
                        }))
                        or host_lookup.state != "ABSENT"):
                    return RollingHistoryV2PublicationRecovery(
                        state="CONFLICT", **shared,
                    )
                return RollingHistoryV2PublicationRecovery(
                    state="NOT_APPLIED", terminal_sha256=terminal_sha256,
                    **shared,
                )

            if current_seed.abandoned_raw is not None:
                abandoned = _decode_canonical_object(
                    current_seed.abandoned_raw,
                    "V2 publication PREPARED abandonment",
                )
                abandoned_sha256 = abandoned.get("abandoned_sha256")
                if (attempt is not None or host_lookup.state != "ABSENT"
                        or set(abandoned) != {
                            "schema_version", "kind", "state", "binding_id",
                            "binding_sha256", "lifecycle_sha256", "host_scan_sha256",
                            "abandoned_sha256",
                        }
                        or abandoned["schema_version"] != 1
                        or abandoned["kind"] != _PUBLICATION_ABANDONED_KIND
                        or abandoned["state"] != "PREPARED_ABANDONED"
                        or abandoned["binding_id"] != checked_binding.binding_id
                        or abandoned["binding_sha256"] != checked_binding.binding_sha256
                        or abandoned["lifecycle_sha256"] != lifecycle_sha256
                        or abandoned["host_scan_sha256"] != host_lookup.scan_sha256
                        or abandoned["abandoned_sha256"] != _sha256(_canonical({
                            key: value for key, value in abandoned.items()
                            if key != "abandoned_sha256"
                        }))):
                    return RollingHistoryV2PublicationRecovery(
                        state="CONFLICT", **shared,
                    )
                return RollingHistoryV2PublicationRecovery(
                    state="PREPARED_ABANDONED", terminal_sha256=abandoned_sha256,
                    **shared,
                )

            if current_seed.confirmation_raw is not None:
                confirmation = _decode_canonical_object(
                    current_seed.confirmation_raw,
                    "V2 publication confirmation",
                )
                confirmation_sha256 = confirmation.get("confirmation_sha256")
                if (attempt is None or host_lookup.state != "CONFIRMED"
                        or set(confirmation) != {
                            "schema_version", "kind", "state", "binding_id",
                            "binding_sha256", "expected_head", "graph_checkpoint_identity",
                            "graph_checkpoint_identity_sha256", "host_receipt_id",
                            "host_receipt_sha256", "host_receipt_revision",
                            "host_coverage_normalizer", "host_prefix_count",
                            "host_prefix_sha256", "host_tail_count", "host_tail_sha256",
                            "host_candidate_sha256", "host_source_crosswalk_sha256",
                            "confirmation_sha256",
                        }
                        or confirmation["schema_version"] != _PUBLICATION_BINDING_SCHEMA_VERSION
                        or confirmation["kind"] != _PUBLICATION_CONFIRMATION_KIND
                        or confirmation["state"] != "PUBLISHED"
                        or confirmation["binding_id"] != checked_binding.binding_id
                        or confirmation["binding_sha256"] != checked_binding.binding_sha256
                        or confirmation["expected_head"] != _head_value(checked_binding.expected_head)
                        or confirmation["graph_checkpoint_identity"] != asdict(graph_identity)
                        or confirmation["graph_checkpoint_identity_sha256"] != graph_identity_sha256
                        or confirmation["host_receipt_id"] != host_lookup.receipt.head.receipt_id
                        or confirmation["host_receipt_sha256"] != host_lookup.receipt.head.receipt_sha256
                        or confirmation["host_receipt_revision"] != host_lookup.receipt.head.revision
                        or getattr(
                            host_lookup.receipt, "source_crosswalk_sha256", None,
                        ) != checked_binding.host_source_crosswalk_sha256
                        or confirmation["host_candidate_sha256"] != checked_binding.host_candidate_sha256
                        or confirmation["host_source_crosswalk_sha256"]
                        != checked_binding.host_source_crosswalk_sha256
                        or type(confirmation["host_source_crosswalk_sha256"]) is not str
                        or _HASH.fullmatch(
                            confirmation["host_source_crosswalk_sha256"],
                        ) is None
                        or confirmation["confirmation_sha256"] != _sha256(_canonical({
                            key: value for key, value in confirmation.items()
                            if key != "confirmation_sha256"
                        }))):
                    return RollingHistoryV2PublicationRecovery(
                        state="CONFLICT", **shared,
                    )
                return RollingHistoryV2PublicationRecovery(
                    state="STORE_CONFIRMATION_RECORDED",
                    confirmation_sha256=confirmation_sha256,
                    **shared,
                )

            if attempt is None:
                state = "PREPARED" if host_lookup.state == "ABSENT" else "CONFLICT"
                return RollingHistoryV2PublicationRecovery(state=state, **shared)
            if host_lookup.state == "CONFIRMED":
                receipt = host_lookup.receipt
                if (receipt is None
                        or receipt.graph_thread_id != graph_identity.thread_id
                        or receipt.checkpoint_ns != graph_identity.checkpoint_ns
                        or receipt.checkpoint_id != graph_identity.checkpoint_id
                        or receipt.candidate_sha256 != graph_identity.candidate_sha256
                        or getattr(receipt, "source_crosswalk_sha256", None)
                        != checked_binding.host_source_crosswalk_sha256):
                    return RollingHistoryV2PublicationRecovery(
                        state="CONFLICT", **shared,
                    )
                return RollingHistoryV2PublicationRecovery(
                    state="HOST_RECEIPT_CONFIRMED", **shared,
                )
            return RollingHistoryV2PublicationRecovery(
                state="APPLYING_UNKNOWN", **shared,
            )
        except Exception:
            state = (
                "CONFLICT"
                if (type(binding_value) is dict
                    and binding_value.get("schema_version") in (
                        _PUBLICATION_BINDING_LEGACY_SCHEMA_VERSION,
                        _PUBLICATION_BINDING_SCHEMA_VERSION,
                    ))
                else "UNKNOWN"
            )
            return RollingHistoryV2PublicationRecovery(
                state=state, error="Publication recovery evidence is incomplete",
            )

    return _finish_publication_operation(
        store_binding, OperationMode.WRITE, operation,
    )


def consume_rolling_history_v2_graph_apply_permit(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    publication_binding: RollingHistoryV2SummaryPublicationBinding,
    expected_graph_checkpoint: RollingHistoryV2GraphCheckpointIdentity,
    graph_saver_fence_proof: object,
) -> RollingHistoryV2GraphApplyAuthorization:
    """Atomically consume the one graph-write permit before GraphSaver INSERT."""
    _validate_bound_head_token(expected_head)
    if type(publication_binding) is not RollingHistoryV2SummaryPublicationBinding:
        raise ValueError("A trusted V2 publication binding is required")
    binding_context = _current_binding()
    scope = binding_context.scope
    identity = get_rolling_history_owner_identity()
    if (expected_head.head != publication_binding.expected_head
            or expected_head.canonical_namespace_digest
            != publication_binding.canonical_namespace_digest
            or publication_binding.scope_sha256 != _scope_digest(scope)
            or publication_binding.delete_epoch != identity.delete_epoch
            or publication_binding.request_id != scope.request_id
            or publication_binding.canonical_namespace_digest
            != identity.canonical_namespace_digest
            or type(expected_graph_checkpoint) is not RollingHistoryV2GraphCheckpointIdentity
            or expected_graph_checkpoint.candidate_sha256
            != publication_binding.host_candidate_sha256):
        raise RollingHistoryV2Conflict("V2 publication graph identity or scope mismatch")
    graph_identity_sha256 = _graph_checkpoint_identity_sha256(
        expected_graph_checkpoint, scope,
    )
    attempt_value = _publication_attempt_value(
        publication_binding, expected_graph_checkpoint, scope, identity,
    )
    snapshot = read_rolling_history_v2_publication_snapshot()

    def operation(handle: RollingHistorySqlHandle, current_identity, namespace):
        stored = _publication_binding_from_readback(
            handle, scope, namespace, current_identity,
            publication_binding.binding_id, publication_binding.binding_sha256,
            snapshot=snapshot,
        )
        if type(stored) in (_ConflictResult, _MigrationRequiredResult):
            return stored
        if stored != publication_binding:
            return _ConflictResult("V2 publication binding changed")
        if _publication_lifecycle_readback(handle, namespace, stored) is None:
            return _ConflictResult("V2 publication lifecycle marker is unavailable")
        if _publication_attempt_readback(
                handle, namespace, stored, current_identity, scope) is not None:
            return _ConflictResult("V2 publication apply attempt was already consumed")
        if any(_read_publication_record(handle, key) is not None for key in (
                _publication_not_applied_key(namespace, stored.binding_id),
                _publication_abandoned_key(namespace, stored.binding_id),
                namespace + "summary_publication_confirmation:" + stored.binding_id,
        )):
            return _ConflictResult("V2 publication is already terminal")
        host_lookup = _publication_host_lookup_in_owner_transaction(
            handle, scope, stored,
        )
        candidate = host_lookup.prepared_candidate
        if (host_lookup.state != "ABSENT" or candidate is None
                or candidate.publication_binding_id != stored.binding_id
                or candidate.publication_binding_sha256 != stored.binding_sha256
                or candidate.candidate_sha256 != stored.host_candidate_sha256
                or getattr(candidate, "source_crosswalk_sha256", None)
                != stored.host_source_crosswalk_sha256
                or candidate.graph_thread_id != expected_graph_checkpoint.thread_id
                or candidate.checkpoint_ns != expected_graph_checkpoint.checkpoint_ns
                or candidate.expected_head is None
                and candidate.graph_thread_id != "hwrag-request:" + scope.request_id):
            return _ConflictResult("Prepared host candidate is not provably absent from receipts")
        try:
            _validate_graph_saver_fence_proof(
                graph_saver_fence_proof,
                phase="APPLY_PARENT_CHECKED",
                binding=stored,
                identity=expected_graph_checkpoint,
                attempt=attempt_value,
                scope=scope,
            )
        except Exception:
            return _ConflictResult("GraphSaver apply fence is unavailable")
        attempt_json = _canonical(attempt_value)
        if len(attempt_json.encode("utf-8")) > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES:
            raise ValueError("V2 publication attempt size exceeded")
        _write_publication_immutable(
            handle, _publication_attempt_key(namespace, stored.binding_id),
            attempt_json,
        )
        if _read_publication_record(
                handle, _publication_attempt_key(namespace, stored.binding_id)) != attempt_json:
            raise ValueError("V2 publication attempt readback mismatch")
        return RollingHistoryV2GraphApplyAuthorization(
            attempt_id=attempt_value["attempt_id"],
            attempt_sha256=attempt_value["attempt_sha256"],
            binding_id=stored.binding_id,
            binding_sha256=stored.binding_sha256,
            graph_checkpoint_identity_sha256=graph_identity_sha256,
            candidate_sha256=stored.host_candidate_sha256,
        )

    return _finish_publication_operation(
        binding_context, OperationMode.WRITE, operation,
        expected_head=expected_head,
    )


def finalize_rolling_history_v2_graph_not_applied(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    publication_binding: RollingHistoryV2SummaryPublicationBinding,
    observed_graph_checkpoint: RollingHistoryV2GraphCheckpointIdentity,
    graph_saver_fence_proof: object,
) -> RollingHistoryV2PublicationNotApplied:
    """Close an attempt only while the fenced saver still shows its old head."""
    _validate_bound_head_token(expected_head)
    if type(publication_binding) is not RollingHistoryV2SummaryPublicationBinding:
        raise ValueError("A trusted V2 publication binding is required")
    binding_context = _current_binding()
    scope = binding_context.scope
    identity = get_rolling_history_owner_identity()
    if (expected_head.head != publication_binding.expected_head
            or expected_head.canonical_namespace_digest
            != publication_binding.canonical_namespace_digest
            or publication_binding.scope_sha256 != _scope_digest(scope)
            or publication_binding.delete_epoch != identity.delete_epoch
            or publication_binding.request_id != scope.request_id
            or publication_binding.canonical_namespace_digest
            != identity.canonical_namespace_digest):
        raise RollingHistoryV2Conflict("V2 publication graph scope mismatch")
    snapshot = read_rolling_history_v2_publication_snapshot()

    def operation(handle: RollingHistorySqlHandle, current_identity, namespace):
        stored = _publication_binding_from_readback(
            handle, scope, namespace, current_identity,
            publication_binding.binding_id, publication_binding.binding_sha256,
            snapshot=snapshot,
        )
        if type(stored) in (_ConflictResult, _MigrationRequiredResult):
            return stored
        if stored != publication_binding:
            return _ConflictResult("V2 publication binding changed")
        lifecycle = _publication_lifecycle_readback(handle, namespace, stored)
        if lifecycle is None:
            return _ConflictResult("V2 publication lifecycle marker is unavailable")
        attempt = _publication_attempt_readback(
            handle, namespace, stored, current_identity, scope,
        )
        if attempt is None:
            return _ConflictResult("V2 publication apply attempt is unavailable")
        target = RollingHistoryV2GraphCheckpointIdentity(
            **attempt["graph_checkpoint_identity"],
        )
        try:
            observed_value = _graph_checkpoint_identity_value(
                observed_graph_checkpoint, scope, require_candidate=False,
            )
            _validate_graph_saver_fence_proof(
                graph_saver_fence_proof,
                phase="OLD_HEAD_UNCHANGED",
                binding=stored,
                identity=target,
                attempt=attempt,
                scope=scope,
            )
            proof_observed = {
                "thread_id": graph_saver_fence_proof.thread_id,
                "checkpoint_ns": graph_saver_fence_proof.checkpoint_ns,
                "checkpoint_id": graph_saver_fence_proof.checkpoint_id,
                "parent_checkpoint_id": graph_saver_fence_proof.parent_checkpoint_id,
                "candidate_sha256": graph_saver_fence_proof.observed_candidate_sha256,
            }
            if (observed_value != proof_observed
                    or observed_graph_checkpoint.thread_id != target.thread_id
                    or observed_graph_checkpoint.checkpoint_ns != target.checkpoint_ns
                    or observed_graph_checkpoint.checkpoint_id != attempt["pre_apply_checkpoint_id"]):
                return _ConflictResult("GraphSaver old checkpoint readback changed")
        except Exception:
            return _ConflictResult("GraphSaver old checkpoint fence is unavailable")
        if any(_read_publication_record(handle, key) is not None for key in (
                _publication_not_applied_key(namespace, stored.binding_id),
                _publication_abandoned_key(namespace, stored.binding_id),
                namespace + "summary_publication_confirmation:" + stored.binding_id,
        )):
            return _ConflictResult("V2 publication already has a terminal result")
        host_lookup = _publication_host_lookup_in_owner_transaction(
            handle, scope, stored,
        )
        if host_lookup.state != "ABSENT" or host_lookup.scan_sha256 is None:
            return _ConflictResult("Host receipt absence is not completely proven")
        terminal_value = {
            "schema_version": 1,
            "kind": _PUBLICATION_NOT_APPLIED_KIND,
            "state": "NOT_APPLIED",
            "binding_id": stored.binding_id,
            "binding_sha256": stored.binding_sha256,
            "attempt_id": attempt["attempt_id"],
            "attempt_sha256": attempt["attempt_sha256"],
            "graph_checkpoint_identity_sha256": attempt["graph_checkpoint_identity_sha256"],
            "host_scan_sha256": host_lookup.scan_sha256,
            "observed_graph_checkpoint": observed_value,
        }
        terminal_value["terminal_sha256"] = _sha256(_canonical(terminal_value))
        terminal_json = _canonical(terminal_value)
        _write_publication_immutable(
            handle, _publication_not_applied_key(namespace, stored.binding_id),
            terminal_json,
        )
        if _read_publication_record(
                handle, _publication_not_applied_key(namespace, stored.binding_id)) != terminal_json:
            raise ValueError("V2 NOT_APPLIED terminal readback mismatch")
        return RollingHistoryV2PublicationNotApplied(
            state="NOT_APPLIED",
            binding_id=stored.binding_id,
            binding_sha256=stored.binding_sha256,
            attempt_id=attempt["attempt_id"],
            graph_checkpoint_identity_sha256=attempt["graph_checkpoint_identity_sha256"],
            host_scan_sha256=host_lookup.scan_sha256,
            terminal_sha256=terminal_value["terminal_sha256"],
        )

    return _finish_publication_operation(
        binding_context, OperationMode.WRITE, operation,
        expected_head=expected_head,
    )


def abandon_rolling_history_v2_prepared_publication(
    *,
    idempotency_key: str,
) -> RollingHistoryV2PublicationRecovery:
    """Terminalize an untouched PREPARED bundle before any permit is issued."""
    if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid V2 publication idempotency key")
    store_binding = _current_binding()
    scope = store_binding.scope
    base_key_sha256 = _sha256(idempotency_key)
    binding_id = _sha256(_canonical({
        "kind": _PUBLICATION_BINDING_KIND,
        "scope_sha256": _scope_digest(scope),
        "request_id": scope.request_id,
        "idempotency_key_sha256": base_key_sha256,
    }))
    snapshot = read_rolling_history_v2_publication_snapshot()

    def operation(handle: RollingHistorySqlHandle, current_identity, namespace):
        raw = _read_publication_record(
            handle, namespace + "summary_publication_binding:" + binding_id,
        )
        if raw is None:
            return RollingHistoryV2PublicationRecovery(state="NOT_FOUND")
        binding_value = _decode_canonical_object(raw, "V2 publication binding")
        publication_binding = _publication_binding_from_value(binding_value)
        stored = _publication_binding_from_readback(
            handle, scope, namespace, current_identity,
            binding_id, publication_binding.binding_sha256,
            snapshot=snapshot,
        )
        if type(stored) in (_ConflictResult, _MigrationRequiredResult):
            return stored
        lifecycle = _publication_lifecycle_readback(handle, namespace, stored)
        if lifecycle is None:
            return _ConflictResult("V2 publication lifecycle marker is unavailable")
        if (_publication_attempt_readback(
                handle, namespace, stored, current_identity, scope) is not None
                or _read_publication_record(
                    handle, _publication_attempt_key(namespace, binding_id)) is not None
                or _read_publication_record(
                    handle, _publication_not_applied_key(namespace, binding_id)) is not None
                or _read_publication_record(
                    handle, namespace + "summary_publication_confirmation:" + binding_id) is not None):
            return _ConflictResult("A publication attempt or terminal state already exists")
        host_lookup = _publication_host_lookup_in_owner_transaction(
            handle, scope, stored,
        )
        if host_lookup.state != "ABSENT" or host_lookup.scan_sha256 is None:
            return _ConflictResult("Host receipt absence is not completely proven")
        lifecycle_value, lifecycle_sha256 = lifecycle
        abandoned_value = {
            "schema_version": 1,
            "kind": _PUBLICATION_ABANDONED_KIND,
            "state": "PREPARED_ABANDONED",
            "binding_id": stored.binding_id,
            "binding_sha256": stored.binding_sha256,
            "lifecycle_sha256": lifecycle_sha256,
            "host_scan_sha256": host_lookup.scan_sha256,
        }
        abandoned_value["abandoned_sha256"] = _sha256(_canonical(abandoned_value))
        abandoned_json = _canonical(abandoned_value)
        _write_publication_immutable(
            handle, _publication_abandoned_key(namespace, binding_id),
            abandoned_json,
        )
        if _read_publication_record(
                handle, _publication_abandoned_key(namespace, binding_id)) != abandoned_json:
            raise ValueError("V2 PREPARED abandonment readback mismatch")
        return RollingHistoryV2PublicationRecovery(
            state="PREPARED_ABANDONED",
            binding=stored,
            host_lookup=host_lookup,
            lifecycle_sha256=lifecycle_sha256,
            terminal_sha256=abandoned_value["abandoned_sha256"],
        )

    return _finish_publication_operation(
        store_binding, OperationMode.WRITE, operation,
    )


def confirm_rolling_history_v2_summary_publication(
    *,
    expected_head: _BoundRollingHistoryV2Head,
    publication_binding: RollingHistoryV2SummaryPublicationBinding,
    graph_published_head: "HostPublishedHead",
    observed_graph_checkpoint: RollingHistoryV2GraphCheckpointIdentity,
    graph_saver_fence_proof: object,
) -> RollingHistoryV2SummaryPublicationConfirmation:
    """Record PUBLISHED only after exact graph and selected-host readbacks."""
    _validate_bound_head_token(expected_head)
    if type(publication_binding) is not RollingHistoryV2SummaryPublicationBinding:
        raise ValueError("A trusted V2 publication binding is required")
    binding_context = _current_binding()
    scope = binding_context.scope
    identity = get_rolling_history_owner_identity()
    if (expected_head.head != publication_binding.expected_head
            or expected_head.canonical_namespace_digest
            != publication_binding.canonical_namespace_digest
            or publication_binding.scope_sha256 != _scope_digest(scope)
            or publication_binding.delete_epoch != identity.delete_epoch
            or publication_binding.request_id != scope.request_id
            or publication_binding.canonical_namespace_digest
            != identity.canonical_namespace_digest):
        raise RollingHistoryV2Conflict("V2 publication binding scope or head mismatch")
    snapshot = read_rolling_history_v2_publication_snapshot()

    def operation(handle: RollingHistorySqlHandle, current_identity, operation_namespace):
        stored = _publication_binding_from_readback(
            handle, scope, operation_namespace, current_identity,
            publication_binding.binding_id,
            publication_binding.binding_sha256,
            snapshot=snapshot,
        )
        if type(stored) in (_ConflictResult, _MigrationRequiredResult):
            return stored
        if stored != publication_binding:
            return _ConflictResult("V2 publication binding changed")
        lifecycle = _publication_lifecycle_readback(handle, operation_namespace, stored)
        if lifecycle is None:
            return _ConflictResult("V2 publication lifecycle marker is unavailable")
        attempt = _publication_attempt_readback(
            handle, operation_namespace, stored, current_identity, scope,
        )
        if attempt is None:
            return _ConflictResult("V2 publication apply attempt is unavailable")
        target = RollingHistoryV2GraphCheckpointIdentity(
            **attempt["graph_checkpoint_identity"],
        )
        try:
            observed_value = _graph_checkpoint_identity_value(
                observed_graph_checkpoint, scope, require_candidate=True,
            )
            if (observed_graph_checkpoint != target
                    or observed_graph_checkpoint.candidate_sha256
                    != stored.host_candidate_sha256):
                return _ConflictResult("GraphSaver candidate readback changed")
            _validate_graph_saver_fence_proof(
                graph_saver_fence_proof,
                phase="CANDIDATE_EXACT_READBACK",
                binding=stored,
                identity=target,
                attempt=attempt,
                scope=scope,
            )
        except Exception:
            return _ConflictResult("GraphSaver candidate readback fence is unavailable")
        if any(_read_publication_record(handle, key) is not None for key in (
                _publication_not_applied_key(operation_namespace, stored.binding_id),
                _publication_abandoned_key(operation_namespace, stored.binding_id),
        )):
            return _ConflictResult("V2 publication already has a terminal result")
        host_lookup = _publication_host_lookup_in_owner_transaction(
            handle, scope, stored,
        )
        host_receipt = host_lookup.receipt
        if (host_lookup.state != "CONFIRMED" or host_receipt is None
                or host_receipt.head != graph_published_head
                or host_receipt.binding_id != stored.binding_id
                or host_receipt.binding_sha256 != stored.binding_sha256
                or host_receipt.coverage_normalizer != stored.host_coverage_normalizer
                or host_receipt.prefix_count != stored.host_prefix_count
                or host_receipt.prefix_sha256 != stored.host_prefix_sha256
                or host_receipt.tail_count != stored.host_tail_count
                or host_receipt.tail_sha256 != stored.host_tail_sha256
                or host_receipt.candidate_sha256 != stored.host_candidate_sha256
                or getattr(host_receipt, "source_crosswalk_sha256", None)
                != stored.host_source_crosswalk_sha256
                or host_receipt.graph_thread_id != target.thread_id
                or host_receipt.checkpoint_ns != target.checkpoint_ns
                or host_receipt.checkpoint_id != target.checkpoint_id):
            return _ConflictResult("Exact current host receipt is unavailable")
        from app.api.compaction_checkpoint import host_publication_binding_is_current_in_transaction
        if not host_publication_binding_is_current_in_transaction(
                scope, host_receipt,
                read_setting=lambda key: _read_publication_record(handle, key)):
            return _ConflictResult("Selected host publication receipt changed")
        value = {
            "schema_version": _PUBLICATION_BINDING_SCHEMA_VERSION,
            "kind": _PUBLICATION_CONFIRMATION_KIND,
            "state": "PUBLISHED",
            "binding_id": stored.binding_id,
            "binding_sha256": stored.binding_sha256,
            "expected_head": _head_value(expected_head.head),
            "graph_checkpoint_identity": asdict(target),
            "graph_checkpoint_identity_sha256": attempt["graph_checkpoint_identity_sha256"],
            "host_receipt_id": graph_published_head.receipt_id,
            "host_receipt_sha256": graph_published_head.receipt_sha256,
            "host_receipt_revision": graph_published_head.revision,
            "host_coverage_normalizer": stored.host_coverage_normalizer,
            "host_prefix_count": stored.host_prefix_count,
            "host_prefix_sha256": stored.host_prefix_sha256,
            "host_tail_count": stored.host_tail_count,
            "host_tail_sha256": stored.host_tail_sha256,
            "host_candidate_sha256": stored.host_candidate_sha256,
            "host_source_crosswalk_sha256": stored.host_source_crosswalk_sha256,
        }
        value["confirmation_sha256"] = _sha256(_canonical(value))
        encoded = _canonical(value)
        if len(encoded.encode("utf-8")) > _PUBLICATION_BUNDLE_MAX_RECORD_BYTES:
            raise ValueError("V2 publication confirmation size exceeded")
        key = operation_namespace + "summary_publication_confirmation:" + stored.binding_id
        existing = _read_publication_record(handle, key)
        if existing is not None and existing != encoded:
            return _ConflictResult("V2 publication confirmation receipt conflict")
        _write_publication_immutable(handle, key, encoded)
        readback = _read_publication_record(handle, key)
        if readback != encoded:
            raise ValueError("V2 publication confirmation readback mismatch")
        return RollingHistoryV2SummaryPublicationConfirmation(
            state="PUBLISHED",
            binding_id=stored.binding_id,
            binding_sha256=stored.binding_sha256,
            expected_head_sha256=expected_head.head.head_sha256,
            host_receipt_id=graph_published_head.receipt_id,
            host_receipt_sha256=graph_published_head.receipt_sha256,
            host_coverage_normalizer=stored.host_coverage_normalizer,
            host_prefix_count=stored.host_prefix_count,
            host_prefix_sha256=stored.host_prefix_sha256,
            host_tail_count=stored.host_tail_count,
            host_tail_sha256=stored.host_tail_sha256,
            host_candidate_sha256=stored.host_candidate_sha256,
            host_source_crosswalk_sha256=stored.host_source_crosswalk_sha256,
            graph_checkpoint_identity_sha256=attempt["graph_checkpoint_identity_sha256"],
            graph_checkpoint_id=target.checkpoint_id,
            graph_parent_checkpoint_id=target.parent_checkpoint_id,
            confirmation_sha256=value["confirmation_sha256"],
        )

    return _finish_publication_operation(
        binding_context, OperationMode.WRITE, operation, expected_head=expected_head,
    )


def _current_binding() -> _StoreBinding:
    binding = _STORE_BINDING.get()
    if type(binding) is not _StoreBinding:
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    if _current_scope(binding.origin) is not binding.scope:
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    return binding


def _finish_operation(
    binding: _StoreBinding,
    mode: OperationMode,
    callback,
    *,
    expected_head: _BoundRollingHistoryV2Head | None = None,
):
    namespace = _namespace(binding.scope)
    session_namespace = _v2_session_namespace_prefix(binding.scope)

    def guarded_callback(handle: RollingHistorySqlHandle):
        identity = get_rolling_history_owner_identity()
        if expected_head is not None:
            _validate_bound_head_token(expected_head)
            if expected_head.canonical_namespace_digest != identity.canonical_namespace_digest:
                return _MigrationRequiredResult()
        if _namespace(binding.scope) != namespace:
            return _MigrationRequiredResult()
        if _has_rows_outside_v2_namespace(
            handle, session_namespace, namespace,
        ):
            return _MigrationRequiredResult()
        result = callback(handle)
        if type(result) is _RawRollingHistoryV2StoreResult:
            return RollingHistoryV2StoreResult(
                result.branch,
                result.revision,
                _new_bound_head(result.head, identity.canonical_namespace_digest),
                result.visible_event_locators,
                result.locator_index,
            )
        if type(result) is RollingHistoryV2LocatorPageResult:
            return result
        if type(result) is RollingHistoryEventChunk:
            return result
        if type(result) is RollingHistoryBodyEventChunk:
            return result
        if type(result) is _MissingEventChunkResult:
            return result
        if type(result) is RollingHistoryV2PreparedSummaryRecord:
            return result
        if type(result) is RollingHistoryV2MigrationSourcePage:
            return result
        if type(result) is RollingHistoryV2CompactionSourceBindingResult:
            return result
        if type(result) in (_ConflictResult, _MigrationRequiredResult):
            return result
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")

    result = run_rolling_history_owner_operation(
        binding.user, mode, guarded_callback,
    )
    if isinstance(result, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    if isinstance(result, _ConflictResult):
        raise RollingHistoryV2Conflict(result.message)
    if type(result) not in (
        RollingHistoryV2StoreResult,
        RollingHistoryV2PreparedSummaryRecord,
        RollingHistoryV2MigrationSourcePage,
        RollingHistoryV2LocatorPageResult,
        RollingHistoryEventChunk,
        RollingHistoryBodyEventChunk,
        _MissingEventChunkResult,
        RollingHistoryV2CompactionSourceBindingResult,
    ):
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    return result


def _finish_grounded_summary_operation(
    binding: _StoreBinding,
    mode: OperationMode,
    callback,
    *,
    expected_head: _BoundRollingHistoryV2Head | None = None,
):
    """Run a grounded-summary callback inside the existing owner transaction."""
    namespace = _namespace(binding.scope)
    session_namespace = _v2_session_namespace_prefix(binding.scope)

    def guarded_callback(handle: RollingHistorySqlHandle):
        identity = get_rolling_history_owner_identity()
        if expected_head is not None:
            _validate_bound_head_token(expected_head)
            if expected_head.canonical_namespace_digest != identity.canonical_namespace_digest:
                return _MigrationRequiredResult()
        if _namespace(binding.scope) != namespace:
            return _MigrationRequiredResult()
        if _has_rows_outside_v2_namespace(
            handle, session_namespace, namespace,
        ):
            return _MigrationRequiredResult()
        result = callback(handle, identity, namespace)
        if type(result) is RollingHistoryV2PreparedGroundedSummaryRecord:
            return result
        if type(result) in (_ConflictResult, _MigrationRequiredResult):
            return result
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")

    result = run_rolling_history_owner_operation(
        binding.user, mode, guarded_callback,
    )
    if isinstance(result, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    if isinstance(result, _ConflictResult):
        raise RollingHistoryV2Conflict(result.message)
    if type(result) is not RollingHistoryV2PreparedGroundedSummaryRecord:
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    return result


def _finish_summary_ledger_operation(
    binding: _StoreBinding,
    mode: OperationMode,
    callback,
    *,
    expected_head: _BoundRollingHistoryV2Head | None = None,
):
    """Run a summary-ledger callback inside the existing owner transaction."""
    namespace = _namespace(binding.scope)
    session_namespace = _v2_session_namespace_prefix(binding.scope)

    def guarded_callback(handle: RollingHistorySqlHandle):
        identity = get_rolling_history_owner_identity()
        if expected_head is not None:
            _validate_bound_head_token(expected_head)
            if expected_head.canonical_namespace_digest != identity.canonical_namespace_digest:
                return _MigrationRequiredResult()
        if _namespace(binding.scope) != namespace:
            return _MigrationRequiredResult()
        if _has_rows_outside_v2_namespace(
            handle, session_namespace, namespace,
        ):
            return _MigrationRequiredResult()
        result = callback(handle, identity, namespace)
        if type(result) is RollingHistoryV2PreparedSummaryLedgerRecord:
            return result
        if type(result) in (_ConflictResult, _MigrationRequiredResult):
            return result
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")

    result = run_rolling_history_owner_operation(
        binding.user, mode, guarded_callback,
    )
    if isinstance(result, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    if isinstance(result, _ConflictResult):
        raise RollingHistoryV2Conflict(result.message)
    if type(result) is not RollingHistoryV2PreparedSummaryLedgerRecord:
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    return result


def _select_rolling_history_v2_active_branch_pointer(
    *,
    expected_pointer: RollingHistoryV2ActiveBranchPointer,
    target_expected_head: _BoundRollingHistoryV2Head,
    expected_state: str,
) -> RollingHistoryV2ActiveBranchPointerResult:
    _validate_active_branch_pointer_token(expected_pointer)
    _validate_bound_head_token(target_expected_head)
    if expected_pointer.state != expected_state:
        raise RollingHistoryV2Conflict(
            "Rolling history v2 active-branch pointer state conflict",
        )
    if expected_pointer.pointer_revision >= _MAX_ACTIVE_BRANCH_POINTER_REVISION:
        raise RollingHistoryV2Conflict(
            "Rolling history v2 active-branch pointer revision exhausted",
        )

    binding = _current_binding()
    scope = binding.scope
    operation_id = uuid4().hex
    candidate_pointer = _new_active_branch_pointer(
        state="active",
        canonical_namespace_digest=expected_pointer.canonical_namespace_digest,
        pointer_revision=expected_pointer.pointer_revision + 1,
        branch_id=target_expected_head.head.branch_id,
        operation_id=operation_id,
    )
    candidate_json = _canonical(_active_branch_pointer_record(candidate_pointer))

    def write_operation(handle, identity, namespace):
        current = _read_active_branch_pointer(
            handle, scope, namespace, identity.canonical_namespace_digest,
        )
        if current.pointer != expected_pointer:
            return _ConflictResult(
                "Rolling history v2 active-branch pointer CAS conflict",
            )

        try:
            target = _read_current_head(
                handle, scope, namespace, target_expected_head.head.branch_id,
            )
        except ValueError:
            return _ConflictResult(
                "Rolling history v2 target branch or head is unavailable",
            )
        if target.head != target_expected_head.head:
            return _ConflictResult(
                "Rolling history v2 target expected-head conflict",
            )

        key = namespace + "active_branch_pointer"
        if current.record_json is None:
            rowcount = handle.execute(
                "INSERT INTO settings (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, candidate_json),
            )
        else:
            rowcount = handle.execute(
                "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE key = ? AND value = ?",
                (candidate_json, key, current.record_json),
            )
        if type(rowcount) is not int or rowcount != 1:
            raise ValueError("Rolling history v2 active-branch pointer is unavailable")
        if expected_state == "unset":
            _persist_immutable(
                handle,
                namespace + "active_branch_pointer_marker",
                _canonical(_active_branch_pointer_marker(
                    identity.canonical_namespace_digest,
                )),
            )

        persisted = _read_setting(handle, key)
        if persisted != candidate_json:
            raise ValueError("Rolling history v2 active-branch pointer is unavailable")
        verified = _read_active_branch_pointer(
            handle, scope, namespace, identity.canonical_namespace_digest,
        )
        if verified.pointer != candidate_pointer:
            raise ValueError("Rolling history v2 active-branch pointer is unavailable")
        return _ActiveBranchPointerWrite(candidate_pointer, candidate_json)

    def read_back() -> _ActiveBranchPointerRead:
        return _finish_active_branch_pointer_operation(
            binding,
            OperationMode.READ,
            lambda handle, identity, namespace: _read_active_branch_pointer(
                handle, scope, namespace, identity.canonical_namespace_digest,
            ),
        )

    try:
        written = _finish_active_branch_pointer_operation(
            binding,
            OperationMode.WRITE,
            write_operation,
            expected_pointer=expected_pointer,
            target_expected_head=target_expected_head,
        )
    except RollingHistoryOwnerScopeError:
        try:
            readback = read_back()
        except Exception:
            return _unknown_active_branch_pointer_result()
        if readback.pointer == candidate_pointer:
            return _active_branch_pointer_result(readback, "readback_confirmed")
        return _unknown_active_branch_pointer_result()
    if (
        written.pointer != candidate_pointer
        or written.record_json != candidate_json
    ):
        return _unknown_active_branch_pointer_result()

    try:
        readback = read_back()
    except Exception:
        return _unknown_active_branch_pointer_result()
    if readback.pointer != candidate_pointer:
        return _unknown_active_branch_pointer_result()
    return _active_branch_pointer_result(readback, "readback_confirmed")


def _finish_active_branch_pointer_operation(
    binding: _StoreBinding,
    mode: OperationMode,
    callback,
    *,
    expected_pointer: RollingHistoryV2ActiveBranchPointer | None = None,
    target_expected_head: _BoundRollingHistoryV2Head | None = None,
):
    namespace = _namespace(binding.scope)
    session_namespace = _v2_session_namespace_prefix(binding.scope)

    def guarded_callback(handle: RollingHistorySqlHandle):
        identity = get_rolling_history_owner_identity()
        if (
            expected_pointer is not None
            and expected_pointer.canonical_namespace_digest
            != identity.canonical_namespace_digest
        ):
            return _MigrationRequiredResult()
        if (
            target_expected_head is not None
            and target_expected_head.canonical_namespace_digest
            != identity.canonical_namespace_digest
        ):
            return _MigrationRequiredResult()
        if _namespace(binding.scope) != namespace:
            return _MigrationRequiredResult()
        if _has_rows_outside_v2_namespace(
            handle, session_namespace, namespace,
        ):
            return _MigrationRequiredResult()
        result = callback(handle, identity, namespace)
        if type(result) in (_ActiveBranchPointerRead, _ActiveBranchPointerWrite):
            return result
        if type(result) in (_ConflictResult, _MigrationRequiredResult):
            return result
        raise RollingHistoryOwnerScopeError(
            "Rolling history owner scope is unavailable",
        )

    result = run_rolling_history_owner_operation(
        binding.user, mode, guarded_callback,
    )
    if isinstance(result, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    if isinstance(result, _ConflictResult):
        raise RollingHistoryV2Conflict(result.message)
    if type(result) not in (_ActiveBranchPointerRead, _ActiveBranchPointerWrite):
        raise RollingHistoryOwnerScopeError(
            "Rolling history owner scope is unavailable",
        )
    return result


def _idempotency_context(
    scope: ContextEventScope,
    idempotency_key: str,
) -> tuple[str, str]:
    if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid rolling history v2 idempotency key")
    key_sha256 = _sha256(idempotency_key)
    idempotency_id = _rolling_history_v2_idempotency_id(
        _scope_digest(scope), scope.request_id, key_sha256,
    )
    return idempotency_id, key_sha256


def _require_head_scope(
    head: _BoundRollingHistoryV2Head,
    scope: ContextEventScope,
) -> None:
    if head.head.scope_sha256 != _scope_digest(scope):
        raise ValueError("Rolling history v2 head scope mismatch")


def _namespace(scope: ContextEventScope) -> str:
    """Return the v2 child bound to the current host owner incarnation."""
    identity = get_rolling_history_owner_identity()
    if (
        type(scope) is not ContextEventScope
        or identity.session_id != scope.session_id
        or identity.principal_hash != scope.principal_hash
        or identity.delete_epoch != scope.session_epoch
        or identity.request_id != scope.request_id
    ):
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    return (
        _v2_session_namespace_prefix(scope)
        + identity.canonical_namespace_digest
        + ":"
    )


def _active_branch_pointer_digest(
    state: str,
    canonical_namespace_digest: str,
    pointer_revision: int,
    branch_id: str | None,
    operation_id: str | None,
) -> str:
    return _sha256(_canonical({
        "branch_id": branch_id,
        "canonical_namespace_digest": canonical_namespace_digest,
        "kind": "rolling_history_v2_active_branch_pointer",
        "operation_id": operation_id,
        "pointer_revision": pointer_revision,
        "schema_version": _ACTIVE_BRANCH_POINTER_SCHEMA_VERSION,
        "state": state,
    }))


def _new_active_branch_pointer(
    *,
    state: str,
    canonical_namespace_digest: str,
    pointer_revision: int,
    branch_id: str | None,
    operation_id: str | None,
) -> RollingHistoryV2ActiveBranchPointer:
    pointer = RollingHistoryV2ActiveBranchPointer(
        state=state,
        canonical_namespace_digest=canonical_namespace_digest,
        pointer_revision=pointer_revision,
        branch_id=branch_id,
        operation_id=operation_id,
        pointer_sha256=_active_branch_pointer_digest(
            state,
            canonical_namespace_digest,
            pointer_revision,
            branch_id,
            operation_id,
        ),
    )
    _validate_active_branch_pointer_token(pointer)
    return pointer


def _validate_active_branch_pointer_token(token: object) -> None:
    if type(token) is not RollingHistoryV2ActiveBranchPointer:
        raise ValueError("Invalid rolling history v2 active-branch pointer token")
    if (
        type(token.state) is not str
        or type(token.canonical_namespace_digest) is not str
        or _HASH.fullmatch(token.canonical_namespace_digest) is None
        or type(token.pointer_revision) is not int
        or token.pointer_revision < 0
        or token.pointer_revision > _MAX_ACTIVE_BRANCH_POINTER_REVISION
        or type(token.pointer_sha256) is not str
        or _HASH.fullmatch(token.pointer_sha256) is None
    ):
        raise ValueError("Invalid rolling history v2 active-branch pointer token")
    if token.state == "unset":
        if (
            token.pointer_revision != 0
            or token.branch_id is not None
            or token.operation_id is not None
        ):
            raise ValueError("Invalid rolling history v2 active-branch pointer token")
    elif token.state == "active":
        if (
            token.pointer_revision < 1
            or type(token.branch_id) is not str
            or _HASH.fullmatch(token.branch_id) is None
            or type(token.operation_id) is not str
            or _POINTER_OPERATION_ID.fullmatch(token.operation_id) is None
        ):
            raise ValueError("Invalid rolling history v2 active-branch pointer token")
    else:
        raise ValueError("Invalid rolling history v2 active-branch pointer token")
    if token.pointer_sha256 != _active_branch_pointer_digest(
        token.state,
        token.canonical_namespace_digest,
        token.pointer_revision,
        token.branch_id,
        token.operation_id,
    ):
        raise ValueError("Invalid rolling history v2 active-branch pointer digest")


def _active_branch_pointer_record(
    pointer: RollingHistoryV2ActiveBranchPointer,
) -> dict:
    _validate_active_branch_pointer_token(pointer)
    if pointer.state != "active":
        raise ValueError("Invalid rolling history v2 active-branch pointer record")
    return {
        "branch_id": pointer.branch_id,
        "canonical_namespace_digest": pointer.canonical_namespace_digest,
        "kind": "rolling_history_v2_active_branch_pointer",
        "operation_id": pointer.operation_id,
        "pointer_revision": pointer.pointer_revision,
        "pointer_sha256": pointer.pointer_sha256,
        "schema_version": _ACTIVE_BRANCH_POINTER_SCHEMA_VERSION,
        "state": pointer.state,
    }


def _active_branch_pointer_marker(
    canonical_namespace_digest: str,
) -> dict:
    value = {
        "canonical_namespace_digest": canonical_namespace_digest,
        "kind": "rolling_history_v2_active_branch_pointer_marker",
        "schema_version": _ACTIVE_BRANCH_POINTER_SCHEMA_VERSION,
    }
    value["marker_sha256"] = _sha256(_canonical(value))
    return value


def _validate_active_branch_pointer_marker(
    raw: str,
    canonical_namespace_digest: str,
) -> None:
    value = _decode_canonical_object(
        raw, "rolling history v2 active-branch pointer marker",
    )
    required = {
        "canonical_namespace_digest", "kind", "marker_sha256", "schema_version",
    }
    if (
        set(value) != required
        or value["canonical_namespace_digest"] != canonical_namespace_digest
        or value["kind"] != "rolling_history_v2_active_branch_pointer_marker"
        or type(value["schema_version"]) is not int
        or value["schema_version"] != _ACTIVE_BRANCH_POINTER_SCHEMA_VERSION
        or type(value["marker_sha256"]) is not str
        or _HASH.fullmatch(value["marker_sha256"]) is None
    ):
        raise ValueError("Invalid rolling history v2 active-branch pointer marker")
    expected = _active_branch_pointer_marker(canonical_namespace_digest)
    if value != expected:
        raise ValueError("Invalid rolling history v2 active-branch pointer marker")


def _decode_active_branch_pointer(
    raw: str,
) -> RollingHistoryV2ActiveBranchPointer:
    value = _decode_canonical_object(raw, "rolling history v2 active-branch pointer")
    required = {
        "branch_id", "canonical_namespace_digest", "kind", "operation_id",
        "pointer_revision", "pointer_sha256", "schema_version", "state",
    }
    if (
        set(value) != required
        or type(value["schema_version"]) is not int
        or value["schema_version"] != _ACTIVE_BRANCH_POINTER_SCHEMA_VERSION
        or value["kind"] != "rolling_history_v2_active_branch_pointer"
        or value["state"] != "active"
    ):
        raise ValueError("Invalid rolling history v2 active-branch pointer")
    pointer = RollingHistoryV2ActiveBranchPointer(
        state=value["state"],
        canonical_namespace_digest=value["canonical_namespace_digest"],
        pointer_revision=value["pointer_revision"],
        branch_id=value["branch_id"],
        operation_id=value["operation_id"],
        pointer_sha256=value["pointer_sha256"],
    )
    _validate_active_branch_pointer_token(pointer)
    return pointer


def _read_active_branch_pointer(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    canonical_namespace_digest: str,
) -> _ActiveBranchPointerRead:
    key = namespace + "active_branch_pointer"
    marker = _read_setting(
        handle, namespace + "active_branch_pointer_marker",
    )
    if marker is not None:
        _validate_active_branch_pointer_marker(
            marker, canonical_namespace_digest,
        )
    raw = _read_setting(handle, key)
    if raw is None:
        if marker is not None:
            raise ValueError("Rolling history v2 active-branch pointer is missing")
        pointer = _new_active_branch_pointer(
            state="unset",
            canonical_namespace_digest=canonical_namespace_digest,
            pointer_revision=0,
            branch_id=None,
            operation_id=None,
        )
        return _ActiveBranchPointerRead(pointer, None, None, None)

    if marker is None:
        raise ValueError("Rolling history v2 active-branch pointer marker is unavailable")
    pointer = _decode_active_branch_pointer(raw)
    if pointer.canonical_namespace_digest != canonical_namespace_digest:
        raise ValueError("Rolling history v2 active-branch pointer owner mismatch")
    current = _read_current_head(
        handle, scope, namespace, pointer.branch_id,
    )
    return _ActiveBranchPointerRead(
        pointer,
        raw,
        current.branch,
        _new_bound_head(current.head, canonical_namespace_digest),
    )


def _read_active_indexed_branch_pointer(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    canonical_namespace_digest: str,
) -> _ActiveBranchPointerRead:
    """Read the active pointer through the bounded v3 index path only."""
    key = namespace + "active_branch_pointer"
    marker = _read_bounded_setting(
        handle, namespace + "active_branch_pointer_marker", 4096,
    )
    if marker is not None:
        _validate_active_branch_pointer_marker(marker, canonical_namespace_digest)
    raw = _read_bounded_setting(handle, key, 4096)
    if raw is None:
        if marker is not None:
            raise ValueError("Rolling history v2 active-branch pointer is missing")
        pointer = _new_active_branch_pointer(
            state="unset",
            canonical_namespace_digest=canonical_namespace_digest,
            pointer_revision=0,
            branch_id=None,
            operation_id=None,
        )
        return _ActiveBranchPointerRead(pointer, None, None, None)
    if marker is None:
        raise ValueError("Rolling history v2 active-branch pointer marker is unavailable")
    pointer = _decode_active_branch_pointer(raw)
    if pointer.canonical_namespace_digest != canonical_namespace_digest:
        raise ValueError("Rolling history v2 active-branch pointer owner mismatch")
    current = _read_indexed_head_for_page(
        handle, scope, namespace, pointer.branch_id,
    )
    if isinstance(current, _MigrationRequiredResult):
        raise RollingHistoryV2MigrationRequired()
    return _ActiveBranchPointerRead(
        pointer,
        raw,
        current.branch,
        _new_bound_head(current.head, canonical_namespace_digest),
    )


def _active_branch_pointer_result(
    result: _ActiveBranchPointerRead,
    outcome: str,
) -> RollingHistoryV2ActiveBranchPointerResult:
    return RollingHistoryV2ActiveBranchPointerResult(
        outcome=outcome,
        pointer=result.pointer,
        branch=result.branch,
        head=result.head,
    )


def _unknown_active_branch_pointer_result() -> RollingHistoryV2ActiveBranchPointerResult:
    return RollingHistoryV2ActiveBranchPointerResult(
        outcome="outcome_unknown",
        pointer=None,
        branch=None,
        head=None,
    )


def _v2_session_namespace_prefix(scope: ContextEventScope) -> str:
    return (
        rolling_history_session_prefix(scope.session_id)
        + "revision-ledger:v2:"
    )


def _has_rows_outside_v2_namespace(
    handle: RollingHistorySqlHandle,
    session_namespace: str,
    current_namespace: str,
) -> bool:
    """Find any same-session v2 rows that belong to a different namespace."""
    rows = _select_rows(
        handle,
        "SELECT key FROM settings "
        "WHERE substr(key, 1, ?) = ? "
        "AND substr(key, 1, ?) <> ? LIMIT 1",
        (
            len(session_namespace), session_namespace,
            len(current_namespace), current_namespace,
        ),
        ("key",),
    )
    return bool(rows)


def _select_rows(
    handle: RollingHistorySqlHandle,
    statement: str,
    parameters: object,
    expected_columns: tuple[str, ...],
) -> tuple[tuple[object, ...], ...]:
    result = handle.execute(statement, parameters)
    if type(result) is not MaterializedRows or result.columns != expected_columns:
        raise ValueError("Rolling history SQL result is unavailable")
    return result.rows


def _read_setting(handle: RollingHistorySqlHandle, key: str) -> str | None:
    rows = _select_rows(
        handle,
        "SELECT value FROM settings WHERE key = ?",
        (key,),
        ("value",),
    )
    if len(rows) > 1:
        raise ValueError("Duplicate rolling history setting")
    if not rows:
        return None
    value = rows[0][0]
    if type(value) is not str:
        raise ValueError("Invalid rolling history setting")
    return value


def _read_bounded_setting(
    handle: RollingHistorySqlHandle,
    key: str,
    max_bytes: int,
) -> str | None:
    rows = _select_rows(
        handle,
        "SELECT substr(value, 1, ?) AS value_prefix, length(value) AS value_bytes FROM settings WHERE key = ?",
        (max_bytes + 1, key),
        ("value_prefix", "value_bytes"),
    )
    if len(rows) > 1:
        raise ValueError("Duplicate rolling history setting")
    if not rows:
        return None
    prefix, value_bytes = rows[0]
    if (type(prefix) is not str or type(value_bytes) is not int
            or value_bytes < 0):
        raise ValueError("Invalid rolling history setting")
    if value_bytes > max_bytes:
        return None
    return prefix


def _empty_locator_index() -> RollingHistoryV2LocatorIndex:
    return RollingHistoryV2LocatorIndex(
        schema_version=_LOCATOR_INDEX_SCHEMA_VERSION,
        root_sha256=_rolling_history_v2_empty_locator_index_root(),
        locator_count=0,
        page_count=0,
        tree_height=0,
    )


def _locator_node_body(level: int, children: tuple[str | None, ...]) -> dict:
    if (type(level) is not int or level < 0
            or type(children) is not tuple
            or len(children) != _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
            or any(child is not None and (
                type(child) is not str or _HASH.fullmatch(child) is None
            ) for child in children)):
        raise ValueError("Invalid rolling history v2 locator index node")
    return {
        "schema_version": _LOCATOR_INDEX_SCHEMA_VERSION,
        "kind": _LOCATOR_INDEX_KIND,
        "level": level,
        "children": list(children),
    }


def _persist_locator_node(
    handle: RollingHistorySqlHandle,
    namespace: str,
    level: int,
    children: tuple[str | None, ...],
) -> str:
    body = _locator_node_body(level, children)
    raw = _canonical(body)
    digest = _sha256(raw)
    _persist_immutable(handle, namespace + "locator-index:node:" + digest, raw)
    return digest


def _read_locator_node(
    handle: RollingHistorySqlHandle,
    namespace: str,
    digest: str,
    expected_level: int,
) -> tuple[str | None, ...]:
    raw = _read_bounded_setting(
        handle,
        namespace + "locator-index:node:" + digest,
        _PUBLICATION_LOCATOR_NODE_MAX_BYTES,
    )
    if (raw is None
            or len(raw.encode("utf-8", errors="strict"))
            > _PUBLICATION_LOCATOR_NODE_MAX_BYTES):
        raise ValueError("Rolling history v2 locator index node is unavailable")
    value = _decode_canonical_object(raw, "rolling history v2 locator index node")
    required = {"schema_version", "kind", "level", "children"}
    if (set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _LOCATOR_INDEX_SCHEMA_VERSION
            or value["kind"] != _LOCATOR_INDEX_KIND
            or type(value["level"]) is not int or value["level"] != expected_level
            or type(value["children"]) is not list
            or len(value["children"]) != _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
            or any(child is not None and (
                type(child) is not str or _HASH.fullmatch(child) is None
            ) for child in value["children"])
            or _sha256(raw) != digest):
        raise ValueError("Rolling history v2 locator index node commitment mismatch")
    return tuple(value["children"])


def _persist_locator_page(
    handle: RollingHistorySqlHandle,
    namespace: str,
    page_index: int,
    locators: tuple[RollingHistoryV2EventLocator, ...],
) -> str:
    _rolling_history_v2_locator_tuple(locators)
    if (type(page_index) is not int or page_index < 0
            or not 1 <= len(locators) <= _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE):
        raise ValueError("Invalid rolling history v2 locator page")
    body = {
        "schema_version": _LOCATOR_INDEX_SCHEMA_VERSION,
        "kind": _LOCATOR_PAGE_KIND,
        "page_index": page_index,
        "locators": [asdict(item) for item in locators],
    }
    raw = _canonical(body)
    digest = _sha256(raw)
    _persist_immutable(handle, namespace + "locator-index:page:" + digest, raw)
    return digest


def _read_locator_page(
    handle: RollingHistorySqlHandle,
    namespace: str,
    digest: str,
    page_index: int,
    expected_count: int,
) -> tuple[RollingHistoryV2EventLocator, ...]:
    raw = _read_bounded_setting(
        handle,
        namespace + "locator-index:page:" + digest,
        _PUBLICATION_LOCATOR_PAGE_MAX_BYTES,
    )
    if (raw is None
            or len(raw.encode("utf-8", errors="strict"))
            > _PUBLICATION_LOCATOR_PAGE_MAX_BYTES):
        raise ValueError("Rolling history v2 locator page is unavailable")
    value = _decode_canonical_object(raw, "rolling history v2 locator page")
    required = {"schema_version", "kind", "page_index", "locators"}
    if (set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _LOCATOR_INDEX_SCHEMA_VERSION
            or value["kind"] != _LOCATOR_PAGE_KIND
            or type(value["page_index"]) is not int or value["page_index"] != page_index
            or type(value["locators"]) is not list
            or len(value["locators"]) != expected_count
            or not 1 <= expected_count <= _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE
            or _sha256(raw) != digest):
        raise ValueError("Rolling history v2 locator page commitment mismatch")
    locators = tuple(_decode_locator(item) for item in value["locators"])
    _rolling_history_v2_locator_tuple(locators)
    return locators


def _locator_tree_leaf(
    handle: RollingHistorySqlHandle,
    namespace: str,
    index: RollingHistoryV2LocatorIndex,
    page_index: int,
) -> str:
    _validate_rolling_history_v2_locator_index(index)
    if (type(page_index) is not int or not 0 <= page_index < index.page_count):
        raise ValueError("Rolling history v2 locator page index is outside its root")
    current = index.root_sha256
    for level in range(index.tree_height, -1, -1):
        children = _read_locator_node(handle, namespace, current, level)
        span = _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** level
        slot = (page_index // span) % _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
        current = children[slot]
        if current is None:
            raise ValueError("Rolling history v2 locator index has a page gap")
    return current


def _read_indexed_locator_page(
    handle: RollingHistorySqlHandle,
    namespace: str,
    index: RollingHistoryV2LocatorIndex,
    page_index: int,
    *,
    include_proof: bool,
) -> tuple[tuple[RollingHistoryV2EventLocator, ...], tuple[RollingHistoryV2LocatorIndexProofStep, ...]]:
    if (type(page_index) is not int or not 0 <= page_index < index.page_count):
        raise ValueError("Rolling history v2 locator page index is outside its root")
    expected_count = min(
        _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE,
        index.locator_count - page_index * _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE,
    )
    path: list[RollingHistoryV2LocatorIndexProofStep] = []
    current = index.root_sha256
    for level in range(index.tree_height, -1, -1):
        children = _read_locator_node(handle, namespace, current, level)
        span = _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** level
        slot = (page_index // span) % _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
        child = children[slot]
        if child is None:
            raise ValueError("Rolling history v2 locator index has a page gap")
        if include_proof:
            path.append(RollingHistoryV2LocatorIndexProofStep(
                level=level, child_index=slot, children=children,
            ))
        current = child
    locators = _read_locator_page(
        handle, namespace, current, page_index, expected_count,
    )
    if not include_proof:
        return locators, ()
    proof = tuple(reversed(path))
    proof_hash = current
    for step in proof:
        if step.children[step.child_index] != proof_hash:
            raise ValueError("Rolling history v2 locator index proof is invalid")
        proof_hash = _sha256(_canonical(_locator_node_body(step.level, step.children)))
    if proof_hash != index.root_sha256:
        raise ValueError("Rolling history v2 locator index proof does not reach its root")
    return locators, proof


def _set_locator_tree_leaf(
    handle: RollingHistorySqlHandle,
    namespace: str,
    root_sha256: str,
    tree_height: int,
    page_index: int,
    page_sha256: str,
) -> tuple[str, int]:
    empty_root = _rolling_history_v2_empty_locator_index_root()
    root = None if root_sha256 == empty_root else root_sha256
    height = tree_height
    while page_index >= _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** (height + 1):
        children: list[str | None] = [None] * _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
        children[0] = root
        root = _persist_locator_node(handle, namespace, height + 1, tuple(children))
        height += 1

    def update(level: int, current: str | None) -> str:
        children: list[str | None] = (
            [None] * _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
            if current is None else list(_read_locator_node(handle, namespace, current, level))
        )
        span = _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** level
        slot = (page_index // span) % _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
        if level == 0:
            children[slot] = page_sha256
        else:
            children[slot] = update(level - 1, children[slot])
        return _persist_locator_node(handle, namespace, level, tuple(children))

    return update(height, root), height


def _compute_set_locator_tree_leaf(
    handle: RollingHistorySqlHandle,
    namespace: str,
    root_sha256: str,
    tree_height: int,
    page_index: int,
    page_sha256: str,
) -> tuple[str, int]:
    """Compute an append-path root without writes for bounded READ verification."""
    empty_root = _rolling_history_v2_empty_locator_index_root()
    root = None if root_sha256 == empty_root else root_sha256
    height = tree_height
    while page_index >= _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** (height + 1):
        children: list[str | None] = [None] * _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
        children[0] = root
        root = _sha256(_canonical(_locator_node_body(height + 1, tuple(children))))
        height += 1

    def update(level: int, current: str | None) -> str:
        children: list[str | None] = (
            [None] * _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
            if current is None else list(_read_locator_node(handle, namespace, current, level))
        )
        span = _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** level
        slot = (page_index // span) % _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT
        if level == 0:
            children[slot] = page_sha256
        else:
            children[slot] = update(level - 1, children[slot])
        return _sha256(_canonical(_locator_node_body(level, tuple(children))))

    return update(height, root), height


def _compute_appended_lineage_index(
    handle: RollingHistorySqlHandle,
    namespace: str,
    previous: RollingHistoryV2LocatorIndex,
    appended: RollingHistoryV2EventLocator,
) -> RollingHistoryV2LocatorIndex:
    """Recompute one immutable lineage-index append using O(log pages) reads."""
    _validate_rolling_history_v2_locator_index(previous)
    _rolling_history_v2_locator_tuple((appended,))
    if previous.locator_count and appended.sequence <= previous.locator_count:
        raise ValueError("Rolling history v2 lineage append is not after its prefix")
    page_count = previous.page_count
    root = previous.root_sha256
    height = previous.tree_height
    page_index = max(0, page_count - 1)
    if previous.locator_count % _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE:
        tail, _ = _read_indexed_locator_page(
            handle, namespace, previous, page_count - 1, include_proof=False,
        )
        page = tail + (appended,)
        page_sha256 = _sha256(_canonical({
            "schema_version": _LOCATOR_INDEX_SCHEMA_VERSION,
            "kind": _LOCATOR_PAGE_KIND,
            "page_index": page_count - 1,
            "locators": [asdict(item) for item in page],
        }))
        root, height = _compute_set_locator_tree_leaf(
            handle, namespace, root, height, page_count - 1, page_sha256,
        )
    else:
        page_index = page_count
        page_sha256 = _sha256(_canonical({
            "schema_version": _LOCATOR_INDEX_SCHEMA_VERSION,
            "kind": _LOCATOR_PAGE_KIND,
            "page_index": page_index,
            "locators": [asdict(appended)],
        }))
        root, height = _compute_set_locator_tree_leaf(
            handle, namespace, root, height, page_index, page_sha256,
        )
        page_count += 1
    result = RollingHistoryV2LocatorIndex(
        schema_version=_LOCATOR_INDEX_SCHEMA_VERSION,
        root_sha256=root,
        locator_count=previous.locator_count + 1,
        page_count=page_count,
        tree_height=height,
    )
    _validate_rolling_history_v2_locator_index(result)
    return result


def _append_locator_index(
    handle: RollingHistorySqlHandle,
    namespace: str,
    index: RollingHistoryV2LocatorIndex | None,
    appended: tuple[RollingHistoryV2EventLocator, ...],
) -> RollingHistoryV2LocatorIndex:
    current = _empty_locator_index() if index is None else index
    _validate_rolling_history_v2_locator_index(current)
    _rolling_history_v2_locator_tuple(appended)
    if not appended:
        return current
    pending = list(appended)
    if current.locator_count:
        last_page_index = current.page_count - 1
        last_page, _ = _read_indexed_locator_page(
            handle, namespace, current, last_page_index, include_proof=False,
        )
        if appended[0].sequence <= last_page[-1].sequence:
            raise ValueError("Rolling history v2 indexed append is not after its prefix")
    root = current.root_sha256
    height = current.tree_height
    page_count = current.page_count
    existing_tail_size = current.locator_count % _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE
    if existing_tail_size:
        tail, _ = _read_indexed_locator_page(
            handle, namespace, current, page_count - 1, include_proof=False,
        )
        take = min(_ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE - len(tail), len(pending))
        merged = tail + tuple(pending[:take])
        page_sha256 = _persist_locator_page(
            handle, namespace, page_count - 1, merged,
        )
        root, height = _set_locator_tree_leaf(
            handle, namespace, root, height, page_count - 1, page_sha256,
        )
        pending = pending[take:]
    while pending:
        chunk = tuple(pending[:_ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE])
        page_sha256 = _persist_locator_page(handle, namespace, page_count, chunk)
        root, height = _set_locator_tree_leaf(
            handle, namespace, root, height, page_count, page_sha256,
        )
        page_count += 1
        pending = pending[len(chunk):]
    result = RollingHistoryV2LocatorIndex(
        schema_version=_LOCATOR_INDEX_SCHEMA_VERSION,
        root_sha256=root,
        locator_count=current.locator_count + len(appended),
        page_count=page_count,
        tree_height=height,
    )
    _validate_rolling_history_v2_locator_index(result)
    return result


def _truncate_locator_index(
    handle: RollingHistorySqlHandle,
    namespace: str,
    index: RollingHistoryV2LocatorIndex,
    retain_count: int,
) -> RollingHistoryV2LocatorIndex:
    _validate_rolling_history_v2_locator_index(index)
    if type(retain_count) is not int or not 0 <= retain_count <= index.locator_count:
        raise ValueError("Invalid rolling history v2 locator index cut")
    if retain_count == index.locator_count:
        return index
    if retain_count == 0:
        return _empty_locator_index()

    page_count = (retain_count + _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE - 1) // (
        _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE
    )
    root = index.root_sha256
    height = index.tree_height
    tail_size = retain_count % _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE
    if tail_size:
        last_page_index = page_count - 1
        old_page, _ = _read_indexed_locator_page(
            handle, namespace, index, last_page_index, include_proof=False,
        )
        page_sha256 = _persist_locator_page(
            handle, namespace, last_page_index, old_page[:tail_size],
        )
        root, height = _set_locator_tree_leaf(
            handle, namespace, root, height, last_page_index, page_sha256,
        )

    def trim(level: int, node_hash: str, base_page: int) -> str | None:
        covered_pages = _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** (level + 1)
        if base_page >= page_count:
            return None
        if base_page + covered_pages <= page_count:
            return node_hash
        children = list(_read_locator_node(handle, namespace, node_hash, level))
        child_pages = _ROLLING_HISTORY_V2_LOCATOR_TREE_FANOUT ** level
        for slot, child_hash in enumerate(children):
            child_base = base_page + slot * child_pages
            if child_base >= page_count:
                children[slot] = None
            elif (child_hash is not None and level > 0
                    and child_base + child_pages > page_count):
                children[slot] = trim(level - 1, child_hash, child_base)
        if not any(child is not None for child in children):
            return None
        return _persist_locator_node(handle, namespace, level, tuple(children))

    trimmed = trim(height, root, 0)
    if trimmed is None:
        return _empty_locator_index()
    root = trimmed
    while height > 0:
        children = _read_locator_node(handle, namespace, root, height)
        present = [position for position, child in enumerate(children) if child is not None]
        if present != [0]:
            break
        root = children[0]
        height -= 1
    result = RollingHistoryV2LocatorIndex(
        schema_version=_LOCATOR_INDEX_SCHEMA_VERSION,
        root_sha256=root,
        locator_count=retain_count,
        page_count=page_count,
        tree_height=height,
    )
    _validate_rolling_history_v2_locator_index(result)
    return result


def _decode_canonical_object(raw: str, label: str) -> dict:
    if type(raw) is not str:
        raise ValueError(f"Invalid {label}")
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"Invalid {label}") from exc
    if type(value) is not dict or _canonical(value) != raw:
        raise ValueError(f"Invalid {label}")
    return value


def _decode_locator(raw) -> RollingHistoryV2EventLocator:
    required = {
        "event_id", "request_id", "sequence", "version",
        "content_sha256", "chain_sha256",
    }
    if type(raw) is not dict or set(raw) != required:
        raise ValueError("Invalid rolling history v2 event locator")
    locator = RollingHistoryV2EventLocator(**raw)
    return locator


def _decode_locator_index(raw) -> RollingHistoryV2LocatorIndex:
    required = {
        "schema_version", "root_sha256", "locator_count", "page_count", "tree_height",
    }
    if type(raw) is not dict or set(raw) != required:
        raise ValueError("Invalid rolling history v2 locator index")
    index = RollingHistoryV2LocatorIndex(**raw)
    _validate_rolling_history_v2_locator_index(index)
    return index


def _revision_value(revision: RollingHistoryV2Revision) -> dict:
    value = _rolling_history_v2_revision_envelope(revision)
    value["revision_sha256"] = revision.revision_sha256
    return value


def _decode_revision(raw: str) -> RollingHistoryV2Revision:
    value = _decode_canonical_object(raw, "rolling history v2 revision")
    legacy_required = {
        "schema_version", "scope_sha256", "branch_id", "revision_id",
        "revision_sha256", "revision_number", "history_revision",
        "history_head_sha256", "request_id", "idempotency_id",
        "idempotency_key_sha256", "operation", "representation",
        "parent_revision_id", "parent_revision_sha256", "lineage_depth",
        "event_locators",
    }
    indexed_required = (legacy_required - {"event_locators"}) | {
        "locator_index", "lineage_index", "parent_lineage_commitment_sha256",
        "lineage_root_sha256", "lineage_commitment_sha256",
    }
    if type(value.get("schema_version")) is not int:
        raise ValueError("Invalid rolling history v2 revision")
    if value["schema_version"] == 2:
        if set(value) != legacy_required or type(value["event_locators"]) is not list:
            raise ValueError("Invalid rolling history v2 revision")
        event_locators = tuple(_decode_locator(item) for item in value["event_locators"])
        locator_index = None
    elif value["schema_version"] == 3:
        if set(value) != indexed_required:
            raise ValueError("Invalid indexed rolling history v2 revision")
        event_locators = ()
        locator_index = _decode_locator_index(value["locator_index"])
        lineage_index = _decode_locator_index(value["lineage_index"])
    else:
        raise ValueError("Unsupported rolling history v2 revision version")
    revision = RollingHistoryV2Revision(
        schema_version=value["schema_version"],
        scope_sha256=value["scope_sha256"],
        branch_id=value["branch_id"],
        revision_id=value["revision_id"],
        revision_sha256=value["revision_sha256"],
        revision_number=value["revision_number"],
        history_revision=value["history_revision"],
        history_head_sha256=value["history_head_sha256"],
        request_id=value["request_id"],
        idempotency_id=value["idempotency_id"],
        idempotency_key_sha256=value["idempotency_key_sha256"],
        operation=value["operation"],
        representation=value["representation"],
        parent_revision_id=value["parent_revision_id"],
        parent_revision_sha256=value["parent_revision_sha256"],
        lineage_depth=value["lineage_depth"],
        event_locators=event_locators,
        locator_index=locator_index,
        lineage_index=(lineage_index if value["schema_version"] == 3 else None),
        parent_lineage_commitment_sha256=value.get("parent_lineage_commitment_sha256"),
        lineage_root_sha256=value.get("lineage_root_sha256"),
        lineage_commitment_sha256=value.get("lineage_commitment_sha256"),
    )
    _validate_rolling_history_v2_revision(revision)
    return revision


def _branch_value(branch: RollingHistoryV2Branch) -> dict:
    value = _rolling_history_v2_branch_envelope(branch)
    value["branch_sha256"] = branch.branch_sha256
    return value


def _decode_branch(raw: str) -> RollingHistoryV2Branch:
    value = _decode_canonical_object(raw, "rolling history v2 branch")
    required = {
        "schema_version", "scope_sha256", "branch_id", "created_revision_id",
        "created_revision_sha256", "request_id", "idempotency_id",
        "idempotency_key_sha256", "branch_sha256",
    }
    if set(value) != required:
        raise ValueError("Invalid rolling history v2 branch")
    return RollingHistoryV2Branch(**value)


def _head_value(head: RollingHistoryV2Head) -> dict:
    value = _rolling_history_v2_head_envelope(head)
    value["head_sha256"] = head.head_sha256
    return value


def _bound_head_digest(
    wrapper_version: int,
    canonical_namespace_digest: str,
    head: RollingHistoryV2Head,
) -> str:
    return _sha256(_canonical({
        "wrapper_version": wrapper_version,
        "canonical_namespace_digest": canonical_namespace_digest,
        "head": _head_value(head),
    }))


def _new_bound_head(
    head: RollingHistoryV2Head,
    canonical_namespace_digest: str,
) -> _BoundRollingHistoryV2Head:
    _validate_rolling_history_v2_head_token(head)
    if (type(canonical_namespace_digest) is not str
            or _HASH.fullmatch(canonical_namespace_digest) is None):
        raise RollingHistoryOwnerScopeError("Rolling history owner scope is unavailable")
    return _BoundRollingHistoryV2Head(
        wrapper_version=_BOUND_HEAD_TOKEN_VERSION,
        canonical_namespace_digest=canonical_namespace_digest,
        head=head,
        wrapper_sha256=_bound_head_digest(
            _BOUND_HEAD_TOKEN_VERSION, canonical_namespace_digest, head,
        ),
    )


def _validate_bound_head_token(token: object) -> None:
    if type(token) is RollingHistoryV2Head:
        raise RollingHistoryV2MigrationRequired()
    if type(token) is not _BoundRollingHistoryV2Head:
        raise ValueError("Invalid rolling history v2 bound-head token")
    if type(token.wrapper_version) is not int or token.wrapper_version != _BOUND_HEAD_TOKEN_VERSION:
        raise RollingHistoryV2MigrationRequired()
    if (type(token.canonical_namespace_digest) is not str
            or _HASH.fullmatch(token.canonical_namespace_digest) is None
            or type(token.wrapper_sha256) is not str
            or _HASH.fullmatch(token.wrapper_sha256) is None):
        raise ValueError("Invalid rolling history v2 bound-head token")
    _validate_rolling_history_v2_head_token(token.head)
    if token.wrapper_sha256 != _bound_head_digest(
        token.wrapper_version, token.canonical_namespace_digest, token.head,
    ):
        raise ValueError("Invalid rolling history v2 bound-head digest")


def _decode_head(raw) -> RollingHistoryV2Head:
    if type(raw) is str:
        value = _decode_canonical_object(raw, "rolling history v2 head")
    elif type(raw) is dict:
        value = raw
    else:
        raise ValueError("Invalid rolling history v2 head")
    required = {
        "schema_version", "scope_sha256", "branch_id", "revision_id",
        "revision_sha256", "revision_number", "history_revision",
        "history_head_sha256", "head_sha256",
    }
    if set(value) != required:
        raise ValueError("Invalid rolling history v2 head")
    head = RollingHistoryV2Head(**value)
    _validate_rolling_history_v2_head_token(head)
    return head


def _new_revision(
    *,
    handle: RollingHistorySqlHandle,
    namespace: str,
    scope: ContextEventScope,
    branch_id: str,
    idempotency_id: str,
    idempotency_key_sha256: str,
    operation: str,
    representation: str,
    parent: RollingHistoryV2Revision | None,
    history_revision: int,
    history_head_sha256: str,
    lineage_depth: int,
    event_locators: tuple[RollingHistoryV2EventLocator, ...],
    locator_index: RollingHistoryV2LocatorIndex | None = None,
) -> RollingHistoryV2Revision:
    if locator_index is None:
        locator_index = _empty_locator_index()
    _validate_rolling_history_v2_locator_index(locator_index)
    if event_locators:
        raise ValueError("New rolling history v2 revisions must use a locator index")
    scope_sha256 = _scope_digest(scope)
    lineage_root_sha256 = _rolling_history_v2_lineage_root_sha256(
        scope_sha256, branch_id,
    )
    if parent is not None:
        if (parent.schema_version != 3
                or type(parent.lineage_commitment_sha256) is not str
                or _HASH.fullmatch(parent.lineage_commitment_sha256) is None
                or parent.lineage_index is None):
            raise RollingHistoryV2MigrationRequired()
        if operation != "fork" and parent.lineage_root_sha256 != lineage_root_sha256:
            raise ValueError("Rolling history v2 revision changed its branch lineage root")
        parent_lineage_commitment_sha256 = parent.lineage_commitment_sha256
    else:
        parent_lineage_commitment_sha256 = None
    values = {
        "schema_version": 3,
        "scope_sha256": scope_sha256,
        "branch_id": branch_id,
        "revision_id": "0" * 64,
        "revision_sha256": "0" * 64,
        "revision_number": 1 if parent is None or operation == "fork" else parent.revision_number + 1,
        "history_revision": history_revision,
        "history_head_sha256": history_head_sha256,
        "request_id": scope.request_id,
        "idempotency_id": idempotency_id,
        "idempotency_key_sha256": idempotency_key_sha256,
        "operation": operation,
        "representation": representation,
        "parent_revision_id": None if parent is None else parent.revision_id,
        "parent_revision_sha256": None if parent is None else parent.revision_sha256,
        "lineage_depth": 0,
        "event_locators": (),
        "locator_index": locator_index,
        "lineage_index": _empty_locator_index(),
        "parent_lineage_commitment_sha256": parent_lineage_commitment_sha256,
        "lineage_root_sha256": lineage_root_sha256,
        "lineage_commitment_sha256": "0" * 64,
    }
    provisional = RollingHistoryV2Revision(**values)
    lineage_commitment_sha256 = _rolling_history_v2_lineage_commitment_sha256(provisional)
    values["lineage_commitment_sha256"] = lineage_commitment_sha256
    lineage_leaf = _lineage_index_locator(
        sequence=1 if parent is None or operation == "fork" else parent.revision_number + 1,
        request_id=scope.request_id,
        lineage_commitment_sha256=lineage_commitment_sha256,
        lineage_root_sha256=lineage_root_sha256,
    )
    lineage_base = (
        _empty_locator_index()
        if parent is None or operation == "fork"
        else parent.lineage_index
    )
    values["lineage_index"] = _append_locator_index(
        handle, namespace + "lineage:", lineage_base, (lineage_leaf,),
    )
    provisional = RollingHistoryV2Revision(**values)
    identity_fields = {
        key: value for key, value in _rolling_history_v2_revision_envelope(provisional).items()
        if key != "revision_id"
    }
    revision_id = _sha256(_canonical(identity_fields))
    with_id = RollingHistoryV2Revision(**{**values, "revision_id": revision_id})
    revision_sha256 = _sha256(_canonical(_rolling_history_v2_revision_envelope(with_id)))
    revision = RollingHistoryV2Revision(**{
        **values,
        "revision_id": revision_id,
        "revision_sha256": revision_sha256,
    })
    _validate_rolling_history_v2_revision(revision)
    return revision


def _revision_preview(
    scope: ContextEventScope,
    handle: RollingHistorySqlHandle,
    namespace: str,
    branch_id: str,
    idempotency_id: str,
    idempotency_key_sha256: str,
    parent: RollingHistoryV2Revision,
    operation: str,
    representation: str,
    history_revision: int,
    history_head_sha256: str,
    lineage_depth: int,
    event_locators: tuple[RollingHistoryV2EventLocator, ...],
    locator_index: RollingHistoryV2LocatorIndex | None = None,
) -> RollingHistoryV2Revision:
    """Construct a validated candidate for the existing lineage helper."""
    return _new_revision(
        handle=handle,
        namespace=namespace,
        scope=scope,
        branch_id=branch_id,
        idempotency_id=idempotency_id,
        idempotency_key_sha256=idempotency_key_sha256,
        operation=operation,
        representation=representation,
        parent=parent,
        history_revision=history_revision,
        history_head_sha256=history_head_sha256,
        lineage_depth=lineage_depth,
        event_locators=event_locators,
        locator_index=locator_index,
    )


def _new_branch(
    scope: ContextEventScope,
    created_revision: RollingHistoryV2Revision,
) -> RollingHistoryV2Branch:
    values = {
        "schema_version": _STORE_SCHEMA_VERSION,
        "scope_sha256": _scope_digest(scope),
        "branch_id": created_revision.branch_id,
        "created_revision_id": created_revision.revision_id,
        "created_revision_sha256": created_revision.revision_sha256,
        "request_id": created_revision.request_id,
        "idempotency_id": created_revision.idempotency_id,
        "idempotency_key_sha256": created_revision.idempotency_key_sha256,
        "branch_sha256": "0" * 64,
    }
    provisional = RollingHistoryV2Branch(**values)
    branch_sha256 = _sha256(_canonical(_rolling_history_v2_branch_envelope(provisional)))
    branch = RollingHistoryV2Branch(**{**values, "branch_sha256": branch_sha256})
    _validate_rolling_history_v2_branch(branch, created_revision)
    return branch


def _new_head(revision: RollingHistoryV2Revision) -> RollingHistoryV2Head:
    values = {
        "schema_version": _STORE_SCHEMA_VERSION,
        "scope_sha256": revision.scope_sha256,
        "branch_id": revision.branch_id,
        "revision_id": revision.revision_id,
        "revision_sha256": revision.revision_sha256,
        "revision_number": revision.revision_number,
        "history_revision": revision.history_revision,
        "history_head_sha256": revision.history_head_sha256,
        "head_sha256": "0" * 64,
    }
    provisional = RollingHistoryV2Head(**values)
    head_sha256 = _sha256(_canonical(_rolling_history_v2_head_envelope(provisional)))
    head = RollingHistoryV2Head(**{**values, "head_sha256": head_sha256})
    _validate_rolling_history_v2_head(head, revision)
    return head


def _locator_from_event(event) -> RollingHistoryV2EventLocator:
    reference = event.reference
    return RollingHistoryV2EventLocator(
        event_id=reference.event_id,
        request_id=reference.request_id,
        sequence=reference.sequence,
        version=reference.version,
        content_sha256=reference.content_sha256,
        chain_sha256=reference.chain_sha256,
    )


def _read_v1_history_head(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    prefix: str,
) -> tuple[int, str]:
    return _owner_history_head(handle, scope, prefix)


def _verify_history_endpoint(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    prefix: str,
    history_revision: int,
    history_head_sha256: str,
) -> None:
    if history_revision == 0:
        if history_head_sha256 != _genesis_chain(scope):
            raise ValueError("Rolling history v2 genesis head mismatch")
        return
    reference = _owner_history_read_event_reference_at_sequence(
        handle, scope, prefix, history_revision,
    )
    if reference.chain_sha256 != history_head_sha256:
        raise ValueError("Rolling history v2 source history endpoint mismatch")


def _verify_locator(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    prefix: str,
    locator: RollingHistoryV2EventLocator,
) -> None:
    reference = _owner_history_read_event_reference_at_sequence(
        handle, scope, prefix, locator.sequence,
    )
    if _locator_from_event(SimpleNamespace(reference=reference)) != locator:
        raise ValueError("Rolling history v2 source locator mismatch")


def _read_revision(
    handle: RollingHistorySqlHandle,
    namespace: str,
    revision_id: str,
) -> RollingHistoryV2Revision:
    raw = _read_setting(handle, namespace + "revision:" + revision_id)
    if raw is None:
        raise ValueError("Rolling history v2 revision is unavailable")
    revision = _decode_revision(raw)
    if revision.revision_id != revision_id:
        raise ValueError("Rolling history v2 revision key mismatch")
    return revision


def _lineage_index_locator(
    *,
    sequence: int,
    request_id: str,
    lineage_commitment_sha256: str,
    lineage_root_sha256: str,
) -> RollingHistoryV2EventLocator:
    return RollingHistoryV2EventLocator(
        event_id=lineage_commitment_sha256,
        request_id=request_id,
        sequence=sequence,
        version=2,
        content_sha256=lineage_commitment_sha256,
        chain_sha256=lineage_root_sha256,
    )


def _read_bounded_revision(
    handle: RollingHistorySqlHandle,
    namespace: str,
    revision_id: str,
) -> RollingHistoryV2Revision:
    raw = _read_bounded_setting(handle, namespace + "revision:" + revision_id, 2048)
    if raw is None:
        raise RollingHistoryV2MigrationRequired()
    revision = _decode_revision(raw)
    if revision.revision_id != revision_id or revision.schema_version != 3:
        raise RollingHistoryV2MigrationRequired()
    return revision


def _new_lineage_proof(**values) -> RollingHistoryV2LineageProof:
    provisional = RollingHistoryV2LineageProof(
        **values,
        proof_sha256="0" * 64,
    )
    envelope = asdict(provisional)
    envelope.pop("proof_sha256")
    return RollingHistoryV2LineageProof(
        **values,
        proof_sha256=_sha256(_canonical(envelope)),
    )


def _read_indexed_revision_lineage_proof(
    handle: RollingHistorySqlHandle,
    namespace: str,
    branch: RollingHistoryV2Branch,
    revision: RollingHistoryV2Revision,
) -> RollingHistoryV2LineageProof:
    """Verify creator, direct parent, and head membership with logarithmic paths."""
    _validate_rolling_history_v2_revision(revision)
    if (revision.schema_version != 3 or revision.lineage_index is None
            or revision.branch_id != branch.branch_id
            or revision.lineage_index.locator_count != revision.revision_number
            or revision.lineage_root_sha256
            != _rolling_history_v2_lineage_root_sha256(
                revision.scope_sha256, branch.branch_id,
            )):
        raise ValueError("Rolling history v2 indexed lineage is unavailable")

    creator = (
        revision if branch.created_revision_id == revision.revision_id
        else _read_bounded_revision(handle, namespace, branch.created_revision_id)
    )
    _validate_rolling_history_v2_branch(branch, creator)
    if (creator.operation not in ("root", "fork")
            or creator.revision_number != 1
            or creator.branch_id != branch.branch_id
            or creator.lineage_root_sha256 != revision.lineage_root_sha256
            or creator.lineage_index is None
            or creator.lineage_index.locator_count != 1):
        raise ValueError("Rolling history v2 branch creator proof is invalid")

    creator_locator = _lineage_index_locator(
        sequence=creator.revision_number,
        request_id=creator.request_id,
        lineage_commitment_sha256=creator.lineage_commitment_sha256,
        lineage_root_sha256=creator.lineage_root_sha256,
    )
    revision_locator = _lineage_index_locator(
        sequence=revision.revision_number,
        request_id=revision.request_id,
        lineage_commitment_sha256=revision.lineage_commitment_sha256,
        lineage_root_sha256=revision.lineage_root_sha256,
    )
    parent = None
    parent_locator = None
    parent_page_locators = None
    parent_proof: tuple[RollingHistoryV2LocatorIndexProofStep, ...] = ()
    lineage_namespace = namespace + "lineage:"
    if revision.parent_revision_id is None:
        if (revision.revision_id != creator.revision_id
                or revision.operation != "root"
                or revision.parent_revision_sha256 is not None
                or revision.parent_lineage_commitment_sha256 is not None):
            raise ValueError("Rolling history v2 lineage root is not the branch creator")
        expected_index = _compute_appended_lineage_index(
            handle, lineage_namespace, _empty_locator_index(), revision_locator,
        )
    else:
        parent = _read_bounded_revision(
            handle, namespace, revision.parent_revision_id,
        )
        if (parent.scope_sha256 != revision.scope_sha256
                or parent.revision_sha256 != revision.parent_revision_sha256
                or parent.lineage_commitment_sha256
                != revision.parent_lineage_commitment_sha256):
            raise ValueError("Rolling history v2 direct parent commitment mismatch")
        if revision.operation == "fork":
            if (revision.revision_id != creator.revision_id
                    or parent.branch_id == branch.branch_id):
                raise ValueError("Rolling history v2 fork creator parent is invalid")
            parent_locator = None
            expected_index = _compute_appended_lineage_index(
                handle, lineage_namespace, _empty_locator_index(), revision_locator,
            )
        else:
            if (revision.revision_id == creator.revision_id
                    or parent.branch_id != branch.branch_id
                    or parent.lineage_root_sha256 != revision.lineage_root_sha256
                    or parent.revision_number + 1 != revision.revision_number
                    or parent.lineage_index is None):
                raise ValueError("Rolling history v2 branch parent lineage is invalid")
            parent_locator = _lineage_index_locator(
                sequence=parent.revision_number,
                request_id=parent.request_id,
                lineage_commitment_sha256=parent.lineage_commitment_sha256,
                lineage_root_sha256=parent.lineage_root_sha256,
            )
            expected_index = _compute_appended_lineage_index(
                handle, lineage_namespace, parent.lineage_index, revision_locator,
            )
    if expected_index != revision.lineage_index:
        raise ValueError("Rolling history v2 lineage index append proof mismatch")

    creator_page, creator_proof = _read_indexed_locator_page(
        handle, lineage_namespace, revision.lineage_index, 0,
        include_proof=True,
    )
    if not creator_page or creator_page[0] != creator_locator:
        raise ValueError("Rolling history v2 branch creator is absent from lineage root")

    revision_page_index = (revision.revision_number - 1) // _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE
    revision_offset = (revision.revision_number - 1) % _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE
    if revision_page_index == 0:
        revision_page, revision_proof = creator_page, creator_proof
    else:
        revision_page, revision_proof = _read_indexed_locator_page(
            handle, lineage_namespace, revision.lineage_index,
            revision_page_index, include_proof=True,
        )
    if revision_offset >= len(revision_page) or revision_page[revision_offset] != revision_locator:
        raise ValueError("Rolling history v2 head is absent from lineage root")

    if parent is not None and parent_locator is not None:
        parent_page_index = (parent.revision_number - 1) // _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE
        parent_offset = (parent.revision_number - 1) % _ROLLING_HISTORY_V2_LOCATOR_PAGE_SIZE
        if parent_page_index == revision_page_index:
            parent_page, parent_proof = revision_page, revision_proof
        elif parent_page_index == 0:
            parent_page, parent_proof = creator_page, creator_proof
        else:
            parent_page, parent_proof = _read_indexed_locator_page(
                handle, lineage_namespace, revision.lineage_index,
                parent_page_index, include_proof=True,
            )
        if parent_offset >= len(parent_page) or parent_page[parent_offset] != parent_locator:
            raise ValueError("Rolling history v2 direct parent is absent from lineage root")
        parent_page_locators = parent_page

    return _new_lineage_proof(
        schema_version=1,
        branch_id=branch.branch_id,
        lineage_root_sha256=revision.lineage_root_sha256,
        lineage_index=revision.lineage_index,
        creator_revision=creator,
        creator_revision_id=creator.revision_id,
        creator_revision_sha256=creator.revision_sha256,
        creator_lineage_commitment_sha256=creator.lineage_commitment_sha256,
        creator_lineage_locator=creator_locator,
        creator_page_locators=creator_page,
        creator_index_proof=creator_proof,
        revision_number=revision.revision_number,
        revision=revision,
        revision_id=revision.revision_id,
        revision_sha256=revision.revision_sha256,
        revision_lineage_commitment_sha256=revision.lineage_commitment_sha256,
        revision_lineage_locator=revision_locator,
        revision_page_locators=revision_page,
        revision_index_proof=revision_proof,
        parent_revision_id=None if parent is None else parent.revision_id,
        parent_revision_sha256=None if parent is None else parent.revision_sha256,
        parent_revision=parent,
        parent_lineage_commitment_sha256=revision.parent_lineage_commitment_sha256,
        parent_revision_lineage_commitment_sha256=(
            None if parent is None else parent.lineage_commitment_sha256
        ),
        parent_lineage_locator=parent_locator,
        parent_page_locators=parent_page_locators,
        parent_index_proof=parent_proof,
    )


def _read_lineage(
    handle: RollingHistorySqlHandle,
    namespace: str,
    head_revision: RollingHistoryV2Revision,
) -> tuple[tuple[RollingHistoryV2Revision, ...], tuple[RollingHistoryV2EventLocator, ...]]:
    reverse_chain: list[RollingHistoryV2Revision] = []
    seen_revision_ids: set[str] = set()
    current = head_revision
    while True:
        _validate_rolling_history_v2_revision(current)
        if current.revision_id in seen_revision_ids:
            raise ValueError("Rolling history v2 lineage contains a cycle")
        seen_revision_ids.add(current.revision_id)
        reverse_chain.append(current)
        if current.parent_revision_id is None:
            break
        current = _read_revision(handle, namespace, current.parent_revision_id)
    chain = tuple(reversed(reverse_chain))
    visible = _validate_rolling_history_v2_lineage(chain)
    return chain, visible


def _verify_lineage_sources(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    chain: tuple[RollingHistoryV2Revision, ...],
) -> None:
    v1_prefix = _scope_prefix(scope)
    verified_endpoints: set[tuple[int, str]] = set()
    verified_event_ids: set[str] = set()
    for revision in chain:
        endpoint = (revision.history_revision, revision.history_head_sha256)
        if endpoint not in verified_endpoints:
            _verify_history_endpoint(
                handle, scope, v1_prefix, revision.history_revision,
                revision.history_head_sha256,
            )
            verified_endpoints.add(endpoint)
        for locator in revision.event_locators:
            if locator.event_id not in verified_event_ids:
                _verify_locator(handle, scope, v1_prefix, locator)
                verified_event_ids.add(locator.event_id)


def _read_current_head(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    branch_id: str,
) -> _RawRollingHistoryV2StoreResult:
    raw = _read_setting(handle, namespace + "head:" + branch_id)
    if raw is None:
        raise ValueError("Rolling history v2 branch head is unavailable")
    head = _decode_head(raw)
    if head.branch_id != branch_id:
        raise ValueError("Rolling history v2 branch head key mismatch")
    return _read_result_for_head(handle, scope, namespace, head)


def _read_result_for_head(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    head: RollingHistoryV2Head,
) -> _RawRollingHistoryV2StoreResult:
    if head.scope_sha256 != _scope_digest(scope):
        raise ValueError("Rolling history v2 head scope mismatch")
    revision = _read_revision(handle, namespace, head.revision_id)
    _validate_rolling_history_v2_head(head, revision)
    branch_raw = _read_setting(handle, namespace + "branch:" + head.branch_id)
    if branch_raw is None:
        raise ValueError("Rolling history v2 branch is unavailable")
    branch = _decode_branch(branch_raw)
    if (branch.scope_sha256 != _scope_digest(scope)
            or branch.branch_id != head.branch_id
            or branch.branch_sha256 != _sha256(_canonical(_rolling_history_v2_branch_envelope(branch)))
            or revision.branch_id != branch.branch_id):
        raise ValueError("Rolling history v2 branch lineage mismatch")
    if revision.schema_version == 3:
        _validate_rolling_history_v2_locator_index(revision.locator_index)
        lineage_proof = _read_indexed_revision_lineage_proof(
            handle, namespace, branch, revision,
        )
        return _RawRollingHistoryV2StoreResult(
            branch, revision, head, (), revision.locator_index, lineage_proof,
        )

    chain, visible = _read_lineage(handle, namespace, revision)
    if chain[-1] != revision:
        raise ValueError("Rolling history v2 head lineage mismatch")
    _verify_lineage_sources(handle, scope, chain)
    creator = _read_revision(handle, namespace, branch.created_revision_id)
    _validate_rolling_history_v2_branch(branch, creator)
    if creator.revision_id not in {item.revision_id for item in chain}:
        raise ValueError("Rolling history v2 branch lineage mismatch")
    return _RawRollingHistoryV2StoreResult(branch, revision, head, visible)


def _upgrade_legacy_result_to_index(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    result: _RawRollingHistoryV2StoreResult,
) -> _RawRollingHistoryV2StoreResult:
    if result.revision.schema_version == 3:
        return result
    raise RollingHistoryV2MigrationRequired()


def _read_idempotent_result(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    idempotency_id: str,
    key_sha256: str,
    payload_sha256: str,
    operation: str,
):
    raw = _read_setting(handle, namespace + "idempotency:" + idempotency_id)
    if raw is None:
        return _NO_IDEMPOTENCY
    value = _decode_canonical_object(raw, "rolling history v2 idempotency record")
    required = {
        "schema_version", "kind", "scope_sha256", "request_id",
        "idempotency_id", "idempotency_key_sha256", "payload_sha256",
        "operation", "branch_id", "revision_id", "revision_sha256", "head",
    }
    if (set(value) != required or type(value["schema_version"]) is not int
            or value["schema_version"] != _STORE_SCHEMA_VERSION
            or value["kind"] != "rolling_revision_idempotency"
            or value["scope_sha256"] != _scope_digest(scope)
            or value["request_id"] != scope.request_id
            or value["idempotency_id"] != idempotency_id
            or value["idempotency_key_sha256"] != key_sha256
            or type(value["payload_sha256"]) is not str
            or _HASH.fullmatch(value["payload_sha256"]) is None
            or type(value["operation"]) is not str):
        raise ValueError("Invalid rolling history v2 idempotency binding")
    if value["operation"] != operation or value["payload_sha256"] != payload_sha256:
        return _ConflictResult("Rolling history v2 idempotency key payload conflict")
    head = _decode_head(value["head"])
    if (head.branch_id != value["branch_id"]
            or head.revision_id != value["revision_id"]
            or head.revision_sha256 != value["revision_sha256"]):
        raise ValueError("Rolling history v2 idempotency result mismatch")
    result = _read_result_for_head(handle, scope, namespace, head)
    if (result.branch.branch_id != value["branch_id"]
            or result.revision.revision_id != value["revision_id"]
            or result.revision.revision_sha256 != value["revision_sha256"]):
        raise ValueError("Rolling history v2 idempotency result is unavailable")
    return result


def _prepared_summary_source_manifest(
    scope: ContextEventScope,
    canonical_namespace_digest: str,
    result: _RawRollingHistoryV2StoreResult,
) -> dict:
    return {
        "schema_version": _STORE_SCHEMA_VERSION,
        "kind": "rolling_history_v2_prepared_summary_source_manifest",
        "scope_sha256": _scope_digest(scope),
        "canonical_namespace_digest": canonical_namespace_digest,
        "request_id": scope.request_id,
        "branch": _branch_value(result.branch),
        "revision": _revision_value(result.revision),
        "expected_head": _head_value(result.head),
        "history_revision": result.revision.history_revision,
        "history_head_sha256": result.revision.history_head_sha256,
        "event_locators": [asdict(item) for item in result.visible_event_locators],
    }


def _new_prepared_summary_descriptor(
    scope: ContextEventScope,
    revision: RollingHistoryV2Revision,
    prepared_id: str,
    key_sha256: str,
    summary_sha256: str,
) -> PreparedRollingHistoryV2Summary:
    values = {
        "schema_version": _STORE_SCHEMA_VERSION,
        "prepared_id": prepared_id,
        "scope_sha256": _scope_digest(scope),
        "branch_id": revision.branch_id,
        "request_id": scope.request_id,
        "idempotency_id": prepared_id,
        "idempotency_key_sha256": key_sha256,
        "history_revision_id": revision.revision_id,
        "history_revision_sha256": revision.revision_sha256,
        "summary_sha256": summary_sha256,
        "prepared_sha256": "0" * 64,
    }
    provisional = PreparedRollingHistoryV2Summary(**values)
    prepared_sha256 = _sha256(
        _canonical(_rolling_history_v2_prepared_envelope(provisional)),
    )
    prepared = PreparedRollingHistoryV2Summary(
        **{**values, "prepared_sha256": prepared_sha256},
    )
    _validate_prepared_rolling_history_v2_summary(prepared, revision)
    return prepared


def _prepared_summary_payload_sha256(
    expected_head: RollingHistoryV2Head,
    summary: str,
    source_manifest: dict,
    prepared: PreparedRollingHistoryV2Summary,
) -> str:
    return _sha256(_canonical({
        "operation": "prepare_summary",
        "expected_head": _head_value(expected_head),
        "summary": summary,
        "source_manifest": source_manifest,
        "prepared": asdict(prepared),
    }))


def _prepared_summary_record_digest(value: dict) -> str:
    fields = {
        key: item for key, item in value.items()
        if key != "prepared_record_sha256"
    }
    return _sha256(_canonical(fields))


def _prepared_summary_record_value(
    scope: ContextEventScope,
    canonical_namespace_digest: str,
    result: _RawRollingHistoryV2StoreResult,
    summary: str,
    prepared_id: str,
    key_sha256: str,
) -> dict:
    summary_sha256 = _sha256(summary)
    prepared = _new_prepared_summary_descriptor(
        scope, result.revision, prepared_id, key_sha256, summary_sha256,
    )
    source_manifest = _prepared_summary_source_manifest(
        scope, canonical_namespace_digest, result,
    )
    source_sha256 = _sha256(_canonical(source_manifest))
    value = {
        "schema_version": _STORE_SCHEMA_VERSION,
        "kind": "rolling_history_v2_prepared_summary",
        "state": "PREPARED",
        "scope_sha256": _scope_digest(scope),
        "canonical_namespace_digest": canonical_namespace_digest,
        "request_id": scope.request_id,
        "prepared_id": prepared_id,
        "idempotency_id": prepared.idempotency_id,
        "idempotency_key_sha256": prepared.idempotency_key_sha256,
        "payload_sha256": _prepared_summary_payload_sha256(
            result.head, summary, source_manifest, prepared,
        ),
        "branch_id": result.branch.branch_id,
        "revision_id": result.revision.revision_id,
        "revision_sha256": result.revision.revision_sha256,
        "history_revision": result.revision.history_revision,
        "history_head_sha256": result.revision.history_head_sha256,
        "branch": _branch_value(result.branch),
        "revision": _revision_value(result.revision),
        "expected_head": _head_value(result.head),
        "summary": summary,
        "summary_sha256": summary_sha256,
        "source_manifest": source_manifest,
        "source_sha256": source_sha256,
        "prepared": asdict(prepared),
    }
    value["prepared_record_sha256"] = _prepared_summary_record_digest(value)
    return value


def _decode_prepared_summary_record(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    raw: str,
    *,
    expected_prepared_id: str,
    current: _RawRollingHistoryV2StoreResult | None = None,
) -> RollingHistoryV2PreparedSummaryRecord | _ConflictResult:
    value = _decode_canonical_object(raw, "rolling history v2 prepared summary")
    required = {
        "schema_version", "kind", "state", "scope_sha256",
        "canonical_namespace_digest", "request_id", "prepared_id",
        "idempotency_id", "idempotency_key_sha256", "payload_sha256",
        "branch_id", "revision_id", "revision_sha256", "history_revision",
        "history_head_sha256", "branch", "revision", "expected_head",
        "summary", "summary_sha256", "source_manifest", "source_sha256",
        "prepared", "prepared_record_sha256",
    }
    if (set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _STORE_SCHEMA_VERSION
            or value["kind"] != "rolling_history_v2_prepared_summary"
            or value["state"] != "PREPARED"
            or value["canonical_namespace_digest"] != identity.canonical_namespace_digest
            or type(value["request_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["request_id"]) is None
            or value["prepared_id"] != expected_prepared_id
            or value["idempotency_id"] != expected_prepared_id
            or type(value["idempotency_key_sha256"]) is not str
            or _HASH.fullmatch(value["idempotency_key_sha256"]) is None
            or value["prepared_id"] != _sha256(_canonical({
                "kind": _SUMMARY_LEDGER_KIND,
                "scope_sha256": value["scope_sha256"],
                "request_id": value["request_id"],
                "idempotency_key_sha256": value["idempotency_key_sha256"],
            }))
            or type(value["payload_sha256"]) is not str
            or _HASH.fullmatch(value["payload_sha256"]) is None
            or type(value["branch_id"]) is not str
            or _HASH.fullmatch(value["branch_id"]) is None
            or type(value["revision_id"]) is not str
            or _HASH.fullmatch(value["revision_id"]) is None
            or type(value["revision_sha256"]) is not str
            or _HASH.fullmatch(value["revision_sha256"]) is None
            or type(value["history_revision"]) is not int
            or value["history_revision"] < 0
            or type(value["history_head_sha256"]) is not str
            or _HASH.fullmatch(value["history_head_sha256"]) is None
            or type(value["summary"]) is not str
            or type(value["summary_sha256"]) is not str
            or _HASH.fullmatch(value["summary_sha256"]) is None
            or type(value["source_manifest"]) is not dict
            or type(value["source_sha256"]) is not str
            or _HASH.fullmatch(value["source_sha256"]) is None
            or type(value["prepared"]) is not dict
            or type(value["prepared_record_sha256"]) is not str
            or _HASH.fullmatch(value["prepared_record_sha256"]) is None):
        raise ValueError("Invalid rolling history v2 prepared-summary binding")

    original_scope = ContextEventScope(
        session_id=scope.session_id,
        request_id=value["request_id"],
        principal_hash=scope.principal_hash,
        session_epoch=scope.session_epoch,
    )
    original_scope_sha256 = _scope_digest(original_scope)
    if value["scope_sha256"] != original_scope_sha256:
        raise ValueError("Rolling history v2 prepared-summary scope mismatch")

    try:
        prepared = PreparedRollingHistoryV2Summary(**value["prepared"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid rolling history v2 prepared-summary descriptor") from exc
    prepared_fields = set(asdict(prepared))
    if set(value["prepared"]) != prepared_fields:
        raise ValueError("Invalid rolling history v2 prepared-summary descriptor")
    head = _decode_head(value["expected_head"])
    revision_raw = _canonical(value["revision"])
    revision = _decode_revision(revision_raw)
    branch_raw = _canonical(value["branch"])
    branch = _decode_branch(branch_raw)
    if (head.branch_id != value["branch_id"]
            or head.revision_id != value["revision_id"]
            or head.revision_sha256 != value["revision_sha256"]
            or head.history_revision != value["history_revision"]
            or head.history_head_sha256 != value["history_head_sha256"]
            or _sha256(value["summary"]) != value["summary_sha256"]
            or _sha256(_canonical(value["source_manifest"])) != value["source_sha256"]
            or value["prepared_record_sha256"] != _prepared_summary_record_digest(value)
            or value["payload_sha256"] != _prepared_summary_payload_sha256(
                head, value["summary"], value["source_manifest"], prepared,
            )):
        raise ValueError("Rolling history v2 prepared-summary digest mismatch")
    _validate_prepared_rolling_history_v2_summary(prepared, revision)
    if (prepared.prepared_id != expected_prepared_id
            or prepared.idempotency_id != value["idempotency_id"]
            or prepared.idempotency_key_sha256 != value["idempotency_key_sha256"]
            or prepared.request_id != original_scope.request_id
            or prepared.summary_sha256 != value["summary_sha256"]):
        raise ValueError("Rolling history v2 prepared-summary descriptor mismatch")

    original = _read_result_for_head(handle, original_scope, namespace, head)
    expected_source = _prepared_summary_source_manifest(
        original_scope, identity.canonical_namespace_digest, original,
    )
    if (value["source_manifest"] != expected_source
            or value["branch"] != _branch_value(original.branch)
            or value["revision"] != _revision_value(original.revision)
            or branch != original.branch
            or revision != original.revision
            or original.branch.branch_id != value["branch_id"]):
        raise ValueError("Rolling history v2 prepared-summary source mismatch")

    if current is not None and current.head != head:
        return _ConflictResult("Rolling history v2 prepared-summary head conflict")

    active_pointer = _read_active_branch_pointer(
        handle, scope, namespace, identity.canonical_namespace_digest,
    )
    if (active_pointer.pointer.state != "active"
            or active_pointer.pointer.branch_id != original.branch.branch_id):
        return _ConflictResult(
            "Rolling history v2 prepared-summary branch is not active",
        )
    if active_pointer.branch is None or active_pointer.head is None:
        raise ValueError("Rolling history v2 active-branch pointer is incomplete")

    active = _read_result_for_head(
        handle, scope, namespace, active_pointer.head.head,
    )
    if (active.branch != active_pointer.branch
            or active.head != active_pointer.head.head
            or active.branch.branch_id != original.branch.branch_id):
        raise ValueError("Rolling history v2 active-branch pointer result mismatch")
    if current is not None and current.head != active.head:
        return _ConflictResult("Rolling history v2 prepared-summary head conflict")

    active_lineage, _ = _read_lineage(
        handle, namespace, active.revision,
    )
    if not active_lineage or active_lineage[-1] != active.revision:
        raise ValueError("Rolling history v2 active head lineage mismatch")
    try:
        original_index = next(
            index for index, item in enumerate(active_lineage)
            if item.revision_id == original.revision.revision_id
        )
    except StopIteration as exc:
        raise ValueError(
            "Rolling history v2 prepared-summary revision is not an active ancestor",
        ) from exc
    if active_lineage[original_index] != original.revision:
        raise ValueError("Rolling history v2 prepared-summary ancestor mismatch")
    for successor in active_lineage[original_index + 1:]:
        if (successor.operation != "append"
                or successor.branch_id != original.branch.branch_id):
            raise ValueError(
                "Rolling history v2 prepared-summary has a non-append descendant",
            )
    if (active.visible_event_locators[:len(original.visible_event_locators)]
            != original.visible_event_locators):
        raise ValueError(
            "Rolling history v2 prepared-summary visible prefix changed",
        )

    return RollingHistoryV2PreparedSummaryRecord(
        schema_version=value["schema_version"],
        state=value["state"],
        canonical_namespace_digest=value["canonical_namespace_digest"],
        payload_sha256=value["payload_sha256"],
        prepared=prepared,
        summary=value["summary"],
        branch=original.branch,
        revision=original.revision,
        expected_head=head,
        source_event_locators=original.visible_event_locators,
        source_manifest_json=_canonical(value["source_manifest"]),
        source_sha256=value["source_sha256"],
        prepared_record_sha256=value["prepared_record_sha256"],
        record_json=raw,
    )


def _persist_new_branch(
    handle: RollingHistorySqlHandle,
    namespace: str,
    result: _RawRollingHistoryV2StoreResult,
) -> None:
    _persist_immutable(
        handle, namespace + "revision:" + result.revision.revision_id,
        _canonical(_revision_value(result.revision)),
    )
    _persist_revision_index_marker(handle, namespace, result.revision)
    _persist_immutable(
        handle, namespace + "branch:" + result.branch.branch_id,
        _canonical(_branch_value(result.branch)),
    )
    _persist_new_head(handle, namespace, result.head)


def _persist_transition(
    handle: RollingHistorySqlHandle,
    namespace: str,
    result: _RawRollingHistoryV2StoreResult,
) -> None:
    _persist_immutable(
        handle, namespace + "revision:" + result.revision.revision_id,
        _canonical(_revision_value(result.revision)),
    )
    _persist_revision_index_marker(handle, namespace, result.revision)
    _persist_head(handle, namespace, result.head)


def _persist_transition_with_head_cas(
    handle: RollingHistorySqlHandle,
    namespace: str,
    expected_head: RollingHistoryV2Head,
    result: _RawRollingHistoryV2StoreResult,
) -> None:
    """Persist a new revision only when its verified persisted parent is current."""
    if (type(expected_head) is not RollingHistoryV2Head
            or expected_head.branch_id != result.branch.branch_id
            or result.revision.branch_id != expected_head.branch_id
            or result.revision.parent_revision_id != expected_head.revision_id
            or result.revision.parent_revision_sha256 != expected_head.revision_sha256
            or result.head.branch_id != expected_head.branch_id):
        raise RollingHistoryV2Conflict("Rolling history head parent CAS mismatch")
    _validate_rolling_history_v2_head_token(expected_head)
    expected_raw = _canonical(_head_value(expected_head))
    head_key = namespace + "head:" + expected_head.branch_id
    if _read_setting(handle, head_key) != expected_raw:
        raise RollingHistoryV2Conflict("Rolling history persisted head changed before append")

    _persist_immutable(
        handle, namespace + "revision:" + result.revision.revision_id,
        _canonical(_revision_value(result.revision)),
    )
    _persist_revision_index_marker(handle, namespace, result.revision)

    next_raw = _canonical(_head_value(result.head))
    updated = handle.execute(
        "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE key = ? AND value = ?",
        (next_raw, head_key, expected_raw),
    )
    if type(updated) is not int or updated != 1:
        raise RollingHistoryV2Conflict("Rolling history persisted head CAS failed")
    if _read_setting(handle, head_key) != next_raw:
        raise RollingHistoryV2Conflict("Rolling history persisted head CAS readback mismatch")


def _persist_revision_index_marker(
    handle: RollingHistorySqlHandle,
    namespace: str,
    revision: RollingHistoryV2Revision,
) -> None:
    if (revision.schema_version != 3 or revision.locator_index is None
            or revision.lineage_index is None):
        return
    marker = {
        "schema_version": _LOCATOR_INDEX_SCHEMA_VERSION,
        "kind": "rolling_history_v2_indexed_revision_marker",
        "revision_id": revision.revision_id,
        "revision_sha256": revision.revision_sha256,
        "locator_index": asdict(revision.locator_index),
        "lineage_index": asdict(revision.lineage_index),
    }
    _persist_immutable(
        handle, namespace + "revision-index:" + revision.revision_id,
        _canonical(marker),
    )


def _persist_immutable(
    handle: RollingHistorySqlHandle,
    key: str,
    value: str,
) -> None:
    existing = _read_setting(handle, key)
    if existing is not None:
        if existing != value:
            raise ValueError("Rolling history v2 immutable record conflict")
        return
    handle.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value),
    )


def _persist_new_head(
    handle: RollingHistorySqlHandle,
    namespace: str,
    head: RollingHistoryV2Head,
) -> None:
    key = namespace + "head:" + head.branch_id
    if _read_setting(handle, key) is not None:
        raise ValueError("Rolling history v2 branch head already exists")
    handle.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, _canonical(_head_value(head))),
    )


def _persist_head(
    handle: RollingHistorySqlHandle,
    namespace: str,
    head: RollingHistoryV2Head,
) -> None:
    handle.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (namespace + "head:" + head.branch_id, _canonical(_head_value(head))),
    )


def _persist_idempotency(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    idempotency_id: str,
    key_sha256: str,
    payload_sha256: str,
    operation: str,
    result: _RawRollingHistoryV2StoreResult,
) -> None:
    value = {
        "schema_version": _STORE_SCHEMA_VERSION,
        "kind": "rolling_revision_idempotency",
        "scope_sha256": _scope_digest(scope),
        "request_id": scope.request_id,
        "idempotency_id": idempotency_id,
        "idempotency_key_sha256": key_sha256,
        "payload_sha256": payload_sha256,
        "operation": operation,
        "branch_id": result.branch.branch_id,
        "revision_id": result.revision.revision_id,
        "revision_sha256": result.revision.revision_sha256,
        "head": _head_value(result.head),
    }
    _persist_immutable(
        handle, namespace + "idempotency:" + idempotency_id,
        _canonical(value),
    )


def _grounded_summary_idempotency_context(
    scope: ContextEventScope,
    idempotency_key: str,
) -> tuple[str, str]:
    if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid rolling history v2 grounded-summary idempotency key")
    key_sha256 = _sha256(idempotency_key)
    prepared_id = _sha256(_canonical({
        "kind": _GROUNDED_SUMMARY_KIND,
        "scope_sha256": _scope_digest(scope),
        "request_id": scope.request_id,
        "idempotency_key_sha256": key_sha256,
    }))
    return prepared_id, key_sha256


def _summary_ledger_idempotency_context(
    scope: ContextEventScope,
    idempotency_key: str,
) -> tuple[str, str]:
    if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)):
        raise ValueError("Invalid rolling history v2 summary-ledger idempotency key")
    key_sha256 = _sha256(idempotency_key)
    prepared_id = _sha256(_canonical({
        "kind": _SUMMARY_LEDGER_KIND,
        "scope_sha256": _scope_digest(scope),
        "request_id": scope.request_id,
        "idempotency_key_sha256": key_sha256,
    }))
    return prepared_id, key_sha256


def _summary_ledger_text(value: object) -> bool:
    if type(value) is not str or not value.strip():
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


def _summary_ledger_locator_from_value(
    raw,
    *,
    allow_v2_v3_locators: bool = False,
) -> RollingHistoryV2PreparedSummaryLedgerLocator:
    # Standalone V1 paths keep the default; only the publication bundle opts
    # into V2/V3 before its exact snapshot-source crosswalk.
    if type(allow_v2_v3_locators) is not bool:
        raise ValueError("Invalid rolling history v2 summary-ledger locator policy")
    if type(raw) is not dict or set(raw) != set(_SUMMARY_LEDGER_LOCATOR_FIELDS):
        raise ValueError("Invalid rolling history v2 summary-ledger locator")
    locator = RollingHistoryV2PreparedSummaryLedgerLocator(**raw)
    if (type(locator.event_id) is not str or _HASH.fullmatch(locator.event_id) is None
            or type(locator.session_id) is not str
            or not 1 <= len(locator.session_id) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in locator.session_id)
            or type(locator.request_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", locator.request_id) is None
            or type(locator.sequence) is not int or locator.sequence < 1
            or type(locator.version) is not int
            or (locator.version not in (1, 2, 3) if allow_v2_v3_locators
                else locator.version != 1)
            or type(locator.content_sha256) is not str
            or _HASH.fullmatch(locator.content_sha256) is None
            or type(locator.chain_sha256) is not str
            or _HASH.fullmatch(locator.chain_sha256) is None):
        raise ValueError("Invalid rolling history v2 summary-ledger locator")
    canonical = json.dumps(
        {field: getattr(locator, field) for field in _SUMMARY_LEDGER_LOCATOR_FIELDS},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if len(canonical.encode("utf-8", errors="strict")) > _SUMMARY_LEDGER_LOCATOR_BYTE_LIMIT:
        raise ValueError("Invalid rolling history v2 summary-ledger locator")
    return locator


def _normalize_summary_ledger_entry(
    *,
    source_sequence,
    source_locator,
    field,
    quote,
    resolution_action,
    evidence_text,
    evidence_locator,
    evidence_sequence,
    evidence_inherited_from_previous_summary,
) -> _SummaryLedgerEntryValue:
    if (type(source_sequence) is not int or source_sequence < 1
            or type(source_locator) is not RollingHistoryV2PreparedSummaryLedgerLocator
            or source_locator.sequence != source_sequence):
        raise ValueError("Invalid rolling history v2 summary-ledger source")
    has_field = field is not None
    has_quote = quote is not None
    has_resolution = resolution_action is not None
    if has_field != has_quote:
        raise ValueError("Invalid rolling history v2 summary-ledger claim")
    if not has_field:
        if (has_resolution or evidence_text is not None or evidence_locator is not None
                or evidence_sequence is not None
                or evidence_inherited_from_previous_summary is not None):
            raise ValueError("Invalid rolling history v2 summary-ledger source-only entry")
        return _SummaryLedgerEntryValue(
            source_sequence, source_locator, None, None, None, None, None, None, None,
        )

    from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS

    if (type(field) is not str or field not in CHECKPOINT_FIELDS or field == "completed"
            or not _summary_ledger_text(quote)):
        raise ValueError("Invalid rolling history v2 summary-ledger claim")
    has_evidence = evidence_text is not None
    if has_resolution != has_evidence:
        raise ValueError("Invalid rolling history v2 summary-ledger resolution")
    if not has_resolution:
        if (evidence_locator is not None or evidence_sequence is not None
                or evidence_inherited_from_previous_summary is not None):
            raise ValueError("Invalid rolling history v2 summary-ledger resolution")
        return _SummaryLedgerEntryValue(
            source_sequence, source_locator, field, quote, None, None, None, None, None,
        )
    if (type(resolution_action) is not str
            or resolution_action not in _SUMMARY_LEDGER_ACTIONS
            or (resolution_action == "complete"
                and field not in ("open_questions", "next_action"))
            or not _summary_ledger_text(evidence_text)
            or type(evidence_locator) is not RollingHistoryV2PreparedSummaryLedgerLocator
            or type(evidence_sequence) is not int
            or evidence_sequence != evidence_locator.sequence
            or evidence_sequence <= source_sequence
            or type(evidence_inherited_from_previous_summary) is not bool
            or evidence_inherited_from_previous_summary):
        raise ValueError("Invalid rolling history v2 summary-ledger resolution")
    return _SummaryLedgerEntryValue(
        source_sequence,
        source_locator,
        field,
        quote,
        resolution_action,
        evidence_text,
        evidence_locator,
        evidence_sequence,
        evidence_inherited_from_previous_summary,
    )


def _summary_ledger_entries_from_payload(
    payload,
    *,
    allow_empty: bool = False,
    allow_v2_v3_locators: bool = False,
) -> tuple[_SummaryLedgerEntryValue, ...]:
    from src.agent.compact.rolling_summary_ledger_bridge import (
        RollingSummaryLedgerBridgePayload,
        RollingSummaryLedgerEntry,
        RollingSummaryLedgerLocator,
        RollingSummaryLedgerResolutionEvidence,
    )

    if (type(payload) is not RollingSummaryLedgerBridgePayload
            or not _summary_ledger_text(payload.host_identity)
            or not _summary_ledger_text(payload.scope_id)
            or type(payload.entries) is not tuple
            or (not allow_empty and not payload.entries)):
        raise ValueError("Invalid rolling history v2 summary-ledger payload")
    entries: list[_SummaryLedgerEntryValue] = []
    for item in payload.entries:
        if type(item) is not RollingSummaryLedgerEntry:
            raise ValueError("Invalid rolling history v2 summary-ledger entry")
        if type(item.source_locator) is not RollingSummaryLedgerLocator:
            raise ValueError("Invalid rolling history v2 summary-ledger source locator")
        source_locator = _summary_ledger_locator_from_value(
            asdict(item.source_locator),
            allow_v2_v3_locators=allow_v2_v3_locators,
        )
        resolution = item.resolution
        if resolution is None:
            resolution_action = None
            evidence_text = None
            evidence_locator = None
            evidence_sequence = None
            evidence_inherited_from_previous_summary = None
        else:
            if type(resolution) is not RollingSummaryLedgerResolutionEvidence:
                raise ValueError("Invalid rolling history v2 summary-ledger resolution")
            if type(resolution.evidence_locator) is not RollingSummaryLedgerLocator:
                raise ValueError("Invalid rolling history v2 summary-ledger evidence locator")
            resolution_action = resolution.action
            evidence_text = resolution.evidence_text
            evidence_locator = _summary_ledger_locator_from_value(
                asdict(resolution.evidence_locator),
                allow_v2_v3_locators=allow_v2_v3_locators,
            )
            evidence_sequence = resolution.evidence_sequence
            evidence_inherited_from_previous_summary = (
                resolution.inherited_from_previous_summary
            )
        entries.append(_normalize_summary_ledger_entry(
            source_sequence=item.source_sequence,
            source_locator=source_locator,
            field=item.field,
            quote=item.quote,
            resolution_action=resolution_action,
            evidence_text=evidence_text,
            evidence_locator=evidence_locator,
            evidence_sequence=evidence_sequence,
            evidence_inherited_from_previous_summary=(
                evidence_inherited_from_previous_summary
            ),
        ))
    return tuple(entries)


def _summary_ledger_entry_value(entry: _SummaryLedgerEntryValue) -> dict:
    resolution = None
    if entry.resolution_action is not None:
        resolution = {
            "action": entry.resolution_action,
            "evidence_text": entry.evidence_text,
            "evidence_locator": asdict(entry.evidence_locator),
            "evidence_sequence": entry.evidence_sequence,
            "inherited_from_previous_summary": (
                entry.evidence_inherited_from_previous_summary
            ),
        }
    return {
        "source_sequence": entry.source_sequence,
        "source_locator": asdict(entry.source_locator),
        "field": entry.field,
        "quote": entry.quote,
        "resolution": resolution,
    }


def _rolling_summary_claim_to_value(
    text: str,
    sequence: int,
    reference_json: str,
    inherited_from_previous_summary: bool,
    covered_through: int,
    *,
    allow_v2_v3_locators: bool = False,
) -> dict:
    if (type(text) is not str or not text.strip()
            or type(sequence) is not int
            or not 1 <= sequence <= covered_through
            or type(reference_json) is not str
            or type(inherited_from_previous_summary) is not bool):
        raise ValueError("Invalid rolling history v2 grounded-summary claim")
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("Rolling history v2 grounded-summary quote is invalid") from exc
    reference = _decode_rolling_summary_reference_json(
        reference_json, allow_v2_v3_locators=allow_v2_v3_locators,
    )
    if reference["sequence"] != sequence:
        raise ValueError("Rolling history v2 grounded-summary citation mismatch")
    return {
        "text": text,
        "sequence": sequence,
        "reference_json": reference_json,
        "inherited_from_previous_summary": inherited_from_previous_summary,
    }


def _grounded_summary_claim_from_value(
    raw: dict,
    covered_through: int,
    *,
    allow_v2_v3_locators: bool = False,
):
    from src.agent.compact.rolling_summarizer import GroundedClaim, RollingCitation

    required = {
        "text", "sequence", "reference_json",
        "inherited_from_previous_summary",
    }
    if type(raw) is not dict or set(raw) != required:
        raise ValueError("Invalid rolling history v2 grounded-summary claim")
    normalized = _rolling_summary_claim_to_value(
        raw["text"], raw["sequence"], raw["reference_json"],
        raw["inherited_from_previous_summary"], covered_through,
        allow_v2_v3_locators=allow_v2_v3_locators,
    )
    citation = RollingCitation(
        normalized["sequence"],
        normalized["reference_json"],
        normalized["inherited_from_previous_summary"],
    )
    return GroundedClaim(normalized["text"], citation), normalized


def _grounded_summary_claim_identity(claim_value: dict) -> tuple[str, int, str]:
    return (
        claim_value["text"],
        claim_value["sequence"],
        claim_value["reference_json"],
    )


def _validate_grounded_summary_resolution_semantics(
    fields: dict[str, list[dict]],
    resolution_records: list[dict],
) -> None:
    for record in resolution_records:
        field = record["field"]
        target_field = field if record["action"] == "replace" else "current_state"
        prior_identity = _grounded_summary_claim_identity(record["prior_claim"])
        evidence_identity = _grounded_summary_claim_identity(record["evidence_claim"])
        if any(
            _grounded_summary_claim_identity(claim) == prior_identity
            for claim in fields[field]
        ):
            raise ValueError("Rolling history v2 resolved claim remains in its field")
        if sum(
            _grounded_summary_claim_identity(claim) == evidence_identity
            for claim in fields[target_field]
        ) != 1:
            raise ValueError("Rolling history v2 resolution evidence has the wrong target field")


def _rolling_summary_to_value(
    summary: "RollingSummary",
    recent_message_start_boundary: int,
    *,
    allow_v2_v3_locators: bool = False,
) -> dict:
    from src.agent.compact.rolling_summarizer import (
        GroundedClaim,
        RollingCitation,
        RollingResolutionRecord,
        RollingSummary,
    )
    from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS

    if (type(summary) is not RollingSummary
            or type(summary.covered_through) is not int
            or summary.covered_through < 0
            or type(recent_message_start_boundary) is not int
            or recent_message_start_boundary != summary.covered_through + 1
            or not isinstance(summary.fields, Mapping)
            or set(summary.fields) != set(CHECKPOINT_FIELDS)
            or type(summary.resolution_records) is not tuple):
        raise ValueError("Invalid rolling history v2 grounded-summary boundaries or shape")

    fields: dict[str, list[dict]] = {}
    for field in CHECKPOINT_FIELDS:
        claims = summary.fields[field]
        if type(claims) not in (tuple, list):
            raise ValueError("Invalid rolling history v2 grounded-summary claims")
        field_claims: list[dict] = []
        for claim in claims:
            if (type(claim) is not GroundedClaim
                    or type(claim.citation) is not RollingCitation):
                raise ValueError("Invalid rolling history v2 grounded-summary claim")
            field_claims.append(_rolling_summary_claim_to_value(
                claim.text,
                claim.citation.sequence,
                claim.citation.reference_json,
                claim.citation.inherited_from_previous_summary,
                summary.covered_through,
                allow_v2_v3_locators=allow_v2_v3_locators,
            ))
        if field == "completed" and field_claims:
            raise ValueError("Rolling history v2 summary cannot claim completion")
        fields[field] = field_claims

    resolution_records: list[dict] = []
    seen_resolutions: set[tuple] = set()
    for record in summary.resolution_records:
        if (type(record) is not RollingResolutionRecord
                or type(record.field) is not str
                or record.field not in CHECKPOINT_FIELDS
                or record.field == "completed"
                or type(record.action) is not str
                or record.action not in ("replace", "complete", "revoke")
                or (record.action == "complete"
                    and record.field not in ("open_questions", "next_action"))
                or type(record.evidence_role) is not str
                or record.evidence_role != "human"
                or type(record.prior_claim) is not GroundedClaim
                or type(record.prior_claim.citation) is not RollingCitation
                or type(record.evidence_claim) is not GroundedClaim
                or type(record.evidence_claim.citation) is not RollingCitation):
            raise ValueError("Invalid rolling history v2 grounded-summary resolution record")
        prior_claim = _rolling_summary_claim_to_value(
            record.prior_claim.text,
            record.prior_claim.citation.sequence,
            record.prior_claim.citation.reference_json,
            record.prior_claim.citation.inherited_from_previous_summary,
            summary.covered_through,
            allow_v2_v3_locators=allow_v2_v3_locators,
        )
        evidence_claim = _rolling_summary_claim_to_value(
            record.evidence_claim.text,
            record.evidence_claim.citation.sequence,
            record.evidence_claim.citation.reference_json,
            record.evidence_claim.citation.inherited_from_previous_summary,
            summary.covered_through,
            allow_v2_v3_locators=allow_v2_v3_locators,
        )
        if (evidence_claim["sequence"] <= prior_claim["sequence"]
                or evidence_claim["inherited_from_previous_summary"]):
            raise ValueError("Rolling history v2 resolution evidence is not later source text")
        identity = (
            record.field,
            record.action,
            _grounded_summary_claim_identity(prior_claim),
            _grounded_summary_claim_identity(evidence_claim),
        )
        if identity in seen_resolutions:
            raise ValueError("Duplicate rolling history v2 grounded-summary resolution record")
        seen_resolutions.add(identity)
        resolution_records.append({
            "field": record.field,
            "action": record.action,
            "prior_claim": prior_claim,
            "evidence_claim": evidence_claim,
            "evidence_role": record.evidence_role,
        })

    _validate_grounded_summary_resolution_semantics(fields, resolution_records)
    return {
        "schema_version": _GROUNDED_SUMMARY_SCHEMA_VERSION,
        "covered_through": summary.covered_through,
        "fields": fields,
        "resolution_records": resolution_records,
    }


def _unique_rolling_summary_json_object(pairs):
    result = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("Duplicate rolling-history summary reference field")
        result[key] = value
    return result


def _decode_rolling_summary_reference_json(
    raw: str,
    *,
    allow_v2_v3_locators: bool = False,
) -> dict:
    # Standalone V1 paths keep the default; only the publication bundle opts
    # into V2/V3 before its exact snapshot-source crosswalk.
    if type(allow_v2_v3_locators) is not bool:
        raise ValueError("Invalid rolling-history summary reference policy")
    if type(raw) is not str:
        raise ValueError("Invalid rolling-history summary reference")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_rolling_summary_json_object)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid rolling-history summary reference") from exc
    if (type(value) is not dict
            or set(value) != set(_GROUNDED_SUMMARY_REFERENCE_FIELDS)):
        raise ValueError("Invalid rolling-history summary reference")
    if (type(value["event_id"]) is not str
            or _HASH.fullmatch(value["event_id"]) is None
            or type(value["session_id"]) is not str
            or not 1 <= len(value["session_id"]) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in value["session_id"])
            or type(value["request_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["request_id"]) is None
            or type(value["sequence"]) is not int
            or value["sequence"] < 1
            or type(value["version"]) is not int
            or (value["version"] not in (1, 2, 3) if allow_v2_v3_locators
                else value["version"] != 1)
            or type(value["content_sha256"]) is not str
            or _HASH.fullmatch(value["content_sha256"]) is None
            or type(value["chain_sha256"]) is not str
            or _HASH.fullmatch(value["chain_sha256"]) is None):
        raise ValueError("Invalid rolling-history summary reference")
    canonical = json.dumps(
        {field: value[field] for field in _GROUNDED_SUMMARY_REFERENCE_FIELDS},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if (canonical != raw
            or len(canonical.encode("utf-8", errors="strict")) > 1024):
        raise ValueError("Noncanonical rolling-history summary reference")
    return value


def _read_visible_rolling_message_sources(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    locators: tuple[RollingHistoryV2EventLocator, ...],
) -> tuple[dict[int, str], dict[int, str]]:
    from app.api.rolling_history_codec import (
        RollingHistoryCodecError,
        decode_rolling_history_events,
    )

    reader = _SettingsReader(handle)
    prefix = _scope_prefix(scope)
    events = []
    for expected_sequence, locator in enumerate(locators, start=1):
        if (type(locator) is not RollingHistoryV2EventLocator
                or locator.sequence != expected_sequence):
            raise ValueError("Rolling history v2 visible source sequence is invalid")
        event = _load_event_at_sequence(
            reader, scope, prefix, locator.sequence, verify_parent=True,
        )
        if event is None or _locator_from_event(event) != locator:
            raise ValueError("Rolling history v2 source locator mismatch")
        events.append(event)
    if not events:
        return {}, {}
    try:
        messages = decode_rolling_history_events(tuple(events))
    except RollingHistoryCodecError as exc:
        raise ValueError(
            "Rolling history v2 source history cannot be decoded as complete text",
        ) from exc
    if len(messages) != len(events):
        raise ValueError("Rolling history v2 source message count mismatch")
    bodies: dict[int, str] = {}
    roles: dict[int, str] = {}
    source_roles = {
        "user_text": "human",
        "assistant_text": "assistant",
        "tool_call": "assistant",
        "tool_result": "tool",
    }
    for item, event in zip(messages, events, strict=True):
        body = item.message.content
        if (type(item.sequence) is not int
                or type(body) is not str
                or item.sequence in bodies
                or item.sequence in roles
                or item.sequence != event.reference.sequence):
            raise ValueError("Rolling history v2 source message is invalid")
        role = source_roles.get(event.event_type)
        if role is None:
            raise ValueError("Rolling history v2 source message role is invalid")
        try:
            body.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("Rolling history v2 source message is invalid") from exc
        bodies[item.sequence] = body
        roles[item.sequence] = role
    return bodies, roles


def _summary_ledger_visible_locator(
    locator: RollingHistoryV2EventLocator,
    scope: ContextEventScope,
) -> RollingHistoryV2PreparedSummaryLedgerLocator:
    return RollingHistoryV2PreparedSummaryLedgerLocator(
        event_id=locator.event_id,
        session_id=scope.session_id,
        request_id=locator.request_id,
        sequence=locator.sequence,
        version=locator.version,
        content_sha256=locator.content_sha256,
        chain_sha256=locator.chain_sha256,
    )


def _summary_ledger_source_manifest(
    scope: ContextEventScope,
    canonical_namespace_digest: str,
    result: _RawRollingHistoryV2StoreResult,
    entries: tuple[_SummaryLedgerEntryValue, ...],
    body_by_sequence: dict[int, str],
    role_by_sequence: dict[int, str],
) -> dict:
    visible_by_sequence: dict[int, RollingHistoryV2EventLocator] = {}
    for expected_sequence, locator in enumerate(
        result.visible_event_locators, start=1,
    ):
        if (type(locator) is not RollingHistoryV2EventLocator
                or locator.sequence != expected_sequence):
            raise ValueError("Rolling history v2 summary-ledger visible sequence is invalid")
        visible_by_sequence[locator.sequence] = locator

    source_values: dict[int, dict] = {}

    def bind_source(
        locator: RollingHistoryV2PreparedSummaryLedgerLocator,
        sequence: int,
    ) -> tuple[RollingHistoryV2PreparedSummaryLedgerLocator, str, str]:
        visible = visible_by_sequence.get(sequence)
        if visible is None:
            raise ValueError(
                "Rolling history v2 summary-ledger source is outside the active revision",
            )
        expected_locator = _summary_ledger_visible_locator(visible, scope)
        if locator != expected_locator:
            raise ValueError("Rolling history v2 summary-ledger source locator mismatch")
        body = body_by_sequence.get(sequence)
        role = role_by_sequence.get(sequence)
        if (type(body) is not str or type(role) is not str
                or role not in ("human", "assistant", "tool")):
            raise ValueError("Rolling history v2 summary-ledger source is unavailable")
        body_sha256 = _sha256(body)
        source_values[sequence] = {
            "locator": asdict(expected_locator),
            "role": role,
            "body": body,
            "body_sha256": body_sha256,
        }
        return expected_locator, body, role

    manifest_entries: list[dict] = []
    for entry in entries:
        source_locator, source_body, source_role = bind_source(
            entry.source_locator, entry.source_sequence,
        )
        if entry.field is None:
            if entry.quote is not None or entry.resolution_action is not None:
                raise ValueError("Rolling history v2 summary-ledger source-only entry mismatch")
        elif (type(entry.quote) is not str or entry.quote not in source_body):
            raise ValueError("Rolling history v2 summary-ledger quote does not match its source")

        entry_value = _summary_ledger_entry_value(entry)
        entry_value["source_locator"] = asdict(source_locator)
        entry_value["source_role"] = source_role
        if entry.resolution_action is not None:
            evidence_locator, evidence_body, evidence_role = bind_source(
                entry.evidence_locator, entry.evidence_sequence,
            )
            if (entry.evidence_sequence <= entry.source_sequence
                    or entry.evidence_inherited_from_previous_summary
                    or evidence_role != "human"
                    or entry.evidence_text not in evidence_body):
                raise ValueError(
                    "Rolling history v2 summary-ledger resolution evidence is invalid",
                )
            entry_value["resolution"]["evidence_locator"] = asdict(evidence_locator)
            entry_value["resolution"]["evidence_source_role"] = evidence_role
        manifest_entries.append(entry_value)

    return {
        "schema_version": _SUMMARY_LEDGER_SCHEMA_VERSION,
        "kind": _SUMMARY_LEDGER_SOURCE_KIND,
        "scope_sha256": _scope_digest(scope),
        "canonical_namespace_digest": canonical_namespace_digest,
        "request_id": scope.request_id,
        "branch": _branch_value(result.branch),
        "revision": _revision_value(result.revision),
        "expected_head": _head_value(result.head),
        "visible_event_count": len(result.visible_event_locators),
        "entries": manifest_entries,
        "sources": [source_values[index] for index in sorted(source_values)],
    }


def _summary_ledger_payload_sha256(
    result: _RawRollingHistoryV2StoreResult,
    prepared_id: str,
    idempotency_key_sha256: str,
    source_manifest: dict,
) -> str:
    return _sha256(_canonical({
        "operation": "prepare_summary_ledger_v1",
        "expected_head": _head_value(result.head),
        "prepared_id": prepared_id,
        "idempotency_key_sha256": idempotency_key_sha256,
        "source_manifest": source_manifest,
    }))


def _summary_ledger_record_digest(value: dict) -> str:
    fields = {
        key: item for key, item in value.items()
        if key != "prepared_record_sha256"
    }
    return _sha256(_canonical(fields))


def _summary_ledger_record_value(
    scope: ContextEventScope,
    canonical_namespace_digest: str,
    result: _RawRollingHistoryV2StoreResult,
    source_manifest: dict,
    prepared_id: str,
    key_sha256: str,
) -> dict:
    head = result.head
    value = {
        "schema_version": _SUMMARY_LEDGER_SCHEMA_VERSION,
        "kind": _SUMMARY_LEDGER_KIND,
        "state": "PREPARED",
        "scope_sha256": _scope_digest(scope),
        "canonical_namespace_digest": canonical_namespace_digest,
        "request_id": scope.request_id,
        "prepared_id": prepared_id,
        "idempotency_id": prepared_id,
        "idempotency_key_sha256": key_sha256,
        "payload_sha256": _summary_ledger_payload_sha256(
            result, prepared_id, key_sha256, source_manifest,
        ),
        "branch_id": result.branch.branch_id,
        "revision_id": result.revision.revision_id,
        "revision_sha256": result.revision.revision_sha256,
        "history_revision": head.history_revision,
        "history_head_sha256": head.history_head_sha256,
        "branch": _branch_value(result.branch),
        "revision": _revision_value(result.revision),
        "expected_head": _head_value(head),
        "source_manifest": source_manifest,
        "source_sha256": _sha256(_canonical(source_manifest)),
    }
    value["prepared_record_sha256"] = _summary_ledger_record_digest(value)
    return value


def _grounded_summary_source_manifest(
    scope: ContextEventScope,
    canonical_namespace_digest: str,
    result: _RawRollingHistoryV2StoreResult,
    summary_value: dict,
    recent_message_start_boundary: int,
    body_by_sequence: dict[int, str],
    role_by_sequence: dict[int, str],
    *,
    allow_v2_v3_locators: bool = False,
) -> dict:
    covered_through = summary_value["covered_through"]
    visible_by_sequence: dict[int, RollingHistoryV2EventLocator] = {}
    for expected_sequence, locator in enumerate(
        result.visible_event_locators, start=1,
    ):
        if (type(locator) is not RollingHistoryV2EventLocator
                or locator.sequence != expected_sequence):
            raise ValueError("Rolling history v2 visible source sequence is invalid")
        visible_by_sequence[locator.sequence] = locator
    if (covered_through > len(result.visible_event_locators)
            or recent_message_start_boundary != covered_through + 1):
        raise ValueError("Rolling history v2 grounded-summary boundary exceeds its source")

    source_bodies: dict[RollingHistoryV2EventLocator, tuple[str, str]] = {}
    claim_sources: list[dict] = []
    resolution_sources: list[dict] = []
    fields = summary_value["fields"]
    from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS

    _validate_grounded_summary_resolution_semantics(
        fields, summary_value["resolution_records"],
    )

    def bind_claim_source(claim: dict):
        reference = _decode_rolling_summary_reference_json(
            claim["reference_json"],
            allow_v2_v3_locators=allow_v2_v3_locators,
        )
        sequence = claim["sequence"]
        locator = visible_by_sequence.get(sequence)
        if locator is None:
            raise ValueError(
                "Rolling history v2 summary citation is outside the active revision",
            )
        expected_reference = {
            "event_id": locator.event_id,
            "session_id": scope.session_id,
            "request_id": locator.request_id,
            "sequence": locator.sequence,
            "version": locator.version,
            "content_sha256": locator.content_sha256,
            "chain_sha256": locator.chain_sha256,
        }
        if reference != expected_reference:
            raise ValueError(
                "Rolling history v2 summary citation does not match its source locator",
            )
        body = body_by_sequence.get(sequence)
        role = role_by_sequence.get(sequence)
        if (type(body) is not str or claim["text"] not in body
                or type(role) is not str or role not in ("human", "assistant", "tool")):
            raise ValueError(
                "Rolling history v2 summary quote does not match its source message",
            )
        body_sha256 = _sha256(body)
        prior_body = source_bodies.get(locator)
        if prior_body is not None and prior_body != (body, body_sha256):
            raise ValueError("Rolling history v2 source body changed within one read")
        source_bodies[locator] = (body, body_sha256)
        return locator, role

    for field in CHECKPOINT_FIELDS:
        for claim_index, claim in enumerate(fields[field]):
            locator, _ = bind_claim_source(claim)
            body_sha256 = source_bodies[locator][1]
            claim_sources.append({
                "field": field,
                "claim_index": claim_index,
                "locator": asdict(locator),
                "quote": claim["text"],
                "body_sha256": body_sha256,
            })

    for record in summary_value["resolution_records"]:
        prior_claim = record["prior_claim"]
        evidence_claim = record["evidence_claim"]
        if (evidence_claim["sequence"] <= prior_claim["sequence"]
                or evidence_claim["inherited_from_previous_summary"]):
            raise ValueError("Rolling history v2 resolution evidence is not later source text")
        prior_locator, prior_role = bind_claim_source(prior_claim)
        evidence_locator, evidence_role = bind_claim_source(evidence_claim)
        if (record["evidence_role"] != "human"
                or evidence_role != "human"):
            raise ValueError(
                "Rolling history v2 resolution evidence is not from a human message",
            )
        resolution_sources.append({
            "field": record["field"],
            "action": record["action"],
            "evidence_role": record["evidence_role"],
            "prior_source": {
                "locator": asdict(prior_locator),
                "quote": prior_claim["text"],
                "role": prior_role,
            },
            "evidence_source": {
                "locator": asdict(evidence_locator),
                "quote": evidence_claim["text"],
                "role": evidence_role,
            },
        })

    source_values = [
        {
            "locator": asdict(locator),
            "body": body,
            "body_sha256": body_sha256,
        }
        for locator, (body, body_sha256) in sorted(
            source_bodies.items(), key=lambda item: item[0].sequence,
        )
    ]
    manifest = {
        "schema_version": _GROUNDED_SUMMARY_SCHEMA_VERSION,
        "kind": _GROUNDED_SUMMARY_SOURCE_KIND,
        "scope_sha256": _scope_digest(scope),
        "canonical_namespace_digest": canonical_namespace_digest,
        "request_id": scope.request_id,
        "branch": _branch_value(result.branch),
        "revision": _revision_value(result.revision),
        "expected_head": _head_value(result.head),
        "covered_through": covered_through,
        "recent_message_start_boundary": recent_message_start_boundary,
        "sources": source_values,
        "claim_sources": claim_sources,
    }
    if resolution_sources:
        manifest["resolution_sources"] = resolution_sources
    return manifest


def _grounded_summary_payload_sha256(
    expected_head: RollingHistoryV2Head,
    prepared_id: str,
    idempotency_key_sha256: str,
    summary_value: dict,
    recent_message_start_boundary: int,
    source_manifest: dict,
) -> str:
    return _sha256(_canonical({
        "operation": "prepare_grounded_summary_v1",
        "expected_head": _head_value(expected_head),
        "prepared_id": prepared_id,
        "idempotency_key_sha256": idempotency_key_sha256,
        "summary": summary_value,
        "recent_message_start_boundary": recent_message_start_boundary,
        "source_manifest": source_manifest,
    }))


def _grounded_summary_record_digest(value: dict) -> str:
    fields = {
        key: item for key, item in value.items()
        if key != "prepared_record_sha256"
    }
    return _sha256(_canonical(fields))


def _grounded_summary_record_value(
    scope: ContextEventScope,
    canonical_namespace_digest: str,
    result: _RawRollingHistoryV2StoreResult,
    summary_value: dict,
    recent_message_start_boundary: int,
    source_manifest: dict,
    prepared_id: str,
    key_sha256: str,
) -> dict:
    summary_sha256 = _sha256(_canonical(summary_value))
    value = {
        "schema_version": _GROUNDED_SUMMARY_SCHEMA_VERSION,
        "kind": _GROUNDED_SUMMARY_KIND,
        "state": "PREPARED",
        "scope_sha256": _scope_digest(scope),
        "canonical_namespace_digest": canonical_namespace_digest,
        "request_id": scope.request_id,
        "prepared_id": prepared_id,
        "idempotency_id": prepared_id,
        "idempotency_key_sha256": key_sha256,
        "payload_sha256": _grounded_summary_payload_sha256(
            result.head, prepared_id, key_sha256, summary_value,
            recent_message_start_boundary, source_manifest,
        ),
        "branch_id": result.branch.branch_id,
        "revision_id": result.revision.revision_id,
        "revision_sha256": result.revision.revision_sha256,
        "history_revision": result.revision.history_revision,
        "history_head_sha256": result.revision.history_head_sha256,
        "branch": _branch_value(result.branch),
        "revision": _revision_value(result.revision),
        "expected_head": _head_value(result.head),
        "summary": summary_value,
        "summary_sha256": summary_sha256,
        "recent_message_start_boundary": recent_message_start_boundary,
        "source_manifest": source_manifest,
        "source_sha256": _sha256(_canonical(source_manifest)),
    }
    value["prepared_record_sha256"] = _grounded_summary_record_digest(value)
    return value


def _decode_grounded_summary_value(
    value: dict,
    *,
    allow_v2_v3_locators: bool = False,
):
    from types import MappingProxyType
    from src.agent.compact.rolling_summarizer import (
        RollingResolutionRecord,
        RollingSummary,
    )
    from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS

    required = {
        "schema_version", "covered_through", "fields", "resolution_records",
    }
    if (type(value) is not dict or set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _GROUNDED_SUMMARY_SCHEMA_VERSION
            or type(value["covered_through"]) is not int
            or value["covered_through"] < 0
            or type(value["fields"]) is not dict
            or set(value["fields"]) != set(CHECKPOINT_FIELDS)
            or type(value["resolution_records"]) is not list):
        raise ValueError("Invalid rolling history v2 grounded-summary value")

    fields: dict[str, tuple] = {}
    normalized_fields: dict[str, list[dict]] = {}
    for field in CHECKPOINT_FIELDS:
        raw_claims = value["fields"][field]
        if type(raw_claims) is not list:
            raise ValueError("Invalid rolling history v2 grounded-summary claims")
        claims = []
        normalized_claims = []
        for raw_claim in raw_claims:
            claim, normalized_claim = _grounded_summary_claim_from_value(
                raw_claim, value["covered_through"],
                allow_v2_v3_locators=allow_v2_v3_locators,
            )
            claims.append(claim)
            normalized_claims.append(normalized_claim)
        if field == "completed" and claims:
            raise ValueError("Rolling history v2 summary cannot claim completion")
        fields[field] = tuple(claims)
        normalized_fields[field] = normalized_claims

    resolution_records = []
    normalized_resolution_records = []
    seen_resolutions: set[tuple] = set()
    required_resolution = {
        "field", "action", "prior_claim", "evidence_claim", "evidence_role",
    }
    for raw_record in value["resolution_records"]:
        if (type(raw_record) is not dict
                or set(raw_record) != required_resolution
                or type(raw_record["field"]) is not str
                or raw_record["field"] not in CHECKPOINT_FIELDS
                or raw_record["field"] == "completed"
                or type(raw_record["action"]) is not str
                or raw_record["action"] not in ("replace", "complete", "revoke")
                or (raw_record["action"] == "complete"
                    and raw_record["field"] not in ("open_questions", "next_action"))
                or type(raw_record["evidence_role"]) is not str
                or raw_record["evidence_role"] != "human"):
            raise ValueError("Invalid rolling history v2 grounded-summary resolution record")
        prior_claim, normalized_prior_claim = _grounded_summary_claim_from_value(
            raw_record["prior_claim"], value["covered_through"],
            allow_v2_v3_locators=allow_v2_v3_locators,
        )
        evidence_claim, normalized_evidence_claim = _grounded_summary_claim_from_value(
            raw_record["evidence_claim"], value["covered_through"],
            allow_v2_v3_locators=allow_v2_v3_locators,
        )
        if (evidence_claim.citation.sequence <= prior_claim.citation.sequence
                or evidence_claim.citation.inherited_from_previous_summary):
            raise ValueError("Rolling history v2 resolution evidence is not later source text")
        record = RollingResolutionRecord(
            field=raw_record["field"],
            action=raw_record["action"],
            prior_claim=prior_claim,
            evidence_claim=evidence_claim,
            evidence_role=raw_record["evidence_role"],
        )
        identity = (
            record.field,
            record.action,
            _grounded_summary_claim_identity(normalized_prior_claim),
            _grounded_summary_claim_identity(normalized_evidence_claim),
        )
        if identity in seen_resolutions:
            raise ValueError("Duplicate rolling history v2 grounded-summary resolution record")
        seen_resolutions.add(identity)
        resolution_records.append(record)
        normalized_resolution_records.append({
            "field": record.field,
            "action": record.action,
            "prior_claim": normalized_prior_claim,
            "evidence_claim": normalized_evidence_claim,
            "evidence_role": record.evidence_role,
        })

    _validate_grounded_summary_resolution_semantics(
        normalized_fields, normalized_resolution_records,
    )
    normalized_value = {
        "schema_version": _GROUNDED_SUMMARY_SCHEMA_VERSION,
        "covered_through": value["covered_through"],
        "fields": normalized_fields,
        "resolution_records": normalized_resolution_records,
    }
    if normalized_value != value:
        raise ValueError("Noncanonical rolling history v2 grounded-summary value")
    summary = RollingSummary(
        covered_through=value["covered_through"],
        fields=MappingProxyType(fields),
        resolution_records=tuple(resolution_records),
    )
    return summary, normalized_value


def _grounded_summary_locator_from_value(raw) -> RollingHistoryV2EventLocator:
    required = {
        "event_id", "request_id", "sequence", "version",
        "content_sha256", "chain_sha256",
    }
    if (type(raw) is not dict or set(raw) != required
            or type(raw["event_id"]) is not str
            or _HASH.fullmatch(raw["event_id"]) is None
            or type(raw["request_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", raw["request_id"]) is None
            or type(raw["sequence"]) is not int or raw["sequence"] < 1
            or type(raw["version"]) is not int or raw["version"] != 1
            or type(raw["content_sha256"]) is not str
            or _HASH.fullmatch(raw["content_sha256"]) is None
            or type(raw["chain_sha256"]) is not str
            or _HASH.fullmatch(raw["chain_sha256"]) is None):
        raise ValueError("Invalid rolling history v2 grounded-summary source locator")
    return RollingHistoryV2EventLocator(**raw)


def _decode_grounded_summary_record(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    raw: str,
    *,
    expected_prepared_id: str,
    current: _RawRollingHistoryV2StoreResult | None = None,
) -> RollingHistoryV2PreparedGroundedSummaryRecord | _ConflictResult:
    value = _decode_canonical_object(raw, "rolling history v2 grounded summary")
    required = {
        "schema_version", "kind", "state", "scope_sha256",
        "canonical_namespace_digest", "request_id", "prepared_id",
        "idempotency_id", "idempotency_key_sha256", "payload_sha256",
        "branch_id", "revision_id", "revision_sha256", "history_revision",
        "history_head_sha256", "branch", "revision", "expected_head",
        "summary", "summary_sha256", "recent_message_start_boundary",
        "source_manifest", "source_sha256", "prepared_record_sha256",
    }
    if (set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _GROUNDED_SUMMARY_SCHEMA_VERSION
            or value["kind"] != _GROUNDED_SUMMARY_KIND
            or value["state"] != "PREPARED"
            or value["scope_sha256"] != _scope_digest(scope)
            or value["canonical_namespace_digest"] != identity.canonical_namespace_digest
            or type(value["request_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["request_id"]) is None
            or type(value["prepared_id"]) is not str
            or _HASH.fullmatch(value["prepared_id"]) is None
            or value["prepared_id"] != expected_prepared_id
            or value["idempotency_id"] != expected_prepared_id
            or type(value["idempotency_key_sha256"]) is not str
            or _HASH.fullmatch(value["idempotency_key_sha256"]) is None
            or type(value["payload_sha256"]) is not str
            or _HASH.fullmatch(value["payload_sha256"]) is None
            or type(value["branch_id"]) is not str
            or _HASH.fullmatch(value["branch_id"]) is None
            or type(value["revision_id"]) is not str
            or _HASH.fullmatch(value["revision_id"]) is None
            or type(value["revision_sha256"]) is not str
            or _HASH.fullmatch(value["revision_sha256"]) is None
            or type(value["history_revision"]) is not int
            or value["history_revision"] < 0
            or type(value["history_head_sha256"]) is not str
            or _HASH.fullmatch(value["history_head_sha256"]) is None
            or type(value["summary"]) is not dict
            or type(value["summary_sha256"]) is not str
            or _HASH.fullmatch(value["summary_sha256"]) is None
            or type(value["recent_message_start_boundary"]) is not int
            or value["recent_message_start_boundary"] < 1
            or type(value["source_manifest"]) is not dict
            or type(value["source_sha256"]) is not str
            or _HASH.fullmatch(value["source_sha256"]) is None
            or type(value["prepared_record_sha256"]) is not str
            or _HASH.fullmatch(value["prepared_record_sha256"]) is None):
        raise ValueError("Invalid rolling history v2 grounded-summary binding")

    original_scope = ContextEventScope(
        session_id=scope.session_id,
        request_id=value["request_id"],
        principal_hash=scope.principal_hash,
        session_epoch=scope.session_epoch,
    )
    if value["scope_sha256"] != _scope_digest(original_scope):
        raise ValueError("Rolling history v2 grounded-summary scope mismatch")
    summary, summary_value = _decode_grounded_summary_value(value["summary"])
    recent_message_start_boundary = value["recent_message_start_boundary"]
    head = _decode_head(value["expected_head"])
    revision = _decode_revision(_canonical(value["revision"]))
    branch = _decode_branch(_canonical(value["branch"]))
    if (head.branch_id != value["branch_id"]
            or head.revision_id != value["revision_id"]
            or head.revision_sha256 != value["revision_sha256"]
            or head.history_revision != value["history_revision"]
            or head.history_head_sha256 != value["history_head_sha256"]
            or summary.covered_through + 1 != recent_message_start_boundary
            or _sha256(_canonical(summary_value)) != value["summary_sha256"]
            or _sha256(_canonical(value["source_manifest"])) != value["source_sha256"]
            or value["payload_sha256"] != _grounded_summary_payload_sha256(
                head, expected_prepared_id, value["idempotency_key_sha256"],
                summary_value, recent_message_start_boundary,
                value["source_manifest"],
            )
            or value["prepared_record_sha256"] != _grounded_summary_record_digest(value)):
        raise ValueError("Rolling history v2 grounded-summary digest mismatch")

    original = _read_result_for_head(handle, original_scope, namespace, head)
    if (branch != original.branch
            or revision != original.revision
            or value["branch"] != _branch_value(original.branch)
            or value["revision"] != _revision_value(original.revision)
            or value["branch_id"] != original.branch.branch_id
            or value["revision_id"] != original.revision.revision_id):
        raise ValueError("Rolling history v2 grounded-summary source revision mismatch")
    if current is not None and current.head != head:
        return _ConflictResult("Rolling history v2 grounded-summary head conflict")

    active_pointer = _read_active_branch_pointer(
        handle, scope, namespace, identity.canonical_namespace_digest,
    )
    if (active_pointer.pointer.state != "active"
            or active_pointer.pointer.branch_id != original.branch.branch_id):
        return _ConflictResult(
            "Rolling history v2 grounded-summary branch is not active",
        )
    if active_pointer.branch is None or active_pointer.head is None:
        raise ValueError("Rolling history v2 active-branch pointer is incomplete")
    active = _read_result_for_head(
        handle, scope, namespace, active_pointer.head.head,
    )
    if (active.branch != active_pointer.branch
            or active.head != active_pointer.head.head
            or active.branch.branch_id != original.branch.branch_id):
        raise ValueError("Rolling history v2 active-branch pointer result mismatch")
    if current is not None and current.head != active.head:
        return _ConflictResult("Rolling history v2 grounded-summary head conflict")

    active_lineage, _ = _read_lineage(handle, namespace, active.revision)
    if not active_lineage or active_lineage[-1] != active.revision:
        raise ValueError("Rolling history v2 active head lineage mismatch")
    try:
        original_index = next(
            index for index, item in enumerate(active_lineage)
            if item.revision_id == original.revision.revision_id
        )
    except StopIteration as exc:
        raise ValueError(
            "Rolling history v2 grounded-summary revision is not an active ancestor",
        ) from exc
    if active_lineage[original_index] != original.revision:
        raise ValueError("Rolling history v2 grounded-summary ancestor mismatch")
    for successor in active_lineage[original_index + 1:]:
        if (successor.operation != "append"
                or successor.branch_id != original.branch.branch_id):
            raise ValueError(
                "Rolling history v2 grounded-summary has a non-append descendant",
            )
    if (active.visible_event_locators[:len(original.visible_event_locators)]
            != original.visible_event_locators):
        raise ValueError(
            "Rolling history v2 grounded-summary visible prefix changed",
        )

    active_bodies, active_roles = _read_visible_rolling_message_sources(
        handle, scope, active.visible_event_locators,
    )
    expected_source = _grounded_summary_source_manifest(
        original_scope, identity.canonical_namespace_digest, original,
        summary_value, recent_message_start_boundary, active_bodies,
        active_roles,
    )
    if (value["source_manifest"] != expected_source
            or value["source_sha256"] != _sha256(_canonical(expected_source))):
        raise ValueError("Rolling history v2 grounded-summary source body mismatch")

    source_bodies = tuple(
        RollingHistoryV2PreparedSummarySourceBody(
            locator=_grounded_summary_locator_from_value(item["locator"]),
            body=item["body"],
            body_sha256=item["body_sha256"],
        )
        for item in expected_source["sources"]
    )
    claim_sources = tuple(
        RollingHistoryV2PreparedSummaryClaimSource(
            field=item["field"],
            claim_index=item["claim_index"],
            quote=item["quote"],
            locator=_grounded_summary_locator_from_value(item["locator"]),
            body_sha256=item["body_sha256"],
        )
        for item in expected_source["claim_sources"]
    )
    resolution_sources = tuple(
        RollingHistoryV2PreparedSummaryResolutionSource(
            field=item["field"],
            action=item["action"],
            prior_quote=item["prior_source"]["quote"],
            prior_source_role=item["prior_source"]["role"],
            prior_locator=_grounded_summary_locator_from_value(
                item["prior_source"]["locator"],
            ),
            evidence_quote=item["evidence_source"]["quote"],
            evidence_source_role=item["evidence_source"]["role"],
            evidence_locator=_grounded_summary_locator_from_value(
                item["evidence_source"]["locator"],
            ),
        )
        for item in expected_source.get("resolution_sources", [])
    )
    return RollingHistoryV2PreparedGroundedSummaryRecord(
        schema_version=value["schema_version"],
        state=value["state"],
        canonical_namespace_digest=value["canonical_namespace_digest"],
        payload_sha256=value["payload_sha256"],
        prepared_id=value["prepared_id"],
        summary=summary,
        recent_message_start_boundary=recent_message_start_boundary,
        branch=original.branch,
        revision=original.revision,
        expected_head=head,
        source_event_locators=tuple(item.locator for item in source_bodies),
        source_bodies=source_bodies,
        claim_sources=claim_sources,
        source_sha256=value["source_sha256"],
        prepared_record_sha256=value["prepared_record_sha256"],
        record_json=raw,
        resolution_sources=resolution_sources,
    )


def _summary_ledger_entries_from_manifest_value(
    raw,
    *,
    allow_empty: bool = False,
    allow_v2_v3_locators: bool = False,
) -> tuple[_SummaryLedgerEntryValue, ...]:
    if type(raw) is not list or (not allow_empty and not raw):
        raise ValueError("Invalid rolling history v2 summary-ledger manifest entries")
    entries: list[_SummaryLedgerEntryValue] = []
    for item in raw:
        if (type(item) is not dict
                or set(item) != {
                    "source_sequence", "source_locator", "source_role", "field",
                    "quote", "resolution",
                }
                or type(item["source_role"]) is not str
                or item["source_role"] not in ("human", "assistant", "tool")):
            raise ValueError("Invalid rolling history v2 summary-ledger manifest entry")
        resolution = item["resolution"]
        if resolution is None:
            resolution_action = None
            evidence_text = None
            evidence_locator = None
            evidence_sequence = None
            evidence_inherited = None
        else:
            if (type(resolution) is not dict
                    or set(resolution) != {
                        "action", "evidence_text", "evidence_locator",
                        "evidence_sequence", "inherited_from_previous_summary",
                        "evidence_source_role",
                    }
                    or type(resolution["evidence_source_role"]) is not str
                    or resolution["evidence_source_role"] not in (
                        "human", "assistant", "tool",
                    )):
                raise ValueError(
                    "Invalid rolling history v2 summary-ledger manifest resolution",
                )
            resolution_action = resolution["action"]
            evidence_text = resolution["evidence_text"]
            evidence_locator = _summary_ledger_locator_from_value(
                resolution["evidence_locator"],
                allow_v2_v3_locators=allow_v2_v3_locators,
            )
            evidence_sequence = resolution["evidence_sequence"]
            evidence_inherited = resolution["inherited_from_previous_summary"]
        entries.append(_normalize_summary_ledger_entry(
            source_sequence=item["source_sequence"],
            source_locator=_summary_ledger_locator_from_value(
                item["source_locator"],
                allow_v2_v3_locators=allow_v2_v3_locators,
            ),
            field=item["field"],
            quote=item["quote"],
            resolution_action=resolution_action,
            evidence_text=evidence_text,
            evidence_locator=evidence_locator,
            evidence_sequence=evidence_sequence,
            evidence_inherited_from_previous_summary=evidence_inherited,
        ))
    return tuple(entries)


def _summary_ledger_record_entries(
    source_manifest: dict,
    *,
    allow_v2_v3_locators: bool = False,
) -> tuple[
    tuple[RollingHistoryV2PreparedSummaryLedgerEntry, ...],
    tuple[RollingHistoryV2PreparedSummaryLedgerSourceBody, ...],
]:
    entries: list[RollingHistoryV2PreparedSummaryLedgerEntry] = []
    for item in source_manifest["entries"]:
        raw_resolution = item["resolution"]
        resolution = None
        if raw_resolution is not None:
            resolution = RollingHistoryV2PreparedSummaryLedgerResolution(
                action=raw_resolution["action"],
                evidence_text=raw_resolution["evidence_text"],
                evidence_locator=_summary_ledger_locator_from_value(
                    raw_resolution["evidence_locator"],
                    allow_v2_v3_locators=allow_v2_v3_locators,
                ),
                evidence_sequence=raw_resolution["evidence_sequence"],
                evidence_source_role=raw_resolution["evidence_source_role"],
                inherited_from_previous_summary=(
                    raw_resolution["inherited_from_previous_summary"]
                ),
            )
        entries.append(RollingHistoryV2PreparedSummaryLedgerEntry(
            source_sequence=item["source_sequence"],
            source_locator=_summary_ledger_locator_from_value(
                item["source_locator"],
                allow_v2_v3_locators=allow_v2_v3_locators,
            ),
            source_role=item["source_role"],
            field=item["field"],
            quote=item["quote"],
            resolution=resolution,
        ))
    sources = tuple(
        RollingHistoryV2PreparedSummaryLedgerSourceBody(
            locator=_summary_ledger_locator_from_value(
                item["locator"],
                allow_v2_v3_locators=allow_v2_v3_locators,
            ),
            role=item["role"],
            body=item["body"],
            body_sha256=item["body_sha256"],
        )
        for item in source_manifest["sources"]
    )
    return tuple(entries), sources


def _decode_summary_ledger_record(
    handle: RollingHistorySqlHandle,
    scope: ContextEventScope,
    namespace: str,
    identity,
    raw: str,
    *,
    expected_prepared_id: str,
    current: _RawRollingHistoryV2StoreResult | None = None,
) -> RollingHistoryV2PreparedSummaryLedgerRecord | _ConflictResult:
    value = _decode_canonical_object(raw, "rolling history v2 summary ledger")
    required = {
        "schema_version", "kind", "state", "scope_sha256",
        "canonical_namespace_digest", "request_id", "prepared_id",
        "idempotency_id", "idempotency_key_sha256", "payload_sha256",
        "branch_id", "revision_id", "revision_sha256", "history_revision",
        "history_head_sha256", "branch", "revision", "expected_head",
        "source_manifest", "source_sha256", "prepared_record_sha256",
    }
    if (set(value) != required
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _SUMMARY_LEDGER_SCHEMA_VERSION
            or value["kind"] != _SUMMARY_LEDGER_KIND
            or value["state"] != "PREPARED"
            or value["scope_sha256"] != _scope_digest(scope)
            or value["canonical_namespace_digest"] != identity.canonical_namespace_digest
            or type(value["request_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["request_id"]) is None
            or type(value["prepared_id"]) is not str
            or _HASH.fullmatch(value["prepared_id"]) is None
            or value["prepared_id"] != expected_prepared_id
            or value["idempotency_id"] != expected_prepared_id
            or type(value["idempotency_key_sha256"]) is not str
            or _HASH.fullmatch(value["idempotency_key_sha256"]) is None
            or type(value["payload_sha256"]) is not str
            or _HASH.fullmatch(value["payload_sha256"]) is None
            or type(value["branch_id"]) is not str
            or _HASH.fullmatch(value["branch_id"]) is None
            or type(value["revision_id"]) is not str
            or _HASH.fullmatch(value["revision_id"]) is None
            or type(value["revision_sha256"]) is not str
            or _HASH.fullmatch(value["revision_sha256"]) is None
            or type(value["history_revision"]) is not int
            or value["history_revision"] < 0
            or type(value["history_head_sha256"]) is not str
            or _HASH.fullmatch(value["history_head_sha256"]) is None
            or type(value["branch"]) is not dict
            or type(value["revision"]) is not dict
            or type(value["expected_head"]) is not dict
            or type(value["source_manifest"]) is not dict
            or type(value["source_sha256"]) is not str
            or _HASH.fullmatch(value["source_sha256"]) is None
            or type(value["prepared_record_sha256"]) is not str
            or _HASH.fullmatch(value["prepared_record_sha256"]) is None):
        raise ValueError("Invalid rolling history v2 summary-ledger binding")

    original_scope = ContextEventScope(
        session_id=scope.session_id,
        request_id=value["request_id"],
        principal_hash=scope.principal_hash,
        session_epoch=scope.session_epoch,
    )
    if value["scope_sha256"] != _scope_digest(original_scope):
        raise ValueError("Rolling history v2 summary-ledger scope mismatch")
    head = _decode_head(value["expected_head"])
    revision = _decode_revision(_canonical(value["revision"]))
    branch = _decode_branch(_canonical(value["branch"]))
    if (head.branch_id != value["branch_id"]
            or head.revision_id != value["revision_id"]
            or head.revision_sha256 != value["revision_sha256"]
            or head.history_revision != value["history_revision"]
            or head.history_head_sha256 != value["history_head_sha256"]
            or value["source_sha256"] != _sha256(_canonical(value["source_manifest"]))
            or value["payload_sha256"] != _summary_ledger_payload_sha256(
                _RawRollingHistoryV2StoreResult(branch, revision, head, ()),
                expected_prepared_id,
                value["idempotency_key_sha256"],
                value["source_manifest"],
            )
            or value["prepared_record_sha256"] != _summary_ledger_record_digest(value)):
        raise ValueError("Rolling history v2 summary-ledger digest mismatch")

    original = _read_result_for_head(handle, original_scope, namespace, head)
    if (branch != original.branch
            or revision != original.revision
            or value["branch"] != _branch_value(original.branch)
            or value["revision"] != _revision_value(original.revision)
            or value["branch_id"] != original.branch.branch_id
            or value["revision_id"] != original.revision.revision_id):
        raise ValueError("Rolling history v2 summary-ledger source revision mismatch")
    if current is not None and current.head != head:
        return _ConflictResult("Rolling history v2 summary-ledger head conflict")

    active_pointer = _read_active_branch_pointer(
        handle, scope, namespace, identity.canonical_namespace_digest,
    )
    if (active_pointer.pointer.state != "active"
            or active_pointer.pointer.branch_id != original.branch.branch_id):
        return _ConflictResult(
            "Rolling history v2 summary-ledger branch is not active",
        )
    if active_pointer.branch is None or active_pointer.head is None:
        raise ValueError("Rolling history v2 active-branch pointer is incomplete")
    active = _read_result_for_head(
        handle, scope, namespace, active_pointer.head.head,
    )
    if (active.branch != active_pointer.branch
            or active.head != active_pointer.head.head
            or active.branch.branch_id != original.branch.branch_id):
        raise ValueError("Rolling history v2 active-branch pointer result mismatch")
    if current is not None and current.head != active.head:
        return _ConflictResult("Rolling history v2 summary-ledger head conflict")

    active_lineage, _ = _read_lineage(handle, namespace, active.revision)
    if not active_lineage or active_lineage[-1] != active.revision:
        raise ValueError("Rolling history v2 active head lineage mismatch")
    try:
        original_index = next(
            index for index, item in enumerate(active_lineage)
            if item.revision_id == original.revision.revision_id
        )
    except StopIteration as exc:
        raise ValueError(
            "Rolling history v2 summary-ledger revision is not an active ancestor",
        ) from exc
    if active_lineage[original_index] != original.revision:
        raise ValueError("Rolling history v2 summary-ledger ancestor mismatch")
    for successor in active_lineage[original_index + 1:]:
        if (successor.operation != "append"
                or successor.branch_id != original.branch.branch_id):
            raise ValueError(
                "Rolling history v2 summary-ledger has a non-append descendant",
            )
    if (active.visible_event_locators[:len(original.visible_event_locators)]
            != original.visible_event_locators):
        raise ValueError("Rolling history v2 summary-ledger visible prefix changed")

    manifest_required = {
        "schema_version", "kind", "scope_sha256", "canonical_namespace_digest",
        "request_id", "branch", "revision", "expected_head",
        "visible_event_count", "entries", "sources",
    }
    source_manifest = value["source_manifest"]
    if (set(source_manifest) != manifest_required
            or type(source_manifest["schema_version"]) is not int
            or source_manifest["schema_version"] != _SUMMARY_LEDGER_SCHEMA_VERSION
            or type(source_manifest["kind"]) is not str
            or source_manifest["kind"] != _SUMMARY_LEDGER_SOURCE_KIND):
        raise ValueError("Invalid rolling history v2 summary-ledger source manifest")
    entries = _summary_ledger_entries_from_manifest_value(
        source_manifest["entries"],
    )
    active_bodies, active_roles = _read_visible_rolling_message_sources(
        handle, scope, active.visible_event_locators,
    )
    expected_source = _summary_ledger_source_manifest(
        original_scope, identity.canonical_namespace_digest, original,
        entries, active_bodies, active_roles,
    )
    if (_canonical(source_manifest) != _canonical(expected_source)
            or value["source_sha256"] != _sha256(_canonical(expected_source))
            or value["payload_sha256"] != _summary_ledger_payload_sha256(
                original, expected_prepared_id,
                value["idempotency_key_sha256"], expected_source,
            )):
        raise ValueError("Rolling history v2 summary-ledger source body mismatch")

    prepared_entries, source_bodies = _summary_ledger_record_entries(expected_source)
    return RollingHistoryV2PreparedSummaryLedgerRecord(
        schema_version=value["schema_version"],
        state=value["state"],
        canonical_namespace_digest=value["canonical_namespace_digest"],
        payload_sha256=value["payload_sha256"],
        prepared_id=value["prepared_id"],
        entries=prepared_entries,
        branch=original.branch,
        revision=original.revision,
        expected_head=head,
        source_bodies=source_bodies,
        source_manifest_json=_canonical(expected_source),
        source_sha256=value["source_sha256"],
        prepared_record_sha256=value["prepared_record_sha256"],
        record_json=raw,
    )

