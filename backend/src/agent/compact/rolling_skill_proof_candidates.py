"""Build host-only rolling Skill candidates from live proof collector state.

This module is intentionally an adapter, not a persistence or compaction
integration. It only reads the active request-local collector's frozen host
snapshot. A rejected or absent candidate leaves the caller's original message
history untouched. The evidence boundary is the real LangChain
``on_chat_model_start.messages`` callback; it is not a provider or graph ACK.

Instruction candidates require the exact ``system_prompt`` argument retained
after ``create_agent`` succeeds and an exact match to the unique LangChain
callback SystemMessage. Callback evidence alone cannot substitute for the
factory input. Pre-save proof is host-only and does not imply provider ACK or
storage atomicity; a future store must enforce epochs in its own transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from ..skill_proof import (
    SkillInstructionPromptDelivery,
    SkillModelInputEvidence,
    SkillModelInputMatch,
    SkillPackagePin,
    SkillProofCollector,
    SkillProofContentCapture,
    SkillProofFactoryPromptPreSaveProof,
    SkillProofFactorySystemPromptEvidence,
    SkillProofHostContentSnapshot,
    SkillProofObservationSnapshot,
    SkillProofRequestBinding,
    SkillSourceEvidence,
    SkillToolResultEvidence,
    current_skill_proof_collector,
    validate_factory_system_prompt_snapshot_before_save,
)


_HASH_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SKILL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_CALL_ID_RE = re.compile(r"[^\x00-\x1f\x7f]{1,256}\Z", re.ASCII)
_MAX_SOURCE_BYTES = 96 * 1024
_MAX_RESULT_BYTES = 128 * 1024
_MAX_MODEL_INPUT_BYTES = 4 * 1024 * 1024
_RESULT_KEYS = frozenset({"success", "output", "data", "error", "metadata"})
_RESOURCE_DATA_KEYS = frozenset({
    "skill_id", "content_hash", "path", "stage", "success", "content",
})
_RESULT_METADATA_KEYS = frozenset({
    "tool_name", "duration_ms", "call_id", "timestamp", "kb_coverage_hint",
    "compacted", "hwrag_tool_event_ref", "hwrag_tool_observation_archived",
    "hwrag_tool_observation_ref",
})
_CALLBACK_TARGET = "langchain.on_chat_model_start.messages"


CandidateStage = Literal["request", "instructions", "resource"]


class SkillProofCandidateRejected(ValueError):
    """A host-only Skill candidate or its pre-save recheck failed closed."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SkillProofCandidateRejection:
    stage: CandidateStage
    code: str


@dataclass(frozen=True, slots=True)
class RollingSkillResourceProofCandidate:
    """One complete resource body tied to its exact observed callback input."""

    request_binding: SkillProofRequestBinding
    skill_id: str
    package_content_hash: str
    source_path: str
    source_call_id: str
    source_call_id_role: Literal["correlation_only"]
    content: str
    content_sha256: str
    content_utf8_byte_length: int
    observation_revision: int
    tool_name: Literal["read_skill_resource"]
    tool_result_canonical_json: str
    raw_result_sha256: str
    raw_result_utf8_byte_length: int
    callback_tool_message: str
    tool_message_sha256: str
    tool_message_utf8_byte_length: int
    model_input_sha256: str
    model_input_utf8_byte_length: int
    model_invocation_sequence: int
    delivery_target: Literal["langchain.on_chat_model_start.messages"]
    _collector: SkillProofCollector = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RollingSkillInstructionProofCandidate:
    """One complete instruction body tied to the exact factory prompt."""

    request_binding: SkillProofRequestBinding
    skill_id: str
    package_content_hash: str
    source_path: str
    source_call_id: str
    source_call_id_role: Literal["correlation_only"]
    content: str
    content_sha256: str
    content_utf8_byte_length: int
    observation_revision: int
    factory_system_prompt: str = field(repr=False)
    factory_system_prompt_sha256: str
    factory_system_prompt_utf8_byte_length: int
    factory_context_json_sha256: str
    instruction_delivery: SkillInstructionPromptDelivery
    callback_model_input_sha256: str
    callback_model_input_utf8_byte_length: int
    model_invocation_sequence: int
    factory_prompt_delivery_target: Literal[
        "agent_factory.create_agent.system_prompt"
    ]
    callback_delivery_target: Literal[
        "langchain.on_chat_model_start.messages"
    ]
    _collector: SkillProofCollector = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SkillProofCandidateBuildResult:
    """Host-only output; rejected candidates do not consume source messages."""

    observation_revision: int | None
    candidates: tuple[
        RollingSkillResourceProofCandidate | RollingSkillInstructionProofCandidate,
        ...,
    ]
    rejections: tuple[SkillProofCandidateRejection, ...]


@dataclass(frozen=True, slots=True)
class SkillProofCandidatePreSaveProof:
    """Structured same-collector evidence produced immediately before saving."""

    request_binding: SkillProofRequestBinding
    source_call_id: str
    observation_revision: int
    content_sha256: str
    tool_message_sha256: str
    model_invocation_sequence: int
    current_snapshot_match: Literal["verified"]
    evidence_boundary: Literal["host_collector_and_langchain_callback"]
    _collector: SkillProofCollector = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SkillProofInstructionCandidatePreSaveProof:
    """Fresh host-only proof; a future store must still check epochs atomically."""

    request_binding: SkillProofRequestBinding
    source_call_id: str
    observation_revision: int
    content_sha256: str
    factory_system_prompt_sha256: str
    factory_context_json_sha256: str
    callback_model_input_sha256: str
    callback_model_input_utf8_byte_length: int
    model_invocation_sequence: int
    current_snapshot_match: Literal["verified"]
    evidence_boundary: Literal["host_factory_prompt_and_langchain_callback"]
    _collector: SkillProofCollector = field(repr=False, compare=False)


def build_current_skill_proof_candidates() -> SkillProofCandidateBuildResult:
    """Return candidates derived only from the current live host collector.

    No caller-supplied snapshot, binding, body, callback input, or boolean is
    accepted as proof. The collector itself authenticates its active request
    scope when ``host_verifier_snapshot`` is called.
    """
    collector = current_skill_proof_collector()
    if type(collector) is not SkillProofCollector:
        return _rejected_result(None, "request", "active_collector_unavailable")
    try:
        host_snapshot = collector.host_verifier_snapshot()
    except Exception:
        return _rejected_result(None, "request", "host_snapshot_unavailable")
    return _build_from_host_snapshot(collector, host_snapshot)


def validate_skill_proof_candidate_before_save(
    candidate: object,
) -> SkillProofCandidatePreSaveProof | SkillProofInstructionCandidatePreSaveProof:
    """Re-read the same live collector and re-derive one candidate before save.

    Callers pass only the candidate they intend to save. The active collector
    and its fresh snapshot are obtained inside this function; stale revisions,
    changed request/delete/memory epochs, closed scopes, and altered candidate
    fields are rejected. This is a pre-save check, not a storage transaction:
    a future store must also enforce its own epoch check atomically.
    """
    if type(candidate) is RollingSkillInstructionProofCandidate:
        return _validate_instruction_candidate_before_save(candidate)
    if type(candidate) is not RollingSkillResourceProofCandidate:
        raise SkillProofCandidateRejected("host_candidate_required")
    collector = current_skill_proof_collector()
    if (type(collector) is not SkillProofCollector
            or collector is not candidate._collector
            or not collector.active):
        raise SkillProofCandidateRejected("active_collector_changed_or_closed")
    try:
        host_snapshot = collector.host_verifier_snapshot()
    except Exception as exc:
        raise SkillProofCandidateRejected("host_snapshot_unavailable") from exc

    rebuilt = _build_from_host_snapshot(collector, host_snapshot)
    observation = _snapshot_observation(host_snapshot)
    if observation is None:
        raise SkillProofCandidateRejected("host_snapshot_unavailable")
    if observation.observation_revision != candidate.observation_revision:
        raise SkillProofCandidateRejected("observation_revision_changed")
    if observation.request != candidate.request_binding:
        raise SkillProofCandidateRejected("request_or_epoch_changed")
    matching = [
        item for item in rebuilt.candidates
        if type(item) is RollingSkillResourceProofCandidate
        and item.source_call_id == candidate.source_call_id
        and item.skill_id == candidate.skill_id
        and item.package_content_hash == candidate.package_content_hash
        and item.source_path == candidate.source_path
    ]
    if len(matching) != 1 or matching[0] != candidate:
        raise SkillProofCandidateRejected("candidate_integrity_or_evidence_mismatch")
    body_digest = _text_digest(candidate.content)
    if body_digest != (candidate.content_sha256, candidate.content_utf8_byte_length):
        raise SkillProofCandidateRejected("candidate_body_integrity_mismatch")
    return SkillProofCandidatePreSaveProof(
        request_binding=candidate.request_binding,
        source_call_id=candidate.source_call_id,
        observation_revision=candidate.observation_revision,
        content_sha256=candidate.content_sha256,
        tool_message_sha256=candidate.tool_message_sha256,
        model_invocation_sequence=candidate.model_invocation_sequence,
        current_snapshot_match="verified",
        evidence_boundary="host_collector_and_langchain_callback",
        _collector=collector,
    )


def _build_from_host_snapshot(
    collector: SkillProofCollector,
    host_snapshot: object,
) -> SkillProofCandidateBuildResult:
    observation = _snapshot_observation(host_snapshot)
    revision = observation.observation_revision if observation is not None else None
    reason = _snapshot_rejection(host_snapshot, observation)
    if reason is not None:
        return _rejected_result(revision, "request", reason)
    assert observation is not None
    assert type(host_snapshot) is SkillProofHostContentSnapshot

    captures = host_snapshot.content_captures
    if type(captures) is not tuple:
        return _rejected_result(revision, "request", "content_capture_shape_invalid")
    candidates: list[
        RollingSkillResourceProofCandidate | RollingSkillInstructionProofCandidate
    ] = []
    rejections: list[SkillProofCandidateRejection] = []
    for capture in captures:
        if type(capture) is not SkillProofContentCapture:
            rejections.append(SkillProofCandidateRejection(
                "request", "content_capture_shape_invalid",
            ))
            continue
        if capture.stage == "instructions":
            try:
                candidates.append(_instruction_candidate(
                    collector=collector,
                    observation=observation,
                    host_snapshot=host_snapshot,
                    capture=capture,
                ))
            except SkillProofCandidateRejected as exc:
                rejections.append(SkillProofCandidateRejection("instructions", exc.code))
            except Exception:
                rejections.append(SkillProofCandidateRejection(
                    "instructions", "instruction_proof_unavailable",
                ))
            continue
        if capture.stage != "resource":
            rejections.append(SkillProofCandidateRejection(
                "resource", "unsupported_source_stage",
            ))
            continue
        try:
            candidates.append(_resource_candidate(
                collector=collector,
                observation=observation,
                capture=capture,
            ))
        except SkillProofCandidateRejected as exc:
            rejections.append(SkillProofCandidateRejection("resource", exc.code))
        except Exception:
            rejections.append(SkillProofCandidateRejection(
                "resource", "resource_proof_unavailable",
            ))
    return SkillProofCandidateBuildResult(
        observation_revision=revision,
        candidates=tuple(candidates),
        rejections=tuple(rejections),
    )


def _snapshot_observation(
    host_snapshot: object,
) -> SkillProofObservationSnapshot | None:
    if type(host_snapshot) is not SkillProofHostContentSnapshot:
        return None
    observation = host_snapshot.observation
    return observation if type(observation) is SkillProofObservationSnapshot else None


def _snapshot_rejection(
    host_snapshot: object,
    observation: SkillProofObservationSnapshot | None,
) -> str | None:
    if type(host_snapshot) is not SkillProofHostContentSnapshot or observation is None:
        return "host_snapshot_unavailable"
    if (observation.availability != "observed"
            or host_snapshot.content_status != "observed"):
        return "host_content_snapshot_not_observed"
    if (type(observation.observation_revision) is not int
            or observation.observation_revision < 0):
        return "observation_revision_invalid"
    if not _valid_binding(observation.request):
        return "request_binding_unavailable"
    if (observation.callback_status != "observed"
            or type(observation.unavailable_count) is not int
            or observation.unavailable_count != 0):
        return "callback_or_observation_incomplete"
    if type(observation.package_pins) is not tuple:
        return "package_pins_unavailable"
    pin_ids: set[str] = set()
    for pin in observation.package_pins:
        if type(pin) is not SkillPackagePin or not _valid_pin(pin):
            return "package_pin_invalid"
        if pin.skill_id in pin_ids:
            return "duplicate_package_pin"
        pin_ids.add(pin.skill_id)
    if type(observation.source_observations) is not tuple:
        return "source_observations_unavailable"
    source_ids: set[str] = set()
    for source in observation.source_observations:
        if type(source) is not SkillSourceEvidence:
            return "source_observation_invalid"
        if source.request != observation.request or source.source_status != "observed":
            return "source_observation_not_current"
        if (type(source.source_call_id) is not str
                or _CALL_ID_RE.fullmatch(source.source_call_id) is None
                or source.source_call_id in source_ids):
            return "source_call_missing_or_duplicate"
        source_ids.add(source.source_call_id)
    if type(observation.tool_result_observations) is not tuple:
        return "tool_result_observations_unavailable"
    result_ids: set[str] = set()
    for result in observation.tool_result_observations:
        if type(result) is not SkillToolResultEvidence:
            return "tool_result_observation_invalid"
        if result.request != observation.request or result.call_result_status != "observed":
            return "tool_result_observation_not_current"
        if (type(result.tool_call_id) is not str
                or _CALL_ID_RE.fullmatch(result.tool_call_id) is None
                or result.tool_call_id in result_ids):
            return "tool_result_call_missing_or_duplicate"
        result_ids.add(result.tool_call_id)
    if type(observation.model_input_observations) is not tuple:
        return "model_input_observations_unavailable"
    if not observation.model_input_observations:
        return "callback_input_not_observed"
    sequences: set[int] = set()
    for model_input in observation.model_input_observations:
        if (type(model_input) is not SkillModelInputEvidence
                or model_input.request != observation.request
                or model_input.status != "observed"
                or model_input.delivery_target != _CALLBACK_TARGET
                or type(model_input.model_invocation_sequence) is not int
                or model_input.model_invocation_sequence <= 0
                or model_input.model_invocation_sequence in sequences
                or not _valid_hash(model_input.model_input_sha256)
                or not _bounded_length(
                    model_input.model_input_utf8_byte_length, _MAX_MODEL_INPUT_BYTES,
                )
                or type(model_input.matches) is not tuple
                or type(model_input.matched_source_count) is not int
                or model_input.matched_source_count != len(model_input.matches)):
            return "callback_input_observation_invalid"
        sequences.add(model_input.model_invocation_sequence)
        if any(type(match) is not SkillModelInputMatch for match in model_input.matches):
            return "callback_input_match_invalid"
    captures = host_snapshot.content_captures
    if type(captures) is not tuple:
        return "content_capture_shape_invalid"
    observed_source_count = len(observation.source_observations)
    if len(captures) != observed_source_count:
        return "content_capture_incomplete"
    capture_ids: set[str] = set()
    for capture in captures:
        if (type(capture) is not SkillProofContentCapture
                or capture.request != observation.request
                or type(capture.source_call_id) is not str
                or capture.source_call_id not in source_ids
                or capture.source_call_id in capture_ids):
            return "content_capture_missing_or_duplicate"
        capture_ids.add(capture.source_call_id)
    if capture_ids != source_ids:
        return "content_capture_incomplete"
    return None


def _instruction_candidate(
    *,
    collector: SkillProofCollector,
    observation: SkillProofObservationSnapshot,
    host_snapshot: SkillProofHostContentSnapshot,
    capture: SkillProofContentCapture,
) -> RollingSkillInstructionProofCandidate:
    request = observation.request
    if not _valid_binding(request) or capture.request != request:
        raise SkillProofCandidateRejected("request_or_epoch_mismatch")
    if (capture.stage != "instructions"
            or capture.source_call_kind != "load_skill"
            or not _valid_instruction_source_identity(
                capture.skill_id,
                capture.package_content_hash,
                capture.source_call_id,
                capture.source_path,
            )):
        raise SkillProofCandidateRejected("instruction_source_identity_invalid")

    matching_sources = [
        source for source in observation.source_observations
        if source.source_call_id == capture.source_call_id
    ]
    if len(matching_sources) != 1:
        raise SkillProofCandidateRejected("unique_instruction_source_not_proven")
    source = matching_sources[0]
    if (source.request != request
            or source.source_status != "observed"
            or source.skill_id != capture.skill_id
            or source.package_content_hash != capture.package_content_hash
            or source.stage != "instructions"
            or source.source_path != capture.source_path
            or source.source_call_id != capture.source_call_id
            or source.source_call_kind != "load_skill"
            or source.source_call_id_role != "correlation_only"):
        raise SkillProofCandidateRejected("instruction_source_capture_mismatch")

    matching_pins = [
        pin for pin in observation.package_pins
        if pin.skill_id == capture.skill_id
    ]
    if (len(matching_pins) != 1
            or matching_pins[0].package_content_hash != capture.package_content_hash):
        raise SkillProofCandidateRejected("fixed_package_pin_mismatch")

    body_digest = _text_digest(capture.source_body)
    if (body_digest is None or body_digest[1] > _MAX_SOURCE_BYTES
            or type(capture.source_body_utf8_byte_length) is not int
            or body_digest != (
                capture.source_body_sha256,
                capture.source_body_utf8_byte_length,
            )
            or body_digest != (source.body_sha256, source.body_utf8_byte_length)):
        raise SkillProofCandidateRejected("complete_instruction_body_integrity_mismatch")

    factory_prompt = host_snapshot.factory_system_prompt
    if (type(factory_prompt) is not SkillProofFactorySystemPromptEvidence
            or factory_prompt.status != "observed"
            or factory_prompt.request != request
            or factory_prompt.delivery_target
            != "agent_factory.create_agent.system_prompt"):
        raise SkillProofCandidateRejected("exact_factory_system_prompt_not_proven")
    system_prompt = factory_prompt.system_prompt
    context_hash = factory_prompt.context_json_sha256
    context_length = factory_prompt.context_json_utf8_byte_length
    if type(system_prompt) is not str:
        raise SkillProofCandidateRejected("complete_factory_system_prompt_not_retained")
    prompt_digest = _text_digest(system_prompt)
    if (prompt_digest is None
            or prompt_digest[1] > _MAX_MODEL_INPUT_BYTES
            or prompt_digest != (
                factory_prompt.system_prompt_sha256,
                factory_prompt.system_prompt_utf8_byte_length,
            )
            or type(context_hash) is not str
            or not _valid_hash(context_hash)
            or not _bounded_length(context_length, _MAX_MODEL_INPUT_BYTES)
            or type(factory_prompt.model_invocation_sequence) is not int
            or factory_prompt.model_invocation_sequence <= 0
            or type(factory_prompt.delivered_bodies) is not tuple):
        raise SkillProofCandidateRejected("factory_system_prompt_integrity_mismatch")

    deliveries = factory_prompt.delivered_bodies
    if any(type(item) is not SkillInstructionPromptDelivery for item in deliveries):
        raise SkillProofCandidateRejected("factory_instruction_delivery_invalid")
    delivery_ids = [item.source_call_id for item in deliveries]
    delivery_skills = [(item.skill_id, item.content_hash) for item in deliveries]
    if (len(delivery_ids) != len(set(delivery_ids))
            or len(delivery_skills) != len(set(delivery_skills))):
        raise SkillProofCandidateRejected("factory_instruction_delivery_ambiguous")
    matching_deliveries = [
        item for item in deliveries
        if item.source_call_id == capture.source_call_id
    ]
    if len(matching_deliveries) != 1:
        raise SkillProofCandidateRejected("unique_factory_instruction_delivery_not_proven")
    delivery = matching_deliveries[0]
    if (delivery.skill_id != capture.skill_id
            or delivery.content_hash != capture.package_content_hash
            or delivery.instructions_sha256 != body_digest[0]
            or delivery.instructions_byte_length != body_digest[1]):
        raise SkillProofCandidateRejected("factory_instruction_delivery_mismatch")

    sequence = factory_prompt.model_invocation_sequence
    matching_inputs = [
        item for item in observation.model_input_observations
        if item.request == request
        and item.status == "observed"
        and item.delivery_target == _CALLBACK_TARGET
        and item.model_invocation_sequence == sequence
    ]
    if len(matching_inputs) != 1:
        raise SkillProofCandidateRejected("unique_factory_callback_not_proven")
    model_input = matching_inputs[0]
    model_input_hash = model_input.model_input_sha256
    model_input_length = model_input.model_input_utf8_byte_length
    matching_source_matches = [
        item for item in model_input.matches
        if item.source_call_id == capture.source_call_id
    ]
    if len(matching_source_matches) != 1:
        raise SkillProofCandidateRejected("unique_instruction_callback_match_not_proven")
    source_match = matching_source_matches[0]
    if (source_match.source_call_kind != "load_skill"
            or source_match.source_call_id_role != "correlation_only"
            or source_match.skill_id != capture.skill_id
            or source_match.package_content_hash != capture.package_content_hash
            or source_match.source_stage != "instructions"
            or source_match.body_sha256 != body_digest[0]
            or source_match.body_utf8_byte_length != body_digest[1]
            or source.model_input_status != "observed"
            or source.model_input_sha256 != model_input.model_input_sha256
            or source.model_input_utf8_byte_length
            != model_input_length
            or source.model_invocation_sequence != sequence
            or capture.model_invocation_sequence != sequence
            or not _valid_hash(model_input_hash)
            or not _bounded_length(model_input_length, _MAX_MODEL_INPUT_BYTES)):
        raise SkillProofCandidateRejected("factory_instruction_callback_mismatch")
    if (type(model_input_hash) is not str
            or type(model_input_length) is not int
            or type(context_hash) is not str):
        raise SkillProofCandidateRejected("factory_instruction_callback_invalid")

    return RollingSkillInstructionProofCandidate(
        request_binding=request,
        skill_id=capture.skill_id,
        package_content_hash=capture.package_content_hash,
        source_path=capture.source_path,
        source_call_id=capture.source_call_id,
        source_call_id_role="correlation_only",
        content=capture.source_body,
        content_sha256=body_digest[0],
        content_utf8_byte_length=body_digest[1],
        observation_revision=observation.observation_revision,
        factory_system_prompt=system_prompt,
        factory_system_prompt_sha256=prompt_digest[0],
        factory_system_prompt_utf8_byte_length=prompt_digest[1],
        factory_context_json_sha256=context_hash,
        instruction_delivery=delivery,
        callback_model_input_sha256=model_input_hash,
        callback_model_input_utf8_byte_length=model_input_length,
        model_invocation_sequence=sequence,
        factory_prompt_delivery_target=factory_prompt.delivery_target,
        callback_delivery_target=_CALLBACK_TARGET,
        _collector=collector,
    )


def _validate_instruction_candidate_before_save(
    candidate: RollingSkillInstructionProofCandidate,
) -> SkillProofInstructionCandidatePreSaveProof:
    collector = current_skill_proof_collector()
    if (type(collector) is not SkillProofCollector
            or collector is not candidate._collector
            or not collector.active):
        raise SkillProofCandidateRejected("active_collector_changed_or_closed")
    try:
        host_snapshot = collector.host_verifier_snapshot()
    except Exception as exc:
        raise SkillProofCandidateRejected("host_snapshot_unavailable") from exc

    observation = _snapshot_observation(host_snapshot)
    if (observation is None
            or observation.observation_revision != candidate.observation_revision
            or observation.request != candidate.request_binding):
        raise SkillProofCandidateRejected("request_or_epoch_changed")
    try:
        factory_proof = validate_factory_system_prompt_snapshot_before_save(
            host_snapshot,
        )
    except Exception as exc:
        raise SkillProofCandidateRejected(
            "factory_prompt_snapshot_rejected",
        ) from exc
    if (type(factory_proof) is not SkillProofFactoryPromptPreSaveProof
            or factory_proof._collector is not collector
            or factory_proof.request != candidate.request_binding
            or factory_proof.observation_revision != candidate.observation_revision
            or factory_proof.system_prompt_sha256
            != candidate.factory_system_prompt_sha256
            or factory_proof.context_json_sha256
            != candidate.factory_context_json_sha256
            or candidate.instruction_delivery not in factory_proof.delivered_bodies):
        raise SkillProofCandidateRejected("factory_prompt_pre_save_proof_mismatch")

    rebuilt = _build_from_host_snapshot(collector, host_snapshot)
    matching = [
        item for item in rebuilt.candidates
        if type(item) is RollingSkillInstructionProofCandidate
        and item.source_call_id == candidate.source_call_id
        and item.skill_id == candidate.skill_id
        and item.package_content_hash == candidate.package_content_hash
        and item.source_path == candidate.source_path
    ]
    if len(matching) != 1 or matching[0] != candidate:
        raise SkillProofCandidateRejected("candidate_integrity_or_evidence_mismatch")
    body_digest = _text_digest(candidate.content)
    if body_digest != (candidate.content_sha256, candidate.content_utf8_byte_length):
        raise SkillProofCandidateRejected("candidate_body_integrity_mismatch")
    prompt_digest = _text_digest(candidate.factory_system_prompt)
    if prompt_digest != (
        candidate.factory_system_prompt_sha256,
        candidate.factory_system_prompt_utf8_byte_length,
    ):
        raise SkillProofCandidateRejected("candidate_factory_prompt_integrity_mismatch")
    return SkillProofInstructionCandidatePreSaveProof(
        request_binding=candidate.request_binding,
        source_call_id=candidate.source_call_id,
        observation_revision=candidate.observation_revision,
        content_sha256=candidate.content_sha256,
        factory_system_prompt_sha256=candidate.factory_system_prompt_sha256,
        factory_context_json_sha256=candidate.factory_context_json_sha256,
        callback_model_input_sha256=candidate.callback_model_input_sha256,
        callback_model_input_utf8_byte_length=(
            candidate.callback_model_input_utf8_byte_length
        ),
        model_invocation_sequence=candidate.model_invocation_sequence,
        current_snapshot_match="verified",
        evidence_boundary="host_factory_prompt_and_langchain_callback",
        _collector=collector,
    )


def _resource_candidate(
    *,
    collector: SkillProofCollector,
    observation: SkillProofObservationSnapshot,
    capture: SkillProofContentCapture,
) -> RollingSkillResourceProofCandidate:
    request = observation.request
    if not _valid_binding(request) or capture.request != request:
        raise SkillProofCandidateRejected("request_or_epoch_mismatch")
    if (capture.source_call_kind != "read_skill_resource"
            or capture.stage != "resource"
            or not _valid_source_identity(
                capture.skill_id,
                capture.package_content_hash,
                capture.source_call_id,
                capture.source_path,
            )):
        raise SkillProofCandidateRejected("resource_source_identity_invalid")

    matching_sources = [
        source for source in observation.source_observations
        if source.source_call_id == capture.source_call_id
    ]
    if len(matching_sources) != 1:
        raise SkillProofCandidateRejected("unique_source_call_not_proven")
    source = matching_sources[0]
    if not _source_matches_capture(source, capture, request):
        raise SkillProofCandidateRejected("source_capture_mismatch")

    matching_pins = [
        pin for pin in observation.package_pins
        if pin.skill_id == capture.skill_id
    ]
    if (len(matching_pins) != 1
            or matching_pins[0].package_content_hash != capture.package_content_hash):
        raise SkillProofCandidateRejected("fixed_package_pin_mismatch")

    body_digest = _text_digest(capture.source_body)
    if (body_digest is None or body_digest[1] > _MAX_SOURCE_BYTES
            or "\x00" in capture.source_body
            or type(capture.source_body_utf8_byte_length) is not int
            or body_digest != (
                capture.source_body_sha256,
                capture.source_body_utf8_byte_length,
            )
            or body_digest != (source.body_sha256, source.body_utf8_byte_length)):
        raise SkillProofCandidateRejected("complete_source_body_integrity_mismatch")

    matching_results = [
        result for result in observation.tool_result_observations
        if result.tool_call_id == capture.source_call_id
    ]
    if len(matching_results) != 1:
        raise SkillProofCandidateRejected("unique_resource_result_not_proven")
    result = matching_results[0]
    if not _tool_result_matches_source(result, source, capture, request):
        raise SkillProofCandidateRejected("resource_result_binding_mismatch")

    raw_result = capture.tool_result_canonical_json
    if type(raw_result) is not str:
        raise SkillProofCandidateRejected("complete_tool_result_not_retained")
    raw_digest = _text_digest(raw_result)
    if (raw_digest is None or raw_digest[1] > _MAX_RESULT_BYTES
            or type(capture.tool_result_utf8_byte_length) is not int
            or raw_digest != (
                capture.tool_result_sha256,
                capture.tool_result_utf8_byte_length,
            )
            or raw_digest != (
                result.raw_result_sha256,
                result.raw_result_utf8_byte_length,
            )):
        raise SkillProofCandidateRejected("tool_result_integrity_mismatch")
    parsed_result, canonical_result = _parse_canonical_result(raw_result)
    if (parsed_result is None or canonical_result is None
            or not _valid_resource_result(
                parsed_result, capture.source_call_id, capture.skill_id,
                capture.package_content_hash, capture.source_path,
                capture.source_body,
            )):
        raise SkillProofCandidateRejected("exact_resource_result_mismatch")

    tool_message = capture.model_visible_text
    if type(tool_message) is not str:
        raise SkillProofCandidateRejected("complete_callback_tool_message_not_retained")
    message_digest = _text_digest(tool_message)
    if (message_digest is None or message_digest[1] > _MAX_RESULT_BYTES
            or type(capture.model_visible_text_utf8_byte_length) is not int
            or message_digest != (
                capture.model_visible_text_sha256,
                capture.model_visible_text_utf8_byte_length,
            )
            or message_digest != (
                result.tool_message_sha256,
                result.tool_message_utf8_byte_length,
            )):
        raise SkillProofCandidateRejected("callback_tool_message_integrity_mismatch")
    parsed_message, canonical_message = _parse_canonical_result(tool_message)
    if (parsed_message is None or canonical_message != canonical_result
            or not _valid_resource_result(
                parsed_message, capture.source_call_id, capture.skill_id,
                capture.package_content_hash, capture.source_path,
                capture.source_body,
            )):
        raise SkillProofCandidateRejected("callback_tool_message_result_mismatch")

    input_evidence, match = _unique_callback_match(
        observation, source, capture,
    )
    if (input_evidence is None or match is None
            or source.model_input_status != "observed"
            or result.model_input_status != "observed"
            or capture.model_invocation_sequence is None
            or source.model_invocation_sequence != capture.model_invocation_sequence
            or result.model_invocation_sequence != capture.model_invocation_sequence
            or type(capture.model_invocation_sequence) is not int
            or capture.model_invocation_sequence <= 0
            or input_evidence.model_invocation_sequence
            != capture.model_invocation_sequence
            or source.model_input_sha256 != input_evidence.model_input_sha256
            or result.model_input_sha256 != input_evidence.model_input_sha256
            or source.model_input_utf8_byte_length
            != input_evidence.model_input_utf8_byte_length
            or result.model_input_utf8_byte_length
            != input_evidence.model_input_utf8_byte_length):
        raise SkillProofCandidateRejected("unique_callback_invocation_not_proven")

    return RollingSkillResourceProofCandidate(
        request_binding=request,
        skill_id=capture.skill_id,
        package_content_hash=capture.package_content_hash,
        source_path=capture.source_path,
        source_call_id=capture.source_call_id,
        source_call_id_role="correlation_only",
        content=capture.source_body,
        content_sha256=body_digest[0],
        content_utf8_byte_length=body_digest[1],
        observation_revision=observation.observation_revision,
        tool_name="read_skill_resource",
        tool_result_canonical_json=raw_result,
        raw_result_sha256=raw_digest[0],
        raw_result_utf8_byte_length=raw_digest[1],
        callback_tool_message=tool_message,
        tool_message_sha256=message_digest[0],
        tool_message_utf8_byte_length=message_digest[1],
        model_input_sha256=input_evidence.model_input_sha256,
        model_input_utf8_byte_length=input_evidence.model_input_utf8_byte_length,
        model_invocation_sequence=input_evidence.model_invocation_sequence,
        delivery_target=_CALLBACK_TARGET,
        _collector=collector,
    )


def _source_matches_capture(
    source: SkillSourceEvidence,
    capture: SkillProofContentCapture,
    request: SkillProofRequestBinding,
) -> bool:
    return (
        type(source) is SkillSourceEvidence
        and source.request == request
        and source.source_status == "observed"
        and source.skill_id == capture.skill_id
        and source.package_content_hash == capture.package_content_hash
        and source.stage == "resource"
        and source.source_path == capture.source_path
        and source.source_call_id == capture.source_call_id
        and source.source_call_kind == "read_skill_resource"
        and source.source_call_id_role == "correlation_only"
        and _valid_hash(source.body_sha256)
        and _bounded_length(source.body_utf8_byte_length, _MAX_SOURCE_BYTES)
        and source.model_input_status == "observed"
        and _valid_hash(source.model_input_sha256)
        and _bounded_length(source.model_input_utf8_byte_length, _MAX_MODEL_INPUT_BYTES)
        and type(source.model_invocation_sequence) is int
        and source.model_invocation_sequence > 0
    )


def _tool_result_matches_source(
    result: SkillToolResultEvidence,
    source: SkillSourceEvidence,
    capture: SkillProofContentCapture,
    request: SkillProofRequestBinding,
) -> bool:
    return (
        type(result) is SkillToolResultEvidence
        and result.request == request
        and result.tool_name == "read_skill_resource"
        and result.tool_call_id == capture.source_call_id
        and result.tool_call_id_role == "correlation_only"
        and result.skill_id == capture.skill_id
        and result.package_content_hash == capture.package_content_hash
        and result.resource_path == capture.source_path
        and result.call_result_status == "observed"
        and _valid_hash(result.raw_result_sha256)
        and _bounded_length(result.raw_result_utf8_byte_length, _MAX_RESULT_BYTES)
        and _valid_hash(result.tool_message_sha256)
        and _bounded_length(result.tool_message_utf8_byte_length, _MAX_RESULT_BYTES)
        and result.body_sha256 == source.body_sha256
        and result.body_utf8_byte_length == source.body_utf8_byte_length
        and result.model_input_status == "observed"
        and _valid_hash(result.model_input_sha256)
        and _bounded_length(result.model_input_utf8_byte_length, _MAX_MODEL_INPUT_BYTES)
        and type(result.model_invocation_sequence) is int
        and result.model_invocation_sequence > 0
    )


def _unique_callback_match(
    observation: SkillProofObservationSnapshot,
    source: SkillSourceEvidence,
    capture: SkillProofContentCapture,
) -> tuple[SkillModelInputEvidence | None, SkillModelInputMatch | None]:
    pairs: list[tuple[SkillModelInputEvidence, SkillModelInputMatch]] = []
    for evidence in observation.model_input_observations:
        for match in evidence.matches:
            if match.source_call_id == capture.source_call_id:
                pairs.append((evidence, match))
    if len(pairs) != 1:
        return None, None
    evidence, match = pairs[0]
    expected = (
        match.source_call_id == source.source_call_id
        and match.source_call_kind == "read_skill_resource"
        and match.source_call_id_role == "correlation_only"
        and match.skill_id == capture.skill_id
        and match.package_content_hash == capture.package_content_hash
        and match.source_stage == "resource"
        and match.body_sha256 == source.body_sha256
        and match.body_utf8_byte_length == source.body_utf8_byte_length
    )
    same_sequence = [
        item for item in observation.model_input_observations
        if item.model_invocation_sequence == evidence.model_invocation_sequence
    ]
    if not expected or len(same_sequence) != 1:
        return None, None
    return evidence, match


def _parse_canonical_result(
    value: str,
) -> tuple[dict[str, object] | None, bytes | None]:
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if type(parsed) is not dict or not _json_value(parsed):
            return None, None
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None, None
    return parsed, canonical


def _valid_resource_result(
    result: dict[str, object],
    call_id: str,
    skill_id: str,
    package_hash: str,
    path: str,
    content: str,
) -> bool:
    data = result.get("data")
    metadata = result.get("metadata")
    return (
        set(result) == _RESULT_KEYS
        and result.get("success") is True
        and result.get("output") == "Skill resource loaded"
        and result.get("error") is None
        and type(data) is dict
        and set(data) == _RESOURCE_DATA_KEYS
        and data.get("success") is True
        and data.get("stage") == "resource"
        and data.get("skill_id") == skill_id
        and data.get("content_hash") == package_hash
        and data.get("path") == path
        and data.get("content") == content
        and type(metadata) is dict
        and {"tool_name", "call_id", "compacted"}.issubset(metadata)
        and set(metadata).issubset(_RESULT_METADATA_KEYS)
        and metadata.get("tool_name") == "read_skill_resource"
        and metadata.get("call_id") == call_id
        and metadata.get("compacted") is False
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _json_value(value: object, depth: int = 0) -> bool:
    if depth > 32:
        return False
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return value == value and value not in (float("inf"), float("-inf"))
    if type(value) is list:
        return all(_json_value(item, depth + 1) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_value(item, depth + 1)
                   for key, item in value.items())
    return False


def _valid_binding(value: object) -> bool:
    if type(value) is not SkillProofRequestBinding:
        return False
    memory_epoch = value.memory_clear_epoch
    return (
        type(value.principal_hash) is str
        and _valid_hash(value.principal_hash)
        and type(value.session_id) is str
        and 1 <= len(value.session_id) <= 128
        and value.session_id == value.session_id.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value.session_id)
        and type(value.request_id) is str
        and _REQUEST_ID_RE.fullmatch(value.request_id) is not None
        and type(value.session_delete_epoch) is str
        and (value.session_delete_epoch == ""
             or _REQUEST_ID_RE.fullmatch(value.session_delete_epoch) is not None)
        and (memory_epoch is None or (
            type(memory_epoch) is str
            and not any(ord(char) < 32 or ord(char) == 127 for char in memory_epoch)
        ))
    )


def _valid_pin(value: SkillPackagePin) -> bool:
    return (
        type(value.skill_id) is str
        and _SKILL_ID_RE.fullmatch(value.skill_id) is not None
        and _valid_hash(value.package_content_hash)
    )


def _valid_source_identity(
    skill_id: object,
    package_hash: object,
    source_call_id: object,
    resource_path: object,
) -> bool:
    return (
        type(skill_id) is str
        and _SKILL_ID_RE.fullmatch(skill_id) is not None
        and _valid_hash(package_hash)
        and type(source_call_id) is str
        and _CALL_ID_RE.fullmatch(source_call_id) is not None
        and bool(source_call_id.strip())
        and _valid_resource_path(resource_path)
    )


def _valid_instruction_source_identity(
    skill_id: object,
    package_hash: object,
    source_call_id: object,
    source_path: object,
) -> bool:
    return (
        type(skill_id) is str
        and _SKILL_ID_RE.fullmatch(skill_id) is not None
        and _valid_hash(package_hash)
        and type(source_call_id) is str
        and _CALL_ID_RE.fullmatch(source_call_id) is not None
        and bool(source_call_id.strip())
        and type(source_path) is str
        and 1 <= len(source_path) <= 512
        and not any(ord(char) < 32 or ord(char) == 127 for char in source_path)
    )


def _valid_resource_path(value: object) -> bool:
    if (type(value) is not str or not value or "\\" in value or ":" in value
            or value.startswith("/") or "\x00" in value):
        return False
    parts = value.split("/")
    return (
        len(parts) >= 2
        and parts[0] in {"references", "assets"}
        and all(part not in {"", ".", ".."} for part in parts)
        and all(part == unicodedata.normalize("NFC", part) for part in parts)
        and all(part == part.strip() and not part.endswith((" ", ".")) for part in parts)
        and not any(ord(char) < 32 or ord(char) == 127 or char in '<>"|?*'
                    for char in value)
    )


def _valid_hash(value: object) -> bool:
    return type(value) is str and _HASH_RE.fullmatch(value) is not None


def _bounded_length(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _text_digest(value: object) -> tuple[str, int] | None:
    if type(value) is not str:
        return None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _rejected_result(
    revision: int | None,
    stage: CandidateStage,
    code: str,
) -> SkillProofCandidateBuildResult:
    return SkillProofCandidateBuildResult(
        observation_revision=revision,
        candidates=(),
        rejections=(SkillProofCandidateRejection(stage, code),),
    )
