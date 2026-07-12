"""Task 4 verification: parallel retrieval + LRU cache.

Tests:
1. LRU cache functions (_cache_key / _get_cached / _set_cached)
2. Parallel execution: 3 mock KBs each sleep 1s → total ≈ 1s (not 3s)
3. Cache hit: second search_docs_core call with same params is instant
"""

import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_cache_functions():
    """Test LRU cache key, get, set, TTL, eviction."""
    from src.rag.search import (
        _cache_key, _get_cached, _set_cached, _SEARCH_CACHE,
        _CACHE_TTL_SECONDS, _CACHE_MAX_SIZE,
    )
    _SEARCH_CACHE.clear()

    # Key order-insensitivity
    k1 = _cache_key("hello", ["kb-a", "kb-b"], 5, 0.5)
    k2 = _cache_key("hello", ["kb-b", "kb-a"], 5, 0.5)
    assert k1 == k2, "kb_ids order should not matter"

    # Set + get
    _set_cached(k1, ["result1"])
    assert _get_cached(k1) == ["result1"], "cache miss after set"

    # Different params = different key
    k3 = _cache_key("hello", ["kb-a", "kb-b"], 10, 0.5)
    assert _get_cached(k3) is None, "different top_k should miss"

    # Eviction (LRU)
    _SEARCH_CACHE.clear()
    for i in range(_CACHE_MAX_SIZE + 10):
        _set_cached(_cache_key(f"q{i}", None, 5, 0.0), [f"r{i}"])
    assert len(_SEARCH_CACHE) == _CACHE_MAX_SIZE, f"cache should cap at {_CACHE_MAX_SIZE}"
    # Oldest entries evicted
    assert _get_cached(_cache_key("q0", None, 5, 0.0)) is None, "oldest should be evicted"
    # Newest still present
    assert _get_cached(_cache_key(f"q{_CACHE_MAX_SIZE + 9}", None, 5, 0.0)) == [f"r{_CACHE_MAX_SIZE + 9}"]

    print("[PASS] cache functions: key order-insensitivity, get/set, LRU eviction")


def test_parallel_pattern():
    """Verify asyncio.gather + to_thread runs KB searches in parallel."""
    import asyncio

    async def fake_search_all_enabled():
        """Mimics search_all_enabled: 3 KBs, each sleeps 1s via to_thread."""
        def slow_kb_search(kb_id):
            time.sleep(1.0)
            return [f"result_from_{kb_id}"]

        tasks = [asyncio.to_thread(slow_kb_search, kb) for kb in ["kb1", "kb2", "kb3"]]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)
        all_results = []
        for kb, res in zip(["kb1", "kb2", "kb3"], results_nested):
            if isinstance(res, Exception):
                continue
            all_results.extend(res)
        return all_results

    start = time.perf_counter()
    results = asyncio.run(fake_search_all_enabled())
    elapsed = time.perf_counter() - start
    assert len(results) == 3, f"expected 3 results, got {len(results)}"
    assert elapsed < 1.8, f"parallel should take ~1s not {elapsed:.2f}s"
    print(f"[PASS] parallel: 3 KBs × 1s = {elapsed:.2f}s (expected ~1.0s, not 3.0s)")


def test_search_docs_core_cache_hit():
    """Verify search_docs_core caches: 2nd call with same params skips retrieval."""
    import asyncio
    from src.rag.search import search_docs_core, _SEARCH_CACHE
    _SEARCH_CACHE.clear()

    call_count = 0

    class FakeKBManager:
        async def search_all_enabled(
            self, query, k=3, kb_ids=None, score_threshold=0.0, doc_filter: str = "",
        ):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.5)
            return [f"result_for_{query}"]

    # Patch get_kb_manager in the kb_manager module
    import src.rag.kb_manager as kbm
    original = kbm.get_kb_manager
    kbm.get_kb_manager = lambda: FakeKBManager()
    try:
        # First call: cold cache, triggers search_all_enabled
        start1 = time.perf_counter()
        r1 = asyncio.run(search_docs_core("test_query", top_k=5, kb_ids=["kb1"], threshold=0.3))
        elapsed1 = time.perf_counter() - start1
        assert call_count == 1, f"expected 1 retrieval, got {call_count}"
        assert len(r1) == 1

        # Second call: same params → cache hit, no retrieval
        start2 = time.perf_counter()
        r2 = asyncio.run(search_docs_core("test_query", top_k=5, kb_ids=["kb1"], threshold=0.3))
        elapsed2 = time.perf_counter() - start2
        assert call_count == 1, f"cache should prevent 2nd retrieval, but call_count={call_count}"
        assert elapsed2 < 0.05, f"cache hit should be instant, took {elapsed2:.3f}s"
        assert r1 == r2, "cached result should match"

        # Different query → cache miss, triggers retrieval
        r3 = asyncio.run(search_docs_core("different_query", top_k=5, kb_ids=["kb1"], threshold=0.3))
        assert call_count == 2, f"different query should miss cache, call_count={call_count}"

        print(f"[PASS] cache: cold={elapsed1:.2f}s, hit={elapsed2:.4f}s, "
              f"retrievals={call_count} (expected 2: 1 miss + 1 hit + 1 miss)")
    finally:
        kbm.get_kb_manager = original


if __name__ == "__main__":
    test_cache_functions()
    test_parallel_pattern()
    test_search_docs_core_cache_hit()
    print("\n✓ All Task 4 tests passed")
