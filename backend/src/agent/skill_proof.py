"""Request-local observations for Skill candidate verification.

This module is an evidence collector, not an authorization or archive API. A
trusted host caller must bind it before Skill preloading, report actual source
and ToolRouter events, and attach :class:`SkillProofCallback` to the Agent run.
While a request is active, it can retain bounded Skill-only source/result text
for a trusted host verifier. It can also retain the complete factory system
prompt only inside the same request budget and only expose it after an exact
LangChain system-message match. Ordinary evidence remains metadata-only; other
model messages are not retained. It does not observe provider transmission or
graph ACKs.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from threading import RLock
from typing import Any, Iterator, Literal

from langchain_core.callbacks import BaseCallbackHandler


_HASH_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SKILL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_CALL_ID_RE = re.compile(r"[^\x00-\x1f\x7f]{1,256}\Z", re.ASCII)
_MAX_PACKAGES = 32
_MAX_SOURCES = 64
_MAX_TOOL_RESULTS = 64
_MAX_MODEL_INPUTS = 128
_MAX_SOURCE_BYTES = 96 * 1024
_MAX_TOOL_RESULT_BYTES = 128 * 1024
_MAX_MODEL_INPUT_BYTES = 4 * 1024 * 1024
_MAX_REQUEST_CONTENT_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_FACTORY_SKILL_CONTEXT_BYTES = 96 * 1024
_MAX_FACTORY_SKILLS = 3
_MAX_JSON_DEPTH = 32
_RESULT_KEYS = frozenset({"success", "output", "data", "error", "metadata"})
_RESOURCE_DATA_KEYS = frozenset({
    "skill_id", "content_hash", "path", "stage", "success", "content",
})
_RESULT_METADATA_KEYS = frozenset({
    "tool_name", "duration_ms", "call_id", "timestamp", "kb_coverage_hint",
    "compacted", "hwrag_tool_event_ref", "hwrag_tool_observation_archived",
    "hwrag_tool_observation_ref",
})

EvidenceStatus = Literal["observed", "unknown", "unavailable", "mismatch"]
SourceStage = Literal["instructions", "resource"]


@dataclass(frozen=True, slots=True)
class SkillProofRequestBinding:
    """Authenticated host identity copied from the request-scoped host scope."""

    principal_hash: str
    session_id: str
    request_id: str
    session_delete_epoch: str
    memory_clear_epoch: str | None = None


@dataclass(frozen=True, slots=True)
class SkillPackagePin:
    """Package content hash pinned by trusted host code before reading its body."""

    skill_id: str
    package_content_hash: str


@dataclass(frozen=True, slots=True)
class SkillSourceEvidence:
    request: SkillProofRequestBinding | None
    skill_id: str
    package_content_hash: str
    stage: SourceStage
    source_path: str
    source_call_id: str
    source_call_kind: Literal["load_skill", "read_skill_resource"]
    source_call_id_role: Literal["correlation_only"]
    source_status: EvidenceStatus
    body_sha256: str | None
    body_utf8_byte_length: int | None
    model_input_status: EvidenceStatus
    model_input_sha256: str | None = None
    model_input_utf8_byte_length: int | None = None
    model_invocation_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class SkillToolResultEvidence:
    request: SkillProofRequestBinding | None
    tool_name: str
    tool_call_id: str
    tool_call_id_role: Literal["correlation_only"]
    skill_id: str
    package_content_hash: str
    resource_path: str
    call_result_status: EvidenceStatus
    raw_result_sha256: str | None
    raw_result_utf8_byte_length: int | None
    tool_message_sha256: str | None
    tool_message_utf8_byte_length: int | None
    body_sha256: str | None
    body_utf8_byte_length: int | None
    model_input_status: EvidenceStatus
    model_input_sha256: str | None = None
    model_input_utf8_byte_length: int | None = None
    model_invocation_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class SkillModelInputMatch:
    """One source/call identity whose complete body was found in callback input."""

    source_call_id: str
    source_call_kind: Literal["load_skill", "read_skill_resource"]
    source_call_id_role: Literal["correlation_only"]
    skill_id: str
    package_content_hash: str
    source_stage: SourceStage
    body_sha256: str
    body_utf8_byte_length: int


@dataclass(frozen=True, slots=True)
class SkillModelInputEvidence:
    """Evidence from the real LangChain callback input, not a send/ACK claim."""

    request: SkillProofRequestBinding | None
    status: EvidenceStatus
    delivery_target: Literal["langchain.on_chat_model_start.messages"]
    model_input_sha256: str | None
    model_input_utf8_byte_length: int | None
    model_invocation_sequence: int | None
    matched_source_count: int
    matches: tuple[SkillModelInputMatch, ...]


@dataclass(frozen=True, slots=True)
class SkillProofRequestEvidence:
    request: SkillProofRequestBinding | None
    outcome: Literal["completed", "unknown"]
    callback_status: EvidenceStatus
    source_observations: tuple[SkillSourceEvidence, ...]
    tool_result_observations: tuple[SkillToolResultEvidence, ...]
    model_input_observations: tuple[SkillModelInputEvidence, ...]
    unavailable_count: int


@dataclass(slots=True)
class _SourceRecord:
    skill_id: str
    package_content_hash: str
    stage: SourceStage
    source_path: str
    source_call_id: str
    status: EvidenceStatus
    body_sha256: str | None
    body_length: int | None
    model_status: EvidenceStatus = "unknown"
    model_sha256: str | None = None
    model_length: int | None = None
    model_sequence: int | None = None
    body_text: str | None = None
    model_visible_text: str | None = None
    model_visible_length: int | None = None


@dataclass(slots=True)
class _ToolRecord:
    tool_name: str
    tool_call_id: str
    skill_id: str
    package_content_hash: str
    resource_path: str
    status: EvidenceStatus
    raw_result_sha256: str | None
    raw_result_length: int | None
    body_sha256: str | None
    body_length: int | None
    model_status: EvidenceStatus = "unknown"
    tool_message_sha256: str | None = None
    tool_message_length: int | None = None
    model_sha256: str | None = None
    model_length: int | None = None
    model_sequence: int | None = None
    result_canonical_json: str | None = None
    model_visible_text: str | None = None
    model_visible_length: int | None = None


@dataclass(frozen=True, slots=True)
class SkillProofObservationSnapshot:
    """Immutable, in-flight metadata state; not a completion or ACK record."""

    availability: EvidenceStatus
    observation_revision: int
    request: SkillProofRequestBinding | None
    package_pins: tuple[SkillPackagePin, ...]
    source_observations: tuple[SkillSourceEvidence, ...]
    tool_result_observations: tuple[SkillToolResultEvidence, ...]
    model_input_observations: tuple[SkillModelInputEvidence, ...]
    callback_status: EvidenceStatus
    unavailable_count: int


@dataclass(frozen=True, slots=True)
class SkillProofContentCapture:
    """One complete Skill source and its optional exact callback-visible text."""

    request: SkillProofRequestBinding
    source_call_id: str
    source_call_kind: Literal["load_skill", "read_skill_resource"]
    skill_id: str
    package_content_hash: str
    stage: SourceStage
    source_path: str
    source_body: str
    source_body_sha256: str
    source_body_utf8_byte_length: int
    tool_result_canonical_json: str | None
    tool_result_sha256: str | None
    tool_result_utf8_byte_length: int | None
    model_visible_text: str | None
    model_visible_text_sha256: str | None
    model_visible_text_utf8_byte_length: int | None
    model_invocation_sequence: int | None


@dataclass(frozen=True, slots=True)
class SkillInstructionPromptDelivery:
    """One complete loaded instruction body matched in the factory prompt."""

    source_call_id: str
    skill_id: str
    content_hash: str
    instructions_sha256: str
    instructions_byte_length: int


@dataclass(frozen=True, slots=True)
class SkillProofFactorySystemPromptEvidence:
    """Host-only proof of the exact factory prompt seen by a model callback."""

    request: SkillProofRequestBinding | None
    status: EvidenceStatus
    delivery_target: Literal["agent_factory.create_agent.system_prompt"]
    system_prompt_sha256: str | None = None
    system_prompt_utf8_byte_length: int | None = None
    context_json_sha256: str | None = None
    context_json_utf8_byte_length: int | None = None
    delivered_bodies: tuple[SkillInstructionPromptDelivery, ...] = ()
    model_invocation_sequence: int | None = None
    system_prompt: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class SkillProofHostContentSnapshot:
    """Frozen host-verifier view bound to one metadata observation revision.

    The snapshot is returned only while the current request identity, epochs,
    active collector context, and host scope all match. A consumer that keeps
    a returned copy must revalidate binding, epoch, and revision before saving
    it; already-held immutable copies cannot be revoked when the scope closes.
    Factory prompt text is included only after an exact callback match.
    """

    observation: SkillProofObservationSnapshot
    content_status: EvidenceStatus
    content_captures: tuple[SkillProofContentCapture, ...]
    factory_system_prompt: SkillProofFactorySystemPromptEvidence | None = None
    _collector: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SkillProofFactoryPromptPreSaveProof:
    """Fresh same-collector check for an immutable factory prompt snapshot."""

    request: SkillProofRequestBinding
    observation_revision: int
    system_prompt_sha256: str
    context_json_sha256: str
    delivered_bodies: tuple[SkillInstructionPromptDelivery, ...]
    evidence_boundary: Literal["host_factory_prompt_and_langchain_callback"]
    _collector: object = field(repr=False, compare=False)


class SkillProofFactoryPromptRejected(ValueError):
    """A stale or incomplete factory prompt snapshot cannot be saved."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(slots=True, eq=False)
class _SkillProofFactoryContext:
    """Temporary host-only bridge from Agent construction to prompt evidence."""

    collector: "SkillProofCollector | None" = field(repr=False)
    request_snapshot: object | None = field(repr=False)
    host_scope: object | None = field(repr=False)
    request: SkillProofRequestBinding = field(repr=False)
    active: bool = True


def _locked_collector_method(method: Callable[..., Any]) -> Callable[..., Any]:
    """Hold the collector's reentrant lock for one complete state operation."""

    @wraps(method)
    def locked(self: "SkillProofCollector", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


_ACTIVE_SKILL_PROOF: ContextVar["SkillProofCollector | None"] = ContextVar(
    "active_skill_proof_request", default=None,
)
_ACTIVE_SKILL_PROOF_FACTORY_CONTEXT: ContextVar[_SkillProofFactoryContext | None] = ContextVar(
    "active_skill_proof_factory_context", default=None,
)


@contextmanager
def skill_proof_request_scope(
    *,
    request_snapshot: object,
    host_scope: object,
    current_binding: Callable[[], SkillProofRequestBinding],
) -> Iterator["SkillProofCollector"]:
    """Bind before preload and keep evidence local through graph/model callbacks.

    ``host_scope`` must be the request's real ``ContextEventScope`` copied from
    the host snapshot. ``current_binding`` must re-read authenticated principal,
    session, request ID and current delete epoch; it is checked at every stage.
    Invalid/missing host state disables observations without affecting the
    request. A future host integration should place this around preload and the
    full SSE stream so cancellation also closes the collector.
    """
    collector = SkillProofCollector._from_host(
        request_snapshot=request_snapshot,
        host_scope=host_scope,
        current_binding=current_binding,
    )
    token = _ACTIVE_SKILL_PROOF.set(collector)
    failed = False
    try:
        yield collector
    except BaseException:
        failed = True
        raise
    finally:
        try:
            collector._close(outcome="unknown" if failed else "completed")
        except BaseException:
            collector._close_unavailable()
        finally:
            _ACTIVE_SKILL_PROOF.reset(token)


def current_skill_proof_collector() -> "SkillProofCollector | None":
    """Return the collector only inside its request-local host scope."""
    collector = _ACTIVE_SKILL_PROOF.get()
    return collector if collector is not None and collector.active else None


@contextmanager
def skill_proof_factory_context(
    *,
    request_snapshot: object,
    host_scope: object,
) -> Iterator[bool]:
    """Temporarily bind a verified request snapshot for trusted Agent creation.

    The scope is accepted only from the active collector's host binding. The
    yielded flag is informational; invalid or stale host state yields ``False``
    and never raises, so proof collection cannot block an ordinary chat.
    ContextVar tokens and the mutable lease are cleared on normal exit,
    exception, or cancellation.
    """
    factory_context = _new_factory_context(request_snapshot, host_scope)
    token = _ACTIVE_SKILL_PROOF_FACTORY_CONTEXT.set(factory_context)
    try:
        yield factory_context is not None
    finally:
        if factory_context is not None:
            factory_context.active = False
            factory_context.request_snapshot = None
            factory_context.host_scope = None
            factory_context.collector = None
        _ACTIVE_SKILL_PROOF_FACTORY_CONTEXT.reset(token)


def record_factory_system_prompt_after_create_agent(
    system_prompt: object,
    *,
    request_snapshot: object,
    host_scope: object,
) -> EvidenceStatus:
    """Record a trusted factory's actual prompt after successful construction.

    This host-only entry point has no client payload route. Its future caller
    must pass the identical ``system_prompt`` value used by ``create_agent``
    only after that call returns successfully. No factory caller means no
    prompt is registered and the host view remains unknown.
    """
    collector = current_skill_proof_collector()
    if type(collector) is not SkillProofCollector:
        return "unavailable"
    return collector.observe_factory_system_prompt_after_create_agent(
        system_prompt,
        request_snapshot=request_snapshot,
        host_scope=host_scope,
    )


def record_factory_system_prompt_after_create_agent_from_context(
    system_prompt: object,
) -> EvidenceStatus:
    """Record a successful factory's exact prompt through the active host lease.

    Trusted factory code calls this only after ``create_agent`` succeeds and
    with the exact ``system_prompt`` value passed to that constructor. The
    request snapshot and scope come from :func:`skill_proof_factory_context`,
    never from model or client arguments. The existing recorder remains the
    authority for prompt validation; this bridge only supplies its host values.
    """
    factory_context = _ACTIVE_SKILL_PROOF_FACTORY_CONTEXT.get()
    if not _factory_context_is_current(factory_context):
        return "unavailable"
    request_snapshot = factory_context.request_snapshot
    host_scope = factory_context.host_scope
    if request_snapshot is None or host_scope is None:
        return "unavailable"
    try:
        from app.api.tool_events import bind_tool_event_archive, get_tool_event_scope

        # The existing recorder checks this host scope. Bind it only for this
        # synchronous evidence call so Agent construction gains no archive authority.
        with bind_tool_event_archive(host_scope):
            if (get_tool_event_scope() is not host_scope
                    or not _factory_context_is_current(factory_context)):
                return "unavailable"
            return record_factory_system_prompt_after_create_agent(
                system_prompt,
                request_snapshot=request_snapshot,
                host_scope=host_scope,
            )
    except Exception:
        return "unavailable"


def validate_factory_system_prompt_snapshot_before_save(
    snapshot: object,
) -> SkillProofFactoryPromptPreSaveProof:
    """Revalidate a frozen prompt view immediately before a host save."""
    collector = current_skill_proof_collector()
    if type(collector) is not SkillProofCollector:
        raise SkillProofFactoryPromptRejected("active_collector_unavailable")
    return collector.validate_factory_system_prompt_snapshot_before_save(snapshot)


class SkillProofCollector:
    """Collect metadata and bounded Skill-only text within one live request."""

    def __init__(
        self,
        *,
        request: SkillProofRequestBinding | None,
        current_binding: Callable[[], SkillProofRequestBinding],
        enabled: bool,
    ) -> None:
        self._request = request
        self._current_binding: Callable[[], SkillProofRequestBinding] | None = current_binding
        self._host_scope: object | None = None
        self._lock = RLock()
        self._enabled = enabled and request is not None
        self._active = True
        self._packages: dict[str, SkillPackagePin] = {}
        self._sources: list[_SourceRecord] = []
        self._tool_results: list[_ToolRecord] = []
        self._model_inputs: list[SkillModelInputEvidence] = []
        self._callback_status: EvidenceStatus = "unknown"
        self._sequence = 0
        self._observation_revision = 0
        self._unavailable_count = 0
        self._result: SkillProofRequestEvidence | None = None
        self._content_capture_status: EvidenceStatus = "unknown"
        self._content_capture_bytes = 0
        self._factory_system_prompt_registered = False
        self._factory_system_prompt_status: EvidenceStatus = "unknown"
        self._factory_system_prompt: str | None = None
        self._factory_system_prompt_sha256: str | None = None
        self._factory_system_prompt_length: int | None = None
        self._factory_context_json_sha256: str | None = None
        self._factory_context_json_length: int | None = None
        self._factory_delivered_skills: tuple[SkillInstructionPromptDelivery, ...] = ()
        self._factory_model_sequence: int | None = None

    @classmethod
    def _from_host(
        cls,
        *,
        request_snapshot: object,
        host_scope: object,
        current_binding: Callable[[], SkillProofRequestBinding],
    ) -> "SkillProofCollector":
        request = _binding_from_snapshot(request_snapshot, host_scope)
        enabled = request is not None and callable(current_binding)
        collector = cls(
            request=request,
            current_binding=current_binding,
            enabled=enabled,
        )
        collector._host_scope = host_scope if enabled else None
        if enabled and not collector._current_binding_matches():
            collector._disable()
        return collector

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def evidence(self) -> SkillProofRequestEvidence | None:
        """Final metadata-only result; it is available after scope exit."""
        with self._lock:
            return self._result

    @_locked_collector_method
    def observe_factory_system_prompt_after_create_agent(
        self,
        system_prompt: object,
        *,
        request_snapshot: object,
        host_scope: object,
    ) -> EvidenceStatus:
        """Register the exact prompt supplied by trusted post-factory host code.

        A real factory caller must invoke this only after ``create_agent``
        returns successfully and pass the identical ``system_prompt`` argument
        plus this request's host snapshot and scope. The prompt is not evidence
        until a later LangChain callback contains the same complete SystemMessage.
        """
        if not self._active:
            return "unavailable"
        if self._factory_system_prompt_registered:
            self._factory_system_prompt_status = "unavailable"
            return self._unavailable()
        self._factory_system_prompt_registered = True
        if self._factory_system_prompt_status == "unavailable":
            return "unavailable"
        if _ACTIVE_SKILL_PROOF.get() is not self:
            self._factory_system_prompt_status = "unavailable"
            return self._unavailable()
        if not self._usable(graph_callback=True):
            self._factory_system_prompt_status = "unavailable"
            return "unavailable"
        if _binding_from_snapshot(request_snapshot, host_scope) != self._request:
            self._factory_system_prompt_status = "unavailable"
            return self._unavailable()

        if type(system_prompt) is not str:
            self._factory_system_prompt_status = "unavailable"
            return self._unavailable()
        digest = _strict_text_digest(system_prompt)
        if digest is None:
            self._factory_system_prompt_status = "unavailable"
            return self._unavailable()
        if (self._content_capture_bytes + digest[1]
                > _MAX_REQUEST_CONTENT_CAPTURE_BYTES):
            self._factory_system_prompt_status = "unavailable"
            return self._unavailable()
        if '"selected_skills"' not in system_prompt:
            self._factory_system_prompt_status = "unknown"
            self._mark_changed()
            return "unknown"
        context_details = _factory_skill_context_details(system_prompt)
        if context_details is None:
            self._factory_system_prompt_status = "unavailable"
            return self._unavailable()
        context_json, selected_entries = context_details
        if not selected_entries:
            self._factory_system_prompt_status = "unknown"
            self._mark_changed()
            return "unknown"

        prompt_bytes = system_prompt.encode("utf-8", errors="strict")
        context_bytes = context_json.encode("utf-8", errors="strict")
        if not self._reserve_content_capture(len(prompt_bytes)):
            self._factory_system_prompt_status = "unavailable"
            return self._unavailable()
        context_digest = hashlib.sha256(context_bytes).hexdigest()
        self._factory_system_prompt = system_prompt
        self._factory_system_prompt_sha256 = digest[0]
        self._factory_system_prompt_length = digest[1]
        self._factory_context_json_sha256 = context_digest
        self._factory_context_json_length = len(context_bytes)
        self._factory_system_prompt_status = "unknown"
        del prompt_bytes
        del context_bytes
        del selected_entries
        self._mark_changed()
        return "unknown"

    @_locked_collector_method
    def validate_factory_system_prompt_snapshot_before_save(
        self,
        snapshot: object,
    ) -> SkillProofFactoryPromptPreSaveProof:
        """Recheck request, epochs, revision, and prompt evidence under lock.

        This pre-save check does not write storage or mutate the caller's frozen
        snapshot. A host store still needs its own atomic epoch check.
        """
        if type(snapshot) is not SkillProofHostContentSnapshot:
            raise SkillProofFactoryPromptRejected("host_snapshot_required")
        if self._request is None:
            raise SkillProofFactoryPromptRejected("request_binding_unavailable")
        if snapshot._collector is not self:
            raise SkillProofFactoryPromptRejected("collector_changed")
        if (not self._active or not self._enabled
                or _ACTIVE_SKILL_PROOF.get() is not self
                or not self._current_binding_matches()
                or not _active_tool_event_scope_matches(self._request)):
            raise SkillProofFactoryPromptRejected("request_or_epoch_changed")
        observation = self.observation_snapshot()
        if (observation.availability != "observed"
                or snapshot.observation != observation
                or snapshot.observation.observation_revision != self._observation_revision
                or snapshot.observation.request != self._request):
            raise SkillProofFactoryPromptRejected("observation_revision_changed")
        if snapshot.content_status != "observed":
            raise SkillProofFactoryPromptRejected("host_content_unavailable")
        captures = self._freeze_host_content_captures()
        if captures is None or tuple(captures) != snapshot.content_captures:
            raise SkillProofFactoryPromptRejected("source_content_changed")
        prompt_evidence = self._freeze_factory_system_prompt()
        if (prompt_evidence is None or prompt_evidence.status != "observed"
                or prompt_evidence != snapshot.factory_system_prompt):
            raise SkillProofFactoryPromptRejected("factory_prompt_evidence_changed")
        if (prompt_evidence.system_prompt_sha256 is None
                or prompt_evidence.context_json_sha256 is None
                or prompt_evidence.request != self._request):
            raise SkillProofFactoryPromptRejected("factory_prompt_evidence_incomplete")
        return SkillProofFactoryPromptPreSaveProof(
            request=self._request,
            observation_revision=self._observation_revision,
            system_prompt_sha256=prompt_evidence.system_prompt_sha256,
            context_json_sha256=prompt_evidence.context_json_sha256,
            delivered_bodies=prompt_evidence.delivered_bodies,
            evidence_boundary="host_factory_prompt_and_langchain_callback",
            _collector=self,
        )

    @_locked_collector_method
    def observation_snapshot(self) -> SkillProofObservationSnapshot:
        """Return an immutable view of facts observed through one revision.

        A snapshot is available only in the matching live request and host
        scope. It never reports request completion, authorization, provider
        delivery, or graph acknowledgement.
        """
        revision = self._observation_revision
        empty = ((), (), (), ())
        if self._request is None:
            return SkillProofObservationSnapshot(
                "unknown", revision, None, *empty, "unknown", 0,
            )
        if (not self._active or not self._enabled
                or _ACTIVE_SKILL_PROOF.get() is not self
                or not self._current_binding_matches()
                or not _active_tool_event_scope_matches(self._request)):
            return SkillProofObservationSnapshot(
                "unavailable", revision, None, *empty, "unknown", 0,
            )
        return SkillProofObservationSnapshot(
            "observed", revision, self._request,
            tuple(self._packages[key] for key in sorted(self._packages)),
            tuple(_freeze_source(self._request, item) for item in self._sources),
            tuple(_freeze_tool(self._request, item) for item in self._tool_results),
            tuple(self._model_inputs), self._callback_status,
            self._unavailable_count,
        )

    @_locked_collector_method
    def host_verifier_snapshot(self) -> SkillProofHostContentSnapshot:
        """Return an immutable, live, host-bound view including Skill text.

        This separate API leaves ``observation_snapshot`` and final
        ``evidence`` metadata-only. Content is available only inside this
        collector's active context while the current request binding and host
        tool-event scope still match.
        """
        observation = self.observation_snapshot()
        if observation.availability != "observed":
            return SkillProofHostContentSnapshot(
                observation, "unavailable", (),
                _empty_factory_prompt_evidence(None, "unavailable"), self,
            )
        if self._content_capture_status == "unavailable":
            return SkillProofHostContentSnapshot(
                observation, "unavailable", (),
                _empty_factory_prompt_evidence(self._request, "unavailable"), self,
            )
        try:
            captures = self._freeze_host_content_captures()
            factory_prompt = self._freeze_factory_system_prompt()
        except Exception:
            captures = None
            factory_prompt = None
        if captures is None or factory_prompt is None:
            self._invalidate_content_capture()
            self._mark_changed()
            return SkillProofHostContentSnapshot(
                self.observation_snapshot(), "unavailable", (),
                _empty_factory_prompt_evidence(self._request, "unavailable"), self,
            )
        return SkillProofHostContentSnapshot(
            observation,
            "observed" if captures else "unknown",
            tuple(captures),
            factory_prompt,
            self,
        )

    @_locked_collector_method
    def _freeze_factory_system_prompt(
        self,
    ) -> SkillProofFactorySystemPromptEvidence | None:
        if self._request is None:
            return _empty_factory_prompt_evidence(None, "unavailable")
        if self._factory_system_prompt_status != "observed":
            return _empty_factory_prompt_evidence(
                self._request, self._factory_system_prompt_status,
            )
        prompt = self._factory_system_prompt
        prompt_digest = _strict_text_digest(prompt)
        if (not self._active or not self._enabled
                or self._content_capture_status == "unavailable"
                or _ACTIVE_SKILL_PROOF.get() is not self
                or not self._current_binding_matches()
                or not _active_tool_event_scope_matches(self._request)
                or prompt is None or prompt_digest is None
                or prompt_digest != (
                    self._factory_system_prompt_sha256,
                    self._factory_system_prompt_length,
                )
                or self._factory_model_sequence is None):
            return None
        context_details = _factory_skill_context_details(prompt)
        if context_details is None:
            return None
        context_json, selected_entries = context_details
        context_digest = _strict_text_digest(context_json)
        deliveries = _match_factory_skill_sources(
            selected_entries, self._sources, self._packages,
            self._factory_model_sequence,
        )
        matching_callbacks = [
            item for item in self._model_inputs
            if item.request == self._request
            and item.status == "observed"
            and item.delivery_target == "langchain.on_chat_model_start.messages"
            and item.model_invocation_sequence == self._factory_model_sequence
        ]
        if (context_digest != (
                self._factory_context_json_sha256,
                self._factory_context_json_length,
            )
                or deliveries is None
                or not deliveries
                or deliveries != self._factory_delivered_skills
                or len(matching_callbacks) != 1):
            return None
        return SkillProofFactorySystemPromptEvidence(
            request=self._request,
            status="observed",
            delivery_target="agent_factory.create_agent.system_prompt",
            system_prompt_sha256=prompt_digest[0],
            system_prompt_utf8_byte_length=prompt_digest[1],
            context_json_sha256=context_digest[0],
            context_json_utf8_byte_length=context_digest[1],
            delivered_bodies=deliveries,
            model_invocation_sequence=self._factory_model_sequence,
            system_prompt=prompt,
        )

    @_locked_collector_method
    def _observe_factory_prompt_callback(
        self,
        batches: list[list[Any]],
        sequence: int,
    ) -> None:
        if (not self._factory_system_prompt_registered
                or self._factory_system_prompt_status == "unavailable"
                or self._factory_system_prompt is None):
            return
        prompt = self._factory_system_prompt
        matching_system_messages = [
            message for message in batches[0]
            if getattr(message, "type", None) == "system"
        ]
        if (len(matching_system_messages) != 1
                or type(getattr(matching_system_messages[0], "content", None)) is not str
                or matching_system_messages[0].content != prompt):
            self._invalidate_content_capture()
            return
        callback_digest = _strict_text_digest(matching_system_messages[0].content)
        prompt_digest = _strict_text_digest(prompt)
        context_details = _factory_skill_context_details(prompt)
        if context_details is None:
            self._invalidate_content_capture()
            return
        context_json, selected_entries = context_details
        context_digest = _strict_text_digest(context_json)
        if (callback_digest is None or callback_digest != prompt_digest
                or prompt_digest != (
                    self._factory_system_prompt_sha256,
                    self._factory_system_prompt_length,
                )
                or context_digest != (
                    self._factory_context_json_sha256,
                    self._factory_context_json_length,
                )):
            self._invalidate_content_capture()
            return
        deliveries = _match_factory_skill_sources(
            selected_entries, self._sources, self._packages, sequence,
        )
        if deliveries is None or not deliveries:
            self._invalidate_content_capture()
            return
        if self._factory_system_prompt_status == "observed":
            if deliveries != self._factory_delivered_skills:
                self._invalidate_content_capture()
            else:
                self._factory_model_sequence = sequence
            return
        self._factory_system_prompt_status = "observed"
        self._factory_delivered_skills = deliveries
        self._factory_model_sequence = sequence

    def _freeze_host_content_captures(
        self,
    ) -> list[SkillProofContentCapture] | None:
        """Validate retained text against one locked metadata revision."""
        if self._request is None:
            return None
        captures: list[SkillProofContentCapture] = []
        for source in self._sources:
            if source.status != "observed":
                continue
            if source.body_text is None:
                return None
            pin = self._packages.get(source.skill_id)
            source_digest = _strict_text_digest(source.body_text)
            if (pin is None or pin.package_content_hash != source.package_content_hash
                    or source_digest is None
                    or source_digest != (source.body_sha256, source.body_length)):
                return None

            tool_result_text: str | None = None
            tool_result_digest: tuple[str, int] | None = None
            model_visible_text: str | None = None
            model_visible_digest: tuple[str, int] | None = None
            model_sequence: int | None = None

            if source.stage == "instructions":
                if source.model_status == "observed":
                    model_visible_text = source.model_visible_text
                    model_visible_digest = (
                        _strict_text_digest(model_visible_text)
                        if model_visible_text is not None else None
                    )
                    model_sequence = source.model_sequence
                    if (model_visible_text != source.body_text
                            or model_visible_digest != source_digest
                            or source.model_visible_length != source_digest[1]
                            or model_sequence is None
                            or not _model_input_has_match(
                                self._model_inputs, self._request, source,
                                model_sequence,
                            )):
                        return None
                elif (source.model_status != "unknown"
                      or source.model_visible_text is not None
                      or source.model_visible_length is not None):
                    return None
            else:
                tool_matches = [
                    item for item in self._tool_results
                    if item.tool_call_id == source.source_call_id
                ]
                if len(tool_matches) > 1:
                    return None
                if tool_matches:
                    item = tool_matches[0]
                    if (item.status != "observed"
                            or item.tool_name != "read_skill_resource"
                            or item.skill_id != source.skill_id
                            or item.package_content_hash != source.package_content_hash
                            or item.resource_path != source.source_path
                            or item.body_sha256 != source.body_sha256
                            or item.body_length != source.body_length
                            or item.result_canonical_json is None):
                        return None
                    tool_result_text = item.result_canonical_json
                    tool_result_digest = _strict_text_digest(tool_result_text)
                    if (tool_result_digest is None
                            or tool_result_digest != (
                                item.raw_result_sha256, item.raw_result_length,
                            )):
                        return None
                    parsed_result = _parse_json_object(tool_result_text)
                    canonical_result = (
                        _canonical_json_bytes(parsed_result)
                        if parsed_result is not None else None
                    )
                    if (canonical_result is None
                            or canonical_result.decode("utf-8", errors="strict")
                            != tool_result_text
                            or not _valid_resource_result(
                                parsed_result, item.tool_call_id, item.skill_id,
                                item.package_content_hash, item.resource_path,
                            )
                            or parsed_result["data"]["content"] != source.body_text
                            or _strict_text_digest(parsed_result["data"]["content"])
                            != source_digest):
                        return None

                    if item.model_status == "observed":
                        model_visible_text = item.model_visible_text
                        model_visible_digest = (
                            _strict_text_digest(model_visible_text)
                            if model_visible_text is not None else None
                        )
                        model_sequence = item.model_sequence
                        visible_result = (
                            _parse_json_object(model_visible_text)
                            if model_visible_text is not None else None
                        )
                        visible_canonical = (
                            _canonical_json_bytes(visible_result)
                            if visible_result is not None else None
                        )
                        if (model_visible_digest != (
                                item.tool_message_sha256, item.tool_message_length,
                            )
                                or item.model_visible_length != item.tool_message_length
                                or visible_canonical is None
                                or visible_canonical != canonical_result
                                or not _valid_resource_result(
                                    visible_result, item.tool_call_id, item.skill_id,
                                    item.package_content_hash, item.resource_path,
                                )
                                or visible_result["data"]["content"] != source.body_text
                                or model_sequence is None
                                or source.model_status != "observed"
                                or source.model_sequence != model_sequence
                                or not _model_input_has_match(
                                    self._model_inputs, self._request, source,
                                    model_sequence,
                                )):
                            return None
                    elif (item.model_status != "unknown"
                          or item.model_visible_text is not None
                          or item.model_visible_length is not None
                          or source.model_status != "unknown"):
                        return None
                elif source.model_status != "unknown":
                    return None

            captures.append(SkillProofContentCapture(
                request=self._request,
                source_call_id=source.source_call_id,
                source_call_kind=_source_call_kind(source.stage),
                skill_id=source.skill_id,
                package_content_hash=source.package_content_hash,
                stage=source.stage,
                source_path=source.source_path,
                source_body=source.body_text,
                source_body_sha256=source_digest[0],
                source_body_utf8_byte_length=source_digest[1],
                tool_result_canonical_json=tool_result_text,
                tool_result_sha256=(
                    tool_result_digest[0] if tool_result_digest is not None else None
                ),
                tool_result_utf8_byte_length=(
                    tool_result_digest[1] if tool_result_digest is not None else None
                ),
                model_visible_text=model_visible_text,
                model_visible_text_sha256=(
                    model_visible_digest[0]
                    if model_visible_digest is not None else None
                ),
                model_visible_text_utf8_byte_length=(
                    model_visible_digest[1]
                    if model_visible_digest is not None else None
                ),
                model_invocation_sequence=model_sequence,
            ))
        return captures

    @_locked_collector_method
    def pin_packages(self, packages: Sequence[SkillPackagePin]) -> EvidenceStatus:
        """Pin package content hashes before source loading; idempotent if equal."""
        if not self._usable():
            return "unavailable"
        try:
            values = tuple(packages)
            if not 1 <= len(values) <= _MAX_PACKAGES or any(
                type(value) is not SkillPackagePin or not _valid_pin(value)
                for value in values
            ):
                return self._unavailable()
            keyed = {value.skill_id: value for value in values}
            if len(keyed) != len(values):
                return self._unavailable()
            if self._packages and self._packages != keyed:
                return self._mismatch()
            self._packages = keyed
            self._mark_changed()
            return "observed"
        except Exception:
            return self._unavailable()

    @_locked_collector_method
    def source_loaded(
        self,
        *,
        skill_id: str,
        package_content_hash: str,
        expected_content_hash: str,
        revalidated_content_hash: str,
        stage: SourceStage,
        source_path: str,
        source_call_id: str,
        body: str,
    ) -> EvidenceStatus:
        """Record a complete source load with its host-only call ID.

        Manual preload uses the host-generated ``load_skill`` call ID. A
        resource read uses the matching ``read_skill_resource`` call ID. IDs
        are correlation-only; they never identify or authorize a principal.
        The expected and post-read revalidated content hashes must both match
        the request pin. This collector accepts those values from host code; it
        does not observe separate repository snapshots itself.
        """
        if not self._usable():
            return "unavailable"
        try:
            if not _valid_call_id(source_call_id):
                return self._unavailable()
            if (type(body) is not str or "\x00" in body
                    or not _valid_source_id(skill_id, package_content_hash,
                                            stage, source_path)):
                return self._unavailable()
            body_bytes = body.encode("utf-8", errors="strict")
            if len(body_bytes) > _MAX_SOURCE_BYTES or len(self._sources) >= _MAX_SOURCES:
                del body_bytes
                return self._unavailable()
            body_hash = hashlib.sha256(body_bytes).hexdigest()
            body_length = len(body_bytes)
            del body_bytes
            pin = self._packages.get(skill_id)
            status: EvidenceStatus = (
                "observed" if pin is not None
                and pin.package_content_hash == package_content_hash
                and type(expected_content_hash) is str
                and expected_content_hash == pin.package_content_hash
                and type(revalidated_content_hash) is str
                and revalidated_content_hash == pin.package_content_hash
                else "mismatch"
            )
            duplicate_call_id = (
                any(source.source_call_id == source_call_id for source in self._sources)
                or any(item.tool_call_id == source_call_id for item in self._tool_results)
            )
            if duplicate_call_id:
                for source in self._sources:
                    if source.source_call_id == source_call_id:
                        source.status = "mismatch"
                        source.model_status = "mismatch"
                        source.body_sha256 = None
                        source.body_length = None
                for item in self._tool_results:
                    if item.tool_call_id == source_call_id:
                        item.status = "mismatch"
                        item.model_status = "mismatch"
                status = "mismatch"
            if status == "mismatch":
                body_hash = None
                body_length = None
                self._invalidate_content_capture()
            retained_body = None
            if status == "observed":
                if self._reserve_content_capture(body_length):
                    retained_body = body
            self._sources.append(_SourceRecord(
                skill_id, package_content_hash, stage,
                source_path, source_call_id, status, body_hash, body_length,
                body_text=retained_body,
            ))
            self._mark_changed()
            return status
        except Exception:
            return self._unavailable()

    @_locked_collector_method
    def tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: Mapping[str, Any],
        result: object,
    ) -> EvidenceStatus:
        """Record an actual host ToolRouter return using metadata and digests.

        ``tool_call_id`` remains correlation-only. The later LangChain callback
        must find one matching AI tool call and one successful ToolMessage with
        this ID, exact Skill/path arguments, and the same canonical result.
        """
        if not self._usable():
            return "unavailable"
        try:
            if not _valid_call_id(tool_call_id):
                return self._unavailable()
            if (tool_name != "read_skill_resource" or type(arguments) is not dict
                    or set(arguments) != {"skill_id", "path"}):
                return self._mismatch()
            skill_id, resource_path = arguments.get("skill_id"), arguments.get("path")
            pin = self._packages.get(skill_id) if type(skill_id) is str else None
            if (pin is None or not _valid_resource_path(resource_path)
                    or len(self._tool_results) >= _MAX_TOOL_RESULTS):
                return self._unavailable()
            canonical = _canonical_json_bytes(result)
            if canonical is None or len(canonical) > _MAX_TOOL_RESULT_BYTES:
                return self._unavailable()
            raw_hash = hashlib.sha256(canonical).hexdigest()
            raw_length = len(canonical)
            canonical_text = canonical.decode("utf-8", errors="strict")
            parsed = _parse_json_object(result)
            if parsed is None:
                del canonical
                return self._mismatch()
            data = parsed.get("data")
            content = data.get("content") if type(data) is dict else None
            if (not _valid_resource_result(parsed, tool_call_id, skill_id,
                                           pin.package_content_hash, resource_path)
                    or type(content) is not str):
                del canonical
                return self._mismatch()
            body_bytes = content.encode("utf-8", errors="strict")
            if len(body_bytes) > _MAX_SOURCE_BYTES:
                del canonical
                del body_bytes
                return self._unavailable()
            body_hash = hashlib.sha256(body_bytes).hexdigest()
            body_length = len(body_bytes)
            del body_bytes
            del canonical
            source = self._find_source(
                skill_id, pin.package_content_hash,
                "resource", resource_path, source_call_id=tool_call_id,
            )
            call_id_sources = [candidate for candidate in self._sources
                               if candidate.source_call_id == tool_call_id]
            if any(candidate.stage != "resource" for candidate in call_id_sources):
                for candidate in call_id_sources:
                    candidate.status = "mismatch"
                    candidate.model_status = "mismatch"
                    candidate.body_sha256 = None
                    candidate.body_length = None
            status: EvidenceStatus = (
                "observed" if source is not None
                and len(call_id_sources) == 1
                and source.status == "observed"
                and source.body_sha256 == body_hash
                and source.body_length == body_length
                else "mismatch"
            )
            if any(item.tool_call_id == tool_call_id for item in self._tool_results):
                for item in self._tool_results:
                    if item.tool_call_id == tool_call_id:
                        item.status = "mismatch"
                        item.model_status = "mismatch"
                status = "mismatch"
                self._invalidate_content_capture()
            if status == "mismatch":
                self._invalidate_content_capture()
            retained_result = None
            if status == "observed":
                if self._reserve_content_capture(raw_length):
                    retained_result = canonical_text
            self._tool_results.append(_ToolRecord(
                tool_name, tool_call_id, skill_id,
                pin.package_content_hash, resource_path, status, raw_hash,
                raw_length, body_hash if status == "observed" else None,
                body_length if status == "observed" else None,
                result_canonical_json=retained_result,
            ))
            self._mark_changed()
            return status
        except Exception:
            return self._unavailable()

    @_locked_collector_method
    def observe_chat_model_input(self, messages: object) -> None:
        """Observe only real callback ``messages``; never infer send or ACK."""
        if not self._active:
            return
        if not self._usable(graph_callback=True):
            self._callback_status = "unavailable"
            return
        try:
            projection, batches = _project_model_messages(messages)
            if projection is None or batches is None:
                self._callback_status = "unavailable"
                self._unavailable()
                return
            payload = json.dumps(
                projection, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8", errors="strict")
            if len(payload) > _MAX_MODEL_INPUT_BYTES:
                del payload
                self._callback_status = "unavailable"
                self._unavailable()
                return
            if len(self._model_inputs) >= _MAX_MODEL_INPUTS:
                del payload
                self._callback_status = "unavailable"
                self._unavailable()
                return
            input_hash = hashlib.sha256(payload).hexdigest()
            input_length = len(payload)
            del payload
            self._sequence += 1
            sequence = self._sequence
            self._callback_status = "observed"
            matched = 0
            matches: list[SkillModelInputMatch] = []
            content_updates: list[tuple[_SourceRecord | _ToolRecord, str, int]] = []

            for source in self._sources:
                if source.status != "observed" or source.stage != "instructions":
                    continue
                count, mismatched, visible_body, visible_length = _manual_body_matches(
                    batches, source.skill_id, source.package_content_hash,
                    source.body_sha256, source.body_length,
                )
                same_source_identity = [candidate for candidate in self._sources
                                        if (candidate.status == "observed"
                                            and candidate.stage == "instructions"
                                            and candidate.skill_id == source.skill_id
                                            and candidate.package_content_hash == source.package_content_hash
                                            and candidate.body_sha256 == source.body_sha256
                                            and candidate.body_length == source.body_length)]
                if (count == 1 and not mismatched
                        and len(same_source_identity) == 1):
                    source.model_status = "observed"
                    source.model_sha256 = input_hash
                    source.model_length = input_length
                    source.model_sequence = sequence
                    matched += 1
                    matches.append(_model_input_match(source))
                    if (source.body_text is None
                            or visible_body != source.body_text
                            or visible_length != source.body_length):
                        self._invalidate_content_capture()
                    elif visible_body is not None and visible_length is not None:
                        content_updates.append((source, visible_body, visible_length))
                elif count > 1 or mismatched or len(same_source_identity) > 1:
                    for candidate in same_source_identity:
                        candidate.model_status = "mismatch"
                    self._invalidate_content_capture()

            for item in self._tool_results:
                if item.status != "observed":
                    continue
                (match_status, tool_message_hash, tool_message_length,
                 tool_message_text) = _match_tool_result(batches, item)
                if match_status == "observed":
                    source = self._find_source(
                        item.skill_id, item.package_content_hash,
                        "resource", item.resource_path,
                        source_call_id=item.tool_call_id,
                    )
                    if (source is None or source.status != "observed"
                            or source.body_sha256 != item.body_sha256
                            or source.body_length != item.body_length):
                        match_status = "mismatch"
                    else:
                        item.model_status = "observed"
                        item.tool_message_sha256 = tool_message_hash
                        item.tool_message_length = tool_message_length
                        item.model_sha256 = input_hash
                        item.model_length = input_length
                        item.model_sequence = sequence
                        source.model_status = "observed"
                        source.model_sha256 = input_hash
                        source.model_length = input_length
                        source.model_sequence = sequence
                        matched += 1
                        matches.append(_model_input_match(source))
                        if (tool_message_text is None
                                or tool_message_length is None
                                or item.result_canonical_json is None):
                            self._invalidate_content_capture()
                        else:
                            content_updates.append((
                                item, tool_message_text, tool_message_length,
                            ))
                if match_status == "mismatch":
                    item.model_status = "mismatch"
                    item.model_visible_text = None
                    item.model_visible_length = None
                    self._invalidate_content_capture()
                elif match_status == "unavailable":
                    self._invalidate_content_capture()

            self._replace_model_content_captures(content_updates)
            self._model_inputs.append(SkillModelInputEvidence(
                self._request, "observed", "langchain.on_chat_model_start.messages",
                input_hash, input_length, sequence, matched, tuple(matches),
            ))
            self._observe_factory_prompt_callback(batches, sequence)
            self._mark_changed()
        except Exception:
            self._callback_status = "unavailable"
            self._unavailable()

    @_locked_collector_method
    def _find_source(
        self,
        skill_id: str,
        package_hash: str,
        stage: SourceStage,
        path: str,
        *,
        source_call_id: str,
    ) -> _SourceRecord | None:
        matches = [source for source in self._sources
                   if (source.skill_id == skill_id
                       and source.package_content_hash == package_hash
                       and source.stage == stage and source.source_path == path
                       and source.source_call_id == source_call_id)]
        return matches[0] if len(matches) == 1 else None

    @_locked_collector_method
    def _current_binding_matches(self) -> bool:
        if self._request is None:
            return False
        try:
            if not callable(self._current_binding):
                return False
            current = self._current_binding()
            return type(current) is SkillProofRequestBinding and current == self._request
        except Exception:
            return False

    @_locked_collector_method
    def _usable(self, *, graph_callback: bool = False) -> bool:
        if not self._active:
            return False
        if not self._enabled or not self._current_binding_matches():
            self._invalidate_content_capture()
            self._unavailable_count += 1
            self._mark_changed()
            return False
        if graph_callback and not _active_tool_event_scope_matches(self._request):
            self._invalidate_content_capture()
            self._unavailable_count += 1
            self._mark_changed()
            return False
        return True

    @_locked_collector_method
    def _unavailable(self) -> EvidenceStatus:
        self._invalidate_content_capture()
        self._unavailable_count += 1
        self._mark_changed()
        return "unavailable"

    @_locked_collector_method
    def _mismatch(self) -> EvidenceStatus:
        self._invalidate_content_capture()
        self._unavailable_count += 1
        self._mark_changed()
        return "mismatch"

    @_locked_collector_method
    def _disable(self) -> None:
        self._enabled = False
        self._host_scope = None
        self._invalidate_content_capture()
        self._unavailable_count += 1
        self._mark_changed()

    def _reserve_content_capture(self, byte_length: int | None) -> bool:
        """Reserve retained UTF-8 bytes or fail closed for the whole request."""
        if self._content_capture_status == "unavailable":
            return False
        if (type(byte_length) is not int or byte_length < 0
                or self._content_capture_bytes + byte_length
                > _MAX_REQUEST_CONTENT_CAPTURE_BYTES):
            self._invalidate_content_capture()
            return False
        self._content_capture_bytes += byte_length
        self._content_capture_status = "observed"
        return True

    def _replace_model_content_captures(
        self,
        updates: Sequence[tuple[_SourceRecord | _ToolRecord, str, int]],
    ) -> bool:
        """Replace callback text atomically under the request byte budget."""
        if self._content_capture_status == "unavailable":
            return False
        byte_total = self._content_capture_bytes
        for record, _text, byte_length in updates:
            previous_length = record.model_visible_length or 0
            if (type(byte_length) is not int or byte_length < 0
                    or byte_total < previous_length):
                self._invalidate_content_capture()
                return False
            byte_total += byte_length - previous_length
        if byte_total > _MAX_REQUEST_CONTENT_CAPTURE_BYTES:
            self._invalidate_content_capture()
            return False
        self._content_capture_bytes = byte_total
        if updates:
            self._content_capture_status = "observed"
        for record, text, byte_length in updates:
            record.model_visible_text = text
            record.model_visible_length = byte_length
        return True

    def _invalidate_content_capture(self) -> None:
        """Drop every request-local body/result reference after an unsafe edge."""
        self._content_capture_status = "unavailable"
        self._content_capture_bytes = 0
        self._factory_system_prompt_status = "unavailable"
        self._factory_system_prompt = None
        self._factory_system_prompt_sha256 = None
        self._factory_system_prompt_length = None
        self._factory_context_json_sha256 = None
        self._factory_context_json_length = None
        self._factory_delivered_skills = ()
        self._factory_model_sequence = None
        for source in self._sources:
            source.body_text = None
            source.model_visible_text = None
            source.model_visible_length = None
        for item in self._tool_results:
            item.result_canonical_json = None
            item.model_visible_text = None
            item.model_visible_length = None

    @_locked_collector_method
    def _close(self, *, outcome: Literal["completed", "unknown"]) -> None:
        if not self._active:
            return
        if outcome == "completed" and not self._current_binding_matches():
            outcome = "unknown"
        if outcome == "unknown":
            self._result = SkillProofRequestEvidence(
                self._request, "unknown", "unknown", (), (), (),
                self._unavailable_count + 1,
            )
        else:
            self._result = SkillProofRequestEvidence(
                self._request, "completed", self._callback_status,
                tuple(_freeze_source(self._request, item) for item in self._sources),
                tuple(_freeze_tool(self._request, item) for item in self._tool_results),
                tuple(self._model_inputs), self._unavailable_count,
            )
        self._invalidate_content_capture()
        self._sources.clear()
        self._tool_results.clear()
        self._model_inputs.clear()
        self._packages.clear()
        self._current_binding = None
        self._host_scope = None
        self._factory_system_prompt_registered = False
        self._active = False
        self._mark_changed()

    @_locked_collector_method
    def _close_unavailable(self) -> None:
        if not self._active:
            return
        self._result = SkillProofRequestEvidence(
            self._request, "unknown", "unavailable", (), (), (),
            self._unavailable_count + 1,
        )
        self._invalidate_content_capture()
        self._sources.clear()
        self._tool_results.clear()
        self._model_inputs.clear()
        self._packages.clear()
        self._current_binding = None
        self._host_scope = None
        self._factory_system_prompt_registered = False
        self._active = False
        self._mark_changed()

    def _mark_changed(self) -> None:
        """Advance the revision for a completed state mutation under ``_lock``."""
        self._observation_revision += 1


class SkillProofCallback(BaseCallbackHandler):
    """LangChain callback: only ``on_chat_model_start`` can prove model input."""

    run_inline = True

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        collector = current_skill_proof_collector()
        if collector is not None:
            collector.observe_chat_model_input(messages)


def _new_factory_context(
    request_snapshot: object,
    host_scope: object,
) -> _SkillProofFactoryContext | None:
    try:
        collector = current_skill_proof_collector()
        if type(collector) is not SkillProofCollector:
            return None
        with collector._lock:
            request = _binding_from_snapshot(request_snapshot, host_scope)
            if (
                not collector._active
                or not collector._enabled
                or _ACTIVE_SKILL_PROOF.get() is not collector
                or collector._host_scope is not host_scope
                or request is None
                or request != collector._request
                or not _factory_snapshot_matches_request(
                    request_snapshot, host_scope, request,
                )
                or not collector._current_binding_matches()
            ):
                return None
            return _SkillProofFactoryContext(
                collector=collector,
                request_snapshot=request_snapshot,
                host_scope=host_scope,
                request=request,
            )
    except Exception:
        return None


def _factory_context_is_current(
    factory_context: _SkillProofFactoryContext | None,
) -> bool:
    if (type(factory_context) is not _SkillProofFactoryContext
            or not factory_context.active
            or factory_context.collector is None
            or factory_context.request_snapshot is None
            or factory_context.host_scope is None
            or _ACTIVE_SKILL_PROOF_FACTORY_CONTEXT.get() is not factory_context):
        return False
    try:
        collector = current_skill_proof_collector()
        if collector is not factory_context.collector:
            return False
        with collector._lock:
            request = _binding_from_snapshot(
                factory_context.request_snapshot, factory_context.host_scope,
            )
            return (
                collector._active
                and collector._enabled
                and _ACTIVE_SKILL_PROOF.get() is collector
                and collector._host_scope is factory_context.host_scope
                and collector._request == factory_context.request
                and request == factory_context.request
                and _factory_snapshot_matches_request(
                    factory_context.request_snapshot,
                    factory_context.host_scope,
                    factory_context.request,
                )
                and collector._current_binding_matches()
            )
    except Exception:
        return False


def _factory_snapshot_matches_request(
    snapshot: object,
    host_scope: object,
    request: SkillProofRequestBinding,
) -> bool:
    return (
        getattr(snapshot, "tool_event_scope", None) is host_scope
        and getattr(snapshot, "principal_hash", None) == request.principal_hash
        and getattr(snapshot, "business_session_id", None) == request.session_id
        and getattr(snapshot, "request_id", None) == request.request_id
        and getattr(snapshot, "session_delete_epoch", None)
        == request.session_delete_epoch
        and getattr(snapshot, "memory_clear_epoch", None)
        == request.memory_clear_epoch
    )


def _binding_from_snapshot(
    snapshot: object, host_scope: object,
) -> SkillProofRequestBinding | None:
    try:
        from app.api.context_events import ContextEventScope

        if type(host_scope) is not ContextEventScope:
            return None
        if getattr(snapshot, "tool_event_scope", None) is not host_scope:
            return None
        principal_hash = getattr(snapshot, "principal_hash", None)
        session_epoch = getattr(snapshot, "session_delete_epoch", None)
        snapshot_session = getattr(snapshot, "business_session_id", "")
        snapshot_request = getattr(snapshot, "request_id", "")
        memory_epoch = getattr(snapshot, "memory_clear_epoch", None)
        if (principal_hash != host_scope.principal_hash
                or session_epoch != host_scope.session_epoch
                or snapshot_session not in ("", host_scope.session_id)
                or snapshot_request not in ("", host_scope.request_id)):
            return None
        binding = SkillProofRequestBinding(
            host_scope.principal_hash,
            host_scope.session_id,
            host_scope.request_id,
            host_scope.session_epoch,
            memory_epoch,
        )
        return binding if _valid_binding(binding) else None
    except Exception:
        return None


def _active_tool_event_scope_matches(
    request: SkillProofRequestBinding | None,
) -> bool:
    if request is None:
        return False
    try:
        from app.api.context_events import ContextEventScope
        from app.api.tool_events import get_tool_event_scope

        scope = get_tool_event_scope()
        return (
            type(scope) is ContextEventScope
            and scope.principal_hash == request.principal_hash
            and scope.session_id == request.session_id
            and scope.request_id == request.request_id
            and scope.session_epoch == request.session_delete_epoch
        )
    except Exception:
        return False


def _valid_binding(value: SkillProofRequestBinding) -> bool:
    return (
        type(value.principal_hash) is str and _HASH_RE.fullmatch(value.principal_hash) is not None
        and type(value.session_id) is str and 1 <= len(value.session_id) <= 128
        and value.session_id == value.session_id.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value.session_id)
        and type(value.request_id) is str and _REQUEST_ID_RE.fullmatch(value.request_id) is not None
        and type(value.session_delete_epoch) is str
        and (value.session_delete_epoch == ""
             or _REQUEST_ID_RE.fullmatch(value.session_delete_epoch) is not None)
        and (value.memory_clear_epoch is None or type(value.memory_clear_epoch) is str)
    )


def _valid_pin(value: SkillPackagePin) -> bool:
    return (
        type(value.skill_id) is str and _SKILL_ID_RE.fullmatch(value.skill_id) is not None
        and type(value.package_content_hash) is str
        and _HASH_RE.fullmatch(value.package_content_hash) is not None
    )


def _valid_call_id(value: object) -> bool:
    return (
        type(value) is str and _CALL_ID_RE.fullmatch(value) is not None
        and bool(value.strip())
    )


def _valid_source_id(
    skill_id: object,
    package_hash: object,
    stage: object,
    path: object,
) -> bool:
    return (
        type(skill_id) is str and _SKILL_ID_RE.fullmatch(skill_id) is not None
        and type(package_hash) is str and _HASH_RE.fullmatch(package_hash) is not None
        and stage in {"instructions", "resource"}
        and type(path) is str and 1 <= len(path) <= 512
        and not any(ord(char) < 32 or ord(char) == 127 for char in path)
        and (stage != "resource" or _valid_resource_path(path))
    )


def _valid_resource_path(value: object) -> bool:
    if (type(value) is not str or not value or "\\" in value or ":" in value
            or value.startswith("/") or "\x00" in value):
        return False
    parts = value.split("/")
    return (
        len(parts) >= 2 and parts[0] in {"references", "assets"}
        and all(part not in {"", ".", ".."} for part in parts)
        and all(part == unicodedata.normalize("NFC", part) for part in parts)
        and all(part == part.strip() and not part.endswith((" ", ".")) for part in parts)
        and not any(ord(char) < 32 or ord(char) == 127 or char in '<>"|?*'
                    for char in value)
    )


def _canonical_json_bytes(value: object) -> bytes | None:
    if not _is_json_value(value):
        return None
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None


def _is_json_value(value: object, depth: int = 0) -> bool:
    if depth > _MAX_JSON_DEPTH:
        return False
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_is_json_value(item, depth + 1) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _is_json_value(item, depth + 1)
                   for key, item in value.items())
    return False


def _parse_json_object(value: object) -> dict[str, Any] | None:
    if type(value) is str:
        text = value
    elif type(value) is dict:
        text = None
    else:
        return None
    try:
        parsed = json.loads(text, object_pairs_hook=_unique_object) if text is not None else value
    except (TypeError, ValueError, RecursionError):
        return None
    return parsed if type(parsed) is dict else None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _valid_resource_result(
    result: dict[str, Any], call_id: str, skill_id: str, package_hash: str,
    path: str,
) -> bool:
    data, metadata = result.get("data"), result.get("metadata")
    return (
        set(result) == _RESULT_KEYS
        and result.get("success") is True
        and result.get("output") == "Skill resource loaded"
        and result.get("error") is None
        and type(data) is dict and set(data) == _RESOURCE_DATA_KEYS
        and data.get("success") is True and data.get("stage") == "resource"
        and data.get("skill_id") == skill_id
        and data.get("content_hash") == package_hash
        and data.get("path") == path and type(data.get("content")) is str
        and type(metadata) is dict
        and {"tool_name", "call_id", "compacted"}.issubset(metadata)
        and set(metadata).issubset(_RESULT_METADATA_KEYS)
        and metadata.get("tool_name") == "read_skill_resource"
        and metadata.get("call_id") == call_id
        and metadata.get("compacted") is False
    )


def _project_model_messages(value: object) -> tuple[dict[str, Any] | None, list[list[Any]] | None]:
    try:
        from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

        if type(value) is not list or len(value) != 1 or type(value[0]) is not list:
            return None, None
        batch = value[0]
        projected: list[dict[str, Any]] = []
        for message in batch:
            if not isinstance(message, BaseMessage):
                return None, None
            content = message.content
            record: dict[str, Any] = {
                "message_type": type(message).__name__,
                "role": message.type,
                "content": content,
            }
            name = getattr(message, "name", None)
            if name is not None:
                if type(name) is not str:
                    return None, None
                record["name"] = name
            if isinstance(message, AIMessage):
                record["tool_calls"] = message.tool_calls
            if isinstance(message, ToolMessage):
                record["tool_call_id"] = message.tool_call_id
                record["status"] = message.status
            if not _is_json_value(record):
                return None, None
            projected.append(record)
        return {"messages": projected}, [batch]
    except Exception:
        return None, None


def _manual_body_matches(
    batches: list[list[Any]],
    skill_id: str,
    package_hash: str,
    body_hash: str | None,
    body_length: int | None,
) -> tuple[int, bool, str | None, int | None]:
    if body_hash is None or body_length is None:
        return 0, False, None, None
    count = 0
    mismatch = False
    visible_body: str | None = None
    visible_length: int | None = None
    for message in batches[0]:
        if getattr(message, "type", None) != "system":
            continue
        content = getattr(message, "content", None)
        if type(content) is not str:
            continue
        entries, ambiguous = _selected_skill_entries(content)
        mismatch = mismatch or ambiguous
        for entry in entries:
            if entry[0] != skill_id or entry[1] != package_hash:
                continue
            if entry[2] == body_hash and entry[3] == body_length:
                count += 1
                visible_body = entry[4]
                visible_length = entry[3]
            else:
                mismatch = True
    if count != 1:
        return count, mismatch, None, None
    return count, mismatch, visible_body, visible_length


def _selected_skill_entries(
    content: str,
) -> tuple[list[tuple[str, str, str, int, str]], bool]:
    marker = '"selected_skills"'
    positions: list[int] = []
    offset = 0
    while True:
        index = content.find(marker, offset)
        if index < 0:
            break
        positions.append(index)
        if len(positions) > 16:
            return [], True
        offset = index + len(marker)
    entries: list[tuple[str, str, str, int, str]] = []
    ambiguous = False
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
    for position in positions:
        start = content.rfind("{", 0, position)
        if start < 0:
            continue
        try:
            parsed, _ = decoder.raw_decode(content, start)
        except (ValueError, RecursionError):
            continue
        selected = parsed.get("selected_skills") if type(parsed) is dict else None
        if type(selected) is not list:
            continue
        for item in selected:
            if (type(item) is not dict
                    or set(item) != {"skill_id", "content_hash", "instructions"}
                    or type(item.get("skill_id")) is not str
                    or type(item.get("content_hash")) is not str
                    or type(item.get("instructions")) is not str):
                ambiguous = True
                continue
            try:
                body = item["instructions"].encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                ambiguous = True
                continue
            entries.append((
                item["skill_id"], item["content_hash"],
                hashlib.sha256(body).hexdigest(), len(body), item["instructions"],
            ))
            del body
    return entries, ambiguous


def _empty_factory_prompt_evidence(
    request: SkillProofRequestBinding | None,
    status: EvidenceStatus,
) -> SkillProofFactorySystemPromptEvidence:
    return SkillProofFactorySystemPromptEvidence(
        request=request,
        status=status,
        delivery_target="agent_factory.create_agent.system_prompt",
    )


def _factory_skill_context_details(
    content: str,
) -> tuple[str, tuple[tuple[str, str, str, int, str], ...]] | None:
    """Parse the exact selected-skills JSON suffix emitted by the prompt builder."""
    marker = '"selected_skills"'
    positions: list[int] = []
    offset = 0
    while True:
        index = content.find(marker, offset)
        if index < 0:
            break
        positions.append(index)
        if len(positions) > 1:
            return None
        offset = index + len(marker)
    if len(positions) != 1:
        return None

    start = content.rfind("{", 0, positions[0])
    if start < 0:
        return None
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
    try:
        parsed, end = decoder.raw_decode(content, start)
    except (TypeError, ValueError, RecursionError):
        return None
    if (end != len(content) or type(parsed) is not dict
            or set(parsed) != {"selected_skills"}
            or type(parsed.get("selected_skills")) is not list):
        return None

    selected = parsed["selected_skills"]
    if len(selected) > _MAX_FACTORY_SKILLS:
        return None
    context: list[dict[str, str]] = []
    entries: list[tuple[str, str, str, int, str]] = []
    for item in selected:
        if (type(item) is not dict
                or set(item) != {"skill_id", "content_hash", "instructions"}
                or type(item.get("skill_id")) is not str
                or type(item.get("content_hash")) is not str
                or type(item.get("instructions")) is not str):
            return None
        body_text = item["instructions"]
        try:
            body = body_text.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return None
        context.append({
            "skill_id": item["skill_id"],
            "content_hash": item["content_hash"],
            "instructions": body_text,
        })
        entries.append((
            item["skill_id"], item["content_hash"],
            hashlib.sha256(body).hexdigest(), len(body), body_text,
        ))
        del body

    try:
        expected_context = json.dumps(
            {"selected_skills": context}, ensure_ascii=False, allow_nan=False,
        )
        actual_context = content[start:end]
        actual_bytes = actual_context.encode("utf-8", errors="strict")
        expected_bytes = expected_context.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return None
    if actual_bytes != expected_bytes:
        return None
    if len(actual_bytes) > _MAX_FACTORY_SKILL_CONTEXT_BYTES:
        return None
    return actual_context, tuple(entries)


def _match_factory_skill_sources(
    selected_entries: Sequence[tuple[str, str, str, int, str]],
    sources: Sequence[_SourceRecord],
    packages: Mapping[str, SkillPackagePin],
    model_sequence: int | None,
) -> tuple[SkillInstructionPromptDelivery, ...] | None:
    if not selected_entries or model_sequence is None:
        return None
    deliveries: list[SkillInstructionPromptDelivery] = []
    seen_call_ids: set[str] = set()
    seen_skills: set[tuple[str, str]] = set()
    for skill_id, package_hash, body_hash, body_length, body_text in selected_entries:
        identity = (skill_id, package_hash)
        if identity in seen_skills:
            return None
        seen_skills.add(identity)
        pin = packages.get(skill_id)
        matching_sources = [
            item for item in sources
            if item.status == "observed"
            and item.stage == "instructions"
            and item.skill_id == skill_id
            and item.package_content_hash == package_hash
        ]
        if len(matching_sources) != 1 or pin is None:
            return None
        source = matching_sources[0]
        source_digest = _strict_text_digest(source.body_text)
        if (pin.package_content_hash != package_hash
                or source.body_text is None
                or source_digest != (source.body_sha256, source.body_length)
                or source_digest != (body_hash, body_length)
                or source.body_text != body_text
                or source.model_status != "observed"
                or source.model_sequence != model_sequence
                or not _valid_call_id(source.source_call_id)
                or source.source_call_id in seen_call_ids):
            return None
        seen_call_ids.add(source.source_call_id)
        deliveries.append(SkillInstructionPromptDelivery(
            source_call_id=source.source_call_id,
            skill_id=skill_id,
            content_hash=package_hash,
            instructions_sha256=body_hash,
            instructions_byte_length=body_length,
        ))
    return tuple(deliveries)


def _match_tool_result(
    batches: list[list[Any]], item: _ToolRecord,
) -> tuple[EvidenceStatus, str | None, int | None, str | None]:
    try:
        from langchain_core.messages import AIMessage, ToolMessage

        ai_matches: list[Any] = []
        tool_matches: list[Any] = []
        for message in batches[0]:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    if type(call) is dict and call.get("id") == item.tool_call_id:
                        ai_matches.append(call)
            if isinstance(message, ToolMessage) and message.tool_call_id == item.tool_call_id:
                tool_matches.append(message)
        if len(ai_matches) != 1 or len(tool_matches) != 1:
            return "mismatch", None, None, None
        call = ai_matches[0]
        if (call.get("name") != item.tool_name or type(call.get("args")) is not dict
                or set(call["args"]) != {"skill_id", "path"}
                or call["args"].get("skill_id") != item.skill_id
                or call["args"].get("path") != item.resource_path):
            return "mismatch", None, None, None
        message = tool_matches[0]
        if (message.name != item.tool_name or message.status != "success"
                or type(message.content) is not str):
            return "mismatch", None, None, None
        message_bytes = message.content.encode("utf-8", errors="strict")
        if len(message_bytes) > _MAX_TOOL_RESULT_BYTES:
            return "unavailable", None, None, None
        tool_message_hash = hashlib.sha256(message_bytes).hexdigest()
        tool_message_length = len(message_bytes)
        parsed = _parse_json_object(message.content)
        canonical = _canonical_json_bytes(parsed) if parsed is not None else None
        if canonical is None or len(canonical) > _MAX_TOOL_RESULT_BYTES:
            return "mismatch", None, None, None
        result_hash = hashlib.sha256(canonical).hexdigest()
        result_length = len(canonical)
        del canonical
        if (result_hash != item.raw_result_sha256
                or result_length != item.raw_result_length
                or not _valid_resource_result(
                    parsed, item.tool_call_id, item.skill_id,
                    item.package_content_hash, item.resource_path,
                )):
            return "mismatch", None, None, None
        body = parsed["data"]["content"].encode("utf-8", errors="strict")
        body_hash = hashlib.sha256(body).hexdigest()
        body_length = len(body)
        del body
        if body_hash != item.body_sha256 or body_length != item.body_length:
            return "mismatch", None, None, None
        return "observed", tool_message_hash, tool_message_length, message.content
    except Exception:
        return "unavailable", None, None, None


def _strict_text_digest(value: object) -> tuple[str, int] | None:
    if type(value) is not str:
        return None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _model_input_has_match(
    observations: Sequence[SkillModelInputEvidence],
    request: SkillProofRequestBinding,
    source: _SourceRecord,
    sequence: int,
) -> bool:
    matching_observations = [
        item for item in observations
        if item.request == request
        and item.status == "observed"
        and item.delivery_target == "langchain.on_chat_model_start.messages"
        and item.model_invocation_sequence == sequence
    ]
    if len(matching_observations) != 1:
        return False
    matches = [
        item for item in matching_observations[0].matches
        if item.source_call_id == source.source_call_id
        and item.source_call_kind == _source_call_kind(source.stage)
        and item.skill_id == source.skill_id
        and item.package_content_hash == source.package_content_hash
        and item.source_stage == source.stage
        and item.body_sha256 == source.body_sha256
        and item.body_utf8_byte_length == source.body_length
    ]
    return len(matches) == 1


def _freeze_source(
    request: SkillProofRequestBinding | None, item: _SourceRecord,
) -> SkillSourceEvidence:
    return SkillSourceEvidence(
        request, item.skill_id, item.package_content_hash,
        item.stage, item.source_path, item.source_call_id,
        _source_call_kind(item.stage), "correlation_only", item.status,
        item.body_sha256, item.body_length, item.model_status, item.model_sha256,
        item.model_length, item.model_sequence,
    )


def _freeze_tool(
    request: SkillProofRequestBinding | None, item: _ToolRecord,
) -> SkillToolResultEvidence:
    return SkillToolResultEvidence(
        request, item.tool_name, item.tool_call_id, "correlation_only", item.skill_id,
        item.package_content_hash, item.resource_path,
        item.status, item.raw_result_sha256, item.raw_result_length,
        item.tool_message_sha256, item.tool_message_length, item.body_sha256,
        item.body_length, item.model_status, item.model_sha256,
        item.model_length, item.model_sequence,
    )


def _source_call_kind(stage: SourceStage) -> Literal["load_skill", "read_skill_resource"]:
    return "load_skill" if stage == "instructions" else "read_skill_resource"


def _model_input_match(source: _SourceRecord) -> SkillModelInputMatch:
    if source.body_sha256 is None or source.body_length is None:
        raise ValueError("source body digest is unavailable")
    return SkillModelInputMatch(
        source.source_call_id, _source_call_kind(source.stage), "correlation_only",
        source.skill_id,
        source.package_content_hash, source.stage,
        source.body_sha256, source.body_length,
    )
