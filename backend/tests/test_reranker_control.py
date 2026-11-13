"""Reranker load status + in-process hot reload (no real model is loaded)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from src.agent.core.toolkit import tool_router
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.retrieval.search_docs import _build_search_result
from src.rag import reranker

_REQUIRED_STATUS_KEYS = {
    "state",
    "model",
    "available_mb",
    "required_mb",
    "reason",
    "loaded_at",
    "retry_interval_sec",
}


@pytest.fixture(autouse=True)
def _isolated_reranker_state(monkeypatch):
    monkeypatch.setattr(reranker, "_RERANKER", None)
    monkeypatch.setattr(reranker, "_failed_at", None)
    monkeypatch.setattr(reranker, "_ACTIVE_LOADS", 0)
    monkeypatch.setattr(reranker, "_LAST_REASON", None)
    monkeypatch.setattr(reranker, "_LAST_AVAILABLE_MB", None)
    monkeypatch.setattr(reranker, "_LOADED_AT", None)


def _wait_for_state(expected: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = reranker.get_reranker_status()
        if status["state"] == expected:
            return status
        time.sleep(0.01)
    pytest.fail(
        f"reranker state {expected!r} not reached; "
        f"last={reranker.get_reranker_status()!r}"
    )


def _fused_result() -> SimpleNamespace:
    return SimpleNamespace(
        content="Parent evidence text.",
        metadata={
            "title": "Manual.pdf",
            "big_chunk_id": "big-a",
            "small_chunks": [
                {
                    "id": "small-a",
                    "chunk_index": 0,
                    "score": 0.9,
                    "text": "GPIO mode bits.",
                }
            ],
        },
        score=0.9,
        doc_id="doc-a",
        kb_id="kb-a",
        kb_name="Knowledge base A",
    )


def test_status_shape_and_initial_not_loaded() -> None:
    status = reranker.get_reranker_status()

    assert set(status) == _REQUIRED_STATUS_KEYS
    assert status["state"] == "not_loaded"
    assert isinstance(status["model"], str) and status["model"]
    assert status["available_mb"] is None
    assert isinstance(status["required_mb"], int)
    assert status["reason"] is None
    assert status["loaded_at"] is None
    assert isinstance(status["retry_interval_sec"], float)


def test_memory_guard_marks_unavailable_with_reason(monkeypatch) -> None:
    required_mb = reranker.get_reranker_status()["required_mb"]
    monkeypatch.setattr(reranker, "_available_memory_mb", lambda: required_mb - 1)

    assert reranker.get_reranker() is None

    status = reranker.get_reranker_status()
    assert status["state"] == "unavailable"
    assert status["available_mb"] == required_mb - 1
    assert status["required_mb"] == required_mb
    assert "insufficient memory" in status["reason"]
    assert status["loaded_at"] is None


def test_successful_load_reports_loaded(monkeypatch) -> None:
    fake_model = SimpleNamespace()
    monkeypatch.setattr(reranker, "_available_memory_mb", lambda: 10**6)
    monkeypatch.setattr(reranker, "_load_cross_encoder", lambda: fake_model)

    assert reranker.get_reranker() is fake_model

    status = reranker.get_reranker_status()
    assert status["state"] == "loaded"
    assert isinstance(status["loaded_at"], float)
    assert status["reason"] is None


def test_load_failure_marks_unavailable(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("synthetic load failure")

    monkeypatch.setattr(reranker, "_available_memory_mb", lambda: 10**6)
    monkeypatch.setattr(reranker, "_load_cross_encoder", _boom)

    assert reranker.get_reranker() is None

    status = reranker.get_reranker_status()
    assert status["state"] == "unavailable"
    assert "synthetic load failure" in status["reason"]
    assert status["loaded_at"] is None


def test_reload_starts_once_and_reports_loading(monkeypatch) -> None:
    release = threading.Event()
    entered = threading.Event()
    calls: list[int] = []

    def _slow_load():
        calls.append(1)
        entered.set()
        release.wait(10)
        return SimpleNamespace()

    monkeypatch.setattr(reranker, "_available_memory_mb", lambda: 10**6)
    monkeypatch.setattr(reranker, "_load_cross_encoder", _slow_load)

    try:
        first = reranker.reload_reranker()
        assert first["started"] is True
        assert first["status"]["state"] == "loading"
        assert entered.wait(5)

        second = reranker.reload_reranker()
        assert second["started"] is False
        assert second["status"]["state"] == "loading"
    finally:
        release.set()

    status = _wait_for_state("loaded")
    assert len(calls) == 1
    assert isinstance(status["loaded_at"], float)
    assert status["reason"] is None


def test_search_docs_result_reranker_status_lands_in_metadata(monkeypatch) -> None:
    monkeypatch.setattr(reranker, "_RERANKER", object())

    result = _build_search_result([_fused_result()], ToolContext())

    assert result["reranker"] == reranker.get_reranker_status()
    assert result["reranker"]["state"] == "loaded"

    state = tool_router._DispatchState(
        call_id="call-reranker",
        spec=SimpleNamespace(name="search_docs"),
        args={"query": "gpio"},
        ctx=ToolContext(),
        start_ms=0,
    )
    envelope = tool_router._success_envelope(state, result)

    assert envelope.metadata.reranker == result["reranker"]
    assert "reranker" not in (envelope.data or {})
    assert envelope.data["results"]


def test_reranker_routes_expose_status_and_reload(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr(reranker, "_available_memory_mb", lambda: 10**6)
    monkeypatch.setattr(reranker, "_load_cross_encoder", lambda: SimpleNamespace())

    client = TestClient(create_app())

    status_body = client.get("/api/rag/reranker").json()
    assert status_body["success"] is True
    assert set(status_body["data"]) == _REQUIRED_STATUS_KEYS

    reload_body = client.post("/api/rag/reranker/reload").json()
    assert reload_body["success"] is True
    assert reload_body["data"]["started"] is True
    assert reload_body["data"]["status"]["state"] == "loading"

    assert _wait_for_state("loaded")["loaded_at"] is not None
