"""Explicit memory store (Memory v2).

All read paths return plain dictionaries so callers never hold detached ORM
rows. Timestamps are stored as naive UTC (the SQLite DateTime convention used
elsewhere in this project) and serialized with an explicit ``Z`` suffix.

Default reads only expose ``status='active'``; review paths pass ``status``
explicitly. The legacy ``scope``/``scope_key`` filters keep working, while
``tier``/``project_id``/``principal_id`` are the v2 filtering surface.

The store never persists obviously secret material: content, structured JSON,
evidence JSON and snapshot bodies are checked on every write.
"""

from __future__ import annotations

import datetime
import json
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.database import SessionLocal
from app.db.memory_models import (
    DEFAULT_MEMORY_STATUS,
    DEFAULT_MEMORY_TIER,
    DEFAULT_PRINCIPAL_ID,
    HARDWARE_PROFILE_KIND,
    MEMORY_KINDS,
    MEMORY_SCHEMA_VERSION,
    MEMORY_SCOPES,
    MEMORY_SOURCES,
    MEMORY_STATUSES,
    MEMORY_TIERS,
    SCOPE_TO_TIER,
    TIER_TO_SCOPE,
    MemoryEntry,
    MemorySnapshot,
)
from app.db.time_utils import to_utc_iso_z

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_CONFIDENCE = 0.7
MAX_CONTENT_CHARS = 4000
MAX_STRUCTURED_CHARS = 8000
MAX_EVIDENCE_CHARS = 8000
MAX_SCOPE_KEY_CHARS = 512
MAX_PROJECT_ID_CHARS = 128
MAX_PRINCIPAL_ID_CHARS = 128
MAX_SNAPSHOT_BODY_CHARS = 262144
MAX_IDENTIFIER_CHARS = 128

# Default visibility for the legacy context-injection path: episodic history
# must not be injected as if it were working/project/user memory.
LEGACY_VISIBLE_TIERS = ("user", "project", "working")

STATUS_TRANSITIONS = {
    ("proposed", "active"),
    ("proposed", "rejected"),
    ("active", "superseded"),
}

_UNSET = object()


class MemoryValidationError(ValueError):
    """Invalid memory input (scope, kind, size, confidence, ...)."""

    code = "INVALID_MEMORY"


class MemorySecretError(MemoryValidationError):
    """Input looks like a credential and must not be stored."""

    code = "MEMORY_SECRET_REJECTED"


# Ordered from high-confidence formats to generic ``key=value`` assignments.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|apikey|access[_-]?key|secret|token|password|passwd|"
    r"authorization|credential)\b['\"]?\s*[:=]\s*['\"]?(?P<value>[^\s'\",}]{6,})"
)
_TERM_SPLIT_RE = re.compile(r"[\s,;，；、:：()（）\[\]【】\"'“”‘’/\\|]+")


def utcnow_naive() -> datetime.datetime:
    """Naive UTC now, matching how SQLite stores DateTime values here."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _assignment_value_is_secret(value: str) -> bool:
    # Reduces false positives such as "password: required" while still
    # catching generated keys, which contain digits or symbols.
    if len(value) >= 16:
        return True
    if any(ch.isdigit() for ch in value):
        return True
    return value.isupper()


def contains_secret(text: str | None) -> bool:
    """Return True when text contains an obvious credential pattern."""
    if not text:
        return False
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        return True
    for match in _ASSIGNMENT_RE.finditer(text):
        if _assignment_value_is_secret(match.group("value")):
            return True
    return False


def redact_secrets(text: str | None) -> str | None:
    """Redact obvious credentials before text is shown to the model."""
    if not text:
        return text
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)

    def _redact_assignment(match: re.Match[str]) -> str:
        value = match.group("value")
        if not _assignment_value_is_secret(value):
            return match.group(0)
        prefix_end = match.start("value") - match.start(0)
        return match.group(0)[:prefix_end] + "[REDACTED]"

    return _ASSIGNMENT_RE.sub(_redact_assignment, result)


def _coerce_datetime(value: Any, field_name: str) -> datetime.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MemoryValidationError(f"{field_name} must be an ISO-8601 datetime") from exc
    else:
        raise MemoryValidationError(f"{field_name} must be an ISO-8601 datetime")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def _normalize_scope(scope: Any) -> str:
    if not isinstance(scope, str) or scope not in MEMORY_SCOPES:
        raise MemoryValidationError("scope must be one of: " + ", ".join(MEMORY_SCOPES))
    return scope


def _normalize_scope_key(scope: str, scope_key: Any) -> str:
    if scope_key is None:
        scope_key = ""
    if not isinstance(scope_key, str):
        raise MemoryValidationError("scope_key must be a string")
    scope_key = scope_key.strip()
    if len(scope_key) > MAX_SCOPE_KEY_CHARS:
        raise MemoryValidationError("scope_key is too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in scope_key):
        raise MemoryValidationError("scope_key contains control characters")
    if scope == "global":
        return ""
    return scope_key


def _normalize_kind(kind: Any) -> str:
    if not isinstance(kind, str) or kind not in MEMORY_KINDS:
        raise MemoryValidationError("kind must be one of: " + ", ".join(MEMORY_KINDS))
    return kind


def _normalize_source(source: Any) -> str:
    if not isinstance(source, str) or source not in MEMORY_SOURCES:
        raise MemoryValidationError("source must be one of: " + ", ".join(MEMORY_SOURCES))
    return source


def _normalize_confidence(confidence: Any) -> float:
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise MemoryValidationError("confidence must be a number between 0 and 1")
    value = float(confidence)
    if not 0.0 <= value <= 1.0:
        raise MemoryValidationError("confidence must be between 0 and 1")
    return value


def _normalize_content(content: Any) -> str:
    if not isinstance(content, str) or not content.strip():
        raise MemoryValidationError("content must be a non-empty string")
    if len(content) > MAX_CONTENT_CHARS:
        raise MemoryValidationError(f"content must be at most {MAX_CONTENT_CHARS} characters")
    if "\x00" in content:
        raise MemoryValidationError("content must not contain NUL characters")
    if contains_secret(content):
        raise MemorySecretError("content looks like a credential and was not stored")
    return content


def _normalize_json_object(
    value: Any,
    *,
    field_name: str,
    max_chars: int,
    secret_message: str,
) -> str:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise MemoryValidationError(f"{field_name} must be a JSON object")
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise MemoryValidationError(f"{field_name} must be JSON-serializable") from exc
    if len(serialized) > max_chars:
        raise MemoryValidationError(
            f"{field_name} must serialize to at most {max_chars} characters"
        )
    if contains_secret(serialized):
        raise MemorySecretError(secret_message)
    return serialized


def _normalize_structured(structured: Any) -> str:
    return _normalize_json_object(
        structured,
        field_name="structured",
        max_chars=MAX_STRUCTURED_CHARS,
        secret_message="structured fields look like credentials and were not stored",
    )


def _normalize_evidence(evidence: Any) -> str:
    return _normalize_json_object(
        evidence,
        field_name="evidence",
        max_chars=MAX_EVIDENCE_CHARS,
        secret_message="evidence fields look like credentials and were not stored",
    )


def _normalize_tier(tier: Any) -> str:
    if not isinstance(tier, str) or tier not in MEMORY_TIERS:
        raise MemoryValidationError("tier must be one of: " + ", ".join(MEMORY_TIERS))
    return tier


def _normalize_status(status: Any) -> str:
    if not isinstance(status, str) or status not in MEMORY_STATUSES:
        raise MemoryValidationError("status must be one of: " + ", ".join(MEMORY_STATUSES))
    return status


def _normalize_project_id(project_id: Any) -> str:
    if project_id is None:
        return ""
    if not isinstance(project_id, str):
        raise MemoryValidationError("project_id must be a string")
    value = project_id.strip()
    if len(value) > MAX_PROJECT_ID_CHARS:
        raise MemoryValidationError("project_id is too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise MemoryValidationError("project_id contains control characters")
    return value


def _normalize_principal_id(principal_id: Any) -> str:
    if principal_id is None:
        return DEFAULT_PRINCIPAL_ID
    if not isinstance(principal_id, str):
        raise MemoryValidationError("principal_id must be a string")
    value = principal_id.strip()
    if not value or len(value) > MAX_PRINCIPAL_ID_CHARS:
        raise MemoryValidationError("principal_id is invalid")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise MemoryValidationError("principal_id contains control characters")
    return value


def _normalize_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryValidationError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > MAX_IDENTIFIER_CHARS:
        raise MemoryValidationError(f"{field_name} is too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise MemoryValidationError(f"{field_name} contains control characters")
    return normalized


def _normalize_supersedes(supersedes_id: Any) -> str | None:
    if supersedes_id is None or supersedes_id == "":
        return None
    if not isinstance(supersedes_id, str) or len(supersedes_id) > 128:
        raise MemoryValidationError("supersedes_id must be a short string")
    return supersedes_id


def _clamp_limit(limit: Any) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise MemoryValidationError("limit must be an integer")
    return max(1, min(limit, MAX_LIMIT))


def _safe_json_object(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _entry_to_dict(row: MemoryEntry) -> dict[str, Any]:
    tier = row.tier or SCOPE_TO_TIER.get(row.scope, DEFAULT_MEMORY_TIER)
    return {
        "id": row.id,
        "scope": row.scope,
        "scope_key": row.scope_key,
        "tier": tier,
        "status": row.status or DEFAULT_MEMORY_STATUS,
        "project_id": row.project_id or "",
        "principal_id": row.principal_id or DEFAULT_PRINCIPAL_ID,
        "kind": row.kind,
        "content": row.content,
        "structured": _safe_json_object(row.structured_json),
        "evidence": _safe_json_object(row.evidence_json),
        "source": row.source,
        "confidence": row.confidence,
        "schema_version": row.schema_version or MEMORY_SCHEMA_VERSION,
        "created_at": to_utc_iso_z(row.created_at),
        "updated_at": to_utc_iso_z(row.updated_at),
        "last_used_at": to_utc_iso_z(row.last_used_at) if row.last_used_at else None,
        "expires_at": to_utc_iso_z(row.expires_at) if row.expires_at else None,
        "supersedes_id": row.supersedes_id,
    }


def _snapshot_to_dict(row: MemorySnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "principal_hash": row.principal_hash or "",
        "memory_epoch": row.memory_epoch or "",
        "delete_epoch": row.delete_epoch or "",
        "seal_id": row.seal_id,
        "generation": int(row.generation or 0),
        "body": _safe_json_object(row.body_json),
        "body_json": row.body_json or "{}",
        "created_at": to_utc_iso_z(row.created_at),
        "updated_at": to_utc_iso_z(row.updated_at),
    }


def _is_expired(row: MemoryEntry, now: datetime.datetime) -> bool:
    return row.expires_at is not None and row.expires_at <= now


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _query_terms(query: str) -> list[str]:
    terms = [term for term in _TERM_SPLIT_RE.split(query.lower()) if term]
    return list(dict.fromkeys(terms))[:20]


def _keyword_score(content_lower: str, query: str, terms: Iterable[str]) -> float:
    if not query:
        return 0.0
    score = 0.0
    if query in content_lower:
        score += 3.0
    for term in terms:
        if term in content_lower:
            score += 1.0
    return score


def _recency_seconds(value: datetime.datetime | None) -> float:
    if value is None:
        return 0.0
    epoch = datetime.datetime(1970, 1, 1)
    return (value - epoch).total_seconds()


def _apply_status_filter(query, status: Any, include_inactive: bool):
    if status is not None:
        return query.filter(MemoryEntry.status == _normalize_status(status))
    if not include_inactive:
        return query.filter(MemoryEntry.status == DEFAULT_MEMORY_STATUS)
    return query


def _normalize_duplicate_text(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip()).casefold()


def _validate_generation(generation: Any) -> int:
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise MemoryValidationError("generation must be a non-negative integer")
    return generation


def _normalize_epoch(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MemoryValidationError(f"{field_name} must be a string")
    normalized = value.strip()
    if len(normalized) > MAX_IDENTIFIER_CHARS:
        raise MemoryValidationError(f"{field_name} is too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise MemoryValidationError(f"{field_name} contains control characters")
    return normalized


def _normalize_snapshot_body(body: Any) -> str:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise MemoryValidationError("snapshot body must be valid JSON") from exc
    return _normalize_json_object(
        body,
        field_name="snapshot body",
        max_chars=MAX_SNAPSHOT_BODY_CHARS,
        secret_message="snapshot body looks like credentials and was not stored",
    )


def purge_session_memory(db, session_id: str) -> dict[str, int]:
    """Delete working memory and snapshots for one session in ``db``.

    Project and user memory are never touched. The caller owns the transaction.
    Session ids come from the existing session route, so this helper keeps the
    same permissive id handling instead of re-validating the caller's input.
    """
    if not isinstance(session_id, str) or not session_id:
        return {"entries": 0, "snapshots": 0}
    entries = (
        db.query(MemoryEntry)
        .filter(
            or_(
                and_(MemoryEntry.tier == "working", MemoryEntry.scope_key == session_id),
                and_(MemoryEntry.scope == "session", MemoryEntry.scope_key == session_id),
            )
        )
        .delete(synchronize_session=False)
    )
    snapshots = (
        db.query(MemorySnapshot)
        .filter(MemorySnapshot.session_id == session_id)
        .delete(synchronize_session=False)
    )
    return {"entries": int(entries or 0), "snapshots": int(snapshots or 0)}


class MemoryStore:
    """CRUD, filtering and ranked search for ``memory_entries``.

    Pass ``db`` to reuse a caller-owned session (FastAPI ``get_db``); commits
    then belong to the caller. Without it the store opens and commits a session
    per operation from ``session_factory`` (defaults to ``SessionLocal``).
    """

    def __init__(self, session_factory=None, db=None):
        if db is not None and session_factory is not None:
            raise ValueError("pass either db or session_factory, not both")
        self._session_factory = session_factory or SessionLocal
        self._bound_db = db

    @contextmanager
    def _session(self):
        if self._bound_db is not None:
            yield self._bound_db
            self._bound_db.flush()
            return
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(
        self,
        *,
        kind: str,
        content: str,
        scope: str | None = None,
        tier: str | None = None,
        status: str = DEFAULT_MEMORY_STATUS,
        scope_key: str = "",
        project_id: Any = "",
        principal_id: Any = DEFAULT_PRINCIPAL_ID,
        evidence: Any = None,
        structured: dict | None = None,
        source: str = "agent",
        confidence: float = DEFAULT_CONFIDENCE,
        expires_at: Any = None,
        supersedes_id: Any = None,
    ) -> dict[str, Any]:
        if scope is None and tier is None:
            raise MemoryValidationError("either scope or tier is required")
        if scope is not None:
            scope = _normalize_scope(scope)
        if tier is not None:
            tier = _normalize_tier(tier)
        else:
            tier = SCOPE_TO_TIER[scope]
        if scope is None:
            scope = TIER_TO_SCOPE[tier]
        status = _normalize_status(status)
        if status == "superseded":
            raise MemoryValidationError("new entries cannot start as superseded")
        project_id = _normalize_project_id(project_id)
        principal_id = _normalize_principal_id(principal_id)
        if tier == "project" and not scope_key and project_id:
            scope_key = project_id
        scope_key = _normalize_scope_key(scope, scope_key)
        kind = _normalize_kind(kind)
        source = _normalize_source(source)
        content = _normalize_content(content)
        structured_json = _normalize_structured(structured)
        evidence_json = _normalize_evidence(evidence)
        confidence = _normalize_confidence(confidence)
        expires = _coerce_datetime(expires_at, "expires_at")
        supersedes = _normalize_supersedes(supersedes_id)
        now = utcnow_naive()
        entry = MemoryEntry(
            id=uuid.uuid4().hex,
            scope=scope,
            scope_key=scope_key,
            tier=tier,
            status=status,
            project_id=project_id,
            principal_id=principal_id,
            kind=kind,
            content=content,
            structured_json=structured_json,
            evidence_json=evidence_json,
            schema_version=MEMORY_SCHEMA_VERSION,
            source=source,
            confidence=confidence,
            created_at=now,
            updated_at=now,
            expires_at=expires,
            supersedes_id=supersedes,
        )
        with self._session() as db:
            db.add(entry)
            db.flush()
            return _entry_to_dict(entry)

    def get(
        self,
        entry_id: str,
        *,
        include_inactive: bool = False,
        include_deleted: bool = False,
        include_expired: bool = False,
    ) -> dict[str, Any] | None:
        if not isinstance(entry_id, str) or not entry_id:
            return None
        now = utcnow_naive()
        with self._session() as db:
            row = db.get(MemoryEntry, entry_id)
            if row is None:
                return None
            if not include_deleted and row.deleted_at is not None:
                return None
            if (
                not include_inactive
                and (row.status or DEFAULT_MEMORY_STATUS) != DEFAULT_MEMORY_STATUS
            ):
                return None
            if not include_expired and _is_expired(row, now):
                return None
            return _entry_to_dict(row)

    def update(
        self,
        entry_id: str,
        *,
        content: Any = _UNSET,
        structured: Any = _UNSET,
        confidence: Any = _UNSET,
        expires_at: Any = _UNSET,
    ) -> dict[str, Any] | None:
        if not isinstance(entry_id, str) or not entry_id:
            return None
        with self._session() as db:
            row = db.get(MemoryEntry, entry_id)
            if row is None or row.deleted_at is not None:
                return None
            if content is not _UNSET:
                row.content = _normalize_content(content)
            if structured is not _UNSET:
                row.structured_json = _normalize_structured(structured)
            if confidence is not _UNSET:
                row.confidence = _normalize_confidence(confidence)
            if expires_at is not _UNSET:
                row.expires_at = _coerce_datetime(expires_at, "expires_at")
            row.updated_at = utcnow_naive()
            db.flush()
            return _entry_to_dict(row)

    def soft_delete(self, entry_id: str) -> bool:
        if not isinstance(entry_id, str) or not entry_id:
            return False
        with self._session() as db:
            row = db.get(MemoryEntry, entry_id)
            if row is None or row.deleted_at is not None:
                return False
            now = utcnow_naive()
            row.deleted_at = now
            row.updated_at = now
            db.flush()
            return True

    def list(
        self,
        *,
        tier: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        principal_id: str | None = None,
        scope: str | None = None,
        scope_key: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        limit: int = DEFAULT_LIMIT,
        include_expired: bool = False,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        limit = _clamp_limit(limit)
        if tier is not None:
            tier = _normalize_tier(tier)
        if scope is not None:
            scope = _normalize_scope(scope)
        if kind is not None:
            kind = _normalize_kind(kind)
        if project_id is not None:
            project_id = _normalize_project_id(project_id)
        if principal_id is not None:
            principal_id = _normalize_principal_id(principal_id)
        now = utcnow_naive()
        with self._session() as db:
            query = db.query(MemoryEntry).filter(MemoryEntry.deleted_at.is_(None))
            query = _apply_status_filter(query, status, include_inactive)
            if not include_expired:
                query = query.filter(
                    or_(MemoryEntry.expires_at.is_(None), MemoryEntry.expires_at > now)
                )
            if tier is not None:
                query = query.filter(MemoryEntry.tier == tier)
            if project_id is not None:
                query = query.filter(MemoryEntry.project_id == project_id)
            if principal_id is not None:
                query = query.filter(MemoryEntry.principal_id == principal_id)
            if scope is not None:
                query = query.filter(MemoryEntry.scope == scope)
            if scope_key is not None:
                query = query.filter(MemoryEntry.scope_key == scope_key)
            if kind is not None:
                query = query.filter(MemoryEntry.kind == kind)
            if q and q.strip():
                pattern = _like_pattern(q.strip())
                query = query.filter(
                    or_(
                        MemoryEntry.content.like(pattern, escape="\\"),
                        MemoryEntry.structured_json.like(pattern, escape="\\"),
                        MemoryEntry.evidence_json.like(pattern, escape="\\"),
                    )
                )
            rows = (
                query.order_by(MemoryEntry.updated_at.desc(), MemoryEntry.created_at.desc())
                .limit(limit)
                .all()
            )
            return [_entry_to_dict(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        tier: str | None = None,
        tiers: Sequence[str] | None = None,
        status: str | None = None,
        project_id: str | None = None,
        principal_id: str | None = None,
        scope: str | None = None,
        scope_key: str | None = None,
        kind: str | None = None,
        limit: int = 5,
        include_expired: bool = False,
        include_inactive: bool = False,
        scope_groups: Sequence[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Keyword + confidence + recency ranking over active entries.

        A non-empty query only returns entries with at least one keyword hit.
        ``scope_groups`` selects an OR set of (scope, scope_key) pairs and is
        used by the legacy context injection path; explicit ``scope``/
        ``scope_key`` win. ``status=None`` means active unless
        ``include_inactive`` is set.
        """
        limit = _clamp_limit(limit)
        if tier is not None:
            tier = _normalize_tier(tier)
        normalized_tiers = None
        if tiers is not None:
            normalized_tiers = tuple(_normalize_tier(item) for item in tiers)
        if scope is not None:
            scope = _normalize_scope(scope)
        if kind is not None:
            kind = _normalize_kind(kind)
        if project_id is not None:
            project_id = _normalize_project_id(project_id)
        if principal_id is not None:
            principal_id = _normalize_principal_id(principal_id)
        normalized_query = (query or "").strip().lower()
        terms = _query_terms(normalized_query)
        now = utcnow_naive()
        with self._session() as db:
            base = db.query(MemoryEntry).filter(MemoryEntry.deleted_at.is_(None))
            base = _apply_status_filter(base, status, include_inactive)
            if not include_expired:
                base = base.filter(
                    or_(MemoryEntry.expires_at.is_(None), MemoryEntry.expires_at > now)
                )
            if tier is not None:
                base = base.filter(MemoryEntry.tier == tier)
            elif normalized_tiers is not None:
                base = base.filter(MemoryEntry.tier.in_(normalized_tiers))
            if project_id is not None:
                base = base.filter(MemoryEntry.project_id == project_id)
            if principal_id is not None:
                base = base.filter(MemoryEntry.principal_id == principal_id)
            if scope is not None:
                base = base.filter(MemoryEntry.scope == scope)
            if scope_key is not None:
                base = base.filter(MemoryEntry.scope_key == scope_key)
            if scope is None and scope_key is None and scope_groups:
                group_filters = [
                    and_(MemoryEntry.scope == group_scope, MemoryEntry.scope_key == group_key)
                    for group_scope, group_key in scope_groups
                ]
                if group_filters:
                    base = base.filter(or_(*group_filters))
                else:
                    return []
            if kind is not None:
                base = base.filter(MemoryEntry.kind == kind)
            if normalized_query:
                if terms:
                    term_filters = [
                        MemoryEntry.content.like(_like_pattern(term), escape="\\")
                        for term in terms
                    ]
                    base = base.filter(or_(*term_filters))
                rows = base.limit(MAX_LIMIT * 5).all()
            else:
                rows = (
                    base.order_by(MemoryEntry.confidence.desc(), MemoryEntry.updated_at.desc())
                    .limit(limit)
                    .all()
                )
            scored: list[tuple[tuple[float, float, float], Any]] = []
            for row in rows:
                keyword = _keyword_score(row.content.lower(), normalized_query, terms)
                if normalized_query and keyword <= 0.0:
                    continue
                recency = _recency_seconds(row.last_used_at or row.updated_at or row.created_at)
                scored.append(((keyword, float(row.confidence or 0.0), recency), row))
            scored.sort(key=lambda item: item[0], reverse=True)
            return [_entry_to_dict(row) for _, row in scored[:limit]]

    def search_visible(
        self,
        query: str,
        *,
        project_key: str = "",
        session_id: str | None = None,
        kind: str | None = None,
        limit: int = 5,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        """Search global memory plus the given project/session scopes."""
        groups: list[tuple[str, str]] = [("global", "")]
        if project_key:
            groups.append(("project", project_key))
        if session_id:
            groups.append(("session", session_id))
        return self.search(
            query,
            kind=kind,
            tiers=LEGACY_VISIBLE_TIERS,
            limit=limit,
            include_expired=include_expired,
            scope_groups=groups,
        )

    def mark_used(self, entry_ids: Iterable[str], *, at: Any = None) -> int:
        ids = [
            entry_id
            for entry_id in dict.fromkeys(entry_ids)
            if isinstance(entry_id, str) and entry_id
        ]
        if not ids:
            return 0
        used_at = _coerce_datetime(at, "last_used_at") or utcnow_naive()
        with self._session() as db:
            return (
                db.query(MemoryEntry)
                .filter(
                    MemoryEntry.id.in_(ids),
                    MemoryEntry.deleted_at.is_(None),
                    or_(
                        MemoryEntry.status == DEFAULT_MEMORY_STATUS,
                        MemoryEntry.status.is_(None),
                    ),
                )
                .update({MemoryEntry.last_used_at: used_at}, synchronize_session=False)
            )

    def update_status(
        self, entry_id: str, status: str, actor: str = "user"
    ) -> dict[str, Any] | None:
        """Apply one legal review transition; illegal or missing ids return None."""
        if not isinstance(entry_id, str) or not entry_id:
            return None
        target = _normalize_status(status)
        if not isinstance(actor, str) or not actor.strip():
            actor = "user"
        with self._session() as db:
            row = db.get(MemoryEntry, entry_id)
            if row is None or row.deleted_at is not None:
                return None
            current = row.status or DEFAULT_MEMORY_STATUS
            if (current, target) not in STATUS_TRANSITIONS:
                return None
            row.status = target
            row.updated_at = utcnow_naive()
            db.flush()
            return _entry_to_dict(row)

    def find_duplicate(
        self,
        tier: str,
        project_id: Any = "",
        principal_id: Any = DEFAULT_PRINCIPAL_ID,
        kind: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any] | None:
        """Find an active/proposed entry with the same normalized content."""
        tier = _normalize_tier(tier)
        project_id = _normalize_project_id(project_id)
        principal_id = _normalize_principal_id(principal_id)
        if kind is None or content is None:
            raise MemoryValidationError("kind and content are required")
        kind = _normalize_kind(kind)
        if not isinstance(content, str) or not content.strip():
            raise MemoryValidationError("content must be a non-empty string")
        normalized = _normalize_duplicate_text(content)
        now = utcnow_naive()
        with self._session() as db:
            rows = (
                db.query(MemoryEntry)
                .filter(
                    MemoryEntry.tier == tier,
                    MemoryEntry.project_id == project_id,
                    MemoryEntry.principal_id == principal_id,
                    MemoryEntry.kind == kind,
                    MemoryEntry.deleted_at.is_(None),
                    or_(MemoryEntry.expires_at.is_(None), MemoryEntry.expires_at > now),
                    MemoryEntry.status.in_(("active", "proposed")),
                )
                .order_by(MemoryEntry.status.asc(), MemoryEntry.updated_at.desc())
                .all()
            )
            for row in rows:
                if _normalize_duplicate_text(row.content) == normalized:
                    return _entry_to_dict(row)
            return None

    def upsert_proposed(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Create a proposed entry unless an active/proposed duplicate exists."""
        if not isinstance(proposal, dict):
            raise MemoryValidationError("proposal must be a JSON object")
        tier = proposal.get("tier")
        if tier is None:
            scope = proposal.get("scope")
            if not isinstance(scope, str) or scope not in SCOPE_TO_TIER:
                raise MemoryValidationError("proposal requires a valid tier or scope")
            tier = SCOPE_TO_TIER[scope]
        tier = _normalize_tier(tier)
        kind = proposal.get("kind") or "fact"
        content = proposal.get("content")
        project_id = proposal.get("project_id", "")
        principal_id = proposal.get("principal_id", DEFAULT_PRINCIPAL_ID)
        existing = self.find_duplicate(
            tier,
            project_id,
            principal_id,
            kind,
            content,
        )
        if existing is not None:
            return existing
        return self.create(
            tier=tier,
            status="proposed",
            kind=kind,
            content=content,
            scope=proposal.get("scope"),
            scope_key=proposal.get("scope_key", "") or "",
            project_id=project_id,
            principal_id=principal_id,
            evidence=proposal.get("evidence"),
            structured=proposal.get("structured"),
            source=proposal.get("source", "agent"),
            confidence=proposal.get("confidence", DEFAULT_CONFIDENCE),
        )

    def purge_session(self, session_id: str) -> dict[str, int]:
        """Physically delete working memory and snapshots for one session."""
        session_id = _normalize_identifier(session_id, "session_id")
        with self._session() as db:
            return purge_session_memory(db, session_id)

    def purge_expired(self, *, now: Any = None) -> dict[str, int]:
        """Physically delete soft-deleted and expired rows; never active rows."""
        moment = _coerce_datetime(now, "now") or utcnow_naive()
        with self._session() as db:
            soft_deleted = (
                db.query(MemoryEntry)
                .filter(MemoryEntry.deleted_at.isnot(None))
                .delete(synchronize_session=False)
            )
            expired = (
                db.query(MemoryEntry)
                .filter(
                    MemoryEntry.deleted_at.is_(None),
                    MemoryEntry.expires_at.isnot(None),
                    MemoryEntry.expires_at <= moment,
                )
                .delete(synchronize_session=False)
            )
            return {
                "soft_deleted": int(soft_deleted or 0),
                "expired": int(expired or 0),
            }

    def create_snapshot(
        self,
        *,
        session_id: str,
        seal_id: str,
        body: Any,
        principal_hash: str = "",
        memory_epoch: str = "",
        delete_epoch: str = "",
        generation: int = 0,
    ) -> dict[str, Any]:
        """Seal one checkpoint projection; duplicate ``(session, seal)`` is a no-op."""
        session_id = _normalize_identifier(session_id, "session_id")
        seal_id = _normalize_identifier(seal_id, "seal_id")
        if principal_hash is None:
            principal_hash = ""
        if not isinstance(principal_hash, str):
            raise MemoryValidationError("principal_hash must be a string")
        principal_hash = principal_hash.strip()
        if len(principal_hash) > MAX_IDENTIFIER_CHARS or any(
            ord(ch) < 32 or ord(ch) == 127 for ch in principal_hash
        ):
            raise MemoryValidationError("principal_hash is invalid")
        memory_epoch = _normalize_epoch(memory_epoch, "memory_epoch")
        delete_epoch = _normalize_epoch(delete_epoch, "delete_epoch")
        generation = _validate_generation(generation)
        body_json = _normalize_snapshot_body(body)
        now = utcnow_naive()
        with self._session() as db:
            statement = (
                sqlite_insert(MemorySnapshot)
                .values(
                    id=uuid.uuid4().hex,
                    session_id=session_id,
                    principal_hash=principal_hash,
                    memory_epoch=memory_epoch,
                    delete_epoch=delete_epoch,
                    seal_id=seal_id,
                    generation=generation,
                    body_json=body_json,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["session_id", "seal_id"])
            )
            db.execute(statement)
            db.flush()
            row = (
                db.query(MemorySnapshot)
                .filter(
                    MemorySnapshot.session_id == session_id,
                    MemorySnapshot.seal_id == seal_id,
                )
                .one()
            )
            return _snapshot_to_dict(row)

    def get_snapshot(self, session_id: str, seal_id: str) -> dict[str, Any] | None:
        session_id = _normalize_identifier(session_id, "session_id")
        seal_id = _normalize_identifier(seal_id, "seal_id")
        with self._session() as db:
            row = (
                db.query(MemorySnapshot)
                .filter(
                    MemorySnapshot.session_id == session_id,
                    MemorySnapshot.seal_id == seal_id,
                )
                .first()
            )
            return _snapshot_to_dict(row) if row is not None else None

    def list_snapshots(
        self,
        session_id: str,
        *,
        memory_epoch: str | None = None,
        delete_epoch: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """List snapshots newest-first, optionally restricted to current epochs."""
        session_id = _normalize_identifier(session_id, "session_id")
        limit = _clamp_limit(limit)
        with self._session() as db:
            query = db.query(MemorySnapshot).filter(MemorySnapshot.session_id == session_id)
            if memory_epoch is not None:
                query = query.filter(
                    MemorySnapshot.memory_epoch == _normalize_epoch(memory_epoch, "memory_epoch")
                )
            if delete_epoch is not None:
                query = query.filter(
                    MemorySnapshot.delete_epoch == _normalize_epoch(delete_epoch, "delete_epoch")
                )
            rows = (
                query.order_by(
                    MemorySnapshot.created_at.desc(),
                    MemorySnapshot.id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [_snapshot_to_dict(row) for row in rows]

    def latest_snapshot(
        self,
        session_id: str,
        *,
        memory_epoch: str | None = None,
        delete_epoch: str | None = None,
    ) -> dict[str, Any] | None:
        snapshots = self.list_snapshots(
            session_id,
            memory_epoch=memory_epoch,
            delete_epoch=delete_epoch,
            limit=1,
        )
        return snapshots[0] if snapshots else None
