"""Request-scoped tool context and graph identity helpers."""

import re
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from src.agent.exceptions import ToolContext

_ACTIVE_TOOL_CONTEXT: ContextVar[ToolContext | None] = ContextVar("active_tool_context", default=None)

REQUEST_GRAPH_BINDING_KEY = "hwrag_request_graph_binding"
REQUEST_GRAPH_THREAD_PREFIX = "hwrag-request:"
_REQUEST_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_REQUEST_GRAPH_BINDING_FIELDS = frozenset({
    "business_session_id", "request_id", "graph_thread_id",
})


class RequestGraphBindingError(ValueError):
    """Raised when a request graph config cannot be tied to its business session."""


@dataclass(frozen=True, slots=True)
class RequestGraphBinding:
    business_session_id: str
    request_id: str
    graph_thread_id: str

    def as_config_value(self) -> dict[str, str]:
        return {
            "business_session_id": self.business_session_id,
            "request_id": self.request_id,
            "graph_thread_id": self.graph_thread_id,
        }


def new_request_graph_binding(session_id: str, request_id: str) -> RequestGraphBinding:
    """Bind a host-minted request ID to its business session and graph thread.

    The ID format is checked for consistency only; it is not authentication or
    authorization. Callers must supply an ID created by trusted host code.
    """
    if (
        not isinstance(session_id, str)
        or not 1 <= len(session_id) <= 128
        or not session_id.strip()
        or session_id != session_id.strip()
        or session_id.startswith(REQUEST_GRAPH_THREAD_PREFIX)
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
    ):
        raise RequestGraphBindingError("Invalid business session for request graph")
    if not isinstance(request_id, str) or _REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        raise RequestGraphBindingError("Invalid host request ID for request graph")
    return RequestGraphBinding(
        business_session_id=session_id,
        request_id=request_id,
        graph_thread_id=f"{REQUEST_GRAPH_THREAD_PREFIX}{request_id}",
    )


def request_graph_binding_from_config(config: Any) -> RequestGraphBinding | None:
    """Parse and strictly validate the optional request graph binding."""
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None

    thread_id = configurable.get("thread_id")
    if REQUEST_GRAPH_BINDING_KEY not in configurable:
        if isinstance(thread_id, str) and thread_id.startswith(REQUEST_GRAPH_THREAD_PREFIX):
            raise RequestGraphBindingError("Request graph config is missing its binding")
        return None

    raw = configurable[REQUEST_GRAPH_BINDING_KEY]
    if not isinstance(raw, Mapping) or set(raw) != _REQUEST_GRAPH_BINDING_FIELDS:
        raise RequestGraphBindingError("Invalid request graph binding fields")
    binding = new_request_graph_binding(
        raw.get("business_session_id"),
        raw.get("request_id"),
    )
    if (
        raw.get("graph_thread_id") != binding.graph_thread_id
        or thread_id != binding.graph_thread_id
    ):
        raise RequestGraphBindingError("Request graph binding does not match thread ID")
    return binding


def business_session_id_from_config(config: Any) -> str | None:
    """Return the business session, rejecting malformed request graph configs."""
    binding = request_graph_binding_from_config(config)
    if binding is not None:
        return binding.business_session_id

    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        return None
    if thread_id.startswith(REQUEST_GRAPH_THREAD_PREFIX):
        raise RequestGraphBindingError("Request graph config cannot be used as a business session")
    return thread_id


def active_tool_context() -> ToolContext | None:
    return _ACTIVE_TOOL_CONTEXT.get()


@contextmanager
def activate_tool_context(context: ToolContext | None) -> Iterator[None]:
    token = _ACTIVE_TOOL_CONTEXT.set(context)
    try:
        yield
    finally:
        _ACTIVE_TOOL_CONTEXT.reset(token)
