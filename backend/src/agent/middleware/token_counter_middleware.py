"""Token counter middleware — AgentMiddleware subclass for token accounting.

Prepared for stage 3 (create_agent). In stage 2, this class is defined but
NOT registered to create_react_agent (which lacks middleware support). The
active token counting path remains context_guard.accumulate_tokens (called
from sse_adapter._handle_message_chunk at every AIMessageChunk).

When stage 3 migrates to create_agent, this middleware will be registered
via create_agent(middleware=[token_counter_middleware_instance]) to
centralize token accounting at the model-call boundary.

Spec: .trae/specs/migrate-langchain-1x-new-api/ Task 7.
"""
from __future__ import annotations

import logging
from typing import Any

from src.agent.context_guard import accumulate_tokens

logger = logging.getLogger(__name__)


try:
    from langchain.agents.middleware import AgentMiddleware
    _BASE = AgentMiddleware
except ImportError:  # pragma: no cover — langchain 1.x verified in stage 0
    _BASE = object
    logger.warning("AgentMiddleware import failed — TokenCounterMiddleware degrades to plain class")


class TokenCounterMiddleware(_BASE):
    """AgentMiddleware subclass for token accounting via after_model hook.

    Prepared for stage 3 create_agent registration. In stage 2, this class
    is defined but not wired into the agent (create_react_agent lacks
    middleware support). The active path remains sse_adapter's direct call
    to accumulate_tokens.

    Implementation notes:
    - after_model fires after each LLM call; we extract message content and
      feed it to accumulate_tokens (same function sse_adapter uses).
    - accumulate_tokens reads token_limit from state['token_limit']; the
      middleware passes state through unchanged.
    - Errors are swallowed (debug log) so middleware never breaks the agent.
    """

    def after_model(self, state: Any, runtime: Any) -> dict:
        """Accumulate tokens after each model call. Returns empty dict (no state mutation)."""
        self._accumulate_from_state(state)
        return {}

    async def aafter_model(self, state: Any, runtime: Any) -> dict:
        """Async variant of after_model."""
        self._accumulate_from_state(state)
        return {}

    def _accumulate_from_state(self, state: Any) -> None:
        """Extract the latest assistant message content from state and count tokens.

        LangChain 1.x AgentMiddleware.after_model receives (state, runtime); the
        runtime does not carry the model response, so we read state['messages'][-1].
        """
        try:
            messages = getattr(state, "messages", None) or (state.get("messages") if isinstance(state, dict) else None)
            if not messages:
                return
            last_msg = messages[-1]
            content = getattr(last_msg, "content", None)
            if content is not None:
                accumulate_tokens(content, state)
        except Exception as exc:
            logger.debug("TokenCounterMiddleware _accumulate_from_state skipped: %s", exc)


# Module-level singleton — stage 3 create_agent will register this instance.
# Stage 2 leaves it unused (create_react_agent has no middleware param).
token_counter_middleware = TokenCounterMiddleware()
