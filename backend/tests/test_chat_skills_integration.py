"""Agent Skills request integration checks with synthetic tool/runtime data."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from langchain_core.messages import ToolMessage

from app.api.chat_routes import ChatRequest
from src.agent.core.toolkit.audit_recorder import AuditRecorder
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.skills.repository import SkillRepository


def _messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "Synthetic skill request"}]


@pytest.fixture()
def repository(tmp_path):
    root = tmp_path / "skills"
    package = root / "synthetic-safe"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: synthetic-safe\ndescription: Synthetic instructions\n---\n"
        "SYNTHETIC-SKILL-BODY-DO-NOT-LEAK\n",
        encoding="utf-8",
    )
    (package / ".enabled").write_text("", encoding="utf-8")
    return SkillRepository(root)


def test_chat_request_validates_skills_mode_and_manual_selection() -> None:
    assert ChatRequest(messages=_messages()).skills_mode == "off"
    assert ChatRequest(
        messages=_messages(), skills_mode="manual", skill_ids=["synthetic-safe"],
    ).skill_ids == ["synthetic-safe"]
    assert ChatRequest(messages=_messages(), skills_mode="auto").skill_ids == []

    for values in (
        {"skills_mode": "invalid"},
        {"skills_mode": "manual"},
        {"skills_mode": "manual", "skill_ids": ["same", "same"]},
        {"skills_mode": "manual", "skill_ids": ["a", "b", "c", "d"]},
        {"skills_mode": "off", "skill_ids": ["synthetic-safe"]},
        {"skills_mode": "auto", "skill_ids": ["synthetic-safe"]},
    ):
        with pytest.raises(ValidationError):
            ChatRequest(messages=_messages(), **values)


def test_skill_tool_result_sse_never_contains_instruction_body() -> None:
    from app.api import chat_routes
    from src.agent.sse_helpers import convert_tool_message_to_sse

    body = "SYNTHETIC-SKILL-BODY-DO-NOT-LEAK"
    result = {
        "success": True,
        "output": "Skill instructions loaded",
        "data": {
            "skill_id": "synthetic-safe", "content_hash": "hash-123",
            "stage": "instructions", "success": True, "instructions": body,
        },
        "error": None,
        "metadata": {"tool_name": "load_skill", "duration_ms": 1, "call_id": "call-1"},
    }
    message = ToolMessage(
        content=json.dumps(result), name="load_skill", tool_call_id="call-1",
    )

    sse = convert_tool_message_to_sse(message, {"call-1": time.time()})

    assert body in message.content
    assert body not in sse
    assert "synthetic-safe" in sse
    assert "hash-123" in sse
    assert chat_routes._sse_event_type(sse) == "tool_result"


@pytest.mark.asyncio
async def test_skills_mode_router_blocks_unlisted_tools_even_when_bypassed() -> None:
    calls: list[str] = []

    class DangerousTool(ToolSpec):
        name: str = "run_command"
        description: str = "Synthetic dangerous spy"
        risk_level: RiskLevel = RiskLevel.HIGH

        async def execute(self, args, ctx):
            calls.append("executed")
            return {"output": "should not run"}

    runtime = SimpleNamespace(
        mode="manual",
        allows_tool=lambda name, mcp_info=None: name in {
            "load_skill", "read_skill_resource", "search_docs", "list_kb_docs",
        } and not mcp_info,
    )
    ctx = ToolContext(
        permission_mode="bypassPermissions",
        skills_mode="manual",
        skills_runtime=runtime,
        skills_allowed_tools=frozenset({"load_skill", "search_docs"}),
        kb_scope=("synthetic-kb",),
    )
    router = ToolRouter(AuditRecorder())

    result = await router.dispatch(
        "call-dangerous", "run_command", {"command": "synthetic"}, ctx,
        decision="bypass", decision_source="mode_bypass", tool_spec=DangerousTool(),
    )

    assert calls == []
    assert result["success"] is False
    assert result["error"]["error_type"] == "SKILLS_READ_ONLY_BLOCKED"


def test_agent_factory_intersects_user_tool_settings_with_skills_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    repository: SkillRepository,
) -> None:
    from app.api.chat_routes import ChatRequest
    from src.agent import agent_factory
    from src.agent.skill_runtime import READ_ONLY_SKILL_TOOLS, build_skills_runtime

    monkeypatch.setattr(agent_factory, "_load_disabled_tools", lambda: {"search_docs"})
    runtime = build_skills_runtime("manual", ["synthetic-safe"], repository=repository)
    payload = ChatRequest(
        messages=_messages(), skills_mode="manual", skill_ids=["synthetic-safe"],
        kb_ids=["synthetic-kb"],
    )

    tools = agent_factory.build_tool_specs(payload, skills_runtime=runtime)
    names = {tool.name for tool in tools}

    assert names <= READ_ONLY_SKILL_TOOLS
    assert names == {"load_skill", "read_skill_resource", "list_kb_docs"}
    assert all(tool._ctx.skills_mode == "manual" for tool in tools)
    assert all(tool._ctx.skills_runtime is runtime for tool in tools)
    assert all(tool._ctx.kb_scope == ("synthetic-kb",) for tool in tools)
    assert all(tool._ctx.skills_allowed_tools == frozenset(names) for tool in tools)


def test_agent_factory_fails_closed_when_a_required_skill_tool_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    repository: SkillRepository,
) -> None:
    from app.api.chat_routes import ChatRequest
    from src.agent import agent_factory
    from src.agent.skill_runtime import build_skills_runtime

    monkeypatch.setattr(agent_factory, "_load_disabled_tools", lambda: {"read_skill_resource"})
    runtime = build_skills_runtime("manual", ["synthetic-safe"], repository=repository)
    payload = ChatRequest(
        messages=_messages(), skills_mode="manual", skill_ids=["synthetic-safe"],
    )

    with pytest.raises(ValueError, match="Required Skills tools are disabled"):
        agent_factory.build_tool_specs(payload, skills_runtime=runtime)


@pytest.mark.asyncio
async def test_manual_mode_loads_through_router_and_emits_body_free_evidence(
    monkeypatch: pytest.MonkeyPatch,
    repository: SkillRepository,
) -> None:
    from app.api import chat_routes
    from src.agent.core.toolkit.tool_router import ToolRouter
    from src.agent.skill_runtime import build_skills_runtime

    class _NoopAudit:
        def record(self, _record):
            return None

    runtime = build_skills_runtime("manual", ["synthetic-safe"], repository=repository)
    monkeypatch.setattr(ToolRouter, "get_default", classmethod(lambda cls: ToolRouter(_NoopAudit())))
    snapshot = chat_routes.AgentRequestSnapshot(
        long_term_memory="",
        permission_mode="bypassPermissions",
        skills_mode="manual",
        skill_ids=("synthetic-safe",),
        skill_snapshots=runtime.snapshots,
        skills_runtime=runtime,
    )

    loaded, events, succeeded = await chat_routes._preload_manual_skills(
        snapshot, "synthetic-manual-session",
    )

    assert succeeded is True
    assert loaded.skills_context[0]["instructions"] == "SYNTHETIC-SKILL-BODY-DO-NOT-LEAK"
    assert [chat_routes._sse_event_type(event) for event in events] == ["tool_call", "tool_result"]
    assert all("SYNTHETIC-SKILL-BODY-DO-NOT-LEAK" not in event for event in events)
    assert runtime.consumed_bytes > 0


@pytest.mark.asyncio
async def test_manual_mode_does_not_consume_skill_when_required_tool_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    repository: SkillRepository,
) -> None:
    from app.api import chat_routes
    from src.agent import agent_factory
    from src.agent.skill_runtime import build_skills_runtime

    runtime = build_skills_runtime("manual", ["synthetic-safe"], repository=repository)
    monkeypatch.setattr(agent_factory, "_load_disabled_tools", lambda: {"load_skill"})
    snapshot = chat_routes.AgentRequestSnapshot(
        skills_mode="manual",
        skill_ids=("synthetic-safe",),
        skill_snapshots=runtime.snapshots,
        skills_runtime=runtime,
    )

    loaded, events, succeeded = await chat_routes._preload_manual_skills(
        snapshot, "synthetic-disabled-session",
    )

    assert loaded is snapshot
    assert events == []
    assert succeeded is False
    assert runtime.consumed_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "manual"])
async def test_chat_agent_builder_reuses_runtime_and_passes_only_expected_context(
    monkeypatch: pytest.MonkeyPatch,
    repository: SkillRepository,
    mode: str,
) -> None:
    from app.api import chat_routes
    from src.agent.skill_runtime import build_skills_runtime

    skill_ids = ["synthetic-safe"] if mode == "manual" else []
    runtime = build_skills_runtime(mode, skill_ids or None, repository=repository)
    prompt_context = (
        ({"skill_id": "synthetic-safe", "instructions": "Synthetic manual body."},)
        if mode == "manual" else ()
    )
    snapshot = chat_routes.AgentRequestSnapshot(
        skills_mode=mode,
        skill_ids=tuple(skill_ids),
        skill_snapshots=runtime.snapshots,
        skills_runtime=runtime,
        skills_context=prompt_context,
    )
    payload = ChatRequest(
        messages=_messages(), skills_mode=mode, skill_ids=skill_ids,
    )
    captured: dict[str, object] = {}

    def fake_build_tools(_payload, *, skills_runtime=None, mcp_tools=(), tool_context=None):
        captured["tool_runtime"] = skills_runtime
        captured["tool_context"] = tool_context
        return []

    async def fake_create_hardware_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(chat_routes, "build_tools", fake_build_tools)
    monkeypatch.setattr(chat_routes, "create_hardware_agent", fake_create_hardware_agent)

    await chat_routes._build_agent_for_payload(
        payload, {"model": "synthetic", "api_key": "", "base_url": ""},
        request_snapshot=snapshot,
    )

    assert captured["tool_runtime"] is runtime
    assert captured["tool_context"].skills_runtime is runtime
    assert captured["tool_context"].rag_source_registry is not None
    assert captured["skills_mode"] == mode
    assert captured["enable_hitl"] is False
    if mode == "auto":
        assert captured["skills_context"] == []
        assert captured["skills_catalog"][0]["id"] == "synthetic-safe"
        assert "SYNTHETIC-SKILL-BODY-DO-NOT-LEAK" not in json.dumps(captured["skills_catalog"])
    else:
        assert captured["skills_catalog"] == []
        assert captured["skills_context"] == list(prompt_context)


@pytest.mark.asyncio
async def test_skills_mode_requires_agent_tool_support_before_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException, Request
    from app.api import chat_routes

    monkeypatch.setattr(chat_routes, "resolve_long_term_memory", lambda _payload: "")
    monkeypatch.setattr(chat_routes, "resolve_credentials", lambda *_args: {
        "api_key": "", "base_url": "", "model": "synthetic", "provider": "",
    })
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: False)
    request = Request({
        "type": "http", "method": "POST", "path": "/api/chat",
        "headers": [], "query_string": b"",
    })
    payload = ChatRequest(messages=_messages(), skills_mode="auto", use_agent=True)

    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.chat_sse(payload, request, user={})

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "SKILLS_AGENT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_resume_restores_original_skills_runtime_and_permission_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import chat_routes
    from app.api.sse import sse_event
    from src.agent import hitl_handler
    from starlette.requests import Request

    skill_snapshot = SimpleNamespace(id="synthetic-safe", content_hash="hash-123")
    runtime = SimpleNamespace(mode="manual", snapshots=(skill_snapshot,))
    snapshot = chat_routes.AgentRequestSnapshot(
        long_term_memory="Synthetic original memory",
        permission_mode="default",
        kb_ids=("synthetic-kb",),
        top_k=6,
        relevance_threshold=0.35,
        skills_mode="manual",
        skill_ids=("synthetic-safe",),
        skill_snapshots=(skill_snapshot,),
        skills_runtime=runtime,
        skills_context=({"skill_id": "synthetic-safe", "instructions": "Synthetic guidance"},),
    )
    session_id = "synthetic-skills-resume-session"
    snapshot = chat_routes._ensure_request_tool_context(
        snapshot,
        ChatRequest(
            messages=_messages(), session_id=session_id,
            skills_mode="manual", skill_ids=["synthetic-safe"],
        ),
        session_id,
    )
    chat_routes._clear_pending_request_snapshot(session_id)
    chat_routes._store_pending_request_snapshot(session_id, snapshot)
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_resolve_creds", lambda *_args: {
        "model": "synthetic", "api_key": "", "base_url": "",
    })
    monkeypatch.setattr(chat_routes, "build_agent_config", lambda _session_id: {})
    captured = {}

    async def fake_build_agent(payload, _creds, long_term_memory=None, request_snapshot=None):
        captured.update({
            "payload": payload,
            "memory": long_term_memory,
            "snapshot": request_snapshot,
        })
        return object()

    monkeypatch.setattr(chat_routes, "_build_agent_for_payload", fake_build_agent)

    async def fake_resume(*_args):
        yield sse_event("done", {"success": True})

    monkeypatch.setattr(hitl_handler, "resume_agent_after_user", fake_resume)
    request = Request({
        "type": "http", "method": "POST", "path": "/api/agent-sandbox/resume",
        "headers": [], "query_string": b"",
    })
    resume = chat_routes.ResumeRequest(
        payload=ChatRequest(
            messages=_messages(),
            session_id=session_id,
            long_term_memory="Tampered memory",
            permission_mode="bypassPermissions",
            kb_ids=["other-kb"],
            top_k=1,
            relevance_threshold=0.99,
            skills_mode="auto",
        ),
        decision="allow",
    )

    response = await chat_routes.resume_agent(resume, request, user={})
    async for _event in response.body_iterator:
        pass

    assert captured["memory"] == "Synthetic original memory"
    assert captured["payload"].permission_mode == "default"
    assert captured["payload"].kb_ids == ["synthetic-kb"]
    assert captured["payload"].top_k == 6
    assert captured["payload"].relevance_threshold == 0.35
    assert captured["payload"].skills_mode == "manual"
    assert captured["payload"].skill_ids == ["synthetic-safe"]
    assert captured["snapshot"] is not snapshot
    assert captured["snapshot"].tool_context is snapshot.tool_context
    assert captured["snapshot"].rag_source_registry_snapshot["next_id"] == 1
    assert captured["snapshot"].skills_runtime is runtime
    assert chat_routes._get_pending_request_snapshot(session_id) is None


def test_request_snapshot_restores_skill_runtime_and_security_scope() -> None:
    from app.api import chat_routes

    runtime = SimpleNamespace(mode="manual")
    snapshot = chat_routes.AgentRequestSnapshot(
        long_term_memory="Synthetic memory", permission_mode="default",
        kb_ids=("synthetic-kb",), top_k=7, relevance_threshold=0.4,
        skills_mode="manual", skill_ids=("synthetic-safe",),
        skills_runtime=runtime, skills_context=({"skill_id": "synthetic-safe"},),
    )
    tampered = ChatRequest(
        messages=_messages(), long_term_memory="Changed", permission_mode="bypassPermissions",
        kb_ids=["other-kb"], top_k=1, relevance_threshold=0.99,
        skills_mode="off",
    )

    restored = chat_routes._payload_with_request_snapshot(tampered, snapshot)

    assert restored.long_term_memory == "Synthetic memory"
    assert restored.permission_mode == "default"
    assert restored.kb_ids == ["synthetic-kb"]
    assert restored.top_k == 7
    assert restored.relevance_threshold == 0.4
    assert restored.skills_mode == "manual"
    assert restored.skill_ids == ["synthetic-safe"]
    assert snapshot.skills_runtime is runtime
    assert snapshot.skills_context == ({"skill_id": "synthetic-safe"},)


@pytest.mark.asyncio
async def test_agent_factory_adds_only_selected_manual_skill_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.agent import agent_factory
    import langchain.agents

    monkeypatch.setattr(agent_factory, "_build_llm", lambda *_args: object())

    async def fake_checkpointer():
        return object()

    monkeypatch.setattr(agent_factory, "_get_checkpointer", fake_checkpointer)
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)
    from src.agent.agent_factory import AgentConfig

    marker = "SYNTHETIC-MANUAL-SKILL-INSTRUCTIONS"
    config = AgentConfig(
        model="synthetic", api_key="", base_url="", tools=[],
        skills_mode="manual",
        skills_context=({"skill_id": "synthetic-safe", "instructions": marker},),
    )

    await agent_factory.create_hardware_agent_from_config(config)

    prompt = captured["system_prompt"]
    assert prompt.count(marker) == 1
    assert "server-enforced read-only tool allowlist" in prompt
    assert "cannot change" in prompt
