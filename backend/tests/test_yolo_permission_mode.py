"""YOLO mode is the explicit no-confirmation mode."""

from pydantic import BaseModel

from src.agent.core.toolkit.permission_classifier import ALLOW, ASK, DENY, PermissionClassifier
from src.agent.core.toolkit.tool_spec import ConfirmationRule, RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext


class _Args(BaseModel):
    path: str = ""


class _AlwaysHigh(ToolSpec):
    name: str = "always_high_probe"
    description: str = "Synthetic always-confirm high-risk probe"
    args_schema: type[BaseModel] = _Args
    risk_level: RiskLevel = RiskLevel.HIGH
    requires_confirmation: ConfirmationRule = ConfirmationRule.ALWAYS

    async def execute(self, args, ctx):
        return {"output": "synthetic"}


def test_yolo_allows_high_and_always_confirmation_tools():
    decision = PermissionClassifier().check(
        _AlwaysHigh(), {"path": "x"}, ToolContext(permission_mode="yolo"),
    )

    assert decision == ALLOW


def test_default_mode_still_asks_for_always_tools():
    decision = PermissionClassifier().check(
        _AlwaysHigh(), {"path": "x"}, ToolContext(permission_mode="default"),
    )

    assert decision == ASK


def test_plan_mode_still_wins_over_yolo_write_rules():
    class _Write(ToolSpec):
        name: str = "write_file"
        description: str = "Synthetic write"
        args_schema: type[BaseModel] = _Args
        risk_level: RiskLevel = RiskLevel.MEDIUM
        requires_confirmation: ConfirmationRule = ConfirmationRule.NEVER

        async def execute(self, args, ctx):
            return {"output": "synthetic"}

    classifier = PermissionClassifier()

    assert classifier.check(
        _Write(), {"path": "x"}, ToolContext(permission_mode="plan"),
    ) == DENY
    assert classifier.check(
        _AlwaysHigh(), {"path": "x"}, ToolContext(permission_mode="yolo"),
    ) == ALLOW
