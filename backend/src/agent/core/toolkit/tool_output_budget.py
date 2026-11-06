"""Plan-mode budget for cumulative tool output.

A read-only planning turn can call many tools; each full result is fed back to
the model and counted by ``context_guard``. Even individually bounded tools add
up (14 scans of a real project reached ~235k estimated tokens). This module
caps the *model-facing* envelope per tool and cumulatively per request while
the audit archive keeps the complete original result.

Only plan mode is affected. Other modes keep the existing tool contracts.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from src.agent.plan_policy import PLAN_MODE, SUBMIT_PLAN_TOOL_NAME

# Cumulative model-facing tool output budget for one plan-mode request.
PLAN_TOOL_OUTPUT_TOKEN_BUDGET: int = 48_000
# No single tool result may exceed this share of the budget.
PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP: int = 8_000
# Below this, another tool call cannot return anything useful.
_MIN_PREFLIGHT_REMAINING: int = 500

_TRUNCATION_MARKER = "…[truncated]"
_DATA_MARKER = "…[more items truncated]"
_COMPACT_NOTE = (
    "计划模式：本工具输出超出单次/累计预算，已压缩后显示。"
    "原始完整结果已归档；请优先基于已有信息制定计划，避免重复大范围扫描。"
)
_BUDGET_EXHAUSTED_TEXT = (
    "计划模式的工具输出预算已用完（上限 48000 tokens）。"
    "请停止继续扫描，基于已获得的信息调用 submit_plan；"
    "若仍缺少关键信息，请在计划的 open_questions 中写明需要用户缩小范围或新开会话，"
    "不要重复执行同类扫描。"
)


@dataclass
class PlanToolOutputBudget:
    """Cumulative estimated-token spend for one plan-mode request."""

    limit_tokens: int = PLAN_TOOL_OUTPUT_TOKEN_BUDGET
    spent_tokens: int = 0

    @property
    def remaining(self) -> int:
        return max(self.limit_tokens - self.spent_tokens, 0)


def get_plan_budget(ctx: Any) -> PlanToolOutputBudget:
    """Return this request context's budget, creating it lazily.

    Small-window models get a proportionally smaller tool-output budget so the
    budget itself can never exceed the model's usable context.
    """
    budget = getattr(ctx, "plan_tool_output_budget", None)
    if not isinstance(budget, PlanToolOutputBudget):
        limit = PLAN_TOOL_OUTPUT_TOKEN_BUDGET
        context_limit = getattr(ctx, "context_token_limit", 0)
        if type(context_limit) is int and context_limit > 0:
            limit = max(2_000, min(limit, int(context_limit * 0.4)))
        budget = PlanToolOutputBudget(limit_tokens=limit)
        setattr(ctx, "plan_tool_output_budget", budget)
    return budget


def _estimate_tokens(value: Any) -> int:
    from src.agent.request_budget import estimate_payload_tokens

    return max(estimate_payload_tokens(value), 0)


def _notice_metadata(tool_name: str) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "duration_ms": 0,
        "call_id": "",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "compacted": True,
        "plan_tool_budget_exhausted": True,
    }


def budget_exhausted_notice(tool_name: str) -> dict[str, Any]:
    """Build the model-facing result used once the budget is spent."""
    return {
        "success": True,
        "output": _BUDGET_EXHAUSTED_TEXT,
        "data": None,
        "error": None,
        "metadata": _notice_metadata(tool_name),
    }


def plan_budget_preflight(tool_name: str, ctx: Any) -> dict[str, Any] | None:
    """Refuse another plan-mode tool call after the budget is exhausted.

    ``submit_plan`` is always admitted: an exhausted turn must still be able
    to hand a plan with open questions back to the user.
    """
    if tool_name == SUBMIT_PLAN_TOOL_NAME:
        return None
    if getattr(ctx, "permission_mode", "") != PLAN_MODE:
        return None
    if get_plan_budget(ctx).remaining > _MIN_PREFLIGHT_REMAINING:
        return None
    return budget_exhausted_notice(tool_name)


def apply_plan_output_budget(
    result: dict[str, Any], tool_name: str, ctx: Any,
) -> dict[str, Any]:
    """Bound one model-facing tool result and record its token cost."""
    if getattr(ctx, "permission_mode", "") != PLAN_MODE:
        return result
    budget = get_plan_budget(ctx)
    full_tokens = _estimate_tokens(result)
    allowed = min(PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP, budget.remaining)
    if full_tokens <= allowed:
        budget.spent_tokens += full_tokens
        return result
    if budget.remaining <= 0:
        return budget_exhausted_notice(tool_name)
    compacted = _compact_envelope(result, allowed)
    compacted["metadata"]["compacted"] = True
    compacted["metadata"]["plan_output_compacted"] = True
    spent = min(_estimate_tokens(compacted), budget.remaining)
    budget.spent_tokens += spent
    return compacted


def _compact_envelope(result: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """Shrink output/data while preserving the envelope shape and references."""
    metadata = dict(result.get("metadata") or {})
    output = str(result.get("output") or "")
    data = result.get("data")
    for string_cap, list_cap in ((2000, 20), (1000, 10), (400, 5), (200, 3)):
        candidate = {
            "success": result.get("success"),
            "output": _append_note(_clip_text(output, string_cap), _COMPACT_NOTE),
            "data": _shrink_value(data, string_cap, list_cap),
            "error": result.get("error"),
            "metadata": metadata,
        }
        if _estimate_tokens(candidate) <= max_tokens:
            return candidate
    fallback_text = output or _json_text(data)
    trimmed = _truncate_text_to_tokens(fallback_text, max_tokens)
    return {
        "success": result.get("success"),
        "output": _append_note(trimmed, _COMPACT_NOTE),
        "data": None,
        "error": result.get("error"),
        "metadata": metadata,
    }


def _json_text(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return str(value)


def _clip_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + _TRUNCATION_MARKER


def _append_note(text: str, note: str) -> str:
    if note in text:
        return text
    return f"{text}\n{note}" if text else note


def _shrink_value(value: Any, string_cap: int, list_cap: int) -> Any:
    if isinstance(value, str):
        return _clip_text(value, string_cap)
    if isinstance(value, list):
        items = [_shrink_value(item, string_cap, list_cap) for item in value[:list_cap]]
        if len(value) > list_cap:
            items.append(_DATA_MARKER)
        return items
    if isinstance(value, dict):
        return {
            key: _shrink_value(item, string_cap, list_cap)
            for key, item in value.items()
        }
    return value


def _truncate_text_to_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return _TRUNCATION_MARKER
    if _estimate_tokens(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _estimate_tokens(text[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return text[:low] + _TRUNCATION_MARKER
