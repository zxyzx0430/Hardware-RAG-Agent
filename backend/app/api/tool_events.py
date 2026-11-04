"""Body-free host tool-return records; never permission or completion grants."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
import hashlib
import json
import logging
import re

from sqlalchemy import LargeBinary, cast, func, text

from app.api.common import get_db_ctx
from app.api.context_events import (
    CONTEXT_EVENT_PREFIX, ContextEventScope, EventReference,
    _BATCH_BYTES, _CREDENTIAL, _MAX_BATCHES, _SESSION_BYTES, _TOTAL_BYTES,
    _session_prefix, _validate_scope,
)
from app.db.models import Settings

logger = logging.getLogger(__name__)
_TOOL_EVENT_SCOPE: ContextVar[ContextEventScope | None] = ContextVar("tool_event_archive", default=None)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", re.ASCII)
_NAME = re.compile(r"[A-Za-z0-9_]{1,128}", re.ASCII)
_SKILL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)


@contextmanager
def bind_tool_event_archive(scope: ContextEventScope | None):
    """Bind verified host scope separately from permission to archive text."""
    token = _TOOL_EVENT_SCOPE.set(scope if type(scope) is ContextEventScope else None)
    try:
        yield
    finally:
        _TOOL_EVENT_SCOPE.reset(token)


def get_tool_event_scope() -> ContextEventScope | None:
    """Return host authority, never a locator- or model-supplied identity."""
    scope = _TOOL_EVENT_SCOPE.get()
    return scope if type(scope) is ContextEventScope else None


def _encode_tool_event(event: dict) -> str:
    """Whitelist metadata, with no arbitrary error/result/argument strings."""
    keys = {"event_type", "call_id", "tool_name", "status", "decision", "risk_level", "skill_version"}
    phases = {"tool_attempt_started": "started", "tool_attempt_timeout": "timeout",
              "tool_attempt_error": "error", "tool_attempt_cancelled": "cancelled"}
    lifecycle = {"tool_dispatch": ("received", "not_found", "spec_mismatch", "invalid_args", "pipeline_error", "cancelled"),
                 "tool_permission": ("pending", "denied", "stopped")}
    if (type(event) is not dict or type(event.get("event_type")) is not str):
        raise ValueError("Invalid safe tool event kind")
    if type(event) is dict and event.get("event_type") in phases:
        keys = keys | {"attempt"}
        if (type(event.get("attempt")) is not int or not 1 <= event["attempt"] <= 16
                or event.get("status") != phases[event["event_type"]]
                or event.get("skill_version") is not None or event.get("decision") != "unknown"):
            raise ValueError("Invalid safe tool attempt event")
    if (type(event) is not dict or set(event) != keys
            or (event["event_type"] != "tool_return" and event["event_type"] not in phases
                and event["event_type"] not in lifecycle)
            or type(event["call_id"]) is not str or not _ID.fullmatch(event["call_id"])
            or type(event["tool_name"]) is not str or not _NAME.fullmatch(event["tool_name"])
            or type(event["status"]) is not str
            or event["decision"] not in ("allow", "deny", "bypass", "unknown")
            or event["risk_level"] not in ("low", "medium", "high", "unknown")):
        raise ValueError("Invalid safe tool event")
    if event["event_type"] == "tool_return" and event["status"] not in ("returned", "failed", "denied"):
        raise ValueError("Invalid safe tool return status")
    if event["event_type"] in lifecycle:
        expected_decision = "deny" if event["event_type"] == "tool_permission" and event["status"] in ("denied", "stopped") else "unknown"
        if (event["status"] not in lifecycle[event["event_type"]]
                or event["decision"] != expected_decision or event["skill_version"] is not None):
            raise ValueError("Invalid safe tool lifecycle status")
    version = event["skill_version"]
    if version is not None and (
        type(version) is not dict or set(version) != {"skill_id", "content_hash"}
        or type(version["skill_id"]) is not str or not _SKILL_ID.fullmatch(version["skill_id"])
        or type(version["content_hash"]) is not str or not _HASH.fullmatch(version["content_hash"])
    ):
        raise ValueError("Invalid safe Skill version")
    encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 4096 or _CREDENTIAL.search(encoded):
        raise ValueError("Safe tool event exceeded encoding policy")
    return encoded


def _tool_event_identity(event: dict) -> str:
    """Keep existing return/attempt locators stable when adding lifecycle types."""
    if event["event_type"] == "tool_return":
        return event["call_id"]
    suffix = str(event["attempt"]) if "attempt" in event else event["status"]
    return event["call_id"] + "\0" + event["event_type"] + "\0" + suffix


def _tool_event_projection(record) -> dict:
    """Observe a host router return, not the truth of its business outcome."""
    envelope = record.envelope
    data = envelope.data if type(envelope.data) is dict else {}
    denied = record.decision == "deny"
    failed = (envelope.success is not True or envelope.error is not None
              or data.get("success") is False or bool(data.get("error"))
              or data.get("timed_out") is True
              or ("exit_code" in data and data["exit_code"] not in (None, 0)))
    version = None
    if record.spec.name in ("load_skill", "read_skill_resource") and not denied and not failed:
        skill_id, content_hash = data.get("skill_id"), data.get("content_hash")
        if (type(skill_id) is str and _SKILL_ID.fullmatch(skill_id)
                and type(content_hash) is str and _HASH.fullmatch(content_hash)):
            version = {"skill_id": skill_id, "content_hash": content_hash}
    decision = record.decision if record.decision in ("allow", "deny", "bypass") else "unknown"
    risk = record.spec.risk_level.value
    return {"event_type": "tool_return", "call_id": record.call_id,
            "tool_name": record.spec.name, "status": "denied" if denied else "failed" if failed else "returned",
            "decision": decision, "risk_level": risk if risk in ("low", "medium", "high") else "unknown",
            "skill_version": version}


def _read_tool_event_chain(db, scope: ContextEventScope) -> list:
    """Validate one bounded request chain and its committed head in a snapshot."""
    _validate_scope(db, scope)
    prefix = _session_prefix(scope.session_id)
    rows = db.query(Settings.key, Settings.value).filter(
        Settings.key.startswith(prefix + "tool:", autoescape=True),
    ).limit(_MAX_BATCHES + 1).all()
    if len(rows) > _MAX_BATCHES:
        raise ValueError("Safe tool event count exceeded")
    chain = []
    scanned_bytes = 0
    for key, raw in rows:
        if type(raw) is not str or len(raw.encode("utf-8")) > _BATCH_BYTES:
            raise ValueError("Safe tool event envelope unavailable")
        scanned_bytes += len(raw.encode("utf-8"))
        if scanned_bytes > _SESSION_BYTES:
            raise ValueError("Safe tool event scan budget exceeded")
        value = json.loads(raw)
        if type(value) is not dict or type(value.get("scope")) is not dict:
            raise ValueError("Invalid safe tool event scope")
        if value["scope"].get("request_id") != scope.request_id:
            continue
        if (set(value) != {"schema_version", "scope", "sequence", "previous_sha256", "event"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 2
                or value["scope"] != asdict(scope)
                or type(value["sequence"]) is not int or not 1 <= value["sequence"] <= 64
                or type(value["event"]) is not str
                or json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) != raw):
            raise ValueError("Unchained or invalid safe tool event")
        event = json.loads(value["event"])
        if _encode_tool_event(event) != value["event"]:
            raise ValueError("Noncanonical safe tool event")
        identity = _tool_event_identity(event)
        batch = hashlib.sha256((scope.request_id + "\0" + identity).encode("utf-8")).hexdigest()[:32]
        if key != prefix + "tool:" + batch:
            raise ValueError("Safe tool event locator mismatch")
        reference = EventReference(batch, scope.session_id, scope.request_id, value["sequence"], 2,
                                   hashlib.sha256(value["event"].encode("utf-8")).hexdigest())
        chain.append((reference, event, hashlib.sha256(raw.encode("utf-8")).hexdigest(), value["previous_sha256"]))
    if len(chain) > 64:
        raise ValueError("Safe tool request chain capacity exhausted")
    chain.sort(key=lambda item: item[0].sequence)
    previous = ""
    for index, (reference, _, digest, parent) in enumerate(chain, start=1):
        if reference.sequence != index or parent != previous:
            raise ValueError("Safe tool event chain is incomplete")
        previous = digest
    head = db.get(Settings, prefix + "tool-head:" + scope.request_id)
    if head is None:
        if chain:
            raise ValueError("Safe tool event chain head is missing")
    else:
        expected = {"schema_version": 2, "scope": asdict(scope), "sequence": len(chain), "last_sha256": previous}
        if (not chain or type(head.value) is not str or len(head.value.encode("utf-8")) > 4096
                or json.loads(head.value) != expected
                or json.dumps(expected, sort_keys=True, ensure_ascii=False, separators=(",", ":")) != head.value):
            raise ValueError("Safe tool event chain head mismatch")
    return chain


def _persist_tool_event(scope: ContextEventScope, event: dict) -> EventReference:
    encoded = _encode_tool_event(event)
    identity = _tool_event_identity(event)
    batch_id = hashlib.sha256((scope.request_id + "\0" + identity).encode("utf-8")).hexdigest()[:32]
    with get_db_ctx() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        chain = _read_tool_event_chain(db, scope)
        prefix = _session_prefix(scope.session_id)
        for reference, previous_event, _, _ in chain:
            if reference.batch_id == batch_id:
                if _encode_tool_event(previous_event) != encoded:
                    raise ValueError("Conflicting safe tool event")
                return reference
        if len(chain) >= 64:
            raise ValueError("Safe tool request chain capacity exhausted")
        sequence = len(chain) + 1
        value = json.dumps({"schema_version": 2, "scope": asdict(scope), "sequence": sequence,
                            "previous_sha256": chain[-1][2] if chain else "", "event": encoded},
                           sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        size = len(value.encode("utf-8"))
        if size > _BATCH_BYTES:
            raise ValueError("Safe tool event envelope exceeded")
        head_key = prefix + "tool-head:" + scope.request_id
        head = db.get(Settings, head_key)
        head_value = json.dumps({"schema_version": 2, "scope": asdict(scope), "sequence": sequence,
                                 "last_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()},
                                sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        delta = size + len(head_value.encode("utf-8")) - (len(head.value.encode("utf-8")) if head else 0)
        rows = db.query(Settings.key, func.length(cast(Settings.value, LargeBinary))).filter(
            Settings.key.startswith(CONTEXT_EVENT_PREFIX, autoescape=True),
        ).limit(_MAX_BATCHES + 1).all()
        if (len(rows) + 1 + int(head is None) > _MAX_BATCHES
                or sum(length or 0 for _, length in rows) + delta > _TOTAL_BYTES
                or sum(length or 0 for key, length in rows if key.startswith(prefix)) + delta > _SESSION_BYTES):
            raise ValueError("Safe tool event archive capacity exhausted")
        db.add(Settings(key=prefix + "tool:" + batch_id, value=value))
        if head is None:
            db.add(Settings(key=head_key, value=head_value))
        else:
            head.value = head_value
    return EventReference(batch_id, scope.session_id, scope.request_id, sequence, 2,
                          hashlib.sha256(encoded.encode("utf-8")).hexdigest())


def record_tool_event(record) -> EventReference | None:
    """Best-effort metadata archive; failure never permits history replacement."""
    scope = _TOOL_EVENT_SCOPE.get()
    if type(scope) is not ContextEventScope:
        return None
    try:
        if record.ctx.session_id != scope.session_id or record.spec.mcp_info is not None:
            return None
        return _persist_tool_event(scope, _tool_event_projection(record))
    except Exception:
        logger.warning("Safe tool event archive unavailable; no recoverability claim")
        return None


def record_tool_phase(state, phase: str, attempt: int) -> EventReference | None:
    """Observe an execution attempt; cancellation never proves side-effect undo."""
    scope = _TOOL_EVENT_SCOPE.get()
    if type(scope) is not ContextEventScope:
        return None
    try:
        if (state.ctx.session_id != scope.session_id or state.spec.mcp_info is not None
                or phase not in ("started", "timeout", "error", "cancelled")):
            return None
        risk = state.spec.risk_level.value
        event = {"event_type": "tool_attempt_" + phase, "attempt": attempt,
                 "call_id": state.call_id, "tool_name": state.spec.name, "status": phase,
                 "decision": "unknown", "risk_level": risk if risk in ("low", "medium", "high") else "unknown",
                 "skill_version": None}
        return _persist_tool_event(scope, event)
    except Exception:
        logger.warning("Safe tool attempt archive unavailable; no lifecycle completeness claim")
        return None


def record_tool_dispatch(call: dict, ctx, phase: str) -> EventReference | None:
    """Record a host dispatch transition without retaining arguments or errors."""
    scope = _TOOL_EVENT_SCOPE.get()
    if type(scope) is not ContextEventScope:
        return None
    try:
        spec = call.get("spec")
        if ctx.session_id != scope.session_id or (spec is not None and spec.mcp_info is not None):
            return None
        risk = spec.risk_level.value if spec is not None and spec.name == call.get("tool_name") else "unknown"
        event = {"event_type": "tool_dispatch", "call_id": call.get("call_id"),
                 "tool_name": call.get("tool_name"), "status": phase, "decision": "unknown",
                 "risk_level": risk if risk in ("low", "medium", "high") else "unknown", "skill_version": None}
        return _persist_tool_event(scope, event)
    except Exception:
        logger.warning("Safe tool dispatch archive unavailable; no completeness claim")
        return None


def record_tool_permission(pending: list[dict], phase: str) -> None:
    """Observe host pending/deny/stop decisions, never create or replay approval."""
    scope = _TOOL_EVENT_SCOPE.get()
    if type(scope) is not ContextEventScope:
        return
    try:
        from src.agent.request_context import active_tool_context
        ctx = active_tool_context()
        if (ctx is None or ctx.session_id != scope.session_id or type(pending) is not list
                or len(pending) > 64 or phase not in ("pending", "denied", "stopped")):
            return
        for call in pending:
            try:
                if type(call) is not dict:
                    continue
                spec = ctx.request_tools.get(call.get("name"))
                if spec is None or spec.mcp_info is not None:
                    continue
                event = {"event_type": "tool_permission", "call_id": call.get("call_id"),
                         "tool_name": call.get("name"), "status": phase,
                         "decision": "deny" if phase in ("denied", "stopped") else "unknown",
                         "risk_level": spec.risk_level.value, "skill_version": None}
                _persist_tool_event(scope, event)
            except Exception:
                logger.warning("Safe tool permission archive unavailable; no completeness claim")
    except Exception:
        logger.warning("Safe tool permission binding unavailable")


def recover_tool_event(scope: ContextEventScope, reference: EventReference, *, byte_budget: int = 4096) -> dict | None:
    """Host-only metadata read; no HTTP/model grant or command replay entry."""
    try:
        if (type(scope) is not ContextEventScope or type(reference) is not EventReference
                or type(byte_budget) is not int or not 1 <= byte_budget <= 4096
                or reference.session_id != scope.session_id
                or type(reference.request_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", reference.request_id)
                or type(reference.batch_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", reference.batch_id)
                or type(reference.version) is not int or reference.version != 2
                or type(reference.sequence) is not int or not 1 <= reference.sequence <= 64
                or type(reference.content_sha256) is not str or not _HASH.fullmatch(reference.content_sha256)):
            return None
        stored_scope = replace(scope, request_id=reference.request_id)
        with get_db_ctx() as db:
            db.execute(text("BEGIN"))
            _validate_scope(db, scope)
            for candidate, event, _, _ in _read_tool_event_chain(db, stored_scope):
                if candidate == reference:
                    result = {"untrusted_history": True, "completion_verified": False,
                              "coverage": "committed_returns_only", "event": event}
                    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > byte_budget:
                        return None
                    return result
            return None
    except Exception:
        logger.warning("Safe tool event recovery refused")
        return None


def recover_tool_event_chain(scope: ContextEventScope, *, byte_budget: int = 32768) -> dict | None:
    """Recover a whole committed metadata chain within a total response budget."""
    if type(scope) is not ContextEventScope or type(byte_budget) is not int or not 1 <= byte_budget <= 32768:
        return None
    try:
        with get_db_ctx() as db:
            db.execute(text("BEGIN"))
            chain = _read_tool_event_chain(db, scope)
            result = {"untrusted_history": True, "completion_verified": False,
                      "coverage": "committed_returns_only",
                      "records": [{"reference": asdict(ref), "event": event} for ref, event, _, _ in chain]}
            if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > byte_budget:
                return None
            return result
    except Exception:
        logger.warning("Safe tool event chain recovery refused")
        return None
