"""SSE encoding helpers shared by the build/upload routes."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from app.api.sse import sse_event


def sse_from_event(event: dict[str, Any]) -> str:
    """Convert a pio_runner event dict into an SSE frame."""
    event_type = event.get("type", "unknown")
    data = {key: value for key, value in event.items() if key != "type"}
    return sse_event(event_type, data)


def is_done_sse_event(event_text: str) -> bool:
    """Return whether an encoded SSE event is the terminal ``done`` event."""
    for line in event_text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            return json.loads(line[6:]).get("type") == "done"
        except (json.JSONDecodeError, AttributeError):
            return False
    return False


def error_done(code: str, message: str) -> str:
    """Build an error ``done`` SSE event."""
    return sse_event("done", {"success": False, "error": {"code": code, "message": message}})


def extract_binary_path(event: dict[str, Any]) -> str:
    """Return the binary path from a successful done event, else ''."""
    if not event.get("success"):
        return ""
    return event.get("binary_path") or ""


def build_not_found_stream() -> AsyncIterator[str]:
    """SSE error for a missing or foreign build id."""
    async def generator() -> AsyncIterator[str]:
        yield error_done("BUILD_NOT_FOUND", "build_id 不存在、已过期或不属于当前会话")
    return generator()
