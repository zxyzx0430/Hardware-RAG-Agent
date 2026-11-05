"""Pure validation of saved SearchDocs evidence against optional current data.

This module performs no storage access. Callers provide the document state,
exact identities, and text already prepared with SearchDocs display rules.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal, TypeAlias

SourceVersionState: TypeAlias = Literal[
    "current", "changed", "indexing", "unavailable", "unverifiable",
]

_SCHEMA_VERSION = 1
_IDENTITY_FIELDS = ("kb_id", "doc_id", "big_chunk_id", "small_chunk_id")
_DIGEST_FIELDS = ("content_sha256", "small_chunk_text_sha256")
_HEX_DIGITS = frozenset("0123456789abcdef")


def classify_source_version(
    source_version: object,
    *,
    saved_kb_id: object,
    saved_doc_id: object,
    saved_big_chunk_id: object,
    saved_small_chunk_id: object,
    current_status: object,
    current_kb_id: object = None,
    current_doc_id: object = None,
    current_big_chunk_id: object = None,
    current_small_chunk_id: object = None,
    current_content: object = None,
    current_small_chunk_text: object = None,
) -> SourceVersionState:
    """Classify one saved source reference without reading or mutating storage.

    A None current_status means the exact saved document row is missing.
    For an indexed row, current identity fields must describe the exact resolved
    KB, document, and chunk IDs. Current text fields must already use the same
    display/truncation rules as SearchDocs; this function hashes them verbatim.

    A matching result proves only that these presented text fields match their
    saved SHA-256 values. It does not prove an indexing generation, original
    upload identity, or the contents hidden by truncation.
    """
    try:
        if not isinstance(source_version, Mapping):
            return "unverifiable"
        if (
            type(source_version.get("schema_version")) is not int
            or source_version.get("schema_version") != _SCHEMA_VERSION
        ):
            return "unverifiable"

        saved_identity = _identity_tuple({
            "kb_id": saved_kb_id,
            "doc_id": saved_doc_id,
            "big_chunk_id": saved_big_chunk_id,
            "small_chunk_id": saved_small_chunk_id,
        })
        version_identity = _identity_tuple(source_version)
        if (
            saved_identity is None
            or version_identity is None
            or version_identity != saved_identity
            or not all(
                _is_sha256_hex(source_version.get(field))
                for field in _DIGEST_FIELDS
            )
        ):
            return "unverifiable"

        if current_status is None:
            return "unavailable"
        if current_status == "indexing":
            if (current_kb_id, current_doc_id) != saved_identity[:2]:
                return "unverifiable"
            return "indexing"
        if current_status != "indexed":
            return "unverifiable"

        current_identity = _identity_tuple({
            "kb_id": current_kb_id,
            "doc_id": current_doc_id,
            "big_chunk_id": current_big_chunk_id,
            "small_chunk_id": current_small_chunk_id,
        })
        if current_identity is None or current_identity != saved_identity:
            return "unverifiable"
        if not isinstance(current_content, str) or not isinstance(
            current_small_chunk_text, str
        ):
            return "unverifiable"

        content_digest = _sha256_utf8(current_content)
        small_digest = _sha256_utf8(current_small_chunk_text)
        if content_digest is None or small_digest is None:
            return "unverifiable"
        if (
            content_digest != source_version.get("content_sha256")
            or small_digest != source_version.get("small_chunk_text_sha256")
        ):
            return "changed"
        return "current"
    except Exception:
        # Malformed caller data must never be interpreted as a successful match.
        return "unverifiable"


def _identity_tuple(values: Mapping[str, object]) -> tuple[str, str, str, str] | None:
    parts: list[str] = []
    for field in _IDENTITY_FIELDS:
        value = values.get(field)
        if not isinstance(value, str) or value != value.strip():
            return None
        parts.append(value)
    kb_id, doc_id, big_chunk_id, small_chunk_id = parts
    if not kb_id or not doc_id or not (big_chunk_id or small_chunk_id):
        return None
    return kb_id, doc_id, big_chunk_id, small_chunk_id


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


def _sha256_utf8(value: str) -> str | None:
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except Exception:
        return None
