"""Run status, event replay, cancellation, and restart recovery endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import current_user
from src.agent import run_events

logger = logging.getLogger(__name__)

router = APIRouter()

_RUN_PUBLIC_FIELDS = (
    "id",
    "session_id",
    "status",
    "model",
    "permission_mode",
    "thread_id",
    "checkpoint_id",
    "started_at",
    "finished_at",
    "last_event_seq",
    "error_code",
    "error_message",
)


def _run_store() -> run_events.RunStore:
    try:
        return run_events.get_run_store()
    except Exception as exc:
        logger.error("Run store is unavailable", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": "RUN_STORE_UNAVAILABLE",
                    "message": "运行记录存储不可用，请稍后重试。",
                },
            },
        ) from exc


def _get_run_or_404(store: run_events.RunStore, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "success": False,
                "error": {"code": "RUN_NOT_FOUND", "message": "运行记录不存在。"},
            },
        )
    return run


def _public_run(store: run_events.RunStore, run: dict[str, Any]) -> dict[str, Any]:
    public = {field: run.get(field) for field in _RUN_PUBLIC_FIELDS}
    approvals = store.list_pending_approvals(
        run["id"], status=run_events.APPROVAL_STATUS_PENDING,
    )
    public["pending_approvals"] = [_public_approval(row) for row in approvals]
    return public


def _public_approval(row: dict[str, Any]) -> dict[str, Any]:
    raw_args = row.get("args_json")
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}
    else:
        args = raw_args if isinstance(raw_args, dict) else {}
    return {
        "id": row.get("id"),
        "run_id": row.get("run_id"),
        "call_id": row.get("call_id"),
        "tool_name": row.get("tool_name"),
        "args": args,
        "risk_level": row.get("risk_level"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires_at"),
        "decided_at": row.get("decided_at"),
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = _run_store()
    run = _get_run_or_404(store, run_id)
    return {
        "success": True,
        "status": run.get("status"),
        "run": _public_run(store, run),
    }


@router.get("/runs/{run_id}/events")
async def replay_run_events(
    run_id: str,
    request: Request,
    after: int | None = Query(default=None, ge=0),
    user: dict = Depends(current_user),
) -> StreamingResponse:
    store = _run_store()
    _get_run_or_404(store, run_id)

    after_seq = after or 0
    last_event_id = request.headers.get("last-event-id")
    if last_event_id is not None:
        parsed = run_events.parse_last_event_id(last_event_id)
        if parsed is None or parsed[0] != run_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "success": False,
                    "error": {
                        "code": "INVALID_LAST_EVENT_ID",
                        "message": "Last-Event-ID 与本运行不匹配。",
                    },
                },
            )
        after_seq = max(after_seq, parsed[1])

    return StreamingResponse(
        run_events.iterate_run_events(run_id, after_seq, store=store),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(
    run_id: str,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = _run_store()
    run = _get_run_or_404(store, run_id)
    if run.get("status") not in run_events.ACTIVE_RUN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "RUN_NOT_ACTIVE",
                    "message": "该运行已结束，无法取消。",
                    "status": run.get("status"),
                },
            },
        )

    try:
        store.consume_pending_approvals(
            run_id, call_id=None, decision="stop",
        )
    except Exception:
        logger.warning("Pending approvals could not be stopped run=%s", run_id, exc_info=True)

    cancelled = run_events.get_run_control().cancel(run_id)
    if not cancelled:
        store.update_run_status(
            run_id,
            run_events.RUN_STATUS_CANCELLED,
            error_code="CANCELLED",
            error_message="cancel requested",
        )
        run_events.get_run_bus().close(run_id)
    return {"success": True, "run_id": run_id, "cancelled": True}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    store = _run_store()
    run = _get_run_or_404(store, run_id)
    if run.get("status") != run_events.RUN_STATUS_INTERRUPTED:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "RUN_NOT_INTERRUPTED",
                    "message": "只有被中断的运行才需要恢复。",
                    "status": run.get("status"),
                },
            },
        )
    # R1 deliberately never auto-replays a run: pending write/execute/hardware
    # calls may have unknown outcomes after a restart.
    raise HTTPException(
        status_code=409,
        detail={
            "success": False,
            "error": {
                "code": "RESUME_UNSUPPORTED",
                "message": "当前版本不自动恢复中断的运行；请重新发送请求或重试待确认操作。",
            },
        },
    )


async def recover_interrupted_runs() -> None:
    """Startup hook: any run still active belongs to a dead process."""
    try:
        store = run_events.get_run_store()
        changed = store.mark_interrupted_runs(
            error_code=run_events.INTERRUPTED_ERROR_CODE,
            error_message="service restarted while the run was active",
        )
    except Exception:
        logger.warning("Run restart recovery failed", exc_info=True)
        return
    if changed:
        logger.warning("Marked %d active run(s) as interrupted after restart", changed)
