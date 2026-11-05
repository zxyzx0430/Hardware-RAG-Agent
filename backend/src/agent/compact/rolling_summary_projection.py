"""Read-only projection of owner-verified rolling-summary records.

This module must be called inside the authenticated rolling-history owner
scope. Prepared IDs are lookup keys only; the store's current owner-bound read
APIs decide visibility and revalidate the persisted records and their sources.
The projection contains untrusted historical claim text, not authorization.
It does not prove semantic truth, dispatch a model, publish PREPARED records,
equate PREPARED with publication, or replay tools.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import re


_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_LOCATOR_FIELDS = (
    "event_id",
    "session_id",
    "request_id",
    "sequence",
    "version",
    "content_sha256",
    "chain_sha256",
)
_EVENT_LOCATOR_FIELDS = (
    "event_id",
    "request_id",
    "sequence",
    "version",
    "content_sha256",
    "chain_sha256",
)
_ROLES = frozenset({"human", "assistant", "tool"})
_RESOLUTION_ACTIONS = frozenset({"replace", "complete", "revoke"})
_PROJECTION_VERSION = 1
_INDEX_VERSION = 1


@dataclass(frozen=True, slots=True)
class RollingSummaryProjection:
    """Immutable host index metadata and locator-free summary text.

    ``summary_json`` is the only field intended to be copied into model
    context. It contains only explicitly untrusted historical claim text and
    all checkpoint fields; source locators, manifests, and ledger evidence
    remain out of that text.
    """

    index_identifier: str
    index_metadata_json: str
    summary_json: str
    projection_sha256: str


def project_prepared_rolling_summary(
    grounded_summary_prepared_id: str,
    *,
    summary_ledger_prepared_id: str | None = None,
) -> RollingSummaryProjection:
    """Read, verify, and project PREPARED summary records for the current owner.

    Only prepared record IDs are accepted as lookup keys. The caller cannot
    supply identity, epoch, branch, revision, head, coverage, or locators. The
    store read APIs require the already-authenticated owner scope and fail
    closed when a record is not visible or any bound data has changed.
    """
    _require_prepared_id(grounded_summary_prepared_id, "grounded-summary")
    if summary_ledger_prepared_id is not None:
        _require_prepared_id(summary_ledger_prepared_id, "summary-ledger")

    # Import at call time so importing this pure projection module does not
    # initialize or access the store. The caller still must establish the
    # authenticated owner scope before invoking this function.
    from app.api import rolling_revision_store as store
    from src.agent.compact import rolling_summarizer
    from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS

    grounded = store.read_rolling_history_v2_grounded_summary(
        grounded_summary_prepared_id,
    )
    grounded_index, field_claims, summary_sessions = _validate_grounded_record(
        grounded,
        grounded_summary_prepared_id,
        store=store,
        summarizer=rolling_summarizer,
        checkpoint_fields=CHECKPOINT_FIELDS,
    )

    ledger_index = None
    if summary_ledger_prepared_id is not None:
        ledger = store.read_rolling_history_v2_summary_ledger(
            summary_ledger_prepared_id,
        )
        ledger_index = _validate_ledger_record(
            ledger,
            summary_ledger_prepared_id,
            store=store,
            checkpoint_fields=CHECKPOINT_FIELDS,
            covered_through=grounded.summary.covered_through,
        )
        if (
            ledger_index["canonical_namespace_digest"]
            != grounded_index["canonical_namespace_digest"]
            or ledger_index["branch"] != grounded_index["branch"]
            or ledger_index["revision"] != grounded_index["revision"]
            or ledger_index["expected_head"] != grounded_index["expected_head"]
        ):
            raise ValueError(
                "Grounded summary and summary ledger are not bound to the same owner head",
            )

        ledger_sessions = ledger_index.pop("_session_ids")
        if summary_sessions and ledger_sessions and summary_sessions != ledger_sessions:
            raise ValueError(
                "Grounded summary and summary ledger use different owner sessions",
            )

    fields = {
        field: [
            {"text": claim.text, "trust": "untrusted_historical_content"}
            for claim in field_claims[field]
        ]
        for field in CHECKPOINT_FIELDS
    }
    projection_document = {
        "schema": f"rolling-summary-projection.v{_PROJECTION_VERSION}",
        "content_trust": "untrusted_historical_content",
        "covered_through": grounded.summary.covered_through,
        "fields": fields,
    }
    summary_json = _canonical_json(projection_document)
    summary_bytes = summary_json.encode("utf-8", errors="strict")
    projection_sha256 = sha256(summary_bytes).hexdigest()

    # This index is host metadata. It deliberately contains no source manifest,
    # citation locator, source body, or evidence chain.
    index_document = {
        "schema": f"rolling-summary-projection-index.v{_INDEX_VERSION}",
        "grounded_summary": grounded_index,
        "summary_ledger": ledger_index,
        "semantics": {
            "semantic_truth_proven": False,
            "authorization_granted": False,
            "model_dispatched": False,
            "published": False,
            "prepared_is_published": False,
            "tool_replayed": False,
            "ledger_is_authorization": False,
        },
    }
    index_metadata_json = _canonical_json(index_document)
    index_digest = sha256(index_metadata_json.encode("utf-8", errors="strict")).hexdigest()
    return RollingSummaryProjection(
        index_identifier=f"rolling-summary-projection:v{_INDEX_VERSION}:{index_digest}",
        index_metadata_json=index_metadata_json,
        summary_json=summary_json,
        projection_sha256=projection_sha256,
    )


def _validate_grounded_record(
    record,
    requested_id: str,
    *,
    store,
    summarizer,
    checkpoint_fields,
) -> tuple[dict, dict[str, tuple], frozenset[str]]:
    if type(record) is not store.RollingHistoryV2PreparedGroundedSummaryRecord:
        raise ValueError("Grounded-summary store read returned an invalid record")
    if (
        record.state != "PREPARED"
        or type(record.schema_version) is not int
        or record.schema_version != 1
        or record.prepared_id != requested_id
        or not _is_hash(record.prepared_id)
        or not _is_hash(record.payload_sha256)
        or not _is_hash(record.source_sha256)
        or not _is_hash(record.prepared_record_sha256)
        or not _is_hash(record.canonical_namespace_digest)
    ):
        raise ValueError("Grounded-summary record identity or hashes are invalid")

    head_data = _head_metadata(
        record.expected_head,
        record.branch,
        record.revision,
        head_type=store.RollingHistoryV2Head,
        branch_type=store.RollingHistoryV2Branch,
        revision_type=store.RollingHistoryV2Revision,
    )
    summary = record.summary
    if (
        type(summary) is not summarizer.RollingSummary
        or type(summary.covered_through) is not int
        or summary.covered_through < 0
        or type(record.recent_message_start_boundary) is not int
        or record.recent_message_start_boundary != summary.covered_through + 1
        or not isinstance(summary.fields, Mapping)
        or set(summary.fields) != set(checkpoint_fields)
        or type(summary.resolution_records) is not tuple
        or type(record.claim_sources) is not tuple
        or type(record.source_bodies) is not tuple
        or type(record.source_event_locators) is not tuple
        or type(record.resolution_sources) is not tuple
    ):
        raise ValueError("Grounded-summary shape or coverage boundary is invalid")

    source_bodies: dict[tuple, object] = {}
    source_body_keys: list[tuple] = []
    previous_sequence = 0
    for source in record.source_bodies:
        if type(source) is not store.RollingHistoryV2PreparedSummarySourceBody:
            raise ValueError("Grounded-summary source body is invalid")
        key = _event_locator_key(
            source.locator,
            locator_type=store.RollingHistoryV2EventLocator,
            covered_through=summary.covered_through,
        )
        if key[2] <= previous_sequence or key in source_bodies:
            raise ValueError("Grounded-summary source locators are duplicated or unordered")
        previous_sequence = key[2]
        if (
            not _valid_text(source.body)
            or not _is_hash(source.body_sha256)
            or _sha256_text(source.body) != source.body_sha256
        ):
            raise ValueError("Grounded-summary source body hash is invalid")
        source_bodies[key] = source
        source_body_keys.append(key)

    event_keys = tuple(
        _event_locator_key(
            locator,
            locator_type=store.RollingHistoryV2EventLocator,
            covered_through=summary.covered_through,
        )
        for locator in record.source_event_locators
    )
    if event_keys != tuple(source_body_keys):
        raise ValueError("Grounded-summary source locator list does not match source bodies")

    claims_by_coordinate: dict[tuple[str, int], object] = {}
    claim_text_fields: dict[str, tuple] = {}
    sessions: set[str] = set()
    for field in checkpoint_fields:
        claims = summary.fields[field]
        if type(claims) not in (tuple, list):
            raise ValueError("Grounded-summary claim collection is invalid")
        if field == "completed" and claims:
            raise ValueError("A rolling summary cannot claim completion")
        claim_text_fields[field] = tuple(claims)
        for claim_index, claim in enumerate(claims):
            if (
                type(claim) is not summarizer.GroundedClaim
                or type(claim.citation) is not summarizer.RollingCitation
                or not _valid_text(claim.text)
                or type(claim.citation.sequence) is not int
                or type(claim.citation.inherited_from_previous_summary) is not bool
            ):
                raise ValueError("Grounded-summary claim or citation is invalid")
            reference = _parse_citation_reference(
                claim.citation.reference_json,
                covered_through=summary.covered_through,
            )
            if reference["sequence"] != claim.citation.sequence:
                raise ValueError("Grounded-summary citation sequence mismatch")
            sessions.add(reference["session_id"])
            claims_by_coordinate[(field, claim_index)] = (claim, reference)

    claim_sources: dict[tuple[str, int], object] = {}
    if len(record.claim_sources) != len(claims_by_coordinate):
        raise ValueError("Grounded-summary claim source count mismatch")
    for source in record.claim_sources:
        if type(source) is not store.RollingHistoryV2PreparedSummaryClaimSource:
            raise ValueError("Grounded-summary claim source is invalid")
        coordinate = (source.field, source.claim_index)
        if coordinate in claim_sources or coordinate not in claims_by_coordinate:
            raise ValueError("Grounded-summary claim source reference is duplicated or unknown")
        claim, reference = claims_by_coordinate[coordinate]
        locator_key = _event_locator_key(
            source.locator,
            locator_type=store.RollingHistoryV2EventLocator,
            covered_through=summary.covered_through,
        )
        _require_reference_matches_event(reference, source.locator)
        body = source_bodies.get(locator_key)
        if (
            body is None
            or source.quote != claim.text
            or source.quote not in body.body
            or source.body_sha256 != body.body_sha256
            or not _is_hash(source.body_sha256)
        ):
            raise ValueError("Grounded-summary claim is not bound to its read-back source body")
        claim_sources[coordinate] = source
    if set(claim_sources) != set(claims_by_coordinate):
        raise ValueError("Grounded-summary claim source coverage is incomplete")

    if len(record.resolution_sources) != len(summary.resolution_records):
        raise ValueError("Grounded-summary resolution evidence count mismatch")
    for resolution, source in zip(
        summary.resolution_records,
        record.resolution_sources,
        strict=True,
    ):
        if (
            type(resolution) is not summarizer.RollingResolutionRecord
            or type(source) is not store.RollingHistoryV2PreparedSummaryResolutionSource
            or resolution.field not in checkpoint_fields
            or resolution.field == "completed"
            or resolution.action not in _RESOLUTION_ACTIONS
            or resolution.evidence_role != "human"
            or source.field != resolution.field
            or source.action != resolution.action
            or source.prior_quote != resolution.prior_claim.text
            or source.evidence_quote != resolution.evidence_claim.text
            or source.prior_source_role not in _ROLES
            or source.evidence_source_role != "human"
        ):
            raise ValueError("Grounded-summary resolution evidence is invalid")
        prior_reference = _parse_citation_reference(
            resolution.prior_claim.citation.reference_json,
            covered_through=summary.covered_through,
        )
        evidence_reference = _parse_citation_reference(
            resolution.evidence_claim.citation.reference_json,
            covered_through=summary.covered_through,
        )
        prior_key = _event_locator_key(
            source.prior_locator,
            locator_type=store.RollingHistoryV2EventLocator,
            covered_through=summary.covered_through,
        )
        evidence_key = _event_locator_key(
            source.evidence_locator,
            locator_type=store.RollingHistoryV2EventLocator,
            covered_through=summary.covered_through,
        )
        _require_reference_matches_event(prior_reference, source.prior_locator)
        _require_reference_matches_event(evidence_reference, source.evidence_locator)
        prior_body = source_bodies.get(prior_key)
        evidence_body = source_bodies.get(evidence_key)
        if (
            prior_body is None
            or evidence_body is None
            or source.prior_quote not in prior_body.body
            or source.evidence_quote not in evidence_body.body
            or evidence_key[2] <= prior_key[2]
            or resolution.evidence_claim.citation.inherited_from_previous_summary
        ):
            raise ValueError("Grounded-summary resolution is not grounded in later source text")
        sessions.update((prior_reference["session_id"], evidence_reference["session_id"]))

    if len(sessions) > 1:
        raise ValueError("Grounded-summary references cross owner sessions")

    index_record = {
        "schema_version": record.schema_version,
        "state": record.state,
        "prepared_id": record.prepared_id,
        "payload_sha256": record.payload_sha256,
        "source_sha256": record.source_sha256,
        "prepared_record_sha256": record.prepared_record_sha256,
        "canonical_namespace_digest": record.canonical_namespace_digest,
        "branch": head_data["branch"],
        "revision": head_data["revision"],
        "expected_head": head_data["expected_head"],
        "covered_through": summary.covered_through,
        "recent_message_start_boundary": record.recent_message_start_boundary,
    }
    return index_record, claim_text_fields, frozenset(sessions)


def _validate_ledger_record(
    record,
    requested_id: str,
    *,
    store,
    checkpoint_fields,
    covered_through: int,
) -> dict:
    if type(record) is not store.RollingHistoryV2PreparedSummaryLedgerRecord:
        raise ValueError("Summary-ledger store read returned an invalid record")
    if (
        record.state != "PREPARED"
        or type(record.schema_version) is not int
        or record.schema_version != 1
        or record.prepared_id != requested_id
        or not _is_hash(record.prepared_id)
        or not _is_hash(record.payload_sha256)
        or not _is_hash(record.source_sha256)
        or not _is_hash(record.prepared_record_sha256)
        or not _is_hash(record.canonical_namespace_digest)
        or type(record.entries) is not tuple
        or not record.entries
        or type(record.source_bodies) is not tuple
        or not _valid_text(record.source_manifest_json)
        or _sha256_text(record.source_manifest_json) != record.source_sha256
    ):
        raise ValueError("Summary-ledger record identity, hashes, or manifest are invalid")

    head_data = _head_metadata(
        record.expected_head,
        record.branch,
        record.revision,
        head_type=store.RollingHistoryV2Head,
        branch_type=store.RollingHistoryV2Branch,
        revision_type=store.RollingHistoryV2Revision,
    )
    source_bodies: dict[tuple, object] = {}
    sessions: set[str] = set()
    for source in record.source_bodies:
        if type(source) is not store.RollingHistoryV2PreparedSummaryLedgerSourceBody:
            raise ValueError("Summary-ledger source body is invalid")
        key = _ledger_locator_key(
            source.locator,
            locator_type=store.RollingHistoryV2PreparedSummaryLedgerLocator,
            covered_through=covered_through,
        )
        if (
            key in source_bodies
            or source.role not in _ROLES
            or not _valid_utf8(source.body)
            or not _is_hash(source.body_sha256)
            or _sha256_text(source.body) != source.body_sha256
        ):
            raise ValueError("Summary-ledger source body role or hash is invalid")
        source_bodies[key] = source
        sessions.add(source.locator.session_id)

    referenced_sources: set[tuple] = set()
    sequence_to_locator: dict[int, tuple] = {}
    for entry in record.entries:
        if type(entry) is not store.RollingHistoryV2PreparedSummaryLedgerEntry:
            raise ValueError("Summary-ledger entry is invalid")
        source_key = _ledger_locator_key(
            entry.source_locator,
            locator_type=store.RollingHistoryV2PreparedSummaryLedgerLocator,
            covered_through=covered_through,
        )
        if source_key[3] != entry.source_sequence:
            raise ValueError("Summary-ledger entry sequence does not match its locator")
        source_body = source_bodies.get(source_key)
        if (
            source_body is None
            or entry.source_role not in _ROLES
            or source_body.role != entry.source_role
        ):
            raise ValueError("Summary-ledger entry role or source body is not verified")
        _record_sequence_locator(sequence_to_locator, source_key)
        referenced_sources.add(source_key)

        if entry.field is None:
            if entry.quote is not None or entry.resolution is not None:
                raise ValueError("Summary-ledger source-only entry has claim data")
            continue
        if (
            entry.field not in checkpoint_fields
            or entry.field == "completed"
            or not _valid_text(entry.quote)
            or entry.quote not in source_body.body
        ):
            raise ValueError("Summary-ledger claim is not grounded in its source body")

        resolution = entry.resolution
        if resolution is None:
            continue
        if (
            type(resolution) is not store.RollingHistoryV2PreparedSummaryLedgerResolution
            or resolution.action not in _RESOLUTION_ACTIONS
            or (resolution.action == "complete"
                and entry.field not in ("open_questions", "next_action"))
            or not _valid_text(resolution.evidence_text)
            or type(resolution.evidence_sequence) is not int
            or type(resolution.inherited_from_previous_summary) is not bool
            or resolution.inherited_from_previous_summary
            or resolution.evidence_sequence <= entry.source_sequence
            or resolution.evidence_source_role != "human"
        ):
            raise ValueError("Summary-ledger resolution evidence is invalid")
        evidence_key = _ledger_locator_key(
            resolution.evidence_locator,
            locator_type=store.RollingHistoryV2PreparedSummaryLedgerLocator,
            covered_through=covered_through,
        )
        if evidence_key[3] != resolution.evidence_sequence:
            raise ValueError("Summary-ledger evidence sequence does not match its locator")
        evidence_body = source_bodies.get(evidence_key)
        if evidence_body is None or evidence_body.role != "human":
            raise ValueError("Summary-ledger evidence source is not a verified human message")
        if (
            resolution.evidence_source_role != evidence_body.role
            or resolution.evidence_text not in evidence_body.body
        ):
            raise ValueError("Summary-ledger evidence text does not match its source body")
        _record_sequence_locator(sequence_to_locator, evidence_key)
        referenced_sources.add(evidence_key)

    if set(source_bodies) != referenced_sources:
        raise ValueError("Summary-ledger source-body manifest has missing or extra references")
    if len(sessions) > 1:
        raise ValueError("Summary-ledger references cross owner sessions")

    return {
        "schema_version": record.schema_version,
        "state": record.state,
        "prepared_id": record.prepared_id,
        "payload_sha256": record.payload_sha256,
        "source_sha256": record.source_sha256,
        "prepared_record_sha256": record.prepared_record_sha256,
        "canonical_namespace_digest": record.canonical_namespace_digest,
        "branch": head_data["branch"],
        "revision": head_data["revision"],
        "expected_head": head_data["expected_head"],
        "_session_ids": frozenset(sessions),
    }


def _head_metadata(
    head,
    branch,
    revision,
    *,
    head_type,
    branch_type,
    revision_type,
) -> dict:
    if (
        type(head) is not head_type
        or type(branch) is not branch_type
        or type(revision) is not revision_type
        or type(head.schema_version) is not int
        or type(branch.schema_version) is not int
        or type(revision.schema_version) is not int
        or head.schema_version != 2
        or branch.schema_version != 2
        or revision.schema_version != 2
    ):
        raise ValueError("Prepared summary branch, revision, or head is invalid")
    for value in (
        head.scope_sha256,
        head.branch_id,
        head.revision_id,
        head.revision_sha256,
        head.history_head_sha256,
        head.head_sha256,
        branch.scope_sha256,
        branch.branch_id,
        branch.created_revision_id,
        branch.created_revision_sha256,
        branch.branch_sha256,
        revision.scope_sha256,
        revision.branch_id,
        revision.revision_id,
        revision.revision_sha256,
        revision.history_head_sha256,
    ):
        if not _is_hash(value):
            raise ValueError("Prepared summary branch, revision, or head hash is invalid")
    if (
        type(head.revision_number) is not int
        or head.revision_number < 0
        or type(head.history_revision) is not int
        or head.history_revision < 0
        or type(revision.revision_number) is not int
        or revision.revision_number < 0
        or type(revision.history_revision) is not int
        or revision.history_revision < 0
        or head.scope_sha256 != branch.scope_sha256
        or head.scope_sha256 != revision.scope_sha256
        or head.branch_id != branch.branch_id
        or head.branch_id != revision.branch_id
        or head.revision_id != revision.revision_id
        or head.revision_sha256 != revision.revision_sha256
        or head.revision_number != revision.revision_number
        or head.history_revision != revision.history_revision
        or head.history_head_sha256 != revision.history_head_sha256
    ):
        raise ValueError("Prepared summary expected head does not match its branch and revision")
    return {
        "branch": {
            "schema_version": branch.schema_version,
            "scope_sha256": branch.scope_sha256,
            "branch_id": branch.branch_id,
            "created_revision_id": branch.created_revision_id,
            "created_revision_sha256": branch.created_revision_sha256,
            "branch_sha256": branch.branch_sha256,
        },
        "revision": {
            "schema_version": revision.schema_version,
            "scope_sha256": revision.scope_sha256,
            "branch_id": revision.branch_id,
            "revision_id": revision.revision_id,
            "revision_sha256": revision.revision_sha256,
            "revision_number": revision.revision_number,
            "history_revision": revision.history_revision,
            "history_head_sha256": revision.history_head_sha256,
        },
        "expected_head": {
            "schema_version": head.schema_version,
            "scope_sha256": head.scope_sha256,
            "branch_id": head.branch_id,
            "revision_id": head.revision_id,
            "revision_sha256": head.revision_sha256,
            "revision_number": head.revision_number,
            "history_revision": head.history_revision,
            "history_head_sha256": head.history_head_sha256,
            "head_sha256": head.head_sha256,
        },
    }


def _event_locator_key(locator, *, locator_type, covered_through: int) -> tuple:
    if type(locator) is not locator_type:
        raise ValueError("Prepared summary event locator has an invalid type")
    _validate_event_locator_fields(locator, covered_through=covered_through)
    return tuple(getattr(locator, field) for field in _EVENT_LOCATOR_FIELDS)


def _ledger_locator_key(locator, *, locator_type, covered_through: int) -> tuple:
    if type(locator) is not locator_type:
        raise ValueError("Prepared summary-ledger locator has an invalid type")
    _validate_event_locator_fields(locator, covered_through=covered_through)
    session_id = locator.session_id
    if (
        type(session_id) is not str
        or not 1 <= len(session_id) <= 128
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
    ):
        raise ValueError("Prepared summary-ledger owner session is invalid")
    return (locator.event_id, session_id) + tuple(
        getattr(locator, field) for field in _EVENT_LOCATOR_FIELDS[1:]
    )


def _validate_event_locator_fields(locator, *, covered_through: int) -> None:
    if (
        not _is_hash(locator.event_id)
        or type(locator.request_id) is not str
        or _REQUEST_ID.fullmatch(locator.request_id) is None
        or type(locator.sequence) is not int
        or not 1 <= locator.sequence <= covered_through
        or type(locator.version) is not int
        or locator.version != 1
        or not _is_hash(locator.content_sha256)
        or not _is_hash(locator.chain_sha256)
    ):
        raise ValueError("Prepared summary source locator fields are invalid")


def _parse_citation_reference(reference_json, *, covered_through: int) -> dict:
    if type(reference_json) is not str:
        raise ValueError("Grounded-summary citation reference is not text")
    try:
        encoded = reference_json.encode("utf-8", errors="strict")
        value = json.loads(reference_json, object_pairs_hook=_unique_object)
        if (
            len(encoded) > 1024
            or type(value) is not dict
            or tuple(value) != _LOCATOR_FIELDS
        ):
            raise ValueError("Grounded-summary citation reference shape is invalid")
        if (
            not _is_hash(value["event_id"])
            or type(value["session_id"]) is not str
            or not 1 <= len(value["session_id"]) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in value["session_id"])
            or type(value["request_id"]) is not str
            or _REQUEST_ID.fullmatch(value["request_id"]) is None
            or type(value["sequence"]) is not int
            or not 1 <= value["sequence"] <= covered_through
            or type(value["version"]) is not int
            or value["version"] != 1
            or not _is_hash(value["content_sha256"])
            or not _is_hash(value["chain_sha256"])
        ):
            raise ValueError("Grounded-summary citation reference fields are invalid")
        if _canonical_json(value) != reference_json:
            raise ValueError("Grounded-summary citation reference is not canonical")
        return value
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("Grounded-summary citation reference is invalid") from exc


def _require_reference_matches_event(reference: dict, locator) -> None:
    if any(reference[field] != getattr(locator, field) for field in _EVENT_LOCATOR_FIELDS):
        raise ValueError("Grounded-summary citation does not match its verified source locator")


def _record_sequence_locator(sequence_to_locator: dict[int, tuple], key: tuple) -> None:
    sequence = key[3]
    previous = sequence_to_locator.get(sequence)
    if previous is not None and previous != key:
        raise ValueError("Summary-ledger sequence is bound to conflicting locators")
    sequence_to_locator[sequence] = key


def _unique_object(pairs) -> dict:
    result = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("Duplicate or invalid JSON object key")
        result[key] = value
    return result


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8", errors="strict")).hexdigest()


def _is_hash(value) -> bool:
    return type(value) is str and _HASH.fullmatch(value) is not None


def _valid_text(value) -> bool:
    if type(value) is not str or not value.strip():
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


def _valid_utf8(value) -> bool:
    if type(value) is not str:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


def _require_prepared_id(value, record_kind: str) -> None:
    if not _is_hash(value):
        raise ValueError(f"Invalid {record_kind} prepared record ID")
