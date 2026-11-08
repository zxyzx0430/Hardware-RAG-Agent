"""Run status, event replay, cancellation, and restart recovery endpoints."""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictStr

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

_ACTIVE_STATUSES = tuple(sorted(run_events.ACTIVE_RUN_STATUSES))


class RunResumeRequest(BaseModel):
    """Controlled resume of an interrupted run after a restart.

    ``payload`` must be the same request configuration the paused run was
    created with; the server re-validates it against the persisted snapshot
    and the one-shot pending approval before touching the checkpoint.
    """

    payload: dict[str, Any]
    decision: Literal["allow", "deny", "stop"]
    call_id: StrictStr | None = Field(default=None, min_length=1, max_length=256)


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


def _public_run(
    store: run_events.RunStore,
    run: dict[str, Any],
    *,
    include_resume: bool = False,
) -> dict[str, Any]:
    public = {field: run.get(field) for field in _RUN_PUBLIC_FIELDS}
    approvals = _live_pending_approvals(store, run["id"])
    public["pending_approvals"] = [_public_approval(row) for row in approvals]
    if include_resume:
        public["resume"] = classify_run_resume(store, run)
    return public


def _approval_expired(row: dict[str, Any], now: _dt.datetime | None = None) -> bool:
    return run_events.approval_is_expired(row, now)


def _live_pending_approvals(
    store: run_events.RunStore, run_id: str,
) -> list[dict[str, Any]]:
    """Pending rows that have not aged out; stale cards are never actionable."""
    rows = store.list_pending_approvals(
        run_id, status=run_events.APPROVAL_STATUS_PENDING,
    )
    return [row for row in rows if not _approval_expired(row)]


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


def classify_run_resume(
    store: run_events.RunStore, run: dict[str, Any],
) -> dict[str, Any]:
    """Classify whether an interrupted run may be resumed safely.

    Safe means: the ledger only has tool calls covered by a still-pending,
    unexpired approval, and the sanitized request snapshot needed to rebuild
    the paused request survived the restart. Anything else is ``needs_review``
    and must never be replayed automatically.
    """
    status = run.get("status")
    if status != run_events.RUN_STATUS_INTERRUPTED:
        return {
            "mode": "none",
            "reason": None,
            "pending_approval_count": 0,
            "request_snapshot_available": False,
            "unresolved_tool_calls": [],
        }
    events = store.list_run_events(run["id"])
    summary = run_events.summarize_run_events(events)
    pending = _live_pending_approvals(store, run["id"])
    pending_calls = {
        row.get("call_id") for row in pending if isinstance(row.get("call_id"), str)
    }
    open_calls = summary["open_tool_calls"]
    unknown_calls = [
        call for call in open_calls
        if not isinstance(call.get("call_id"), str)
        or call.get("call_id") not in pending_calls
    ]
    snapshot = store.get_latest_request_snapshot(run["id"])
    info: dict[str, Any] = {
        "pending_approval_count": len(pending),
        "request_snapshot_available": snapshot is not None,
        "unresolved_tool_calls": open_calls,
    }
    if unknown_calls:
        info.update(
            mode="needs_review",
            reason="部分工具调用没有结果，副作用状态未知，禁止自动重放。",
        )
    elif pending and snapshot is not None:
        info.update(
            mode="hitl_approval",
            reason="存在持久化审批与请求快照，可提交确认后受控恢复。",
            resume_endpoint="/api/agent-sandbox/resume",
        )
    elif pending:
        info.update(
            mode="needs_review",
            reason="审批仍存在，但请求快照不可用，无法安全恢复。",
        )
    else:
        info.update(
            mode="needs_review",
            reason="没有可安全恢复的断点，禁止自动重放。",
        )
    return info


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
        "run": _public_run(store, run, include_resume=True),
    }


@router.get("/sessions/{session_id}/runs/active")
async def get_active_runs(
    session_id: str,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Frontend contract: find the run to reconnect to after a refresh."""
    store = _run_store()
    runs = store.list_runs(
        session_id=session_id, statuses=_ACTIVE_STATUSES, limit=50,
    )
    public = [_public_run(store, run) for run in runs]
    return {
        "success": True,
        "session_id": session_id,
        "runs": public,
        "active_run": public[0] if public else None,
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

    stopped: list[dict[str, Any]] = []
    try:
        stopped = store.consume_pending_approvals(
            run_id, call_id=None, decision="stop",
        )
    except Exception:
        logger.warning("Pending approvals could not be stopped run=%s", run_id, exc_info=True)
    run_events.append_approval_decisions(store, run_id, stopped, decision="stop")

    control = run_events.get_run_control()
    cancelled = control.cancel(run_id)
    if not cancelled:
        run_events.append_terminal_events(store, run_id, cancelled=True)
        store.update_run_status(
            run_id,
            run_events.RUN_STATUS_CANCELLED,
            error_code="CANCELLED",
            error_message="cancel requested",
        )
        control.clear_cancel_requested(run_id)
        run_events.get_run_bus().close(run_id)
    return {"success": True, "run_id": run_id, "cancelled": True}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    request: Request,
    body: RunResumeRequest | None = Body(default=None),
    user: dict = Depends(current_user),
):
    """Controlled resume entry for an interrupted run.

    Without a body this endpoint only reports what is needed. With the
    original request payload and an explicit decision it delegates to the
    single HITL resume implementation, which re-validates the persisted
    request snapshot and consumes the pending approval exactly once.
    """
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

    resume_info = classify_run_resume(store, run)
    if resume_info["mode"] == "needs_review":
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "RESUME_NEEDS_REVIEW",
                    "message": resume_info["reason"],
                },
                "resume": resume_info,
            },
        )
    if body is None:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "RESUME_REQUIRES_DECISION",
                    "message": "受控恢复需要重新提交原请求和审批决定。",
                },
                "resume": resume_info,
            },
        )
    if run_events.get_run_control().is_resuming(run_id):
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "RUN_RESUME_IN_PROGRESS",
                    "message": "该运行正在恢复中，请勿重复提交。",
                },
            },
        )

    # Imported lazily: chat_routes includes this router, so a module-level
    # import would be circular.
    from app.api.chat_routes import ChatRequest, ResumeRequest, resume_agent

    resume_payload = dict(body.payload)
    # A dumped ChatRequest carries long_term_memory=None, which the request
    # model rejects on input; omit it so the saved setting/memory is rebuilt.
    if resume_payload.get("long_term_memory") is None:
        resume_payload.pop("long_term_memory", None)
    try:
        payload = ChatRequest(**resume_payload)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": {
                    "code": "INVALID_RESUME_PAYLOAD",
                    "message": "恢复请求的原始 payload 无效。",
                },
            },
        ) from exc
    resume_req = ResumeRequest(
        payload=payload,
        decision=body.decision,
        call_id=body.call_id,
        run_id=run_id,
    )
    return await resume_agent(resume_req, request, user)


async def recover_interrupted_runs() -> None:
    """Startup hook: any run still active belongs to a dead process.

    Besides flipping the status, this writes the terminal pair into the same
    event ledger every client reads, so replay never has to guess. The hook is
    idempotent: a run whose ledger already ends in ``done`` only gets its
    status repaired, never a second terminal event.
    """
    try:
        store = run_events.get_run_store()
    except Exception:
        logger.warning("Run restart recovery skipped: store unavailable", exc_info=True)
        return

    try:
        active = store.list_runs(statuses=_ACTIVE_STATUSES, limit=1000)
    except Exception:
        logger.warning("Run restart recovery could not list active runs", exc_info=True)
        active = []

    try:
        changed = store.mark_interrupted_runs(
            error_code=run_events.INTERRUPTED_ERROR_CODE,
            error_message="service restarted while the run was active",
        )
    except Exception:
        logger.warning("Run restart recovery failed", exc_info=True)
        return

    repaired = 0
    terminal_recovered = 0
    for run in active:
        run_id = run.get("id")
        if not isinstance(run_id, str):
            continue
        try:
            events = store.list_run_events(run_id)
        except Exception:
            logger.warning("Run events could not be read run=%s", run_id, exc_info=True)
            continue
        last = events[-1] if events else None
        if last is not None and last.get("type") == run_events.DONE_EVENT:
            payload = last.get("payload") if isinstance(last.get("payload"), dict) else {}
            if payload.get("awaiting_confirmation"):
                # A pause is not a completed run; keep the interrupted status.
                continue
            if payload.get("success") is True:
                status = run_events.RUN_STATUS_COMPLETED
                error_code = None
            elif payload.get("cancelled"):
                status = run_events.RUN_STATUS_CANCELLED
                error_code = run_events.CANCELLED_EVENT_CODE
            else:
                status = run_events.RUN_STATUS_FAILED
                error_code = "RUN_FAILED"
            try:
                store.claim_run_status(
                    run_id,
                    expected_status=run_events.RUN_STATUS_INTERRUPTED,
                    status=status,
                    error_code=error_code,
                )
                repaired += 1
            except Exception:
                logger.warning(
                    "Run terminal status repair failed run=%s", run_id, exc_info=True,
                )
            continue
        if run_events.append_terminal_events(store, run_id, interrupted=True):
            terminal_recovered += 1

    try:
        expired = store.expire_pending_approvals()
    except Exception:
        expired = 0
        logger.warning("Expired approval sweep failed", exc_info=True)

    if changed or repaired or terminal_recovered or expired:
        logger.warning(
            "Run restart recovery: interrupted=%d repaired=%d terminal=%d expired=%d",
            changed, repaired, terminal_recovered, expired,
        )
