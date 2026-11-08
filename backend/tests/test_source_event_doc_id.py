"""Source SSE payloads expose a stable upload document ID and PDF hint."""

from __future__ import annotations

from types import SimpleNamespace

from src.agent.exceptions import ToolContext
from src.agent.tools.groups.retrieval.search_docs import (
    _build_search_result,
    build_source_event_from_dict,
)


def _result(*, title: str = "Manual.pdf", **metadata) -> SimpleNamespace:
    meta = {
        "title": title,
        "page_start": 3,
        "big_chunk_id": "parent-a",
        "small_chunks": [
            {"id": "small-a", "chunk_index": 0, "score": 0.8, "text": "GPIO"}
        ],
    }
    meta.update(metadata)
    return SimpleNamespace(
        content="Parent evidence text.",
        metadata=meta,
        score=0.8,
        doc_id="doc-a",
        kb_id="kb-a",
        kb_name="Knowledge base A",
    )


def _event(result: SimpleNamespace) -> dict:
    output = _build_search_result([result], ToolContext())
    return build_source_event_from_dict(output["results"][0], 0)


def test_kb_source_event_carries_doc_id_and_pdf_hint():
    event = _event(_result(file_type="pdf"))

    assert event["id"] == "src1"
    assert event["doc_id"] == "doc-a"
    assert event["doc"] == "doc-a"
    assert event["is_pdf"] is True
    assert event["mime_type"] == "application/pdf"
    assert event["page_start"] == 3
    assert event["excerpt"] == "GPIO"
    assert event["kb_id"] == "kb-a"


def test_file_type_falls_back_to_title_suffix():
    event = _event(_result(title="Guide.PDF"))

    assert event["doc_id"] == "doc-a"
    assert event["is_pdf"] is True
    assert event["mime_type"] == "application/pdf"


def test_non_pdf_source_is_not_marked_as_pdf():
    event = _event(_result(title="notes.md", file_type="md"))

    assert event["doc_id"] == "doc-a"
    assert event["is_pdf"] is False
    assert event["mime_type"] == ""


def test_version_like_title_does_not_invent_a_file_type():
    event = _event(_result(title="Datasheet v1.2"))

    assert event["doc_id"] == "doc-a"
    assert event["is_pdf"] is False
    assert event["mime_type"] == ""


def test_web_source_has_no_document_id():
    event = build_source_event_from_dict(
        {
            "title": "Example",
            "doc": "https://example.com/manual",
            "source_url": "https://example.com/manual",
            "category": "web",
        },
        0,
    )

    assert event["doc_id"] == ""
    assert event["is_pdf"] is False
    assert event["mime_type"] == ""
    assert event["doc"] == "https://example.com/manual"
