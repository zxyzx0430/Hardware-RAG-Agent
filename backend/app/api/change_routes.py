"""ChangeSet observation, review, revert and validation endpoints."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from app.api.dependencies import current_user
from app.api.session_owner import require_session_owner_if_bound
from app.api.session_workspace import stored_session_workspace
from app.db import change_store
from app.db.database import get_db
from app.db.models import Session as SessionModel
from src.agent.change_capture import (
    emit_change_event, file_digest, read_blob, workspace_context,
)
from src.agent.change_revert import revert_change_item, revert_change_set
from src.agent.validation_runner import run_validation

router = APIRouter(prefix="/api", tags=["changes"])


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data}


def _fail(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


def _require_session(db: DBSession, session_id: str, user: dict) -> None:
    try:
        require_session_owner_if_bound(db, session_id, user)
    except PermissionError as exc:
        raise _fail("FORBIDDEN", "无权访问该会话", 403) from exc
    if db.query(SessionModel.id).filter(SessionModel.id == session_id).first() is None:
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", 404)


def _require_set(db: DBSession, change_set_id: str, user: dict) -> dict[str, Any]:
    row = change_store.get_change_set(change_set_id)
    if row is None:
        raise _fail("CHANGE_SET_NOT_FOUND", "变更集不存在", 404)
    _require_session(db, row["session_id"], user)
    return row


class ItemReviewRequest(BaseModel):
    review_status: Literal["accepted", "rejected"]
    review_note: str | None = Field(default=None, max_length=2000)


class SetReviewRequest(BaseModel):
    review_status: Literal["accepted", "rejected"]
    review_note: str | None = Field(default=None, max_length=2000)


class ValidationRequest(BaseModel):
    command: str = Field(min_length=1, max_length=2000)
    cwd: str = Field(default="", max_length=1000)
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)


@router.get("/workspace/context")
async def get_workspace_context(
    _user: dict = Depends(current_user),
) -> dict[str, Any]:
    return _ok(await asyncio.to_thread(workspace_context))


@router.get("/sessions/{session_id}/change-sets")
async def list_change_sets(
    session_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    _require_session(db, session_id, user)
    bound_root = stored_session_workspace(session_id)
    if bound_root:
        workspace = await asyncio.to_thread(workspace_context, bound_root)
    else:
        workspace = await asyncio.to_thread(workspace_context)
    return _ok({
        "workspace": workspace,
        "change_sets": change_store.list_change_sets(session_id, limit),
    })


@router.get("/change-sets/{change_set_id}")
def get_change_set(
    change_set_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    row = _require_set(db, change_set_id, user)
    return _ok({
        "change_set": row,
        "workspace": workspace_context(row.get("workspace_root")),
    })


@router.get("/change-items/{change_item_id}/diff")
def get_change_item_diff(
    change_item_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    item = change_store.get_change_item(change_item_id)
    if item is None:
        raise _fail("CHANGE_ITEM_NOT_FOUND", "变更项不存在", 404)
    change_set = change_store.get_change_set(item["change_set_id"])
    if change_set is None:
        raise _fail("CHANGE_SET_NOT_FOUND", "变更集不存在", 404)
    _require_session(db, change_set["session_id"], user)
    before = read_blob(item.get("before_blob_ref"))
    after = read_blob(item.get("after_blob_ref"))
    available = before is not None or after is not None
    return _ok({
        "item": item,
        "before_content": before,
        "after_content": after,
        "available": available,
        "reason": None if available else "二进制或过大文件，仅记录 hash",
    })


def _review_result(item: dict[str, Any], review_status: str, note: str | None) -> dict[str, Any]:
    if item.get("status") != "observed":
        return {"item_id": item.get("id"), "ok": False, "error": "item is not reviewable"}
    if review_status == "accepted":
        current = file_digest(item.get("path", ""))
        if item.get("after_sha256") is not None and current != item.get("after_sha256"):
            change_store.update_change_item(item["id"], status="conflict")
            return {
                "item_id": item["id"], "ok": False,
                "error": "current file changed after capture", "status": "conflict",
            }
    updated = change_store.update_change_item(
        item["id"],
        review_status=review_status,
        review_note=note,
    )
    if updated is None:
        return {"item_id": item["id"], "ok": False, "error": "item disappeared"}
    change_store.record_event(
        change_set_id=item["change_set_id"],
        change_item_id=item["id"],
        event_type="reviewed",
        payload={"review_status": review_status, "review_note": note},
    )
    return {"item_id": item["id"], "ok": True, "item": updated}


@router.patch("/change-items/{change_item_id}")
def review_change_item(
    change_item_id: str,
    payload: ItemReviewRequest,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    item = change_store.get_change_item(change_item_id)
    if item is None:
        raise _fail("CHANGE_ITEM_NOT_FOUND", "变更项不存在", 404)
    change_set = _require_set(db, item["change_set_id"], user)
    result = _review_result(item, payload.review_status, payload.review_note)
    change_store.recompute_change_set_status(change_set["id"])
    emit_change_event(
        change_set["run_id"], "change_reviewed",
        {"change_set_id": change_set["id"], "result": result},
    )
    if not result.get("ok"):
        raise _fail("CHANGE_REVIEW_CONFLICT", result.get("error", "could not review item"), 409)
    return _ok({
        "item": result["item"],
        "change_set": change_store.get_change_set(change_set["id"]),
    })


@router.post("/change-sets/{change_set_id}/review")
def review_change_set(
    change_set_id: str,
    payload: SetReviewRequest,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    change_set = _require_set(db, change_set_id, user)
    results = [
        _review_result(item, payload.review_status, payload.review_note)
        for item in change_set.get("items", [])
        if item.get("status") == "observed"
    ]
    status = change_store.recompute_change_set_status(change_set_id)
    emit_change_event(
        change_set["run_id"], "change_reviewed",
        {"change_set_id": change_set_id, "results": results, "status": status},
    )
    return _ok({
        "results": results,
        "change_set": change_store.get_change_set(change_set_id),
    })


@router.post("/change-items/{change_item_id}/revert")
def revert_item(
    change_item_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    item = change_store.get_change_item(change_item_id)
    if item is None:
        raise _fail("CHANGE_ITEM_NOT_FOUND", "变更项不存在", 404)
    change_set = _require_set(db, item["change_set_id"], user)
    result = revert_change_item(item, str(change_set.get("workspace_root") or ""))
    refreshed = change_store.get_change_set(change_set["id"])
    if refreshed is not None:
        observed = [i for i in refreshed.get("items", []) if i.get("status") == "observed"]
        reverted = [i for i in refreshed.get("items", []) if i.get("status") == "reverted"]
        if reverted:
            change_store.set_change_set_status(
                change_set["id"], "partially_reverted" if observed else "reverted",
            )
            refreshed = change_store.get_change_set(change_set["id"])
    if not result.get("ok"):
        raise _fail("CHANGE_REVERT_FAILED", result.get("error", "could not revert item"), 409)
    return _ok({"result": result, "change_set": refreshed, "item": change_store.get_change_item(change_item_id)})


@router.post("/change-sets/{change_set_id}/revert")
def revert_set(
    change_set_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    change_set = _require_set(db, change_set_id, user)
    result = revert_change_set(change_set)
    refreshed = change_store.get_change_set(change_set_id)
    return _ok({"result": result, "change_set": refreshed})


@router.get("/change-sets/{change_set_id}/validations")
def list_validations(
    change_set_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    _require_set(db, change_set_id, user)
    return _ok({"validations": change_store.list_validation_runs(change_set_id)})


@router.post("/change-sets/{change_set_id}/validations/run")
async def run_validation_endpoint(
    change_set_id: str,
    payload: ValidationRequest,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    change_set = _require_set(db, change_set_id, user)
    result = await run_validation(
        change_set=change_set,
        command=payload.command,
        cwd=payload.cwd,
        timeout_ms=payload.timeout_ms,
    )
    if not result.get("ok"):
        return {
            "success": False,
            "data": result,
            "error": {"code": "VALIDATION_FAILED", "message": result.get("error", "validation failed")},
        }
    return _ok(result)
