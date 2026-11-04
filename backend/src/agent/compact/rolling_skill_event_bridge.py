"""Prepare host-verified Skill bodies as rolling-history events.

This bridge only prepares an in-memory event. It does not write history, call a
store, allocate a locator, acknowledge delivery, or authorize a later commit.
Callers must re-run :func:`validate_prepared_skill_event_before_transaction`
inside the owner and memory-epoch transaction immediately before their own
atomic epoch check and write.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, TypeAlias

from .rolling_skill_proof_candidates import (
    RollingSkillInstructionProofCandidate,
    RollingSkillResourceProofCandidate,
    SkillProofCandidatePreSaveProof,
    SkillProofCandidateRejected,
    SkillProofInstructionCandidatePreSaveProof,
    validate_skill_proof_candidate_before_save,
)
from ..skill_proof import SkillProofRequestBinding


SkillEventCandidate: TypeAlias = (
    RollingSkillInstructionProofCandidate | RollingSkillResourceProofCandidate
)
SkillEventProof: TypeAlias = (
    SkillProofCandidatePreSaveProof | SkillProofInstructionCandidatePreSaveProof
)
SkillEventType: TypeAlias = Literal["skill_instruction", "skill_resource"]


class RollingSkillEventBridgeRejected(ValueError):
    """A host candidate or prepared event failed closed validation."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RollingSkillEventProvenance:
    """Source identity kept separate from the original Skill body."""

    request_binding: SkillProofRequestBinding
    stage: Literal["instructions", "resource"]
    skill_id: str
    package_content_hash: str
    source_path: str
    source_call_id: str
    source_call_id_role: Literal["correlation_only"]
    observation_revision: int
    content_sha256: str
    content_utf8_byte_length: int


@dataclass(frozen=True, slots=True)
class PreparedRollingSkillEvent:
    """In-memory event plus the exact candidate and proof that produced it.

    ``content`` is the candidate's original string, without normalization,
    summarization, prompt substitution, or metadata wrapping. ``proof`` is a
    host pre-save proof only; it is not a durable receipt or commit token.
    """

    event_type: SkillEventType
    content: str
    provenance: RollingSkillEventProvenance
    candidate: SkillEventCandidate
    proof: SkillEventProof


def prepare_rolling_skill_event(candidate: object) -> PreparedRollingSkillEvent:
    """Revalidate one exact host candidate and prepare its original body.

    Mapping inputs, subclasses, client payloads, and model-produced dictionaries
    are rejected. Validation re-reads the live host collector and does not
    consume or mutate the caller's message history.
    """
    if not _is_candidate(candidate):
        raise RollingSkillEventBridgeRejected("host_candidate_required")

    proof = _recheck_candidate(candidate)
    _require_proof_binding(candidate, proof)
    digest, byte_length = _candidate_body_commitment(candidate)
    if (digest, byte_length) != (
        candidate.content_sha256,
        candidate.content_utf8_byte_length,
    ):
        raise RollingSkillEventBridgeRejected("candidate_body_integrity_mismatch")

    event_type, stage = _event_kind(candidate)
    return PreparedRollingSkillEvent(
        event_type=event_type,
        content=candidate.content,
        provenance=RollingSkillEventProvenance(
            request_binding=candidate.request_binding,
            stage=stage,
            skill_id=candidate.skill_id,
            package_content_hash=candidate.package_content_hash,
            source_path=candidate.source_path,
            source_call_id=candidate.source_call_id,
            source_call_id_role=candidate.source_call_id_role,
            observation_revision=candidate.observation_revision,
            content_sha256=digest,
            content_utf8_byte_length=byte_length,
        ),
        candidate=candidate,
        proof=proof,
    )


def validate_prepared_skill_event_before_transaction(
    prepared: object,
) -> SkillEventProof:
    """Recheck the bound candidate immediately before a caller's transaction.

    This repeats the host validator and checks that the prepared event still
    carries the same candidate, proof, source identity, and exact UTF-8 body.
    The caller must separately enforce owner and memory epochs atomically in
    its transaction; a successful return is not commit authorization.
    """
    if type(prepared) is not PreparedRollingSkillEvent:
        raise RollingSkillEventBridgeRejected("prepared_host_event_required")
    candidate = prepared.candidate
    if not _is_candidate(candidate):
        raise RollingSkillEventBridgeRejected("host_candidate_required")

    expected_event_type, _ = _event_kind(candidate)
    if prepared.event_type != expected_event_type:
        raise RollingSkillEventBridgeRejected("prepared_event_type_mismatch")
    if type(prepared.content) is not str or prepared.content is not candidate.content:
        raise RollingSkillEventBridgeRejected("prepared_body_binding_mismatch")
    if prepared.provenance != _provenance_for(candidate):
        raise RollingSkillEventBridgeRejected("prepared_provenance_mismatch")

    _require_proof_binding(candidate, prepared.proof)
    digest, byte_length = _candidate_body_commitment(candidate)
    if (digest, byte_length) != (
        candidate.content_sha256,
        candidate.content_utf8_byte_length,
    ):
        raise RollingSkillEventBridgeRejected("candidate_body_integrity_mismatch")
    if prepared.provenance.content_sha256 != digest or (
        prepared.provenance.content_utf8_byte_length != byte_length
    ):
        raise RollingSkillEventBridgeRejected("prepared_body_commitment_mismatch")

    fresh_proof = _recheck_candidate(candidate)
    _require_proof_binding(candidate, fresh_proof)
    if not _same_proof(prepared.proof, fresh_proof):
        raise RollingSkillEventBridgeRejected("prepared_proof_is_stale")
    return fresh_proof


def _is_candidate(value: object) -> bool:
    return type(value) in (
        RollingSkillInstructionProofCandidate,
        RollingSkillResourceProofCandidate,
    )


def _event_kind(
    candidate: SkillEventCandidate,
) -> tuple[SkillEventType, Literal["instructions", "resource"]]:
    if type(candidate) is RollingSkillInstructionProofCandidate:
        return "skill_instruction", "instructions"
    if type(candidate) is RollingSkillResourceProofCandidate:
        return "skill_resource", "resource"
    raise RollingSkillEventBridgeRejected("host_candidate_required")


def _provenance_for(candidate: SkillEventCandidate) -> RollingSkillEventProvenance:
    _, stage = _event_kind(candidate)
    digest, byte_length = _candidate_body_commitment(candidate)
    return RollingSkillEventProvenance(
        request_binding=candidate.request_binding,
        stage=stage,
        skill_id=candidate.skill_id,
        package_content_hash=candidate.package_content_hash,
        source_path=candidate.source_path,
        source_call_id=candidate.source_call_id,
        source_call_id_role=candidate.source_call_id_role,
        observation_revision=candidate.observation_revision,
        content_sha256=digest,
        content_utf8_byte_length=byte_length,
    )


def _candidate_body_commitment(candidate: SkillEventCandidate) -> tuple[str, int]:
    if type(candidate.content) is not str:
        raise RollingSkillEventBridgeRejected("candidate_body_not_text")
    try:
        raw_utf8 = candidate.content.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise RollingSkillEventBridgeRejected("candidate_body_not_strict_utf8") from exc
    return hashlib.sha256(raw_utf8).hexdigest(), len(raw_utf8)


def _recheck_candidate(candidate: SkillEventCandidate) -> SkillEventProof:
    try:
        return validate_skill_proof_candidate_before_save(candidate)
    except SkillProofCandidateRejected as exc:
        raise RollingSkillEventBridgeRejected(
            f"candidate_pre_save_rejected:{exc.code}",
        ) from exc
    except Exception as exc:
        raise RollingSkillEventBridgeRejected(
            "candidate_pre_save_unavailable",
        ) from exc


def _require_proof_binding(
    candidate: SkillEventCandidate,
    proof: object,
) -> None:
    if type(candidate) is RollingSkillResourceProofCandidate:
        valid = (
            type(proof) is SkillProofCandidatePreSaveProof
            and proof.request_binding == candidate.request_binding
            and proof.source_call_id == candidate.source_call_id
            and proof.observation_revision == candidate.observation_revision
            and proof.content_sha256 == candidate.content_sha256
            and proof.tool_message_sha256 == candidate.tool_message_sha256
            and proof.model_invocation_sequence == candidate.model_invocation_sequence
            and proof.current_snapshot_match == "verified"
            and proof.evidence_boundary == "host_collector_and_langchain_callback"
            and proof._collector is candidate._collector
        )
    elif type(candidate) is RollingSkillInstructionProofCandidate:
        valid = (
            type(proof) is SkillProofInstructionCandidatePreSaveProof
            and proof.request_binding == candidate.request_binding
            and proof.source_call_id == candidate.source_call_id
            and proof.observation_revision == candidate.observation_revision
            and proof.content_sha256 == candidate.content_sha256
            and proof.factory_system_prompt_sha256
            == candidate.factory_system_prompt_sha256
            and proof.factory_context_json_sha256
            == candidate.factory_context_json_sha256
            and proof.callback_model_input_sha256
            == candidate.callback_model_input_sha256
            and proof.callback_model_input_utf8_byte_length
            == candidate.callback_model_input_utf8_byte_length
            and proof.model_invocation_sequence == candidate.model_invocation_sequence
            and proof.current_snapshot_match == "verified"
            and proof.evidence_boundary
            == "host_factory_prompt_and_langchain_callback"
            and proof._collector is candidate._collector
        )
    else:
        valid = False
    if not valid:
        raise RollingSkillEventBridgeRejected("candidate_proof_binding_mismatch")


def _same_proof(left: SkillEventProof, right: SkillEventProof) -> bool:
    if type(left) is not type(right):
        return False
    if left != right:
        return False
    return left._collector is right._collector
