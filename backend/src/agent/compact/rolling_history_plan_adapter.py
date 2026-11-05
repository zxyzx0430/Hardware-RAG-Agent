"""Fail-closed host adapter from authenticated v1 history to a rolling plan.

This module has no storage, model, tool, or publication access. A trusted host
must authenticate the complete readback and its active identity scope before
the adapter uses it. The adapter itself does not authenticate a principal,
session, or branch.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from app.api.rolling_history import RollingHistoryEvent, RollingHistoryReference
from app.api.rolling_history_codec import (
    RollingHistoryCodecError,
    decode_rolling_history_events,
)
from src.agent.compact.rolling_window import (
    RollingWindowPlan,
    RollingWindowPlanningError,
    SequencedMessage,
    plan_rolling_window,
)


_REFERENCE_FIELDS = (
    "event_id",
    "session_id",
    "request_id",
    "sequence",
    "version",
    "content_sha256",
    "chain_sha256",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SUPPORTED_EVENT_TYPES = frozenset({
    "user_text",
    "assistant_text",
    "tool_call",
    "tool_result",
})
_SKILL_EVENT_TYPES = frozenset({"skill_instruction", "skill_resource"})
_REFERENCE_BYTE_LIMIT = 1024


class RollingHistoryPlanAdapterError(ValueError):
    """The host readback cannot safely produce a rolling-window plan."""


HostReadbackAuthenticator = Callable[
    [tuple[RollingHistoryEvent, ...], object],
    None,
]


@dataclass(frozen=True, slots=True)
class RollingHistoryPlanAdapterResult:
    """Immutable planner input and evidence derived from visible v1 text."""

    messages: tuple[SequencedMessage, ...]
    plan: RollingWindowPlan
    reference_json_by_sequence: Mapping[int, str]
    source_evidence_by_sequence: Mapping[int, str]


def adapt_authenticated_rolling_history(
    events: Sequence[RollingHistoryEvent],
    *,
    host_readback_proof: object,
    authenticate_host_readback: HostReadbackAuthenticator,
    covered_through: int,
    current_user_sequence: int,
    history_budget_tokens: int,
    recent_target_tokens: int,
    summary_chunk_budget_tokens: int,
) -> RollingHistoryPlanAdapterResult:
    """Decode and plan a host-authenticated, complete v1 text history readback.

    ``authenticate_host_readback`` must be a trusted callback injected by host
    code, never by a client, model, or event payload. Given this exact immutable
    event tuple and an opaque host proof, it must raise unless it verifies the
    complete-event read path, the currently authenticated principal/session
    scope, and that no model-visible context is omitted from this v1 projection.
    In particular, it must reject active Skill instructions/resources or v2
    branch/revision visibility until those representations are supported here.
    The callback returns exactly ``None`` on success; a boolean is not proof.

    This adapter separately checks v1 reference shape and content hashes through
    the codec, decodes only the actual message bodies (never the JSON envelopes),
    requires event and planner sequences to match, and calls the existing pure
    rolling-window planner. Any unsupported data raises so the caller can retain
    its original context. No principal or branch authentication is claimed here.
    """
    event_tuple = _require_event_tuple(events)
    _require_host_authenticator(host_readback_proof, authenticate_host_readback)

    references = tuple(_validate_v1_reference(event) for event in event_tuple)
    if len({reference.session_id for reference in references}) != 1:
        raise RollingHistoryPlanAdapterError("History readback mixes session scopes")

    for event in event_tuple:
        if event.event_type in _SKILL_EVENT_TYPES:
            raise RollingHistoryPlanAdapterError(
                "Skill instruction/resource history is not supported by this adapter"
            )
        if event.event_type not in _SUPPORTED_EVENT_TYPES:
            raise RollingHistoryPlanAdapterError("Unsupported v1 visible event type")
        if event.attachments:
            raise RollingHistoryPlanAdapterError(
                "History with attachment references is not supported by this adapter"
            )

    try:
        authentication_result = authenticate_host_readback(event_tuple, host_readback_proof)
    except Exception as exc:
        raise RollingHistoryPlanAdapterError(
            "Trusted host did not authenticate the complete readback and identity scope"
        ) from exc
    if authentication_result is not None:
        raise RollingHistoryPlanAdapterError(
            "Host readback authenticator must raise on failure and return None on success"
        )

    try:
        messages = decode_rolling_history_events(event_tuple)
    except RollingHistoryCodecError as exc:
        raise RollingHistoryPlanAdapterError(
            "v1 history cannot be decoded losslessly as visible text"
        ) from exc

    reference_sequences = tuple(reference.sequence for reference in references)
    planner_sequences = tuple(item.sequence for item in messages)
    if planner_sequences != reference_sequences:
        raise RollingHistoryPlanAdapterError(
            "Event reference sequences do not match planner source sequences"
        )

    source_evidence: dict[int, str] = {}
    reference_json: dict[int, str] = {}
    for event, item, reference in zip(event_tuple, messages, references, strict=True):
        if type(item.message.content) is not str:
            raise RollingHistoryPlanAdapterError(
                "Non-text model-visible message content is not supported"
            )
        try:
            item.message.content.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise RollingHistoryPlanAdapterError(
                "Model-visible source text is not valid UTF-8"
            ) from exc
        if item.sequence != event.reference.sequence:
            raise RollingHistoryPlanAdapterError(
                "Decoded planner source is not bound to its event reference"
            )
        source_evidence[item.sequence] = item.message.content
        reference_json[item.sequence] = _canonical_reference_json(reference)

    try:
        plan = plan_rolling_window(
            messages,
            covered_through=covered_through,
            current_user_sequence=current_user_sequence,
            history_budget_tokens=history_budget_tokens,
            recent_target_tokens=recent_target_tokens,
            summary_chunk_budget_tokens=summary_chunk_budget_tokens,
        )
    except RollingWindowPlanningError as exc:
        raise RollingHistoryPlanAdapterError(
            "Complete history cannot be planned without losing or splitting source data"
        ) from exc

    _require_complete_plan_partition(plan, messages, covered_through)
    _require_tool_pairs_on_one_side(messages, plan)
    return RollingHistoryPlanAdapterResult(
        messages=messages,
        plan=plan,
        reference_json_by_sequence=MappingProxyType(reference_json),
        source_evidence_by_sequence=MappingProxyType(source_evidence),
    )


def _require_event_tuple(
    events: Sequence[RollingHistoryEvent],
) -> tuple[RollingHistoryEvent, ...]:
    if type(events) not in (list, tuple) or not events:
        raise RollingHistoryPlanAdapterError(
            "A nonempty complete v1 RollingHistoryEvent sequence is required"
        )
    event_tuple = tuple(events)
    for event in event_tuple:
        if (type(event) is not RollingHistoryEvent
                or type(event.reference) is not RollingHistoryReference):
            raise RollingHistoryPlanAdapterError(
                "Only complete RollingHistoryEvent values are supported"
            )
    return event_tuple


def _require_host_authenticator(
    proof: object,
    authenticator: HostReadbackAuthenticator,
) -> None:
    if proof is None or type(proof) in (bool, int, float, str, bytes):
        raise RollingHistoryPlanAdapterError(
            "An opaque host-generated readback proof is required"
        )
    if not callable(authenticator):
        raise RollingHistoryPlanAdapterError(
            "A trusted host readback authenticator is required"
        )


def _validate_v1_reference(event: RollingHistoryEvent) -> RollingHistoryReference:
    reference = event.reference
    session_id = reference.session_id
    if (type(reference.event_id) is not str
            or _SHA256.fullmatch(reference.event_id) is None
            or type(session_id) is not str
            or not 1 <= len(session_id) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
            or type(reference.request_id) is not str
            or _REQUEST_ID.fullmatch(reference.request_id) is None
            or type(reference.sequence) is not int or reference.sequence < 1
            or type(reference.version) is not int or reference.version != 1
            or type(reference.content_sha256) is not str
            or _SHA256.fullmatch(reference.content_sha256) is None
            or type(reference.chain_sha256) is not str
            or _SHA256.fullmatch(reference.chain_sha256) is None):
        raise RollingHistoryPlanAdapterError("Invalid immutable v1 event reference")
    return reference


def _canonical_reference_json(reference: RollingHistoryReference) -> str:
    projection = {
        "event_id": reference.event_id,
        "session_id": reference.session_id,
        "request_id": reference.request_id,
        "sequence": reference.sequence,
        "version": reference.version,
        "content_sha256": reference.content_sha256,
        "chain_sha256": reference.chain_sha256,
    }
    if tuple(projection) != _REFERENCE_FIELDS:
        raise RollingHistoryPlanAdapterError("Invalid v1 reference projection")
    try:
        encoded = json.dumps(
            {field: projection[field] for field in _REFERENCE_FIELDS},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8", errors="strict")) > _REFERENCE_BYTE_LIMIT:
            raise RollingHistoryPlanAdapterError("v1 reference exceeds its byte limit")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, RollingHistoryPlanAdapterError):
            raise
        raise RollingHistoryPlanAdapterError("v1 reference cannot be encoded canonically") from exc
    return encoded


def _require_complete_plan_partition(
    plan: RollingWindowPlan,
    messages: tuple[SequencedMessage, ...],
    covered_through: int,
) -> None:
    first_sequence = messages[0].sequence
    last_sequence = messages[-1].sequence
    if (plan.already_covered_through != covered_through
            or first_sequence != covered_through + 1):
        raise RollingHistoryPlanAdapterError("Planner did not preserve the complete source suffix")
    if plan.candidate_range is None:
        if (plan.next_covered_through != covered_through
                or plan.retained_range is None
                or plan.retained_range.first_sequence != first_sequence
                or plan.retained_range.last_sequence != last_sequence):
            raise RollingHistoryPlanAdapterError("Planner returned an incomplete retained range")
        return
    if (plan.candidate_range.first_sequence != first_sequence
            or plan.candidate_range.last_sequence != plan.next_covered_through
            or plan.retained_range is None
            or plan.retained_range.first_sequence != plan.next_covered_through + 1
            or plan.retained_range.last_sequence != last_sequence):
        raise RollingHistoryPlanAdapterError(
            "Planner candidate and retained ranges do not partition the source suffix"
        )


def _require_tool_pairs_on_one_side(
    messages: tuple[SequencedMessage, ...],
    plan: RollingWindowPlan,
) -> None:
    from langchain_core.messages import AIMessage, ToolMessage

    call_sequence_by_id: dict[str, int] = {}
    pairs: list[tuple[int, int]] = []
    for item in messages:
        message = item.message
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                call_id = call.get("id") if type(call) is dict else None
                if type(call_id) is not str or not call_id or call_id in call_sequence_by_id:
                    raise RollingHistoryPlanAdapterError("Tool-call identity is invalid or duplicated")
                call_sequence_by_id[call_id] = item.sequence
        elif isinstance(message, ToolMessage):
            call_sequence = call_sequence_by_id.get(message.tool_call_id)
            if call_sequence is None:
                raise RollingHistoryPlanAdapterError("Tool result has no preceding source call")
            pairs.append((call_sequence, item.sequence))

    def side(sequence: int) -> str | None:
        candidate = plan.candidate_range
        retained = plan.retained_range
        if (candidate is not None
                and candidate.first_sequence <= sequence <= candidate.last_sequence):
            return "summary"
        if (retained is not None
                and retained.first_sequence <= sequence <= retained.last_sequence):
            return "retained"
        return None

    for call_sequence, result_sequence in pairs:
        call_side = side(call_sequence)
        result_side = side(result_sequence)
        if call_side is None or result_side is None or call_side != result_side:
            raise RollingHistoryPlanAdapterError(
                "Rolling-window plan split a tool call from its result"
            )
