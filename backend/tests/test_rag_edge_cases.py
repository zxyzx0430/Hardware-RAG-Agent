"""
Edge case unit tests for RAG P0/P1 fixes.

Covers all bugs and defensive issues found during the edge-case audit:
- P0-1: _make_rrf_key with chunk_index=None
- P0-2: import_data embeddings length mismatch
- P0-3: _rewrite_query_for_rag newline cleanup
- P0-4: _rewrite_query_for_rag multimodal history extraction
- P1-5: rrf_fusion constant_k < 1 (division by zero)
- P1-6: rrf_fusion orig_score clamp (negative BM25 / cosine)
- P1-7: import_data checks ALL embedding dimensions (not just first)
- P1-8: import_data fail-open when embedding API unavailable
- P1-A: _bm25_search negative score normalization
- P1-B: _get_embedding_dimension negative caching
- Edge: empty inputs, same-doc different-chunk dedup, zero scores,
        rewrite fallbacks (empty/too-long/timeout/exception)

Run: cd backend && python -m pytest tests/test_rag_edge_cases.py -v
"""
import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.kb_manager import (
    rrf_fusion,
    _make_rrf_key,
    KnowledgeBaseManager,
)
from src.rag.vector_store import HardwareVectorStore, SearchResult
from src.llm.client import LLMResponse, ChatMessage


# ═══════════════════════════════════════════
# Helper factory
# ═══════════════════════════════════════════

def _result(content: str, doc_id: str, score: float, chunk_index=None) -> SearchResult:
    """Build a SearchResult with the given chunk_index (default None)."""
    return SearchResult(
        content=content,
        metadata={"chunk_index": chunk_index, "doc_id": doc_id},
        score=score,
        doc_id=doc_id,
    )


# ═══════════════════════════════════════════
# P0-1: _make_rrf_key
# ═══════════════════════════════════════════

class TestMakeRRFKey:
    """P0-1: _make_rrf_key must handle chunk_index being explicitly None.

    Bug: dict.get('chunk_index', i) returns None (not i) when the key
    exists with value None, causing the same chunk to split into separate
    entries across vector/BM25 lists.
    """

    def test_chunk_index_explicitly_none_uses_fallback(self):
        """chunk_index=None (explicit) must fall back to fallback_idx."""
        r = _result("text", "doc1", 0.9, chunk_index=None)
        assert _make_rrf_key(r, fallback_idx=3) == "doc1#3"

    def test_chunk_index_missing_uses_fallback(self):
        """Missing chunk_index key must fall back to fallback_idx."""
        r = SearchResult(content="text", metadata={}, score=0.5, doc_id="doc2")
        assert _make_rrf_key(r, fallback_idx=7) == "doc2#7"

    def test_chunk_index_zero_not_replaced_by_fallback(self):
        """chunk_index=0 is a valid value — must NOT be replaced by fallback."""
        r = _result("text", "doc3", 0.8, chunk_index=0)
        assert _make_rrf_key(r, fallback_idx=5) == "doc3#0"

    def test_chunk_index_positive_value_preserved(self):
        """chunk_index=5 must be preserved, not replaced by fallback."""
        r = _result("text", "doc4", 0.7, chunk_index=5)
        assert _make_rrf_key(r, fallback_idx=1) == "doc4#5"

    def test_different_doc_same_chunk_index_distinguishable(self):
        """Same chunk_index but different doc_id must produce different keys."""
        r1 = _result("text", "docA", 0.9, chunk_index=1)
        r2 = _result("text", "docB", 0.9, chunk_index=1)
        assert _make_rrf_key(r1, 0) != _make_rrf_key(r2, 0)

    def test_same_doc_different_chunk_index_distinguishable(self):
        """Same doc_id but different chunk_index must produce different keys."""
        r1 = _result("text", "docA", 0.9, chunk_index=0)
        r2 = _result("text", "docA", 0.9, chunk_index=1)
        assert _make_rrf_key(r1, 0) != _make_rrf_key(r2, 1)


# ═══════════════════════════════════════════
# P1-5 + P1-6: rrf_fusion edge cases
# ═══════════════════════════════════════════

class TestRRFFusionConstantK:
    """P1-5: constant_k < 1 must be clamped to prevent division by zero."""

    def test_constant_k_zero_does_not_crash(self):
        """constant_k=0 must not cause ZeroDivisionError."""
        v = [_result("a", "d1", 0.9, chunk_index=0)]
        b = [_result("a", "d1", 0.8, chunk_index=0)]
        # Should not raise
        result = rrf_fusion(v, b, constant_k=0)
        assert len(result) == 1

    def test_constant_k_negative_clamped(self):
        """constant_k=-5 must be clamped to 1."""
        v = [_result("a", "d1", 0.9, chunk_index=0)]
        b = [_result("a", "d1", 0.8, chunk_index=0)]
        result = rrf_fusion(v, b, constant_k=-5)
        assert len(result) == 1
        assert 0.0 <= result[0].score <= 1.0


class TestRRFFusionScoreClamp:
    """P1-6: orig_score must be clamped to 0-1 to defend against negative BM25."""

    def test_negative_bm25_score_clamped_to_zero(self):
        """BM25 score < 0 must be clamped to 0.0 in output."""
        v = []
        b = [_result("text", "d1", -0.5, chunk_index=0)]  # negative score
        result = rrf_fusion(v, b, constant_k=60)
        assert len(result) == 1
        assert result[0].score == 0.0, f"Negative score leaked: {result[0].score}"

    def test_score_above_one_clamped_to_one(self):
        """Score > 1.0 must be clamped to 1.0."""
        v = [_result("text", "d1", 1.5, chunk_index=0)]
        b = []
        result = rrf_fusion(v, b, constant_k=60)
        assert len(result) == 1
        assert result[0].score == 1.0

    def test_all_scores_in_zero_one_range(self):
        """All output scores must be in [0, 1]."""
        v = [
            _result("a", "d1", 0.9, chunk_index=0),
            _result("b", "d1", -0.3, chunk_index=1),
            _result("c", "d2", 1.2, chunk_index=0),
        ]
        b = [
            _result("a", "d1", -1.0, chunk_index=0),
            _result("d", "d3", 0.5, chunk_index=0),
        ]
        result = rrf_fusion(v, b, constant_k=60)
        for r in result:
            assert 0.0 <= r.score <= 1.0, f"Score out of range: {r.score}"


class TestRRFFusionEdgeCases:
    """Edge cases: empty inputs, dedup, zero scores."""

    def test_both_inputs_empty(self):
        """Empty vector + empty BM25 → empty result."""
        assert rrf_fusion([], [], constant_k=60) == []

    def test_vector_empty_only(self):
        """Empty vector, non-empty BM25 → BM25 results only."""
        b = [_result("text", "d1", 0.8, chunk_index=0)]
        result = rrf_fusion([], b, constant_k=60)
        assert len(result) == 1
        assert result[0].doc_id == "d1"

    def test_bm25_empty_only(self):
        """Non-empty vector, empty BM25 → vector results only."""
        v = [_result("text", "d1", 0.9, chunk_index=0)]
        result = rrf_fusion(v, [], constant_k=60)
        assert len(result) == 1
        assert result[0].doc_id == "d1"

    def test_same_doc_different_chunk_not_merged(self):
        """Same doc_id, different chunk_index must NOT be merged into one."""
        v = [
            _result("chunk0", "d1", 0.9, chunk_index=0),
            _result("chunk1", "d1", 0.8, chunk_index=1),
        ]
        b = []
        result = rrf_fusion(v, b, constant_k=60)
        assert len(result) == 2, "Different chunks of same doc were wrongly merged"

    def test_same_chunk_fused_across_lists(self):
        """Same doc_id + chunk_index in both lists must be fused (deduped)."""
        v = [_result("text", "d1", 0.9, chunk_index=0)]
        b = [_result("text", "d1", 0.8, chunk_index=0)]
        result = rrf_fusion(v, b, constant_k=60)
        assert len(result) == 1, "Same chunk was not deduped"
        # Display score = max(vector, bm25) = max(0.9, 0.8) = 0.9
        assert result[0].score == 0.9

    def test_zero_scores_preserved(self):
        """Zero scores are valid and must be preserved (not dropped)."""
        v = [_result("text", "d1", 0.0, chunk_index=0)]
        b = [_result("text", "d2", 0.0, chunk_index=0)]
        result = rrf_fusion(v, b, constant_k=60)
        assert len(result) == 2
        for r in result:
            assert r.score == 0.0

    def test_chunk_index_none_in_both_lists_dedupes(self):
        """P0-1 regression: chunk_index=None in both lists must dedupe by fallback idx.

        Before the fix, dict.get('chunk_index', i) returned None for both,
        producing key 'd1#None' — so they DID dedupe. But the bug was that
        when one list had chunk_index=0 and the other had chunk_index=None
        for the SAME chunk, they'd split. Here we test the None-None case
        still fuses correctly.
        """
        v = [_result("text", "d1", 0.9, chunk_index=None)]
        b = [_result("text", "d1", 0.8, chunk_index=None)]
        # Both at index 0 → same key 'd1#0' → fused
        result = rrf_fusion(v, b, constant_k=60)
        assert len(result) == 1

    def test_results_sorted_by_rrf_score_descending(self):
        """Output must be sorted by RRF score descending.

        RRF ranks by rank-contribution (1/(k+rank+1)), NOT by original
        similarity score. A chunk appearing in BOTH lists (even at rank 1)
        accumulates more RRF score than a chunk in only one list at rank 0.
        """
        # d1: vector rank 0 only → RRF = 1/61
        # d2: vector rank 1 + BM25 rank 0 → RRF = 1/62 + 1/61 (higher)
        v = [
            _result("low", "d1", 0.95, chunk_index=0),   # rank 0, high score
            _result("high", "d2", 0.5, chunk_index=0),   # rank 1, low score
        ]
        b = [_result("high", "d2", 0.4, chunk_index=0)]  # d2 also in BM25
        result = rrf_fusion(v, b, constant_k=60)
        # d2 appears in both lists → higher RRF → ranks first
        assert result[0].doc_id == "d2"
        assert result[1].doc_id == "d1"


# ═══════════════════════════════════════════
# P1-A: _bm25_search negative score normalization
# ═══════════════════════════════════════════

class TestBM25NegativeScoreClamp:
    """P1-A: _bm25_search must clamp negative BM25 scores to 0.

    rank_bm25.BM25Okapi can return negative scores for very short docs
    (IDF < 0 when a term appears in most documents).
    """

    def _make_mgr_with_mock_bm25(self, mock_bm25):
        """Build a KnowledgeBaseManager with an injected mock BM25 index."""
        mgr = KnowledgeBaseManager(db_session_factory=lambda: None)
        mgr._bm25_indices["kb-test"] = mock_bm25
        return mgr

    def test_negative_scores_clamped_to_zero(self):
        """All negative BM25 scores must be clamped to 0.0 in output."""
        mock_bm25 = MagicMock()
        mock_bm25.search.return_value = [(0, -2.5), (1, -1.0), (2, 0.5)]
        mock_bm25.corpus = ["doc0 text", "doc1 text", "doc2 text"]
        mock_bm25.metadatas = [{"doc_id": "d0"}, {"doc_id": "d1"}, {"doc_id": "d2"}]

        mgr = self._make_mgr_with_mock_bm25(mock_bm25)
        results = mgr._bm25_search("kb-test", "query", 5)

        assert len(results) == 3
        for r in results:
            assert r.score >= 0.0, f"Negative score leaked: {r.score}"

    def test_top_result_negative_uses_fallback_max(self):
        """When top result has negative score, max_score falls back to 1.0."""
        mock_bm25 = MagicMock()
        mock_bm25.search.return_value = [(0, -3.0), (1, 0.4)]
        mock_bm25.corpus = ["doc0", "doc1"]
        mock_bm25.metadatas = [{"doc_id": "d0"}, {"doc_id": "d1"}]

        mgr = self._make_mgr_with_mock_bm25(mock_bm25)
        results = mgr._bm25_search("kb-test", "query", 5)

        # doc0: max(0, -3.0/1.0) = 0.0; doc1: max(0, 0.4/1.0) = 0.4
        scores_by_doc = {r.doc_id: r.score for r in results}
        assert scores_by_doc["d0"] == 0.0
        assert scores_by_doc["d1"] == 0.4

    def test_all_scores_in_zero_one_range(self):
        """All normalized BM25 scores must be in [0, 1]."""
        mock_bm25 = MagicMock()
        mock_bm25.search.return_value = [
            (0, 5.0), (1, 2.5), (2, -1.0), (3, 0.0),
        ]
        mock_bm25.corpus = ["d0", "d1", "d2", "d3"]
        mock_bm25.metadatas = [{"doc_id": f"d{i}"} for i in range(4)]

        mgr = self._make_mgr_with_mock_bm25(mock_bm25)
        results = mgr._bm25_search("kb-test", "query", 5)

        for r in results:
            assert 0.0 <= r.score <= 1.0, f"Score out of range: {r.score}"


# ═══════════════════════════════════════════
# P0-2 + P1-7 + P1-8: import_data validation
# ═══════════════════════════════════════════

def _make_store_with_mocks(embeddings_present=True, dim=4):
    """Build a HardwareVectorStore bypassing __init__ (no ChromaDB setup).

    Args:
        embeddings_present: If True, mock embeddings with embed_query
            returning a vector of length `dim`.
        dim: Embedding dimension for the probe.
    """
    store = HardwareVectorStore.__new__(HardwareVectorStore)
    store.collection_name = "test_kb"
    store.embedding_model = "test-model"
    store._cached_embedding_dim = None
    store._dim_check_attempted = False
    if embeddings_present:
        store.embeddings = MagicMock()
        store.embeddings.embed_query.return_value = [0.1] * dim
    else:
        store.embeddings = None
    # Mock db property's backing field
    store._db = MagicMock()
    return store


class TestImportDataEmbeddingsMismatch:
    """P0-2: import_data must raise ValueError when embeddings count != documents count."""

    def test_length_mismatch_raises_value_error(self):
        """Embeddings length != documents length must raise ValueError."""
        store = _make_store_with_mocks(dim=4)
        data = {
            "ids": ["1", "2", "3"],
            "documents": ["doc1", "doc2", "doc3"],
            "embeddings": [[0.1] * 4, [0.2] * 4],  # 2 embeddings, 3 docs
            "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}, {"doc_id": "d3"}],
        }
        with pytest.raises(ValueError, match="Embeddings length mismatch"):
            store.import_data(data)

    def test_matching_lengths_does_not_raise_on_count(self):
        """Equal lengths must pass the count check (may raise on dimension)."""
        store = _make_store_with_mocks(dim=4)
        data = {
            "ids": ["1", "2"],
            "documents": ["doc1", "doc2"],
            "embeddings": [[0.1] * 4, [0.2] * 4],
            "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}],
        }
        # Should not raise ValueError for length mismatch
        store.import_data(data)
        # Verify collection.add was called
        store._db._collection.add.assert_called_once()


class TestImportDataDimensionCheck:
    """P1-7: import_data must check ALL embeddings, not just the first."""

    def test_dimension_mismatch_at_second_index_raises(self):
        """Wrong dimension at index 1 (not 0) must be caught."""
        store = _make_store_with_mocks(dim=4)
        data = {
            "ids": ["1", "2"],
            "documents": ["doc1", "doc2"],
            "embeddings": [
                [0.1] * 4,   # correct dim
                [0.2] * 8,   # WRONG dim — must be caught
            ],
            "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}],
        }
        with pytest.raises(ValueError, match="index 1"):
            store.import_data(data)

    def test_dimension_mismatch_at_first_index_raises(self):
        """Wrong dimension at index 0 must be caught (baseline)."""
        store = _make_store_with_mocks(dim=4)
        data = {
            "ids": ["1", "2"],
            "documents": ["doc1", "doc2"],
            "embeddings": [
                [0.1] * 8,   # WRONG dim
                [0.2] * 4,
            ],
            "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}],
        }
        with pytest.raises(ValueError, match="index 0"):
            store.import_data(data)

    def test_none_embedding_caught(self):
        """A None embedding in the list must be caught (len(None) → 0)."""
        store = _make_store_with_mocks(dim=4)
        data = {
            "ids": ["1", "2"],
            "documents": ["doc1", "doc2"],
            "embeddings": [[0.1] * 4, None],
            "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}],
        }
        with pytest.raises(ValueError, match="index 1"):
            store.import_data(data)

    def test_all_matching_dimensions_passes(self):
        """All embeddings with correct dimension must pass."""
        store = _make_store_with_mocks(dim=4)
        data = {
            "ids": ["1", "2", "3"],
            "documents": ["doc1", "doc2", "doc3"],
            "embeddings": [[0.1] * 4, [0.2] * 4, [0.3] * 4],
            "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}, {"doc_id": "d3"}],
        }
        store.import_data(data)
        store._db._collection.add.assert_called_once()


class TestImportDataAPIUnavailable:
    """P1-8: import_data must fail-open (warn, not raise) when API unavailable."""

    def test_no_embeddings_object_skips_dimension_check(self):
        """When self.embeddings is None, dimension check is skipped (fail-open)."""
        store = _make_store_with_mocks(embeddings_present=False, dim=4)
        data = {
            "ids": ["1", "2"],
            "documents": ["doc1", "doc2"],
            "embeddings": [[0.1] * 4, [0.2] * 4],
            "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}],
        }
        # Should NOT raise — fail-open
        store.import_data(data)
        store._db._collection.add.assert_called_once()

    def test_dimension_probe_failure_skips_check(self):
        """When embedding API probe fails, dimension check is skipped (fail-open)."""
        store = _make_store_with_mocks(dim=4)
        # Make the probe fail
        store.embeddings.embed_query.side_effect = RuntimeError("API down")
        data = {
            "ids": ["1", "2"],
            "documents": ["doc1", "doc2"],
            "embeddings": [[0.1] * 8, [0.2] * 8],  # wrong dim but check skipped
            "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}],
        }
        # Should NOT raise — API unavailable → fail-open
        store.import_data(data)
        store._db._collection.add.assert_called_once()


# ═══════════════════════════════════════════
# P1-B: _get_embedding_dimension negative caching
# ═══════════════════════════════════════════

class TestEmbeddingDimensionCaching:
    """P1-B: _get_embedding_dimension must cache failures to avoid repeated API calls."""

    def test_successful_probe_caches_result(self):
        """First successful call probes API; second call returns cache."""
        store = _make_store_with_mocks(dim=1536)
        assert store._dim_check_attempted is False

        dim1 = store._get_embedding_dimension()
        assert dim1 == 1536
        assert store._dim_check_attempted is True
        assert store._cached_embedding_dim == 1536

        # Second call should NOT re-probe
        call_count_before = store.embeddings.embed_query.call_count
        dim2 = store._get_embedding_dimension()
        assert dim2 == 1536
        assert store.embeddings.embed_query.call_count == call_count_before

    def test_failed_probe_caches_none(self):
        """Failed probe must cache None so subsequent calls don't retry."""
        store = _make_store_with_mocks(dim=4)
        store.embeddings.embed_query.side_effect = RuntimeError("API down")

        dim1 = store._get_embedding_dimension()
        assert dim1 is None
        assert store._dim_check_attempted is True
        assert store._cached_embedding_dim is None

        # Second call must NOT retry (would have called embed_query again)
        dim2 = store._get_embedding_dimension()
        assert dim2 is None
        # embed_query called only once (first probe), not retried
        assert store.embeddings.embed_query.call_count == 1

    def test_no_embeddings_returns_none_without_probe(self):
        """When embeddings is None, returns None without attempting probe."""
        store = _make_store_with_mocks(embeddings_present=False)
        dim = store._get_embedding_dimension()
        assert dim is None
        assert store._dim_check_attempted is False  # not attempted


# ═══════════════════════════════════════════
# P0-3 + P0-4: _rewrite_query_for_rag
# ═══════════════════════════════════════════

def _run_rewrite(query, history, mock_response=None, side_effect=None):
    """Run _rewrite_query_for_rag with a mocked LLMClient.

    Args:
        mock_response: LLMResponse for the mock chat() to return.
        side_effect: Exception/async behavior for the mock chat() to raise.
    """
    from app.api import chat_routes

    mock_client = MagicMock()

    if side_effect is not None:
        async def _chat_side_effect(*args, **kwargs):
            raise side_effect
        mock_client.chat = _chat_side_effect
    elif mock_response is not None:
        async def _chat_return(*args, **kwargs):
            return mock_response
        mock_client.chat = _chat_return
    else:
        mock_client.chat = MagicMock(side_effect=RuntimeError("no mock set"))

    with patch.object(chat_routes, "LLMClient", return_value=mock_client):
        return asyncio.run(_rewrite_query_for_rag_wrapped(query, history))


async def _rewrite_query_for_rag_wrapped(query, history):
    """Thin wrapper to call the async _rewrite_query_for_rag."""
    from app.api.chat_routes import _rewrite_query_for_rag
    return await _rewrite_query_for_rag(
        query=query,
        history=history,
        api_key="fake-key",
        base_url="http://fake",
        model="fake-model",
        provider="openai",
    )


class TestQueryRewriteNewlineCleanup:
    """P0-3: _rewrite_query_for_rag must clean newlines/whitespace from LLM output.

    Bug: LLM may return multi-line output which breaks BM25 tokenization
    and embedding models.
    """

    def test_multiline_output_collapsed_to_single_line(self):
        """Multi-line LLM output must be collapsed to single line."""
        resp = LLMResponse(content="STM32F4\nDMA\n配置\n传输", model="fake", usage={})
        result = _run_rewrite("STM32F4怎么配置DMA", [], mock_response=resp)
        assert "\n" not in result
        assert result == "STM32F4 DMA 配置 传输"

    def test_excess_whitespace_collapsed(self):
        """Multiple spaces/tabs in LLM output must be collapsed."""
        resp = LLMResponse(content="  STM32F4   DMA   配置  ", model="fake", usage={})
        result = _run_rewrite("STM32F4 DMA配置", [], mock_response=resp)
        assert result == "STM32F4 DMA 配置"

    def test_clean_output_preserved(self):
        """Already-clean output must pass through unchanged."""
        resp = LLMResponse(content="STM32F4 DMA 配置", model="fake", usage={})
        result = _run_rewrite("STM32F4怎么配置DMA", [], mock_response=resp)
        assert result == "STM32F4 DMA 配置"


class TestQueryRewriteMultimodalHistory:
    """P0-4: _rewrite_query_for_rag must extract text from multimodal history.

    Bug: Multimodal content (list[dict]) was silently dropped, breaking
    pronoun resolution for image+text conversations.
    """

    def test_multimodal_history_text_extracted(self):
        """History with list content (text+image) must extract text part."""
        history = [
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "STM32F4怎么接线"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            ),
        ]
        resp = LLMResponse(content="STM32F4 接线 配置", model="fake", usage={})
        result = _run_rewrite("STM32F4的DMA怎么配置", history, mock_response=resp)
        # The rewrite succeeded — the key assertion is that it didn't fall
        # back to the original query due to multimodal history crashing.
        assert result == "STM32F4 接线 配置"

    def test_multimodal_history_only_image_no_text(self):
        """History with only image (no text) must not crash."""
        history = [
            ChatMessage(
                role="user",
                content=[
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            ),
        ]
        resp = LLMResponse(content="DMA 配置 传输", model="fake", usage={})
        result = _run_rewrite("STM32F4的DMA怎么配置", history, mock_response=resp)
        assert result == "DMA 配置 传输"

    def test_history_none_does_not_crash(self):
        """P1: history=None must not crash (defensive default to [])."""
        resp = LLMResponse(content="DMA 配置", model="fake", usage={})
        result = _run_rewrite("STM32F4的DMA怎么配置", None, mock_response=resp)
        assert result == "DMA 配置"

    def test_mixed_str_and_list_history(self):
        """History with mixed str and list content must handle both."""
        history = [
            ChatMessage(role="user", content="STM32F4怎么用"),
            ChatMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "STM32F4配置GPIO先开RCC时钟"},
                ],
            ),
        ]
        resp = LLMResponse(content="STM32F4 GPIO RCC", model="fake", usage={})
        result = _run_rewrite("STM32F4的DMA怎么配置", history, mock_response=resp)
        assert result == "STM32F4 GPIO RCC"


class TestQueryRewriteFallbacks:
    """_rewrite_query_for_rag must fall back to original query on failures."""

    def test_empty_query_returns_original(self):
        """Empty query must skip rewrite and return as-is."""
        result = _run_rewrite("", [], mock_response=MagicMock())
        assert result == ""

    def test_whitespace_only_query_returns_original(self):
        """Whitespace-only query must skip rewrite."""
        result = _run_rewrite("   ", [], mock_response=MagicMock())
        assert result == "   "

    def test_short_query_skips_rewrite(self):
        """Very short query (≤6 chars) must skip rewrite (likely chip names)."""
        # mock_client won't be called, but provide anyway
        result = _run_rewrite("STM32", [], mock_response=MagicMock())
        assert result == "STM32"

    def test_llm_returns_empty_falls_back(self):
        """Empty LLM response must fall back to original query."""
        resp = LLMResponse(content="", model="fake", usage={})
        query = "STM32F4怎么配置DMA传输"
        result = _run_rewrite(query, [], mock_response=resp)
        assert result == query

    def test_llm_returns_none_content_falls_back(self):
        """None content in LLM response must fall back to original."""
        resp = LLMResponse(content=None, model="fake", usage={})
        query = "STM32F4怎么配置DMA传输"
        result = _run_rewrite(query, [], mock_response=resp)
        assert result == query

    def test_rewrite_too_long_falls_back(self):
        """Rewrite > 3x original length must fall back to original."""
        query = "DMA配置"  # 4 chars, but > 6 bytes... wait, len check is on query
        # Use a query longer than 6 chars to actually trigger rewrite path
        query = "STM32F4怎么配置DMA"
        long_rewrite = "x" * (len(query) * 4)  # 4x longer than query
        resp = LLMResponse(content=long_rewrite, model="fake", usage={})
        result = _run_rewrite(query, [], mock_response=resp)
        assert result == query, f"Too-long rewrite should fall back, got: {result}"

    def test_exception_falls_back_to_original(self):
        """Any exception in LLM call must fall back to original query."""
        query = "STM32F4怎么配置DMA传输"
        result = _run_rewrite(query, [], side_effect=RuntimeError("LLM down"))
        assert result == query


class TestQueryRewriteTimeout:
    """_rewrite_query_for_rag must handle timeout gracefully."""

    def test_timeout_falls_back_to_original(self):
        """asyncio.TimeoutError must fall back to original query."""
        import asyncio as _asyncio

        query = "STM32F4怎么配置DMA传输"

        async def _chat_timeout(*args, **kwargs):
            raise _asyncio.TimeoutError()

        from app.api import chat_routes
        mock_client = MagicMock()
        mock_client.chat = _chat_timeout

        with patch.object(chat_routes, "LLMClient", return_value=mock_client):
            result = asyncio.run(_rewrite_query_for_rag_wrapped(query, []))

        assert result == query
