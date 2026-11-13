"""Regression tests for token usage accounting.

Covers two defects that made the settings chart unreadable: every row shared
one process-import timestamp, and providers reporting cached prompt tokens
(OpenAI prompt_tokens_details.cached_tokens / DeepSeek
prompt_cache_hit_tokens) were recorded as zero cache hits.
"""
from __future__ import annotations

import datetime
import time
from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, TokenUsage
from app.api.chat_helpers import _sum_usage_calls
from src.agent.sse_adapter import _capture_usage
from src.llm.client import _provider_usage


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'usage.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_created_at_is_evaluated_per_row(tmp_path):
    db = _session(tmp_path)
    db.add(TokenUsage(model="m", prompt_tokens=1, completion_tokens=1, total_tokens=2))
    db.commit()
    first = db.query(TokenUsage).one().created_at
    time.sleep(0.01)
    db.add(TokenUsage(model="m", prompt_tokens=1, completion_tokens=1, total_tokens=2))
    db.commit()
    rows = db.query(TokenUsage).order_by(TokenUsage.id).all()
    assert first == rows[0].created_at
    assert rows[1].created_at > rows[0].created_at
    db.close()


def test_provider_usage_reads_openai_cached_tokens():
    usage = SimpleNamespace(
        prompt_tokens=100, completion_tokens=5, total_tokens=105,
        prompt_tokens_details=SimpleNamespace(cached_tokens=64),
    )
    assert _provider_usage(usage, "deepseek/deepseek-v4.1-flash") == {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "total_tokens": 105,
        "cached_tokens": 64,
        "model": "deepseek/deepseek-v4.1-flash",
    }


def test_provider_usage_reads_deepseek_cache_hit_field():
    usage = SimpleNamespace(
        prompt_tokens=200, completion_tokens=7, total_tokens=207,
        prompt_tokens_details=None, prompt_cache_hit_tokens=128,
    )
    payload = _provider_usage(usage, None)
    assert payload["cached_tokens"] == 128
    assert payload["prompt_tokens"] == 200
    assert "model" not in payload


def test_provider_usage_without_usage_is_none():
    assert _provider_usage(None, "m") is None


def test_capture_usage_accumulates_tokens_and_served_model():
    from langchain_core.messages import AIMessageChunk

    state: dict = {}
    chunk = AIMessageChunk(
        content="",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 5,
            "total_tokens": 105,
            "input_token_details": {"cache_read": 64},
        },
        response_metadata={"model_name": "deepseek/deepseek-v4.1-flash"},
    )
    _capture_usage(chunk, state)
    call_id, per_call = _capture_usage(chunk, state)
    assert per_call == {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "total_tokens": 105,
        "cached_tokens": 64,
        "model": "deepseek/deepseek-v4.1-flash",
    }
    assert call_id
    assert state["usage_event_count"] == 2
    assert state["usage_by_call"][call_id] == per_call
    assert state["usage"] == {
        "prompt_tokens": 200,
        "completion_tokens": 10,
        "total_tokens": 210,
        "cached_tokens": 128,
        "model": "deepseek/deepseek-v4.1-flash",
    }


def test_merge_usage_totals_sums_calls_for_done_event():
    total = _sum_usage_calls({
        "call-a": {
            "prompt_tokens": 100, "completion_tokens": 5,
            "total_tokens": 105, "cached_tokens": 40, "model": "served-a",
        },
        "call-b": {
            "prompt_tokens": 200, "completion_tokens": 7,
            "total_tokens": 207, "cached_tokens": 50, "model": "served-b",
        },
    })
    assert total == {
        "prompt_tokens": 300,
        "completion_tokens": 12,
        "total_tokens": 312,
        "cached_tokens": 90,
        "model": "served-b",
    }


def test_capture_usage_replaces_estimate_with_real_for_same_call():
    from langchain_core.messages import AIMessageChunk

    state: dict = {}
    estimated = AIMessageChunk(
        content="", id="lc_run--1",
        usage_metadata={
            "input_tokens": 2035, "output_tokens": 13,
            "total_tokens": 2048,
            "input_token_details": {}, "output_token_details": {},
        },
        response_metadata={"model_name": "deepseek-v4.1-flash"},
    )
    real = AIMessageChunk(
        content="", id="lc_run--1",
        usage_metadata={
            "input_tokens": 32, "output_tokens": 17,
            "total_tokens": 49,
            "input_token_details": {"cache_read": 0},
            "output_token_details": {"reasoning": 14},
        },
    )
    _capture_usage(estimated, state)
    _capture_usage(real, state)
    assert state["usage"]["prompt_tokens"] == 32
    assert state["usage"]["completion_tokens"] == 17
    assert state["usage"]["model"] == "deepseek-v4.1-flash"
    assert len(state["usage_by_call"]) == 1


def test_record_token_usage_upserts_real_usage_over_estimate(monkeypatch, tmp_path):
    import app.api.chat_helpers as chat_helpers

    engine = create_engine(f"sqlite:///{(tmp_path / 'upsert.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def fake_ctx():
        db = Session()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    monkeypatch.setattr(chat_helpers, "get_db_ctx", fake_ctx)
    chat_helpers._record_token_usage(
        model="requested", provider="p", session_id="s",
        usage_data={"prompt_tokens": 2035, "completion_tokens": 13,
                    "total_tokens": 2048, "cached_tokens": 0},
        run_id="r1", call_id="call-1", source="agent",
    )
    chat_helpers._record_token_usage(
        model="requested", provider="p", session_id="s",
        usage_data={"prompt_tokens": 32, "completion_tokens": 17,
                    "total_tokens": 49, "cached_tokens": 0,
                    "model": "served"},
        run_id="r1", call_id="call-1", source="agent",
    )
    with Session() as db:
        rows = db.query(TokenUsage).all()
        assert len(rows) == 1
        assert rows[0].prompt_tokens == 32
        assert rows[0].model == "served"


def test_stats_exclude_estimates_and_sessionless_rows(monkeypatch, tmp_path):
    import app.api.chat_routes as chat_routes

    engine = create_engine(f"sqlite:///{(tmp_path / 'stats.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    now = datetime.datetime.now(datetime.timezone.utc)
    with Session() as db:
        db.add_all([
            TokenUsage(
                model="provider-model", source="agent", session_id="s1",
                prompt_tokens=1000, completion_tokens=100,
                cached_tokens=400, total_tokens=1100, created_at=now,
            ),
            TokenUsage(
                model="provider-model", source="estimated", session_id="s1",
                prompt_tokens=9999, completion_tokens=999,
                total_tokens=10998, created_at=now,
            ),
            TokenUsage(
                model="provider-model", source="agent", session_id="",
                prompt_tokens=7777, completion_tokens=777,
                total_tokens=8554, created_at=now,
            ),
        ])
        db.commit()

    @contextmanager
    def fake_ctx():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(chat_routes, "get_db_ctx", fake_ctx)
    result = chat_routes.token_usage_stats(days=30)["data"]
    assert result["summary"]["total_input"] == 1000
    assert result["summary"]["total_output"] == 100
    assert result["summary"]["total_cached"] == 400
    assert result["summary"]["hit_rate"] == 0.4
    assert [row["calls"] for row in result["by_model"]] == [1]
