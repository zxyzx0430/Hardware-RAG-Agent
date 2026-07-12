"""Large-field truncation helpers for SSE events.

Extracted from sse_adapter.py to keep that file under the line cap.
Prevents base64 image data and other oversized string values from
bloating tool_call / tool_result SSE payloads sent to the frontend.

Spec: add-grep-glob-todo-tools §Task 14 (extracted to keep sse_adapter ≤350 lines).
"""
from __future__ import annotations

import json
from typing import Any

# Max byte size for a single field value in tool_call/tool_result SSE events;
# larger values (e.g. base64 image data) are replaced with a truncated summary.
_MAX_FIELD_BYTES: int = 1024


def _truncate_large_fields(data: dict, max_bytes: int = _MAX_FIELD_BYTES) -> dict:
    """Return a copy of data with oversized string values replaced by summaries."""
    return {k: _shrink_value(v, max_bytes) for k, v in data.items()}


def _shrink_value(v: Any, max_bytes: int) -> Any:
    """Recursively shrink large strings inside dict/list/str values."""
    if isinstance(v, str):
        return _shrink_str(v, max_bytes)
    if isinstance(v, dict):
        return _truncate_large_fields(v, max_bytes)
    if isinstance(v, list):
        return [_shrink_value(i, max_bytes) for i in v]
    return v


def _shrink_str(s: str, max_bytes: int) -> str:
    """Replace a string exceeding max_bytes with a truncated summary."""
    size = len(s.encode("utf-8"))
    if size <= max_bytes:
        return s
    kind = "base64 image" if s.startswith("data:") else "text"
    return f"[truncated: {kind}, {size // 1024}KB]"


def _truncate_sse_payload(sse: str) -> str:
    """Truncate large fields in a tool_result SSE data line."""
    prefix = "data: "
    if not sse.startswith(prefix):
        return sse
    try:
        payload = json.loads(sse[len(prefix):].strip())
    except json.JSONDecodeError:
        return sse
    if payload.get("type") != "tool_result":
        return sse
    payload = _shrink_value(payload, _MAX_FIELD_BYTES)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
