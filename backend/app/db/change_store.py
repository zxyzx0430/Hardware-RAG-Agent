"""Database access for P2 ChangeSets.

Small, synchronous repository helpers. Tool execution records facts through
this module; HTTP routes read the same rows. Events are append-only.
"""
from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.db import database
from app.db.change_models import ChangeEvent, ChangeItem, ChangeSet


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _iso(value: datetime.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _item_dict(row: ChangeItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "change_set_id": row.change_set_id,
        "call_id": row.call_id,
        "tool_name": row.tool_name,
        "operation": row.operation,
        "path": row.path,
        "before_sha256": row.before_sha256,
        "after_sha256": row.after_sha256,
        "before_blob_ref": row.before_blob_ref,
        "after_blob_ref": row.after_blob_ref,
        "diff_stats": json.loads(row.diff_stats_json or "{}"),
        "risk": row.risk,
        "status": row.status,
        "review_status": row.review_status,
        "review_note": row.review_note,
        "decided_at": _iso(row.decided_at),
        "plan_step_id": row.plan_step_id,
        "plan_step_index": row.plan_step_index,
        "created_at": _iso(row.created_at),
    }


def _set_dict(row: ChangeSet, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "run_id": row.run_id,
        "plan_id": row.plan_id,
        "plan_version": row.plan_version,
        "workspace_root": row.workspace_root,
        "status": row.status,
        "base_ref": row.base_ref,
        "base_sha": row.base_sha,
        "summary": json.loads(row.summary_json or "{}"),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        **({"items": items} if items is not None else {}),
    }


def get_or_create_change_set(
    *,
    session_id: str,
    run_id: str,
    workspace_root: str,
    base_ref: str | None = None,
    base_sha: str | None = None,
    plan_id: str | None = None,
    plan_version: int | None = None,
) -> dict[str, Any]:
    with database.SessionLocal() as db:
        existing = db.query(ChangeSet).filter(ChangeSet.run_id == run_id).first()
        if existing is not None:
            return _set_dict(existing)
        row = ChangeSet(
            id=_new_id("cs"),
            session_id=session_id,
            run_id=run_id,
            workspace_root=workspace_root,
            base_ref=base_ref,
            base_sha=base_sha,
            plan_id=plan_id,
            plan_version=plan_version,
            status="open",
            summary_json="{}",
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
            return _set_dict(row)
        except IntegrityError:
            db.rollback()
            existing = db.query(ChangeSet).filter(ChangeSet.run_id == run_id).first()
            if existing is None:
                raise
            return _set_dict(existing)


def add_change_item(**fields: Any) -> dict[str, Any]:
    with database.SessionLocal() as db:
        row = ChangeItem(
            id=_new_id("ci"),
            created_at=_now(),
            **fields,
        )
        db.add(row)
        parent = db.query(ChangeSet).filter(ChangeSet.id == row.change_set_id).first()
        if parent is not None:
            parent.updated_at = _now()
        db.commit()
        db.refresh(row)
        return _item_dict(row)


def record_event(
    *,
    change_set_id: str,
    change_item_id: str | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    with database.SessionLocal() as db:
        db.add(ChangeEvent(
            id=_new_id("ce"),
            change_set_id=change_set_id,
            change_item_id=change_item_id,
            event_type=event_type,
            payload_json=_json(payload),
            created_at=_now(),
        ))
        db.commit()


def prune_change_data(*, older_than_days: int = 30) -> dict[str, int]:
    """Delete old ChangeSets and their review rows.

    This is a manual retention helper. It intentionally does not run at startup,
    because deleting review history must remain an explicit maintenance action.
    Content-addressed blobs are not deleted here; they require a separate
    reference-aware sweep.
    """
    from datetime import timedelta

    cutoff = _now() - timedelta(days=max(1, int(older_than_days)))
    with database.SessionLocal() as db:
        rows = db.query(ChangeSet).filter(ChangeSet.created_at < cutoff).all()
        set_ids = [row.id for row in rows]
        if not set_ids:
            return {"change_sets": 0, "change_items": 0, "change_events": 0}

        item_count = db.query(ChangeItem).filter(ChangeItem.change_set_id.in_(set_ids)).count()
        event_count = db.query(ChangeEvent).filter(ChangeEvent.change_set_id.in_(set_ids)).count()
        db.query(ChangeItem).filter(ChangeItem.change_set_id.in_(set_ids)).delete(synchronize_session=False)
        db.query(ChangeEvent).filter(ChangeEvent.change_set_id.in_(set_ids)).delete(synchronize_session=False)
        db.query(ChangeSet).filter(ChangeSet.id.in_(set_ids)).delete(synchronize_session=False)
        db.commit()
        return {
            "change_sets": len(set_ids),
            "change_items": item_count,
            "change_events": event_count,
        }


def list_change_sets(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with database.SessionLocal() as db:
        rows = (
            db.query(ChangeSet)
            .filter(ChangeSet.session_id == session_id)
            .order_by(ChangeSet.created_at.desc())
            .limit(max(1, min(limit, 50)))
            .all()
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            items = (
                db.query(ChangeItem)
                .filter(ChangeItem.change_set_id == row.id)
                .order_by(ChangeItem.created_at.asc())
                .all()
            )
            result.append(_set_dict(row, [_item_dict(item) for item in items]))
        return result


def get_change_set(change_set_id: str) -> dict[str, Any] | None:
    with database.SessionLocal() as db:
        row = db.query(ChangeSet).filter(ChangeSet.id == change_set_id).first()
        if row is None:
            return None
        items = (
            db.query(ChangeItem)
            .filter(ChangeItem.change_set_id == row.id)
            .order_by(ChangeItem.created_at.asc())
            .all()
        )
        return _set_dict(row, [_item_dict(item) for item in items])


def get_change_item(change_item_id: str) -> dict[str, Any] | None:
    with database.SessionLocal() as db:
        row = db.query(ChangeItem).filter(ChangeItem.id == change_item_id).first()
        return _item_dict(row) if row is not None else None


def get_change_set_by_run(run_id: str) -> dict[str, Any] | None:
    with database.SessionLocal() as db:
        row = db.query(ChangeSet).filter(ChangeSet.run_id == run_id).first()
        if row is None:
            return None
        items = (
            db.query(ChangeItem)
            .filter(ChangeItem.change_set_id == row.id)
            .order_by(ChangeItem.created_at.asc())
            .all()
        )
        return _set_dict(row, [_item_dict(item) for item in items])


def resolve_run_plan(run_id: str) -> tuple[str, int] | None:
    """Resolve the approved plan frozen into a run, if any."""
    try:
        from app.db.models import Plan
        from app.db.recovery_models import Run
    except Exception:
        return None
    with database.SessionLocal() as db:
        run = db.query(Run).filter(Run.id == run_id).first()
        if run is None or not run.config_json:
            return None
        try:
            config = json.loads(run.config_json)
        except (TypeError, ValueError):
            return None
        plan_id = config.get("approved_plan_id") if isinstance(config, dict) else None
        if not isinstance(plan_id, str) or not plan_id:
            return None
        plan = db.query(Plan).filter(Plan.id == plan_id).first()
        if plan is None:
            return None
        return plan.id, int(plan.version)


def resolve_run_plan_step_count(run_id: str) -> int | None:
    """Return the number of steps in the run's frozen approved plan."""
    try:
        from app.db.models import Plan
        from app.db.recovery_models import Run
    except Exception:
        return None
    with database.SessionLocal() as db:
        run = db.query(Run).filter(Run.id == run_id).first()
        if run is None or not run.config_json:
            return None
        try:
            config = json.loads(run.config_json)
            plan_id = config.get("approved_plan_id") if isinstance(config, dict) else None
            if not isinstance(plan_id, str) or not plan_id:
                return None
            plan = db.query(Plan).filter(Plan.id == plan_id).first()
            if plan is None:
                return None
            steps = json.loads(plan.steps_json or "[]")
            return len(steps) if isinstance(steps, list) else None
        except (TypeError, ValueError):
            return None


def set_change_set_status(change_set_id: str, status: str) -> None:
    with database.SessionLocal() as db:
        row = db.query(ChangeSet).filter(ChangeSet.id == change_set_id).first()
        if row is None:
            return
        row.status = status
        row.updated_at = _now()
        db.commit()


def recompute_change_set_status(change_set_id: str) -> str | None:
    with database.SessionLocal() as db:
        parent = db.query(ChangeSet).filter(ChangeSet.id == change_set_id).first()
        if parent is None:
            return None
        items = (
            db.query(ChangeItem)
            .filter(ChangeItem.change_set_id == change_set_id)
            .all()
        )
        observed = [item for item in items if item.status == "observed"]
        if not observed:
            status = "reverted" if any(item.status == "reverted" for item in items) else "open"
        elif all(item.review_status == "accepted" for item in observed):
            status = "accepted"
        elif all(item.review_status == "rejected" for item in observed):
            status = "rejected"
        elif any(item.review_status == "accepted" for item in observed):
            status = "reviewing"
        else:
            status = "open"
        parent.status = status
        parent.updated_at = _now()
        db.commit()
        return status


def update_change_item(
    change_item_id: str,
    *,
    review_status: str | None = None,
    review_note: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    with database.SessionLocal() as db:
        row = db.query(ChangeItem).filter(ChangeItem.id == change_item_id).first()
        if row is None:
            return None
        if review_status is not None:
            row.review_status = review_status
            row.decided_at = _now()
        if review_note is not None:
            row.review_note = review_note
        if status is not None:
            row.status = status
        db.commit()
        db.refresh(row)
        return _item_dict(row)


def set_plan_step_cursor(change_set_id: str, step_index: int | None) -> None:
    with database.SessionLocal() as db:
        row = db.query(ChangeSet).filter(ChangeSet.id == change_set_id).first()
        if row is None:
            return
        try:
            summary = json.loads(row.summary_json or "{}")
        except (TypeError, ValueError):
            summary = {}
        if not isinstance(summary, dict):
            summary = {}
        summary["current_plan_step_index"] = step_index
        row.summary_json = _json(summary)
        row.updated_at = _now()
        db.commit()


def map_change_items_to_step(
    change_set_id: str,
    *,
    plan_step_id: str | None,
    plan_step_index: int | None,
) -> None:
    with database.SessionLocal() as db:
        rows = (
            db.query(ChangeItem)
            .filter(
                ChangeItem.change_set_id == change_set_id,
                ChangeItem.plan_step_id.is_(None),
            )
            .all()
        )
        for row in rows:
            row.plan_step_id = plan_step_id
            row.plan_step_index = plan_step_index
        if rows:
            parent = db.query(ChangeSet).filter(ChangeSet.id == change_set_id).first()
            if parent is not None:
                parent.updated_at = _now()
        db.commit()
