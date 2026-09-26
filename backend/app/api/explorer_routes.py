"""Explorer routes — file tree browser + editable code preview backend."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from app.api.sse import sse_event
from app.api.auth import get_provider_key_by_session
from src.explorer.files import (
    copy_node,
    create_node,
    FileVersionConflictError,
    move_node,
    read_file_response,
    rename_node,
    write_text_file,
)
from src.explorer.trash import ExplorerTrashError, list_trash, move_to_trash, restore_from_trash
from src.explorer.search import search_content
from src.explorer.security import (
    ExplorerSecurityError,
    authorize_root,
    authorized_root_for,
    require_authorized_root,
    validate_path,
)
from src.explorer.tree import build_tree, build_tree_for_dir
from src.explorer.watcher import WatchEvent, get_watch_manager

logger = logging.getLogger(__name__)


class ExplorerAuthRoute(APIRoute):
    """Return Explorer auth failures in the shared API error envelope."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def route_handler(request: Request):
            try:
                return await original_handler(request)
            except HTTPException as exc:
                detail = exc.detail
                if exc.status_code == 401 and isinstance(detail, dict) and detail.get("success") is False:
                    return JSONResponse(status_code=401, content=detail)
                raise

        return route_handler


def require_explorer_session(
    authorization: str | None = Header(default=None),
) -> str:
    """Require a valid application session for every Explorer route.

    First-time provider setup remains available through /api/auth/store-key;
    Explorer itself never uses the anonymous setup exception.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={"success": False, "error": {"code": "AUTH_REQUIRED", "message": "Explorer requires a session", "details": None}},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token or not get_provider_key_by_session(token):
        raise HTTPException(
            status_code=401,
            detail={"success": False, "error": {"code": "AUTH_INVALID", "message": "Explorer session is invalid or expired", "details": None}},
        )
    return token


router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_explorer_session)],
    route_class=ExplorerAuthRoute,
)


class OpenRequest(BaseModel):
    path: str


class WriteRequest(BaseModel):
    path: str
    content: str
    expected_version: str | None = Field(...)


class CreateRequest(BaseModel):
    path: str
    type: str = Field(default="file", pattern="^(file|directory)$")


class RenameRequest(BaseModel):
    path: str
    new_name: str


class DeleteRequest(BaseModel):
    path: str


class RestoreRequest(BaseModel):
    root_path: str
    item_id: str
    target_path: str | None = None


class MoveRequest(BaseModel):
    path: str
    target_dir: str


class CopyRequest(BaseModel):
    path: str
    target_dir: str


class SearchRequest(BaseModel):
    root_path: str
    query: str
    max_results: int = 100
    include_pattern: str = "*"


class RevealRequest(BaseModel):
    path: str


# ═══════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════

def _error_response(
    message: str,
    detail: str | None = None,
    status: int = 400,
    code: str = "EXPLORER_ERROR",
) -> JSONResponse:
    full_message = f"{message}: {detail}" if detail else message
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": {"code": code, "message": full_message, "details": detail}},
    )


def _to_response(result: dict[str, Any]) -> JSONResponse | dict[str, Any]:
    status = result.pop("_status", None)
    if status is None:
        return result
    return JSONResponse(status_code=status, content=result)


def _ensure_authorized(path: Path, session_id: str) -> Path:
    return validate_path(str(path), must_exist=False, session_id=session_id)


def _git_head_content(path: Path) -> tuple[str | None, bool]:
    """Return (HEAD content, ok) for a tracked file, or (None, False) if git fails."""
    try:
        cwd = str(path.parent)
        root_res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if root_res.returncode != 0:
            return None, False
        root = Path(root_res.stdout.strip())
        relative = path.relative_to(root).as_posix()
        head_res = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if head_res.returncode != 0:
            return None, False
        return head_res.stdout, True
    except Exception:
        return None, False


def _list_drives() -> list[dict[str, str]]:
    """List available drives (Windows) or root entries (Unix)."""
    import sys
    entries: list[dict[str, str]] = []
    if sys.platform == "win32":
        import string
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                entries.append({"name": f"{letter}:", "path": str(drive)})
    else:
        root = Path("/")
        try:
            for child in sorted(root.iterdir()):
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child)})
        except PermissionError:
            pass
    return entries


def _filter_dir_name(name: str) -> bool:
    """Return True if a directory name should be hidden from the browser."""
    from src.explorer.security import DENY_PATTERNS
    import fnmatch
    return any(fnmatch.fnmatch(name, pat) for pat in DENY_PATTERNS)


# ═══════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════

@router.get("/explorer/browse")
async def explorer_browse(path: str | None = None):
    """Browse directories and files for the folder picker.

    This endpoint intentionally allows browsing any directory so the user can
    navigate and pick a project root. Directories are selectable/enterable; files
    are shown as context (so the user can confirm the folder contents) but cannot
    be selected. Because the app is local-only (127.0.0.1), this is acceptable; if
    the service is ever exposed beyond localhost, this endpoint must be gated.
    """
    try:
        if not path:
            return {"success": True, "data": {
                "current": "",
                "parent": None,
                "directories": _list_drives(),
                "files": [],
            }}
        target = Path(path)
        real = target.resolve(strict=False)
        if not real.is_dir():
            return _error_response("not a directory", str(real), 400)
        dirs: list[dict[str, str]] = []
        files: list[dict[str, str]] = []
        try:
            for child in sorted(real.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir():
                    if _filter_dir_name(child.name):
                        continue
                    # Skip system dirs on Windows
                    low = str(child).replace("\\", "/").lower()
                    if any(s in low for s in ("system32/", "syswow64/", "$recycle.bin/", "boot/bootmgr")):
                        continue
                    dirs.append({"name": child.name, "path": str(child)})
                elif child.is_file():
                    files.append({"name": child.name, "path": str(child)})
        except PermissionError:
            pass
        parent = str(real.parent) if real.parent != real else None
        return {"success": True, "data": {
            "current": str(real),
            "parent": parent,
            "directories": dirs,
            "files": files,
        }}
    except Exception as exc:
        logger.exception("explorer_browse failed")
        return _error_response("browse failed", str(exc), 500)


@router.post("/explorer/open")
async def explorer_open(
    req: OpenRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Authorize and return the directory tree for a root path."""
    try:
        root = authorize_root(req.path, session_id)
        tree = await asyncio.to_thread(build_tree, root)
        return {"tree": [tree]}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except FileNotFoundError as exc:
        return _error_response("directory not found", str(exc), 404)
    except Exception as exc:
        logger.exception("explorer_open failed")
        return _error_response("open failed", str(exc), 500)


@router.get("/explorer/dir")
async def explorer_dir(
    path: str,
    session_id: str = Depends(require_explorer_session),
):
    """Return the immediate children of a directory for lazy loading."""
    try:
        real = validate_path(path, allow_file=False, session_id=session_id)
        if not real.is_dir():
            return _error_response("not a directory", str(real), 400)
        children = await asyncio.to_thread(build_tree_for_dir, real)
        return {"success": True, "data": {"children": children}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except Exception as exc:
        logger.exception("explorer_dir failed")
        return _error_response("dir load failed", str(exc), 500)


@router.get("/explorer/read")
async def explorer_read(
    path: str,
    session_id: str = Depends(require_explorer_session),
):
    """Read a file; return text content or binary metadata."""
    try:
        real = validate_path(path, allow_dir=False, session_id=session_id)
        if not real.is_file():
            return _error_response("not a file", str(real), 400)
        return _to_response(read_file_response(real))
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except Exception as exc:
        logger.exception("explorer_read failed")
        return _error_response("read failed", str(exc), 500)


@router.get("/explorer/diff")
async def explorer_diff(
    path: str,
    session_id: str = Depends(require_explorer_session),
):
    """Return a diff base (Git HEAD when available) and current file content."""
    try:
        real = validate_path(path, allow_dir=False, session_id=session_id)
        if not real.is_file():
            return _error_response("not a file", str(real), 400)
        read_result = read_file_response(real)
        if "_status" in read_result:
            return _to_response(read_result)
        if not read_result.get("is_text"):
            return _error_response("binary file", "diff not supported for binary files", 400)
        file_text = read_result.get("content", "")
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except Exception as exc:
        logger.exception("explorer_diff failed")
        return _error_response("diff failed", str(exc), 500)

    try:
        head_content, ok = await asyncio.to_thread(_git_head_content, real)
        if ok:
            return {
                "success": True,
                "data": {
                    "base_content": head_content,
                    "current_content": file_text,
                    "base": "git",
                    "has_changes": head_content != file_text,
                },
            }
    except Exception:
        logger.exception("git HEAD read failed")

    return {
        "success": True,
        "data": {
            "base_content": None,
            "current_content": file_text,
            "base": None,
            "has_changes": None,
        },
    }


@router.post("/explorer/write")
async def explorer_write(
    req: WriteRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Write text content to an authorized file."""
    try:
        real = validate_path(
            req.path, must_exist=False, allow_dir=False, session_id=session_id
        )
        version = write_text_file(real, req.content, req.expected_version)
        return {"success": True, "data": {"path": str(real), "version": version}}
    except FileVersionConflictError as exc:
        return _error_response("file changed on disk", str(exc), 409, "VERSION_CONFLICT")
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except Exception as exc:
        logger.exception("explorer_write failed")
        return _error_response("write failed", str(exc), 500)


@router.post("/explorer/create")
async def explorer_create(
    req: CreateRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Create a new file or directory under an authorized root."""
    try:
        real = _ensure_authorized(Path(req.path), session_id)
        create_node(real, req.type, session_id=session_id)
        return {"success": True, "data": {"path": str(real), "type": req.type}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except FileExistsError as exc:
        return _error_response("already exists", str(exc), 409)
    except Exception as exc:
        logger.exception("explorer_create failed")
        return _error_response("create failed", str(exc), 500)


@router.post("/explorer/rename")
async def explorer_rename(
    req: RenameRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Rename a file or directory within the same parent."""
    try:
        real = validate_path(req.path, session_id=session_id)
        new_path = rename_node(real, req.new_name, session_id=session_id)
        return {"success": True, "data": {"path": str(new_path)}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except FileExistsError as exc:
        return _error_response("target already exists", str(exc), 409)
    except Exception as exc:
        logger.exception("explorer_rename failed")
        return _error_response("rename failed", str(exc), 500)


@router.post("/explorer/delete")
async def explorer_delete(
    req: DeleteRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Move a file or directory into the authorized project's recycle area."""
    try:
        real = validate_path(req.path, session_id=session_id)
        root = authorized_root_for(real, session_id)
        trash_id = await asyncio.to_thread(move_to_trash, real, root)
        return {"success": True, "data": {"path": str(real), "trash_id": trash_id}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except ExplorerTrashError as exc:
        return _error_response("delete could not be moved to recycle area", str(exc), 400, "TRASH_ERROR")
    except FileExistsError as exc:
        return _error_response("recycle item already exists", str(exc), 409)
    except Exception as exc:
        logger.exception("explorer_delete failed")
        return _error_response("delete failed", str(exc), 500)


@router.get("/explorer/trash")
async def explorer_trash(
    root_path: str,
    session_id: str = Depends(require_explorer_session),
):
    """List recoverable deletes for an explicitly opened project root."""
    try:
        root = require_authorized_root(root_path, session_id)
        return {"success": True, "data": {"items": list_trash(root)}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except Exception as exc:
        logger.exception("explorer_trash failed")
        return _error_response("trash list failed", str(exc), 500)


@router.post("/explorer/restore")
async def explorer_restore(
    req: RestoreRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Restore a recycle item beneath its explicitly opened project root."""
    try:
        root = require_authorized_root(req.root_path, session_id)
        target = Path(req.target_path) if req.target_path else None
        if target is not None:
            target = validate_path(
                str(target), must_exist=False, session_id=session_id
            )
        metadata = await asyncio.to_thread(
            restore_from_trash,
            root,
            req.item_id,
            target,
            session_id=session_id,
        )
        return {
            "success": True,
            "data": {"path": metadata["restored_path"], "item_id": metadata["item_id"]},
        }
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except FileExistsError as exc:
        return _error_response("restore target already exists", str(exc), 409, "RESTORE_CONFLICT")
    except ExplorerTrashError as exc:
        return _error_response("restore failed", str(exc), 400, "TRASH_ERROR")
    except FileNotFoundError as exc:
        return _error_response("recycle item not found", str(exc), 404, "TRASH_ITEM_NOT_FOUND")
    except Exception as exc:
        logger.exception("explorer_restore failed")
        return _error_response("restore failed", str(exc), 500)


@router.post("/explorer/move")
async def explorer_move(
    req: MoveRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Move a file or directory into an authorized target directory."""
    try:
        src = validate_path(req.path, session_id=session_id)
        target_dir = validate_path(
            req.target_dir, allow_file=False, session_id=session_id
        )
        new_path = move_node(src, target_dir, session_id=session_id)
        return {"success": True, "data": {"path": str(new_path)}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except FileExistsError as exc:
        return _error_response("target already exists", str(exc), 409)
    except Exception as exc:
        logger.exception("explorer_move failed")
        return _error_response("move failed", str(exc), 500)


@router.post("/explorer/copy")
async def explorer_copy(
    req: CopyRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Copy a file or directory into an authorized target directory."""
    try:
        src = validate_path(req.path, session_id=session_id)
        target_dir = validate_path(
            req.target_dir, allow_file=False, session_id=session_id
        )
        new_path = copy_node(src, target_dir, session_id=session_id)
        return {"success": True, "data": {"path": str(new_path)}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except FileExistsError as exc:
        return _error_response("target already exists", str(exc), 409)
    except Exception as exc:
        logger.exception("explorer_copy failed")
        return _error_response("copy failed", str(exc), 500)


@router.post("/explorer/search")
async def explorer_search(
    req: SearchRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Search file contents under an authorized root path."""
    try:
        root = require_authorized_root(req.root_path, session_id)
        results = await asyncio.wait_for(
            asyncio.to_thread(
                search_content, root, req.query, req.max_results, req.include_pattern
            ),
            timeout=30.0,
        )
        return {"success": True, "data": {"results": results}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except asyncio.TimeoutError:
        return _error_response("search timeout", "exceeded 30 seconds", 504)
    except Exception as exc:
        logger.exception("explorer_search failed")
        return _error_response("search failed", str(exc), 500)


@router.post("/explorer/reveal")
async def explorer_reveal(
    req: RevealRequest,
    session_id: str = Depends(require_explorer_session),
):
    """Reveal a file in the OS file manager (Windows Explorer)."""
    import sys
    try:
        real = validate_path(req.path, session_id=session_id)
        if sys.platform == "win32":
            # Windows: explorer /select,"path"
            subprocess.Popen(["explorer", "/select,", str(real)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(real)])
        else:
            subprocess.Popen(["xdg-open", str(real.parent)])
        return {"success": True, "data": {"path": str(real)}}
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except Exception as exc:
        logger.exception("explorer_reveal failed")
        return _error_response("reveal failed", str(exc), 500)


# ═══════════════════════════════════════════
# SSE watch
# ═══════════════════════════════════════════

@router.get("/explorer/watch")
async def explorer_watch(
    path: str,
    request: Request,
    session_id: str = Depends(require_explorer_session),
):
    """SSE endpoint that pushes filesystem events for an authorized root."""
    try:
        # Watching requires an explicitly opened root; never re-authorize on
        # reconnect or after a backend restart.
        root = require_authorized_root(path, session_id)
    except ExplorerSecurityError as exc:
        return _error_response("security check failed", str(exc), 403, "FORBIDDEN")
    except Exception as exc:
        logger.exception("explorer_watch validation failed")
        return _error_response("watch failed", str(exc), 500)

    manager = get_watch_manager()
    try:
        queue_id, queue = manager.subscribe(root)
    except RuntimeError as exc:
        return _error_response("watch unavailable", str(exc), 503)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event: WatchEvent = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield sse_event("heartbeat", {})
                    continue
                yield sse_event(event.get("type", "change"), event)
        finally:
            manager.unsubscribe(root, queue_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
