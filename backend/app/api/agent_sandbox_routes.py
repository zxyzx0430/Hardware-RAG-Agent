"""Agent sandbox routes — policy management + audit log query (v3-T2).

Endpoints:
- GET  /api/agent-sandbox/policy  — read current permission_mode
- POST /api/agent-sandbox/policy  — update permission_mode
- GET  /api/agent-sandbox/audit   — query tool-call audit logs

Note: POST /api/agent-sandbox/resume stays in chat_routes.py (registered in v2).
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from app.api.dependencies import current_user
from app.db.database import get_db
from app.db.models import Settings as SettingsModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-sandbox", tags=["agent-sandbox"])

POLICY_KEY: str = "permissionMode"
DEFAULT_POLICY: str = "default"
VALID_POLICIES: set[str] = {"bypassPermissions", "default", "acceptEdits"}
DEFAULT_LIMIT: int = 100
MAX_LIMIT: int = 500


def _ok(data: dict | None = None) -> dict:
    return {"success": True, "data": data}


class PolicyUpdate(BaseModel):
    permission_mode: str


@router.get("/policy")
def get_policy(db: DBSession = Depends(get_db)) -> dict:
    """Read current permission_mode from settings table."""
    row = db.query(SettingsModel).filter(SettingsModel.key == POLICY_KEY).first()
    mode = row.value if row else DEFAULT_POLICY
    return _ok({"permission_mode": mode})


@router.post("/policy")
def update_policy(
    payload: PolicyUpdate,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> dict:
    """Update permission_mode in settings table (requires auth)."""
    mode = payload.permission_mode
    if mode not in VALID_POLICIES:
        return {"success": False, "error": {"code": "INVALID_POLICY",
                "message": f"invalid permission_mode: {mode}"}}
    _upsert_setting(db, POLICY_KEY, mode)
    logger.info("permission_mode updated to %s", mode)
    return _ok({"permission_mode": mode})


def _upsert_setting(db: DBSession, key: str, value: str) -> None:
    """Insert or update a settings row."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    existing = db.query(SettingsModel).filter(SettingsModel.key == key).first()
    if existing:
        existing.value = value
        existing.updated_at = now
    else:
        db.add(SettingsModel(key=key, value=value, updated_at=now))
    db.commit()


@router.get("/audit")
def get_audit_logs(
    session_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
    tool_name: str | None = None,
    decision: str | None = None,
    risk_level: str | None = None,
) -> dict:
    """Query tool-call audit logs with optional filters."""
    from src.agent.audit_logger import query_logs

    safe_limit = min(max(limit, 1), MAX_LIMIT)
    logs = query_logs(
        session_id=session_id,
        limit=safe_limit,
        tool_name=tool_name,
        decision=decision,
        risk_level=risk_level,
    )
    return _ok({"logs": logs, "count": len(logs)})
