"""SSE 协议工具 — 构造 data 行。"""

import json


def sse_event(event_type: str, data: dict) -> str:
    """构造 SSE data 行。"""
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
