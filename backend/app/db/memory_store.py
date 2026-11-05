"""Explicit memory store for Memory v1.

All read paths return plain dictionaries so callers never hold detached ORM
rows. Timestamps are stored as naive UTC (the SQLite DateTime convention used
elsewhere in this project) and serialized with an explicit ``Z`` suffix.

The store never persists obviously secret material: content and structured
JSON are checked on create and update.
"""

from __future__ import annotations

import datetime
import json
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, or_

from app.db.database import SessionLocal
from app.db.memory_models import (
    HARDWARE_PROFILE_KIND,
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_SOURCES,
    MemoryEntry,
)
from app.db.time_utils import to_utc_iso_z

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_CONFIDENCE = 0.7
MAX_CONTENT_CHARS = 4000
MAX_STRUCTURED_CHARS = 8000
MAX_SCOPE_KEY_CHARS = 512

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


def _normalize_structured(structured: Any) -> str:
    if structured is None:
        structured = {}
    if not isinstance(structured, dict):
        raise MemoryValidationError("structured must be a JSON object")
    try:
        serialized = json.dumps(
            structured,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise MemoryValidationError("structured must be JSON-serializable") from exc
    if len(serialized) > MAX_STRUCTURED_CHARS:
        raise MemoryValidationError(
            f"structured must serialize to at most {MAX_STRUCTURED_CHARS} characters"
        )
    if contains_secret(serialized):
        raise MemorySecretError("structured fields look like credentials and were not stored")
    return serialized


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
    return {
        "id": row.id,
        "scope": row.scope,
        "scope_key": row.scope_key,
        "kind": row.kind,
        "content": row.content,
        "structured": _safe_json_object(row.structured_json),
        "source": row.source,
        "confidence": row.confidence,
        "created_at": to_utc_iso_z(row.created_at),
        "updated_at": to_utc_iso_z(row.updated_at),
        "last_used_at": to_utc_iso_z(row.last_used_at) if row.last_used_at else None,
        "expires_at": to_utc_iso_z(row.expires_at) if row.expires_at else None,
        "supersedes_id": row.supersedes_id,
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
        scope: str,
        kind: str,
        content: str,
        scope_key: str = "",
        structured: dict | None = None,
        source: str = "agent",
        confidence: float = DEFAULT_CONFIDENCE,
        expires_at: Any = None,
        supersedes_id: Any = None,
    ) -> dict[str, Any]:
        scope = _normalize_scope(scope)
        scope_key = _normalize_scope_key(scope, scope_key)
        kind = _normalize_kind(kind)
        source = _normalize_source(source)
        content = _normalize_content(content)
        structured_json = _normalize_structured(structured)
        confidence = _normalize_confidence(confidence)
        expires = _coerce_datetime(expires_at, "expires_at")
        supersedes = _normalize_supersedes(supersedes_id)
        now = utcnow_naive()
        entry = MemoryEntry(
            id=uuid.uuid4().hex,
            scope=scope,
            scope_key=scope_key,
            kind=kind,
            content=content,
            structured_json=structured_json,
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
        scope: str | None = None,
        scope_key: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        limit: int = DEFAULT_LIMIT,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        limit = _clamp_limit(limit)
        if scope is not None:
            scope = _normalize_scope(scope)
        if kind is not None:
            kind = _normalize_kind(kind)
        now = utcnow_naive()
        with self._session() as db:
            query = db.query(MemoryEntry).filter(MemoryEntry.deleted_at.is_(None))
            if not include_expired:
                query = query.filter(
                    or_(MemoryEntry.expires_at.is_(None), MemoryEntry.expires_at > now)
                )
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
        scope: str | None = None,
        scope_key: str | None = None,
        kind: str | None = None,
        limit: int = 5,
        include_expired: bool = False,
        scope_groups: Sequence[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Keyword + confidence + recency ranking over active entries.

        A non-empty query only returns entries with at least one keyword hit.
        ``scope_groups`` selects an OR set of (scope, scope_key) pairs and is
        used by context injection; explicit ``scope``/``scope_key`` win.
        """
        limit = _clamp_limit(limit)
        if scope is not None:
            scope = _normalize_scope(scope)
        if kind is not None:
            kind = _normalize_kind(kind)
        normalized_query = (query or "").strip().lower()
        terms = _query_terms(normalized_query)
        now = utcnow_naive()
        with self._session() as db:
            base = db.query(MemoryEntry).filter(MemoryEntry.deleted_at.is_(None))
            if not include_expired:
                base = base.filter(
                    or_(MemoryEntry.expires_at.is_(None), MemoryEntry.expires_at > now)
                )
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
                .filter(MemoryEntry.id.in_(ids), MemoryEntry.deleted_at.is_(None))
                .update({MemoryEntry.last_used_at: used_at}, synchronize_session=False)
            )
