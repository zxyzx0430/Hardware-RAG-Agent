"""ToolSpec — unified tool spec table, inheriting LangChain BaseTool.

Each tool MUST subclass ToolSpec and implement `execute(args, ctx) -> dict`.
The LangGraph ToolNode calls `_arun(**args)` which delegates to
ToolRouter.dispatch — so the LangGraph call chain is unchanged while all
cross-cutting logic (validation / timeout / loop / audit) is centralised
in ToolRouter.

Stage 3 migration: _arun now accepts optional `runtime` param (ToolRuntime
from create_agent context_schema). When runtime is present, ctx is read
from runtime.context; otherwise falls back to _ctx PrivateAttr (preserved
for backward compat with standalone tool calls). InjectedToolArg full
migration (per-tool args_schema) deferred to a later stage due to risk.

Spec: industrial-tool-runtime §ADDED Requirements (ToolSpec).
"""
from __future__ import annotations

import logging
import uuid
from abc import abstractmethod
from enum import Enum
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Defaults
# ═══════════════════════════════════════════

DEFAULT_TIMEOUT_SECONDS: int = 300
DEFAULT_MAX_RETRIES: int = 1


class RiskLevel(str, Enum):
    """Tool risk levels used by PermissionClassifier.

    LOW    -> auto-allow in every non-bypass mode.
    MEDIUM -> path_guard then route by permission_mode (ask in default).
    HIGH   -> always ask (even in acceptEdits); risk_classifier grades args.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConfirmationRule(str, Enum):
    """When the user must explicitly confirm a tool call."""
    ALWAYS = "always"
    NEVER = "never"
    CONDITIONAL = "conditional"


class ToolSpec(BaseTool):
    """Abstract base class for every Agent tool.

    Subclasses MUST:
      * declare `name`, `description`, `args_schema` (BaseTool requirement)
      * optionally declare `output_schema: type[BaseModel]` for output validation
      * set `risk_level` / `timeout_seconds` / `max_retries` /
        `requires_confirmation` / `audit` as appropriate
      * implement `async def execute(self, args, ctx) -> dict`

    The LangGraph ToolNode invokes `_arun(**args)`; this base implementation
    delegates to `ToolRouter.dispatch(call_id, self.name, args, self._ctx)`.
    ToolRouter then calls `spec.execute(args, ctx)` — so dispatch and _arun
    do NOT recurse (dispatch calls execute, not _arun).
    """

    # ── Spec fields (Pydantic-managed, set by subclasses) ──────────────
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    requires_confirmation: ConfirmationRule = ConfirmationRule.NEVER
    audit: bool = True
    # Output contract: a Pydantic model describing the shape of
    # envelope.data. ToolRouter soft-validates against this (warning-only,
    # never blocks). None means the tool does not declare an output schema.
    output_schema: type[BaseModel] | None = None
    # MCP origin marker. None for built-in tools; MCP tools set
    # {"server_name": <id>, "tool_name": <mcp tool name>} so the runtime
    # can trace provenance (spec v2 §4.3).
    mcp_info: dict | None = None

    # ── Per-request private state (injected by agent_factory) ──────────
    # _ctx: the ToolContext for the current chat request. Injected by
    #       agent_factory.build_tool_specs before the graph runs.
    # _current_call_id: optional call_id hint. _arun falls back to a fresh
    #       uuid when this is empty (BaseTool._arun cannot read LangGraph
    #       RunContext, so a hint is the only way to correlate).
    _ctx: ToolContext | None = PrivateAttr(default=None)
    _current_call_id: str = PrivateAttr(default="")

    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Subclasses MUST implement tool business logic.

        Returns a plain dict (NOT a ToolResultEnvelope). ToolRouter wraps
        the dict into an envelope. Business-level error recovery (e.g.
        run_command catching TimeoutError to return exit_code/timed_out)
        stays inside execute — ToolRouter only catches non-business
        exceptions.
        """
        raise NotImplementedError

    async def _arun(self, *args: Any, **kwargs: Any) -> dict:
        """LangGraph ToolNode entry point. Delegates to ToolRouter.dispatch.

        Stage 3: accepts optional `runtime` kwarg (ToolRuntime from
        create_agent context_schema). When present, ctx is read from
        runtime.context; otherwise falls back to _ctx PrivateAttr (preserved
        for backward compat with standalone tool calls).

        Importing ToolRouter lazily breaks the tool_spec <-> tool_router
        import cycle (tool_router imports ToolSpec for typing).
        """
        from src.agent.core.toolkit.tool_router import ToolRouter

        runtime = kwargs.pop("runtime", None)
        call_id = self._current_call_id or _new_call_id()
        ctx = _resolve_ctx(runtime, self._ctx)
        decision, decision_source = _derive_decision(ctx)
        router = ToolRouter.get_default()
        return await router.dispatch(call_id, self.name, dict(kwargs), ctx, decision, decision_source)

    def _run(self, **args: Any) -> dict:  # pragma: no cover - sync path unused
        """Sync fallback — not used by LangGraph async ToolNode.

        Kept for BaseTool compliance; raises to surface accidental sync use.
        """
        raise NotImplementedError(
            f"ToolSpec '{self.name}' is async-only; use the async Agent path."
        )


def _new_call_id() -> str:
    """Generate a fallback call_id when RunContext is unavailable."""
    return uuid.uuid4().hex


def _resolve_ctx(runtime: Any, fallback_ctx: ToolContext | None) -> ToolContext:
    """Resolve ToolContext from runtime (create_agent context_schema) or fallback.

    Stage 3: create_agent injects context via ToolRuntime; when present,
    runtime.context holds the ToolContext instance. Falls back to the
    PrivateAttr _ctx (preserved for standalone tool calls without a graph).
    """
    if runtime is not None:
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, ToolContext):
            return ctx
    return fallback_ctx or ToolContext()


def _derive_decision(ctx: ToolContext) -> tuple[str, str]:
    """Map the request context to the audit decision labels.

    bypassPermissions is a blanket override; every other mode records the
    real source stored on the context (auto_allow by default, user_allow
    after HITL confirmation).
    """
    if ctx.permission_mode == "bypassPermissions":
        return "bypass", "mode_bypass"
    return "allow", ctx.decision_source or "auto_allow"
