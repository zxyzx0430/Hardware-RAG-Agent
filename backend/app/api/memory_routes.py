"""Memory v1 HTTP API — /api/memory CRUD and ranked search."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.db.database import get_db
from app.db.memory_store import (
    DEFAULT_CONFIDENCE,
    DEFAULT_LIMIT,
    MemorySecretError,
    MemoryStore,
    MemoryValidationError,
    redact_secrets,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryCreatePayload(BaseModel):
    scope: str = "project"
    scope_key: str = ""
    kind: str
    content: str
    structured: Optional[dict[str, Any]] = None
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
    kind: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=200)


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


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_value(item) for key, item in value.items()}
    return value


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Strip nothing by contract; defense-in-depth redaction for imported rows."""
    public = dict(entry)
    public["content"] = redact_secrets(entry.get("content", ""))
    public["structured"] = _sanitize_value(entry.get("structured") or {})
    return public


@router.get("")
def list_memory(
    scope: Optional[str] = Query(default=None),
    scope_key: Optional[str] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = MemoryStore(db=db)
    try:
        entries = store.list(
            scope=scope, scope_key=scope_key, kind=kind, q=q, limit=limit
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
    try:
        entry = store.create(
            scope=payload.scope,
            scope_key=payload.scope_key,
            kind=payload.kind,
            content=payload.content,
            structured=payload.structured,
            source=payload.source,
            confidence=payload.confidence,
            expires_at=payload.expires_at,
            supersedes_id=payload.supersedes_id,
        )
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    db.commit()
    return {"success": True, "data": _public_entry(entry)}


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
            scope=payload.scope,
            scope_key=payload.scope_key,
            kind=payload.kind,
            limit=payload.limit,
        )
    except MemoryValidationError as exc:
        raise _validation_error(exc) from None
    return {
        "success": True,
        "data": {"results": [_public_entry(entry) for entry in results], "total": len(results)},
    }


__all__ = ["router", "MemoryCreatePayload", "MemoryUpdatePayload", "MemorySearchPayload"]
