"""Checks that evaluation only scores a completed KB retrieval path."""

import pytest

from tests.rag_eval.chat_evidence import summarize_chat_evidence


def _events():
    return [
        {"type": "tool_call", "tool": "search_docs"},
        {"type": "source", "id": "src1", "kb_id": "kb-fixed"},
        {"type": "tool_result", "tool": "search_docs", "success": True},
        {"type": "done", "success": True},
    ]


def test_completed_kb_source_path():
    result = summarize_chat_evidence(_events(), "GPIO uses MODER [src1].")
    assert result["status"] == "verified_source_path"
    assert result["search_docs_completed"] is True


@pytest.mark.parametrize("events,answer,status", [
    (_events()[:-1], "GPIO [src1]", "request_failed"),
    (_events()[:-2] + [{"type": "tool_result", "tool": "search_docs", "success": False}, {"type": "done", "success": True}], "GPIO [src1]", "request_failed"),
    ([{"type": "done", "success": True}], "GPIO", "retrieval_not_called"),
    ([{"type": "tool_call", "tool": "search_docs"}, {"type": "tool_result", "tool": "search_docs", "success": True}, {"type": "done", "success": True}], "GPIO", "no_hits"),
    (_events(), "GPIO [src2]", "invalid_citation"),
    (_events(), "GPIO", "uncited_sources"),
    (_events() + [{"type": "source", "id": "src9", "source_url": "https://example.org"}], "GPIO [src9]", "uncited_sources"),
])
def test_unverified_paths(events, answer, status):
    assert summarize_chat_evidence(events, answer)["status"] == status


def test_explicit_model_failure_and_legacy_mode():
    events = [{"type": "error", "detail": "bind_tools unsupported"}, {"type": "done", "success": False}]
    assert summarize_chat_evidence(events, "")["status"] == "model_unsupported"
    assert summarize_chat_evidence(_events(), "GPIO [src1]", use_agent=False)["status"] == "legacy_chat"
