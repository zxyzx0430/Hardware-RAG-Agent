"""Microcompact: drop old tool_result contents to bound context growth.

Pure in-memory operation, no LLM call. Called every round after tool calls
finish. Replaces ToolMessage content older than keep_recent rounds with a
short placeholder so the accumulated message list stays lean.

P1-T5: first layer of the layered compact mechanism (autocompact is the
second layer, invoked only when microcompact alone cannot keep the context
under the token threshold).
"""
from __future__ import annotations

import logging

from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

# Keep this many recent tool_results untouched; older ones get compressed.
MICROCOMPACT_KEEP_RECENT: int = 5

# Placeholder template for compressed tool results.
_COMPRESSED_PLACEHOLDER: str = "[已压缩：{name}]"


def compact_old_tool_results(
    messages: list, keep_recent: int = MICROCOMPACT_KEEP_RECENT,
) -> list:
    """Replace old ToolMessage contents with a short placeholder.

    Leaves the most recent `keep_recent` ToolMessages as-is; older ones have
    their content replaced by "[已压缩：tool_name]". Mutates in place and
    returns the same list for fluent chaining.
    """
    tool_indices = _find_tool_indices(messages)
    stale = _select_stale_indices(tool_indices, keep_recent)
    if not stale:
        return messages
    for idx in stale:
        _compress_one(messages[idx])
    logger.debug(
        "microcompact compressed=%s kept=%s",
        len(stale), len(tool_indices) - len(stale),
    )
    return messages


def _find_tool_indices(messages: list) -> list[int]:
    """Return indices of all ToolMessage entries in the list."""
    return [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]


def _select_stale_indices(tool_indices: list[int], keep_recent: int) -> list[int]:
    """Return indices of ToolMessages older than keep_recent."""
    if keep_recent <= 0:
        return tool_indices
    if len(tool_indices) <= keep_recent:
        return []
    return tool_indices[:-keep_recent]


def _compress_one(msg: ToolMessage) -> None:
    """Replace a ToolMessage's content with a short placeholder."""
    name = msg.name or msg.tool_call_id or "tool"
    msg.content = _COMPRESSED_PLACEHOLDER.format(name=name)
