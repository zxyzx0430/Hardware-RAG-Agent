"""Server-authoritative session TODO snapshot endpoints.

``GET`` returns the persisted list so a refreshed client can restore it;
``DELETE`` clears it. Both routes require an existing session and reuse the
session-route ownership rule: an owner-bound session is readable only by that
verified principal, while unbound legacy sessions stay accessible.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.api.dependencies import current_user_optional
from app.api.session_owner import require_session_owner_if_bound
from app.db.database import get_db
from app.db.models import Session as SessionModel
from app.db.time_utils import to_utc_iso_z
from app.db.todo_store import clear_todos, list_todos

router = APIRouter(prefix="/api", tags=["todos"])

_TODO_FIELDS = ("content", "status", "priority")


def _ok(data: dict) -> dict:
    return {"success": True, "data": data}


def _fail(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


def _require_session_owner_if_bound(
    db: DBSession,
    session_id: str,
    user: dict,
) -> None:
    """Preserve shared legacy sessions while enforcing an existing owner binding."""
    try:
        require_session_owner_if_bound(db, session_id, user)
    except PermissionError as exc:
        raise _fail("FORBIDDEN", "无权访问该会话", status_code=403) from exc
    _require_existing_session(db, session_id)


def _require_existing_session(db: DBSession, session_id: str) -> None:
    exists = db.query(SessionModel.id).filter(
        SessionModel.id == session_id
    ).first()
    if exists is None:
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)


def _public_todos(rows: list[dict]) -> list[dict]:
    return [{field: row[field] for field in _TODO_FIELDS} for row in rows]


@router.get("/sessions/{session_id}/todos")
def get_session_todos(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    """Return the session's persisted TODO snapshot and its newest update time."""
    _require_session_owner_if_bound(db, session_id, user)
    rows = list_todos(session_id)
    newest = max(
        (row["updated_at"] for row in rows if row["updated_at"] is not None),
        default=None,
    )
    return _ok({
        "todos": _public_todos(rows),
        "updated_at": to_utc_iso_z(newest) or None,
    })


@router.delete("/sessions/{session_id}/todos")
def delete_session_todos(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    """Clear the session's TODO list; an already empty list still succeeds."""
    _require_session_owner_if_bound(db, session_id, user)
    clear_todos(session_id)
    return _ok({"todos": [], "cleared": True})
