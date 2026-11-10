"""Record which approved-plan step the current run is executing.

The ChangeSet can then attribute later file changes to a frozen
``plan_id:version:step_index`` without relying on model prose.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext


class SetPlanStepArgs(BaseModel):
    step_index: int = Field(
        ge=0,
        description="Approved plan step index, zero-based, matching the plan's steps order",
    )


class SetPlanStepTool(ToolSpec):
    name: str = "set_plan_step"
    description: str = (
        "标记当前 Run 正在执行的已批准计划步骤（0-based index）。"
        "在执行计划中每个步骤前调用一次；之后的文件变更会归因到该步骤。"
        "非计划任务不要调用。"
    )
    args_schema: type = SetPlanStepArgs
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 5
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        run_id = getattr(ctx, "run_id", None)
        if not run_id:
            return {"output": "no active run", "success": False}
        step_index = args.get("step_index")
        if not isinstance(step_index, int) or step_index < 0:
            return {"output": "step_index must be a non-negative integer", "success": False}
        try:
            from src.agent.change_capture import set_plan_step_cursor

            ok = set_plan_step_cursor(
                session_id=str(getattr(ctx, "session_id", "default")),
                run_id=str(run_id),
                step_index=step_index,
            )
        except Exception as exc:
            return {"output": f"could not set plan step: {exc}", "success": False}
        return {
            "output": f"plan step index set to {step_index}" if ok else "plan step was not recorded",
            "success": ok,
            "step_index": step_index,
        }
