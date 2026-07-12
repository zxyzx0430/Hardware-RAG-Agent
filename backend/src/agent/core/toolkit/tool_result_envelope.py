"""ToolResultEnvelope — unified tool return format (Pydantic models).

ToolResultEnvelope wraps tool execution results.

The full envelope (output + data + metadata) is serialized into
ToolMessage.content for the LLM to read. This is intentional for
ParentDocument retrieval: the LLM needs big_chunk text in data.results
to generate grounded answers with [srcN] citations.

The same envelope is also shipped to the frontend via the SSE
tool_result event for UI rendering.

Spec: industrial-tool-runtime §ADDED Requirements (ToolResultEnvelope).
"""
from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Structured error info attached when a tool call fails.

    error_type is one of the ToolRouter error tags:
    INVALID_ARGS / TIMEOUT / EXEC_ERROR / TOOL_NOT_FOUND /
    OUTPUT_SCHEMA_VIOLATION / LOOP_DETECTED.
    """
    error_type: str
    error_message: str = ""
    suggestion: str = ""
    retryable: bool = False


class ResultMetadata(BaseModel):
    """Per-call metadata attached to every tool result.

    `kb_coverage_hint` is populated by search_docs (via ToolContext counter)
    when consecutive low-relevance calls cross the threshold.
    """
    tool_name: str
    duration_ms: int = 0
    call_id: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    kb_coverage_hint: str | None = None
    # Set to True by ToolRouter when envelope.output was truncated to
    # OUTPUT_TRUNCATE_THRESHOLD (full payload preserved in `data`).
    compacted: bool = False


class ToolResultEnvelope(BaseModel):
    """Unified tool result format returned by ToolRouter.dispatch.

    `model_dump()` is the dict that flows into ToolMessage.content (minus
    `data`) and into the SSE tool_result event (with `data`).
    """
    success: bool
    output: str = ""
    data: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    metadata: ResultMetadata
