"""Small, deterministic checks for the chat path used by RAG evaluations."""

from __future__ import annotations

import re
from collections import Counter


_CITATION_RE = re.compile(r"\[src(\d+)\]", re.IGNORECASE)
_UNSUPPORTED_TOOL_HINTS = ("bind_tools", "tool calling not supported", "tools are not supported")
_CONFIRMATION_STATES = {"tool_confirm", "tool_confirm_required", "awaiting_confirmation"}
_PARENT_METADATA_FIELDS = (
    "title", "doc", "page", "pages", "page_start", "page_end", "section_title",
    "chunk_index", "score", "kb_id", "kb_name", "big_chunk_id", "small_chunk_id",
)


def extract_agent_context(events: list[dict], source_events: list[dict]) -> tuple[list[str], str, list[dict]]:
    """Capture full successful search_docs parent context and safe metadata.

    Source excerpts are retained only as diagnostic input. They never stand in
    for the full context that was passed to the Agent/model.
    """
    kb_source_ids = {
        str(event.get("id")) for event in source_events
        if event.get("kb_id") and event.get("id")
    }
    excerpts = [
        event["excerpt"] for event in source_events
        if isinstance(event.get("excerpt"), str) and event["excerpt"]
    ]
    contexts: list[str] = []
    context_ids: set[str] = set()
    metadata: list[dict] = []
    saw_search_result = False

    for event in events:
        if event.get("type") != "tool_result" or event.get("tool") != "search_docs":
            continue
        saw_search_result = True
        if event.get("success") is not True:
            continue
        envelope = event.get("result")
        if not isinstance(envelope, dict) or envelope.get("success") is not True:
            continue
        data = envelope.get("data")
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = row.get("id")
            content = row.get("content")
            if not isinstance(source_id, str) or not source_id:
                continue
            if not isinstance(content, str) or not content:
                continue
            contexts.append(content)
            context_ids.add(source_id)
            metadata.append({
                key: row[key] for key in ("id", *_PARENT_METADATA_FIELDS)
                if row.get(key) is not None
            })

    if kb_source_ids and kb_source_ids.issubset(context_ids):
        status = "complete"
    elif contexts:
        status = "incomplete_parent_context"
    elif excerpts:
        contexts = excerpts
        status = "excerpt_only"
    elif saw_search_result:
        status = "no_context"
    else:
        status = "missing_tool_result"
    return contexts, status, metadata


def summarize_chat_evidence(events: list[dict], answer: str, use_agent: bool = True) -> dict:
    """Classify retrieval evidence without using model-judged quality scores."""
    source_ids = {
        str(event.get("id")) for event in events
        if event.get("type") == "source" and event.get("id")
    }
    kb_source_ids = {
        str(event.get("id")) for event in events
        if event.get("type") == "source" and event.get("kb_id") and event.get("id")
    }
    cited_ids = {f"src{n}" for n in _CITATION_RE.findall(answer)}
    invalid_citations = sorted(cited_ids - source_ids)
    search_calls = [
        event for event in events
        if event.get("type") in ("tool_call", "tool_result", "tool")
        and (event.get("tool") or event.get("name")) == "search_docs"
    ]
    failed_search = any(
        event.get("type") == "tool_result" and event.get("success") is False
        for event in search_calls
    )
    errors = [event for event in events if event.get("type") == "error"]
    confirmation_pending = any(
        str(event.get("type", "")).lower() in _CONFIRMATION_STATES
        or any(str(event.get(field, "")).lower() in _CONFIRMATION_STATES
               for field in ("status", "state", "terminal_status", "terminal_state"))
        or event.get("awaiting_confirmation") is True
        or (
            event.get("type") == "done"
            and event.get("success") is True
            and event.get("completed") is False
        )
        for event in events
    )
    done_failed = any(
        event.get("type") == "done"
        and (event.get("success") is False or event.get("completed") is False)
        for event in events
    )
    # Older stored streams omit `completed`; keep them readable while the new
    # protocol requires completed != false and no pending confirmation.
    done_succeeded = any(
        event.get("type") == "done"
        and event.get("success") is True
        and event.get("completed") is not False
        and event.get("awaiting_confirmation") is not True
        for event in events
    ) and not confirmation_pending
    search_completed = any(
        event.get("type") == "tool_result" and event.get("success") is True
        for event in search_calls
    )
    error_details = " ".join(
        str(event.get("detail") or event.get("message") or "") for event in errors
    ).lower()

    if not use_agent:
        status = "legacy_chat"
    elif any(hint in error_details for hint in _UNSUPPORTED_TOOL_HINTS):
        status = "model_unsupported"
    elif confirmation_pending:
        status = "awaiting_confirmation"
    elif errors or done_failed or failed_search or not done_succeeded:
        status = "request_failed"
    elif not search_calls:
        status = "retrieval_not_called"
    elif not search_completed:
        status = "request_failed"
    elif not kb_source_ids:
        status = "no_hits"
    elif invalid_citations:
        status = "invalid_citation"
    elif not cited_ids:
        status = "uncited_sources"
    elif not cited_ids.intersection(kb_source_ids):
        status = "uncited_sources"
    else:
        status = "verified_source_path"

    return {
        "status": status,
        "search_docs_called": bool(search_calls),
        "search_docs_completed": search_completed,
        "done_succeeded": done_succeeded,
        "source_count": len(source_ids),
        "kb_source_count": len(kb_source_ids),
        "cited_ids": sorted(cited_ids),
        "invalid_citation_ids": invalid_citations,
    }


def count_evidence_statuses(records: list[dict]) -> dict[str, int]:
    """Return stable status counts, including the evaluation denominator."""
    return dict(sorted(Counter(record["status"] for record in records).items()))
