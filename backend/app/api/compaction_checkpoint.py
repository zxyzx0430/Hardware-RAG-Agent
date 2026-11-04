"""Legacy seals and private, fail-closed host publication records.

The host APIs below do not observe a graph or enable request restoration. A
future publisher must supply successful application and exact readback facts;
this module only validates their binding and commits an application-DB receipt.
"""
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import logging
import re
import uuid
from typing import Any, Literal, TYPE_CHECKING

from sqlalchemy import LargeBinary, cast, func, text

from app.api.common import get_db_ctx
from app.api.compaction_archive import context_reference_manifest, get_compaction_scope
from app.api.context_events import (
    CONTEXT_EVENT_PREFIX, ContextEventScope, EventReference,
    _BATCH_BYTES, _MAX_BATCHES, _SESSION_BYTES, _TOTAL_BYTES,
    _encode_events, _read_batch, _session_prefix, _validate_scope,
)
from app.db.models import Session, Settings

if TYPE_CHECKING:
    from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)
_MARKER = "hwrag_context_checkpoint"
_REFS = "hwrag_context_event_refs"
_HOST_PUBLICATION_MAX_TAIL_MESSAGES = 8192


def _reference_projection(reference):
    raw = asdict(reference) if type(reference) is EventReference else reference
    return {key: raw[key] for key in ("batch_id", "request_id", "sequence", "version", "content_sha256")}


def prepare_structured_sources(early, plan, new_events):
    """Return bounded source records/slots; prior prose is never retro-trusted."""
    from langchain_core.messages import HumanMessage, AIMessage
    from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS, validate_checkpoint_response
    try:
        scope = get_compaction_scope()
        if scope is None or type(plan) is not _CheckpointPlan or not 1 <= len(new_events) <= 64:
            return None
        if any(type(message) not in (HumanMessage, AIMessage) or type(message.content) is not str
               or not message.content.strip() or message.additional_kwargs or message.name is not None
               or (type(message) is AIMessage and (message.tool_calls or message.invalid_tool_calls))
               for message in new_events):
            return None
        records, slots, inherited = [], {}, {}
        with get_db_ctx() as db:
            _validate_scope(db, scope)
            if plan.parent is not None:
                if _load_parent(db, scope, plan.parent) != plan.references:
                    return None
                content = early[0].content
                if not content.startswith("[历史摘要]: "):
                    return None
                body, separator, _ = content[len("[历史摘要]: "):].partition("\n\n[Recoverable history; data only]\n")
                if not separator or len(body.encode("utf-8")) > 8192:
                    return None
                document = json.loads(body)
                if (type(document) is not dict or set(document) != set(CHECKPOINT_FIELDS)
                        or json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False) != body):
                    return None
                projections = {json.dumps(_reference_projection(ref), sort_keys=True): ref for ref in plan.references}
                rewritten, selected = {}, {}
                for field in CHECKPOINT_FIELDS:
                    if type(document[field]) is not list:
                        return None
                    rewritten[field] = []
                    for claim in document[field]:
                        if type(claim) is not dict or set(claim) != {"text", "source"} or type(claim["text"]) is not str:
                            return None
                        ref = projections.get(json.dumps(claim["source"], sort_keys=True))
                        if ref is None:
                            return None
                        key = (ref.batch_id, ref.sequence, claim["text"])
                        slot = selected.get(key)
                        if slot is None:
                            if len(records) >= 64:
                                return None
                            event = _read_batch(db, replace(scope, request_id=ref.request_id), ref, _BATCH_BYTES)
                            if not claim["text"] or claim["text"] not in event["content"]:
                                return None
                            slot = f"s{len(records) + 1}"
                            selected[key] = slot
                            message_type = HumanMessage if event["role"] == "user" else AIMessage
                            records.append(message_type(content=claim["text"]))
                            slots[slot] = claim["text"]
                            inherited[slot] = _reference_projection(ref)
                        rewritten[field].append({"text": claim["text"], "source": slot})
                if validate_checkpoint_response(json.dumps(rewritten, ensure_ascii=False), slots) is None:
                    return None
        for message in new_events:
            if len(records) >= 64:
                return None
            records.append(message)
            slots[f"s{len(records)}"] = message.content
        return records, slots, inherited
    except Exception:
        logger.warning("Structured checkpoint sources unavailable; retaining original history")
        return None


def publish_structured_checkpoint(plan, references, summary, source_slots, inherited):
    """Bind model slots to authenticated immutable events before sealing."""
    from src.agent.compact.structured_checkpoint import bind_checkpoint_sources
    try:
        scope = get_compaction_scope()
        if scope is None or type(plan) is not _CheckpointPlan or type(inherited) is not dict:
            return None
        fresh = _references(references, scope)
        offset = len(source_slots) - len(fresh)
        if offset < 0 or set(inherited) != {f"s{i}" for i in range(1, offset + 1)}:
            return None
        previous = [_reference_projection(ref) for ref in plan.references]
        if any(ref not in previous for ref in inherited.values()):
            return None
        mappings = dict(inherited)
        mappings.update({f"s{offset + index}": _reference_projection(ref) for index, ref in enumerate(fresh, 1)})
        bound = bind_checkpoint_sources(summary, source_slots, mappings)
        if bound is None:
            return None
        with get_db_ctx() as db:
            for slot, projection in mappings.items():
                ref = EventReference(session_id=scope.session_id, **projection)
                event = _read_batch(db, replace(scope, request_id=ref.request_id), ref, _BATCH_BYTES)
                if (source_slots[slot] not in event["content"]
                        or (slot not in inherited and source_slots[slot] != event["content"])):
                    return None
        # The existing transaction rechecks current scope/parent/all immutable
        # source hashes, so a later invalidation cannot bypass this grounding.
        sealed = publish_compaction_checkpoint(plan, references, bound)
        if sealed is None:
            return None
        # Only this strict, newly generated path issues structured eligibility.
        # The legacy seal/transport shape stays unchanged. Eligibility is NOT a
        # prepared replacement or evidence that a graph accepted the message.
        try:
            marker, raw, content = _host_message_parts(sealed)
            with get_db_ctx() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                _validate_scope(db, scope)
                refs, events = _host_sources(db, scope, raw)
                _host_grounded_body(bound, refs, events)
                if (_load_parent(db, scope, (marker["id"], marker["version"], marker["sha256"])) != refs
                        or content != "[历史摘要]: " + bound + _manifest(refs)):
                    raise ValueError("Structured origin mismatch")
                origin = {"schema_version": 2, "kind": "structured_origin", "scope": asdict(scope),
                          "seal_id": marker["id"], "seal_version": marker["version"],
                          "content_sha256": marker["sha256"], "body": bound, "references": raw}
                key = _session_prefix(scope.session_id) + "host2:origin:" + marker["id"]
                _host_store(db, scope, {key: origin})
        except Exception:
            logger.warning("Structured host eligibility unavailable; legacy seal only")
        return sealed
    except Exception:
        logger.warning("Structured checkpoint binding refused; retaining original history")
        return None


@dataclass(frozen=True)
class _CheckpointPlan:
    parent: tuple[str, int, str] | None = None
    references: tuple[EventReference, ...] = ()
    generation: int = 0


def _references(raw, scope):
    if type(raw) is not list or not 1 <= len(raw) <= 64:
        raise ValueError("Invalid inherited references")
    refs = tuple(EventReference(**ref) for ref in raw)
    if (any(ref.session_id != scope.session_id for ref in refs)
            or len({(ref.batch_id, ref.sequence) for ref in refs}) != len(refs)):
        raise ValueError("Invalid inherited reference scope")
    return refs


def _validate_references(db, scope, refs):
    for ref in refs:
        _read_batch(db, replace(scope, request_id=ref.request_id), ref, _BATCH_BYTES)


def _load_parent(db, scope, parent):
    checkpoint_id, generation, digest = parent
    if (type(checkpoint_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", checkpoint_id)
            or type(generation) is not int or not 1 <= generation <= 64
            or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("Invalid host checkpoint marker")
    _validate_scope(db, scope)
    row = db.get(Settings, _session_prefix(scope.session_id) + "checkpoint:" + checkpoint_id)
    if row is None or type(row.value) is not str or len(row.value.encode("utf-8")) > _BATCH_BYTES:
        raise ValueError("Host checkpoint unavailable")
    value = json.loads(row.value)
    if (type(value) is not dict or set(value) != {
            "schema_version", "scope", "checkpoint_version", "content_sha256", "references"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["checkpoint_version"]) is not int or value["checkpoint_version"] != generation
            or value["content_sha256"] != digest):
        raise ValueError("Invalid host checkpoint record")
    stored = ContextEventScope(**value["scope"])
    _validate_scope(db, stored)
    if (stored.session_id, stored.principal_hash, stored.session_epoch) != (
            scope.session_id, scope.principal_hash, scope.session_epoch):
        raise ValueError("Host checkpoint identity mismatch")
    refs = _references(value["references"], scope)
    _validate_references(db, scope, refs)
    return refs


def prepare_compaction_checkpoint(early: list):
    """Authenticate a prior host summary; never archive arbitrary system text."""
    from langchain_core.messages import SystemMessage
    if not any(isinstance(message, SystemMessage) for message in early):
        return _CheckpointPlan(), early
    try:
        scope = get_compaction_scope()
        if scope is None or len(early) < 2:
            return None
        previous = early[0]
        if (type(previous) is not SystemMessage or type(previous.content) is not str
                or previous.name is not None or set(previous.additional_kwargs) != {_MARKER, _REFS}
                or any(isinstance(message, SystemMessage) for message in early[1:])):
            return None
        marker = previous.additional_kwargs[_MARKER]
        if type(marker) is not dict or set(marker) != {"id", "version", "sha256"}:
            return None
        parent = (marker["id"], marker["version"], marker["sha256"])
        if hashlib.sha256(previous.content.encode("utf-8")).hexdigest() != marker["sha256"]:
            return None
        with get_db_ctx() as db:
            refs = _load_parent(db, scope, parent)
        if refs != _references(previous.additional_kwargs[_REFS], scope):
            return None
        return _CheckpointPlan(parent, refs, marker["version"]), early[1:]
    except Exception:
        logger.warning("Inherited host checkpoint refused")
        return None


def _manifest(refs):
    groups = {}
    for ref in refs:
        groups.setdefault((ref.batch_id, ref.request_id), []).append(asdict(ref))
    parts = [context_reference_manifest(group) for group in groups.values()]
    if len(parts) == 1:
        return parts[0]
    header = parts[0].rsplit("\n", 1)[0]
    result = header + "\n" + json.dumps([json.loads(part.rsplit("\n", 1)[1]) for part in parts], separators=(",", ":"))
    if len(result.encode("utf-8")) > 8192:
        raise ValueError("Inherited locator budget exceeded")
    return result


def checkpoint_metadata_for_transport(metadata):
    """Retain only bounded primitive locator fields, without granting authority."""
    try:
        if type(metadata) is not dict:
            return {}
        raw = metadata.get(_REFS)
        if type(raw) is not list or not raw or type(raw[0]) is not dict:
            return {}
        session_id = raw[0].get("session_id")
        if (type(session_id) is not str or not 1 <= len(session_id) <= 128
                or any(ord(char) < 32 or ord(char) == 127 for char in session_id)):
            return {}
        refs = _references(raw, ContextEventScope(session_id, "", "", ""))
        _manifest(refs)  # Shape/type/sequence/hash validation for every group.
        retained = {_REFS: [asdict(ref) for ref in refs]}
        if _MARKER in metadata:
            marker = metadata[_MARKER]
            if (type(marker) is not dict or set(marker) != {"id", "version", "sha256"}
                    or type(marker["id"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", marker["id"])
                    or type(marker["version"]) is not int or not 1 <= marker["version"] <= 64
                    or type(marker["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", marker["sha256"])):
                return {}
            retained[_MARKER] = dict(marker)
        if len(json.dumps(retained, separators=(",", ":")).encode("utf-8")) > _BATCH_BYTES:
            return {}
        return retained
    except Exception:
        return {}


def publish_compaction_checkpoint(plan, references, summary: str):
    """Commit only a bounded digest/ref sidecar before releasing a replacement."""
    from langchain_core.messages import SystemMessage
    try:
        scope = get_compaction_scope()
        if scope is None or type(plan) is not _CheckpointPlan or type(summary) is not str:
            return None
        if len(summary) > 8192 or len(summary.encode("utf-8")) > 8192:
            return None
        refs = plan.references + _references(references, scope)
        raw = [asdict(ref) for ref in refs]
        refs = _references(raw, scope)
        content = "[历史摘要]: " + summary + _manifest(refs)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        generation = plan.generation + 1
        if generation > 64:
            return None
        value = json.dumps({"schema_version": 1, "scope": asdict(scope), "checkpoint_version": generation,
                            "content_sha256": digest, "references": raw}, separators=(",", ":"), ensure_ascii=False)
        size = len(value.encode("utf-8"))
        if size > _BATCH_BYTES:
            return None
        checkpoint_id = uuid.uuid4().hex
        with get_db_ctx() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            _validate_scope(db, scope)
            if plan.parent is not None and _load_parent(db, scope, plan.parent) != plan.references:
                return None
            _validate_references(db, scope, refs)
            rows = db.query(Settings.key, func.length(cast(Settings.value, LargeBinary))).filter(
                Settings.key.startswith(CONTEXT_EVENT_PREFIX, autoescape=True),
            ).limit(_MAX_BATCHES + 1).all()
            prefix = _session_prefix(scope.session_id)
            if (len(rows) >= _MAX_BATCHES or sum(length or 0 for _, length in rows) + size > _TOTAL_BYTES
                    or sum(length or 0 for key, length in rows if key.startswith(prefix)) + size > _SESSION_BYTES):
                return None
            db.add(Settings(key=prefix + "checkpoint:" + checkpoint_id, value=value))
        return SystemMessage(content=content, additional_kwargs={
            _REFS: raw, _MARKER: {"id": checkpoint_id, "version": generation, "sha256": digest},
        })
    except Exception:
        logger.warning("Host checkpoint publication refused; retaining original history")
        return None


@dataclass(frozen=True)
class HostPreparedContext:
    """Private handle to a committed preparation, never a graph assertion."""

    prepared_id: str
    prepared_sha256: str
    publication_binding_id: str | None = None
    publication_binding_sha256: str | None = None


@dataclass(frozen=True)
class HostPublishedHead:
    """A selected, application-DB publication receipt, not task completion."""

    receipt_id: str
    receipt_sha256: str
    revision: int


@dataclass(frozen=True)
class HostPublicationBindingReceipt:
    """Readback of a selected host receipt stamped with one V2 binding."""

    head: HostPublishedHead
    binding_id: str
    binding_sha256: str
    coverage_normalizer: str
    prefix_count: int
    prefix_sha256: str
    tail_count: int
    tail_sha256: str
    candidate_sha256: str
    graph_thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    source_crosswalk_sha256: str | None = None
    source_crosswalk_complete: bool = False


@dataclass(frozen=True)
class HostPreparedCandidate:
    """Verified host preparation needed to recover a graph-applied candidate."""

    prepared: HostPreparedContext
    checkpoint: "SystemMessage"
    expected_head: HostPublishedHead | None
    graph_thread_id: str
    checkpoint_ns: str
    tail_count: int
    tail_sha256: str
    candidate_sha256: str
    publication_binding_id: str
    publication_binding_sha256: str
    source_crosswalk_sha256: str | None = None
    source_crosswalk_complete: bool = False


@dataclass(frozen=True)
class HostPublicationBindingReceiptLookup:
    """Bounded receipt scan result; only CONFIRMED carries a current receipt."""

    state: Literal["CONFIRMED", "ABSENT", "CONFLICT", "UNKNOWN"]
    receipt: HostPublicationBindingReceipt | None
    prepared_candidate: HostPreparedCandidate | None
    scan_sha256: str | None
    receipt_count: int | None
    prepared_count: int | None


@dataclass(frozen=True)
class HostContextReplacement:
    """A proposed exact prefix replacement; the caller still owns admission."""

    prefix_count: int
    checkpoint: Any
    tail: tuple
    head: HostPublishedHead


def _host_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _host_digest(value: str):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _host_scope(scope, skills_mode):
    if (type(scope) is not ContextEventScope or skills_mode != "off"
            or get_compaction_scope() != scope):
        raise ValueError("Active host archive scope required")


def _host_read(db, scope, key, kind):
    if not key.startswith(_session_prefix(scope.session_id) + "host2:"):
        raise ValueError("Invalid host record key")
    row = db.get(Settings, key)
    if row is None or type(row.value) is not str or len(row.value.encode("utf-8")) > _BATCH_BYTES:
        raise ValueError("Host record unavailable")
    value = json.loads(row.value)
    if (type(value) is not dict or _host_json(value) != row.value
            or type(value.get("schema_version")) is not int
            or value["schema_version"] not in (2, 3, 4)
            or (value["schema_version"] in (3, 4) and kind not in ("prepared", "graph_applied"))
            or value.get("kind") != kind or type(value.get("scope")) is not dict):
        raise ValueError("Invalid host record")
    stored = ContextEventScope(**value["scope"])
    _validate_scope(db, scope)
    _validate_scope(db, stored)
    if (stored.session_id, stored.principal_hash, stored.session_epoch) != (
            scope.session_id, scope.principal_hash, scope.session_epoch):
        raise ValueError("Host record identity mismatch")
    return value, row.value


def _host_store(db, scope, values, *, replace_key=None):
    """Write only inside the caller's BEGIN IMMEDIATE; never evict on quota."""
    _validate_scope(db, scope)
    prefix = _session_prefix(scope.session_id)
    encoded = {key: _host_json(value) for key, value in values.items()}
    if not encoded or any(not key.startswith(prefix + "host2:") for key in encoded):
        raise ValueError("Invalid host write set")
    if replace_key is not None and replace_key != _host_head_key(scope):
        raise ValueError("Only the selected publication head is mutable")
    rows = db.query(Settings.key, func.length(cast(Settings.value, LargeBinary))).filter(
        Settings.key.startswith(CONTEXT_EVENT_PREFIX, autoescape=True),
    ).limit(_MAX_BATCHES + 1).all()
    sizes = {key: length or 0 for key, length in rows}
    if len(rows) > _MAX_BATCHES:
        raise ValueError("Host record capacity exhausted")
    for key, value in encoded.items():
        if key in sizes and key != replace_key:
            raise ValueError("Immutable host record already exists")
        size = len(value.encode("utf-8"))
        if size > _BATCH_BYTES:
            raise ValueError("Host record row budget exceeded")
        sizes[key] = size
    if (len(sizes) > _MAX_BATCHES or sum(sizes.values()) > _TOTAL_BYTES
            or sum(size for key, size in sizes.items() if key.startswith(prefix)) > _SESSION_BYTES):
        raise ValueError("Host record capacity exhausted")
    for key, value in encoded.items():
        row = db.get(Settings, key)
        if row is None:
            db.add(Settings(key=key, value=value))
        else:
            row.value = value


def _host_head_key(scope):
    identity = _host_digest(_host_json([scope.principal_hash, scope.session_epoch]))
    return _session_prefix(scope.session_id) + "host2:head:" + identity


def _host_head_token(db, scope):
    key = _host_head_key(scope)
    if db.get(Settings, key) is None:
        return None, None
    value, _ = _host_read(db, scope, key, "published_head")
    if (set(value) != {"schema_version", "kind", "scope", "receipt_id", "receipt_sha256", "revision"}
            or type(value["receipt_id"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", value["receipt_id"])
            or type(value["receipt_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", value["receipt_sha256"])
            or type(value["revision"]) is not int or not 1 <= value["revision"] <= 64):
        raise ValueError("Invalid published head")
    token = HostPublishedHead(value["receipt_id"], value["receipt_sha256"], value["revision"])
    publication = _host_publication(db, scope, token)
    if publication[0]["scope"] != value["scope"]:
        raise ValueError("Head producer mismatch")
    return token, publication


def _host_sources(db, scope, raw):
    """Authenticate all refs, including those not selected by summary claims."""
    refs = _references(raw, scope)
    _manifest(refs)
    events, remaining = [], _SESSION_BYTES
    for ref in refs:
        event = _read_batch(db, replace(scope, request_id=ref.request_id), ref, _BATCH_BYTES)
        remaining -= len(_host_json(event).encode("utf-8"))
        if remaining < 0:
            raise ValueError("Host source validation budget exceeded")
        events.append(event)
    return refs, events


def _host_source_crosswalk(
    scope, raw, events, *, aligned_messages=None, aligned_offset=0,
    expected_binding=None,
):
    """Rebuild a complete single-snapshot V3 crosswalk from exact host refs.

    A prior-generation archive digest is never reused as proof for a cumulative
    ref list. This canonical shape is the archive's schema-1 crosswalk; mixed
    request/snapshot/fence/boundary groups fail closed until a cumulative
    multi-generation contract exists.
    """
    from langchain_core.messages import AIMessage, HumanMessage
    from app.api.context_events import (
        ContextEventSourceBinding, ContextEventV3Locator,
        recover_context_event_source_binding,
    )

    refs = _references(raw, scope)
    if (type(events) is not list or len(events) != len(refs)
            or not 1 <= len(refs) <= 64
            or type(aligned_offset) is not int or aligned_offset < 0):
        raise ValueError("Incomplete host V3 source set")
    first_ref = refs[0]
    if (first_ref.request_id != scope.request_id
            or any(ref.batch_id != first_ref.batch_id
                   or ref.request_id != scope.request_id
                   or ref.sequence != index
                   or ref.version != 1
                   or ref.source_binding_sha256 is None
                   for index, ref in enumerate(refs, start=1))):
        raise ValueError("Host refs are not one complete ordered V3 archive batch")
    if aligned_messages is None:
        if aligned_offset != 0:
            raise ValueError("Missing aligned host source messages")
    elif (type(aligned_messages) is not list
            or aligned_offset + len(aligned_messages) != len(refs)):
        raise ValueError("Host source message alignment is incomplete")

    entries = []
    seen_message_ids = set()
    seen_identities = set()
    seen_locators = set()
    common_header = None
    previous_locator_sequence = None
    first_binding = None
    for index, (reference, event) in enumerate(zip(refs, events, strict=True)):
        if (type(event) is not dict or set(event) != {"role", "content"}
                or type(event["role"]) is not str or type(event["content"]) is not str):
            raise ValueError("Invalid host V3 event")
        event_scope = replace(scope, request_id=reference.request_id)
        lookup = recover_context_event_source_binding(
            event_scope, reference, authorized=True,
        )
        binding = lookup.binding
        if (lookup.state != "CONFIRMED"
                or type(binding) is not ContextEventSourceBinding
                or lookup.source_binding_sha256 != reference.source_binding_sha256
                or binding.binding_sha256 != reference.source_binding_sha256):
            raise ValueError("Host ref has no exact authorized V3 sidecar")
        if first_binding is None:
            first_binding = binding

        locator = binding.locator
        if (type(locator) is not ContextEventV3Locator
                or binding.schema_version != 1
                or binding.request_id != scope.request_id
                or binding.delete_epoch != scope.session_epoch
                or binding.scope_sha256 != _host_digest(_host_json(asdict(scope)))
                or locator.version != 3
                or type(locator.sequence) is not int
                or locator.sequence <= 0
                or (previous_locator_sequence is not None
                    and locator.sequence != previous_locator_sequence + 1)
                or type(binding.recent_message_start_boundary) is not int
                or binding.recent_message_start_boundary <= locator.sequence
                or event["role"] != binding.source_role
                or _host_digest(event["content"]) != binding.source_text_sha256):
            raise ValueError("Host V3 source binding does not align with its event")
        previous_locator_sequence = locator.sequence

        metadata_raw = binding.source_metadata_json
        metadata = json.loads(metadata_raw)
        if (type(metadata) is not dict or _host_json(metadata) != metadata_raw
                or type(metadata.get("message_id")) is not str
                or not 1 <= len(metadata["message_id"]) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in metadata["message_id"])
                or metadata.get("source_identity_sha256") != binding.source_identity_sha256
                or _host_digest(metadata_raw) != binding.source_metadata_sha256
                or metadata["message_id"] in seen_message_ids
                or binding.source_identity_sha256 in seen_identities):
            raise ValueError("Host V3 message identity is incomplete or repeated")
        seen_message_ids.add(metadata["message_id"])
        seen_identities.add(binding.source_identity_sha256)

        header = (
            binding.canonical_namespace_digest, binding.scope_sha256,
            binding.delete_epoch, binding.request_id, binding.snapshot_sha256,
            binding.head_sha256, binding.source_fence_sha256,
            binding.recent_message_start_boundary,
        )
        if common_header is None:
            common_header = header
        elif common_header != header:
            raise ValueError("Host V3 refs cross owner, snapshot, fence, or boundary")

        locator_key = (
            locator.event_id, locator.request_id, locator.sequence,
            locator.version, locator.content_sha256, locator.chain_sha256,
        )
        if locator_key in seen_locators:
            raise ValueError("Host V3 locator is duplicated")
        seen_locators.add(locator_key)

        if aligned_messages is not None and index >= aligned_offset:
            message = aligned_messages[index - aligned_offset]
            expected_type = HumanMessage if event["role"] == "user" else AIMessage
            if (type(message) is not expected_type
                    or type(message.content) is not str
                    or message.id != metadata["message_id"]
                    or message.content != event["content"]
                    or message.additional_kwargs or message.name is not None
                    or (type(message) is AIMessage
                        and (message.tool_calls or message.invalid_tool_calls))):
                raise ValueError("V3 source message ID, role, or text mismatch")

        entries.append({
            "host_reference": asdict(reference),
            "source_locator": asdict(locator),
            "source_binding_sha256": binding.binding_sha256,
            "source_metadata_sha256": binding.source_metadata_sha256,
            "source_identity_sha256": binding.source_identity_sha256,
        })

    if type(first_binding) is not ContextEventSourceBinding:
        raise ValueError("Host V3 crosswalk header is unavailable")
    if expected_binding is not None:
        expected_fence_sha256 = _host_digest(
            _host_json(asdict(expected_binding.source_fence)),
        )
        if (
            first_binding.canonical_namespace_digest
            != expected_binding.canonical_namespace_digest
            or first_binding.delete_epoch != expected_binding.delete_epoch
            or first_binding.request_id != expected_binding.request_id
            or first_binding.snapshot_sha256 != expected_binding.snapshot_sha256
            or first_binding.source_fence_sha256 != expected_fence_sha256
            or first_binding.recent_message_start_boundary
            != expected_binding.recent_message_start_boundary
        ):
            raise ValueError("Host V3 crosswalk differs from the current V2 owner binding")
    crosswalk = {
        "schema_version": 1,
        "kind": "compaction_prefix_v3_source_crosswalk",
        "scope": asdict(scope),
        "scope_sha256": _host_digest(_host_json(asdict(scope))),
        "canonical_namespace_digest": first_binding.canonical_namespace_digest,
        "delete_epoch": first_binding.delete_epoch,
        "request_id": first_binding.request_id,
        "snapshot_sha256": first_binding.snapshot_sha256,
        "head_sha256": first_binding.head_sha256,
        "source_fence_sha256": first_binding.source_fence_sha256,
        "recent_message_start_boundary": first_binding.recent_message_start_boundary,
        "prefix_complete": True,
        "entries": entries,
    }
    return _host_digest(_host_json(crosswalk))


def _host_grounded_body(body, refs, events):
    """Replay the strict validator and binding against authenticated quotations."""
    from src.agent.compact.structured_checkpoint import (
        CHECKPOINT_FIELDS, bind_checkpoint_sources, validate_checkpoint_response,
    )
    if type(body) is not str or len(body.encode("utf-8")) > 8192:
        raise ValueError("Host structured body budget exceeded")
    document = json.loads(body)
    if type(document) is not dict or set(document) != set(CHECKPOINT_FIELDS):
        raise ValueError("Invalid host structured body")
    sources = {_host_json(_reference_projection(ref)): event for ref, event in zip(refs, events)}
    rewritten, slots, mappings, selected = {}, {}, {}, {}
    for field in CHECKPOINT_FIELDS:
        if type(document[field]) is not list:
            raise ValueError("Invalid host structured claims")
        rewritten[field] = []
        for claim in document[field]:
            if type(claim) is not dict or set(claim) != {"text", "source"} or type(claim["text"]) is not str:
                raise ValueError("Invalid host structured quote")
            projection = _host_json(claim["source"])
            event = sources.get(projection)
            quote = claim["text"]
            if event is None or not quote.strip() or quote not in event["content"]:
                raise ValueError("Ungrounded host structured quote")
            pair = (projection, quote)
            if pair not in selected:
                if len(slots) >= 64:
                    raise ValueError("Host source slot count exceeded")
                slot = f"s{len(slots) + 1}"
                selected[pair], slots[slot], mappings[slot] = slot, quote, claim["source"]
            rewritten[field].append({"text": quote, "source": selected[pair]})
    validated = validate_checkpoint_response(_host_json(rewritten), slots)
    if validated is None or bind_checkpoint_sources(validated, slots, mappings) != body:
        raise ValueError("Host structured body validation failed")


def _host_message_parts(message):
    from langchain_core.messages import SystemMessage
    if (type(message) is not SystemMessage or type(message.content) is not str or message.name is not None
            or type(message.additional_kwargs) is not dict or set(message.additional_kwargs) != {_MARKER, _REFS}):
        raise ValueError("Exact sealed host message required")
    metadata = checkpoint_metadata_for_transport(message.additional_kwargs)
    if set(metadata) != {_MARKER, _REFS} or metadata != message.additional_kwargs:
        raise ValueError("Invalid sealed host message")
    if _host_digest(message.content) != metadata[_MARKER]["sha256"]:
        raise ValueError("Host message digest mismatch")
    return metadata[_MARKER], metadata[_REFS], message.content


def _host_message_digest(messages):
    """Hash, but never archive, the unchanged tail; ignore graph-assigned IDs."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    if type(messages) is not list or not messages:
        raise ValueError("Concrete host messages required")
    digest, remaining = hashlib.sha256(), _SESSION_BYTES
    for message in messages:
        if type(message) not in (HumanMessage, AIMessage, SystemMessage, ToolMessage):
            raise ValueError("Unsupported host message type")
        encoded = _host_json(message.model_dump(mode="json", exclude={"id"})).encode("utf-8")
        remaining -= len(encoded)
        if remaining < 0:
            raise ValueError("Host candidate validation budget exceeded")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def digest_published_context_candidate(candidate: list) -> str | None:
    """Return the host's stable digest for the exact candidate message list."""
    try:
        return _host_message_digest(candidate)
    except Exception:
        return None


def _host_revalidate_publication_binding(scope, publication_binding, candidate_sha256):
    if publication_binding is None:
        return None
    from app.api.rolling_revision_store import (
        RollingHistoryV2SummaryPublicationBinding,
        read_rolling_history_v2_publication_binding,
    )
    if type(publication_binding) is not RollingHistoryV2SummaryPublicationBinding:
        raise ValueError("Typed V2 publication binding required")
    stored = read_rolling_history_v2_publication_binding(
        publication_binding.binding_id,
        expected_binding_sha256=publication_binding.binding_sha256,
    )
    if (stored != publication_binding
            or stored.schema_version != 2
            or type(stored.host_source_crosswalk_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", stored.host_source_crosswalk_sha256)
            or stored.request_id != scope.request_id
            or stored.host_candidate_sha256 != candidate_sha256):
        raise ValueError("V2 publication binding does not match the host candidate")
    return stored


def build_published_context_candidate_commitment(
    scope: ContextEventScope,
    original: list,
    candidate: list,
    *,
    skills_mode: str,
    graph_thread_id: str,
    checkpoint_ns: str = "",
) -> "RollingHistoryV2HostCandidateCommitment | None":
    """Build a trusted typed commitment from the exact host prefix, tail, and candidate."""
    try:
        from langchain_core.messages import SystemMessage
        from app.api.rolling_revision_store import RollingHistoryV2HostCandidateCommitment

        _host_scope(scope, skills_mode)
        if (type(original) is not list or type(candidate) is not list or len(candidate) < 2
                or len(candidate) - 1 > _HOST_PUBLICATION_MAX_TAIL_MESSAGES
                or type(scope.request_id) is not str
                or not re.fullmatch(r"[0-9a-f]{32}", scope.request_id)
                or type(graph_thread_id) is not str
                or graph_thread_id != "hwrag-request:" + scope.request_id
                or type(checkpoint_ns) is not str or len(checkpoint_ns) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in checkpoint_ns)):
            return None
        marker, raw, content = _host_message_parts(candidate[0])
        tail_count = len(candidate) - 1
        prefix_count = len(original) - tail_count
        if not 1 <= prefix_count <= 64 or candidate[1:] != original[prefix_count:]:
            return None
        candidate_digest = _host_message_digest(candidate)
        tail_digest = _host_message_digest(candidate[1:])
        graph_prefix = original[:prefix_count]
        prefix = None if type(graph_prefix[0]) is SystemMessage else _host_plain_events(graph_prefix)
        with get_db_ctx() as db:
            db.execute(text("BEGIN"))
            _validate_scope(db, scope)
            origin, _origin_digest, events, restored = _host_origin(db, scope, marker["id"])
            if (origin["scope"] != asdict(scope) or raw != origin["references"]
                    or content != restored or marker["version"] != origin["seal_version"]):
                return None
            previous, publication = _host_head_token(db, scope)
            if previous is not None and previous.revision >= 64:
                return None
            parent_prefix_count = 0
            if prefix is None:
                if previous is None:
                    return None
                prefix, parent_proof = _host_published_prefix(publication, graph_prefix)
                parent_count = parent_proof["parent_prefix_count"]
                parent_prefix_count = parent_count
                if (raw[:parent_count] != publication[0]["references"]
                        or any(ref["request_id"] != scope.request_id
                               for ref in raw[parent_count:])):
                    return None
            if prefix != events:
                return None
            source_messages = graph_prefix if parent_prefix_count == 0 else graph_prefix[1:]
            source_crosswalk_sha256 = _host_source_crosswalk(
                scope, raw, events,
                aligned_messages=source_messages,
                aligned_offset=parent_prefix_count,
            )
            coverage = {
                "normalizer": "plain-text-v1",
                "prefix_count": len(prefix),
                "prefix_sha256": _host_digest(_host_json(prefix)),
            }
        return RollingHistoryV2HostCandidateCommitment(
            schema_version=2,
            coverage_normalizer=coverage["normalizer"],
            prefix_count=coverage["prefix_count"],
            prefix_sha256=coverage["prefix_sha256"],
            tail_count=tail_count,
            tail_sha256=tail_digest,
            candidate_sha256=candidate_digest,
            source_crosswalk_sha256=source_crosswalk_sha256,
        )
    except Exception:
        return None


def _host_plain_events(messages):
    from langchain_core.messages import AIMessage, HumanMessage
    if type(messages) is not list or not 1 <= len(messages) <= 64:
        raise ValueError("Plain prefix count exceeded")
    events = []
    for message in messages:
        if type(message) is dict:
            if set(message) != {"role", "content"}:
                raise ValueError("Plain incoming message required")
            event = dict(message)
        elif (type(message) in (HumanMessage, AIMessage) and not message.additional_kwargs
                and message.name is None
                and not (type(message) is AIMessage and (message.tool_calls or message.invalid_tool_calls))):
            event = {"role": "user" if type(message) is HumanMessage else "assistant", "content": message.content}
        else:
            raise ValueError("Unsupported or inherited prefix")
        events.append(event)
    _encode_events(events)
    return events


def _host_published_prefix(publication, graph_prefix):
    """Expand only the stable-head publication validated in this transaction."""
    from langchain_core.messages import SystemMessage
    if type(graph_prefix) is not list or not 1 <= len(graph_prefix) <= 64:
        raise ValueError("Invalid inherited graph prefix")
    parent, events, content = publication
    marker, raw, observed_content = _host_message_parts(graph_prefix[0])
    expected = SystemMessage(content=content, additional_kwargs={
        _REFS: parent["references"], _MARKER: {
            "id": parent["seal_id"], "version": parent["seal_version"], "sha256": parent["content_sha256"],
        },
    })
    if ((marker, raw, observed_content) != _host_message_parts(expected)
            or _host_message_digest([graph_prefix[0]]) != _host_message_digest([expected])):
        raise ValueError("Inherited message is not the selected published checkpoint")
    fresh = _host_plain_events(graph_prefix[1:]) if len(graph_prefix) > 1 else []
    expanded = events + fresh
    _encode_events(expanded)
    return expanded, {"graph_prefix_count": len(graph_prefix), "parent_prefix_count": len(events)}


def _host_origin(db, scope, seal_id):
    if type(seal_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", seal_id):
        raise ValueError("Invalid host seal ID")
    key = _session_prefix(scope.session_id) + "host2:origin:" + seal_id
    origin, encoded = _host_read(db, scope, key, "structured_origin")
    if (set(origin) != {"schema_version", "kind", "scope", "seal_id", "seal_version", "content_sha256", "body", "references"}
            or origin["seal_id"] != seal_id):
        raise ValueError("Invalid structured origin")
    refs, events = _host_sources(db, scope, origin["references"])
    marker = (seal_id, origin["seal_version"], origin["content_sha256"])
    if _load_parent(db, scope, marker) != refs:
        raise ValueError("Structured origin seal mismatch")
    legacy = db.get(Settings, _session_prefix(scope.session_id) + "checkpoint:" + seal_id)
    if json.loads(legacy.value)["scope"] != origin["scope"]:
        raise ValueError("Structured origin producer mismatch")
    _host_grounded_body(origin["body"], refs, events)
    content = "[历史摘要]: " + origin["body"] + _manifest(refs)
    if _host_digest(content) != origin["content_sha256"]:
        raise ValueError("Structured origin content mismatch")
    return origin, _host_digest(encoded), events, content


def _host_prepared(db, scope, token, *, _depth=0, _revision=None):
    if type(_depth) is not int or not 0 <= _depth < 64:
        raise ValueError("Published parent chain depth exceeded")
    if (type(token) is not HostPreparedContext or type(token.prepared_id) is not str
            or not re.fullmatch(r"[0-9a-f]{32}", token.prepared_id)
            or type(token.prepared_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", token.prepared_sha256)):
        raise ValueError("Invalid host preparation handle")
    key = _session_prefix(scope.session_id) + "host2:prepared:" + token.prepared_id
    value, encoded = _host_read(db, scope, key, "prepared")
    fields = {"schema_version", "kind", "scope", "seal_id", "seal_version", "content_sha256", "body", "references",
              "origin_sha256", "coverage", "tail_count", "tail_sha256", "candidate_sha256",
              "graph_thread_id", "checkpoint_ns", "expected_head"}
    binding_fields = {"publication_binding_id", "publication_binding_sha256"}
    crosswalk_fields = {"source_crosswalk_complete", "source_crosswalk_sha256"}
    permitted_fields = (
        fields,
        fields | {"published_parent"},
        fields | binding_fields,
        fields | binding_fields | {"published_parent"},
        fields | binding_fields | crosswalk_fields,
        fields | binding_fields | crosswalk_fields | {"published_parent"},
    )
    schema_version = value.get("schema_version")
    binding_id = value.get("publication_binding_id")
    binding_sha256 = value.get("publication_binding_sha256")
    if (set(value) not in permitted_fields
            or (schema_version == 2 and (
                set(value) not in (fields, fields | {"published_parent"})
                or token.publication_binding_id is not None
                or token.publication_binding_sha256 is not None
            ))
            or (schema_version == 3 and (
                set(value) not in (fields | binding_fields, fields | binding_fields | {"published_parent"})
                or type(binding_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", binding_id)
                or type(binding_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", binding_sha256)
                or token.publication_binding_id != binding_id
                or token.publication_binding_sha256 != binding_sha256
            ))
            or (schema_version == 4 and (
                set(value) not in (
                    fields | binding_fields | crosswalk_fields,
                    fields | binding_fields | crosswalk_fields | {"published_parent"},
                )
                or type(binding_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", binding_id)
                or type(binding_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", binding_sha256)
                or token.publication_binding_id != binding_id
                or token.publication_binding_sha256 != binding_sha256
                or value.get("source_crosswalk_complete") is not True
                or type(value.get("source_crosswalk_sha256")) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", value["source_crosswalk_sha256"])
            ))
            or schema_version not in (2, 3, 4)
            or _host_digest(encoded) != token.prepared_sha256):
        raise ValueError("Invalid host preparation")
    origin, origin_digest, events, content = _host_origin(db, scope, value["seal_id"])
    if (origin_digest != value["origin_sha256"]
            or any(value[key] != origin[key] for key in (
                "scope", "seal_id", "seal_version", "content_sha256", "body", "references"))):
        raise ValueError("Host preparation origin mismatch")
    if schema_version == 4:
        source_crosswalk_sha256 = _host_source_crosswalk(
            scope, origin["references"], events,
        )
        if (value["source_crosswalk_complete"] is not True
                or value["source_crosswalk_sha256"] != source_crosswalk_sha256):
            raise ValueError("Host V3 source crosswalk readback mismatch")
    coverage = value["coverage"]
    if (type(coverage) is not dict or type(coverage.get("prefix_count")) is not int
            or coverage != {"normalizer": "plain-text-v1", "prefix_count": len(events),
                            "prefix_sha256": _host_digest(_host_json(events))}
            or type(value["tail_count"]) is not int
            or not 1 <= value["tail_count"] <= _HOST_PUBLICATION_MAX_TAIL_MESSAGES
            or any(type(value[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", value[key])
                   for key in ("tail_sha256", "candidate_sha256"))
            or type(value["graph_thread_id"]) is not str
            or value["graph_thread_id"] != "hwrag-request:" + value["scope"]["request_id"]
            or type(value["checkpoint_ns"]) is not str or len(value["checkpoint_ns"]) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in value["checkpoint_ns"])):
        raise ValueError("Invalid host coverage proof")
    previous = value["expected_head"]
    if previous is not None and (
            type(previous) is not dict or set(previous) != {"receipt_id", "receipt_sha256", "revision"}
            or type(previous["receipt_id"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", previous["receipt_id"])
            or type(previous["receipt_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", previous["receipt_sha256"])
            or type(previous["revision"]) is not int or not 1 <= previous["revision"] < 64):
        raise ValueError("Invalid expected publication head")
    if _revision is not None and (
            type(_revision) is not int or not 1 <= _revision <= 64
            or _revision != (1 if previous is None else previous["revision"] + 1)):
        raise ValueError("Published parent revision mismatch")
    if "published_parent" in value:
        proof = value["published_parent"]
        if (previous is None or type(proof) is not dict
                or set(proof) != {"graph_prefix_count", "parent_prefix_count"}
                or any(type(proof[key]) is not int or not 1 <= proof[key] <= 64 for key in proof)):
            raise ValueError("Invalid published parent proof")
        parent, parent_events, _ = _host_publication(
            db, scope, HostPublishedHead(**previous), _depth=_depth + 1,
        )
        parent_count = len(parent_events)
        if (proof["parent_prefix_count"] != parent_count
                or len(events) != parent_count + proof["graph_prefix_count"] - 1
                or value["references"][:parent_count] != parent["references"]
                or events[:parent_count] != parent_events
                or any(ref["request_id"] != value["scope"]["request_id"]
                       for ref in value["references"][parent_count:])):
            raise ValueError("Published parent coverage mismatch")
        _encode_events(events)
    return value, events, content


def _host_publication(db, scope, head, *, _depth=0):
    if type(_depth) is not int or not 0 <= _depth < 64:
        raise ValueError("Published parent chain depth exceeded")
    key = _session_prefix(scope.session_id) + "host2:receipt:" + head.receipt_id
    receipt, encoded = _host_read(db, scope, key, "graph_applied")
    receipt_fields = {"schema_version", "kind", "scope", "prepared_id", "prepared_sha256",
                      "expected_head", "revision", "graph"}
    binding_fields = {"publication_binding_id", "publication_binding_sha256"}
    host_proof_fields = {"coverage", "tail_count", "tail_sha256", "candidate_sha256"}
    crosswalk_fields = {"source_crosswalk_complete", "source_crosswalk_sha256"}
    receipt_schema = receipt.get("schema_version")
    expected_receipt_fields = (
        receipt_fields if receipt_schema == 2
        else receipt_fields | binding_fields | host_proof_fields if receipt_schema == 3
        else receipt_fields | binding_fields | host_proof_fields | crosswalk_fields
        if receipt_schema == 4 else set()
    )
    if (set(receipt) != expected_receipt_fields
            or _host_digest(encoded) != head.receipt_sha256
            or type(receipt["revision"]) is not int or receipt["revision"] != head.revision):
        raise ValueError("Invalid host publication receipt")
    if receipt_schema == 4 and (
            receipt.get("source_crosswalk_complete") is not True
            or type(receipt.get("source_crosswalk_sha256")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["source_crosswalk_sha256"])):
        raise ValueError("Invalid host V3 receipt crosswalk")
    prepared, events, content = _host_prepared(
        db, scope, HostPreparedContext(
            receipt["prepared_id"], receipt["prepared_sha256"],
            receipt.get("publication_binding_id"),
            receipt.get("publication_binding_sha256"),
        ),
        _depth=_depth, _revision=head.revision,
    )
    previous = prepared["expected_head"]
    expected_revision = 1 if previous is None else previous["revision"] + 1
    graph = receipt["graph"]
    if (receipt["scope"] != prepared["scope"] or receipt["expected_head"] != previous
            or head.revision != expected_revision or type(graph) is not dict
            or set(graph) != {"thread_id", "checkpoint_ns", "checkpoint_id", "candidate_sha256"}
            or graph["thread_id"] != prepared["graph_thread_id"]
            or graph["thread_id"] != "hwrag-request:" + prepared["scope"]["request_id"]
            or graph["checkpoint_ns"] != prepared["checkpoint_ns"]
            or graph["candidate_sha256"] != prepared["candidate_sha256"]
            or receipt.get("publication_binding_id")
            != prepared.get("publication_binding_id")
            or receipt.get("publication_binding_sha256")
            != prepared.get("publication_binding_sha256")
            or receipt_schema != prepared["schema_version"]
            or (receipt["schema_version"] == 3 and (
                receipt.get("coverage") != prepared.get("coverage")
                or receipt.get("tail_count") != prepared.get("tail_count")
                or receipt.get("tail_sha256") != prepared.get("tail_sha256")
                or receipt.get("candidate_sha256") != prepared.get("candidate_sha256")
            ))
            or (receipt_schema == 4 and (
                receipt.get("coverage") != prepared.get("coverage")
                or receipt.get("tail_count") != prepared.get("tail_count")
                or receipt.get("tail_sha256") != prepared.get("tail_sha256")
                or receipt.get("candidate_sha256") != prepared.get("candidate_sha256")
                or receipt.get("source_crosswalk_complete")
                != prepared.get("source_crosswalk_complete")
                or receipt.get("source_crosswalk_sha256")
                != prepared.get("source_crosswalk_sha256")
            ))
            or type(graph["checkpoint_id"]) is not str or not 1 <= len(graph["checkpoint_id"]) <= 128
            or any(ord(char) < 33 or ord(char) == 127 for char in graph["checkpoint_id"])):
        raise ValueError("Host publication binding mismatch")
    return prepared, events, content


def prepare_published_context(
    scope: ContextEventScope, original: list, candidate: list, *, skills_mode: str,
    graph_thread_id: str, checkpoint_ns: str = "",
    publication_binding: "RollingHistoryV2SummaryPublicationBinding | None" = None,
) -> HostPreparedContext | None:
    """Prepare a strict replacement with plain or selected published-parent sources.

The caller supplies actual pre/post message lists, not claims about coverage.
The required graph ID must derive exactly from this host producer request.
An inherited first SystemMessage must exactly match the stable published head
selected in this preparation transaction. Expand all its immutable events,
not its summary claims, before appending actually replaced plain messages.
No graph is read or written; no publication/head is created by preparation.
"""
    try:
        from langchain_core.messages import SystemMessage
        _host_scope(scope, skills_mode)
        if (type(original) is not list or type(candidate) is not list or len(candidate) < 2
                or (publication_binding is not None
                    and len(candidate) - 1 > _HOST_PUBLICATION_MAX_TAIL_MESSAGES)
                or type(scope.request_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", scope.request_id)
                or type(graph_thread_id) is not str or graph_thread_id != "hwrag-request:" + scope.request_id
                or type(checkpoint_ns) is not str or len(checkpoint_ns) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in checkpoint_ns)):
            return None
        marker, raw, content = _host_message_parts(candidate[0])
        tail_count = len(candidate) - 1
        prefix_count = len(original) - tail_count
        if not 1 <= prefix_count <= 64 or candidate[1:] != original[prefix_count:]:
            return None
        graph_prefix = original[:prefix_count]
        prefix = None if type(graph_prefix[0]) is SystemMessage else _host_plain_events(graph_prefix)
        tail_digest = _host_message_digest(candidate[1:])
        candidate_digest = _host_message_digest(candidate)
        stored_binding = _host_revalidate_publication_binding(
            scope, publication_binding, candidate_digest,
        )
        if stored_binding is not None and stored_binding.host_candidate_sha256 != candidate_digest:
            return None
        prepared_id = (
            _host_digest(_host_json({
                "kind": "host2_prepared_publication_binding",
                "scope": [scope.session_id, scope.principal_hash, scope.session_epoch,
                          scope.request_id],
                "publication_binding_id": stored_binding.binding_id,
                "publication_binding_sha256": stored_binding.binding_sha256,
                "candidate_sha256": candidate_digest,
                "graph_thread_id": graph_thread_id,
                "checkpoint_ns": checkpoint_ns,
            }))[:32]
            if stored_binding is not None else uuid.uuid4().hex
        )
        with get_db_ctx() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            _validate_scope(db, scope)
            origin, origin_digest, events, restored = _host_origin(db, scope, marker["id"])
            if (origin["scope"] != asdict(scope) or raw != origin["references"] or content != restored
                    or marker["version"] != origin["seal_version"]):
                return None
            previous, publication = _host_head_token(db, scope)
            if previous is not None and previous.revision >= 64:
                return None
            parent_proof = None
            parent_prefix_count = 0
            if prefix is None:
                if previous is None:
                    return None
                prefix, parent_proof = _host_published_prefix(publication, graph_prefix)
                parent_count = parent_proof["parent_prefix_count"]
                parent_prefix_count = parent_count
                if (raw[:parent_count] != publication[0]["references"]
                        or any(ref["request_id"] != scope.request_id for ref in raw[parent_count:])):
                    return None
            if prefix != events:
                return None
            source_crosswalk_sha256 = None
            if stored_binding is not None:
                source_messages = graph_prefix if parent_prefix_count == 0 else graph_prefix[1:]
                source_crosswalk_sha256 = _host_source_crosswalk(
                    scope, raw, events,
                    aligned_messages=source_messages,
                    aligned_offset=parent_prefix_count,
                    expected_binding=stored_binding,
                )
                if source_crosswalk_sha256 != stored_binding.host_source_crosswalk_sha256:
                    return None
            coverage = {"normalizer": "plain-text-v1", "prefix_count": len(prefix),
                        "prefix_sha256": _host_digest(_host_json(prefix))}
            if stored_binding is not None and (
                    coverage["normalizer"] != stored_binding.host_coverage_normalizer
                    or coverage["prefix_count"] != stored_binding.host_prefix_count
                    or coverage["prefix_sha256"] != stored_binding.host_prefix_sha256
                    or tail_count != stored_binding.host_tail_count
                    or tail_digest != stored_binding.host_tail_sha256
                    or candidate_digest != stored_binding.host_candidate_sha256):
                return None
            prepared = dict(origin)
            prepared.update({"schema_version": 4 if stored_binding is not None else 2,
                             "kind": "prepared", "origin_sha256": origin_digest,
                             "coverage": coverage,
                             "tail_count": tail_count, "tail_sha256": tail_digest,
                             "candidate_sha256": candidate_digest, "graph_thread_id": graph_thread_id,
                             "checkpoint_ns": checkpoint_ns,
                             "expected_head": None if previous is None else asdict(previous)})
            if stored_binding is not None:
                prepared["publication_binding_id"] = stored_binding.binding_id
                prepared["publication_binding_sha256"] = stored_binding.binding_sha256
                prepared["source_crosswalk_complete"] = True
                prepared["source_crosswalk_sha256"] = source_crosswalk_sha256
            if parent_proof is not None:
                prepared["published_parent"] = parent_proof
            key = _session_prefix(scope.session_id) + "host2:prepared:" + prepared_id
            existing = db.get(Settings, key)
            if existing is not None:
                if type(existing.value) is not str or existing.value != _host_json(prepared):
                    return None
                token = HostPreparedContext(
                    prepared_id,
                    _host_digest(existing.value),
                    None if stored_binding is None else stored_binding.binding_id,
                    None if stored_binding is None else stored_binding.binding_sha256,
                )
                _host_prepared(db, scope, token)
                return token
            _host_store(db, scope, {key: prepared})
        return HostPreparedContext(
            prepared_id,
            _host_digest(_host_json(prepared)),
            None if stored_binding is None else stored_binding.binding_id,
            None if stored_binding is None else stored_binding.binding_sha256,
        )
    except Exception:
        logger.warning("Host publication preparation unavailable")
        return None


def confirm_published_context(
    scope: ContextEventScope, prepared: HostPreparedContext, *, applied_config: dict,
    observed_config: dict, observed_messages: list, skills_mode: str,
    publication_binding: "RollingHistoryV2SummaryPublicationBinding | None" = None,
) -> HostPublishedHead | None:
    """Private host-only registration of externally observed graph application.

This function cannot prove that supplied Python values came from a graph. The
opt-in publisher MUST first observe a successful save, then read back that
exact checkpoint/marker/body/tail; IDs, hashes or a trusted flag alone are not
accepted. This receipt means graph-applied was observed, never model ingestion,
completion or approval. Existing callers do not enable registration by default.

This is synchronous application-DB work, not a cross-DB transaction or an
all-writer CAS. A future async caller MUST shield and join any started work
through commit/rollback before propagating cancellation, with no detached retry.
"""
    try:
        _host_scope(scope, skills_mode)
        if type(applied_config) is not dict or type(observed_config) is not dict:
            return None
        applied, observed = applied_config.get("configurable"), observed_config.get("configurable")
        if type(applied) is not dict or type(observed) is not dict:
            return None
        fields = ("thread_id", "checkpoint_ns", "checkpoint_id")
        if (any(key not in applied or key not in observed or applied[key] != observed[key] for key in fields)
                or applied["thread_id"] != "hwrag-request:" + scope.request_id
                or type(applied["checkpoint_id"]) is not str or not 1 <= len(applied["checkpoint_id"]) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in applied["checkpoint_id"])):
            return None
        if (publication_binding is not None
                and (type(observed_messages) is not list
                     or len(observed_messages) > _HOST_PUBLICATION_MAX_TAIL_MESSAGES + 1)):
            return None
        observed_digest = _host_message_digest(observed_messages)
        stored_binding = _host_revalidate_publication_binding(
            scope, publication_binding, observed_digest,
        )
        if (stored_binding is not None
                and (prepared.publication_binding_id != stored_binding.binding_id
                     or prepared.publication_binding_sha256 != stored_binding.binding_sha256)):
            return None
        if stored_binding is None and (
                prepared.publication_binding_id is not None
                or prepared.publication_binding_sha256 is not None):
            return None
        marker, raw, content = _host_message_parts(observed_messages[0])
        receipt_id = uuid.uuid4().hex
        with get_db_ctx() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            _validate_scope(db, scope)
            value, _, restored = _host_prepared(db, scope, prepared)
            if (value["scope"] != asdict(scope) or applied["thread_id"] != value["graph_thread_id"]
                    or applied["checkpoint_ns"] != value["checkpoint_ns"]
                    or observed_digest != value["candidate_sha256"] or len(observed_messages) != value["tail_count"] + 1
                    or _host_message_digest(observed_messages[1:]) != value["tail_sha256"]
                    or raw != value["references"] or content != restored
                    or marker != {"id": value["seal_id"], "version": value["seal_version"], "sha256": value["content_sha256"]}):
                return None
            if (value["schema_version"] == 4
                    and (stored_binding is None
                         or stored_binding.schema_version != 2
                         or value.get("publication_binding_id") != stored_binding.binding_id
                         or value.get("publication_binding_sha256") != stored_binding.binding_sha256
                         or value.get("source_crosswalk_complete") is not True
                         or value.get("source_crosswalk_sha256")
                         != stored_binding.host_source_crosswalk_sha256
                         or value.get("coverage", {}).get("normalizer")
                         != stored_binding.host_coverage_normalizer
                         or value.get("coverage", {}).get("prefix_count")
                         != stored_binding.host_prefix_count
                         or value.get("coverage", {}).get("prefix_sha256")
                         != stored_binding.host_prefix_sha256
                         or value.get("tail_count") != stored_binding.host_tail_count
                         or value.get("tail_sha256") != stored_binding.host_tail_sha256
                         or value.get("candidate_sha256") != stored_binding.host_candidate_sha256)):
                return None
            if value["schema_version"] in (2, 3) and stored_binding is not None:
                # A legacy V1 binding has no verified source crosswalk.
                return None
            previous, current_publication = _host_head_token(db, scope)
            if previous is not None and current_publication is not None:
                current_prepared = current_publication[0]
                if current_prepared.get("prepared_id") == prepared.prepared_id:
                    current_receipt_key = (
                        _session_prefix(scope.session_id)
                        + "host2:receipt:" + previous.receipt_id
                    )
                    current_receipt, _ = _host_read(
                        db, scope, current_receipt_key, "graph_applied",
                    )
                    if (current_receipt.get("prepared_sha256") == prepared.prepared_sha256
                            and current_receipt.get("publication_binding_id")
                            == (None if stored_binding is None else stored_binding.binding_id)
                            and current_receipt.get("publication_binding_sha256")
                            == (None if stored_binding is None else stored_binding.binding_sha256)
                            and current_receipt.get("source_crosswalk_complete")
                            == (None if stored_binding is None else True)
                            and current_receipt.get("source_crosswalk_sha256")
                            == (None if stored_binding is None else value["source_crosswalk_sha256"])
                            and current_receipt["graph"] == {
                                "thread_id": applied["thread_id"],
                                "checkpoint_ns": applied["checkpoint_ns"],
                                "checkpoint_id": applied["checkpoint_id"],
                                "candidate_sha256": observed_digest,
                            }):
                        return previous
                    return None
            if (None if previous is None else asdict(previous)) != value["expected_head"]:
                return None
            revision = 1 if previous is None else previous.revision + 1
            if revision > 64:
                return None
            receipt = {"schema_version": 4 if stored_binding is not None else 2,
                       "kind": "graph_applied", "scope": asdict(scope),
                       "prepared_id": prepared.prepared_id, "prepared_sha256": prepared.prepared_sha256,
                       "expected_head": value["expected_head"], "revision": revision,
                       "graph": {"thread_id": applied["thread_id"], "checkpoint_ns": applied["checkpoint_ns"],
                                 "checkpoint_id": applied["checkpoint_id"], "candidate_sha256": observed_digest}}
            if stored_binding is not None:
                receipt["publication_binding_id"] = stored_binding.binding_id
                receipt["publication_binding_sha256"] = stored_binding.binding_sha256
                receipt["coverage"] = value["coverage"]
                receipt["tail_count"] = value["tail_count"]
                receipt["tail_sha256"] = value["tail_sha256"]
                receipt["candidate_sha256"] = value["candidate_sha256"]
                receipt["source_crosswalk_complete"] = value["source_crosswalk_complete"]
                receipt["source_crosswalk_sha256"] = value["source_crosswalk_sha256"]
            head = HostPublishedHead(receipt_id, _host_digest(_host_json(receipt)), revision)
            head_key = _host_head_key(scope)
            _host_store(db, scope, {
                _session_prefix(scope.session_id) + "host2:receipt:" + receipt_id: receipt,
                head_key: {"schema_version": 2, "kind": "published_head", "scope": asdict(scope), **asdict(head)},
            }, replace_key=head_key)
        return head
    except Exception:
        logger.warning("Host publication confirmation unavailable")
        return None


def read_published_context_binding_receipt(
    scope: ContextEventScope,
    head: HostPublishedHead,
    *,
    expected_binding_id: str,
    expected_binding_sha256: str,
    skills_mode: str,
) -> HostPublicationBindingReceipt | None:
    """Read back the selected schema-4 receipt and its complete V3 crosswalk."""
    try:
        _host_scope(scope, skills_mode)
        if (type(head) is not HostPublishedHead
                or type(head.receipt_id) is not str
                or not re.fullmatch(r"[0-9a-f]{32}", head.receipt_id)
                or type(head.receipt_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", head.receipt_sha256)
                or type(head.revision) is not int or not 1 <= head.revision <= 64
                or type(expected_binding_id) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected_binding_id)
                or type(expected_binding_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected_binding_sha256)):
            return None
        from app.api.rolling_revision_store import read_rolling_history_v2_publication_binding
        stored_binding = read_rolling_history_v2_publication_binding(
            expected_binding_id,
            expected_binding_sha256=expected_binding_sha256,
        )
        if (stored_binding.schema_version != 2
                or stored_binding.binding_id != expected_binding_id
                or stored_binding.binding_sha256 != expected_binding_sha256
                or stored_binding.request_id != scope.request_id
                or type(stored_binding.host_source_crosswalk_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", stored_binding.host_source_crosswalk_sha256)):
            return None
        with get_db_ctx() as db:
            db.execute(text("BEGIN"))
            _validate_scope(db, scope)
            selected_head, publication = _host_head_token(db, scope)
            if selected_head != head or publication is None:
                return None
            prepared = publication[0]
            if (prepared.get("schema_version") != 4
                    or prepared.get("publication_binding_id") != expected_binding_id
                    or prepared.get("publication_binding_sha256") != expected_binding_sha256
                    or type(prepared.get("candidate_sha256")) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", prepared["candidate_sha256"])):
                return None
            if (prepared.get("source_crosswalk_complete") is not True
                    or prepared.get("source_crosswalk_sha256")
                    != stored_binding.host_source_crosswalk_sha256):
                return None
            if stored_binding.host_candidate_sha256 != prepared["candidate_sha256"]:
                return None
            receipt_key = (
                _session_prefix(scope.session_id) + "host2:receipt:" + head.receipt_id
            )
            receipt, encoded = _host_read(db, scope, receipt_key, "graph_applied")
            if (receipt.get("schema_version") != 4
                    or receipt.get("publication_binding_id") != expected_binding_id
                    or receipt.get("publication_binding_sha256") != expected_binding_sha256
                    or receipt.get("source_crosswalk_complete") is not True
                    or receipt.get("source_crosswalk_sha256")
                    != prepared.get("source_crosswalk_sha256")
                    or receipt.get("source_crosswalk_sha256")
                    != stored_binding.host_source_crosswalk_sha256
                    or receipt.get("prepared_sha256") != _host_digest(_host_json(prepared))
                    or receipt.get("coverage") != prepared.get("coverage")
                    or receipt.get("tail_count") != prepared.get("tail_count")
                    or receipt.get("tail_sha256") != prepared.get("tail_sha256")
                    or receipt.get("candidate_sha256") != prepared.get("candidate_sha256")
                    or _host_digest(encoded) != head.receipt_sha256
                    or receipt.get("revision") != head.revision):
                return None
            graph = receipt.get("graph")
            if (type(graph) is not dict
                    or graph.get("candidate_sha256") != prepared["candidate_sha256"]):
                return None
            return HostPublicationBindingReceipt(
                head=head,
                binding_id=expected_binding_id,
                binding_sha256=expected_binding_sha256,
                coverage_normalizer=prepared["coverage"]["normalizer"],
                prefix_count=prepared["coverage"]["prefix_count"],
                prefix_sha256=prepared["coverage"]["prefix_sha256"],
                tail_count=prepared["tail_count"],
                tail_sha256=prepared["tail_sha256"],
                candidate_sha256=prepared["candidate_sha256"],
                graph_thread_id=graph["thread_id"],
                checkpoint_ns=graph["checkpoint_ns"],
                checkpoint_id=graph["checkpoint_id"],
                source_crosswalk_sha256=prepared["source_crosswalk_sha256"],
                source_crosswalk_complete=True,
            )
    except Exception:
        return None


class _HostCallbackReader:
    """Minimal read-only adapter over one caller-owned application transaction."""

    __slots__ = ("_read_setting", "_session_exists")

    def __init__(self, read_setting, session_exists):
        self._read_setting = read_setting
        self._session_exists = session_exists

    def get(self, model, key):
        if model is Session:
            return SimpleNamespace(id=key) if self._session_exists(key) else None
        if model is Settings:
            value = self._read_setting(key)
            return None if value is None else SimpleNamespace(value=value)
        raise ValueError("Unsupported host row type")


def _host_scan_rows(scan_settings, prefix, limit):
    rows = scan_settings(prefix, limit)
    if type(rows) not in (list, tuple) or len(rows) > limit:
        raise ValueError("Incomplete host publication scan")
    normalized = []
    previous = None
    for row in rows:
        if (type(row) not in (list, tuple) or len(row) != 2
                or type(row[0]) is not str or not row[0].startswith(prefix)
                or type(row[1]) is not str
                or len(row[1].encode("utf-8", errors="strict")) > _BATCH_BYTES
                or (previous is not None and row[0] <= previous)):
            raise ValueError("Invalid host publication scan row")
        previous = row[0]
        normalized.append((row[0], row[1]))
    if len(normalized) >= limit:
        raise ValueError("Host publication scan is truncated")
    return tuple(normalized)


def _host_receipt_binding(
    receipt, binding_id, binding_sha256, candidate_sha256,
    source_crosswalk_sha256,
):
    return (
        receipt.get("schema_version") == 4
        and receipt.get("source_crosswalk_complete") is True
        and type(receipt.get("source_crosswalk_sha256")) is str
        and re.fullmatch(r"[0-9a-f]{64}", receipt["source_crosswalk_sha256"]) is not None
        and receipt.get("source_crosswalk_sha256") == source_crosswalk_sha256
        and receipt.get("publication_binding_id") == binding_id
        and receipt.get("publication_binding_sha256") == binding_sha256
        and receipt.get("candidate_sha256") == candidate_sha256
    )


class _HostOwnerSqlLookupCallbacks:
    """Bounded SELECT callbacks tied to the live owner WRITE SQL handle."""

    __slots__ = ("handle",)

    def __init__(self, handle):
        self.handle = handle

    def _assert_active(self):
        from app.api.rolling_history_owner_scope import (
            OperationMode, _require_active_rolling_history_sql_handle,
        )
        _require_active_rolling_history_sql_handle(self.handle, OperationMode.WRITE)

    def _materialized(self, statement, parameters):
        from app.api.rolling_history_owner_scope import MaterializedRows
        self._assert_active()
        rows = self.handle.execute(statement, parameters)
        self._assert_active()
        if type(rows) is not MaterializedRows:
            raise ValueError("Owner lookup query did not return rows")
        return rows

    def read_setting(self, key):
        rows = self._materialized(
            "SELECT substr(value, 1, ?), length(value) FROM settings WHERE key = ?",
            (_BATCH_BYTES + 1, key),
        ).rows
        if len(rows) > 1:
            raise ValueError("Duplicate owner setting")
        if not rows:
            return None
        value, length = rows[0]
        if (type(value) is not str or type(length) is not int
                or length < 0 or length > _BATCH_BYTES
                or len(value.encode("utf-8", errors="strict")) > _BATCH_BYTES):
            raise ValueError("Owner setting exceeds host row budget")
        return value

    def scan_settings(self, prefix, limit):
        rows = self._materialized(
            "SELECT key, substr(value, 1, ?), length(value) FROM settings "
            "WHERE substr(key, 1, ?) = ? ORDER BY key LIMIT ?",
            (_BATCH_BYTES + 1, len(prefix), prefix, limit),
        ).rows
        result = []
        for key, value, length in rows:
            if (type(key) is not str or type(value) is not str
                    or type(length) is not int or length < 0
                    or length > _BATCH_BYTES
                    or len(value.encode("utf-8", errors="strict")) > _BATCH_BYTES):
                raise ValueError("Owner setting scan row exceeds host row budget")
            result.append((key, value))
        return tuple(result)

    def session_exists(self, session_id):
        rows = self._materialized(
            "SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,),
        ).rows
        return len(rows) == 1


def _lookup_current_published_context_binding_receipt_using_callbacks(
    scope: ContextEventScope,
    *,
    expected_binding_id: str,
    expected_binding_sha256: str,
    expected_candidate_sha256: str,
    expected_source_crosswalk_sha256: str | None = None,
    skills_mode: str,
    read_setting: Callable[[str], str | None],
    scan_settings: Callable[[str, int], list[tuple[str, str]] | tuple[tuple[str, str], ...]],
    session_exists: Callable[[str], bool],
) -> HostPublicationBindingReceiptLookup:
    """Verify the selected receipt and absence through bounded read callbacks.

    The scan is bounded by the application store cap and must be complete. The
    callbacks must remain tied to one read transaction for the whole scan.
    """
    receipt_count = None
    prepared_count = None
    scan_sha256 = None

    def result(state, receipt=None, prepared_candidate=None):
        return HostPublicationBindingReceiptLookup(
            state, receipt, prepared_candidate, scan_sha256,
            receipt_count, prepared_count,
        )

    try:
        _host_scope(scope, skills_mode)
        if (type(expected_binding_id) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected_binding_id)
                or type(expected_binding_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected_binding_sha256)
                or type(expected_candidate_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected_candidate_sha256)
                or type(expected_source_crosswalk_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected_source_crosswalk_sha256)
                or not callable(read_setting) or not callable(scan_settings)
                or not callable(session_exists)):
            return result("UNKNOWN")

        reader = _HostCallbackReader(read_setting, session_exists)
        _validate_scope(reader, scope)
        session_prefix = _session_prefix(scope.session_id) + "host2:"
        receipt_prefix = session_prefix + "receipt:"
        prepared_prefix = session_prefix + "prepared:"
        cap = _MAX_BATCHES + 1
        receipt_rows = _host_scan_rows(
            scan_settings, receipt_prefix, cap,
        )
        receipt_count = len(receipt_rows)
        prepared_rows = _host_scan_rows(
            scan_settings, prepared_prefix, cap,
        )
        prepared_count = len(prepared_rows)
        scan_sha256 = _host_digest(_host_json({
            "scope": asdict(scope),
            "scope_sha256": _host_digest(_host_json(asdict(scope))),
            "expected_binding_id": expected_binding_id,
            "expected_binding_sha256": expected_binding_sha256,
            "expected_candidate_sha256": expected_candidate_sha256,
            "expected_source_crosswalk_sha256": expected_source_crosswalk_sha256,
            "skills_mode": skills_mode,
            "receipts": [[key, _host_digest(raw)] for key, raw in receipt_rows],
            "prepared": [[key, _host_digest(raw)] for key, raw in prepared_rows],
        }))

        matching_receipts = []
        for key, raw in receipt_rows:
            receipt_id = key[len(receipt_prefix):]
            if not re.fullmatch(r"[0-9a-f]{32}", receipt_id):
                raise ValueError("Invalid host receipt key")
            row = reader.get(Settings, key)
            if row is None or row.value != raw:
                raise ValueError("Host receipt scan changed during read")
            value, encoded = _host_read(reader, scope, key, "graph_applied")
            if (encoded != raw or type(value.get("revision")) is not int
                    or not 1 <= value["revision"] <= 64):
                raise ValueError("Invalid host receipt row")
            head = HostPublishedHead(
                receipt_id, _host_digest(encoded), value["revision"],
            )
            _host_publication(reader, scope, head)
            if (value.get("publication_binding_id") == expected_binding_id
                    or value.get("publication_binding_sha256") == expected_binding_sha256):
                if not _host_receipt_binding(
                        value, expected_binding_id, expected_binding_sha256,
                        expected_candidate_sha256,
                        expected_source_crosswalk_sha256):
                    return result("CONFLICT")
                matching_receipts.append((head, value))

        matching_prepared = []
        for key, raw in prepared_rows:
            prepared_id = key[len(prepared_prefix):]
            if not re.fullmatch(r"[0-9a-f]{32}", prepared_id):
                raise ValueError("Invalid host preparation key")
            value = json.loads(raw)
            if (type(value) is not dict or _host_json(value) != raw
                    or value.get("kind") != "prepared"):
                raise ValueError("Invalid host preparation row")
            if (value.get("publication_binding_id") == expected_binding_id
                    or value.get("publication_binding_sha256") == expected_binding_sha256):
                if (value.get("schema_version") != 4
                        or value.get("publication_binding_id") != expected_binding_id
                        or value.get("publication_binding_sha256") != expected_binding_sha256
                        or value.get("source_crosswalk_complete") is not True
                        or type(value.get("source_crosswalk_sha256")) is not str
                        or not re.fullmatch(r"[0-9a-f]{64}", value["source_crosswalk_sha256"])
                        or value.get("source_crosswalk_sha256")
                        != expected_source_crosswalk_sha256
                        or value.get("candidate_sha256") != expected_candidate_sha256):
                    return result("CONFLICT")
                token = HostPreparedContext(
                    prepared_id, _host_digest(raw), expected_binding_id,
                    expected_binding_sha256,
                )
                prepared_value, events, content = _host_prepared(
                    reader, scope, token,
                )
                from langchain_core.messages import SystemMessage
                checkpoint = SystemMessage(content=content, additional_kwargs={
                    _REFS: prepared_value["references"],
                    _MARKER: {
                        "id": prepared_value["seal_id"],
                        "version": prepared_value["seal_version"],
                        "sha256": prepared_value["content_sha256"],
                    },
                })
                previous = prepared_value["expected_head"]
                expected_head = None if previous is None else HostPublishedHead(**previous)
                candidate = HostPreparedCandidate(
                    prepared=token,
                    checkpoint=checkpoint,
                    expected_head=expected_head,
                    graph_thread_id=prepared_value["graph_thread_id"],
                    checkpoint_ns=prepared_value["checkpoint_ns"],
                    tail_count=prepared_value["tail_count"],
                    tail_sha256=prepared_value["tail_sha256"],
                    candidate_sha256=prepared_value["candidate_sha256"],
                    publication_binding_id=expected_binding_id,
                    publication_binding_sha256=expected_binding_sha256,
                    source_crosswalk_sha256=prepared_value["source_crosswalk_sha256"],
                    source_crosswalk_complete=prepared_value["source_crosswalk_complete"],
                )
                matching_prepared.append(candidate)

        if len(matching_prepared) != 1:
            return result("UNKNOWN" if not matching_prepared else "CONFLICT")
        prepared_candidate = matching_prepared[0]

        current_head, publication = _host_head_token(reader, scope)
        if len(matching_receipts) > 1:
            return result("CONFLICT", prepared_candidate=prepared_candidate)
        if matching_receipts:
            receipt_head, receipt_value = matching_receipts[0]
            if current_head != receipt_head or publication is None:
                return result("CONFLICT", prepared_candidate=prepared_candidate)
            graph = receipt_value["graph"]
            binding_receipt = HostPublicationBindingReceipt(
                head=receipt_head,
                binding_id=expected_binding_id,
                binding_sha256=expected_binding_sha256,
                coverage_normalizer=receipt_value["coverage"]["normalizer"],
                prefix_count=receipt_value["coverage"]["prefix_count"],
                prefix_sha256=receipt_value["coverage"]["prefix_sha256"],
                tail_count=receipt_value["tail_count"],
                tail_sha256=receipt_value["tail_sha256"],
                candidate_sha256=receipt_value["candidate_sha256"],
                graph_thread_id=graph["thread_id"],
                checkpoint_ns=graph["checkpoint_ns"],
                checkpoint_id=graph["checkpoint_id"],
                source_crosswalk_sha256=receipt_value["source_crosswalk_sha256"],
                source_crosswalk_complete=receipt_value["source_crosswalk_complete"],
            )
            return result("CONFIRMED", binding_receipt, prepared_candidate)

        expected_previous = (
            None if prepared_candidate.expected_head is None
            else asdict(prepared_candidate.expected_head)
        )
        actual_previous = None if current_head is None else asdict(current_head)
        if actual_previous != expected_previous:
            return result("CONFLICT", prepared_candidate=prepared_candidate)
        return result("ABSENT", prepared_candidate=prepared_candidate)
    except Exception:
        return result("UNKNOWN")


def _lookup_current_published_context_binding_receipt_in_transaction(
    scope: ContextEventScope,
    *,
    expected_binding_id: str,
    expected_binding_sha256: str,
    expected_candidate_sha256: str,
    expected_source_crosswalk_sha256: str | None = None,
    skills_mode: str,
    owner_sql_handle,
) -> HostPublicationBindingReceiptLookup:
    """Run the lookup on the exact active owner WRITE handle, without nesting."""
    try:
        from app.api.rolling_history_owner_scope import (
            OperationMode, _require_active_rolling_history_sql_handle,
        )
        _require_active_rolling_history_sql_handle(
            owner_sql_handle, OperationMode.WRITE,
        )
        callbacks = _HostOwnerSqlLookupCallbacks(owner_sql_handle)
        return _lookup_current_published_context_binding_receipt_using_callbacks(
            scope,
            expected_binding_id=expected_binding_id,
            expected_binding_sha256=expected_binding_sha256,
            expected_candidate_sha256=expected_candidate_sha256,
            expected_source_crosswalk_sha256=expected_source_crosswalk_sha256,
            skills_mode=skills_mode,
            read_setting=callbacks.read_setting,
            scan_settings=callbacks.scan_settings,
            session_exists=callbacks.session_exists,
        )
    except Exception:
        return HostPublicationBindingReceiptLookup(
            "UNKNOWN", None, None, None, None, None,
        )


def lookup_current_published_context_binding_receipt(
    scope: ContextEventScope,
    *,
    expected_binding_id: str,
    expected_binding_sha256: str,
    expected_candidate_sha256: str,
    expected_source_crosswalk_sha256: str | None = None,
    skills_mode: str,
) -> HostPublicationBindingReceiptLookup:
    """Standalone bounded host lookup used only for restart recovery inspection."""
    try:
        _host_scope(scope, skills_mode)
        from app.api.rolling_revision_store import (
            read_rolling_history_v2_publication_binding,
        )
        stored_binding = read_rolling_history_v2_publication_binding(
            expected_binding_id,
            expected_binding_sha256=expected_binding_sha256,
        )
        if (stored_binding.schema_version != 2
                or stored_binding.binding_id != expected_binding_id
                or stored_binding.binding_sha256 != expected_binding_sha256
                or stored_binding.request_id != scope.request_id
                or stored_binding.host_candidate_sha256 != expected_candidate_sha256
                or stored_binding.host_source_crosswalk_sha256
                != expected_source_crosswalk_sha256):
            return HostPublicationBindingReceiptLookup(
                "UNKNOWN", None, None, None, None, None,
            )
        with get_db_ctx() as db:
            db.execute(text("BEGIN"))
            _validate_scope(db, scope)
            def scan_settings(prefix, limit):
                return tuple(
                    (key, value) for key, value in db.query(Settings.key, Settings.value)
                    .filter(Settings.key.startswith(prefix, autoescape=True))
                    .order_by(Settings.key).limit(limit).all()
                )

            def read_setting(key):
                row = db.get(Settings, key)
                return None if row is None else row.value

            return _lookup_current_published_context_binding_receipt_using_callbacks(
                scope,
                expected_binding_id=expected_binding_id,
                expected_binding_sha256=expected_binding_sha256,
                expected_candidate_sha256=expected_candidate_sha256,
                expected_source_crosswalk_sha256=expected_source_crosswalk_sha256,
                skills_mode=skills_mode,
                read_setting=read_setting,
                scan_settings=scan_settings,
                session_exists=lambda session_id: db.get(Session, session_id) is not None,
            )
    except Exception:
        return HostPublicationBindingReceiptLookup(
            "UNKNOWN", None, None, None, None, None,
        )


def host_publication_binding_is_current_in_transaction(
    scope: ContextEventScope,
    receipt: HostPublicationBindingReceipt,
    *,
    read_setting: Callable[[str], str | None],
) -> bool:
    """Compare the selected host head and immutable receipt on a caller transaction."""
    try:
        _host_scope(scope, "off")
        if (type(receipt) is not HostPublicationBindingReceipt
                or type(receipt.head) is not HostPublishedHead
                or type(receipt.head.receipt_id) is not str
                or not re.fullmatch(r"[0-9a-f]{32}", receipt.head.receipt_id)
                or type(receipt.head.receipt_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", receipt.head.receipt_sha256)
                or type(receipt.head.revision) is not int
                or not 1 <= receipt.head.revision <= 64
                or type(receipt.binding_id) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", receipt.binding_id)
                or type(receipt.binding_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", receipt.binding_sha256)
                or receipt.coverage_normalizer != "plain-text-v1"
                or type(receipt.prefix_count) is not int
                or not 1 <= receipt.prefix_count <= 64
                or type(receipt.prefix_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", receipt.prefix_sha256)
                or type(receipt.tail_count) is not int
                or not 1 <= receipt.tail_count <= _HOST_PUBLICATION_MAX_TAIL_MESSAGES
                or type(receipt.tail_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", receipt.tail_sha256)
                or type(receipt.candidate_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", receipt.candidate_sha256)
                or receipt.source_crosswalk_complete is not True
                or type(receipt.source_crosswalk_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", receipt.source_crosswalk_sha256)
                or type(receipt.graph_thread_id) is not str
                or receipt.graph_thread_id != "hwrag-request:" + scope.request_id
                or type(receipt.checkpoint_ns) is not str or len(receipt.checkpoint_ns) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in receipt.checkpoint_ns)
                or type(receipt.checkpoint_id) is not str
                or not 1 <= len(receipt.checkpoint_id) <= 128
                or any(ord(char) < 33 or ord(char) == 127 for char in receipt.checkpoint_id)):
            return False
        head_key = _host_head_key(scope)
        head_raw = read_setting(head_key)
        if type(head_raw) is not str or len(head_raw.encode("utf-8")) > _BATCH_BYTES:
            return False
        head_value = json.loads(head_raw)
        expected_head_fields = {
            "schema_version", "kind", "scope", "receipt_id", "receipt_sha256", "revision",
        }
        if (type(head_value) is not dict or set(head_value) != expected_head_fields
                or _host_json(head_value) != head_raw
                or head_value["schema_version"] != 2
                or head_value["kind"] != "published_head"
                or head_value["scope"] != asdict(scope)
                or head_value["receipt_id"] != receipt.head.receipt_id
                or head_value["receipt_sha256"] != receipt.head.receipt_sha256
                or head_value["revision"] != receipt.head.revision):
            return False
        receipt_key = (
            _session_prefix(scope.session_id) + "host2:receipt:" + receipt.head.receipt_id
        )
        receipt_raw = read_setting(receipt_key)
        if type(receipt_raw) is not str or len(receipt_raw.encode("utf-8")) > _BATCH_BYTES:
            return False
        value = json.loads(receipt_raw)
        required_fields = {
            "schema_version", "kind", "scope", "prepared_id", "prepared_sha256",
            "expected_head", "revision", "graph", "publication_binding_id",
            "publication_binding_sha256", "coverage", "tail_count", "tail_sha256",
            "candidate_sha256", "source_crosswalk_complete", "source_crosswalk_sha256",
        }
        graph = value.get("graph") if type(value) is dict else None
        coverage = value.get("coverage") if type(value) is dict else None
        if (type(value) is not dict or set(value) != required_fields
                or _host_json(value) != receipt_raw
                or value["schema_version"] != 4
                or value["kind"] != "graph_applied"
                or value["scope"] != asdict(scope)
                or value["publication_binding_id"] != receipt.binding_id
                or value["publication_binding_sha256"] != receipt.binding_sha256
                or type(coverage) is not dict
                or set(coverage) != {"normalizer", "prefix_count", "prefix_sha256"}
                or coverage["normalizer"] != receipt.coverage_normalizer
                or coverage["prefix_count"] != receipt.prefix_count
                or coverage["prefix_sha256"] != receipt.prefix_sha256
                or value["tail_count"] != receipt.tail_count
                or value["tail_sha256"] != receipt.tail_sha256
                or value["candidate_sha256"] != receipt.candidate_sha256
                or value["source_crosswalk_complete"] is not True
                or value["source_crosswalk_sha256"] != receipt.source_crosswalk_sha256
                or type(value["source_crosswalk_sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", value["source_crosswalk_sha256"])
                or value["revision"] != receipt.head.revision
                or _host_digest(receipt_raw) != receipt.head.receipt_sha256
                or type(value["prepared_id"]) is not str
                or not re.fullmatch(r"[0-9a-f]{32}", value["prepared_id"])
                or type(value["prepared_sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", value["prepared_sha256"])
                or type(graph) is not dict
                or set(graph) != {"thread_id", "checkpoint_ns", "checkpoint_id", "candidate_sha256"}
                or graph["thread_id"] != "hwrag-request:" + scope.request_id
                or graph["thread_id"] != receipt.graph_thread_id
                or graph["checkpoint_ns"] != receipt.checkpoint_ns
                or graph["checkpoint_id"] != receipt.checkpoint_id
                or graph["candidate_sha256"] != receipt.candidate_sha256):
            return False
        prepared_key = (
            _session_prefix(scope.session_id) + "host2:prepared:" + value["prepared_id"]
        )
        prepared_raw = read_setting(prepared_key)
        if (type(prepared_raw) is not str
                or len(prepared_raw.encode("utf-8")) > _BATCH_BYTES
                or _host_digest(prepared_raw) != value["prepared_sha256"]):
            return False
        prepared = json.loads(prepared_raw)
        if (type(prepared) is not dict or _host_json(prepared) != prepared_raw
                or prepared.get("schema_version") != 4
                or prepared.get("kind") != "prepared"
                or prepared.get("publication_binding_id") != receipt.binding_id
                or prepared.get("publication_binding_sha256") != receipt.binding_sha256
                or prepared.get("source_crosswalk_complete") is not True
                or prepared.get("source_crosswalk_sha256")
                != receipt.source_crosswalk_sha256
                or prepared.get("candidate_sha256") != receipt.candidate_sha256):
            return False
        return True
    except Exception:
        return False


def load_published_context(
    scope: ContextEventScope, incoming: list, *, skills_mode: str,
) -> HostContextReplacement | None:
    """Return a verified prefix plan without mutating input or enabling restore.

Only a stable published head and its receipt/preparation/origin/seal/source
chain qualify. Plain incoming role/content must match every covered source in
order. All incoming messages must be plain text; inherited/multimodal/tool
history is unsupported. The tail objects are returned unchanged. A future
route must separately provide admission/fencing and final provider budgeting;
None means preserve complete input or explicitly fail, never trim or guess.
"""
    try:
        from langchain_core.messages import SystemMessage
        _host_scope(scope, skills_mode)
        if type(incoming) is not list:
            return None
        for message in incoming:
            _host_plain_events([message])
        with get_db_ctx() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            _validate_scope(db, scope)
            head, publication = _host_head_token(db, scope)
            if head is None:
                return None
            value, events, content = publication
            prefix_count = value["coverage"]["prefix_count"]
            if len(incoming) <= prefix_count:
                return None
            prefix = _host_plain_events(incoming[:prefix_count])
            if prefix != events or _host_digest(_host_json(prefix)) != value["coverage"]["prefix_sha256"]:
                return None
            checkpoint = SystemMessage(content=content, additional_kwargs={
                _REFS: value["references"], _MARKER: {
                    "id": value["seal_id"], "version": value["seal_version"], "sha256": value["content_sha256"],
                },
            })
            result = HostContextReplacement(prefix_count, checkpoint, tuple(incoming[prefix_count:]), head)
        return result
    except Exception:
        logger.warning("Published host context unavailable")
        return None
