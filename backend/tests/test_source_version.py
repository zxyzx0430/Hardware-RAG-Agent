"""Tests for pure source-version classification."""

import hashlib

import pytest

from src.rag.source_version import classify_source_version


_IDENTITY = {
    "kb_id": "kb-a",
    "doc_id": "doc-a",
    "big_chunk_id": "doc-a#b0",
    "small_chunk_id": "doc-a#s0",
}


def _version(content: str, small_text: str) -> dict:
    return {
        "schema_version": 1,
        **_IDENTITY,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "small_chunk_text_sha256": hashlib.sha256(
            small_text.encode("utf-8")
        ).hexdigest(),
    }


def _arguments(content: str, small_text: str) -> dict:
    return {
        "source_version": _version(content, small_text),
        "saved_kb_id": _IDENTITY["kb_id"],
        "saved_doc_id": _IDENTITY["doc_id"],
        "saved_big_chunk_id": _IDENTITY["big_chunk_id"],
        "saved_small_chunk_id": _IDENTITY["small_chunk_id"],
        "current_status": "indexed",
        "current_kb_id": _IDENTITY["kb_id"],
        "current_doc_id": _IDENTITY["doc_id"],
        "current_big_chunk_id": _IDENTITY["big_chunk_id"],
        "current_small_chunk_id": _IDENTITY["small_chunk_id"],
        "current_content": content,
        "current_small_chunk_text": small_text,
    }


def test_matching_unicode_truncated_display_text_is_current() -> None:
    raw_content = "寄存器配置" * 12
    displayed_content = raw_content[:8] + "...[chunk truncated]"
    small_text = "GPIO 输入模式。"
    assert displayed_content != raw_content

    assert classify_source_version(
        **_arguments(displayed_content, small_text)
    ) == "current"
    assert hashlib.sha256(displayed_content.encode("utf-8")).hexdigest() == _version(
        displayed_content, small_text
    )["content_sha256"]
    raw_text_arguments = _arguments(displayed_content, small_text)
    raw_text_arguments["current_content"] = raw_content
    assert classify_source_version(**raw_text_arguments) == "changed"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("current_content", "changed parent text"),
        ("current_small_chunk_text", "changed small chunk text"),
    ],
)
def test_comparable_text_hash_mismatch_is_changed(field: str, replacement: str) -> None:
    arguments = _arguments("parent text", "small text")
    arguments[field] = replacement

    assert classify_source_version(**arguments) == "changed"


def test_missing_document_row_is_unavailable() -> None:
    arguments = _arguments("parent text", "small text")
    arguments["current_status"] = None

    assert classify_source_version(**arguments) == "unavailable"


def test_same_document_indexing_is_reported_without_rebinding() -> None:
    arguments = _arguments("parent text", "small text")
    arguments.update(
        current_status="indexing",
        current_big_chunk_id=None,
        current_small_chunk_id=None,
        current_content=None,
        current_small_chunk_text=None,
    )

    assert classify_source_version(**arguments) == "indexing"
    arguments["current_doc_id"] = "new-doc"
    assert classify_source_version(**arguments) == "unverifiable"


@pytest.mark.parametrize("status", ["error", "paused", "", 1])
def test_error_or_unknown_document_status_is_unverifiable(status: object) -> None:
    arguments = _arguments("parent text", "small text")
    arguments["current_status"] = status

    assert classify_source_version(**arguments) == "unverifiable"


def test_identity_mismatch_cannot_rebind_to_another_document_or_chunk() -> None:
    arguments = _arguments("parent text", "small text")
    arguments["current_doc_id"] = "new-doc"
    assert classify_source_version(**arguments) == "unverifiable"

    arguments = _arguments("parent text", "small text")
    arguments["current_small_chunk_id"] = "doc-a#s1"
    assert classify_source_version(**arguments) == "unverifiable"

    arguments = _arguments("parent text", "small text")
    arguments["saved_doc_id"] = "new-doc"
    assert classify_source_version(**arguments) == "unverifiable"


@pytest.mark.parametrize(
    "source_version",
    [
        None,
        {},
        {"schema_version": 0},
        {"schema_version": 1, **_IDENTITY, "content_sha256": "bad"},
    ],
)
def test_missing_or_legacy_version_fields_are_unverifiable(
    source_version: object,
) -> None:
    arguments = _arguments("parent text", "small text")
    arguments["source_version"] = source_version

    assert classify_source_version(**arguments) == "unverifiable"


def test_incomplete_identity_or_evidence_text_is_unverifiable() -> None:
    arguments = _arguments("parent text", "small text")
    arguments["saved_small_chunk_id"] = ""
    assert classify_source_version(**arguments) == "unverifiable"

    arguments = _arguments("parent text", "small text")
    arguments["current_small_chunk_text"] = None
    assert classify_source_version(**arguments) == "unverifiable"


def test_utf8_encoding_failure_is_unverifiable() -> None:
    arguments = _arguments("parent text", "small text")
    arguments["current_content"] = "\ud800"

    assert classify_source_version(**arguments) == "unverifiable"
