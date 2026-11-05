"""Build an immutable evidence payload from rolling-summary archive candidates.

This is a pure adapter for host review. Its output is neither a persistent
ledger nor an authorization token. A locator does not authenticate a source or
grant access to it. This module does not reread source bodies, verify source
roles, establish quote meaning, or prove that a tool or action completed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
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
_LOCATOR_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_LOCATOR_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_LOCATOR_BYTE_LIMIT = 1024
_RESOLUTION_ACTIONS = ("replace", "complete", "revoke")


@dataclass(frozen=True, slots=True)
class RollingSummaryLedgerLocator:
    """The exact seven-field rolling-history locator projection.

    ``canonical_json`` is a deterministic serialization of these seven fields;
    it remains a locator only and is not an authentication credential.
    """

    event_id: str
    session_id: str
    request_id: str
    sequence: int
    version: int
    content_sha256: str
    chain_sha256: str

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            {field: getattr(self, field) for field in _LOCATOR_FIELDS},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class RollingSummaryLedgerResolutionEvidence:
    """A preserved later-source relation attached to one archived claim."""

    action: Literal["replace", "complete", "revoke"]
    evidence_text: str
    evidence_locator: RollingSummaryLedgerLocator
    evidence_sequence: int
    inherited_from_previous_summary: bool


@dataclass(frozen=True, slots=True)
class RollingSummaryLedgerEntry:
    """One candidate, in input order, including source-only candidates.

    ``field`` and ``quote`` are both absent for a source-only entry. A claim
    entry keeps both values and may carry its optional resolution relation.
    """

    source_sequence: int
    source_locator: RollingSummaryLedgerLocator
    field: str | None
    quote: str | None
    resolution: RollingSummaryLedgerResolutionEvidence | None


@dataclass(frozen=True, slots=True)
class RollingSummaryLedgerBridgePayload:
    """Immutable, scope-bound evidence candidates awaiting host review."""

    host_identity: str
    scope_id: str
    entries: tuple[RollingSummaryLedgerEntry, ...]


def build_rolling_summary_ledger_bridge_payload(
    result: RollingSummaryResult,
    *,
    expected_binding: RollingHostBinding | None,
) -> RollingSummaryLedgerBridgePayload | None:
    """Return a complete candidate payload, or ``None`` for any invalid input.

    ``expected_binding`` must come from the caller's already authenticated host
    context. Candidate identity and scope are checked against that binding;
    locators are checked for canonical shape, sequence, and shared session
    consistency. No source manifest is available on this binding, so this
    adapter cannot authenticate locator membership. It preserves every
    candidate in order and rejects the whole result on missing, conflicting,
    or noncanonical data. A downstream store must reread and verify source
    records before use.
    """
    from src.agent.compact.rolling_summarizer import (
        GroundedClaim,
        RollingArchiveCandidate,
        RollingCitation,
        RollingHostBinding,
        RollingSummary,
        RollingSummaryResult,
    )
    from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS

    if (
        type(result) is not RollingSummaryResult
        or type(result.status) is not str
        or result.status != "accepted"
        or result.reason is not None
        or type(result.summary) is not RollingSummary
        or type(result.summary.covered_through) is not int
        or result.summary.covered_through < 0
        or type(result.summary.resolution_records) is not tuple
        or result.summary.resolution_records
        or type(result.archive_candidates) is not tuple
        or type(expected_binding) is not RollingHostBinding
        or type(expected_binding.host_identity) is not str
        or not _valid_text(expected_binding.host_identity)
        or type(expected_binding.scope_id) is not str
        or not _valid_text(expected_binding.scope_id)
    ):
        return None

    entries: list[RollingSummaryLedgerEntry] = []
    payload_session_id: str | None = None
    for candidate in result.archive_candidates:
        if type(candidate) is not RollingArchiveCandidate:
            return None
        if (
            type(candidate.host_identity) is not str
            or candidate.host_identity != expected_binding.host_identity
            or type(candidate.scope_id) is not str
            or candidate.scope_id != expected_binding.scope_id
            or type(candidate.source_sequence) is not int
            or candidate.source_sequence < 1
            or candidate.source_sequence > result.summary.covered_through
            or type(candidate.source_reference_json) is not str
        ):
            return None

        source_locator = _parse_canonical_locator(candidate.source_reference_json)
        if (
            source_locator is None
            or source_locator.sequence != candidate.source_sequence
        ):
            return None
        if payload_session_id is None:
            payload_session_id = source_locator.session_id
        elif source_locator.session_id != payload_session_id:
            return None

        has_field = candidate.field is not None
        has_quote = candidate.quote is not None
        if has_field != has_quote:
            return None
        if not has_field:
            if (
                candidate.resolution_action is not None
                or candidate.resolution_evidence is not None
            ):
                return None
            entries.append(
                RollingSummaryLedgerEntry(
                    source_sequence=candidate.source_sequence,
                    source_locator=source_locator,
                    field=None,
                    quote=None,
                    resolution=None,
                ),
            )
            continue

        if (
            type(candidate.field) is not str
            or candidate.field not in CHECKPOINT_FIELDS
            or candidate.field == "completed"
            or not _valid_text(candidate.quote)
        ):
            return None

        has_action = candidate.resolution_action is not None
        has_evidence = candidate.resolution_evidence is not None
        if has_action != has_evidence:
            return None

        resolution: RollingSummaryLedgerResolutionEvidence | None = None
        if has_action:
            if (
                type(candidate.resolution_action) is not str
                or candidate.resolution_action not in _RESOLUTION_ACTIONS
                or (
                    candidate.resolution_action == "complete"
                    and candidate.field not in ("open_questions", "next_action")
                )
                or type(candidate.resolution_evidence) is not GroundedClaim
            ):
                return None

            evidence = candidate.resolution_evidence
            citation = evidence.citation
            if (
                not _valid_text(evidence.text)
                or type(citation) is not RollingCitation
                or type(citation.sequence) is not int
                or citation.sequence <= candidate.source_sequence
                or citation.sequence > result.summary.covered_through
                or type(citation.reference_json) is not str
                or type(citation.inherited_from_previous_summary) is not bool
                or citation.inherited_from_previous_summary
            ):
                return None

            evidence_locator = _parse_canonical_locator(citation.reference_json)
            if (
                evidence_locator is None
                or evidence_locator.sequence != citation.sequence
                or evidence_locator.session_id != payload_session_id
            ):
                return None

            resolution = RollingSummaryLedgerResolutionEvidence(
                action=candidate.resolution_action,
                evidence_text=evidence.text,
                evidence_locator=evidence_locator,
                evidence_sequence=citation.sequence,
                inherited_from_previous_summary=citation.inherited_from_previous_summary,
            )

        entries.append(
            RollingSummaryLedgerEntry(
                source_sequence=candidate.source_sequence,
                source_locator=source_locator,
                field=candidate.field,
                quote=candidate.quote,
                resolution=resolution,
            ),
        )

    return RollingSummaryLedgerBridgePayload(
        host_identity=expected_binding.host_identity,
        scope_id=expected_binding.scope_id,
        entries=tuple(entries),
    )


def _parse_canonical_locator(reference_json: str) -> RollingSummaryLedgerLocator | None:
    if type(reference_json) is not str:
        return None
    try:
        reference = json.loads(reference_json)
        if (
            type(reference) is not dict
            or set(reference) != set(_LOCATOR_FIELDS)
            or any(type(key) is not str for key in reference)
        ):
            return None

        event_id = reference["event_id"]
        session_id = reference["session_id"]
        request_id = reference["request_id"]
        sequence = reference["sequence"]
        version = reference["version"]
        content_sha256 = reference["content_sha256"]
        chain_sha256 = reference["chain_sha256"]
        if (
            type(event_id) is not str
            or _LOCATOR_HASH.fullmatch(event_id) is None
            or type(session_id) is not str
            or not 1 <= len(session_id) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
            or type(request_id) is not str
            or _LOCATOR_REQUEST_ID.fullmatch(request_id) is None
            or type(sequence) is not int
            or sequence < 1
            or type(version) is not int
            or version != 1
            or type(content_sha256) is not str
            or _LOCATOR_HASH.fullmatch(content_sha256) is None
            or type(chain_sha256) is not str
            or _LOCATOR_HASH.fullmatch(chain_sha256) is None
        ):
            return None

        canonical = json.dumps(
            {field: reference[field] for field in _LOCATOR_FIELDS},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if (
            canonical != reference_json
            or len(canonical.encode("utf-8", errors="strict")) > _LOCATOR_BYTE_LIMIT
        ):
            return None

        return RollingSummaryLedgerLocator(
            event_id=event_id,
            session_id=session_id,
            request_id=request_id,
            sequence=sequence,
            version=version,
            content_sha256=content_sha256,
            chain_sha256=chain_sha256,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        return None


def _valid_text(value: object) -> bool:
    if type(value) is not str or not value.strip():
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True
