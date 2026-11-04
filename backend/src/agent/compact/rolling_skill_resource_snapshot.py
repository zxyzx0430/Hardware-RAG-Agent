"""Build fail-closed candidates from model-visible Skill resource results.

This module is a pure host-side transformer. It performs no storage, database,
filesystem, network, model, or publication work. A trusted host verifier must
prove the request scope, pinned package, exact resource read, ToolRouter return,
ToolMessage binding, and delivery of that ToolMessage to the next model call.
Without that proof, no candidate is returned.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol


_HASH_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SKILL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_TOOL_CALL_ID_RE = re.compile(r"[^\x00-\x1f\x7f]{1,256}\Z", re.ASCII)
_MAX_RESOURCE_BYTES = 96 * 1024
_MAX_RESULT_BYTES = 128 * 1024
_RESULT_KEYS = frozenset({"success", "output", "data", "error", "metadata"})
_RESOURCE_DATA_KEYS = frozenset({
    "skill_id", "content_hash", "path", "stage", "success", "content",
})
_METADATA_KEYS = frozenset({
    "tool_name", "duration_ms", "call_id", "timestamp", "kb_coverage_hint",
    "compacted", "hwrag_tool_event_ref", "hwrag_tool_observation_archived",
    "hwrag_tool_observation_ref",
})


class SkillResourceSnapshotRejected(ValueError):
    """A Skill resource source or host proof failed the binding contract."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ResourceRequestBinding:
    """Request scope copied from host-pinned identity and lifecycle state."""

    principal_hash: str
    session_id: str
    request_id: str
    session_delete_epoch: str


@dataclass(frozen=True, slots=True)
class PinnedSkillPackage:
    """The package version selected by the host for this request."""

    skill_id: str
    package_content_hash: str


@dataclass(frozen=True, slots=True)
class LoadedSkillResource:
    """Host-normalized output from one successful SkillsRuntime resource read."""

    skill_id: str
    package_content_hash: str
    resource_path: str
    content: str


@dataclass(frozen=True, slots=True)
class ExpectedToolCall:
    """The host-observed read_skill_resource call to bind to its ToolMessage.

    ``tool_call_id`` is only a correlation token. It is never used as request,
    user, session, authorization, or package identity. The host verifier must
    independently find this exact call in the next model input and verify its
    name and complete argument object against these values.
    """

    tool_name: Literal["read_skill_resource"]
    tool_call_id: str
    skill_id: str
    resource_path: str


@dataclass(frozen=True, slots=True)
class RequestBindingProof:
    """Verifier evidence for the pinned request and its current host scope."""

    snapshot_binding: ResourceRequestBinding
    current_binding: ResourceRequestBinding
    pinned_skill: PinnedSkillPackage


@dataclass(frozen=True, slots=True)
class ResourceReadProof:
    """Verifier evidence that the full text came from the pinned package."""

    request_binding: ResourceRequestBinding
    skill_id: str
    package_content_hash: str
    resource_path: str
    content_sha256: str
    content_byte_length: int
    version_check: Literal["matched_before_and_after_read"]
    path_check: Literal["exact_package_relative_resource"]
    body_check: Literal["complete_utf8_resource"]


@dataclass(frozen=True, slots=True)
class ToolCallBindingProof:
    """Verifier evidence joining the host dispatch, raw return, and ToolMessage."""

    request_binding: ResourceRequestBinding
    tool_name: Literal["read_skill_resource"]
    tool_call_id: str
    matched_ai_tool_call_count: int
    actual_call_skill_id: str
    actual_call_resource_path: str
    actual_call_argument_keys: frozenset[str]
    tool_call_id_role: Literal["correlation_only"]
    source_check: Literal["request_local_skills_runtime_and_tool_router"]
    call_check: Literal["unique_ai_call_name_and_exact_skill_id_path_args"]
    raw_result_sha256: str
    raw_result_byte_length: int
    tool_message_sha256: str
    tool_message_byte_length: int
    message_check: Literal["actual_success_tool_message"]


@dataclass(frozen=True, slots=True)
class ModelInputProof:
    """Verifier evidence that this exact message entered the next model input."""

    request_binding: ResourceRequestBinding
    delivery_target: Literal["next_agent_model_call.messages"]
    tool_name: Literal["read_skill_resource"]
    tool_call_id: str
    tool_message_sha256: str
    tool_message_byte_length: int
    matched_tool_message_count: int
    model_input_sha256: str
    model_input_byte_length: int
    model_invocation_sequence: int
    delivery_check: Literal["contains_exact_bound_tool_message"]


@dataclass(frozen=True, slots=True)
class SkillResourceHostProof:
    """Structured evidence returned by trusted host code, never by the model."""

    request: RequestBindingProof
    source: ResourceReadProof
    call: ToolCallBindingProof
    model_input: ModelInputProof


class HostSkillResourceVerifier(Protocol):
    """Trusted host verifier; it must inspect real host objects and lifecycle.

    The implementation must establish that ``host_snapshot`` is the request
    snapshot; current identity and delete epoch still match; the result came
    from that snapshot's SkillsRuntime and exact repository path; package hash
    revalidation succeeded before and after the read; and exactly one AI
    ``tool_calls`` entry with this call ID exists in ``next_model_input``. That
    entry must be named ``read_skill_resource`` and have exactly the
    ``skill_id`` and ``path`` arguments matching ``loaded_resource``. Duplicate
    matches, extra or missing arguments, or any mismatch must be rejected. The
    implementation must also bind the raw ToolRouter result to the same actual
    successful ToolMessage and prove that this complete ToolMessage occurs
    exactly once in that same next model input. It must not use model/client-
    supplied request, user, session, or package identifiers as authority. The
    ToolMessage's call ID is a correlation token only.

    No proof producer currently exists in the host request flow. Until trusted
    host code implements this contract, callers must not invoke this converter
    with fabricated proof records.
    """

    def verify(
        self,
        *,
        host_snapshot: object,
        request_binding: ResourceRequestBinding,
        current_binding: ResourceRequestBinding,
        pinned_skill: PinnedSkillPackage,
        loaded_resource: LoadedSkillResource,
        raw_tool_result: object,
        tool_message: object,
        expected_tool_call: ExpectedToolCall,
        next_model_input: object,
        raw_result_sha256: str,
        raw_result_byte_length: int,
        tool_message_sha256: str,
        tool_message_byte_length: int,
        content_sha256: str,
        content_byte_length: int,
    ) -> SkillResourceHostProof: ...


@dataclass(frozen=True, slots=True)
class SkillResourceSnapshotCandidate:
    """One complete resource body with independent source and body identity."""

    principal_hash: str
    session_id: str
    request_id: str
    session_delete_epoch: str
    skill_id: str
    package_content_hash: str
    resource_path: str
    content: str
    content_sha256: str
    content_byte_length: int
    tool_name: Literal["read_skill_resource"]
    tool_call_id: str
    raw_result_sha256: str
    tool_message_sha256: str
    model_input_sha256: str
    model_input_byte_length: int
    model_invocation_sequence: int


def build_skill_resource_snapshot_candidate(
    *,
    host_snapshot: object,
    request_binding: ResourceRequestBinding,
    current_binding: ResourceRequestBinding,
    pinned_skill: PinnedSkillPackage,
    loaded_resource: LoadedSkillResource,
    raw_tool_result: object,
    tool_message: object,
    expected_tool_call: ExpectedToolCall,
    next_model_input: object,
    verifier: HostSkillResourceVerifier,
) -> SkillResourceSnapshotCandidate:
    """Convert one fully proved model-visible resource result into a candidate.

    The function computes the UTF-8 body digest and length itself. The package
    hash remains a distinct pinned-version field. It accepts only the exact
    read_skill_resource result shape; arbitrary tool results and client payloads
    cannot be converted. The host verifier is mandatory and must return
    structured evidence for every source and model-input edge.
    """
    _validate_request_binding(request_binding)
    _validate_request_binding(current_binding)
    if request_binding != current_binding:
        raise SkillResourceSnapshotRejected("request_identity_or_delete_epoch_changed")

    _validate_pinned_skill(pinned_skill)
    _validate_loaded_resource(loaded_resource, pinned_skill)
    _validate_expected_call(expected_tool_call)
    if (
        expected_tool_call.skill_id != loaded_resource.skill_id
        or expected_tool_call.resource_path != loaded_resource.resource_path
    ):
        raise SkillResourceSnapshotRejected("tool_call_resource_arguments_mismatch")
    content_bytes = _utf8(loaded_resource.content, "invalid_resource_text")
    if len(content_bytes) > _MAX_RESOURCE_BYTES:
        raise SkillResourceSnapshotRejected("resource_exceeds_context_budget")
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()

    raw_result_json, raw_result_bytes = _canonical_json_bytes(
        raw_tool_result, "invalid_raw_tool_result",
    )
    if len(raw_result_bytes) > _MAX_RESULT_BYTES:
        raise SkillResourceSnapshotRejected("raw_tool_result_exceeds_limit")
    raw_result_sha256 = hashlib.sha256(raw_result_bytes).hexdigest()

    message_fields = _read_tool_message(tool_message)
    if (
        message_fields["name"] != expected_tool_call.tool_name
        or message_fields["tool_call_id"] != expected_tool_call.tool_call_id
        or message_fields["status"] != "success"
    ):
        raise SkillResourceSnapshotRejected("tool_message_call_binding_mismatch")
    message_content = message_fields["content"]
    message_bytes = _utf8(message_content, "invalid_tool_message_content")
    if len(message_bytes) > _MAX_RESULT_BYTES:
        raise SkillResourceSnapshotRejected("tool_message_exceeds_limit")
    tool_message_sha256 = hashlib.sha256(message_bytes).hexdigest()
    parsed_message = _parse_json_object(message_content)
    message_json, message_result_bytes = _canonical_json_bytes(
        parsed_message, "invalid_tool_message_result",
    )
    if (
        message_json != raw_result_json
        or message_result_bytes != raw_result_bytes
    ):
        raise SkillResourceSnapshotRejected("tool_message_does_not_match_raw_result")
    _validate_resource_result(
        parsed_message,
        expected_tool_call=expected_tool_call,
        loaded_resource=loaded_resource,
    )

    verify = getattr(verifier, "verify", None)
    if not callable(verify):
        raise SkillResourceSnapshotRejected("trusted_host_verifier_required")
    try:
        proof = verify(
            host_snapshot=host_snapshot,
            request_binding=request_binding,
            current_binding=current_binding,
            pinned_skill=pinned_skill,
            loaded_resource=loaded_resource,
            raw_tool_result=raw_tool_result,
            tool_message=tool_message,
            expected_tool_call=expected_tool_call,
            next_model_input=next_model_input,
            raw_result_sha256=raw_result_sha256,
            raw_result_byte_length=len(raw_result_bytes),
            tool_message_sha256=tool_message_sha256,
            tool_message_byte_length=len(message_bytes),
            content_sha256=content_sha256,
            content_byte_length=len(content_bytes),
        )
    except Exception as exc:
        raise SkillResourceSnapshotRejected("host_verification_failed") from exc

    _validate_host_proof(
        proof=proof,
        request_binding=request_binding,
        current_binding=current_binding,
        pinned_skill=pinned_skill,
        loaded_resource=loaded_resource,
        expected_tool_call=expected_tool_call,
        content_sha256=content_sha256,
        content_byte_length=len(content_bytes),
        raw_result_sha256=raw_result_sha256,
        raw_result_byte_length=len(raw_result_bytes),
        tool_message_sha256=tool_message_sha256,
        tool_message_byte_length=len(message_bytes),
    )

    model_input = proof.model_input
    return SkillResourceSnapshotCandidate(
        principal_hash=request_binding.principal_hash,
        session_id=request_binding.session_id,
        request_id=request_binding.request_id,
        session_delete_epoch=request_binding.session_delete_epoch,
        skill_id=loaded_resource.skill_id,
        package_content_hash=loaded_resource.package_content_hash,
        resource_path=loaded_resource.resource_path,
        content=loaded_resource.content,
        content_sha256=content_sha256,
        content_byte_length=len(content_bytes),
        tool_name=expected_tool_call.tool_name,
        tool_call_id=expected_tool_call.tool_call_id,
        raw_result_sha256=raw_result_sha256,
        tool_message_sha256=tool_message_sha256,
        model_input_sha256=model_input.model_input_sha256,
        model_input_byte_length=model_input.model_input_byte_length,
        model_invocation_sequence=model_input.model_invocation_sequence,
    )


def _validate_request_binding(binding: ResourceRequestBinding) -> None:
    if type(binding) is not ResourceRequestBinding:
        raise SkillResourceSnapshotRejected("host_request_binding_required")
    if (
        type(binding.principal_hash) is not str
        or _HASH_RE.fullmatch(binding.principal_hash) is None
        or type(binding.session_id) is not str
        or not 1 <= len(binding.session_id) <= 128
        or binding.session_id != binding.session_id.strip()
        or type(binding.request_id) is not str
        or _REQUEST_ID_RE.fullmatch(binding.request_id) is None
        or type(binding.session_delete_epoch) is not str
        or (
            binding.session_delete_epoch != ""
            and _REQUEST_ID_RE.fullmatch(binding.session_delete_epoch) is None
        )
        or any(
            ord(char) < 32 or ord(char) == 127
            for value in (
                binding.session_id,
                binding.principal_hash,
                binding.request_id,
                binding.session_delete_epoch,
            )
            for char in value
        )
    ):
        raise SkillResourceSnapshotRejected("invalid_host_request_binding")


def _validate_pinned_skill(skill: PinnedSkillPackage) -> None:
    if (
        type(skill) is not PinnedSkillPackage
        or type(skill.skill_id) is not str
        or _SKILL_ID_RE.fullmatch(skill.skill_id) is None
        or type(skill.package_content_hash) is not str
        or _HASH_RE.fullmatch(skill.package_content_hash) is None
    ):
        raise SkillResourceSnapshotRejected("invalid_pinned_skill_package")


def _validate_loaded_resource(
    source: LoadedSkillResource,
    pinned_skill: PinnedSkillPackage,
) -> None:
    if (
        type(source) is not LoadedSkillResource
        or source.skill_id != pinned_skill.skill_id
        or source.package_content_hash != pinned_skill.package_content_hash
        or type(source.content) is not str
        or "\x00" in source.content
    ):
        raise SkillResourceSnapshotRejected("resource_source_does_not_match_pinned_skill")
    _validate_resource_path(source.resource_path)
    _utf8(source.content, "invalid_resource_text")


def _validate_resource_path(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or "\x00" in value
    ):
        raise SkillResourceSnapshotRejected("invalid_resource_path")
    parts = value.split("/")
    if (
        len(parts) < 2
        or parts[0] not in {"references", "assets"}
        or any(part in {"", ".", ".."} for part in parts)
        or any(part != unicodedata.normalize("NFC", part) for part in parts)
        or any(
            part.endswith((" ", "."))
            or any(ord(char) < 32 or char in '<>"|?*' for char in part)
            for part in parts
        )
    ):
        raise SkillResourceSnapshotRejected("invalid_resource_path")


def _validate_expected_call(call: ExpectedToolCall) -> None:
    if (
        type(call) is not ExpectedToolCall
        or call.tool_name != "read_skill_resource"
        or type(call.tool_call_id) is not str
        or _TOOL_CALL_ID_RE.fullmatch(call.tool_call_id) is None
        or not call.tool_call_id.strip()
        or type(call.skill_id) is not str
        or _SKILL_ID_RE.fullmatch(call.skill_id) is None
    ):
        raise SkillResourceSnapshotRejected("invalid_host_tool_call_binding")
    _validate_resource_path(call.resource_path)


def _read_tool_message(message: object) -> dict[str, str]:
    """Read normalized attributes; the verifier must prove exact ToolMessage type."""
    name = getattr(message, "name", None)
    tool_call_id = getattr(message, "tool_call_id", None)
    status = getattr(message, "status", None)
    content = getattr(message, "content", None)
    if (
        type(name) is not str
        or type(tool_call_id) is not str
        or type(status) is not str
        or type(content) is not str
    ):
        raise SkillResourceSnapshotRejected("unsupported_tool_message")
    return {
        "name": name,
        "tool_call_id": tool_call_id,
        "status": status,
        "content": content,
    }


def _validate_resource_result(
    result: dict,
    *,
    expected_tool_call: ExpectedToolCall,
    loaded_resource: LoadedSkillResource,
) -> None:
    if type(result) is not dict or set(result) != _RESULT_KEYS:
        raise SkillResourceSnapshotRejected("unsupported_tool_result_shape")
    metadata = result.get("metadata")
    data = result.get("data")
    if (
        result.get("success") is not True
        or result.get("output") != "Skill resource loaded"
        or result.get("error") is not None
        or type(data) is not dict
        or set(data) != _RESOURCE_DATA_KEYS
        or data.get("success") is not True
        or data.get("stage") != "resource"
        or data.get("skill_id") != loaded_resource.skill_id
        or data.get("content_hash") != loaded_resource.package_content_hash
        or data.get("path") != loaded_resource.resource_path
        or data.get("content") != loaded_resource.content
        or type(metadata) is not dict
        or not {"tool_name", "call_id", "compacted"}.issubset(metadata)
        or not set(metadata).issubset(_METADATA_KEYS)
        or metadata.get("tool_name") != expected_tool_call.tool_name
        or metadata.get("call_id") != expected_tool_call.tool_call_id
        or metadata.get("compacted") is not False
    ):
        raise SkillResourceSnapshotRejected("tool_result_resource_binding_mismatch")


def _validate_host_proof(
    *,
    proof: object,
    request_binding: ResourceRequestBinding,
    current_binding: ResourceRequestBinding,
    pinned_skill: PinnedSkillPackage,
    loaded_resource: LoadedSkillResource,
    expected_tool_call: ExpectedToolCall,
    content_sha256: str,
    content_byte_length: int,
    raw_result_sha256: str,
    raw_result_byte_length: int,
    tool_message_sha256: str,
    tool_message_byte_length: int,
) -> None:
    if type(proof) is not SkillResourceHostProof:
        raise SkillResourceSnapshotRejected("structured_host_proof_required")

    request = proof.request
    if (
        type(request) is not RequestBindingProof
        or request.snapshot_binding != request_binding
        or request.current_binding != current_binding
        or request.snapshot_binding != request.current_binding
        or request.pinned_skill != pinned_skill
    ):
        raise SkillResourceSnapshotRejected("host_request_proof_mismatch")

    source = proof.source
    if (
        type(source) is not ResourceReadProof
        or source.request_binding != request_binding
        or source.skill_id != loaded_resource.skill_id
        or source.package_content_hash != loaded_resource.package_content_hash
        or source.resource_path != loaded_resource.resource_path
        or source.content_sha256 != content_sha256
        or type(source.content_byte_length) is not int
        or source.content_byte_length != content_byte_length
        or source.version_check != "matched_before_and_after_read"
        or source.path_check != "exact_package_relative_resource"
        or source.body_check != "complete_utf8_resource"
    ):
        raise SkillResourceSnapshotRejected("host_resource_read_proof_mismatch")

    call = proof.call
    if (
        type(call) is not ToolCallBindingProof
        or call.request_binding != request_binding
        or call.tool_name != expected_tool_call.tool_name
        or call.tool_call_id != expected_tool_call.tool_call_id
        or type(call.matched_ai_tool_call_count) is not int
        or call.matched_ai_tool_call_count != 1
        or call.actual_call_skill_id != expected_tool_call.skill_id
        or call.actual_call_resource_path != expected_tool_call.resource_path
        or call.actual_call_argument_keys != frozenset({"skill_id", "path"})
        or call.tool_call_id_role != "correlation_only"
        or call.source_check != "request_local_skills_runtime_and_tool_router"
        or call.call_check != "unique_ai_call_name_and_exact_skill_id_path_args"
        or call.raw_result_sha256 != raw_result_sha256
        or type(call.raw_result_byte_length) is not int
        or call.raw_result_byte_length != raw_result_byte_length
        or call.tool_message_sha256 != tool_message_sha256
        or type(call.tool_message_byte_length) is not int
        or call.tool_message_byte_length != tool_message_byte_length
        or call.message_check != "actual_success_tool_message"
    ):
        raise SkillResourceSnapshotRejected("host_tool_call_proof_mismatch")

    delivery = proof.model_input
    if (
        type(delivery) is not ModelInputProof
        or delivery.request_binding != request_binding
        or delivery.delivery_target != "next_agent_model_call.messages"
        or delivery.tool_name != expected_tool_call.tool_name
        or delivery.tool_call_id != expected_tool_call.tool_call_id
        or delivery.tool_message_sha256 != tool_message_sha256
        or type(delivery.tool_message_byte_length) is not int
        or delivery.tool_message_byte_length != tool_message_byte_length
        or type(delivery.matched_tool_message_count) is not int
        or delivery.matched_tool_message_count != 1
        or type(delivery.model_input_sha256) is not str
        or _HASH_RE.fullmatch(delivery.model_input_sha256) is None
        or type(delivery.model_input_byte_length) is not int
        or delivery.model_input_byte_length <= 0
        or delivery.model_input_byte_length > 4 * 1024 * 1024
        or type(delivery.model_invocation_sequence) is not int
        or delivery.model_invocation_sequence <= 0
        or delivery.delivery_check != "contains_exact_bound_tool_message"
    ):
        raise SkillResourceSnapshotRejected("host_model_input_proof_mismatch")


def _parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise SkillResourceSnapshotRejected("invalid_tool_message_json") from exc
    if type(parsed) is not dict:
        raise SkillResourceSnapshotRejected("tool_message_result_not_object")
    return parsed


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str):
    raise ValueError("non-finite JSON number")


def _canonical_json_bytes(value: object, error_code: str) -> tuple[str, bytes]:
    if not _is_json_native(value):
        raise SkillResourceSnapshotRejected(error_code)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        encoded_bytes = encoded.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise SkillResourceSnapshotRejected(error_code) from exc
    return encoded, encoded_bytes


def _is_json_native(value: object, depth: int = 0) -> bool:
    if depth > 32:
        return False
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return value == value and value not in (float("inf"), float("-inf"))
    if type(value) is list:
        return len(value) <= 65536 and all(_is_json_native(item, depth + 1) for item in value)
    if type(value) is dict:
        return len(value) <= 65536 and all(
            type(key) is str and _is_json_native(item, depth + 1)
            for key, item in value.items()
        )
    return False


def _utf8(value: object, error_code: str) -> bytes:
    if type(value) is not str:
        raise SkillResourceSnapshotRejected(error_code)
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SkillResourceSnapshotRejected(error_code) from exc
