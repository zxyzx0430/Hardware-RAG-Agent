"""Task-local tool metadata for streaming and HITL, never checkpointed."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from src.agent.exceptions import ToolContext

_ACTIVE_TOOL_CONTEXT: ContextVar[ToolContext | None] = ContextVar("active_tool_context", default=None)


def active_tool_context() -> ToolContext | None:
    return _ACTIVE_TOOL_CONTEXT.get()


@contextmanager
def activate_tool_context(context: ToolContext | None) -> Iterator[None]:
    token = _ACTIVE_TOOL_CONTEXT.set(context)
    try:
        yield
    finally:
        _ACTIVE_TOOL_CONTEXT.reset(token)
