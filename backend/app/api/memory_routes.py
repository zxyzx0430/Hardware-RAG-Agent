"""Memory HTTP API — /api/memory CRUD, review workflow and snapshots.

Read paths default to ``status=active``; the review workflow uses explicit
``/proposals/{id}/approve`` and ``/proposals/{id}/reject`` endpoints instead of
a free-form status PATCH. Every route requires ``current_user``; this build is
single-user local, so ``principal_id`` defaults to ``local``.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.api.memory_epoch import MEMORY_CLEAR_EPOCH_KEY
from app.api.session_epoch import SESSION_DELETE_EPOCH_PREFIX
from app.db.database import get_db
from app.db.memory_models import (
    DEFAULT_PRINCIPAL_ID,
    MEMORY_SCHEMA_VERSION,
    MemoryEntry,
    MemorySnapshot,
)
from app.db.memory_store import (
    DEFAULT_CONFIDENCE,
    DEFAULT_LIMIT,
    MemoryStore,
    MemoryValidationError,
    redact_secrets,
)
from src.agent import memory_export, memory_policy, memory_project
from src.agent.memory_context import snapshot_field_texts

router = APIRouter(prefix="/api/memory", tags=["memory"])

_COLUMN_PROBES = (
    "tier",
    "status",
    "project_id",
    "principal_id",
    "evidence_json",
    "schema_version",
)


class MemoryCreatePayload(BaseModel):
    scope: str = "project"
    tier: Optional[str] = None
    scope_key: str = ""
    project_id: str = ""
    principal_id: str = DEFAULT_PRINCIPAL_ID
    kind: str
    content: str
    structured: Optional[dict[str, Any]] = None
    evidence: Optional[dict[str, Any]] = None
    source: str = "user"
    confidence: float = DEFAULT_CONFIDENCE
    expires_at: Optional[str] = None
    supersedes_id: Optional[str] = None


class MemoryUpdatePayload(BaseModel):
    content: Optional[str] = None
    structured: Optional[dict[str, Any]] = None
    confidence: Optional[float] = None
    expires_at: Optional[str] = None


class MemorySearchPayload(BaseModel):
    query: str = ""
    scope: Optional[str] = None
    scope_key: Optional[str] = None
    tier: Optional[str] = None
    project_id: Optional[str] = None
    kind: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=200)


def _clean_filter(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


def _validation_error(exc: MemoryValidationError) -> HTTPException:
    return _error(
        status.HTTP_400_BAD_REQUEST,
        getattr(exc, "code", "INVALID_MEMORY"),
        str(exc),
    )


def _transition_error(exc: memory_policy.IllegalTransitionError) -> HTTPException:
    return _error(
        status.HTTP_409_CONFLICT,
        getattr(exc, "code", "MEMORY_INVALID_TRANSITION"),
        str(exc),
    )


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_value(item) for key, item in value.items()}
    return value


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Defense-in-depth redaction for imported rows; fields stay unchanged."""
    public = dict(entry)
    public["content"] = redact_secrets(entry.get("content", ""))
    public["structured"] = _sanitize_value(entry.get("structured") or {})
    public["evidence"] = _sanitize_value(entry.get("evidence") or {})
    return public


def _table_columns(db: Session, table_name: str) -> set[str]:
    try:
        rows = db.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    except Exception:
        return set()
    return {row[1] for row in rows}


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        row = db.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = :name"),
            {"name": table_name},
        ).first()
    except Exception:
        return False
    return row is not None


def _read_setting(db: Session, key: str) -> str:
    from app.db.models import Settings

    row = db.query(Settings).filter(Settings.key == key).first()
    return row.value if row is not None else ""


def _epochs_from_db(db: Session, session_id: str | None) -> dict[str, str] | None:
    """Read current epochs from the request database, not the global one."""
    try:
        memory_epoch = _read_setting(db, MEMORY_CLEAR_EPOCH_KEY)
        digest = hashlib.sha256((session_id or "default").encode("utf-8")).hexdigest()
        delete_epoch = _read_setting(db, SESSION_DELETE_EPOCH_PREFIX + digest)
    except Exception:
        return None
    if not isinstance(memory_epoch, str) or not isinstance(delete_epoch, str):
        return None
    return {"memory_epoch": memory_epoch, "delete_epoch": delete_epoch}


def _memory_counts(db: Session) -> dict[str, int] | None:
    try:
        base = db.query(func.count(MemoryEntry.id)).filter(
            MemoryEntry.deleted_at.is_(None)
        )
        counts = {"entries": int(base.scalar() or 0)}
        for label in ("active", "proposed", "rejected", "superseded"):
            counts[label] = int(
                base.filter(MemoryEntry.status == label).scalar() or 0
            )
        counts["snapshots"] = int(
            db.query(func.count(MemorySnapshot.id)).scalar() or 0
        )
        return counts
    except Exception:
        return None


def _snapshot_summary(
    snapshot: dict[str, Any], epochs: dict[str, str] | None
) -> dict[str, Any]:
    valid: bool | None = None
    if epochs is not None:
        valid = (
            snapshot.get("memory_epoch") == epochs.get("memory_epoch")
            and snapshot.get("delete_epoch") == epochs.get("delete_epoch")
        )
    return {
        "id": snapshot.get("id"),
        "session_id": snapshot.get("session_id"),
        "seal_id": snapshot.get("seal_id"),
        "generation": snapshot.get("generation", 0),
        "memory_epoch": snapshot.get("memory_epoch", ""),
        "delete_epoch": snapshot.get("delete_epoch", ""),
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "valid": valid,
        "fields": snapshot_field_texts(snapshot.get("body")),
    }


@router.get("")
def list_memory(
    scope: Optional[str] = Query(default=None),
    scope_key: Optional[str] = Query(default=None),
    tier: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    project_id: Optional[str] = Query(default=None),
    principal_id: Optional[str] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = MemoryStore(db=db)
    try:
        entries = store.list(
            scope=_clean_filter(scope),
            scope_key=scope_key,
            tier=_clean_filter(tier),
            status=status_filter or "active",
            project_id=_clean_filter(project_id),
            principal_id=_clean_filter(principal_id),
            kind=_clean_filter(kind),
            q=q,
            limit=limit,
        )
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    return {
        "success": True,
        "data": {"entries": [_public_entry(entry) for entry in entries], "total": len(entries)},
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_memory(
    payload: MemoryCreatePayload,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = MemoryStore(db=db)
    provided = payload.model_fields_set
    tier = _clean_filter(payload.tier)
    scope: Optional[str] = payload.scope
    if "tier" in provided:
        # The v2 tier is authoritative; the legacy scope column is derived.
        scope = None
    project_id = payload.project_id
    if tier == "user":
        project_id = ""
    elif tier == "project" and not project_id:
        project_id = memory_project.current_project_id()
    if memory_policy.is_hardware_candidate(payload.kind, payload.structured):
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "MEMORY_HARDWARE_PROFILE",
            "hardware profiles are stored outside generic memory",
        )
    try:
        entry = store.create(
            scope=scope,
            tier=tier,
            scope_key=payload.scope_key,
            project_id=project_id,
            principal_id=payload.principal_id,
            kind=payload.kind,
            content=payload.content,
            structured=payload.structured,
            evidence=payload.evidence,
            source=payload.source,
            confidence=payload.confidence,
            expires_at=payload.expires_at,
            supersedes_id=payload.supersedes_id,
        )
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    db.commit()
    return {"success": True, "data": _public_entry(entry)}


@router.get("/proposals")
def list_proposals(
    tier: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    principal_id: Optional[str] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = MemoryStore(db=db)
    try:
        entries = store.list(
            tier=_clean_filter(tier),
            status="proposed",
            project_id=_clean_filter(project_id),
            principal_id=_clean_filter(principal_id),
            limit=limit,
        )
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    return {
        "success": True,
        "data": {"entries": [_public_entry(entry) for entry in entries], "total": len(entries)},
    }


def _review_proposal(
    entry_id: str,
    action: str,
    db: Session,
    user: dict,
) -> dict[str, Any]:
    store = MemoryStore(db=db)
    entry = store.get(entry_id, include_inactive=True)
    if entry is None:
        raise _error(status.HTTP_404_NOT_FOUND, "MEMORY_NOT_FOUND", "memory entry not found")
    try:
        target = memory_policy.review_target(entry.get("status"), action)
    except memory_policy.IllegalTransitionError as exc:
        raise _transition_error(exc) from None
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    actor = "user"
    if isinstance(user, dict) and isinstance(user.get("provider"), str):
        actor = user["provider"]
    updated = store.update_status(entry_id, target, actor=actor)
    if updated is None:
        raise _error(
            status.HTTP_409_CONFLICT,
            "MEMORY_INVALID_TRANSITION",
            "memory entry changed state before the review was applied",
        )
    db.commit()
    return {"success": True, "data": _public_entry(updated)}


@router.post("/proposals/{entry_id}/approve")
def approve_proposal(
    entry_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    return _review_proposal(entry_id, "approve", db, _user)


@router.post("/proposals/{entry_id}/reject")
def reject_proposal(
    entry_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    return _review_proposal(entry_id, "reject", db, _user)


@router.get("/episodes")
def list_episodes(
    session_id: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = MemoryStore(db=db)
    try:
        snapshots = store.list_snapshots(session_id, limit=limit)
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    epochs = _epochs_from_db(db, session_id)
    return {
        "success": True,
        "data": {
            "episodes": [_snapshot_summary(snapshot, epochs) for snapshot in snapshots],
            "total": len(snapshots),
            "epochs": epochs,
        },
    }


@router.get("/status")
def memory_status(
    session_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    columns = _table_columns(db, "memory_entries")
    tables = {
        "memory_entries": bool(columns),
        "memory_snapshots": _table_exists(db, "memory_snapshots"),
    }
    legacy_rows: int | None = None
    if "schema_version" in columns:
        try:
            legacy_rows = int(
                db.query(func.count(MemoryEntry.id))
                .filter(
                    (MemoryEntry.schema_version.is_(None))
                    | (MemoryEntry.schema_version < MEMORY_SCHEMA_VERSION)
                )
                .scalar()
                or 0
            )
        except Exception:
            legacy_rows = None
    epochs = _epochs_from_db(db, session_id) if session_id else None
    working_summary = None
    if session_id and epochs is not None and tables["memory_snapshots"]:
        try:
            snapshot = MemoryStore(db=db).latest_snapshot(
                session_id,
                memory_epoch=epochs["memory_epoch"],
                delete_epoch=epochs["delete_epoch"],
            )
        except MemoryValidationError:
            snapshot = None
        if snapshot is not None:
            working_summary = _snapshot_summary(snapshot, epochs)
    home = memory_export.memory_home()
    return {
        "success": True,
        "data": {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "tables": tables,
            "columns": {name: name in columns for name in _COLUMN_PROBES},
            "legacy_rows": legacy_rows,
            "counts": _memory_counts(db),
            "epochs": epochs,
            "working_snapshot": working_summary,
            "export": {"home": str(home), "exists": home.exists()},
        },
    }


@router.patch("/{entry_id}")
def update_memory(
    entry_id: str,
    payload: MemoryUpdatePayload,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if "content" in payload.model_fields_set:
        changes["content"] = payload.content
    if "structured" in payload.model_fields_set:
        changes["structured"] = payload.structured
    if "confidence" in payload.model_fields_set:
        changes["confidence"] = payload.confidence
    if "expires_at" in payload.model_fields_set:
        changes["expires_at"] = payload.expires_at
    store = MemoryStore(db=db)
    try:
        entry = store.update(entry_id, **changes) if changes else store.get(entry_id)
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    if entry is None:
        raise _error(status.HTTP_404_NOT_FOUND, "MEMORY_NOT_FOUND", "memory entry not found")
    if changes:
        db.commit()
    return {"success": True, "data": _public_entry(entry)}


@router.delete("/{entry_id}")
def delete_memory(
    entry_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = MemoryStore(db=db)
    if not store.soft_delete(entry_id):
        raise _error(status.HTTP_404_NOT_FOUND, "MEMORY_NOT_FOUND", "memory entry not found")
    db.commit()
    return {"success": True, "data": {"deleted": True, "id": entry_id}}


@router.post("/search")
def search_memory(
    payload: MemorySearchPayload,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = MemoryStore(db=db)
    try:
        results = store.search(
            payload.query,
            scope=_clean_filter(payload.scope),
            scope_key=payload.scope_key,
            tier=_clean_filter(payload.tier),
            project_id=_clean_filter(payload.project_id),
            kind=_clean_filter(payload.kind),
            limit=payload.limit,
        )
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    return {
        "success": True,
        "data": {"results": [_public_entry(entry) for entry in results], "total": len(results)},
    }


__all__ = ["router", "MemoryCreatePayload", "MemoryUpdatePayload", "MemorySearchPayload"]
