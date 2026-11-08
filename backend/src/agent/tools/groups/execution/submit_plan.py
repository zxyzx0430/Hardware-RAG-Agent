"""SubmitPlanTool — submit one versioned plan for user approval."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_ID = "default"
_PLAN_MARKER_KEY = "_plan_update"


class PlanStepArgs(BaseModel):
    """One ordered plan step."""

    title: str = Field(description="步骤标题（一句话）")
    detail: str = Field(default="", description="可选的执行细节，支持 Markdown")
    verification: str = Field(default="", description="可选的验证方式，支持 Markdown")


class SubmitPlanArgs(BaseModel):
    """Plan payload submitted for user approval."""

    title: str = Field(description="计划标题")
    summary: str = Field(description="计划摘要，结论先行，支持 Markdown")
    steps: list[PlanStepArgs] = Field(description="有序步骤，1-12 条")
    risks: list[str] = Field(default_factory=list, description="风险列表")
    open_questions: list[str] = Field(default_factory=list, description="待确认问题")


class SubmitPlanTool(ToolSpec):
    """Persist a plan version and wait for the user's decision."""

    name: str = "submit_plan"
    description: str = (
        "提交一份带步骤的计划，交给用户批准。任何权限模式下都可调用，不需要先进入 plan mode；"
        "提交成功后停止执行，等待用户批准或拒绝。计划不是工具授权。"
    )
    args_schema: type = SubmitPlanArgs

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Validate and persist a plan version; never start execution."""
        session_id = getattr(ctx, "session_id", "") or _DEFAULT_SESSION_ID
        if session_id == _DEFAULT_SESSION_ID:
            return _error("submit_plan requires a real session")
        try:
            plan = _create_plan(session_id, args, getattr(ctx, "run_id", None))
        except Exception as exc:
            logger.warning("submit_plan failed session=%s: %s", session_id, exc)
            return _error(f"plan could not be saved: {exc}")
        return {
            "output": f"Plan v{plan['version']} submitted for user approval. Stop and wait.",
            "plan": plan,
            _PLAN_MARKER_KEY: True,
        }


def _create_plan(session_id: str, args: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    from app.db import plan_store

    payload = _payload(args)
    return plan_store.create_version(session_id, payload, run_id)


def _payload(args: dict[str, Any]) -> dict[str, Any]:
    steps = args.get("steps")
    return {
        "title": args.get("title", ""),
        "summary": args.get("summary", ""),
        "steps": [_step(item) for item in steps] if isinstance(steps, list) else steps,
        "risks": args.get("risks", []),
        "open_questions": args.get("open_questions", []),
    }


def _step(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        item = item.model_dump()
    if not isinstance(item, dict):
        return {}
    return {
        "title": item.get("title", ""),
        "detail": item.get("detail", ""),
        "verification": item.get("verification", ""),
    }


def _error(message: str) -> dict[str, Any]:
    return {"output": f"invalid plan: {message}", "plan": None, _PLAN_MARKER_KEY: False}
