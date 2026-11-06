"""Plan-mode cumulative tool-output budget."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.agent.core.toolkit import tool_router as tool_router_module
from src.agent.core.toolkit import tool_output_budget
from src.agent.core.toolkit.tool_output_budget import (
    PLAN_TOOL_OUTPUT_TOKEN_BUDGET,
    PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP,
    PlanToolOutputBudget,
    apply_plan_output_budget,
    plan_budget_preflight,
)
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.request_budget import estimate_payload_tokens


def _envelope(output="", data=None, metadata=None):
    return {
        "success": True,
        "output": output,
        "data": data,
        "error": None,
        "metadata": metadata or {
            "tool_name": "read_file",
            "hwrag_tool_observation_archived": True,
            "hwrag_tool_observation_ref": "r" * 43,
        },
    }


def test_non_plan_mode_result_is_untouched():
    context = ToolContext(permission_mode="default")
    original = _envelope(output="x" * 200_000)

    assert apply_plan_output_budget(original, "read_file", context) is original
    assert context.plan_tool_output_budget is None


def test_small_plan_result_is_kept_and_counted():
    context = ToolContext(permission_mode="plan")

    result = apply_plan_output_budget(_envelope(output="hello"), "read_file", context)

    assert result["output"] == "hello"
    assert context.plan_tool_output_budget.spent_tokens > 0


def test_large_plan_result_is_compacted_and_keeps_reference():
    context = ToolContext(permission_mode="plan")
    data = {"tree": [{"name": "file-" + "x" * 4000} for _ in range(20)]}

    result = apply_plan_output_budget(
        _envelope(output="summary", data=data), "list_files", context,
    )

    assert result["metadata"]["plan_output_compacted"] is True
    assert result["metadata"]["compacted"] is True
    assert result["metadata"]["hwrag_tool_observation_ref"] == "r" * 43
    assert "计划模式" in result["output"]
    assert estimate_payload_tokens(result) <= PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP
    assert context.plan_tool_output_budget.spent_tokens <= PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP


def test_preflight_exhausts_budget_with_actionable_message():
    context = ToolContext(permission_mode="plan")
    context.plan_tool_output_budget = PlanToolOutputBudget(
        limit_tokens=100, spent_tokens=100,
    )

    notice = plan_budget_preflight("list_files", context)

    assert notice is not None
    assert "submit_plan" in notice["output"]
    assert notice["metadata"]["plan_tool_budget_exhausted"] is True
    assert plan_budget_preflight("read_file", ToolContext(permission_mode="plan")) is None
    # An exhausted turn must still be able to submit its plan.
    assert plan_budget_preflight("submit_plan", context) is None


def test_cumulative_spend_exhausts_budget():
    context = ToolContext(permission_mode="plan")
    context.plan_tool_output_budget = PlanToolOutputBudget(limit_tokens=500, spent_tokens=0)
    payload = _envelope(output="z" * 400)

    for _ in range(10):
        result = apply_plan_output_budget(payload, "read_file", context)
        if result.get("metadata", {}).get("plan_tool_budget_exhausted"):
            break

    assert context.plan_tool_output_budget.remaining == 0
    notice = plan_budget_preflight("read_file", context)
    assert notice is not None


class _EmptyArgs(BaseModel):
    pass


class _BigReadTool(ToolSpec):
    name: str = "read_file"
    description: str = "Return a large deterministic payload for budget tests."
    args_schema: type[BaseModel] = _EmptyArgs

    async def execute(self, args, ctx):
        _EXECUTED.append("called")
        return {"output": "x" * 120_000, "payload": {"body": "y" * 60_000}}


_EXECUTED: list[str] = []


class _Recorder:
    def record(self, record):
        return None


@pytest.fixture(autouse=True)
def _no_archive(monkeypatch):
    _EXECUTED.clear()
    monkeypatch.setattr(
        tool_router_module, "_archive_tool_observation",
        lambda envelope, spec, ctx: None,
    )


async def test_dispatch_compacts_plan_mode_output():
    router = ToolRouter(_Recorder())
    context = ToolContext(permission_mode="plan")

    result = await router.dispatch(
        "call-1", "read_file", {}, context, tool_spec=_BigReadTool(),
    )

    assert _EXECUTED == ["called"]
    assert result["metadata"]["plan_output_compacted"] is True
    assert estimate_payload_tokens(result) <= PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP


async def test_dispatch_refuses_after_budget_is_spent():
    router = ToolRouter(_Recorder())
    context = ToolContext(permission_mode="plan")
    context.plan_tool_output_budget = PlanToolOutputBudget(
        limit_tokens=PLAN_TOOL_OUTPUT_TOKEN_BUDGET, spent_tokens=PLAN_TOOL_OUTPUT_TOKEN_BUDGET,
    )

    result = await router.dispatch(
        "call-2", "read_file", {}, context, tool_spec=_BigReadTool(),
    )

    assert _EXECUTED == []
    assert result["metadata"]["plan_tool_budget_exhausted"] is True


async def test_dispatch_preserves_full_output_outside_plan_mode():
    router = ToolRouter(_Recorder())
    context = ToolContext(permission_mode="default")

    result = await router.dispatch(
        "call-3", "read_file", {}, context, tool_spec=_BigReadTool(),
    )

    assert len(result["output"]) == 120_000
    assert "plan_output_compacted" not in result["metadata"]


def test_budget_constants_are_plan_mode_sized():
    assert 0 < PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP <= PLAN_TOOL_OUTPUT_TOKEN_BUDGET
    assert PLAN_TOOL_OUTPUT_TOKEN_BUDGET < 60_000


def test_budget_scales_down_for_small_model_windows():
    from src.agent.core.toolkit.tool_output_budget import get_plan_budget

    small = ToolContext(permission_mode="plan", context_token_limit=10_000)
    large = ToolContext(permission_mode="plan", context_token_limit=200_000)

    assert get_plan_budget(small).limit_tokens == 4_000
    assert get_plan_budget(large).limit_tokens == PLAN_TOOL_OUTPUT_TOKEN_BUDGET


async def test_fourteen_large_tool_calls_stay_under_the_model_window():
    """The audit scenario: 14 large calls must not reach 235k > 206k."""
    from src.agent import context_guard

    router = ToolRouter(_Recorder())
    context = ToolContext(permission_mode="plan", context_token_limit=206_233)
    context_guard.reset_token_counter(0)

    for index in range(14):
        result = await router.dispatch(
            f"audit-call-{index}", "read_file", {}, context,
            tool_spec=_BigReadTool(),
        )
        context_guard.accumulate_tokens(result, {"token_limit": 206_233})
        assert context_guard._CUMULATIVE_TOKENS.get() <= 206_233

    total = context_guard._CUMULATIVE_TOKENS.get()
    assert total <= PLAN_TOOL_OUTPUT_TOKEN_BUDGET + 2_000
    assert context.plan_tool_output_budget.spent_tokens <= PLAN_TOOL_OUTPUT_TOKEN_BUDGET
