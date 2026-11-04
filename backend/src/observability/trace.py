"""Request-scoped trace context for HTTP, SSE, tools and token records."""

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class TraceContext:
    """One logical request; all child records share its trace_id."""

    trace_id: str
    span_id: str = field(default_factory=lambda: uuid4().hex[:16])
    parent_span_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    call_id: str | None = None
    _seq: int = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq


_CURRENT: ContextVar[TraceContext | None] = ContextVar(
    "w5_trace_context", default=None,
)


def new_trace(trace_id: str | None = None) -> TraceContext:
    return TraceContext(trace_id=trace_id or uuid4().hex)


def current_trace() -> TraceContext | None:
    return _CURRENT.get()


def current_trace_id() -> str | None:
    trace = _CURRENT.get()
    return trace.trace_id if trace is not None else None


def next_seq() -> int | None:
    trace = _CURRENT.get()
    return trace.next_seq() if trace is not None else None


def set_trace(trace: TraceContext) -> Token:
    return _CURRENT.set(trace)


def reset_trace(token: Token) -> None:
    _CURRENT.reset(token)
