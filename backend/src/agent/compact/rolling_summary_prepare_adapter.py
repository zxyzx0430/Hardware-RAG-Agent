"""Coordinate PREPARED rolling-summary and summary-ledger store records.

The adapter runs only inside an already authenticated rolling-history owner
scope. It performs complete local shape and binding checks before asking the
store to prepare an optional ledger and then a grounded summary. Store owner
scope, expected-head checks, immutable writes, and readbacks remain the only
authorization and persistence boundary.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.api.rolling_history import RollingHistoryV2Head
    from app.api.rolling_revision_store import _BoundRollingHistoryV2Head
    from src.agent.compact.rolling_summarizer import (
        RollingHostBinding,
        RollingSummaryResult,
    )


_LOCATOR_FIELDS = (
    "event_id",
    "session_id",
    "request_id",
    "sequence",
    "version",
    "content_sha256",
    "chain_sha256",
)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_LOCATOR_BYTE_LIMIT = 1024


@dataclass(frozen=True, slots=True)
class RollingSummaryPreparedRecordReference:
    """Immutable reference to one store record returned after readback."""

    kind: Literal["grounded_summary", "summary_ledger"]
    prepared_id: str
    payload_sha256: str
    prepared_record_sha256: str


@dataclass(frozen=True, slots=True)
class RollingSummaryPrepareAdapterResult:
    """Combined PREPARED references; this does not publish or acknowledge."""

    state: Literal["PREPARED"]
    expected_head: RollingHistoryV2Head
    covered_through: int
    grounded_summary: RollingSummaryPreparedRecordReference
    ledger_status: Literal["PREPARED", "NOT_REQUIRED"]
    summary_ledger: RollingSummaryPreparedRecordReference | None


def prepare_rolling_summary_and_ledger(
    result: RollingSummaryResult,
    *,
    expected_binding: RollingHostBinding,
    expected_head: _BoundRollingHistoryV2Head,
    recent_message_start_boundary: int,
    summary_ledger_idempotency_key: str,
    grounded_summary_idempotency_key: str,
) -> RollingSummaryPrepareAdapterResult:
    """Prepare optional evidence and then the accepted summary at one head.

    ``expected_binding`` and both idempotency keys are host inputs. The caller
    must already be inside ``rolling_history_v2_owner_scope``; the store APIs
    enforce that ContextVar scope and validate the host-issued expected-head
    token before writing. The same token object is passed to both APIs.

    Store prepare APIs commit immutable records and perform owner-bound
    readback before returning. Any error propagates and no combined result is
    returned. If the ledger prepare succeeds but grounded-summary prepare
    fails, the ledger remains an orphan PREPARED record, not a publication.
    """
    from app.api import rolling_revision_store as store
    from src.agent.compact.rolling_summarizer import (
        GroundedClaim,
        RollingCitation,
        RollingHostBinding,
        RollingSummary,
        RollingSummaryResult,
    )
    from src.agent.compact.rolling_summary_ledger_bridge import (
        RollingSummaryLedgerBridgePayload,
        RollingSummaryLedgerResolutionEvidence,
        build_rolling_summary_ledger_bridge_payload,
    )
    from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS

    if (
        type(result) is not RollingSummaryResult
        or type(result.status) is not str
        or result.status != "accepted"
        or result.reason is not None
        or type(result.summary) is not RollingSummary
        or type(result.archive_candidates) is not tuple
    ):
        raise ValueError("Invalid accepted rolling-summary result")

    summary = result.summary
    if (
        type(summary.covered_through) is not int
        or summary.covered_through < 0
        or type(recent_message_start_boundary) is not int
        or recent_message_start_boundary != summary.covered_through + 1
        or type(summary.resolution_records) is not tuple
        or summary.resolution_records
    ):
        raise ValueError("Invalid rolling-summary boundary or legacy resolution shape")

    _require_idempotency_key(summary_ledger_idempotency_key)
    _require_idempotency_key(grounded_summary_idempotency_key)

    summary_references = _validate_summary_shape(
        summary,
        checkpoint_fields=CHECKPOINT_FIELDS,
        grounded_claim_type=GroundedClaim,
        citation_type=RollingCitation,
    )
    source_manifest = _validate_host_binding(
        expected_binding,
        binding_type=RollingHostBinding,
        covered_through=summary.covered_through,
    )

    payload = build_rolling_summary_ledger_bridge_payload(
        result,
        expected_binding=expected_binding,
    )
    if type(payload) is not RollingSummaryLedgerBridgePayload:
        raise ValueError("Invalid rolling-summary evidence candidates")
    _validate_reference_binding(
        payload,
        source_manifest=source_manifest,
        summary_references=summary_references,
        resolution_type=RollingSummaryLedgerResolutionEvidence,
    )

    if (
        type(expected_head) is not store._BoundRollingHistoryV2Head
        or type(expected_head.head) is not store.RollingHistoryV2Head
    ):
        raise ValueError("Invalid rolling-history expected-head token")
    head_value = expected_head.head

    ledger_reference: RollingSummaryPreparedRecordReference | None = None
    if payload.entries:
        ledger_record = store.prepare_rolling_history_v2_summary_ledger(
            expected_head=expected_head,
            payload=payload,
            idempotency_key=summary_ledger_idempotency_key,
        )
        if (
            type(ledger_record) is not store.RollingHistoryV2PreparedSummaryLedgerRecord
            or ledger_record.state != "PREPARED"
            or ledger_record.expected_head != head_value
            or type(ledger_record.entries) is not tuple
            or len(ledger_record.entries) != len(payload.entries)
            or not _valid_prepared_reference_fields(
                ledger_record.prepared_id,
                ledger_record.payload_sha256,
                ledger_record.prepared_record_sha256,
            )
        ):
            raise ValueError("Summary-ledger store did not return a matching PREPARED record")
        ledger_reference = RollingSummaryPreparedRecordReference(
            kind="summary_ledger",
            prepared_id=ledger_record.prepared_id,
            payload_sha256=ledger_record.payload_sha256,
            prepared_record_sha256=ledger_record.prepared_record_sha256,
        )

    summary_record = store.prepare_rolling_history_v2_grounded_summary(
        expected_head=expected_head,
        summary=summary,
        recent_message_start_boundary=recent_message_start_boundary,
        idempotency_key=grounded_summary_idempotency_key,
    )
    if (
        type(summary_record) is not store.RollingHistoryV2PreparedGroundedSummaryRecord
        or summary_record.state != "PREPARED"
        or summary_record.expected_head != head_value
        or type(summary_record.summary) is not RollingSummary
        or summary_record.summary.covered_through != summary.covered_through
        or summary_record.recent_message_start_boundary != recent_message_start_boundary
        or not _valid_prepared_reference_fields(
            summary_record.prepared_id,
            summary_record.payload_sha256,
            summary_record.prepared_record_sha256,
        )
    ):
        raise ValueError("Grounded-summary store did not return a matching PREPARED record")

    return RollingSummaryPrepareAdapterResult(
        state="PREPARED",
        expected_head=head_value,
        covered_through=summary.covered_through,
        grounded_summary=RollingSummaryPreparedRecordReference(
            kind="grounded_summary",
            prepared_id=summary_record.prepared_id,
            payload_sha256=summary_record.payload_sha256,
            prepared_record_sha256=summary_record.prepared_record_sha256,
        ),
        ledger_status="PREPARED" if ledger_reference is not None else "NOT_REQUIRED",
        summary_ledger=ledger_reference,
    )


def _validate_summary_shape(
    summary: object,
    *,
    checkpoint_fields,
    grounded_claim_type,
    citation_type,
) -> tuple[tuple[int, str], ...]:
    if (
        not isinstance(summary.fields, Mapping)
        or set(summary.fields) != set(checkpoint_fields)
    ):
        raise ValueError("Invalid rolling-summary fields")

    references: list[tuple[int, str]] = []
    reference_by_sequence: dict[int, str] = {}
    session_id: str | None = None
    for field in checkpoint_fields:
        claims = summary.fields[field]
        if type(claims) not in (tuple, list):
            raise ValueError("Invalid rolling-summary claim collection")
        if field == "completed" and claims:
            raise ValueError("Rolling summary cannot claim completion")
        for claim in claims:
            if (
                type(claim) is not grounded_claim_type
                or type(claim.citation) is not citation_type
                or not _valid_text(claim.text)
                or type(claim.citation.sequence) is not int
                or not 1 <= claim.citation.sequence <= summary.covered_through
                or type(claim.citation.reference_json) is not str
                or type(claim.citation.inherited_from_previous_summary) is not bool
            ):
                raise ValueError("Invalid rolling-summary claim or citation")
            locator = _parse_canonical_locator(claim.citation.reference_json)
            if locator is None or locator[0] != claim.citation.sequence:
                raise ValueError("Invalid rolling-summary citation locator")
            if session_id is None:
                session_id = locator[1]
            elif locator[1] != session_id:
                raise ValueError("Rolling-summary citations cross owner sessions")
            previous = reference_by_sequence.get(locator[0])
            if previous is not None and previous != claim.citation.reference_json:
                raise ValueError("Conflicting rolling-summary citation locators")
            reference_by_sequence[locator[0]] = claim.citation.reference_json
            references.append((locator[0], claim.citation.reference_json))
    return tuple(references)


def _validate_host_binding(
    binding: object,
    *,
    binding_type,
    covered_through: int,
) -> dict[int, str]:
    if (
        type(binding) is not binding_type
        or not _valid_text(binding.host_identity)
        or not _valid_text(binding.scope_id)
        or type(binding.source_manifest) is not tuple
    ):
        raise ValueError("Invalid trusted rolling-summary host binding shape")

    result: dict[int, str] = {}
    sequence_by_reference: dict[str, int] = {}
    session_id: str | None = None
    for item in binding.source_manifest:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("Invalid rolling-summary source manifest entry")
        sequence, reference_json = item
        if (
            type(sequence) is not int
            or not 1 <= sequence <= covered_through
            or type(reference_json) is not str
        ):
            raise ValueError("Invalid rolling-summary source manifest locator")
        locator = _parse_canonical_locator(reference_json)
        if locator is None or locator[0] != sequence or sequence in result:
            raise ValueError("Invalid or duplicated rolling-summary source locator")
        if session_id is None:
            session_id = locator[1]
        elif locator[1] != session_id:
            raise ValueError("Rolling-summary source manifest crosses owner sessions")
        previous_sequence = sequence_by_reference.get(reference_json)
        if previous_sequence is not None and previous_sequence != sequence:
            raise ValueError("Rolling-summary source locator is reused across sequences")
        result[sequence] = reference_json
        sequence_by_reference[reference_json] = sequence
    return result


def _validate_reference_binding(
    payload: object,
    *,
    source_manifest: dict[int, str],
    summary_references: tuple[tuple[int, str], ...],
    resolution_type,
) -> None:
    reference_by_sequence: dict[int, str] = {}
    session_id: str | None = None

    def bind_reference(sequence: int, reference_json: str, *, require_manifest: bool) -> None:
        nonlocal session_id
        locator = _parse_canonical_locator(reference_json)
        if locator is None or locator[0] != sequence:
            raise ValueError("Invalid rolling-summary evidence locator")
        if session_id is None:
            session_id = locator[1]
        elif locator[1] != session_id:
            raise ValueError("Rolling-summary evidence crosses owner sessions")
        prior = reference_by_sequence.get(sequence)
        if prior is not None and prior != reference_json:
            raise ValueError("Conflicting rolling-summary references for one sequence")
        reference_by_sequence[sequence] = reference_json

        expected_reference = source_manifest.get(sequence)
        if expected_reference is not None:
            if expected_reference != reference_json:
                raise ValueError("Rolling-summary reference differs from host manifest")
        elif require_manifest:
            raise ValueError("Rolling-summary reference is absent from host manifest")

    for sequence, reference_json in summary_references:
        bind_reference(sequence, reference_json, require_manifest=True)

    for entry in payload.entries:
        bind_reference(
            entry.source_sequence,
            entry.source_locator.canonical_json,
            require_manifest=True,
        )
        resolution = entry.resolution
        if resolution is not None:
            if type(resolution) is not resolution_type:
                raise ValueError("Invalid rolling-summary resolution evidence")
            bind_reference(
                resolution.evidence_sequence,
                resolution.evidence_locator.canonical_json,
                require_manifest=True,
            )


def _parse_canonical_locator(reference_json: str) -> tuple[int, str] | None:
    if type(reference_json) is not str:
        return None
    try:
        value = json.loads(reference_json, object_pairs_hook=_unique_json_object)
        if type(value) is not dict or set(value) != set(_LOCATOR_FIELDS):
            return None
        event_id = value["event_id"]
        session_id = value["session_id"]
        request_id = value["request_id"]
        sequence = value["sequence"]
        version = value["version"]
        content_sha256 = value["content_sha256"]
        chain_sha256 = value["chain_sha256"]
        if (
            type(event_id) is not str
            or _HASH.fullmatch(event_id) is None
            or type(session_id) is not str
            or not 1 <= len(session_id) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
            or type(request_id) is not str
            or _REQUEST_ID.fullmatch(request_id) is None
            or type(sequence) is not int
            or sequence < 1
            or type(version) is not int
            or version != 1
            or type(content_sha256) is not str
            or _HASH.fullmatch(content_sha256) is None
            or type(chain_sha256) is not str
            or _HASH.fullmatch(chain_sha256) is None
        ):
            return None
        canonical = json.dumps(
            {field: value[field] for field in _LOCATOR_FIELDS},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if (
            canonical != reference_json
            or len(canonical.encode("utf-8", errors="strict")) > _LOCATOR_BYTE_LIMIT
        ):
            return None
        return sequence, session_id
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        return None


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("Duplicate rolling-summary locator field")
        result[key] = value
    return result


def _valid_text(value: object) -> bool:
    if type(value) is not str or not value.strip():
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


def _require_idempotency_key(value: object) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 256
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Invalid host-supplied rolling-summary idempotency key")


def _valid_prepared_reference_fields(
    prepared_id: object,
    payload_sha256: object,
    prepared_record_sha256: object,
) -> bool:
    return all(
        type(value) is str and _HASH.fullmatch(value) is not None
        for value in (prepared_id, payload_sha256, prepared_record_sha256)
    )
