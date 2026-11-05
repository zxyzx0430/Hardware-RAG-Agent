"""HTTP API for hardware profiles (Memory v1 / hardware-profile Skill).

Profiles are only read/written explicitly by the management panel or the
hardware_profile Agent tool. This module never injects profile content into a
chat request.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import current_user
from app.db.hardware_profile_store import (
    HardwareProfileSecretError,
    HardwareProfileStore,
    HardwareProfileValidationError,
)

router = APIRouter(prefix="/api/hardware-profiles", tags=["hardware-profiles"])


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


def _validation_error(exc: Exception) -> HTTPException:
    code = "HARDWARE_PROFILE_SECRET" if isinstance(exc, HardwareProfileSecretError) else "INVALID_HARDWARE_PROFILE"
    return _error(status.HTTP_400_BAD_REQUEST, code, str(exc))


@router.get("")
def list_profiles(
    q: str = Query(default="", max_length=256),
    limit: int = Query(default=50, ge=1, le=200),
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = HardwareProfileStore()
    try:
        records = store.search(q, limit=limit) if q.strip() else store.list_all(limit=limit)
    except HardwareProfileValidationError as exc:
        raise _validation_error(exc) from None
    profiles = [record.to_dict() for record in records]
    return {"success": True, "data": {"profiles": profiles, "total": len(profiles)}}


@router.get("/{profile_id}")
def get_profile(
    profile_id: str,
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    try:
        record = HardwareProfileStore().get(profile_id)
    except HardwareProfileValidationError as exc:
        raise _validation_error(exc) from None
    if record is None:
        raise _error(status.HTTP_404_NOT_FOUND, "HARDWARE_PROFILE_NOT_FOUND", "hardware profile not found")
    return {"success": True, "data": record.to_dict()}


@router.post("", status_code=status.HTTP_201_CREATED)
def upsert_profile(
    payload: dict[str, Any],
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    try:
        record = HardwareProfileStore().upsert(payload)
    except HardwareProfileValidationError as exc:
        raise _validation_error(exc) from None
    return {"success": True, "data": record.to_dict()}


__all__ = ["router"]
