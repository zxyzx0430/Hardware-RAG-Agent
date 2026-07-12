"""Layered compact mechanisms for Agent context management.

- microcompact: in-memory, drops old tool_result contents each round.
- autocompact: LLM-powered summarization when token budget overflows.

P1-T5/T6: keeps the Agent stream under the context window without
unconditionally aborting on ContextLimitError.
"""
from src.agent.compact.autocompact import (
    AUTOCOMPACT_KEEP_RECENT_MSGS,
    AUTOCOMPACT_MAX_RETRIES,
    RecoveryResult,
    attempt_context_recovery,
    autocompact_messages,
    should_autocompact,
)
from src.agent.compact.microcompact import (
    MICROCOMPACT_KEEP_RECENT,
    compact_old_tool_results,
)

__all__ = [
    "MICROCOMPACT_KEEP_RECENT",
    "compact_old_tool_results",
    "AUTOCOMPACT_KEEP_RECENT_MSGS",
    "AUTOCOMPACT_MAX_RETRIES",
    "should_autocompact",
    "autocompact_messages",
    "RecoveryResult",
    "attempt_context_recovery",
]
