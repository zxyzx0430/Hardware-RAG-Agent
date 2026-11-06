"""Plan-mode policy: allowlist, action rules, classifier ordering and router gate."""

import pytest
from pydantic import BaseModel

from src.agent.core.toolkit.audit_recorder import AuditRecorder
from src.agent.core.toolkit.permission_classifier import ALLOW, DENY, PermissionClassifier
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.plan_policy import (
    SUBMIT_PLAN_TOOL_NAME,
    filter_tools_for_plan_mode,
    is_allowed_in_plan_mode,
)
from src.agent.tools.groups.execution.submit_plan import SubmitPlanTool
from src.agent.tools.groups.file_ops.read_file import ReadFileTool
from src.agent.tools.groups.file_ops.write_file import WriteFileTool


class _EmptyArgs(BaseModel):
    pass


class _McpSpec(ToolSpec):
    name: str = "mcp__server__echo"
    description: str = "fake MCP tool"
    args_schema: type = _EmptyArgs
    risk_level: RiskLevel = RiskLevel.HIGH
    mcp_info: dict | None = {"server_name": "server", "tool_name": "echo"}

    async def execute(self, args: dict, ctx: ToolContext) -> dict:
        return {"output": "should not run"}


def test_read_only_allowlist():
    assert is_allowed_in_plan_mode("search_docs")
    assert is_allowed_in_plan_mode("read_file")
    assert is_allowed_in_plan_mode("memory_search")
    assert is_allowed_in_plan_mode(SUBMIT_PLAN_TOOL_NAME)
    assert not is_allowed_in_plan_mode("write_file")
    assert not is_allowed_in_plan_mode("run_command")
    assert not is_allowed_in_plan_mode("todo_write")
    assert not is_allowed_in_plan_mode("mcp__server__echo")


def test_hardware_profile_action_rule():
    assert is_allowed_in_plan_mode("hardware_profile", {"action": "search"})
    assert is_allowed_in_plan_mode("hardware_profile", {"action": "get"})
    assert not is_allowed_in_plan_mode("hardware_profile", {"action": "upsert"})
    assert not is_allowed_in_plan_mode("hardware_profile", {})


def test_filter_tools_drops_writes_and_keeps_submit():
    filtered = filter_tools_for_plan_mode([WriteFileTool(), ReadFileTool(), SubmitPlanTool()])
    names = {tool.name for tool in filtered}

    assert names == {"read_file", SUBMIT_PLAN_TOOL_NAME}


def test_classifier_plan_branch_runs_before_mcp_and_risk():
    classifier = PermissionClassifier()
    ctx = ToolContext(permission_mode="plan", session_id="s1")

    assert classifier.check(ReadFileTool(), {"path": "x"}, ctx) == ALLOW
    assert classifier.check(WriteFileTool(), {}, ctx) == DENY
    assert classifier.check(_McpSpec(), {}, ctx) == DENY


def test_classifier_default_mode_still_asks_for_write():
    classifier = PermissionClassifier()
    ctx = ToolContext(permission_mode="default", session_id="s1")

    assert classifier.check(WriteFileTool(), {"path": "x", "content": "y"}, ctx) == "ask"


@pytest.mark.asyncio
async def test_router_denies_write_in_plan_mode():
    router = ToolRouter(AuditRecorder())
    ctx = ToolContext(permission_mode="plan", session_id="s1")

    result = await router.dispatch(
        "c1", "write_file", {"path": "x", "content": "y"}, ctx,
        tool_spec=WriteFileTool(),
    )

    assert result["success"] is False
    assert result["error"]["error_type"] == "PLAN_MODE_READ_ONLY"


def test_plan_mode_prompt_documents_narrow_scanning_and_budget():
    from src.agent.plan_prompt import build_plan_mode_prompt

    prompt = build_plan_mode_prompt()

    assert "Scanning discipline" in prompt
    assert "node_modules" in prompt
    assert "48000" in prompt
    assert "submit_plan" in prompt
