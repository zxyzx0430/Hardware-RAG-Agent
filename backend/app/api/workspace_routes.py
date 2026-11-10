"""Session-scoped external workspace binding.

The Explorer owns the bearer-token root authorization. Chat may bind one of
those already-authorized roots to a session; it never accepts a raw client path
as trusted state.
"""
from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from app.api.dependencies import current_user
from app.api.explorer_routes import require_explorer_session
from app.api.session_owner import require_session_owner_if_bound
from app.api.session_workspace import resolve_session_workspace
from app.db.database import get_db
from app.db.models import Session as SessionModel
from src.explorer.security import (
    ExplorerSecurityError,
    require_authorized_root,
)
from src.agent import workspace_git

router = APIRouter(prefix="/api", tags=["workspace"])


class BindWorkspaceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


def _fail(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data}


def _session_for_write(db: DBSession, session_id: str, user: dict) -> SessionModel:
    try:
        require_session_owner_if_bound(db, session_id, user)
    except PermissionError as exc:
        raise _fail("FORBIDDEN", "无权访问该会话", 403) from exc
    row = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if row is None:
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", 404)
    return row


@router.post("/sessions/{session_id}/workspace/bind")
def bind_workspace(
    session_id: str,
    payload: BindWorkspaceRequest,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
    token: str = Depends(require_explorer_session),
) -> dict[str, Any]:
    row = _session_for_write(db, session_id, user)
    try:
        root = require_authorized_root(payload.path, token)
    except ExplorerSecurityError as exc:
        raise _fail(
            "WORKSPACE_NOT_AUTHORIZED",
            "该文件夹尚未在 Explorer 中打开授权；请先打开文件夹后重试。",
            403,
        ) from exc
    if not root.is_dir():
        raise _fail("WORKSPACE_NOT_DIRECTORY", "工作区必须是文件夹", 400)
    row.workspace_root = str(root)
    row.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    return _ok({"session_id": session_id, "workspace_root": row.workspace_root})


@router.delete("/sessions/{session_id}/workspace/bind")
def unbind_workspace(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    row = _session_for_write(db, session_id, user)
    row.workspace_root = None
    row.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    return _ok({"session_id": session_id, "workspace_root": None})


@router.get("/sessions/{session_id}/workspace")
def get_bound_workspace(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    row = _session_for_write(db, session_id, user)
    return _ok({"session_id": session_id, "workspace_root": row.workspace_root})


class GitBranchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class GitCommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    paths: list[str] = Field(default_factory=list, max_length=200)


class GitPrRequest(BaseModel):
    base: str = Field(default="main", max_length=128)
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=20000)
    push: bool = False


def _workspace_for_request(
    session_id: str, request: Request, db: DBSession, user: dict,
) -> str:
    _session_for_write(db, session_id, user)
    try:
        return resolve_session_workspace(session_id, request)
    except Exception as exc:
        raise _fail("WORKSPACE_NOT_AUTHORIZED", str(exc), 409) from exc


def _git_result(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        raise _fail(
            str(result.get("code") or "GIT_OPERATION_FAILED"),
            str(result.get("message") or "Git 操作失败"),
            409,
        )
    return result


@router.get("/sessions/{session_id}/git")
def get_git_context(
    session_id: str,
    request: Request,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    root = _workspace_for_request(session_id, request, db, user)
    return _ok(workspace_git.git_context(root))


@router.post("/sessions/{session_id}/git/branch")
def create_git_branch(
    session_id: str,
    payload: GitBranchRequest,
    request: Request,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    root = _workspace_for_request(session_id, request, db, user)
    return _ok(_git_result(workspace_git.create_branch(root, payload.name)))


@router.post("/sessions/{session_id}/git/commit")
def commit_git_changes(
    session_id: str,
    payload: GitCommitRequest,
    request: Request,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    root = _workspace_for_request(session_id, request, db, user)
    return _ok(_git_result(workspace_git.commit_paths(root, payload.paths, payload.message)))


@router.get("/sessions/{session_id}/git/pr")
def get_git_pr(
    session_id: str,
    request: Request,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    root = _workspace_for_request(session_id, request, db, user)
    # Read-only status: return the diagnostic in the payload so the UI can
    # show NO_GITHUB_REMOTE / GH_AUTH_REQUIRED instead of a generic 409.
    return _ok(workspace_git.pr_status(root))


@router.post("/sessions/{session_id}/git/pr")
def create_git_pr(
    session_id: str,
    payload: GitPrRequest,
    request: Request,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    root = _workspace_for_request(session_id, request, db, user)
    return _ok(_git_result(workspace_git.create_draft_pr(
        root,
        base=payload.base,
        title=payload.title,
        body=payload.body,
        push=payload.push,
    )))
