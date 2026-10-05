"""Regression coverage for retrieval ranking across fusion, reranking and KB merge."""

import asyncio
from types import SimpleNamespace

import pytest

from src.rag import reranker as reranker_module
from src.rag.kb_manager import (
    FusedResult,
    KnowledgeBase,
    KnowledgeBaseManager,
    _apply_reranker_score,
    _blend_reranker_score,
    rrf_fusion,
)
from src.rag.vector_store import SearchResult


def _search_result(doc_id: str, display_score: float, chunk_index: int = 0) -> SearchResult:
    return SearchResult(
        content=doc_id,
        metadata={"doc_id": doc_id, "chunk_index": chunk_index},
        score=display_score,
        doc_id=doc_id,
    )


def _fused_result(
    kb_id: str,
    doc_id: str,
    display_score: float,
    rank_score: float,
    *,
    metadata: dict | None = None,
) -> FusedResult:
    return FusedResult(
        content=doc_id,
        metadata={"doc_id": doc_id, "chunk_index": 0, **(metadata or {})},
        score=display_score,
        doc_id=doc_id,
        kb_id=kb_id,
        kb_name=kb_id,
        rank_score=rank_score,
    )


class _FakeDb:
    def __init__(self, kbs: list[SimpleNamespace]):
        self._kbs = kbs

    def query(self, _model):
        return self

    def filter(self, *_args):
        return self

    def all(self):
        return self._kbs

    def close(self):
        pass


def _manager_with_results(monkeypatch, results_by_kb: dict[str, list[FusedResult]]) -> KnowledgeBaseManager:
    kbs = [SimpleNamespace(id=kb_id, name=kb_id) for kb_id in results_by_kb]
    manager = KnowledgeBaseManager(db_session_factory=lambda: _FakeDb(kbs))

    def search(kb_id, _query, k, _threshold, _doc_filter):
        return sorted(results_by_kb[kb_id], key=lambda item: item.rank_score, reverse=True)[:k]

    manager.search = search
    return manager


def test_rrf_order_is_separate_from_display_score() -> None:
    vector = [
        _search_result("display-first", 0.99),
        _search_result("rank-first", 0.40),
    ]
    bm25 = [_search_result("rank-first", 0.80)]

    fused = rrf_fusion(vector, bm25)

    assert [item.doc_id for item in fused] == ["rank-first", "display-first"]
    assert fused[0].score == pytest.approx(0.60)
    assert fused[0].rank_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1].score == pytest.approx(0.99)
    assert fused[1].rank_score == pytest.approx(1 / 61)


def test_active_reranker_blends_with_rrf_without_mutating_display_score() -> None:
    display_first = _fused_result("kb-a", "display-first", 0.99, 1 / 61)
    reranker_first = _fused_result("kb-a", "reranker-first", 0.20, 1 / 62 + 1 / 61)

    reranked = _apply_reranker_score(
        [display_first, reranker_first],
        [(1, 2.0), (0, -1.0)],
    )

    assert [item.doc_id for item in reranked] == ["reranker-first", "display-first"]
    assert reranker_first.rank_score == pytest.approx(
        _blend_reranker_score(1 / 62 + 1 / 61, 2.0)
    )
    assert display_first.rank_score == pytest.approx(_blend_reranker_score(1 / 61, -1.0))
    assert reranker_first.score == pytest.approx(0.20)
    assert display_first.score == pytest.approx(0.99)


def test_invalid_reranker_output_does_not_partially_change_fallback_order() -> None:
    first = _fused_result("kb-a", "first", 0.90, 0.02)
    second = _fused_result("kb-a", "second", 0.10, 0.01)

    with pytest.raises(ValueError, match="invalid result index"):
        _apply_reranker_score([first, second], [(0, 2.0), (4, 4.0)])

    assert first.rank_score == pytest.approx(0.02)
    assert second.rank_score == pytest.approx(0.01)


def test_active_reranker_changes_global_order_but_keeps_display_scores(monkeypatch) -> None:
    lower_rrf = _fused_result("kb-b", "lower-rrf", 0.91, 0.01)
    higher_rrf = _fused_result("kb-a", "higher-rrf", 0.22, 0.03)
    manager = _manager_with_results(
        monkeypatch,
        {"kb-b": [lower_rrf], "kb-a": [higher_rrf]},
    )
    monkeypatch.setattr(
        reranker_module,
        "rerank",
        lambda _query, _texts: [(1, 2.0), (0, -1.0)],
    )

    result = asyncio.run(manager.search_all_enabled("query", k=2))

    assert [item.doc_id for item in result] == ["higher-rrf", "lower-rrf"]
    assert result[0].score == pytest.approx(0.22)
    assert result[1].score == pytest.approx(0.91)


def test_single_kb_top_k_uses_internal_rank_not_display_score(monkeypatch) -> None:
    display_first = _fused_result("kb-one", "display-first", 0.99, 0.01)
    rank_first = _fused_result("kb-one", "rank-first", 0.10, 0.03)
    manager = _manager_with_results(monkeypatch, {"kb-one": [display_first, rank_first]})
    monkeypatch.setattr(
        reranker_module,
        "rerank",
        lambda _query, texts: [(index, 0.0) for index, _ in enumerate(texts)],
    )

    result = asyncio.run(manager.search_all_enabled("query", k=1))

    assert [item.doc_id for item in result] == ["rank-first"]


@pytest.mark.parametrize("top_k", [1, 2, 5, 7])
@pytest.mark.parametrize("failure_mode", ["zero_scores", "exception"])
def test_reranker_fallback_keeps_global_rrf_order_and_top_k(
    monkeypatch, top_k: int, failure_mode: str,
) -> None:
    results: dict[str, list[FusedResult]] = {"kb-b": [], "kb-a": []}
    expected: list[FusedResult] = []
    for index in range(8):
        kb_id = "kb-a" if index % 2 else "kb-b"
        item = _fused_result(
            kb_id,
            f"doc-{index}",
            display_score=0.99 - index * 0.1,
            rank_score=0.01 + index * 0.01,
        )
        results[kb_id].append(item)
        expected.append(item)

    manager = _manager_with_results(monkeypatch, results)
    if failure_mode == "zero_scores":
        monkeypatch.setattr(
            reranker_module,
            "rerank",
            lambda _query, texts: [(index, 0.0) for index, _ in enumerate(texts)],
        )
    else:
        def fail_rerank(_query, _texts):
            raise RuntimeError("reranker unavailable")
        monkeypatch.setattr(reranker_module, "rerank", fail_rerank)

    actual = asyncio.run(manager.search_all_enabled("query", k=top_k))
    expected_ids = [item.doc_id for item in sorted(expected, key=lambda item: item.rank_score, reverse=True)[:top_k]]

    assert [item.doc_id for item in actual] == expected_ids


def test_global_tie_order_does_not_depend_on_database_kb_order(monkeypatch) -> None:
    kb_b = _fused_result("kb-b", "same-rank-b", 0.90, 0.02)
    kb_a = _fused_result("kb-a", "same-rank-a", 0.10, 0.02)
    manager = _manager_with_results(monkeypatch, {"kb-b": [kb_b], "kb-a": [kb_a]})
    monkeypatch.setattr(reranker_module, "rerank", lambda _query, texts: [(i, 0.0) for i in range(len(texts))])

    result = asyncio.run(manager.search_all_enabled("query", k=2))

    assert [item.kb_id for item in result] == ["kb-a", "kb-b"]


def test_parent_merge_uses_rank_score_and_preserves_distinct_small_chunks() -> None:
    manager = KnowledgeBaseManager()
    display_first = _fused_result(
        "kb-a", "display-first", 0.99, 0.01,
        metadata={"big_chunk_id": "parent-1", "small_chunk_id": "small-1"},
    )
    rank_first = _fused_result(
        "kb-a", "rank-first", 0.20, 0.03,
        metadata={"big_chunk_id": "parent-1", "small_chunk_id": "small-2"},
    )

    merged = manager._dedup_by_big_chunk(
        [display_first, rank_first],
        {"parent-1": {"text": "full parent"}},
    )

    assert len(merged) == 1
    assert merged[0].content == "full parent"
    assert merged[0].score == pytest.approx(0.20)
    assert merged[0].rank_score == pytest.approx(0.03)
    assert [item["id"] for item in merged[0].metadata["small_chunks"]] == ["small-2", "small-1"]


@pytest.mark.parametrize("threshold", [0.0, 0.5])
def test_vector_threshold_does_not_filter_bm25_only_candidates(monkeypatch, threshold: float) -> None:
    kb = SimpleNamespace(id="kb-one", name="One", enabled=True)
    manager = KnowledgeBaseManager()
    manager.get_kb = lambda _kb_id: kb
    manager._get_store = lambda _kb: SimpleNamespace(
        search=lambda _query, k, score_threshold: (
            [_search_result("vector-hit", 0.9)] if score_threshold == 0.0 else []
        )
    )
    monkeypatch.setattr(
        manager,
        "_bm25_search",
        lambda _kb_id, _query, _k: [_search_result("bm25-only", 0.7)],
    )

    result = manager.search("kb-one", "query", k=2, score_threshold=threshold)

    assert "bm25-only" in [item.doc_id for item in result]
    assert ("vector-hit" in [item.doc_id for item in result]) is (threshold == 0.0)
