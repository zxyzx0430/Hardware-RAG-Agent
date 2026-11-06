"""Versioned plan endpoints: latest/history, approve and reject."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from app.api.dependencies import current_user_optional
from app.api.session_owner import require_session_owner_if_bound
from app.db.database import get_db
from app.db.models import Session as SessionModel
from app.db import plan_store

router = APIRouter(prefix="/api", tags=["plans"])


def _ok(data: dict) -> dict:
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
    exists = db.query(SessionModel.id).filter(SessionModel.id == session_id).first()
    if exists is None:
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", 404)


def _decide(session_id: str, plan_id: str, action: str) -> dict:
    try:
        plan = plan_store.approve(session_id, plan_id) if action == "approve" else plan_store.reject(session_id, plan_id)
    except plan_store.PlanStateError as exc:
        raise _fail("PLAN_NOT_PENDING", "计划不存在或不是最新的待批准版本。", 409) from exc
    return _ok({"plan": plan})


@router.get("/sessions/{session_id}/plans/latest")
def latest_plan(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    _require_session(db, session_id, user)
    return _ok({"plan": plan_store.latest(session_id)})


@router.get("/sessions/{session_id}/plans")
def list_plans(
    session_id: str,
    limit: int = Query(default=plan_store.DEFAULT_LIST_LIMIT, ge=1, le=plan_store.MAX_LIST_LIMIT),
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    _require_session(db, session_id, user)
    return _ok({"plans": plan_store.list_metadata(session_id, limit)})


@router.post("/sessions/{session_id}/plans/{plan_id}/approve")
def approve_plan(
    session_id: str,
    plan_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    _require_session(db, session_id, user)
    return _decide(session_id, plan_id, "approve")


@router.post("/sessions/{session_id}/plans/{plan_id}/reject")
def reject_plan(
    session_id: str,
    plan_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    _require_session(db, session_id, user)
    return _decide(session_id, plan_id, "reject")
