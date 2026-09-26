"""Regression coverage for request-scoped Agent tool configuration.

These tests use the real ToolSpec/ToolRouter path but replace retrieval and
HTTP boundaries with in-memory fakes. They must not touch a user knowledge
base, make network calls, or write audit records.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from src.agent import agent_factory
from src.agent.core.toolkit import tool_router
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.sse_helpers import convert_tool_message_to_sse
from src.agent.tools.groups.retrieval import web_search as web_search_module
from src.rag import search as search_module
from src.config.settings import settings


class _NoopAuditRecorder:
    def record(self, _record: object) -> None:
        """Keep this isolated test from touching the local audit database."""


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"results": []}


def _fake_search_result(kb_id: str, index: int, score: float) -> SimpleNamespace:
    chunk_id = f"{kb_id}-chunk-{index}"
    return SimpleNamespace(
        kb_id=kb_id,
        kb_name=f"Knowledge base {kb_id}",
        doc_id=f"{kb_id}-manual.pdf",
        content=f"mock passage {kb_id} {index}",
        score=score,
        metadata={
            "title": f"{kb_id} manual",
            "small_chunk_id": chunk_id,
            "small_chunks": [
                {
                    "id": chunk_id,
                    "score": score,
                    "chunk_index": index,
                    "text": f"mock passage {kb_id} {index}",
                }
            ],
        },
    )


def _source_events(tool_result: dict, call_id: str) -> list[dict]:
    message = ToolMessage(
        content=json.dumps(tool_result),
        name="search_docs",
        tool_call_id=call_id,
    )
    sse = convert_tool_message_to_sse(message, {})
    events = [
        json.loads(line.removeprefix("data: "))
        for line in sse.splitlines()
        if line.startswith("data: ")
    ]
    return [event for event in events if event.get("type") == "source"]


@pytest.mark.asyncio
async def test_interleaved_agent_requests_use_their_own_tool_config(monkeypatch):
    """Each request must execute its own retrieval and credential settings."""
    registry: dict[str, object] = {}
    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", registry)
    monkeypatch.setattr(agent_factory, "_filter_disabled_tools", lambda tools: tools)

    router = ToolRouter(audit_recorder=_NoopAuditRecorder())
    monkeypatch.setattr(
        ToolRouter,
        "get_default",
        classmethod(lambda _cls: router),
    )
    # Seed a fake process-wide default so a mistaken default-instance dispatch
    # is observable without reading any real credential or contacting a service.
    monkeypatch.setattr(settings, "tavily_api_key", "registry-default-key")

    retrieval_calls: list[dict] = []
    mock_results = [
        _fake_search_result("kb-a", 1, 0.91),
        _fake_search_result("kb-a", 2, 0.56),
        _fake_search_result("kb-a", 3, 0.22),
        _fake_search_result("kb-b", 1, 0.83),
        _fake_search_result("kb-b", 2, 0.66),
        _fake_search_result("kb-b", 3, 0.18),
    ]

    async def fake_search_docs_core(query, top_k, kb_ids, threshold, doc_filter):
        retrieval_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "kb_ids": kb_ids,
                "threshold": threshold,
                "doc_filter": doc_filter,
            }
        )
        selected = [
            result
            for result in mock_results
            if (not kb_ids or result.kb_id in kb_ids)
            and result.score >= threshold
        ]
        return selected[:top_k]

    monkeypatch.setattr(search_module, "search_docs_core", fake_search_docs_core)

    web_calls: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json, headers):
            web_calls.append({"url": url, "api_key": json["api_key"]})
            return _FakeResponse()

    monkeypatch.setattr(web_search_module.httpx, "AsyncClient", FakeAsyncClient)

    request_a = SimpleNamespace(
        top_k=2,
        kb_ids=["kb-a"],
        relevance_threshold=0.5,
        tool_keys={
            "tavily": "request-a-key",
            "tavily_base_url": "https://request-a.invalid/api",
        },
        permission_mode="default",
        session_id="session-a",
    )
    request_b = SimpleNamespace(
        top_k=1,
        kb_ids=["kb-b"],
        relevance_threshold=0.7,
        tool_keys={
            "tavily": "request-b-key",
            "tavily_base_url": "https://request-b.invalid/api",
        },
        permission_mode="default",
        session_id="session-b",
    )

    tools_a = {tool.name: tool for tool in agent_factory.build_tool_specs(request_a)}
    tools_b = {tool.name: tool for tool in agent_factory.build_tool_specs(request_b)}

    result_a1 = await tools_a["search_docs"].ainvoke({"query": "request-a-1"})
    await tools_a["web_search"].ainvoke({"query": "request-a-web-1"})
    result_b1 = await tools_b["search_docs"].ainvoke({"query": "request-b-1"})
    await tools_b["web_search"].ainvoke({"query": "request-b-web-1"})
    result_a2 = await tools_a["search_docs"].ainvoke({"query": "request-a-2"})
    await tools_a["web_search"].ainvoke({"query": "request-a-web-2"})
    result_b2 = await tools_b["search_docs"].ainvoke({"query": "request-b-2"})
    await tools_b["web_search"].ainvoke({"query": "request-b-web-2"})

    expected_retrieval = [
        {"query": "request-a-1", "top_k": 2, "kb_ids": ["kb-a"], "threshold": 0.5, "doc_filter": ""},
        {"query": "request-b-1", "top_k": 1, "kb_ids": ["kb-b"], "threshold": 0.7, "doc_filter": ""},
        {"query": "request-a-2", "top_k": 2, "kb_ids": ["kb-a"], "threshold": 0.5, "doc_filter": ""},
        {"query": "request-b-2", "top_k": 1, "kb_ids": ["kb-b"], "threshold": 0.7, "doc_filter": ""},
    ]
    expected_web_calls = [
        {"url": "https://request-a.invalid/api/search", "api_key": "request-a-key"},
        {"url": "https://request-b.invalid/api/search", "api_key": "request-b-key"},
        {"url": "https://request-a.invalid/api/search", "api_key": "request-a-key"},
        {"url": "https://request-b.invalid/api/search", "api_key": "request-b-key"},
    ]
    observed_source_kbs = {
        "request-a-1": {
            event["kb_id"]
            for event in _source_events(result_a1, "call-a-1")
        },
        "request-b-1": {
            event["kb_id"]
            for event in _source_events(result_b1, "call-b-1")
        },
        "request-a-2": {
            event["kb_id"]
            for event in _source_events(result_a2, "call-a-2")
        },
        "request-b-2": {
            event["kb_id"]
            for event in _source_events(result_b2, "call-b-2")
        },
    }

    failures: list[str] = []
    if retrieval_calls != expected_retrieval:
        failures.append(
            "retrieval received default/shared settings instead of each request: "
            f"{retrieval_calls!r}"
        )
    if web_calls != expected_web_calls:
        failures.append(
            "web_search received default/shared credentials instead of each request: "
            f"{web_calls!r}"
        )
    expected_source_kbs = {
        "request-a-1": {"kb-a"},
        "request-b-1": {"kb-b"},
        "request-a-2": {"kb-a"},
        "request-b-2": {"kb-b"},
    }
    if observed_source_kbs != expected_source_kbs:
        failures.append(
            "source SSE included a KB not selected for the request: "
            f"{observed_source_kbs!r}"
        )

    assert not failures, "\n".join(failures)
