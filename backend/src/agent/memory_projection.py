"""Project a confirmed structured checkpoint into Memory v2 working memory.

The structured checkpoint already serves context replacement. This module
copies its confirmed body into ``memory_snapshots`` and derives a small,
bounded set of ``proposed`` durable candidates. It never publishes, restores,
or authorizes anything: callers must invoke it only after a graph-applied host
publication receipt was confirmed for the same request scope.

Projection is best effort. Any failure returns None and leaves the compaction
result, the graph, and the request untouched.

Mapping:
- snapshot: sanitized nine-field body (``completed`` stays empty by contract);
- ``constraints`` -> project ``constraint`` candidate;
- ``decisions`` -> project ``decision`` candidate;
- ``failed_attempts`` -> project ``constraint`` candidate prefixed ``Avoid:``;
- goal/current_state/open_questions/next_action/artifacts_and_evidence stay in
  working memory and never become durable candidates.

Every candidate is ``status=proposed``; nothing here promotes memory to active,
and hardware-profile entries are never produced.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.db.memory_store import MemoryStore, contains_secret, redact_secrets
from src.agent.compact.structured_checkpoint import CHECKPOINT_FIELDS

logger = logging.getLogger(__name__)

MAX_PROPOSALS = 8
MAX_CLAIM_CHARS = 1024
MAX_BODY_BYTES = 8192
PROPOSAL_CONFIDENCE = 0.6

_REQUIRED_CONTENT = {"goal", "current_state", "artifacts_and_evidence", "next_action"}
_MEMORY_TAG_RE = re.compile(r"</?\s*memory\s*>", re.IGNORECASE)
_UUID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_KEYS = ("batch_id", "request_id", "sequence", "version", "content_sha256")
_BOUND_SOURCE_KEYS = _SOURCE_KEYS + ("source_binding_sha256",)

# field -> (kind, deterministic content prefix)
_DURABLE_FIELDS = {
    "constraints": ("constraint", ""),
    "decisions": ("decision", ""),
    "failed_attempts": ("constraint", "Avoid: "),
}
# Durable kind -> tier. Checkpoint fields currently yield constraints,
# decisions and avoid-constraints only; preferences are user-tier by design.
_KIND_TIERS = {
    "preference": "user",
    "constraint": "project",
    "decision": "project",
    "procedure": "project",
}


def _default_store() -> MemoryStore:
    """Test seam: production uses the process-wide session factory."""
    return MemoryStore()


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate checkpoint field")
        result[key] = value
    return result


def _valid_source(source: Any) -> bool:
    if type(source) is not dict or set(source) not in (
        set(_SOURCE_KEYS), set(_BOUND_SOURCE_KEYS)
    ):
        return False
    if not (
        type(source["batch_id"]) is str and _UUID.fullmatch(source["batch_id"])
        and type(source["request_id"]) is str and _UUID.fullmatch(source["request_id"])
        and type(source["sequence"]) is int and 1 <= source["sequence"] <= 64
        and type(source["version"]) is int and source["version"] == 1
        and type(source["content_sha256"]) is str
        and _HASH.fullmatch(source["content_sha256"])
    ):
        return False
    if "source_binding_sha256" in source:
        return (
            type(source["source_binding_sha256"]) is str
            and _HASH.fullmatch(source["source_binding_sha256"]) is not None
        )
    return True


def parse_checkpoint_body(body: Any) -> dict[str, list[dict[str, Any]]] | None:
    """Validate the bound nine-field body; content shape never confers authority."""
    try:
        if (
            type(body) is not str
            or len(body) > MAX_BODY_BYTES
            or len(body.encode("utf-8")) > MAX_BODY_BYTES
        ):
            return None
        document = json.loads(body, object_pairs_hook=_json_object)
        if type(document) is not dict or set(document) != set(CHECKPOINT_FIELDS):
            return None
        for field in CHECKPOINT_FIELDS:
            claims = document[field]
            if (
                type(claims) is not list
                or len(claims) > 8
                or (field in _REQUIRED_CONTENT and not claims)
                or (field == "completed" and claims)
            ):
                # Self-reported completion is never projected.
                return None
            seen = set()
            for claim in claims:
                if type(claim) is not dict or set(claim) != {"text", "source"}:
                    return None
                text, source = claim["text"], claim["source"]
                if (
                    type(text) is not str
                    or not text.strip()
                    or len(text) > MAX_CLAIM_CHARS
                    or not _valid_source(source)
                ):
                    return None
                key = (text, json.dumps(source, sort_keys=True))
                if key in seen:
                    return None
                seen.add(key)
        return document
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return None


def _sanitize_text(text: str) -> str:
    """Redact credentials and memory wrappers; keep the text as plain data."""
    redacted = redact_secrets(text) or ""
    # The store's detector treats an all-caps placeholder as a value; stored
    # text uses a lowercase marker so redacted assignments stay storable.
    redacted = redacted.replace("[REDACTED]", "[redacted]")
    collapsed = " ".join(redacted.split())
    return _MEMORY_TAG_RE.sub("[memory-tag]", collapsed)


def _sanitize_document(
    document: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]] | None:
    sanitized: dict[str, list[dict[str, Any]]] = {}
    for field in CHECKPOINT_FIELDS:
        claims = []
        for claim in document[field]:
            text = _sanitize_text(claim["text"])
            if not text or contains_secret(text):
                # A claim that still looks like a credential is not projected.
                continue
            claims.append({"text": text, "source": dict(claim["source"])})
        sanitized[field] = claims
    if any(not sanitized[field] for field in _REQUIRED_CONTENT):
        return None
    return sanitized


def _source_ref(source: dict[str, Any]) -> dict[str, Any]:
    return {key: source[key] for key in _SOURCE_KEYS}


def build_proposals(
    document: dict[str, list[dict[str, Any]]],
    *,
    seal_id: str,
    generation: int,
    project_id: str,
    principal_id: str = "local",
) -> list[dict[str, Any]]:
    """Derive at most ``MAX_PROPOSALS`` proposed candidates from one checkpoint."""
    proposals: list[dict[str, Any]] = []
    seen = set()
    for field in ("constraints", "decisions", "failed_attempts"):
        kind, prefix = _DURABLE_FIELDS[field]
        tier = _KIND_TIERS[kind]
        for claim in document[field]:
            text = _sanitize_text(claim["text"])
            if not text or contains_secret(text):
                continue
            content = prefix + text
            key = (tier, kind, content.casefold())
            if key in seen:
                continue
            seen.add(key)
            proposals.append({
                "tier": tier,
                "kind": kind,
                "content": content,
                "project_id": project_id if tier == "project" else "",
                "principal_id": principal_id,
                "evidence": {
                    "seal_id": seal_id,
                    "generation": generation,
                    "field": field,
                    "source_ref": _source_ref(claim["source"]),
                },
                "confidence": PROPOSAL_CONFIDENCE,
                "source": "agent",
            })
            if len(proposals) >= MAX_PROPOSALS:
                return proposals
    return proposals


def project_checkpoint_body(
    *,
    body: Any,
    seal_id: str,
    generation: int,
    session_id: str,
    memory_epoch: str,
    delete_epoch: str,
    principal_hash: str = "",
    store: MemoryStore | None = None,
    project_id: str = "",
) -> dict[str, Any] | None:
    """Write one snapshot plus proposed candidates; None means nothing stored."""
    document = parse_checkpoint_body(body)
    if document is None:
        logger.warning("Confirmed checkpoint body rejected for projection")
        return None
    sanitized = _sanitize_document(document)
    if sanitized is None:
        logger.warning("Confirmed checkpoint lost a required field after sanitizing")
        return None
    memory_store = store or _default_store()
    try:
        snapshot = memory_store.create_snapshot(
            session_id=session_id,
            seal_id=seal_id,
            body=sanitized,
            principal_hash=principal_hash,
            memory_epoch=memory_epoch,
            delete_epoch=delete_epoch,
            generation=generation,
        )
    except Exception:
        logger.warning("Working-memory snapshot write refused")
        return None
    proposals = build_proposals(
        sanitized,
        seal_id=seal_id,
        generation=generation,
        project_id=project_id,
    )
    stored = 0
    for proposal in proposals:
        try:
            memory_store.upsert_proposed(proposal)
            stored += 1
        except Exception:
            logger.warning("Memory candidate refused kind=%s", proposal["kind"])
    return {
        "snapshot_id": snapshot["id"],
        "seal_id": seal_id,
        "generation": generation,
        "candidates": stored,
    }


def project_confirmed_checkpoint(scope, *, store: MemoryStore | None = None) -> dict[str, Any] | None:
    """Project the confirmed checkpoint for this exact request scope.

    Only call after the host publication receipt is CONFIRMED. The memory-clear
    epoch is read at projection time; when it cannot be read the projection is
    skipped rather than stamped with a value readers could misjudge.
    """
    from app.api.compaction_checkpoint import read_confirmed_checkpoint
    from app.api.memory_epoch import read_memory_clear_epoch
    from app.db.project_identity import current_project_id

    checkpoint = read_confirmed_checkpoint(scope)
    if checkpoint is None:
        return None
    memory_epoch = read_memory_clear_epoch()
    if memory_epoch is None:
        logger.warning("Memory clear epoch unavailable; skipping projection")
        return None
    project_id = current_project_id()
    return project_checkpoint_body(
        body=checkpoint.body,
        seal_id=checkpoint.seal_id,
        generation=checkpoint.generation,
        session_id=checkpoint.session_id,
        principal_hash=checkpoint.principal_hash,
        memory_epoch=memory_epoch,
        delete_epoch=checkpoint.delete_epoch,
        store=store,
        project_id=project_id,
    )


__all__ = [
    "MAX_PROPOSALS",
    "build_proposals",
    "parse_checkpoint_body",
    "project_checkpoint_body",
    "project_confirmed_checkpoint",
]
