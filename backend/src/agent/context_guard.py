"""v3-T5: Cumulative context-limit + wall-clock timeout guards for the Agent.

Lives outside sse_adapter so the adapter stays under the 300-line file cap
(AGENTS.md). All token accounting flows through a module-level ContextVar so
concurrent Agent requests (each in its own asyncio task) never share state.

Spec: docs/superpowers/specs/2026-06-30-agent-react-design.md §6.3, §8.5.
"""
from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import Any

from app.api.sse import sse_event
from src.agent.exceptions import AgentTimeoutError, ContextLimitError
from src.agent.prompts import MAX_TOKEN_RATIO, TOOL_CALL_TIMEOUT_S
from src.llm.client import LLMClient
from src.llm.model_registry import get_context_window

logger = logging.getLogger(__name__)

# Per-request cumulative token counter. Each Agent request runs in its own
# asyncio task → contextvar naturally isolates the count without locks.
_CUMULATIVE_TOKENS: ContextVar[int] = ContextVar("agent_cumulative_tokens", default=0)


def reset_token_counter() -> None:
    """Reset the per-request counter to 0. Call at stream start."""
    _CUMULATIVE_TOKENS.set(0)


def compute_token_limit(
    model: str | None, context_window_override: int | None = None,
) -> int:
    """Compute max tokens. Priority: override > model_registry. 0 when both empty."""
    if context_window_override:
        context_window = context_window_override
    elif model:
        context_window = get_context_window(model)
    else:
        return 0
    return int(context_window * MAX_TOKEN_RATIO)


def accumulate_tokens(content: Any, state: dict) -> None:
    """Estimate tokens for a chunk and add to the contextvar.

    Tokens are accumulated silently; the caller is responsible for checking
    the limit via _CUMULATIVE_TOKENS (see sse_adapter._should_compact).
    No-op when token_limit <= 0 (model unknown / not provided).
    """
    if state.get("token_limit", 0) <= 0:
        return
    delta = LLMClient._estimate_tokens(content)
    cumulative = _CUMULATIVE_TOKENS.get() + max(delta, 0)
    _CUMULATIVE_TOKENS.set(cumulative)


def check_tool_timeout(call_start_time: dict[str, float], call_id: str) -> None:
    """Raise AgentTimeoutError when a single tool call exceeds TOOL_CALL_TIMEOUT_S.

    Per-tool wall-clock check (spec agent-reliability-batch MODIFIED Requirements).
    If call_id is not in call_start_time, returns without checking (no active call).
    """
    if call_id not in call_start_time:
        return
    elapsed = time.time() - call_start_time[call_id]
    if elapsed > TOOL_CALL_TIMEOUT_S:
        logger.warning(
            "tool_timeout call_id=%s elapsed=%.1fs limit=%ss",
            call_id, elapsed, TOOL_CALL_TIMEOUT_S,
        )
        raise AgentTimeoutError(elapsed=elapsed, limit=TOOL_CALL_TIMEOUT_S)


def context_limit_event(exc: ContextLimitError) -> str:
    """Build the SSE error event for cumulative-token overflow."""
    return sse_event("error", {
        "code": "CONTEXT_LIMIT",
        "message": f"上下文超长（{exc.cumulative} > {exc.limit}），降级为基础模式",
    })


def timeout_event(exc: AgentTimeoutError) -> str:
    """Build the SSE error event for wall-clock timeout."""
    return sse_event("error", {
        "code": "AGENT_TIMEOUT",
        "message": f"Agent 执行超时（{exc.elapsed:.0f}s > {exc.limit}s），降级为基础模式",
    })
