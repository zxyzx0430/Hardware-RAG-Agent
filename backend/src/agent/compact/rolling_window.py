"""Pure, fail-closed planning for bounded rolling-history summaries.

The host supplies a contiguous, already-validated history suffix. This module
only describes source ranges and bounded text slices; it does not read storage,
call a model, publish a summary, or carry authorization state.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from src.llm.client import LLMClient


_CHUNK_FRAMING_TOKENS = 32
_RECORD_FRAMING_TOKENS = 16


class RollingWindowPlanningError(ValueError):
    """The requested window cannot be planned without losing or misbinding data."""


@dataclass(frozen=True, slots=True)
class SequencedMessage:
    """One host-validated message and its immutable, one-based history sequence."""

    sequence: int
    message: BaseMessage


@dataclass(frozen=True, slots=True)
class SourceRange:
    """Inclusive sequence range. Ranges describe source records, not permission."""

    first_sequence: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class SummaryTextSlice:
    """A lossless UTF-8 byte slice plus the message/tool identity needed to ground it."""

    sequence: int
    role: str
    text: str
    start_byte: int
    end_byte: int
    tool_calls_json: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_status: str | None = None
    paired_call_sequence: int | None = None
    paired_tool_call_json: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryChunk:
    """One bounded summary input payload; concatenate each source's slices to recover it."""

    source_range: SourceRange
    parts: tuple[SummaryTextSlice, ...]
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class RollingWindowPlan:
    """Pure partition of history into prior coverage, new summary, and raw tail."""

    already_covered_through: int
    candidate_range: SourceRange | None
    retained_range: SourceRange | None
    next_covered_through: int
    summary_chunks: tuple[SummaryChunk, ...]
    history_estimated_tokens: int
    retained_estimated_tokens: int


@dataclass(frozen=True, slots=True)
class _ToolCall:
    sequence: int
    encoded: str


@dataclass(frozen=True, slots=True)
class _SummaryRecord:
    text: str
    metadata: SummaryTextSlice
    metadata_tokens: int


def plan_rolling_window(
    messages: Sequence[SequencedMessage],
    *,
    covered_through: int,
    current_user_sequence: int,
    history_budget_tokens: int,
    recent_target_tokens: int,
    summary_chunk_budget_tokens: int,
) -> RollingWindowPlan:
    """Plan a contiguous suffix without summarizing the current user or splitting tool pairs.

    ``covered_through`` is the prior inclusive cursor. ``messages`` must contain
    every subsequent sequence, beginning at cursor + 1, with no filtered or
    missing records. History and chunk estimates use the project's
    ``LLMClient._estimate_tokens`` definition. Each chunk budget covers its
    estimated text/metadata and local framing; callers must reserve their own
    provider prompt and output capacity before sending any chunk.
    History totals also add positive framing and serialized AI tool-call /
    ToolMessage identity metadata; provider tool schemas remain caller-reserved.
    """
    if type(covered_through) is not int or covered_through < 0:
        raise RollingWindowPlanningError("Invalid prior coverage cursor")
    if (type(history_budget_tokens) is not int or history_budget_tokens <= 0
            or type(recent_target_tokens) is not int or recent_target_tokens < 0
            or recent_target_tokens >= history_budget_tokens
            or type(summary_chunk_budget_tokens) is not int
            or summary_chunk_budget_tokens <= _CHUNK_FRAMING_TOKENS
            or summary_chunk_budget_tokens > history_budget_tokens):
        raise RollingWindowPlanningError("Invalid rolling-window token budgets")
    if type(messages) not in (list, tuple) or not messages:
        raise RollingWindowPlanningError("A nonempty complete history suffix is required")

    items: list[SequencedMessage] = []
    token_counts: list[int] = []
    expected_sequence = covered_through + 1
    current_user_index: int | None = None
    latest_user_sequence: int | None = None
    for item in messages:
        if (type(item) is not SequencedMessage or type(item.sequence) is not int
                or item.sequence != expected_sequence
                or not isinstance(item.message, BaseMessage)):
            raise RollingWindowPlanningError("History must be a contiguous sequenced message suffix")
        content = item.message.content
        if type(content) not in (str, list, type(None)):
            raise RollingWindowPlanningError("Unsupported message content shape")
        try:
            content_tokens = LLMClient._estimate_tokens(content)
            metadata_tokens = _RECORD_FRAMING_TOKENS + LLMClient._estimate_tokens(
                _stable_json(_visible_message_metadata(item.message))
            )
        except Exception as exc:
            raise RollingWindowPlanningError("Message or tool metadata estimate is unavailable") from exc
        if (type(content_tokens) is not int or content_tokens < 0
                or type(metadata_tokens) is not int or metadata_tokens < _RECORD_FRAMING_TOKENS):
            raise RollingWindowPlanningError("Message token estimate is invalid")
        cost = content_tokens + metadata_tokens
        items.append(item)
        token_counts.append(cost)
        if isinstance(item.message, HumanMessage):
            latest_user_sequence = item.sequence
            if item.sequence == current_user_sequence:
                current_user_index = len(items) - 1
        expected_sequence += 1

    if (type(current_user_sequence) is not int or current_user_index is None
            or latest_user_sequence != current_user_sequence):
        raise RollingWindowPlanningError("Current user message is missing or is not the latest user message")

    safe_cuts, result_calls = _validate_tool_pairs(items)
    total_tokens = sum(token_counts)
    full_range = SourceRange(items[0].sequence, items[-1].sequence)
    if total_tokens <= history_budget_tokens:
        return RollingWindowPlan(
            already_covered_through=covered_through,
            candidate_range=None,
            retained_range=full_range,
            next_covered_through=covered_through,
            summary_chunks=(),
            history_estimated_tokens=total_tokens,
            retained_estimated_tokens=total_tokens,
        )

    tail_start = _recent_tail_start(token_counts, current_user_index, recent_target_tokens)
    safe_cut = max((cut for cut in safe_cuts if 0 < cut <= tail_start), default=0)
    if safe_cut == 0:
        raise RollingWindowPlanningError("No complete pre-tail history group can be summarized")
    retained_tokens = sum(token_counts[safe_cut:])
    if retained_tokens > history_budget_tokens:
        raise RollingWindowPlanningError("Recent tail alone exceeds the available history budget")

    candidate = items[:safe_cut]
    chunks = _build_summary_chunks(candidate, result_calls, summary_chunk_budget_tokens)
    candidate_range = SourceRange(candidate[0].sequence, candidate[-1].sequence)
    retained_range = SourceRange(items[safe_cut].sequence, items[-1].sequence)
    return RollingWindowPlan(
        already_covered_through=covered_through,
        candidate_range=candidate_range,
        retained_range=retained_range,
        next_covered_through=candidate[-1].sequence,
        summary_chunks=chunks,
        history_estimated_tokens=total_tokens,
        retained_estimated_tokens=retained_tokens,
    )


def _validate_tool_pairs(
    messages: Sequence[SequencedMessage],
) -> tuple[list[int], dict[int, _ToolCall]]:
    """Return complete-group cut points and the exact call paired to each result."""
    pending: dict[str, _ToolCall] = {}
    seen_ids: set[str] = set()
    result_calls: dict[int, _ToolCall] = {}
    safe_cuts = [0]
    for index, item in enumerate(messages):
        message = item.message
        if isinstance(message, AIMessage):
            if message.invalid_tool_calls:
                raise RollingWindowPlanningError("Unparsed AI tool call in history")
            calls = message.tool_calls
            if type(calls) is not list:
                raise RollingWindowPlanningError("Malformed AI tool-call list")
            for call in calls:
                if (type(call) is not dict or type(call.get("id")) is not str
                        or not call["id"] or type(call.get("name")) is not str
                        or not call["name"] or type(call.get("args")) is not dict):
                    raise RollingWindowPlanningError("Malformed AI tool call")
                call_id = call["id"]
                if call_id in seen_ids:
                    raise RollingWindowPlanningError("Duplicate AI tool-call ID")
                encoded = _stable_json(call)
                pending[call_id] = _ToolCall(item.sequence, encoded)
                seen_ids.add(call_id)
        elif isinstance(message, ToolMessage):
            call_id = message.tool_call_id
            if type(call_id) is not str or not call_id or call_id not in pending:
                raise RollingWindowPlanningError("Tool result has no unique preceding AI tool call")
            result_calls[item.sequence] = pending.pop(call_id)
        if not pending:
            safe_cuts.append(index + 1)
    if pending:
        raise RollingWindowPlanningError("Unclosed AI tool call in history")
    return safe_cuts, result_calls


def _recent_tail_start(token_counts: Sequence[int], current_index: int, target: int) -> int:
    start = current_index
    accumulated = sum(token_counts[current_index:])
    while start > 0 and accumulated < target:
        start -= 1
        accumulated += token_counts[start]
    return start


def _visible_message_metadata(message: BaseMessage) -> dict[str, object]:
    """Return message routing fields that accompany content in model payloads."""
    metadata: dict[str, object] = {"role": message.type}
    if isinstance(message, AIMessage):
        calls = message.tool_calls
        if type(calls) is not list:
            raise RollingWindowPlanningError("Malformed AI tool-call list")
        if calls:
            # Include every call ID, name, argument and other serialized field.
            metadata["tool_calls"] = calls
    elif isinstance(message, ToolMessage):
        metadata.update({
            "tool_call_id": message.tool_call_id,
            "name": message.name,
            "status": message.status,
        })
    return metadata


def _build_summary_chunks(
    messages: Sequence[SequencedMessage],
    result_calls: dict[int, _ToolCall],
    budget: int,
) -> tuple[SummaryChunk, ...]:
    records = [_summary_record(item, result_calls.get(item.sequence)) for item in messages]
    chunks: list[SummaryChunk] = []
    current: list[SummaryTextSlice] = []
    current_tokens = _CHUNK_FRAMING_TOKENS

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        chunks.append(SummaryChunk(
            source_range=SourceRange(
                min(part.sequence for part in current),
                max(part.sequence for part in current),
            ),
            parts=tuple(current),
            estimated_tokens=current_tokens,
        ))
        current = []
        current_tokens = _CHUNK_FRAMING_TOKENS

    for record in records:
        base_cost = record.metadata_tokens
        if base_cost + _CHUNK_FRAMING_TOKENS > budget:
            raise RollingWindowPlanningError("A complete message/tool identity exceeds the summary chunk budget")
        if not record.text:
            if current_tokens + base_cost > budget:
                flush()
            current.append(record.metadata)
            current_tokens += base_cost
            continue

        text_offset = 0
        byte_offset = 0
        while text_offset < len(record.text):
            available = budget - current_tokens - base_cost
            if available <= 0:
                flush()
                available = budget - current_tokens - base_cost
            if available <= 0:
                raise RollingWindowPlanningError("No text from a complete message fits the summary chunk budget")
            suffix = record.text[text_offset:]
            take = _largest_prefix_chars(suffix, available)
            if take == 0:
                if current:
                    flush()
                    continue
                raise RollingWindowPlanningError("A text character exceeds the summary chunk budget")
            piece = suffix[:take]
            piece_bytes = len(piece.encode("utf-8"))
            piece_tokens = LLMClient._estimate_tokens(piece)
            metadata = record.metadata
            part = SummaryTextSlice(
                sequence=metadata.sequence,
                role=metadata.role,
                text=piece,
                start_byte=byte_offset,
                end_byte=byte_offset + piece_bytes,
                tool_calls_json=metadata.tool_calls_json,
                tool_call_id=metadata.tool_call_id,
                tool_name=metadata.tool_name,
                tool_status=metadata.tool_status,
                paired_call_sequence=metadata.paired_call_sequence,
                paired_tool_call_json=metadata.paired_tool_call_json,
            )
            current.append(part)
            current_tokens += base_cost + piece_tokens
            text_offset += take
            byte_offset += piece_bytes
            if text_offset < len(record.text):
                flush()
    flush()
    return tuple(chunks)


def _summary_record(item: SequencedMessage, paired_call: _ToolCall | None) -> _SummaryRecord:
    message = item.message
    content = message.content
    if type(content) is not str:
        raise RollingWindowPlanningError("Non-text candidate message cannot be summarized losslessly")
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RollingWindowPlanningError("Candidate text is not valid UTF-8") from exc

    role = message.type
    if type(role) is not str or not role:
        raise RollingWindowPlanningError("Message role is unavailable")
    tool_calls_json = None
    tool_call_id = None
    tool_name = None
    tool_status = None
    paired_call_sequence = None
    paired_tool_call_json = None
    if isinstance(message, AIMessage) and message.tool_calls:
        tool_calls_json = _stable_json(message.tool_calls)
    if isinstance(message, ToolMessage):
        tool_call_id = message.tool_call_id
        tool_name = message.name if type(message.name) is str else None
        tool_status = message.status if type(message.status) is str else None
        if paired_call is None:
            raise RollingWindowPlanningError("Tool result lost its validated call pairing")
        paired_call_sequence = paired_call.sequence
        paired_tool_call_json = paired_call.encoded

    metadata = SummaryTextSlice(
        sequence=item.sequence,
        role=role,
        text=content,
        start_byte=0,
        end_byte=len(content.encode("utf-8")),
        tool_calls_json=tool_calls_json,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_status=tool_status,
        paired_call_sequence=paired_call_sequence,
        paired_tool_call_json=paired_tool_call_json,
    )
    metadata_text = _stable_json({
        "sequence": item.sequence,
        "role": role,
        "tool_calls_json": tool_calls_json,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "tool_status": tool_status,
        "paired_call_sequence": paired_call_sequence,
        "paired_tool_call_json": paired_tool_call_json,
    })
    metadata_tokens = _RECORD_FRAMING_TOKENS + LLMClient._estimate_tokens(metadata_text)
    return _SummaryRecord(content, metadata, metadata_tokens)


def _largest_prefix_chars(text: str, token_budget: int) -> int:
    """Find the longest nonempty code-point prefix within the real local estimate."""
    low, high = 1, len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if LLMClient._estimate_tokens(text[:middle]) <= token_budget:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _stable_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RollingWindowPlanningError("Message/tool metadata is not stable JSON") from exc
