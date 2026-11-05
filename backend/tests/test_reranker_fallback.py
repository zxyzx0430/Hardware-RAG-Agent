"""Regression: request-path rerank must never wait for a cold model load."""
from __future__ import annotations

import pytest

from src.rag import reranker


@pytest.fixture(autouse=True)
def _reset_reranker_state(monkeypatch):
    monkeypatch.setattr(reranker, "_RERANKER", None)
    monkeypatch.setattr(reranker, "_failed_at", None)


def test_cold_rerank_returns_rrf_fallback_without_loading(monkeypatch):
    def _must_not_load():
        raise AssertionError("request path must not call get_reranker()")

    monkeypatch.setattr(reranker, "get_reranker", _must_not_load)

    assert reranker.rerank("gpio", ["a", "b"]) == [(0, 0.0), (1, 0.0)]


def test_loaded_reranker_is_used(monkeypatch):
    class _FakeModel:
        def predict(self, pairs):
            return [0.1, 0.9]

    monkeypatch.setattr(reranker, "_RERANKER", _FakeModel())

    assert reranker.rerank("gpio", ["a", "b"], top_k=1) == [(1, 0.9)]
