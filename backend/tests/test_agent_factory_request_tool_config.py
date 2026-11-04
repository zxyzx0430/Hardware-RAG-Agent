"""Regression coverage for request-scoped Agent tool configuration.

These tests use the real ToolSpec/ToolRouter path but replace retrieval and
HTTP boundaries with in-memory fakes. They must not touch a user knowledge
base, make network calls, or write audit records.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from pydantic import BaseModel

from src.agent import agent_factory
from src.agent.core.toolkit import tool_router
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import ToolSpec
from src.agent.exceptions import ToolContext
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


def _tool_result_data(tool_result: dict) -> dict:
    data = tool_result.get("data")
    return data if isinstance(data, dict) else tool_result


class _RequestToolArgs(BaseModel):
    value: str = "probe"


class _InjectedRequestTool(ToolSpec):
    name: str = "request_scoped_probe"
    description: str = "Test-only request-scoped tool."
    args_schema: type[BaseModel] = _RequestToolArgs

    async def execute(self, args, ctx):
        return {"output": f"{ctx.session_id}:{args.get('value', '')}"}


def _patch_minimal_tool_builder(monkeypatch, base_tools):
    registry: dict[str, object] = {}
    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", registry)
    monkeypatch.setattr(agent_factory, "ensure_default_tools_registered", lambda: None)
    monkeypatch.setattr(agent_factory, "_assemble_all_tools", lambda _payload: list(base_tools))
    monkeypatch.setattr(agent_factory, "_filter_disabled_tools", lambda tools: list(tools))
    return registry


def test_build_tool_specs_includes_preconfigured_request_tool(monkeypatch):
    base_tool = _InjectedRequestTool(name="base_probe")
    injected_tool = _InjectedRequestTool(name="request_scoped_install")
    registry = _patch_minimal_tool_builder(monkeypatch, [base_tool])
    context = ToolContext(request_tools={injected_tool.name: injected_tool})

    tools = agent_factory.build_tool_specs(
        SimpleNamespace(skills_mode="off"), tool_context=context,
    )

    assert tools == [base_tool, injected_tool]
    assert context.request_tools[base_tool.name] is base_tool
    assert context.request_tools[injected_tool.name] is injected_tool
    assert injected_tool._ctx is context
    assert injected_tool.name not in registry


def test_build_tool_specs_rejects_request_tool_name_conflict(monkeypatch):
    built_tool = _InjectedRequestTool(name="shared_name")
    injected_tool = _InjectedRequestTool(name="shared_name")
    _patch_minimal_tool_builder(monkeypatch, [built_tool])
    context = ToolContext(request_tools={injected_tool.name: injected_tool})

    with pytest.raises(ValueError, match="request tool name conflict"):
        agent_factory.build_tool_specs(
            SimpleNamespace(skills_mode="off"), tool_context=context,
        )


def test_build_tool_specs_rejects_invalid_request_tool(monkeypatch):
    _patch_minimal_tool_builder(monkeypatch, [])
    context = ToolContext(request_tools={"invalid": object()})

    with pytest.raises(ValueError, match="Invalid request tool snapshot"):
        agent_factory.build_tool_specs(
            SimpleNamespace(skills_mode="off"), tool_context=context,
        )


def test_build_tool_specs_keeps_request_tool_when_context_is_rebuilt(monkeypatch):
    injected_tool = _InjectedRequestTool(name="request_scoped_install")
    context = ToolContext(request_tools={injected_tool.name: injected_tool})
    _patch_minimal_tool_builder(
        monkeypatch, [_InjectedRequestTool(name="base_probe")],
    )
    payload = SimpleNamespace(skills_mode="off")

    first_tools = agent_factory.build_tool_specs(payload, tool_context=context)
    second_tools = agent_factory.build_tool_specs(payload, tool_context=context)

    assert first_tools[-1] is injected_tool
    assert second_tools[-1] is injected_tool
    assert context.request_tools[injected_tool.name] is injected_tool


@pytest.mark.asyncio
async def test_request_tools_are_isolated_between_interleaved_agent_builds(monkeypatch):
    registry = _patch_minimal_tool_builder(monkeypatch, [])
    default_tool = _InjectedRequestTool(name="request_scoped_probe")
    default_tool._ctx = ToolContext(session_id="process-default")
    registry[default_tool.name] = default_tool
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())
    monkeypatch.setattr(
        ToolRouter,
        "get_default",
        classmethod(lambda _cls: router),
    )
    tool_a = _InjectedRequestTool(name="request_scoped_probe")
    tool_b = _InjectedRequestTool(name="request_scoped_probe")
    context_a = ToolContext(
        session_id="request-a", request_tools={tool_a.name: tool_a},
    )
    context_b = ToolContext(
        session_id="request-b", request_tools={tool_b.name: tool_b},
    )

    tools_a = agent_factory.build_tool_specs(
        SimpleNamespace(skills_mode="off"), tool_context=context_a,
    )
    tools_b = agent_factory.build_tool_specs(
        SimpleNamespace(skills_mode="off"), tool_context=context_b,
    )
    results = await asyncio.gather(
        tools_a[0].ainvoke({"value": "a1"}),
        tools_b[0].ainvoke({"value": "b1"}),
        tools_a[0].ainvoke({"value": "a2"}),
    )

    assert [result["output"] for result in results] == [
        "request-a:a1", "request-b:b1", "request-a:a2",
    ]
    assert tools_a[0] is tool_a
    assert tools_b[0] is tool_b
    assert context_a.request_tools[tool_a.name] is tool_a
    assert context_b.request_tools[tool_b.name] is tool_b
    assert registry[default_tool.name] is default_tool


def test_build_tool_specs_keeps_existing_behavior_without_request_tools(monkeypatch):
    base_tool = _InjectedRequestTool(name="base_probe")
    registry = _patch_minimal_tool_builder(monkeypatch, [base_tool])
    context = ToolContext()

    tools = agent_factory.build_tool_specs(
        SimpleNamespace(skills_mode="off"), tool_context=context,
    )

    assert tools == [base_tool]
    assert context.request_tools == {base_tool.name: base_tool}
    assert registry == {}


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
    first_retrieval_queries = {"request-a-1", "request-b-1"}
    first_retrieval_arrivals: set[str] = set()
    first_retrieval_ready = asyncio.Event()
    active_retrievals = 0
    max_active_retrievals = 0
    mock_results = [
        _fake_search_result("kb-a", 1, 0.91),
        _fake_search_result("kb-a", 2, 0.56),
        _fake_search_result("kb-a", 3, 0.22),
        _fake_search_result("kb-b", 1, 0.83),
        _fake_search_result("kb-b", 2, 0.66),
        _fake_search_result("kb-b", 3, 0.18),
        _fake_search_result("kb-c", 1, 0.99),
    ]

    async def fake_search_docs_core(query, top_k, kb_ids, threshold, doc_filter):
        nonlocal active_retrievals, max_active_retrievals
        active_retrievals += 1
        max_active_retrievals = max(max_active_retrievals, active_retrievals)
        try:
            if query in first_retrieval_queries:
                first_retrieval_arrivals.add(query)
                if first_retrieval_queries.issubset(first_retrieval_arrivals):
                    first_retrieval_ready.set()
                await asyncio.wait_for(first_retrieval_ready.wait(), timeout=2)
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
        finally:
            active_retrievals -= 1

    monkeypatch.setattr(search_module, "search_docs_core", fake_search_docs_core)

    web_calls: list[dict] = []
    first_web_queries = {"request-a-web-1", "request-b-web-1"}
    first_web_arrivals: set[str] = set()
    first_web_ready = asyncio.Event()
    active_web_requests = 0
    max_active_web_requests = 0

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json, headers):
            nonlocal active_web_requests, max_active_web_requests
            active_web_requests += 1
            max_active_web_requests = max(
                max_active_web_requests, active_web_requests,
            )
            try:
                query = json["query"]
                if query in first_web_queries:
                    first_web_arrivals.add(query)
                    if first_web_queries.issubset(first_web_arrivals):
                        first_web_ready.set()
                    await asyncio.wait_for(first_web_ready.wait(), timeout=2)
                web_calls.append(
                    {
                        "query": query,
                        "url": url,
                        "api_key": json["api_key"],
                    }
                )
                return _FakeResponse()
            finally:
                active_web_requests -= 1

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
    request_c = SimpleNamespace(
        top_k=1,
        kb_ids=None,
        relevance_threshold=0.5,
        tool_keys={},
        permission_mode="default",
        session_id="session-c",
    )

    tools_a = {tool.name: tool for tool in agent_factory.build_tool_specs(request_a)}
    tools_b = {tool.name: tool for tool in agent_factory.build_tool_specs(request_b)}
    tools_c = {tool.name: tool for tool in agent_factory.build_tool_specs(request_c)}
    context_a = tools_a["search_docs"]._ctx
    context_b = tools_b["search_docs"]._ctx
    assert context_a.rag_source_registry is not context_b.rag_source_registry

    async def run_request(tools, label):
        first_result = await tools["search_docs"].ainvoke(
            {"query": f"{label}-1"},
        )
        await tools["web_search"].ainvoke({"query": f"{label}-web-1"})
        second_result = await tools["search_docs"].ainvoke(
            {"query": f"{label}-2"},
        )
        await tools["web_search"].ainvoke({"query": f"{label}-web-2"})
        return first_result, second_result

    (result_a1, result_a2), (result_b1, result_b2) = await asyncio.gather(
        run_request(tools_a, "request-a"),
        run_request(tools_b, "request-b"),
    )
    result_c = await tools_c["search_docs"].ainvoke({"query": "request-c-no-kb"})

    expected_retrieval = {
        "request-a-1": {"top_k": 2, "kb_ids": ["kb-a"], "threshold": 0.5, "doc_filter": ""},
        "request-b-1": {"top_k": 1, "kb_ids": ["kb-b"], "threshold": 0.7, "doc_filter": ""},
        "request-a-2": {"top_k": 2, "kb_ids": ["kb-a"], "threshold": 0.5, "doc_filter": ""},
        "request-b-2": {"top_k": 1, "kb_ids": ["kb-b"], "threshold": 0.7, "doc_filter": ""},
        "request-c-no-kb": {"top_k": 1, "kb_ids": None, "threshold": 0.5, "doc_filter": ""},
    }
    observed_retrieval = {
        call["query"]: {
            key: value for key, value in call.items() if key != "query"
        }
        for call in retrieval_calls
    }
    expected_web_calls = {
        "request-a-web-1": ("https://request-a.invalid/api/search", "request-a-key"),
        "request-a-web-2": ("https://request-a.invalid/api/search", "request-a-key"),
        "request-b-web-1": ("https://request-b.invalid/api/search", "request-b-key"),
        "request-b-web-2": ("https://request-b.invalid/api/search", "request-b-key"),
    }
    observed_web_calls = {
        call["query"]: (call["url"], call["api_key"])
        for call in web_calls
    }
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
        "request-c-no-kb": {
            event["kb_id"]
            for event in _source_events(result_c, "call-c-no-kb")
        },
    }

    failures: list[str] = []
    if observed_retrieval != expected_retrieval:
        failures.append(
            "retrieval received default/shared settings instead of each request: "
            f"{observed_retrieval!r}"
        )
    if observed_web_calls != expected_web_calls:
        failures.append(
            "web_search received default/shared credentials instead of each request: "
            f"{web_calls!r}"
        )
    if max_active_retrievals < 2 or max_active_web_requests < 2:
        failures.append(
            "the request tools were not held concurrently at the controlled barriers: "
            f"retrieval={max_active_retrievals}, web={max_active_web_requests}"
        )
    default_search = registry.get("search_docs")
    if default_search is None or default_search is tools_a["search_docs"] or default_search is tools_b["search_docs"]:
        failures.append("the test did not retain a distinct process-default search tool")
    expected_source_kbs = {
        "request-a-1": {"kb-a"},
        "request-b-1": {"kb-b"},
        "request-a-2": set(),
        "request-b-2": set(),
        "request-c-no-kb": set(),
    }
    if observed_source_kbs != expected_source_kbs:
        failures.append(
            "source SSE did not match the request KB selection and duplicate policy: "
            f"{observed_source_kbs!r}"
        )

    # Repeated evidence in one request keeps its existing [srcN] citation but
    # does not emit a duplicate source event. A separate request owns a fresh
    # registry and may start again from src1.
    data_a2 = _tool_result_data(result_a2)
    data_b2 = _tool_result_data(result_b2)
    data_a1 = _tool_result_data(result_a1)
    data_b1 = _tool_result_data(result_b1)
    if data_a2["results"] or "[src1]" not in result_a2["output"] or "[src2]" not in result_a2["output"]:
        failures.append(f"request A did not reuse its prior source IDs: {result_a2!r}")
    if data_b2["results"] or "[src1]" not in result_b2["output"]:
        failures.append(f"request B did not reuse its prior source ID: {result_b2!r}")
    if [item["id"] for item in data_a1["results"]] != ["src1", "src2"]:
        failures.append(f"request A source IDs were not request-local: {data_a1['results']!r}")
    if [item["id"] for item in data_b1["results"]] != ["src1"]:
        failures.append(f"request B source IDs were not request-local: {data_b1['results']!r}")

    assert not failures, "\n".join(failures)
