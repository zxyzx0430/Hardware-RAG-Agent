"""EnterPlanModeTool — the model's only permission change: downgrade to plan."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.plan_policy import PLAN_MODE

logger = logging.getLogger(__name__)

ENTER_PLAN_MODE_TOOL_NAME = "enter_plan_mode"
DEFAULT_SESSION_ID = "default"
MAX_REASON_CHARS = 500

PLAN_MODE_ACTIVE_MESSAGE = (
    "Plan mode is now active. Only read-only tools are available. "
    "Research first; when ready, call submit_plan with title/summary/steps/"
    "risks/open_questions. Unresolved questions go in open_questions. "
    "You cannot exit plan mode; the user must."
)


class EnterPlanModeArgs(BaseModel):
    """Optional audit note; it does not select a target permission level."""

    reason: str = Field(
        default="",
        max_length=MAX_REASON_CHARS,
        description="可选：简要说明为什么要先规划（仅用于审计/UI 提示）",
    )


class EnterPlanModeTool(ToolSpec):
    """Switch this session to read-only plan mode for the rest of the run."""

    name: str = ENTER_PLAN_MODE_TOOL_NAME
    description: str = (
        "当你判断任务需要先调研、设计或和用户确认方案，而不是立刻修改时调用。"
        "它会把本会话切换到只读 plan 模式：不能再写文件、跑命令、编译或烧录；"
        "调研完成后调用 submit_plan 提交计划。你不能自行切回更高权限，只有用户能。"
    )
    args_schema: type = EnterPlanModeArgs

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        if ctx.permission_mode == PLAN_MODE:
            return _error("already in plan mode")
        session_id = getattr(ctx, "session_id", "") or ""
        if session_id == DEFAULT_SESSION_ID:
            return _error("enter_plan_mode requires a real session")
        reason = str(args.get("reason") or "")[:MAX_REASON_CHARS]
        try:
            persisted = _persist_agent_plan_request(session_id)
        except Exception as exc:
            logger.warning("enter_plan_mode failed session=%s: %s", session_id, exc)
            return _error("plan mode could not be persisted")
        if not persisted:
            return _error("session does not exist")
        ctx.permission_mode = PLAN_MODE
        logger.info("agent_entered_plan_mode session=%s reason=%s", session_id, reason)
        return {
            "output": PLAN_MODE_ACTIVE_MESSAGE,
            "_agent_plan_entered": True,
            "permission_mode": PLAN_MODE,
            "reason": reason,
        }


def _persist_agent_plan_request(session_id: str) -> bool:
    from app.db import agent_mode_store

    if not agent_mode_store.session_exists(session_id):
        return False
    agent_mode_store.request_plan_mode(session_id)
    return True


def _error(message: str) -> dict[str, Any]:
    return {
        "output": f"invalid plan mode request: {message}",
        "permission_mode": None,
        "_agent_plan_entered": False,
    }
