"""Resolve a chat session's bound Agent workspace safely."""
from __future__ import annotations

from pathlib import Path

from fastapi import Request

from app.db.database import SessionLocal
from app.db.models import Session as SessionModel
from src.agent.path_guard import PROJECT_ROOT
from src.explorer.security import is_authorized


class WorkspaceAuthorizationError(RuntimeError):
    """The bound root is no longer authorized for the current bearer token."""


def _bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    if not value.lower().startswith("bearer "):
        return None
    token = value.split(" ", 1)[1].strip()
    return token or None


def stored_session_workspace(session_id: str | None) -> str | None:
    """Return the persisted bound root without rechecking Explorer authorization."""
    if not session_id:
        return None
    with SessionLocal() as db:
        row = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        root = getattr(row, "workspace_root", None) if row is not None else None
    return str(root) if root else None


def resolve_session_workspace(session_id: str | None, request: Request) -> str:
    """Return a bound external root after rechecking Explorer authorization.

    No bound root means the legacy project root. A bound root that is no longer
    authorized must fail closed instead of silently editing a different folder.
    """
    if not session_id:
        return str(PROJECT_ROOT)
    with SessionLocal() as db:
        row = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        root = getattr(row, "workspace_root", None) if row is not None else None
    if not root:
        return str(PROJECT_ROOT)
    resolved = Path(root)
    token = _bearer_token(request)
    if not token or not is_authorized(resolved, token):
        raise WorkspaceAuthorizationError(
            "绑定工作区未重新授权；请在 Explorer 中重新打开该文件夹。"
        )
    return str(resolved.resolve())
