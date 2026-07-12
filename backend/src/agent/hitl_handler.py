"""HITL (Human-in-the-Loop) interrupt handler for Agent tools node.

When enable_hitl=True (permission_mode != bypassPermissions), langgraph's
interrupt_before=["tools"] pauses execution before each tool call. This module:

1. Detects the interrupt by inspecting agent.get_state(config).next
2. Evaluates each pending tool_call against permission_gate
3. Auto-resumes for allow decisions, yields tool_confirm_required for ask,
   injects denial ToolMessages for deny
4. Exposes resume_agent_after_user for the frontend's resume API

Spec §7 (permission gate), §9 (HITL flow).
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator
from collections import Counter

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from app.api.sse import sse_event
from src.agent.hitl_sse import emit_deny_tool_results
from src.agent.hitl_permission import evaluate_pending, build_confirm_required_event

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

TOOL_NODE_NAME: str = "tools"
DECISION_ALLOW: str = "allow"
DECISION_ASK: str = "ask"
DECISION_DENY: str = "deny"
USER_DECISION_STOP: str = "stop"
DENY_MESSAGE: str = "用户拒绝执行此工具"

# Audit decision_source enums (spec industrial-tool-runtime §AuditRecorder).
SOURCE_USER_DENY: str = "user_deny"
SOURCE_PATH_DENY: str = "path_deny"
SOURCE_USER_ALLOW: str = "user_allow"

# Resume command payload used to resume LangGraph after HITL interrupt.
_RESUME_ALLOW: Command = Command(resume={"action": "allow"})
_RESUME_DENY: Command = Command(resume={"action": "deny"})


# ═══════════════════════════════════════════
# Public entry points
# ═══════════════════════════════════════════

async def handle_auto_resume(
    agent: Any, config: dict, permission_mode: str,
    call_counter: Counter, call_start_time: dict, state: dict,
) -> AsyncIterator[str]:
    """Loop over tools interrupts: auto-resume allow/deny, yield confirm for ask."""
    from src.agent.sse_adapter import _iter_agent_sse
    session_id = config.get("configurable", {}).get("thread_id", "?")
    while True:
        pending = await _detect_tools_interrupt(agent, config)
        if not pending:
            return
        tool_name = pending[0].get("name", "?") if pending else "?"
        logger.info("hitl_auto_resume session=%s tool=%s", session_id, tool_name)
        decision = evaluate_pending(pending, permission_mode)
        if decision == DECISION_ASK:
            yield build_confirm_required_event(pending)
            return
        if decision == DECISION_DENY:
            await _inject_deny_messages(agent, config, pending, SOURCE_PATH_DENY)
            for sse in emit_deny_tool_results(pending, call_start_time, DENY_MESSAGE):
                yield sse
            resume_cmd = _RESUME_DENY
        else:
            resume_cmd = _RESUME_ALLOW
        async for sse in _iter_agent_sse(agent, resume_cmd, config, call_counter, call_start_time, state):
            yield sse


async def resume_agent_after_user(
    agent: Any, config: dict, decision: str,
    call_counter: Counter, permission_mode: str,
) -> AsyncIterator[str]:
    """Resume agent after user confirms/denies/stops via the resume API."""
    from src.agent.sse_adapter import _iter_agent_sse
    from src.agent.sse_helpers import init_stream_state
    session_id = config.get("configurable", {}).get("thread_id", "?")
    logger.info("hitl_user_resume session=%s decision=%s", session_id, decision)
    call_start_time: dict[str, float] = {}
    state: dict = init_stream_state(config, "")
    if decision == USER_DECISION_STOP:
        pending = await _detect_tools_interrupt(agent, config)
        tool_name = pending[0].get("name", "?") if pending else "?"
        logger.warning("hitl_user_reject_and_stop session=%s tool=%s", session_id, tool_name)
        if pending:
            await _inject_deny_messages(agent, config, pending, SOURCE_USER_DENY)
            for sse in emit_deny_tool_results(pending, call_start_time, DENY_MESSAGE):
                yield sse
        yield sse_event("done", {"success": False, "reason": "user stopped"})
        return
    if decision == DECISION_DENY:
        pending = await _detect_tools_interrupt(agent, config)
        tool_name = pending[0].get("name", "?") if pending else "?"
        logger.warning("hitl_user_reject session=%s tool=%s", session_id, tool_name)
        await _inject_deny_messages(agent, config, pending, SOURCE_USER_DENY)
        for sse in emit_deny_tool_results(pending, call_start_time, DENY_MESSAGE):
            yield sse
        resume_cmd = _RESUME_DENY
    else:
        _mark_user_allow_in_ctx()
        resume_cmd = _RESUME_ALLOW
    async for sse in _iter_agent_sse(agent, resume_cmd, config, call_counter, call_start_time, state):
        yield sse
    async for sse in handle_auto_resume(agent, config, permission_mode, call_counter, call_start_time, state):
        yield sse


# ═══════════════════════════════════════════
# Interrupt detection
# ═══════════════════════════════════════════

async def _detect_tools_interrupt(agent: Any, config: dict) -> list[dict]:
    """Return pending tool_calls if agent paused before tools node, else []."""
    snapshot = await _safe_get_state(agent, config)
    if not snapshot or not _is_at_tools_node(snapshot):
        return []
    return _extract_pending_calls(snapshot)


async def _safe_get_state(agent: Any, config: dict) -> Any:
    """Wrap agent.get_state so checkpoint errors never crash the stream."""
    try:
        return await agent.aget_state(config)
    except Exception as exc:
        logger.debug("aget_state failed: %s", exc)
        return None


def _is_at_tools_node(snapshot: Any) -> bool:
    """True when snapshot.next lists the tools node (i.e. paused before tools)."""
    nxt = getattr(snapshot, "next", None) or []
    return TOOL_NODE_NAME in nxt


def _extract_pending_calls(snapshot: Any) -> list[dict]:
    """Pull tool_calls from the last AIMessage in the snapshot state."""
    values = getattr(snapshot, "values", None) or {}
    messages = values.get("messages", []) if isinstance(values, dict) else []
    if not messages:
        return []
    last = messages[-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return []
    return [_format_pending_call(tc) for tc in last.tool_calls]


def _format_pending_call(tc: dict) -> dict:
    """Shape one tool_call into the dict the SSE event + gate expect."""
    return {
        "name": tc.get("name", ""),
        "args": tc.get("args", {}),
        "call_id": tc.get("id", ""),
    }


# ═══════════════════════════════════════════
# Deny injection
# ═══════════════════════════════════════════

async def _inject_deny_messages(agent: Any, config: dict, pending: list[dict], decision_source: str) -> None:
    """Inject ToolMessage denials so the tools node skips execution.

    industrial-tool-runtime: also record the deny decision to ToolAudit
    (spec §AuditRecorder — every permission decision must be audited).
    """
    if not pending:
        return
    deny_msgs = [_build_deny_message(tc) for tc in pending]
    try:
        await agent.aupdate_state(config, values={"messages": deny_msgs})
    except Exception as exc:
        logger.warning("aupdate_state inject deny failed: %s", exc)
    _audit_deny_decisions(config, pending, decision_source)


def _audit_deny_decisions(config: dict, pending: list[dict], decision_source: str) -> None:
    """Record each deny decision to ToolAudit (spec §AuditRecorder).

    ToolRouter only audits dispatches that actually ran; deny happens upstream
    at the pre-ToolNode stage, so we log it here with error_type=PERMISSION_DENIED.
    """
    from src.agent.audit_logger import log_tool_call
    from src.agent.core.toolkit.tool_router import _TOOL_REGISTRY

    session_id = config.get("configurable", {}).get("thread_id")
    for tc in pending:
        name = tc.get("name", "?")
        spec = _TOOL_REGISTRY.get(name)
        risk = spec.risk_level.value if spec else "unknown"
        try:
            log_tool_call(
                session_id=session_id,
                tool_name=name,
                args=tc.get("args", {}),
                decision=DECISION_DENY,
                decision_source=decision_source,
                risk_level=risk,
                call_id=tc.get("call_id") or "",
                success=False,
                error_type="PERMISSION_DENIED",
            )
        except Exception as exc:  # noqa: BLE001 — DB must not break the stream
            logger.warning("audit_deny_failed tool=%s err=%s", name, exc)


def _build_deny_message(tc: dict) -> ToolMessage:
    """Build one denial ToolMessage matching the pending tool_call_id."""
    return ToolMessage(content=DENY_MESSAGE, tool_call_id=tc.get("call_id", ""))


def _get_tool_ctx() -> Any:
    """Return the per-request ToolContext shared by registered ToolSpec instances.

    All tools for a request are injected with the same ctx object, so grabbing
    the first registered spec's _ctx is sufficient for HITL bookkeeping.
    """
    from src.agent.core.toolkit.tool_router import _TOOL_REGISTRY
    for spec in _TOOL_REGISTRY.values():
        ctx = getattr(spec, "_ctx", None)
        if ctx is not None:
            return ctx
    return None


def _mark_user_allow_in_ctx() -> None:
    """Set decision_source to user_allow so ToolRouter audits the real source."""
    ctx = _get_tool_ctx()
    if ctx is not None:
        ctx.decision_source = SOURCE_USER_ALLOW
