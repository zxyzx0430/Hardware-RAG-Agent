"""Permission evaluation for HITL pending tool calls.

Extracted from hitl_handler.py to keep that file under the 300-line cap.
"""
from __future__ import annotations

import logging
from typing import Any

from app.api.sse import sse_event

logger = logging.getLogger(__name__)

DECISION_ALLOW: str = "allow"
DECISION_ASK: str = "ask"
DECISION_DENY: str = "deny"

ENTER_PLAN_MODE_TOOL_NAME: str = "enter_plan_mode"


def is_mixed_enter_plan_batch(pending: list[dict]) -> bool:
    """True when enter_plan_mode shares an AI message with other tool calls.

    LangGraph runs one message's tool calls concurrently, so a write issued in
    the same batch could finish before the downgrade mutates the context.
    Deny the whole batch; the model must call enter_plan_mode alone first.
    """
    names = [
        tc.get("name") for tc in pending
        if type(tc) is dict and isinstance(tc.get("name"), str)
    ]
    return ENTER_PLAN_MODE_TOOL_NAME in names and len(names) > 1


def evaluate_pending(pending: list[dict], permission_mode: str) -> str:
    """Return 'ask' / 'allow' / 'deny' via PermissionClassifier for all calls.

    Replaces the legacy permission_gate.check_permission call with the
    industrial-tool-runtime PermissionClassifier (spec §HITL 权限中断). The
    classifier reads risk_level from the ToolSpec registered in ToolRouter.
    """
    if is_mixed_enter_plan_batch(pending):
        return DECISION_DENY
    from src.agent.core.toolkit.permission_classifier import PermissionClassifier
    from src.agent.exceptions import ToolContext
    from src.agent.request_context import active_tool_context
    classifier = PermissionClassifier()
    ctx = active_tool_context() or ToolContext(permission_mode=permission_mode)
    has_ask = False
    for tc in pending:
        decision = _classify_one_call(classifier, tc, ctx)
        if decision == DECISION_DENY:
            return DECISION_DENY
        has_ask = has_ask or decision == DECISION_ASK
    return DECISION_ASK if has_ask else DECISION_ALLOW


def ask_pending_calls(pending: list[dict], permission_mode: str) -> list[dict]:
    """Return only the calls that actually need a user decision."""
    from src.agent.core.toolkit.permission_classifier import PermissionClassifier
    from src.agent.exceptions import ToolContext
    from src.agent.request_context import active_tool_context
    classifier = PermissionClassifier()
    ctx = active_tool_context() or ToolContext(permission_mode=permission_mode)
    return [
        tc for tc in pending
        if _classify_one_call(classifier, tc, ctx) == DECISION_ASK
    ]


def _classify_one_call(classifier: Any, tc: dict, ctx: Any) -> str:
    """Classify a single pending tool_call. Unknown tools are denied."""
    from src.agent.core.toolkit.tool_router import _TOOL_REGISTRY
    from src.agent.request_context import active_tool_context
    active = active_tool_context()
    spec = (active.request_tools if active is not None else _TOOL_REGISTRY).get(tc["name"])
    if spec is None:
        logger.warning("permission_unknown_tool tool=%s -> deny", tc["name"])
        return DECISION_DENY
    return classifier.check(spec, tc["args"], ctx)


def build_confirm_required_event(pending: list[dict]) -> str:
    """Build the tool_confirm_required SSE event for the frontend ConfirmDialog."""
    calls = [_summarize_call(tc) for tc in pending]
    return sse_event("tool_confirm_required", {
        "calls": calls,
        "count": len(calls),
    })


def _summarize_call(tc: dict) -> dict:
    """Add risk_level to a pending call so the dialog can show a badge.

    risk_level now comes from the ToolSpec registered in ToolRouter (spec
    industrial-tool-runtime §PermissionClassifier), not the legacy
    permission_gate/risk_classifier pair.
    """
    from src.agent.core.toolkit.tool_router import _TOOL_REGISTRY
    from src.agent.request_context import active_tool_context
    active = active_tool_context()
    spec = (active.request_tools if active is not None else _TOOL_REGISTRY).get(tc["name"])
    risk = spec.risk_level.value if spec else "unknown"
    return {
        "name": tc["name"],
        "args": tc["args"],
        "call_id": tc["call_id"],
        "risk_level": risk,
    }
