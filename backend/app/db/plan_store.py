"""SQLite storage for versioned, session-scoped plans.

A plan version is immutable once written. Approve/reject use a conditional
UPDATE that also requires the row to be the session's newest version, so a
stale approval cannot win a race. A new version supersedes older pending or
approved rows that have not started executing; an executed plan stays
approved for audit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db import database
from app.db.models import Plan
from app.db.time_utils import to_utc_iso_z, utc_now

logger = logging.getLogger(__name__)

PLAN_FILE_DIR = Path(database.DB_PATH).parent / "plans"
_PLAN_FILE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")

MAX_PLAN_STEPS = 12
MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 2000
MAX_STEP_TITLE_CHARS = 200
MAX_STEP_DETAIL_CHARS = 1000
MAX_STEP_VERIFICATION_CHARS = 500
MAX_LIST_ITEMS = 10
MAX_LIST_ITEM_CHARS = 500
MAX_PLAN_JSON_BYTES = 32 * 1024
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_APPROVED)

# Derived execution state exposed alongside the plan. It is computed from the
# plan row and its execution run so no schema migration is required; these
# values are additive and optional for older clients.
EXECUTION_NONE = "none"
EXECUTION_STARTED = "started"
EXECUTION_COMPLETED = "completed"
EXECUTION_REJECTED = "rejected"
EXECUTION_FAILED = "failed"


class PlanValidationError(ValueError):
    """Plan payload is outside the accepted bounds."""


class PlanStateError(ValueError):
    """Plan is missing or not in a state that allows the requested action."""


class PlanConflictError(RuntimeError):
    """A concurrent version allocation could not be resolved."""


def _clean_text(value: Any, field: str, max_chars: int, required: bool = True) -> str:
    text_value = str(value or "").strip()
    if required and not text_value:
        raise PlanValidationError(f"{field} is required")
    if len(text_value) > max_chars:
        raise PlanValidationError(f"{field} exceeds {max_chars} characters")
    return text_value


def _clean_steps(raw_steps: Any) -> list[dict[str, str]]:
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_PLAN_STEPS:
        raise PlanValidationError(f"steps must contain 1-{MAX_PLAN_STEPS} items")
    return [_clean_step(item) for item in raw_steps]


def _clean_step(item: Any) -> dict[str, str]:
    if not isinstance(item, dict):
        raise PlanValidationError("each step must be an object")
    return {
        "title": _clean_text(item.get("title"), "step.title", MAX_STEP_TITLE_CHARS),
        "detail": _clean_text(item.get("detail"), "step.detail", MAX_STEP_DETAIL_CHARS, required=False),
        "verification": _clean_text(
            item.get("verification"), "step.verification", MAX_STEP_VERIFICATION_CHARS, required=False,
        ),
    }


def _clean_string_list(raw_items: Any, field: str) -> list[str]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list) or len(raw_items) > MAX_LIST_ITEMS:
        raise PlanValidationError(f"{field} must be a list of at most {MAX_LIST_ITEMS} items")
    return [_clean_text(item, field, MAX_LIST_ITEM_CHARS) for item in raw_items]


def normalize_plan_payload(payload: Any) -> dict[str, Any]:
    """Validate and normalize a plan payload; compute its content fingerprint."""
    if not isinstance(payload, dict):
        raise PlanValidationError("plan payload must be an object")
    normalized = {
        "title": _clean_text(payload.get("title"), "title", MAX_TITLE_CHARS),
        "summary": _clean_text(payload.get("summary"), "summary", MAX_SUMMARY_CHARS),
        "steps": _clean_steps(payload.get("steps")),
        "risks": _clean_string_list(payload.get("risks"), "risks"),
        "open_questions": _clean_string_list(payload.get("open_questions"), "open_questions"),
    }
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(canonical.encode("utf-8")) > MAX_PLAN_JSON_BYTES:
        raise PlanValidationError(f"plan exceeds {MAX_PLAN_JSON_BYTES} bytes")
    normalized["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return normalized


def _has_blocked_approval(store: Any, run_id: str) -> bool:
    """True when a tool call in this run was denied or stopped by the user."""
    for status in ("denied", "stopped"):
        try:
            if store.list_pending_approvals(run_id, status=status):
                return True
        except Exception:
            logger.warning(
                "plan execution approval lookup failed run=%s status=%s",
                run_id, status, exc_info=True,
            )
    return False


def _execution_state(row: Plan) -> tuple[str, str | None]:
    """Derive (execution_state, execution_note) from the plan and its run.

    Never raises: plan reads must keep working when run recovery storage is
    unavailable, so an unreachable run degrades to ``started`` with a note.
    """
    if row.status == STATUS_REJECTED:
        return EXECUTION_REJECTED, "计划未获批准，未执行"
    run_id = row.executed_run_id
    if not run_id:
        return EXECUTION_NONE, None
    try:
        from app.db import run_store as run_store_module

        store = run_store_module.RunStore()
        run = store.get_run(run_id)
        if run is None:
            return EXECUTION_STARTED, "执行记录不可用"
        if run.status == run_store_module.RUN_STATUS_AWAITING_CONFIRMATION:
            return EXECUTION_STARTED, "等待工具调用确认"
        if run.status == run_store_module.RUN_STATUS_RUNNING:
            return EXECUTION_STARTED, None
        if run.status == run_store_module.RUN_STATUS_COMPLETED:
            if _has_blocked_approval(store, run_id):
                return EXECUTION_REJECTED, "执行中部分工具调用被拒绝，计划未完整执行"
            return EXECUTION_COMPLETED, None
        if run.status == run_store_module.RUN_STATUS_FAILED:
            return EXECUTION_FAILED, run.error_message or "执行失败"
        if run.status == run_store_module.RUN_STATUS_CANCELLED:
            return EXECUTION_FAILED, "执行已取消"
        if run.status == run_store_module.RUN_STATUS_INTERRUPTED:
            return EXECUTION_FAILED, "服务重启，执行被中断"
        return EXECUTION_STARTED, None
    except Exception:
        logger.warning(
            "plan execution state unavailable plan=%s run=%s", row.id, run_id,
            exc_info=True,
        )
        return EXECUTION_STARTED, None


def _plan_file_path(session_id: str, version: int) -> str:
    safe_session = _PLAN_FILE_NAME.sub("_", session_id)[:80] or "session"
    return str(PLAN_FILE_DIR / safe_session / f"plan-v{version}.md")


def _render_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        f"# {plan['title']}",
        "",
        f"- Version: v{plan['version']}",
        f"- Status: {plan['status']}",
        f"- Created: {plan.get('created_at') or ''}",
        "",
        "## Summary",
        plan.get("summary") or "",
        "",
        "## Steps",
    ]
    for index, step in enumerate(plan.get("steps") or [], start=1):
        lines.append(f"{index}. **{step.get('title', '')}**")
        detail = str(step.get("detail") or "").strip()
        if detail:
            lines.append(f"   {detail}")
        verification = str(step.get("verification") or "").strip()
        if verification:
            lines.append(f"   Verification: {verification}")
    for heading, key in (("Risks", "risks"), ("Open questions", "open_questions")):
        values = plan.get(key) or []
        if values:
            lines.extend(["", f"## {heading}"])
            lines.extend(f"- {value}" for value in values)
    return "\n".join(lines).rstrip() + "\n"


def _write_plan_file(plan: dict[str, Any]) -> None:
    """Best-effort durable Markdown copy; the database stays authoritative."""
    try:
        path = Path(plan["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = _render_plan_markdown(plan)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
        latest = path.parent / "latest.md"
        latest_tmp = latest.with_suffix(".md.tmp")
        latest_tmp.write_text(content, encoding="utf-8")
        latest_tmp.replace(latest)
    except (OSError, KeyError, TypeError, ValueError):
        logger.warning(
            "plan markdown write failed plan=%s", plan.get("id"), exc_info=True,
        )


def _plan_dict(row: Plan) -> dict[str, Any]:
    execution_state, execution_note = _execution_state(row)
    return {
        "id": row.id,
        "session_id": row.session_id,
        "version": row.version,
        "status": row.status,
        "title": row.title,
        "summary": row.summary,
        "steps": _load_json(row.steps_json, []),
        "risks": _load_json(row.risks_json, []),
        "open_questions": _load_json(row.open_questions_json, []),
        "run_id": row.run_id,
        "executed_run_id": row.executed_run_id,
        "execution_state": execution_state,
        "execution_note": execution_note,
        "file_path": _plan_file_path(row.session_id, row.version),
        "created_at": to_utc_iso_z(row.created_at),
        "decided_at": to_utc_iso_z(row.decided_at) or None,
    }


def _load_json(raw: Any, default: Any) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _plan_meta(row: Plan) -> dict[str, Any]:
    execution_state, execution_note = _execution_state(row)
    return {
        "id": row.id,
        "session_id": row.session_id,
        "version": row.version,
        "status": row.status,
        "title": row.title,
        "file_path": _plan_file_path(row.session_id, row.version),
        "created_at": to_utc_iso_z(row.created_at),
        "decided_at": to_utc_iso_z(row.decided_at) or None,
        "executed_run_id": row.executed_run_id,
        "execution_state": execution_state,
        "execution_note": execution_note,
    }


def _begin_write(session) -> None:
    """Start SQLite's write lock before reading the version counter."""
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _same_run_content(session, session_id: str, run_id: str | None, digest: str) -> Plan | None:
    if not run_id:
        return None
    return session.execute(
        select(Plan).where(
            Plan.session_id == session_id,
            Plan.run_id == run_id,
            Plan.content_sha256 == digest,
            Plan.status == STATUS_PENDING,
        )
    ).scalars().first()


def _supersede_unexecuted(session, session_id: str, now) -> None:
    session.execute(
        update(Plan).where(
            Plan.session_id == session_id,
            Plan.status.in_(ACTIVE_STATUSES),
            Plan.executed_run_id.is_(None),
        ).values(status=STATUS_SUPERSEDED, decided_at=now)
    )


def _next_version(session, session_id: str) -> int:
    current = session.execute(
        select(func.max(Plan.version)).where(Plan.session_id == session_id)
    ).scalar_one_or_none()
    return int(current or 0) + 1


def _new_plan_row(session_id: str, version: int, normalized: dict[str, Any], run_id: str | None, now) -> Plan:
    return Plan(
        id=f"pl_{uuid.uuid4().hex}",
        session_id=session_id,
        version=version,
        status=STATUS_PENDING,
        title=normalized["title"],
        summary=normalized["summary"],
        steps_json=json.dumps(normalized["steps"], ensure_ascii=False),
        risks_json=json.dumps(normalized["risks"], ensure_ascii=False),
        open_questions_json=json.dumps(normalized["open_questions"], ensure_ascii=False),
        run_id=run_id,
        content_sha256=normalized["content_sha256"],
        created_at=now,
    )


def create_version(session_id: str, payload: Any, run_id: str | None = None) -> dict[str, Any]:
    """Create the next plan version, superseding unexecuted older versions."""
    normalized = normalize_plan_payload(payload)
    now = utc_now()
    for _ in range(2):
        try:
            return _create_once(session_id, normalized, run_id, now)
        except IntegrityError:
            logger.warning("plan version race session=%s; retrying", session_id)
    raise PlanConflictError("plan version allocation conflicted")


def _create_once(session_id: str, normalized: dict[str, Any], run_id: str | None, now) -> dict[str, Any]:
    with database.SessionLocal() as session:
        _begin_write(session)
        existing = _same_run_content(session, session_id, run_id, normalized["content_sha256"])
        if existing is not None:
            return _plan_dict(existing)
        _supersede_unexecuted(session, session_id, now)
        row = _new_plan_row(session_id, _next_version(session, session_id), normalized, run_id, now)
        session.add(row)
        session.commit()
        plan = _plan_dict(row)
        _write_plan_file(plan)
        return plan


def get(plan_id: str) -> dict[str, Any] | None:
    with database.SessionLocal() as session:
        row = session.get(Plan, plan_id)
        return _plan_dict(row) if row is not None else None


def latest(session_id: str) -> dict[str, Any] | None:
    with database.SessionLocal() as session:
        row = session.execute(
            select(Plan).where(Plan.session_id == session_id).order_by(Plan.version.desc())
        ).scalars().first()
        return _plan_dict(row) if row is not None else None


def list_metadata(session_id: str, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), MAX_LIST_LIMIT))
    with database.SessionLocal() as session:
        rows = session.execute(
            select(Plan).where(Plan.session_id == session_id)
            .order_by(Plan.version.desc()).limit(safe_limit)
        ).scalars().all()
        return [_plan_meta(row) for row in rows]


def _decide(session_id: str, plan_id: str, status: str) -> dict[str, Any]:
    now = utc_now()
    newer = select(func.max(Plan.version)).where(Plan.session_id == session_id).scalar_subquery()
    with database.SessionLocal() as session:
        result = session.execute(
            update(Plan).where(
                Plan.id == plan_id,
                Plan.session_id == session_id,
                Plan.status == STATUS_PENDING,
                Plan.version == newer,
            ).values(status=status, decided_at=now)
        )
        session.commit()
        if result.rowcount != 1:
            raise PlanStateError("plan is not the latest pending version")
    plan = get(plan_id)
    if plan is None:
        raise PlanStateError("plan not found")
    _write_plan_file(plan)
    return plan


def approve(session_id: str, plan_id: str) -> dict[str, Any]:
    return _decide(session_id, plan_id, STATUS_APPROVED)


def reject(session_id: str, plan_id: str) -> dict[str, Any]:
    return _decide(session_id, plan_id, STATUS_REJECTED)


def mark_executed(plan_id: str, run_id: str) -> bool:
    """Claim first execution; True only for the request that consumed it."""
    with database.SessionLocal() as session:
        result = session.execute(
            update(Plan).where(
                Plan.id == plan_id,
                Plan.status == STATUS_APPROVED,
                Plan.executed_run_id.is_(None),
            ).values(executed_run_id=run_id)
        )
        session.commit()
        return result.rowcount == 1


def delete_session(session_id: str) -> int:
    with database.SessionLocal() as session:
        result = session.execute(delete(Plan).where(Plan.session_id == session_id))
        session.commit()
        return int(result.rowcount or 0)
