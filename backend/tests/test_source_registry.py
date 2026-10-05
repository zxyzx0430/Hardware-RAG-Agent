"""Request-local source IDs stay stable without repeating evidence bodies."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.agent.exceptions import ToolContext
from src.agent.sse_helpers import build_source_events
from src.agent.tools.groups.retrieval.search_docs import MAX_CHUNK_CHARS, _build_search_result
from src.agent.tools.groups.retrieval.web_search import _simplify_results
from src.agent.tools.groups.retrieval.source_registry import (
    InvalidSourceRegistrySnapshot,
    RagSourceRegistry,
    get_context_source_registry,
)


def _result(
    *,
    kb_id: str = "kb-a",
    doc_id: str = "doc-a",
    parent_id: str = "parent-a",
    parent_text: str = "Parent evidence text.",
    chunks: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=parent_text,
        metadata={
            "title": "Manual.pdf",
            "big_chunk_id": parent_id,
            "small_chunks": chunks or [
                {"id": "small-a", "chunk_index": 0, "score": 0.8, "text": "GPIO mode bits."}
            ],
        },
        score=0.8,
        doc_id=doc_id,
        kb_id=kb_id,
        kb_name="Knowledge base A",
    )


def _web_result(
    *,
    title: str = "Web manual",
    url: str = "https://example.invalid/manual",
    content: str = "Web evidence.",
) -> dict:
    return {"title": title, "url": url, "content": content, "score": 0.9}


def _decode_sse_event(event: str) -> dict:
    return json.loads(event.removeprefix("data: ").strip())


def test_repeated_search_result_reuses_id_and_emits_one_source_event() -> None:
    context = ToolContext()
    result = _result()

    first = _build_search_result([result], context)
    second = _build_search_result([result], context)

    assert [entry["id"] for entry in first["results"]] == ["src1"]
    assert second["results"] == []
    assert "[src1]" in second["output"]
    assert "Parent evidence text." not in second["output"]
    assert len(build_source_events({"data": first})) == 1
    assert build_source_events({"data": second}) == []
    assert context.source_counter == 1
    assert context.rag_source_registry.next_id == context.source_counter + 1


def test_parallel_search_result_builds_share_the_context_registry() -> None:
    context = ToolContext()
    result = _result()

    with ThreadPoolExecutor(max_workers=8) as pool:
        outputs = list(pool.map(lambda _: _build_search_result([result], context), range(24)))

    new_entries = [entry for output in outputs for entry in output["results"]]
    assert [entry["id"] for entry in new_entries] == ["src1"]
    assert context.rag_source_registry.next_id == context.source_counter + 1


def test_distinct_small_chunks_in_same_parent_keep_distinct_sources() -> None:
    result = _result(chunks=[
        {"id": "small-a", "chunk_index": 0, "score": 0.8, "text": "GPIO input mode."},
        {"id": "small-b", "chunk_index": 1, "score": 0.7, "text": "GPIO output mode."},
    ])

    output = _build_search_result([result], ToolContext())

    assert [entry["id"] for entry in output["results"]] == ["src1", "src2"]
    assert [entry["small_chunk_id"] for entry in output["results"]] == ["small-a", "small-b"]


def test_changed_parent_content_with_same_chunk_ids_gets_new_source_id() -> None:
    context = ToolContext()

    first = _build_search_result([_result()], context)
    changed = _build_search_result(
        [_result(parent_text="Changed parent evidence text.")], context,
    )

    assert first["results"][0]["id"] == "src1"
    assert changed["results"][0]["id"] == "src2"


def test_content_changes_beyond_display_truncation_get_new_source_id() -> None:
    context = ToolContext()
    common_prefix = "x" * MAX_CHUNK_CHARS

    first = _build_search_result([_result(parent_text=common_prefix + "A")], context)
    changed = _build_search_result([_result(parent_text=common_prefix + "B")], context)

    assert first["results"][0]["content"] == changed["results"][0]["content"]
    assert first["results"][0]["id"] == "src1"
    assert changed["results"][0]["id"] == "src2"


def test_missing_identity_is_never_deduplicated() -> None:
    context = ToolContext()
    result = _result(kb_id="", doc_id="")

    first = _build_search_result([result], context)
    second = _build_search_result([result], context)

    assert first["results"][0]["id"] == "src1"
    assert second["results"][0]["id"] == "src2"


def test_new_request_starts_with_fresh_registry() -> None:
    result = _result()

    first = _build_search_result([result], ToolContext())
    second = _build_search_result([result], ToolContext())

    assert first["results"][0]["id"] == "src1"
    assert second["results"][0]["id"] == "src1"


def test_same_request_snapshot_restores_duplicate_source_reference() -> None:
    original_context = ToolContext()
    first = _build_search_result([_result()], original_context)
    snapshot = original_context.rag_source_registry.to_snapshot()
    resumed_context = ToolContext(source_counter=original_context.source_counter)
    resumed_context.rag_source_registry = snapshot

    resumed = _build_search_result([_result()], resumed_context)

    assert first["results"][0]["id"] == "src1"
    assert resumed["results"] == []
    assert "[src1]" in resumed["output"]
    assert resumed_context.source_counter == 1


def test_registry_fails_closed_when_counter_has_no_matching_registry() -> None:
    context = ToolContext(source_counter=3)

    with pytest.raises(InvalidSourceRegistrySnapshot):
        _build_search_result([_result()], context)


def test_web_first_then_rag_share_source_id_sequence() -> None:
    context = ToolContext()

    web = _simplify_results([_web_result()], context)
    rag = _build_search_result([_result()], context)

    assert [entry["id"] for entry in web] == ["src1"]
    assert [entry["id"] for entry in rag["results"]] == ["src2"]
    assert context.source_counter == 2
    assert context.rag_source_registry.next_id == 3


def test_rag_first_then_web_share_source_id_sequence() -> None:
    context = ToolContext()

    rag = _build_search_result([_result()], context)
    web = _simplify_results([_web_result()], context)

    assert [entry["id"] for entry in rag["results"]] == ["src1"]
    assert [entry["id"] for entry in web] == ["src2"]
    assert context.source_counter == 2
    assert context.rag_source_registry.next_id == 3


def test_concurrent_rag_and_web_allocation_is_atomic() -> None:
    context = ToolContext()
    web_ids: list[str] = []
    rag_ids: list[str] = []

    def run_web(barrier: threading.Barrier) -> list[dict]:
        barrier.wait(timeout=5)
        return _simplify_results([_web_result()], context)

    def run_rag(barrier: threading.Barrier) -> dict:
        barrier.wait(timeout=5)
        return _build_search_result([_result()], context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(16):
            barrier = threading.Barrier(2)
            web_future = pool.submit(run_web, barrier)
            rag_future = pool.submit(run_rag, barrier)
            web_ids.extend(entry["id"] for entry in web_future.result(timeout=10))
            rag_result = rag_future.result(timeout=10)
            if rag_result["results"]:
                rag_ids.extend(entry["id"] for entry in rag_result["results"])

    assert len(web_ids) == len(set(web_ids)) == 16
    assert len(rag_ids) == 1
    allocated = set(web_ids + rag_ids)
    assert allocated == {f"src{index}" for index in range(1, 18)}
    assert context.source_counter == 17
    assert context.rag_source_registry.next_id == 18


def test_repeated_web_results_are_not_deduplicated_but_rag_evidence_is() -> None:
    context = ToolContext()

    first_web = _simplify_results([_web_result()], context)
    repeated_web = _simplify_results([_web_result()], context)
    first_rag = _build_search_result([_result()], context)
    repeated_rag = _build_search_result([_result()], context)

    assert first_web[0]["id"] == "src1"
    assert repeated_web[0]["id"] == "src2"
    assert first_rag["results"][0]["id"] == "src3"
    assert repeated_rag["results"] == []
    assert "[src3]" in repeated_rag["output"]
    assert context.source_counter == 3
    assert context.rag_source_registry.next_id == 4


def test_web_source_fields_and_sse_payload_remain_compatible() -> None:
    context = ToolContext()
    web = _simplify_results([_web_result()], context)

    assert set(web[0]) == {
        "id", "title", "doc", "source_url", "category", "score",
        "score_percentage", "relevance_level", "content", "excerpt",
    }
    assert web[0] == {
        "id": "src1",
        "title": "Web manual",
        "doc": "https://example.invalid/manual",
        "source_url": "https://example.invalid/manual",
        "category": "web",
        "score": 0.9,
        "score_percentage": 90.0,
        "relevance_level": "high",
        "content": "Web evidence.",
        "excerpt": "Web evidence.",
    }

    events = build_source_events({"data": {"results": web}})
    assert len(events) == 1
    payload = _decode_sse_event(events[0])
    assert payload["type"] == "source"
    assert payload["id"] == "src1"
    assert payload["doc"] == "https://example.invalid/manual"
    assert payload["source_url"] == "https://example.invalid/manual"
    assert payload["category"] == "web"
    assert payload["excerpt"] == "Web evidence."


def test_snapshot_continues_shared_web_and_rag_source_ids() -> None:
    original_context = ToolContext()
    web = _simplify_results(
        [
            _web_result(url="https://example.invalid/one"),
            _web_result(url="https://example.invalid/two"),
        ],
        original_context,
    )
    first_rag = _build_search_result([_result()], original_context)
    snapshot = original_context.rag_source_registry.to_snapshot()
    resumed_context = ToolContext(source_counter=original_context.source_counter)
    resumed_context.rag_source_registry = snapshot

    repeated_rag = _build_search_result([_result()], resumed_context)
    next_web = _simplify_results([_web_result(url="https://example.invalid/three")], resumed_context)
    next_rag = _build_search_result(
        [_result(chunks=[
            {"id": "small-a", "chunk_index": 0, "score": 0.8, "text": "GPIO mode bits."},
            {"id": "small-b", "chunk_index": 1, "score": 0.7, "text": "GPIO alternate mode."},
        ])],
        resumed_context,
    )

    assert [entry["id"] for entry in web] == ["src1", "src2"]
    assert first_rag["results"][0]["id"] == "src3"
    assert repeated_rag["results"] == []
    assert "[src3]" in repeated_rag["output"]
    assert [entry["id"] for entry in next_web] == ["src4"]
    assert [entry["id"] for entry in next_rag["results"]] == ["src5"]
    assert resumed_context.source_counter == 5
    assert resumed_context.rag_source_registry.next_id == 6


def test_registry_counter_mirror_cannot_be_advanced_independently() -> None:
    context = ToolContext()
    registry = get_context_source_registry(context)
    context.source_counter = 4

    with pytest.raises(InvalidSourceRegistrySnapshot):
        registry.allocate_ids(1, context=context)

    assert registry.next_id == 1


def test_parallel_registration_allocates_one_id_for_duplicate_evidence() -> None:
    registry = RagSourceRegistry()
    evidence = {
        "kb_id": "kb-a",
        "doc_id": "doc-a",
        "big_chunk_id": "parent-a",
        "small_chunk_id": "small-a",
        "chunk_index": 0,
        "content": "Parent evidence.",
        "small_chunk_text": "GPIO mode bits.",
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        registrations = list(pool.map(lambda _: registry.register_many([evidence])[0], range(32)))

    assert {item.source_id for item in registrations} == {"src1"}
    assert sum(item.is_new for item in registrations) == 1
    assert registry.next_id == 2


def test_snapshot_restores_same_request_mapping_without_source_text() -> None:
    registry = RagSourceRegistry()
    evidence = {
        "kb_id": "kb-private",
        "doc_id": "secret-manual.pdf",
        "big_chunk_id": "parent-a",
        "small_chunk_id": "small-a",
        "chunk_index": 0,
        "content": "Private source body.",
        "small_chunk_text": "Private excerpt.",
    }
    assert registry.register_many([evidence])[0].source_id == "src1"

    snapshot = registry.to_snapshot()
    restored = RagSourceRegistry.from_snapshot(snapshot)
    repeated = restored.register_many([evidence])[0]
    new_evidence = {**evidence, "small_chunk_id": "small-b", "small_chunk_text": "Another excerpt."}

    assert repeated.source_id == "src1" and not repeated.is_new
    assert restored.register_many([new_evidence])[0].source_id == "src2"
    assert "Private source body" not in repr(registry)
    assert "secret-manual.pdf" not in repr(registry)
    assert "Private source body" not in repr(snapshot)
    assert "secret-manual.pdf" not in repr(snapshot)


@pytest.mark.parametrize(
    "snapshot",
    [
        {"version": 2, "next_id": 1, "entries": []},
        {"version": True, "next_id": 1, "entries": []},
        {"version": 1, "next_id": 1, "entries": [{"identity_hash": "bad", "source_id": "src1"}]},
        {
            "version": 1,
            "next_id": 2,
            "entries": [
                {"identity_hash": "a" * 64, "source_id": "src1"},
                {"identity_hash": "b" * 64, "source_id": "src1"},
            ],
        },
        {"version": 1, "next_id": 1, "entries": [{"identity_hash": "a" * 64, "source_id": "src1"}]},
    ],
)
def test_invalid_or_reusing_snapshot_fails_closed(snapshot: dict) -> None:
    with pytest.raises(InvalidSourceRegistrySnapshot):
        RagSourceRegistry.from_snapshot(snapshot)
