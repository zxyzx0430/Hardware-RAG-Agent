"""Build fail-closed candidates from complete manual Skill instructions.

This module performs no storage, filesystem, database, model, or publication
work. A host caller must inject a verifier that binds the request snapshot,
current identity/epochs, loaded Skill sources, and the exact system prompt that
is passed to ``create_agent``. The returned values are source candidates only;
they grant no permission and are not themselves an archive operation.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


_HASH_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SKILL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_MAX_SKILLS = 3
_MAX_CONTEXT_BYTES = 96 * 1024
_CONTEXT_ENTRY_KEYS = frozenset({"skill_id", "content_hash", "instructions"})


class SkillSnapshotRejected(ValueError):
    """A host Skill source did not satisfy the complete binding contract."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RequestBinding:
    """Request identity copied from the host snapshot and current host state."""

    principal_hash: str
    business_session_id: str
    request_id: str
    session_delete_epoch: str
    memory_clear_epoch: str | None


@dataclass(frozen=True, slots=True)
class PinnedSkillSnapshot:
    """Request-pinned package identity; ``content_hash`` is not a body hash."""

    skill_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class LoadedSkillInstructions:
    """Host-normalized result from ``load_skill`` for one selected Skill."""

    skill_id: str
    content_hash: str
    instructions: str
    stage: Literal["instructions"] = "instructions"


@dataclass(frozen=True, slots=True)
class RequestBindingProof:
    """Structured verifier output for the snapshot and its current identity."""

    snapshot_binding: RequestBinding
    current_binding: RequestBinding
    pinned_skills: tuple[PinnedSkillSnapshot, ...]


@dataclass(frozen=True, slots=True)
class SkillSourceProof:
    """Verifier output tying one complete load result to the pinned package."""

    request_binding: RequestBinding
    skill_id: str
    content_hash: str
    stage: Literal["instructions"]
    instructions_sha256: str
    instructions_byte_length: int
    version_check: Literal["matched_before_and_after_load"]
    body_check: Literal["complete_runtime_instructions"]


@dataclass(frozen=True, slots=True)
class DeliveredSkillBody:
    """One exact instruction body the verifier found in the model-facing prompt."""

    skill_id: str
    content_hash: str
    instructions_sha256: str
    instructions_byte_length: int


@dataclass(frozen=True, slots=True)
class ModelPromptProof:
    """Verifier output for the exact host prompt passed to agent construction."""

    request_binding: RequestBinding
    delivery_target: Literal["agent_factory.create_agent.system_prompt"]
    system_prompt_sha256: str
    context_json_sha256: str
    delivered_bodies: tuple[DeliveredSkillBody, ...]


@dataclass(frozen=True, slots=True)
class SkillInstructionHostProof:
    """All structured host evidence required before candidates can be emitted."""

    request: RequestBindingProof
    sources: tuple[SkillSourceProof, ...]
    delivery: ModelPromptProof


class HostSkillInstructionVerifier(Protocol):
    """Host-only verifier; return exact proof records, never an authorization bool.

    Implementations must verify that ``host_snapshot`` is the actual request
    snapshot; that current principal/session/request/session-epoch/memory-epoch
    still match; that each source came from that request's SkillsRuntime and
    matched its pinned package hash before and after loading; and that each
    complete body is in the exact ``system_prompt`` passed to ``create_agent``.
    A model/client-supplied identifier or a package hash alone is insufficient.
    """

    def verify(
        self,
        *,
        host_snapshot: object,
        request_binding: RequestBinding,
        current_binding: RequestBinding,
        pinned_skills: tuple[PinnedSkillSnapshot, ...],
        loaded_sources: tuple[LoadedSkillInstructions, ...],
        skills_context: tuple[dict[str, str], ...],
        system_prompt: str,
    ) -> SkillInstructionHostProof: ...


@dataclass(frozen=True, slots=True)
class SkillInstructionSnapshotCandidate:
    """Complete, request-bound Skill instructions with an independent body hash."""

    skill_id: str
    content_hash: str
    instructions: str
    instructions_sha256: str
    request_binding: RequestBinding


def build_skill_instruction_candidates(
    *,
    host_snapshot: object,
    request_binding: RequestBinding,
    current_binding: RequestBinding,
    pinned_skills: Sequence[PinnedSkillSnapshot],
    loaded_sources: Sequence[LoadedSkillInstructions],
    skills_context: Sequence[Mapping[str, object]],
    system_prompt: str,
    verifier: HostSkillInstructionVerifier,
) -> tuple[SkillInstructionSnapshotCandidate, ...]:
    """Convert only complete manual instructions proven in the final prompt.

    ``verifier`` is mandatory and must be implemented by trusted host code. It
    returns identity, source-version, body-completeness, and prompt-delivery
    proof records. Missing, malformed, stale, or mismatched proof fails closed.
    The function computes every body SHA-256 from the full UTF-8 instructions
    string; the package ``content_hash`` is retained separately as version data.
    """
    _validate_request_binding(request_binding)
    _validate_request_binding(current_binding)
    if request_binding != current_binding:
        raise SkillSnapshotRejected("request_identity_or_epoch_changed")

    pinned = _validate_pinned_skills(pinned_skills)
    sources = _validate_loaded_sources(loaded_sources, pinned)
    context = _validate_skills_context(skills_context, pinned, sources)
    context_json = _serialize_context(context)
    if type(system_prompt) is not str or not system_prompt.endswith(context_json):
        raise SkillSnapshotRejected("complete_skills_context_not_in_system_prompt")
    prompt_sha256 = _sha256_text(system_prompt, "invalid_system_prompt_text")
    context_sha256 = _sha256_text(context_json, "invalid_skills_context_text")

    verify = getattr(verifier, "verify", None)
    if not callable(verify):
        raise SkillSnapshotRejected("host_verifier_required")
    try:
        proof = verify(
            host_snapshot=host_snapshot,
            request_binding=request_binding,
            current_binding=current_binding,
            pinned_skills=pinned,
            loaded_sources=sources,
            skills_context=context,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        raise SkillSnapshotRejected("host_verification_failed") from exc

    _validate_host_proof(
        proof=proof,
        request_binding=request_binding,
        current_binding=current_binding,
        pinned_skills=pinned,
        sources=sources,
        prompt_sha256=prompt_sha256,
        context_sha256=context_sha256,
    )

    candidates = []
    for source in sources:
        body_sha256 = _sha256_text(source.instructions, "invalid_instruction_text")
        candidates.append(SkillInstructionSnapshotCandidate(
            skill_id=source.skill_id,
            content_hash=source.content_hash,
            instructions=source.instructions,
            instructions_sha256=body_sha256,
            request_binding=request_binding,
        ))
    return tuple(candidates)


def _validate_request_binding(binding: RequestBinding) -> None:
    if type(binding) is not RequestBinding:
        raise SkillSnapshotRejected("host_request_binding_required")
    if (type(binding.principal_hash) is not str or not binding.principal_hash
            or len(binding.principal_hash) > 256
            or type(binding.business_session_id) is not str
            or not 1 <= len(binding.business_session_id) <= 128
            or binding.business_session_id != binding.business_session_id.strip()
            or type(binding.request_id) is not str
            or _REQUEST_ID_RE.fullmatch(binding.request_id) is None
            or type(binding.session_delete_epoch) is not str
            or (binding.memory_clear_epoch is not None
                and (type(binding.memory_clear_epoch) is not str
                     or not binding.memory_clear_epoch))):
        raise SkillSnapshotRejected("invalid_host_request_binding")
    if any(
        ord(char) < 32 or ord(char) == 127
        for value in (binding.principal_hash, binding.business_session_id,
                      binding.session_delete_epoch, binding.memory_clear_epoch or "")
        for char in value
    ):
        raise SkillSnapshotRejected("invalid_host_request_binding")


def _validate_pinned_skills(
    values: Sequence[PinnedSkillSnapshot],
) -> tuple[PinnedSkillSnapshot, ...]:
    if type(values) not in (tuple, list) or not 1 <= len(values) <= _MAX_SKILLS:
        raise SkillSnapshotRejected("invalid_pinned_skill_count")
    result = tuple(values)
    for item in result:
        if (type(item) is not PinnedSkillSnapshot
                or type(item.skill_id) is not str
                or _SKILL_ID_RE.fullmatch(item.skill_id) is None
                or type(item.content_hash) is not str
                or _HASH_RE.fullmatch(item.content_hash) is None):
            raise SkillSnapshotRejected("invalid_pinned_skill_binding")
    if len({item.skill_id for item in result}) != len(result):
        raise SkillSnapshotRejected("duplicate_pinned_skill")
    return result


def _validate_loaded_sources(
    values: Sequence[LoadedSkillInstructions],
    pinned: tuple[PinnedSkillSnapshot, ...],
) -> tuple[LoadedSkillInstructions, ...]:
    if type(values) not in (tuple, list) or len(values) != len(pinned):
        raise SkillSnapshotRejected("incomplete_instruction_sources")
    result = tuple(values)
    total_bytes = 0
    for item, expected in zip(result, pinned, strict=True):
        if (type(item) is not LoadedSkillInstructions
                or item.stage != "instructions"
                or item.skill_id != expected.skill_id
                or item.content_hash != expected.content_hash
                or type(item.instructions) is not str
                or "\x00" in item.instructions):
            raise SkillSnapshotRejected("instruction_source_binding_mismatch")
        body_bytes = _utf8(item.instructions, "invalid_instruction_text")
        total_bytes += len(body_bytes)
        if total_bytes > _MAX_CONTEXT_BYTES:
            raise SkillSnapshotRejected("instruction_context_exceeds_limit")
    return result


def _validate_skills_context(
    values: Sequence[Mapping[str, object]],
    pinned: tuple[PinnedSkillSnapshot, ...],
    sources: tuple[LoadedSkillInstructions, ...],
) -> tuple[dict[str, str], ...]:
    if type(values) not in (tuple, list) or len(values) != len(pinned):
        raise SkillSnapshotRejected("incomplete_model_skills_context")
    context: list[dict[str, str]] = []
    for entry, expected, source in zip(values, pinned, sources, strict=True):
        if type(entry) is not dict or set(entry) != _CONTEXT_ENTRY_KEYS:
            raise SkillSnapshotRejected("invalid_model_skills_context_entry")
        if (entry.get("skill_id") != expected.skill_id
                or entry.get("content_hash") != expected.content_hash
                or entry.get("instructions") != source.instructions):
            raise SkillSnapshotRejected("model_skills_context_source_mismatch")
        context.append({
            "skill_id": expected.skill_id,
            "content_hash": expected.content_hash,
            "instructions": source.instructions,
        })
    return tuple(context)


def _serialize_context(context: tuple[dict[str, str], ...]) -> str:
    try:
        payload = json.dumps(
            {"selected_skills": list(context)},
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SkillSnapshotRejected("invalid_skills_context_serialization") from exc
    if len(_utf8(payload, "invalid_skills_context_text")) > _MAX_CONTEXT_BYTES:
        raise SkillSnapshotRejected("instruction_context_exceeds_limit")
    return payload


def _validate_host_proof(
    *,
    proof: object,
    request_binding: RequestBinding,
    current_binding: RequestBinding,
    pinned_skills: tuple[PinnedSkillSnapshot, ...],
    sources: tuple[LoadedSkillInstructions, ...],
    prompt_sha256: str,
    context_sha256: str,
) -> None:
    if type(proof) is not SkillInstructionHostProof:
        raise SkillSnapshotRejected("structured_host_proof_required")
    request_proof = proof.request
    if (type(request_proof) is not RequestBindingProof
            or request_proof.snapshot_binding != request_binding
            or request_proof.current_binding != current_binding
            or request_proof.snapshot_binding != request_proof.current_binding
            or type(request_proof.pinned_skills) is not tuple
            or request_proof.pinned_skills != pinned_skills):
        raise SkillSnapshotRejected("host_request_proof_mismatch")

    if type(proof.sources) is not tuple or len(proof.sources) != len(sources):
        raise SkillSnapshotRejected("host_source_proof_incomplete")
    for source_proof, source in zip(proof.sources, sources, strict=True):
        body = _utf8(source.instructions, "invalid_instruction_text")
        body_sha256 = hashlib.sha256(body).hexdigest()
        if (type(source_proof) is not SkillSourceProof
                or source_proof.request_binding != request_binding
                or source_proof.skill_id != source.skill_id
                or source_proof.content_hash != source.content_hash
                or source_proof.stage != "instructions"
                or source_proof.instructions_sha256 != body_sha256
                or type(source_proof.instructions_byte_length) is not int
                or source_proof.instructions_byte_length != len(body)
                or source_proof.version_check != "matched_before_and_after_load"
                or source_proof.body_check != "complete_runtime_instructions"):
            raise SkillSnapshotRejected("host_source_proof_mismatch")

    delivery = proof.delivery
    expected_bodies = tuple(
        DeliveredSkillBody(
            skill_id=source.skill_id,
            content_hash=source.content_hash,
            instructions_sha256=hashlib.sha256(
                _utf8(source.instructions, "invalid_instruction_text")
            ).hexdigest(),
            instructions_byte_length=len(_utf8(source.instructions, "invalid_instruction_text")),
        )
        for source in sources
    )
    if (type(delivery) is not ModelPromptProof
            or delivery.request_binding != request_binding
            or delivery.delivery_target != "agent_factory.create_agent.system_prompt"
            or delivery.system_prompt_sha256 != prompt_sha256
            or delivery.context_json_sha256 != context_sha256
            or type(delivery.delivered_bodies) is not tuple
            or delivery.delivered_bodies != expected_bodies):
        raise SkillSnapshotRejected("host_model_delivery_proof_mismatch")


def _sha256_text(value: str, error_code: str) -> str:
    return hashlib.sha256(_utf8(value, error_code)).hexdigest()


def _utf8(value: str, error_code: str) -> bytes:
    if type(value) is not str:
        raise SkillSnapshotRejected(error_code)
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SkillSnapshotRejected(error_code) from exc
