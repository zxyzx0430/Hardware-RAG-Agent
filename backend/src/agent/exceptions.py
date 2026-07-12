"""Agent-specific exceptions and runtime context for permission gating."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolContext:
    """Per-request context injected into local tools.

    Carries the permission mode (from ChatRequest), session_id (for audit
    logging and HITL resume correlation), kb_coverage_counter (per-request
    shared state for the search_docs coverage-hint feature, migrated out of
    the tool's PrivateAttr by the industrial-tool-runtime spec), and
    decision_source (so ToolRouter can record the real permission source).
    """
    permission_mode: str = "default"
    session_id: str = "default"
    kb_coverage_counter: dict[str, int] = field(default_factory=dict)
    source_counter: int = 0
    decision_source: str = "auto_allow"


class PermissionAskError(Exception):
    """Raised when permission_gate returns 'ask' — triggers HITL interrupt.

    Carries tool name, args, and risk_level so sse_adapter can build the
    tool_confirm_required SSE event for the frontend ConfirmDialog.
    """

    def __init__(self, tool: str, args: dict, risk_level: str, reason: str = ""):
        self.tool = tool
        self.args = args
        self.risk_level = risk_level
        self.reason = reason
        super().__init__(f"permission ask: {tool} risk={risk_level}")


class PermissionDenyError(Exception):
    """Raised when permission_gate returns 'deny' — Agent receives a refusal."""

    def __init__(self, tool: str, reason: str = ""):
        self.tool = tool
        self.reason = reason
        super().__init__(f"permission denied: {tool}: {reason}")


class ContextLimitError(Exception):
    """Raised when cumulative Agent tokens exceed context_window * MAX_TOKEN_RATIO.

    Carries the cumulative token count and the configured limit so sse_adapter
    can build a precise error SSE event before falling back to the LLM stream.
    """

    def __init__(self, cumulative: int, limit: int):
        self.cumulative = cumulative
        self.limit = limit
        super().__init__(f"context limit exceeded: {cumulative} > {limit}")


class AgentTimeoutError(Exception):
    """Raised when a single tool call exceeds TOOL_CALL_TIMEOUT_S wall-clock."""

    def __init__(self, elapsed: float, limit: int):
        self.elapsed = elapsed
        self.limit = limit
        super().__init__(f"agent timeout: {elapsed:.1f}s > {limit}s")


