"""Request-local RAG source state across Agent pause and resume."""
from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi import Request
from langchain_core.messages import ToolMessage

from app.api import chat_routes
from app.api.sse import sse_event
from src.agent import agent_factory, hitl_handler
from src.agent.core.toolkit import tool_router
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.exceptions import ToolContext
from src.agent.sse_helpers import convert_tool_message_to_sse
from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry
from src.config.settings import settings
from src.rag import search as search_module


class _NoopAuditRecorder:
    def record(self, _record: object) -> None:
        """Keep the test from opening or writing the local audit database."""


class _FakeMcpManager:
    def connected_tool_specs_snapshot(self) -> tuple[()]:
        return ()


class _ControlledAgent:
    def __init__(self, tools: list[object]) -> None:
        self.tools = {tool.name: tool for tool in tools}


def _fake_result(kb_id: str, index: int, score: float) -> SimpleNamespace:
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


def _decode_sse_payloads(chunks: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for chunk in chunks:
        normalized = chunk.replace("\r\n", "\n")
        for frame in normalized.split("\n\n"):
            data_lines = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
            if not data_lines:
                continue
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def _source_payloads(chunks: list[str]) -> list[dict]:
    return [event for event in _decode_sse_payloads(chunks) if event.get("type") == "source"]


def _request(session_id: str, kb_ids: list[str], top_k: int, threshold: float) -> chat_routes.ChatRequest:
    return chat_routes.ChatRequest(
        messages=[{"role": "user", "content": "Find the requested register details."}],
        session_id=session_id,
        kb_ids=kb_ids,
        top_k=top_k,
        relevance_threshold=threshold,
        permission_mode="default",
    )


def _request_scope() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/agent-sandbox/resume",
        "headers": [],
        "query_string": b"",
    })


def test_source_registry_state_is_hidden_from_context_and_snapshot_repr_and_equality() -> None:
    context = ToolContext(rag_source_registry=RagSourceRegistry())
    snapshot = chat_routes.AgentRequestSnapshot(
        tool_context=context,
        rag_source_registry_snapshot={
            "version": 1,
            "next_id": 2,
            "entries": [{"identity_hash": "a" * 64, "source_id": "src1"}],
        },
    )

    assert context == ToolContext()
    assert "rag_source_registry" not in repr(context)
    assert "identity_hash" not in repr(snapshot)
    assert snapshot == chat_routes.AgentRequestSnapshot()


@pytest.mark.asyncio
async def test_pause_resume_restores_registry_and_new_same_session_request_resets_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause keeps source IDs; a fresh request in the same session starts over."""
    registry: dict[str, object] = {}
    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", registry)
    monkeypatch.setattr(agent_factory, "_filter_disabled_tools", lambda tools: tools)
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())
    monkeypatch.setattr(ToolRouter, "get_default", classmethod(lambda _cls: router))
    monkeypatch.setattr(settings, "tavily_api_key", "isolated-test-key")
    monkeypatch.setattr(
        chat_routes,
        "_ensure_external_tool_snapshot",
        lambda snapshot, _session_id, _payload=None: replace(snapshot, mcp_snapshot_bound=True),
    )

    async def no_checkpoint_write(_session_id: str) -> None:
        return None

    monkeypatch.setattr(agent_factory, "reset_thread_checkpoint", no_checkpoint_write)

    async def no_compaction(*_args, **_kwargs):
        if False:
            yield ""

    monkeypatch.setattr(chat_routes, "_check_and_compact_history", no_compaction)

    async def no_manual_skill_load(snapshot, _session_id):
        return snapshot, [], True

    monkeypatch.setattr(chat_routes, "_preload_manual_skills", no_manual_skill_load)

    created_contexts = []

    async def create_controlled_agent(**kwargs):
        tools = kwargs["tools"]
        created_contexts.append(next(tool._ctx for tool in tools if tool.name == "search_docs"))
        return _ControlledAgent(tools)

    monkeypatch.setattr(chat_routes, "create_hardware_agent", create_controlled_agent)
    monkeypatch.setattr(chat_routes, "build_agent_config", lambda _session_id: {})
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: {
        "model": "controlled-test-model",
        "api_key": "",
        "base_url": "",
    })
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)

    results = [
        _fake_result("kb-a", 1, 0.95),
        _fake_result("kb-a", 2, 0.72),
        _fake_result("kb-b", 1, 0.91),
        _fake_result("kb-b", 2, 0.67),
    ]
    search_calls: list[dict] = []

    async def fake_search_docs_core(query, top_k, kb_ids, threshold, doc_filter):
        search_calls.append({
            "query": query,
            "top_k": top_k,
            "kb_ids": tuple(kb_ids) if kb_ids is not None else None,
            "threshold": threshold,
            "doc_filter": doc_filter,
        })
        selected = [
            result for result in results
            if (kb_ids is None or result.kb_id in kb_ids) and result.score >= threshold
        ]
        return selected[:top_k]

    monkeypatch.setattr(search_module, "search_docs_core", fake_search_docs_core)

    tool_outputs: list[dict] = []

    async def execute_search(agent: _ControlledAgent, query: str, call_id: str) -> str:
        result = await agent.tools["search_docs"].ainvoke({"query": query})
        tool_outputs.append(result)
        message = ToolMessage(
            content=json.dumps(result),
            name="search_docs",
            tool_call_id=call_id,
        )
        return convert_tool_message_to_sse(message, {})

    async def controlled_stream(agent, _events, _config, _counter, *_args, **_kwargs):
        yield await execute_search(agent, "before-pause", "initial-call")
        yield sse_event("tool_confirm_required", {"calls": [], "count": 0})

    async def controlled_resume(agent, _config, _decision, _counter, _permission_mode, **_kwargs):
        yield await execute_search(agent, "after-resume", "resumed-call")
        yield sse_event("done", {"success": True, "usage": None})

    monkeypatch.setattr(chat_routes, "stream_agent_to_sse", controlled_stream)
    monkeypatch.setattr(hitl_handler, "resume_agent_after_user", controlled_resume)

    session_id = "source-registry-route-session"
    creds = {"model": "controlled-test-model", "api_key": "", "base_url": ""}
    first_request = _request(session_id, ["kb-a"], 2, 0.5)
    paused_chunks = [chunk async for chunk in chat_routes._run_agent_stream(first_request, creds)]
    first_sources = _source_payloads(paused_chunks)
    assert [event["id"] for event in first_sources] == ["src1", "src2"]
    assert {event["kb_id"] for event in first_sources} == {"kb-a"}

    pending = chat_routes._get_pending_request_snapshot(session_id)
    assert pending is not None
    assert pending.rag_source_registry_snapshot is not None
    assert pending.rag_source_registry_snapshot["next_id"] == 3
    assert "kb-a-manual.pdf" not in repr(pending)

    tampered_resume = _request(session_id, ["kb-b"], 1, 0.99)
    response = await chat_routes.resume_agent(
        chat_routes.ResumeRequest(payload=tampered_resume, decision="allow"),
        _request_scope(),
        user={},
    )
    resumed_chunks = [chunk async for chunk in response.body_iterator]
    assert _source_payloads(resumed_chunks) == []
    assert "[src1]" in tool_outputs[1]["output"]
    assert "[src2]" in tool_outputs[1]["output"]
    assert chat_routes._get_pending_request_snapshot(session_id) is None

    # The next user request may reuse the same chat session, but must receive a
    # new ToolContext and a fresh source registry for its own KB/settings.
    second_request = _request(session_id, ["kb-b"], 1, 0.8)
    next_chunks = [chunk async for chunk in chat_routes._run_agent_stream(second_request, creds)]
    next_sources = _source_payloads(next_chunks)
    assert [(event["kb_id"], event["id"]) for event in next_sources] == [("kb-b", "src1")]
    assert created_contexts[0] is created_contexts[1]
    assert created_contexts[0].rag_source_registry is created_contexts[1].rag_source_registry
    assert created_contexts[1] is not created_contexts[2]
    assert created_contexts[1].rag_source_registry is not created_contexts[2].rag_source_registry
    assert search_calls == [
        {"query": "before-pause", "top_k": 2, "kb_ids": ("kb-a",), "threshold": 0.5, "doc_filter": ""},
        {"query": "after-resume", "top_k": 2, "kb_ids": ("kb-a",), "threshold": 0.5, "doc_filter": ""},
        {"query": "before-pause", "top_k": 1, "kb_ids": ("kb-b",), "threshold": 0.8, "doc_filter": ""},
    ]
    chat_routes._clear_pending_request_snapshot(session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("registry_snapshot", "drop_context"),
    [
        (None, False),
        ({"version": 999, "next_id": 1, "entries": []}, False),
        ({"version": 1, "next_id": 1, "entries": []}, True),
    ],
)
async def test_resume_rejects_missing_or_invalid_registry_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    registry_snapshot: dict | None,
    drop_context: bool,
) -> None:
    class _Revocable:
        revoked = 0

        def revoke(self) -> None:
            self.revoked += 1

    session_id = "invalid-source-registry-snapshot"
    chat_routes._clear_pending_request_snapshot(session_id)
    context = agent_factory._build_tool_ctx(_request(session_id, ["kb-a"], 2, 0.5))
    authorization = _Revocable()
    context.mcp_authorization = authorization
    snapshot = chat_routes.AgentRequestSnapshot(
        permission_mode="default",
        kb_ids=("kb-a",),
        top_k=2,
        relevance_threshold=0.5,
        tool_context=context,
    )
    chat_routes._store_pending_request_snapshot(session_id, snapshot)
    stored = chat_routes._get_pending_request_snapshot(session_id)
    assert stored is not None
    broken = replace(
        stored,
        tool_context=None if drop_context else stored.tool_context,
        rag_source_registry_snapshot=registry_snapshot,
    )
    with chat_routes._PENDING_AGENT_SNAPSHOT_LOCK:
        _, stored_at = chat_routes._PENDING_AGENT_REQUESTS[session_id]
        chat_routes._PENDING_AGENT_REQUESTS[session_id] = (broken, stored_at)

    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(
        chat_routes,
        "_build_agent_for_payload",
        lambda *_args, **_kwargs: pytest.fail("invalid request snapshot must not build an Agent"),
    )
    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.resume_agent(
            chat_routes.ResumeRequest(
                payload=_request(session_id, ["kb-b"], 1, 0.99),
                decision="allow",
            ),
            _request_scope(),
            user={},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "HITL_REQUEST_SNAPSHOT_UNAVAILABLE"
    assert chat_routes._get_pending_request_snapshot(session_id) is None
    assert authorization.revoked == (0 if drop_context else 1)
