"""RedactPlugin — masks API keys in tool results.

Scans result['output'] and string values in result['data'] for the common
API key pattern (``sk-...``) and replaces matches with ``[REDACTED]``.
High priority (10) so it runs before other post-processing plugins.

Spec: P1 task 8.
"""
from __future__ import annotations

import re
from typing import Any

from src.agent.plugins.base import Plugin

# Pre-compiled for performance; matches OpenAI-style keys sk- + 20+ chars.
API_KEY_PATTERN: str = r"sk-[a-zA-Z0-9\-_]{20,}"
REDACTED_TEXT: str = "[REDACTED]"
_COMPILED_KEY_RE: re.Pattern[str] = re.compile(API_KEY_PATTERN)

# Fields in a tool result dict that may carry sensitive text.
_OUTPUT_FIELD: str = "output"
_DATA_FIELD: str = "data"


class RedactPlugin(Plugin):
    """Masks API keys (sk-...) in tool result output and data."""

    name = "redact"
    priority = 10

    async def post_tool_result(self, tool: str, result: dict[str, Any]) -> dict[str, Any]:
        """Replace sk-... patterns with [REDACTED] in output + data fields."""
        _redact_output(result)
        _redact_data(result)
        return result


def _redact_output(result: dict[str, Any]) -> None:
    """Redact API keys in the ``output`` string field (in place)."""
    value = result.get(_OUTPUT_FIELD)
    if isinstance(value, str):
        result[_OUTPUT_FIELD] = _COMPILED_KEY_RE.sub(REDACTED_TEXT, value)


def _redact_data(result: dict[str, Any]) -> None:
    """Redact API keys in string values nested under ``data``."""
    data = result.get(_DATA_FIELD)
    if isinstance(data, dict):
        result[_DATA_FIELD] = {k: _redact_value(v) for k, v in data.items()}
    elif isinstance(data, str):
        result[_DATA_FIELD] = _COMPILED_KEY_RE.sub(REDACTED_TEXT, data)


def _redact_value(value: Any) -> Any:
    """Redact a single value if it is a string."""
    if isinstance(value, str):
        return _COMPILED_KEY_RE.sub(REDACTED_TEXT, value)
    return value


__all__ = ["RedactPlugin", "API_KEY_PATTERN", "REDACTED_TEXT"]
