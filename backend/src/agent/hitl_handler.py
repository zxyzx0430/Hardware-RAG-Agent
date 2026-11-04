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

import asyncio
import logging
from typing import Any, AsyncIterator
from collections import Counter

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from app.api.sse import sse_event
from src.agent.context_guard import estimate_context_tokens, reset_token_counter
from src.agent.exceptions import ContextLimitError
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
        ctx = _get_tool_ctx()
        authorization = getattr(ctx, "mcp_authorization", None)
        if authorization is not None:
            from src.agent.core.toolkit.permission_classifier import PermissionClassifier
            confirmation = authorization.next_confirmation(pending, ctx.request_tools, PermissionClassifier(), ctx)
            if confirmation is not None:
                yield build_confirm_required_event([confirmation])
                return
            async for sse in _iter_agent_sse(agent, _RESUME_ALLOW, config, call_counter, call_start_time, state):
                yield sse
            continue
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
    *, confirmed_call_id: str | None = None, confirmation_resolved: bool = False,
    model: str = "", requested_max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Resume agent after user confirms/denies/stops via the resume API."""
    if not isinstance(decision, str) or decision not in {
        DECISION_ALLOW,
        DECISION_DENY,
        USER_DECISION_STOP,
    }:
        raise ValueError("Invalid HITL decision; expected allow, deny, or stop")

    from src.agent.sse_adapter import _iter_agent_sse
    from src.agent.sse_helpers import init_stream_state
    session_id = config.get("configurable", {}).get("thread_id", "?")
    logger.info("hitl_user_resume session=%s decision=%s", session_id, decision)
    call_start_time: dict[str, float] = {}
    ctx = _get_tool_ctx()
    authorization = getattr(ctx, "mcp_authorization", None)
    if authorization is not None:
        if decision == USER_DECISION_STOP:
            authorization.revoke()
            yield sse_event("done", {"success": False, "reason": "user stopped"})
            return
        pending = None
        if not confirmation_resolved:
            pending = await _detect_tools_interrupt(agent, config)
            if not authorization.validate_confirmation(confirmed_call_id, pending):
                raise ValueError("Confirmation no longer matches the pending call")
        state: dict = init_stream_state(config, model, requested_max_tokens)
        if not confirmation_resolved:
            authorization.resolve_confirmation(confirmed_call_id, decision)
        await _reset_tokens_from_checkpoint(agent, config, state["token_limit"])
        async for sse in handle_auto_resume(agent, config, permission_mode, call_counter, call_start_time, state):
            yield sse
        return
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
    if decision == DECISION_ALLOW:
        pending = await _detect_tools_interrupt(agent, config)
        _require_single_pending_confirmation_call(confirmed_call_id, pending)
    state: dict = init_stream_state(config, model, requested_max_tokens)
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
    await _reset_tokens_from_checkpoint(agent, config, state["token_limit"])
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


def _require_single_pending_confirmation_call(
    confirmed_call_id: str | None,
    pending: list[dict],
) -> None:
    """Require ALLOW to bind exactly one live pending call to its submitted ID."""
    if (
        type(confirmed_call_id) is not str
        or not confirmed_call_id
        or type(pending) is not list
        or len(pending) != 1
        or type(pending[0]) is not dict
        or type(pending[0].get("call_id")) is not str
        or not pending[0]["call_id"]
        or pending[0]["call_id"] != confirmed_call_id
    ):
        raise ValueError("Confirmation no longer matches one pending call")


async def _safe_get_state(agent: Any, config: dict) -> Any:
    """Read the Agent checkpoint and propagate failures to the caller."""
    return await agent.aget_state(config)


async def _reset_tokens_from_checkpoint(
    agent: Any, config: dict, token_limit: int,
) -> None:
    """Seed the resume guard from the complete checkpoint message list."""
    snapshot = await _safe_get_state(agent, config)
    values = getattr(snapshot, "values", None)
    messages = values.get("messages") if isinstance(values, dict) else None
    if not isinstance(messages, list):
        raise ContextLimitError(cumulative=1, limit=0)
    initial_tokens = estimate_context_tokens(messages)
    if initial_tokens > token_limit:
        raise ContextLimitError(cumulative=initial_tokens, limit=token_limit)
    reset_token_counter(initial_tokens)


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

    from src.agent.compact.writer_lifecycle import (
        WriterRunError,
        _HostStateUpdateOutcome,
        current_writer_binding,
    )

    binding = current_writer_binding(required=False)
    if binding is None:
        _audit_deny_best_effort(config, pending, decision_source)
        raise WriterRunError("HITL denial requires a host-owned writer binding")

    reservation: object | None = None
    update_task: asyncio.Task | None = None
    applied_config: Any = None
    write_started = False
    try:
        scope = _fresh_hitl_scope(binding)
        reservation = binding.registry._begin_host_state_update(binding.lease, scope)

        _fresh_hitl_scope(binding)
        before_snapshot = await agent.aget_state(config)
        _fresh_hitl_scope(binding)
        before_messages, verified_pending = _verify_pending_checkpoint(
            before_snapshot, pending,
        )
        deny_msgs = [_build_deny_message(tc) for tc in verified_pending]
        expected_checkpoint = _snapshot_checkpoint(
            before_snapshot, binding.scope.owner_key,
        )

        scope = _fresh_hitl_scope(binding)
        binding.registry._mark_host_state_update_started(
            binding.lease, scope, reservation,
        )
        write_started = True
        update_task = asyncio.create_task(
            agent.aupdate_state(
                getattr(before_snapshot, "config", None),
                values={"messages": deny_msgs},
            ),
        )
        applied_config = await _join_owned_state_update(update_task)
        _fresh_hitl_scope(binding)
        applied_checkpoint = _returned_checkpoint(
            applied_config, expected_checkpoint,
        )

        _fresh_hitl_scope(binding)
        readback = await agent.aget_state(applied_config)
        scope = _fresh_hitl_scope(binding)
        _verify_deny_readback(
            readback,
            before_messages,
            verified_pending,
            deny_msgs,
            expected_checkpoint=applied_checkpoint,
        )
        binding.registry._finish_host_state_update(
            binding.lease,
            scope,
            reservation,
            outcome=_HostStateUpdateOutcome.ACKNOWLEDGED,
        )
        reservation = None
    except asyncio.CancelledError:
        if update_task is not None:
            try:
                await _join_owned_state_update(update_task)
            except BaseException:
                pass
        if reservation is not None:
            _finish_hitl_reservation(
                binding,
                reservation,
                outcome=(
                    _HostStateUpdateOutcome.UNKNOWN
                    if write_started
                    else _HostStateUpdateOutcome.NOT_ATTEMPTED
                ),
            )
        raise
    except BaseException:
        if reservation is not None:
            _finish_hitl_reservation(
                binding,
                reservation,
                outcome=(
                    _HostStateUpdateOutcome.UNKNOWN
                    if write_started
                    else _HostStateUpdateOutcome.NOT_ATTEMPTED
                ),
            )
        _audit_deny_best_effort(config, pending, decision_source)
        raise

    _audit_deny_best_effort(config, pending, decision_source)


def _fresh_hitl_scope(binding: Any) -> Any:
    """Recheck the pinned host epoch and lease before or after each await."""
    from src.agent.compact.writer_lifecycle import QuarantineReason, WriterRunError

    try:
        scope_provider = getattr(binding, "scope_provider", None)
        if not callable(scope_provider):
            raise WriterRunError("trusted host scope provider is unavailable")
        scope = scope_provider()
        binding.registry.assert_scope(binding.lease, scope)
        return scope
    except Exception:
        _quarantine_hitl_binding(binding, QuarantineReason.WRITE_OUTCOME_UNKNOWN)
        raise WriterRunError("HITL writer epoch revalidation failed") from None


def _quarantine_hitl_binding(binding: Any, reason: Any) -> None:
    """Keep an owner closed when an HITL write or its readback is uncertain."""
    try:
        binding.registry.quarantine(binding.lease, binding.scope, reason=reason)
    except BaseException:
        # Scope mismatch and an existing quarantine are already fail-closed.
        pass


def _finish_hitl_reservation(
    binding: Any,
    reservation: object,
    *,
    outcome: Any,
) -> None:
    """Close a known no-write reservation or quarantine unresolved state."""
    from src.agent.compact.writer_lifecycle import QuarantineReason

    try:
        scope = _fresh_hitl_scope(binding)
        binding.registry._finish_host_state_update(
            binding.lease, scope, reservation, outcome=outcome,
        )
    except BaseException:
        _quarantine_hitl_binding(binding, QuarantineReason.WRITE_OUTCOME_UNKNOWN)
        return


async def _join_owned_state_update(task: asyncio.Task) -> Any:
    """Join the host-owned update task before allowing cancellation to escape."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


def _verify_pending_checkpoint(
    snapshot: Any,
    pending: list[dict],
) -> tuple[list[Any], list[dict]]:
    """Bind the caller's denial list to the current tools interrupt exactly."""
    from src.agent.compact.writer_lifecycle import WriterRunError

    if not snapshot or not _is_at_tools_node(snapshot):
        raise WriterRunError("HITL checkpoint is no longer waiting at the tools node")
    values = getattr(snapshot, "values", None)
    messages = values.get("messages") if type(values) is dict else None
    if type(messages) is not list or not messages or not isinstance(messages[-1], AIMessage):
        raise WriterRunError("HITL checkpoint does not end with a pending assistant call")
    current_pending = _extract_pending_calls(snapshot)
    if not current_pending or _pending_call_signature(current_pending) != _pending_call_signature(pending):
        raise WriterRunError("HITL denial no longer matches the pending tool calls")
    return list(messages), current_pending


def _pending_call_signature(calls: list[dict]) -> list[tuple[str, str, dict]]:
    """Validate and retain the exact ordered IDs, names, and args of pending calls."""
    from src.agent.compact.writer_lifecycle import WriterRunError

    if type(calls) is not list or not calls:
        raise WriterRunError("HITL denial has no pending tool calls")
    signature: list[tuple[str, str, dict]] = []
    seen_ids: set[str] = set()
    for call in calls:
        if type(call) is not dict:
            raise WriterRunError("HITL pending call is malformed")
        call_id = call.get("call_id")
        name = call.get("name")
        args = call.get("args")
        if (
            type(call_id) is not str
            or not call_id
            or type(name) is not str
            or not name
            or type(args) is not dict
            or call_id in seen_ids
        ):
            raise WriterRunError("HITL pending call identity is incomplete or ambiguous")
        seen_ids.add(call_id)
        signature.append((call_id, name, args))
    return signature


def _snapshot_checkpoint(snapshot: Any, owner_key: str) -> tuple[str, str, str]:
    """Read the exact thread/namespace/checkpoint tuple used by the snapshot."""
    from src.agent.compact.writer_lifecycle import WriterRunError

    config = getattr(snapshot, "config", None)
    configurable = config.get("configurable") if type(config) is dict else None
    if type(configurable) is not dict:
        raise WriterRunError("HITL checkpoint config is unavailable")
    thread_id = configurable.get("thread_id")
    namespace = configurable.get("checkpoint_ns", "")
    checkpoint_id = configurable.get("checkpoint_id")
    if (
        thread_id != owner_key
        or type(namespace) is not str
        or type(checkpoint_id) is not str
        or not checkpoint_id
    ):
        raise WriterRunError("HITL checkpoint is outside the leased writer scope")
    return thread_id, namespace, checkpoint_id


def _returned_checkpoint(
    config: Any,
    expected_checkpoint: tuple[str, str, str],
) -> tuple[str, str, str]:
    """Require the actual update-state config for a new checkpoint in-scope."""
    from src.agent.compact.writer_lifecycle import WriterRunError

    configurable = config.get("configurable") if type(config) is dict else None
    if type(configurable) is not dict:
        raise WriterRunError("HITL state update returned no checkpoint config")
    thread_id = configurable.get("thread_id")
    namespace = configurable.get("checkpoint_ns", "")
    checkpoint_id = configurable.get("checkpoint_id")
    checkpoint = (thread_id, namespace, checkpoint_id)
    if (
        type(thread_id) is not str
        or type(namespace) is not str
        or type(checkpoint_id) is not str
        or not checkpoint_id
        or thread_id != expected_checkpoint[0]
        or namespace != expected_checkpoint[1]
        or checkpoint_id == expected_checkpoint[2]
    ):
        raise WriterRunError("HITL state update returned a mismatched checkpoint")
    return checkpoint


def _verify_deny_readback(
    snapshot: Any,
    before_messages: list[Any],
    pending: list[dict],
    expected_deny_messages: list[ToolMessage],
    *,
    expected_checkpoint: tuple[str, str, str],
) -> None:
    """Prove exact denial state at the exact config before allowing resume."""
    from src.agent.compact.writer_lifecycle import WriterRunError

    if not snapshot or type(getattr(snapshot, "values", None)) is not dict:
        raise WriterRunError("HITL denial checkpoint readback is unavailable")
    if _snapshot_checkpoint(snapshot, expected_checkpoint[0]) != expected_checkpoint:
        raise WriterRunError("HITL denial readback points at another checkpoint")
    metadata = getattr(snapshot, "metadata", None)
    if type(metadata) is not dict or metadata.get("source") != "update":
        raise WriterRunError("HITL denial readback is not an update checkpoint")
    messages = snapshot.values.get("messages")
    if (
        type(messages) is not list
        or len(messages) != len(before_messages) + len(expected_deny_messages)
        or messages[:len(before_messages)] != before_messages
    ):
        raise WriterRunError("HITL denial readback changed the prior message state")
    actual_denials = messages[len(before_messages):]
    if len(actual_denials) != len(expected_deny_messages):
        raise WriterRunError("HITL denial readback is incomplete")
    for actual, expected in zip(actual_denials, expected_deny_messages):
        if not isinstance(actual, ToolMessage) or actual != expected:
            raise WriterRunError("HITL denial ToolMessage readback did not match")

    ai_message_index = len(before_messages) - 1
    ai_message = messages[ai_message_index]
    if not isinstance(ai_message, AIMessage):
        raise WriterRunError("HITL denial readback lost the originating assistant call")
    readback_pending = [_format_pending_call(call) for call in ai_message.tool_calls]
    if _pending_call_signature(readback_pending) != _pending_call_signature(pending):
        raise WriterRunError("HITL denial readback no longer matches the pending calls")
    tail_tools = [
        message for message in messages[ai_message_index + 1:]
        if isinstance(message, ToolMessage)
    ]
    expected_ids = [call["call_id"] for call in pending]
    if [message.tool_call_id for message in tail_tools] != expected_ids:
        raise WriterRunError("HITL denial readback does not account for every pending call")
    next_nodes = getattr(snapshot, "next", None)
    if (
        not isinstance(next_nodes, (list, tuple))
        or tuple(next_nodes) not in {
            ("model",),
            ("TokenCounterMiddleware.after_model",),
        }
    ):
        raise WriterRunError("HITL denial readback does not prove tools will be skipped")


def _audit_deny_best_effort(
    config: dict,
    pending: list[dict],
    decision_source: str,
) -> None:
    try:
        _audit_deny_decisions(config, pending, decision_source)
    except Exception as exc:
        logger.warning("audit_deny_failed err=%s", type(exc).__name__)


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
    from src.agent.request_context import active_tool_context
    active = active_tool_context()
    if active is not None:
        return active
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
