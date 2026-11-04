"""SSE protocol helpers — build data frames with optional trace metadata."""

import json

from src.observability.trace import current_trace


def sse_event(event_type: str, data: dict) -> str:
    """Build one SSE frame; adds id/seq/trace_id only inside a trace context."""
    payload = {"type": event_type, **data}
    trace = current_trace()
    if trace is None:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    seq = trace.next_seq()
    payload.setdefault("trace_id", trace.trace_id)
    payload.setdefault("seq", seq)
    body = json.dumps(payload, ensure_ascii=False)
    return f"id: {trace.trace_id}:{seq}\ndata: {body}\n\n"
