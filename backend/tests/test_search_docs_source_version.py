"""SearchDocs source versions bind exact returned text and survive source reuse."""

import hashlib
from types import SimpleNamespace

from src.agent.exceptions import ToolContext
from src.agent.tools.groups.retrieval import search_docs
from src.agent.tools.groups.retrieval.search_docs import (
    MAX_CHUNK_CHARS,
    SearchDocsOutput,
    TRUNCATE_SUFFIX,
    _build_search_result,
)


def _result(
    *,
    parent_text: str = "Parent evidence text.",
    small_text: str = "GPIO mode bits.",
) -> SimpleNamespace:
    return SimpleNamespace(
        content=parent_text,
        metadata={
            "title": "Manual.pdf",
            "big_chunk_id": "parent-a",
            "small_chunks": [
                {
                    "id": "small-a",
                    "chunk_index": 0,
                    "score": 0.8,
                    "text": small_text,
                }
            ],
        },
        score=0.8,
        doc_id="doc-a",
        kb_id="kb-a",
        kb_name="Knowledge base A",
    )


def test_source_version_hashes_final_truncated_and_small_chunk_text() -> None:
    parent_text = "寄存器" * (MAX_CHUNK_CHARS + 1)
    small_text = "GPIO 输入配置。"

    output = _build_search_result(
        [_result(parent_text=parent_text, small_text=small_text)],
        ToolContext(),
    )

    entry = output["results"][0]
    version = entry["source_version"]
    expected_content = parent_text[:MAX_CHUNK_CHARS] + TRUNCATE_SUFFIX
    assert entry["content"] == expected_content
    assert version == {
        "schema_version": 1,
        "kb_id": "kb-a",
        "doc_id": "doc-a",
        "big_chunk_id": "parent-a",
        "small_chunk_id": "small-a",
        "content_sha256": hashlib.sha256(
            expected_content.encode("utf-8")
        ).hexdigest(),
        "small_chunk_text_sha256": hashlib.sha256(
            small_text.encode("utf-8")
        ).hexdigest(),
        "chunk_index": 0,
    }
    assert "id" not in version
    assert "generation_id" not in version
    assert "document_sha256" not in version


def test_reused_source_id_keeps_version_ref_in_tool_result() -> None:
    context = ToolContext()
    first = _build_search_result([_result()], context)
    repeated = _build_search_result([_result()], context)

    source_id = first["results"][0]["id"]
    source_version = first["results"][0]["source_version"]
    assert repeated["results"] == []
    assert repeated["reused_source_refs"] == [
        {"id": source_id, "source_version": source_version}
    ]
    assert f"[{source_id}]" in repeated["output"]
    assert source_id not in source_version.values()
    assert SearchDocsOutput(
        results=repeated["results"],
        truncated=repeated["truncated"],
        reused_source_refs=repeated["reused_source_refs"],
    ).reused_source_refs == repeated["reused_source_refs"]


def test_restored_registry_reuses_source_id_with_matching_version() -> None:
    original_context = ToolContext()
    first = _build_search_result(
        [_result(parent_text="当时返回的证据正文。", small_text="GPIO 模式位。")],
        original_context,
    )
    first_entry = first["results"][0]
    snapshot = original_context.rag_source_registry.to_snapshot()
    resumed_context = ToolContext(source_counter=original_context.source_counter)
    resumed_context.rag_source_registry = snapshot

    resumed = _build_search_result(
        [_result(parent_text="当时返回的证据正文。", small_text="GPIO 模式位。")],
        resumed_context,
    )

    assert resumed["results"] == []
    assert resumed["reused_source_refs"] == [
        {
            "id": first_entry["id"],
            "source_version": first_entry["source_version"],
        }
    ]
    resumed_version = resumed["reused_source_refs"][0]["source_version"]
    assert resumed["reused_source_refs"][0]["id"] == "src1"
    assert resumed_version["content_sha256"] == hashlib.sha256(
        first_entry["content"].encode("utf-8")
    ).hexdigest()
    assert resumed_version["small_chunk_text_sha256"] == hashlib.sha256(
        first_entry["small_chunk_text"].encode("utf-8")
    ).hexdigest()


def test_changed_evidence_with_same_chunk_ids_gets_new_source_and_version() -> None:
    context = ToolContext()
    original = _build_search_result(
        [_result(parent_text="原始手册正文。", small_text="GPIO 模式位。")],
        context,
    )["results"][0]
    changed = _build_search_result(
        [_result(parent_text="更新后的手册正文。", small_text="GPIO 模式位。")],
        context,
    )["results"][0]

    assert original["big_chunk_id"] == changed["big_chunk_id"] == "parent-a"
    assert original["small_chunk_id"] == changed["small_chunk_id"] == "small-a"
    assert original["id"] == "src1"
    assert changed["id"] == "src2"
    assert original["source_version"] != changed["source_version"]
    assert original["source_version"]["content_sha256"] == hashlib.sha256(
        original["content"].encode("utf-8")
    ).hexdigest()
    assert changed["source_version"]["content_sha256"] == hashlib.sha256(
        changed["content"].encode("utf-8")
    ).hexdigest()


class _Utf8EncodingFailureText(str):
    def encode(self, *_args, **_kwargs) -> bytes:
        raise UnicodeEncodeError("utf-8", self, 0, 1, "simulated encoding failure")


def test_missing_identity_or_utf8_failure_keeps_results_without_version() -> None:
    missing_identity = _result()
    missing_identity.kb_id = ""
    unencodable_text = _Utf8EncodingFailureText("Parent evidence text.")
    encoding_failure = _result(parent_text=unencodable_text)

    for result in (missing_identity, encoding_failure):
        output = _build_search_result([result], ToolContext())

        assert len(output["results"]) == 1
        assert output["results"][0]["content"] == result.content
        assert "source_version" not in output["results"][0]


def test_digest_failure_omits_version_without_dropping_results(monkeypatch) -> None:
    monkeypatch.setattr(search_docs, "_sha256_utf8_text", lambda _text: None)
    context = ToolContext()

    first = _build_search_result([_result()], context)
    repeated = _build_search_result([_result()], context)

    assert len(first["results"]) == 1
    assert first["results"][0]["content"] == "Parent evidence text."
    assert "source_version" not in first["results"][0]
    assert repeated["results"] == []
    assert "reused_source_refs" not in repeated
    assert "[src1]" in repeated["output"]
