"""
Chat 路由 — /api/chat SSE + /api/models

迁移自 routes.py，共享工具见 common.py。
"""

import asyncio
import hashlib
import copy
import json
import logging
import threading
import time
import uuid
from collections import Counter
from contextlib import ExitStack, contextmanager, nullcontext, nullcontext
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictStr, field_validator, model_validator

from src.config.settings import settings
from src.llm.client import LLMClient, LLMError
try:
    from openai import NotFoundError as OpenAINotFoundError
except ImportError:  # pragma: no cover - openai optional dependency
    OpenAINotFoundError = None  # type: ignore[assignment]
from app.api.auth import get_provider_key, resolve_credentials
from app.api.dependencies import current_user, current_user_optional
from app.api.runs import recover_interrupted_runs, router as runs_router
from app.api.common import get_db_ctx, make_client
from app.api.memory_epoch import read_memory_clear_epoch
from app.api.sse import sse_event
from app.api.errors import sanitize_error
from app.api.session_epoch import read_session_delete_epoch
from app.api.compaction_archive import bind_compaction_archive, compaction_principal, get_compaction_scope, make_compaction_scope
from app.api.tool_events import bind_tool_event_archive
from app.api.chat_helpers import (
    MAX_LONG_TERM_MEMORY_CHARS, resolve_long_term_memory,
    _record_token_usage,
    _process_attachments, _build_chat_history, _build_system_prompt,
)
from app.db.models import TokenUsage
from src.agent import run_events
from src.agent.request_budget import AgentRequestBudgetError

logger = logging.getLogger(__name__)


async def stream_agent_to_sse(
    agent: Any,
    events: Any,
    config: dict,
    call_counter: Counter,
    permission_mode: str = "default",
    model: str = "",
    requested_max_tokens: int | None = None,
    context_budget: Any = None,
    *,
    compaction_source_message_ids: tuple[str | None, ...] | None = None,
    compaction_source_messages: tuple[dict, ...] | None = None,
) -> AsyncIterator[str]:
    """Lazily forward to the Agent SSE adapter while keeping a patchable API symbol."""
    from src.agent.sse_adapter import stream_agent_to_sse as adapter_stream

    adapter_iterator = adapter_stream(
        agent, events, config, call_counter, permission_mode, model=model,
        requested_max_tokens=requested_max_tokens, context_budget=context_budget,
        compaction_source_message_ids=compaction_source_message_ids,
        compaction_source_messages=compaction_source_messages,
    )
    stream_error: BaseException | None = None
    try:
        async for frame in adapter_iterator:
            yield frame
    except BaseException as exc:
        stream_error = exc
        raise
    finally:
        close = getattr(adapter_iterator, "aclose", None)
        if close is not None:
            try:
                await close()
            except BaseException:
                if stream_error is None:
                    raise
                logger.debug("Agent SSE adapter cleanup failed after stream error", exc_info=True)


# Agent path imports — guarded so a missing langgraph/langchain dependency degrades
# gracefully to the fallback LLM stream instead of breaking the whole chat route
# (spec §10.2: "LangGraph import 失败 → use_agent = False, 自动走 fallback").
try:
    from src.agent.agent_factory import (
        _should_use_agent, build_agent_config, build_tools, create_hardware_agent,
        replace_request_tools,
    )
    _AGENT_PATH_AVAILABLE = True
except ImportError as _agent_import_err:
    _should_use_agent = None  # type: ignore[assignment]
    _AGENT_PATH_AVAILABLE = False
    logger.debug("Agent path unavailable, will use fallback LLM stream: %s", _agent_import_err)

router = APIRouter(prefix="/api")
router.include_router(runs_router)
router.on_startup.append(recover_interrupted_runs)

# HITL resumes must use the same manual-memory value as the paused request,
# even when the original payload omitted it and the saved setting changes.
_PENDING_AGENT_SNAPSHOT_TTL_SECONDS = 30 * 60
_MAX_PENDING_AGENT_SNAPSHOTS = 128
_PENDING_AGENT_REQUESTS: dict[str, tuple["AgentRequestSnapshot", float]] = {}
_PENDING_AGENT_SNAPSHOT_LOCK = threading.Lock()


@dataclass(frozen=True)
class AgentRequestSnapshot:
    """Request-scoped state required to resume a paused Agent consistently."""

    long_term_memory: str = ""
    session_delete_epoch: str | None = field(default=None, repr=False)
    memory_clear_epoch: str | None = field(default=None, repr=False)
    permission_mode: str = "default"
    kb_ids: tuple[str, ...] | None = None
    top_k: int | None = 5
    relevance_threshold: float | None = 0.0
    skills_mode: str = "off"
    skill_ids: tuple[str, ...] = ()
    skill_snapshots: tuple[Any, ...] = ()
    skills_runtime: Any = None
    skills_context: tuple[dict, ...] = ()
    mcp_tools: tuple[Any, ...] = ()
    tool_context: Any = field(default=None, repr=False, compare=False)
    mcp_snapshot_bound: bool = False
    rag_source_registry_snapshot: dict[str, Any] | None = field(
        default=None, repr=False, compare=False,
    )
    principal_id: str | None = field(default=None, repr=False, compare=False)
    session_id: str | None = field(default=None, repr=False, compare=False)
    request_id: str = field(
        default_factory=lambda: uuid.uuid4().hex,
        repr=False,
        compare=False,
    )
    context_event_scope: Any = field(default=None, repr=False, compare=False)
    tool_event_scope: Any = field(default=None, repr=False, compare=False)
    business_session_id: str = ""
    principal_hash: str | None = field(default=None, repr=False)
    graph_config: dict | None = field(default=None, repr=False, compare=False)
    pending_call_ids: tuple[str, ...] = ()
    pending_call_fingerprints: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    skill_install_tool: Any = field(default=None, repr=False, compare=False)
    approved_plan_id: str | None = None
    approved_plan_text: str = ""


class _RequestSnapshotEpochError(RuntimeError):
    """Raised when a request snapshot can no longer be validated."""


class _WriterLifecycleUnavailableError(RuntimeError):
    """Raised when a request cannot establish guarded checkpoint ownership."""


def _capture_request_snapshot_epochs(session_id: str | None) -> tuple[str, str]:
    """Pin the generations that bound this request's session and memory snapshot."""
    from app.api.session_epoch import is_session_tombstoned

    if is_session_tombstoned(session_id):
        raise _RequestSnapshotEpochError("session was deleted")
    session_epoch = read_session_delete_epoch(session_id)
    memory_epoch = read_memory_clear_epoch()
    if session_epoch is None or memory_epoch is None:
        raise _RequestSnapshotEpochError("request snapshot epochs are unavailable")
    return session_epoch, memory_epoch


def _require_request_snapshot_epochs_current(
    snapshot: AgentRequestSnapshot,
    session_id: str | None,
) -> None:
    """Fail closed if a request snapshot crossed session deletion or memory clear."""
    if snapshot.session_delete_epoch is None or snapshot.memory_clear_epoch is None:
        raise _RequestSnapshotEpochError("request snapshot epochs are missing")
    session_epoch = read_session_delete_epoch(session_id)
    memory_epoch = read_memory_clear_epoch()
    if (
        session_epoch is None
        or memory_epoch is None
        or session_epoch != snapshot.session_delete_epoch
        or memory_epoch != snapshot.memory_clear_epoch
    ):
        raise _RequestSnapshotEpochError("request snapshot epochs changed")


def _ensure_external_tool_snapshot(
    snapshot: AgentRequestSnapshot,
    session_id: str,
    payload: Any = None,
) -> AgentRequestSnapshot:
    """Freeze connected tool instances and approvals for this request only."""
    if snapshot.mcp_snapshot_bound:
        return snapshot
    from src.mcp.manager import get_mcp_manager
    tools = get_mcp_manager().connected_tool_specs_snapshot()
    if not tools:
        return replace(snapshot, mcp_snapshot_bound=True)
    from src.agent.mcp_authorization import MCPRequestAuthorizations
    snapshot = _ensure_request_tool_context(snapshot, payload, session_id)
    ctx = snapshot.tool_context
    ctx.mcp_authorization = MCPRequestAuthorizations(tools)
    return replace(snapshot, mcp_tools=tools, tool_context=ctx, mcp_snapshot_bound=True)


def _session_memory_key(session_id: str | None) -> str:
    return session_id or "default"


def _verified_principal_id(user: Any) -> str | None:
    """Derive a non-reversible scope key from verified non-anonymous credentials."""
    if type(user) is not dict or user.get("anonymous") is not False:
        return None
    provider = user.get("provider")
    api_key = user.get("api_key")
    if (
        type(provider) is not str
        or not provider
        or type(api_key) is not str
        or not api_key
    ):
        return None
    identity = f"{provider}\0{api_key}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _tool_observation_scope(snapshot: AgentRequestSnapshot) -> Any:
    """Create store scope only from host-pinned request snapshot fields."""
    from app.api.tool_observations import make_tool_observation_scope

    return make_tool_observation_scope(
        snapshot.principal_id,
        snapshot.session_id,
        snapshot.request_id,
        snapshot.session_delete_epoch,
    )


def _build_request_writer_binding(
    agent: Any,
    config: dict,
    snapshot: AgentRequestSnapshot,
    session_id: str | None,
):
    """Bind one factory graph to this request's pinned deletion epochs."""
    from src.agent.agent_factory import build_agent_writer_binding

    owner_key = config.get("configurable", {}).get("thread_id")
    if type(owner_key) is not str or not owner_key:
        raise RuntimeError("Agent checkpoint thread id is unavailable")
    request_scope_token = object()
    epoch_token = object()

    def epoch_provider() -> object:
        _require_request_snapshot_epochs_current(snapshot, session_id)
        return epoch_token

    return build_agent_writer_binding(
        agent,
        owner_key=owner_key,
        request_scope_token=request_scope_token,
        epoch_provider=epoch_provider,
    )


def _acquire_request_writer_lease(binding: Any):
    """Acquire only after the authoritative request scope has been re-read."""
    scope = binding.current_scope()
    return binding.registry.acquire(scope)


def _release_request_writer_lease(
    binding: Any,
    lease: Any,
    release_state: dict[str, bool] | None = None,
) -> None:
    """Release a fully joined owner, or quarantine any unresolved outcome."""
    if release_state is not None and release_state.get("released"):
        return
    from src.agent.compact.writer_lifecycle import QuarantineReason

    try:
        binding.registry.release(lease, binding.current_scope())
        if release_state is not None:
            release_state["released"] = True
        return
    except BaseException as exc:
        logger.warning(
            "Agent writer lease could not be safely released; owner remains closed (%s)",
            type(exc).__name__,
        )
    try:
        binding.registry.quarantine(
            lease,
            binding.scope,
            reason=QuarantineReason.CALLER_REPORTED_UNCERTAINTY,
        )
    except BaseException:
        # A stale or already-quarantined lease is already fail-closed.
        pass
    if release_state is not None:
        release_state["released"] = True


@contextmanager
def _request_writer_context(
    binding: Any, lease: Any, model: str = "", config: dict | None = None,
    request_snapshot: Any = None,
):
    """Expose one baseline lease and host request identity to the adapter."""
    if binding is None or lease is None:
        yield
        return
    from src.agent.compact.writer_lifecycle import bind_writer_scope
    from src.agent.writer_bridge import bind_request_identity
    from src.agent.request_context import request_graph_binding_from_config

    scope = binding.current_scope()
    binding.registry.assert_scope(lease, scope)
    if request_snapshot is not None:
        identity_scope = bind_request_identity(
            request_snapshot.business_session_id,
            request_snapshot.request_id,
            getattr(request_snapshot, "session_delete_epoch", "") or "",
        )
    else:
        graph_binding = (
            request_graph_binding_from_config(config)
            if type(config) is dict else None
        )
        identity_scope = (
            bind_request_identity(
                graph_binding.business_session_id,
                graph_binding.request_id,
            )
            if graph_binding is not None else nullcontext()
        )
    with identity_scope:
        with bind_writer_scope(
            binding.registry, lease, scope, scope_provider=binding.current_scope,
        ):
            yield


def _request_snapshot_from_payload(
    payload: Any,
    memory: str,
    *,
    session_delete_epoch: str | None = None,
    memory_clear_epoch: str | None = None,
) -> AgentRequestSnapshot:
    kb_ids = getattr(payload, "kb_ids", None)
    if session_delete_epoch is None:
        session_delete_epoch = read_session_delete_epoch(
            getattr(payload, "session_id", None),
        )
    if memory_clear_epoch is None:
        memory_clear_epoch = read_memory_clear_epoch()
    return AgentRequestSnapshot(
        long_term_memory=memory,
        session_delete_epoch=session_delete_epoch,
        memory_clear_epoch=memory_clear_epoch,
        permission_mode=getattr(payload, "permission_mode", "default") or "default",
        kb_ids=tuple(kb_ids) if kb_ids is not None else None,
        top_k=getattr(payload, "top_k", None),
        relevance_threshold=getattr(payload, "relevance_threshold", None),
        skills_mode=getattr(payload, "skills_mode", "off") or "off",
        skill_ids=tuple(getattr(payload, "skill_ids", None) or ()),
        approved_plan_id=getattr(payload, "approved_plan_id", None),
    )


def _payload_with_request_snapshot(payload: Any, snapshot: AgentRequestSnapshot) -> Any:
    updates = {
        "long_term_memory": snapshot.long_term_memory,
        "permission_mode": snapshot.permission_mode,
        "kb_ids": list(snapshot.kb_ids) if snapshot.kb_ids is not None else None,
        "top_k": snapshot.top_k,
        "relevance_threshold": snapshot.relevance_threshold,
        "skills_mode": snapshot.skills_mode,
        "skill_ids": list(snapshot.skill_ids),
        "approved_plan_id": snapshot.approved_plan_id,
    }
    if hasattr(payload, "model_copy"):
        return payload.model_copy(update=updates)
    copied = payload.__class__(**{**vars(payload), **updates})
    return copied


def _ensure_request_tool_context(
    snapshot: AgentRequestSnapshot,
    payload: Any,
    session_id: str,
) -> AgentRequestSnapshot:
    """Bind one fresh Agent ToolContext to a new request snapshot."""
    if snapshot.tool_context is not None:
        snapshot.tool_context.tool_observation_scope = _tool_observation_scope(snapshot)
        return snapshot

    from src.agent.agent_factory import _build_tool_ctx

    if payload is None:
        from types import SimpleNamespace

        payload = SimpleNamespace(
            permission_mode=snapshot.permission_mode,
            session_id=session_id,
            kb_ids=list(snapshot.kb_ids) if snapshot.kb_ids is not None else None,
            top_k=snapshot.top_k,
            relevance_threshold=snapshot.relevance_threshold,
            skills_mode=snapshot.skills_mode,
        )
    request_payload = _payload_with_request_snapshot(payload, snapshot)
    context = _build_tool_ctx(request_payload, snapshot.skills_runtime)
    context.tool_observation_scope = _tool_observation_scope(snapshot)
    return replace(snapshot, tool_context=context)


def _seed_skill_install_tool_context(tool_context: Any, tool: Any) -> None:
    if tool is None:
        return
    from src.agent.tools.groups.skills.install_tool import SkillInstallTool

    if type(tool) is not SkillInstallTool:
        raise ValueError("Invalid request-scoped Skill install tool")
    current = tool_context.request_tools.get(tool.name)
    if current is not None and current is not tool:
        raise ValueError("Skill install tool name conflicts with another request tool")
    tool_context.request_tools[tool.name] = tool


def _attach_skill_install_tool(tool_context: Any, tools: list[Any], tool: Any) -> None:
    if tool is None:
        return
    from src.agent.agent_factory import _load_disabled_tools

    _seed_skill_install_tool_context(tool_context, tool)
    if tool.name in _load_disabled_tools():
        raise ValueError("Skill install tool was disabled before the request Agent was built")
    if not any(item is tool for item in tools):
        if any(getattr(item, "name", None) == tool.name for item in tools):
            raise ValueError("Skill install tool name conflicts with a built-in tool")
        tools.append(tool)
    injected = getattr(tool_context.request_tools, "injected_tools", None)
    if injected is not None and not any(item is tool for item in injected):
        tool_context.request_tools.injected_tools = tuple(injected) + (tool,)


def _ensure_skills_runtime(snapshot: AgentRequestSnapshot) -> AgentRequestSnapshot:
    if snapshot.skills_runtime is not None:
        if getattr(snapshot.skills_runtime, "mode", None) != snapshot.skills_mode:
            raise ValueError("Skills runtime does not match the saved request mode")
        current = tuple(snapshot.skills_runtime.snapshots)
        saved = tuple((item.id, item.content_hash) for item in snapshot.skill_snapshots)
        current_prefix = tuple((item.id, item.content_hash) for item in current[:len(saved)])
        if len(saved) > len(current) or saved != current_prefix:
            raise ValueError("Skills runtime does not match the saved request snapshot")
        return replace(snapshot, skill_snapshots=current)

    from src.agent.skill_runtime import build_skills_runtime
    runtime = build_skills_runtime(
        snapshot.skills_mode,
        snapshot.skill_ids if snapshot.skills_mode == "manual" else None,
    )
    current = tuple(runtime.snapshots)
    saved = tuple((item.id, item.content_hash) for item in snapshot.skill_snapshots)
    current_prefix = tuple((item.id, item.content_hash) for item in current[:len(saved)])
    if len(saved) > len(current) or saved != current_prefix:
        raise ValueError("Skills runtime does not match the saved request snapshot")
    return replace(
        snapshot,
        skill_snapshots=current,
        skills_runtime=runtime,
    )


async def _preload_manual_skills(
    snapshot: AgentRequestSnapshot, session_id: str,
) -> tuple[AgentRequestSnapshot, list[str], bool]:
    """Load explicitly selected instructions through ToolRouter and emit safe events."""
    if snapshot.skills_mode != "manual":
        return snapshot, [], True

    import uuid
    from langchain_core.messages import ToolMessage
    from src.agent.core.toolkit.tool_router import ToolRouter
    from src.agent.exceptions import ToolContext
    from src.agent.skill_runtime import LoadSkillTool, SKILL_RUNTIME_TOOLS
    from src.agent.sse_helpers import convert_tool_message_to_sse
    from src.agent.agent_factory import _load_disabled_tools

    runtime = snapshot.skills_runtime
    if runtime is None:
        raise RuntimeError("Selected Skills runtime is unavailable")
    disabled_tools = _load_disabled_tools()
    required_skill_tools = {"load_skill", "read_skill_resource"}
    if required_skill_tools.intersection(disabled_tools):
        return snapshot, [], False
    kb_scope = tuple(snapshot.kb_ids) if snapshot.kb_ids else None
    ctx = ToolContext(
        permission_mode=snapshot.permission_mode,
        session_id=session_id,
        skills_mode=snapshot.skills_mode,
        skills_runtime=runtime,
        skills_allowed_tools=frozenset(SKILL_RUNTIME_TOOLS - disabled_tools),
        kb_scope=kb_scope,
    )
    router = ToolRouter.get_default()
    loaded: list[dict] = []
    events: list[str] = []
    all_loaded = True
    for index, skill_snapshot in enumerate(snapshot.skill_snapshots, start=1):
        tool = LoadSkillTool(runtime)
        tool._ctx = ctx
        call_id = uuid.uuid4().hex
        started_at = time.time()
        args = {"skill_id": skill_snapshot.id}
        events.append(sse_event("tool_call", {
            "tool": tool.name,
            "args": args,
            "call_id": call_id,
            "step_index": index,
            "timestamp": started_at,
            "risk_level": tool.risk_level.value,
            "decision_source": "manual_skill_selection",
        }))
        result = await router.dispatch(
            call_id,
            tool.name,
            args,
            ctx,
            decision="allow",
            decision_source="manual_skill_selection",
            tool_spec=tool,
        )
        message = ToolMessage(
            content=json.dumps(result, ensure_ascii=False),
            name=tool.name,
            tool_call_id=call_id,
        )
        events.append(convert_tool_message_to_sse(message, {call_id: started_at}))
        result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        instructions = result_data.get("instructions")
        if not result.get("success") or not isinstance(instructions, str):
            all_loaded = False
            break
        loaded.append({
            "skill_id": result_data.get("skill_id", skill_snapshot.id),
            "content_hash": result_data.get("content_hash", skill_snapshot.content_hash),
            "instructions": instructions,
        })
    if not all_loaded:
        return snapshot, events, False
    return replace(snapshot, skills_context=tuple(loaded)), events, True


def _prune_pending_agent_snapshots(now: float) -> None:
    expired = [
        key for key, (_, stored_at) in _PENDING_AGENT_REQUESTS.items()
        if now - stored_at >= _PENDING_AGENT_SNAPSHOT_TTL_SECONDS
    ]
    for key in expired:
        _PENDING_AGENT_REQUESTS.pop(key, None)
    while len(_PENDING_AGENT_REQUESTS) > _MAX_PENDING_AGENT_SNAPSHOTS:
        oldest = min(_PENDING_AGENT_REQUESTS, key=lambda key: _PENDING_AGENT_REQUESTS[key][1])
        _PENDING_AGENT_REQUESTS.pop(oldest, None)


def _pending_call_fingerprints(pending: list[dict]) -> tuple[tuple[str, str], ...]:
    if type(pending) is not list or not 1 <= len(pending) <= 64:
        raise ValueError("Invalid pending call count")
    result = []
    remaining = 64 * 1024
    for call in pending:
        call_id, name, args = call.get("call_id"), call.get("name"), call.get("args")
        if (type(call_id) is not str or not 1 <= len(call_id) <= 256
                or type(name) is not str or not name or type(args) is not dict):
            raise ValueError("Invalid pending call")
        encoded = json.dumps([name, args], ensure_ascii=False, sort_keys=True,
                             allow_nan=False, separators=(",", ":")).encode("utf-8")
        remaining -= len(encoded)
        if remaining < 0:
            raise ValueError("Pending call budget exceeded")
        result.append((call_id, hashlib.sha256(encoded).hexdigest()))
    if len({item[0] for item in result}) != len(result):
        raise ValueError("Duplicate pending call ID")
    return tuple(result)


def _store_pending_request_snapshot(
    session_id: str | None, snapshot: AgentRequestSnapshot,
    pending: list[dict] | None = None, *, claim: Any = None,
) -> AgentRequestSnapshot | None:
    if pending is not None:
        try:
            fingerprints = _pending_call_fingerprints(pending)
        except ValueError:
            logger.warning("Cannot retain invalid pending call snapshot")
            return None
        snapshot = replace(
            snapshot,
            pending_call_ids=tuple(item[0] for item in fingerprints),
            pending_call_fingerprints=fingerprints,
        )
    context = snapshot.tool_context
    if context is None:
        logger.warning("cannot store resumable Agent request without ToolContext")
        _clear_pending_request_snapshot(session_id)
        return None
    try:
        from src.agent.tools.groups.retrieval.source_registry import get_context_source_registry

        registry_snapshot = get_context_source_registry(context).to_snapshot()
    except Exception:
        logger.warning("cannot store resumable Agent request with invalid source registry", exc_info=True)
        _clear_pending_request_snapshot(session_id)
        return None
    snapshot = replace(snapshot, rag_source_registry_snapshot=registry_snapshot)
    now = time.monotonic()
    with _PENDING_AGENT_SNAPSHOT_LOCK:
        _prune_pending_agent_snapshots(now)
        _PENDING_AGENT_REQUESTS[_session_memory_key(session_id)] = (snapshot, now)
        _prune_pending_agent_snapshots(now)
    return snapshot


def _get_pending_request_snapshot(
    session_id: str | None,
) -> AgentRequestSnapshot | None:
    now = time.monotonic()
    with _PENDING_AGENT_SNAPSHOT_LOCK:
        _prune_pending_agent_snapshots(now)
        key = _session_memory_key(session_id)
        pending = _PENDING_AGENT_REQUESTS.get(key)
        if pending is None:
            return None
        snapshot = pending[0]
        try:
            _require_request_snapshot_epochs_current(
                snapshot,
                snapshot.business_session_id or snapshot.session_id or session_id,
            )
            if snapshot.tool_context is None or snapshot.rag_source_registry_snapshot is None:
                raise ValueError("request source registry snapshot is missing")
            from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry

            registry = RagSourceRegistry.from_snapshot(snapshot.rag_source_registry_snapshot)
            snapshot.tool_context.rag_source_registry = registry
            snapshot.tool_context.source_counter = registry.next_id - 1
            return replace(snapshot, rag_source_registry_snapshot=registry.to_snapshot())
        except Exception:
            # A resumed tool call must never guess request-local evidence state.
            removed = _PENDING_AGENT_REQUESTS.pop(key, None)
            if removed is not None:
                authorization = getattr(removed[0].tool_context, "mcp_authorization", None)
                if authorization is not None:
                    authorization.revoke()
            logger.warning("discarded Agent resume snapshot with invalid source registry", exc_info=True)
            return None


def _store_pending_memory_snapshot(session_id: str | None, memory: str) -> None:
    """Compatibility helper for request paths that currently carry memory only."""
    _store_pending_request_snapshot(
        session_id, AgentRequestSnapshot(long_term_memory=memory),
    )


def _get_pending_memory_snapshot(session_id: str | None) -> str | None:
    # Memory-only compatibility reads are not resumable Agent requests and do
    # not require or synthesize a source registry snapshot.
    now = time.monotonic()
    with _PENDING_AGENT_SNAPSHOT_LOCK:
        _prune_pending_agent_snapshots(now)
        pending = _PENDING_AGENT_REQUESTS.get(_session_memory_key(session_id))
        return pending[0].long_term_memory if pending is not None else None


@dataclass(frozen=True)
class PendingAgentSnapshotClaim:
    """Compatibility claim used by context acceptance callers."""

    key: str
    token: object = field(repr=False)
    snapshot: AgentRequestSnapshot = field(repr=False)


def _claim_pending_request_snapshot(
    principal_hash: str | None, session_id: str, call_id: str | None,
) -> PendingAgentSnapshotClaim | None:
    if not principal_hash:
        return None
    with _PENDING_AGENT_SNAPSHOT_LOCK:
        _prune_pending_agent_snapshots(time.monotonic())
        matches = [
            (key, entry[0]) for key, entry in _PENDING_AGENT_REQUESTS.items()
            if key == _session_memory_key(session_id)
            and entry[0].principal_hash == principal_hash
            and (call_id is None or call_id in entry[0].pending_call_ids)
        ]
        if len(matches) != 1:
            return None
        key, snapshot = matches[0]
        return PendingAgentSnapshotClaim(key=key, token=object(), snapshot=snapshot)


def _clear_pending_request_snapshot(session_id: str | None) -> None:
    with _PENDING_AGENT_SNAPSHOT_LOCK:
        pending = _PENDING_AGENT_REQUESTS.pop(_session_memory_key(session_id), None)
        if pending is not None:
            authorization = getattr(pending[0].tool_context, "mcp_authorization", None)
            if authorization is not None:
                authorization.revoke()


def _require_resume_snapshot_current(
    snapshot: AgentRequestSnapshot,
    session_id: str | None,
) -> None:
    try:
        _require_request_snapshot_epochs_current(snapshot, session_id)
    except _RequestSnapshotEpochError as exc:
        _clear_pending_request_snapshot(session_id)
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_REQUEST_SNAPSHOT_UNAVAILABLE",
                    "message": "原请求快照已失效，请重新发送本轮请求。",
                },
            },
        ) from exc


def _clear_pending_memory_snapshot(session_id: str | None) -> None:
    """Compatibility alias; the cache now contains the whole Agent request snapshot."""
    _clear_pending_request_snapshot(session_id)


def _sse_event_type(sse: str) -> str | None:
    """Read the project event type without exposing its payload in logs."""
    for line in sse.splitlines():
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                return None
            event_type = payload.get("type") if isinstance(payload, dict) else None
            return event_type if isinstance(event_type, str) else None
    return None


def _sse_frames(sse: str) -> list[str]:
    """Split one adapter yield into its serialized SSE frames."""
    return [frame.strip() for frame in sse.replace("\r\n", "\n").split("\n\n") if frame.strip()]


def _sse_frame_payload(frame: str) -> dict[str, Any] | None:
    """Decode one JSON data frame, ignoring comments and malformed payloads."""
    data_lines = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
    if not data_lines:
        return None
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


_UPSTREAM_ZERO_OUTPUT_SIGNATURE = "Empty response from upstream (zero output tokens)"


def _is_known_upstream_zero_output(exc: BaseException) -> bool:
    """Match only the reproduced zero-output signature, including wrapped causes."""
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        candidates = [str(current)]
        for attribute in ("detail", "message"):
            value = getattr(current, attribute, None)
            if isinstance(value, str):
                candidates.append(value)
        if any(value.strip() == _UPSTREAM_ZERO_OUTPUT_SIGNATURE for value in candidates):
            return True
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException):
            pending.append(context)
    return False


class ChatErrorCode(str, Enum):
    HISTORY_UNAVAILABLE = "HISTORY_UNAVAILABLE"
    AUTH_FAILED = "AUTH_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_EMPTY_RESPONSE = "MODEL_EMPTY_RESPONSE"
    TIMEOUT = "TIMEOUT"
    RAG_FAILED = "RAG_FAILED"
    CONTEXT_LIMIT = "CONTEXT_LIMIT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"


# ═══════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════

class ChatMessageSchema(BaseModel):
    role: str
    content: Optional[str | list[dict]] = None  # str=纯文本, list=[{type,text|image_url},...]
    message_id: Optional[str] = None


class ChatRequest(BaseModel):
    messages: list[ChatMessageSchema] = Field(min_length=1)
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_k: Optional[int] = 5
    relevance_threshold: Optional[float] = 0.0  # 0.0-1.0, filter RAG results below this score
    system_prompt: Optional[str] = None
    long_term_memory: Optional[str] = Field(
        default=None, strict=True, max_length=MAX_LONG_TERM_MEMORY_CHARS,
    )
    provider: Optional[str] = None
    base_url: Optional[str] = None
    attachments: Optional[list[dict]] = None
    session_id: Optional[str] = None
    kb_ids: Optional[list[str]] = None  # Selected KB IDs for RAG search; None/empty = all enabled
    # Agent path opt-in (spec §10.1): when True + model supports tools + langgraph available,
    # the request flows through the ReAct agent instead of the single-shot LLM stream.
    use_agent: bool = False
    permission_mode: Literal["bypassPermissions", "default", "acceptEdits", "plan"] = "default"
    approved_plan_id: Optional[str] = Field(default=None, max_length=128)
    tool_keys: dict[str, str] = {}  # per-request tool credentials, e.g. {"tavily": "..."}
    skills_mode: Literal["off", "auto", "manual"] = "off"
    skill_ids: list[str] = Field(default_factory=list, max_length=3)
    history_mode: Literal["client", "server"] = "client"

    @field_validator("long_term_memory", mode="before")
    @classmethod
    def _reject_null_manual_memory(cls, value: Any) -> Any:
        """Use omission for saved fallback and an empty string for explicit empty."""
        if value is None:
            raise ValueError("long_term_memory must be a string when provided")
        return value

    @field_validator("skill_ids", mode="before")
    @classmethod
    def _validate_skill_id_values(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("skill_ids must be an array when provided")
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError("skill_ids must contain nonempty strings")
        return value

    @model_validator(mode="after")
    def _validate_skill_selection(self) -> "ChatRequest":
        if len(set(self.skill_ids)) != len(self.skill_ids):
            raise ValueError("skill_ids must be unique")
        if self.skills_mode == "manual" and not self.skill_ids:
            raise ValueError("manual Skills mode requires one to three skill_ids")
        if self.skills_mode != "manual" and self.skill_ids:
            raise ValueError("skill_ids are only valid in manual Skills mode")
        if self.history_mode == "server" and not self.session_id:
            raise ValueError("history_mode=server requires session_id")
        return self


class ModelsRequest(BaseModel):
    base_url: str
    provider: str = ""


# ═══════════════════════════════════════════
# Agent helpers
# ═══════════════════════════════════════════

def _build_agent_messages(payload: ChatRequest) -> list[dict]:
    """Convert ChatRequest.messages to langgraph agent input message dicts.

    Images are ISOLATED from the LLM: data URIs are cached and replaced with a
    text hint telling the Agent to call vision_analysis(image="cache:image_id"). This
    avoids 400 errors on text-only models (e.g. deepseek-v4-flash) and keeps
    base64 out of the LLM context (a screenshot can be 100KB+).

    Assistant-generated images are marked differently so the Agent does not
    mistake them for user uploads on follow-up turns.
    """
    messages: list[dict] = []
    for msg in payload.messages:
        content = _isolate_images_in_content(msg.content, msg.role)
        messages.append({"role": msg.role, "content": content})
    return messages


def _isolate_images_in_content(content, role: str = "user") -> str:
    """Return str content as-is; convert multimodal list to text only."""
    if isinstance(content, str):
        return content
    return _multimodal_to_text_with_image_hints(content, role)


def _multimodal_to_text_with_image_hints(parts, role: str = "user") -> str:
    """Strip image_url parts, cache them, append vision_analysis call hints.

    For user messages, instruct the Agent to call vision_analysis.
    For assistant messages, only label generated images so the Agent knows they
    are historical outputs, not new uploads.
    """
    from src.agent.tools.groups.retrieval.vision_analysis import cache_image
    texts: list[str] = []
    image_refs: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            texts.append(str(part.get("text", "")))
        elif ptype == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if not url:
                continue
            if url.startswith("data:"):
                image_id = cache_image(url)
                image_refs.append(f'cache:{image_id}')
            else:
                image_refs.append(url)
    if role == "assistant":
        for i, ref in enumerate(image_refs, 1):
            texts.append(f'[这是之前生成的图片 {i}，不是用户新上传的图片]')
        return "\n".join(texts)
    for i, ref in enumerate(image_refs, 1):
        texts.append(f'[用户上传了图片 {i}，请调用 vision_analysis(image="{ref}") 分析图片内容]')
    if image_refs:
        texts.append(f"共 {len(image_refs)} 张图片。分析图片时必须调用 vision_analysis 工具，不要凭空猜测图片内容。")
    return "\n".join(texts)


# ═══════════════════════════════════════════
# Pre-send history compaction
# ═══════════════════════════════════════════

def _transport_system_metadata(metadata: Any) -> dict:
    """Retain sealed host locators or the bounded graph-only summary marker."""
    from app.api.compaction_checkpoint import checkpoint_metadata_for_transport

    retained = checkpoint_metadata_for_transport(metadata)
    if retained:
        return retained
    try:
        from src.agent.compact.autocompact import (
            _MIDRUN_SUMMARY_MARKER,
            _MIDRUN_SUMMARY_MARKER_VALUE,
        )
    except Exception:
        return {}
    if (type(metadata) is dict
            and metadata.get(_MIDRUN_SUMMARY_MARKER) == _MIDRUN_SUMMARY_MARKER_VALUE):
        return {_MIDRUN_SUMMARY_MARKER: dict(_MIDRUN_SUMMARY_MARKER_VALUE)}
    return {}


def _dicts_to_langchain_messages(dicts: list[dict]) -> list:
    """Convert agent_input message dicts to langchain Message objects."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    cls_by_role = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
    messages: list = []
    for d in dicts:
        cls = cls_by_role.get(d.get("role", "user"), HumanMessage)
        retained = (
            _transport_system_metadata(d.get("additional_kwargs"))
            if cls is SystemMessage else {}
        )
        messages.append(cls(content=d.get("content", ""), additional_kwargs=retained))
    return messages


def _langchain_messages_to_dicts(messages: list) -> list[dict]:
    """Convert langchain Message objects back to agent_input dicts."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    role_by_cls = {HumanMessage: "user", AIMessage: "assistant", SystemMessage: "system"}
    dicts: list[dict] = []
    for m in messages:
        role = role_by_cls.get(type(m), "user")
        content = getattr(m, "content", "")
        if not isinstance(content, str):
            content = str(content)
        item = {"role": role, "content": content}
        if type(m) is SystemMessage:
            retained = _transport_system_metadata(m.additional_kwargs)
            if retained:
                item["additional_kwargs"] = retained
        dicts.append(item)
    return dicts


def _last_plain_user_turn(payload: Any) -> str | None:
    messages = getattr(payload, "messages", None)
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    if getattr(last, "role", None) != "user":
        return None
    content = getattr(last, "content", None)
    return content if isinstance(content, str) else None


async def _prepare_chat_skill_install(
    payload: Any,
    user: dict[str, Any] | None,
    model: str,
) -> tuple[Any | None, str | None]:
    """Preflight only an explicit, plain-text install command from the latest user turn."""
    from src.agent.tools.groups.skills.install_tool import (
        SkillInstallService,
        parse_explicit_install_intent,
    )

    user_text = _last_plain_user_turn(payload)
    if parse_explicit_install_intent(user_text) is None:
        return None, None
    if not (_AGENT_PATH_AVAILABLE and _should_use_agent(payload, model)):
        return None, "当前模型不能安全执行 Agent 工具调用；没有安装技能。请切换到支持工具调用的模型后重试。"
    try:
        from src.agent.agent_factory import _load_disabled_tools

        if "install_github_skill" in _load_disabled_tools():
            return None, "技能安装工具当前已禁用；没有安装技能。"
        preparation = await asyncio.to_thread(
            SkillInstallService().prepare,
            user_text,
            user,
            _session_memory_key(getattr(payload, "session_id", None)),
        )
    except Exception as exc:
        logger.warning("chat Skill install preflight failed error_type=%s", type(exc).__name__)
        return None, "无法安全预览该技能；没有执行安装。请稍后重试。"
    if preparation is None:
        return None, None
    return preparation.tool, preparation.response


def _bind_request_graph(snapshot: AgentRequestSnapshot, session_id: str) -> AgentRequestSnapshot:
    """Mint a host graph identity before construction, without archive authority."""
    from app.api.context_events import ContextEventScope

    scope = snapshot.tool_event_scope
    request_id = uuid.uuid4().hex
    if (type(scope) is ContextEventScope and scope.session_id == session_id
            and scope.principal_hash == snapshot.principal_hash
            and scope.session_epoch == snapshot.session_delete_epoch):
        request_id = scope.request_id
    config = build_agent_config(session_id, request_id=request_id)
    return replace(snapshot, business_session_id=session_id, request_id=request_id,
                   graph_config=copy.deepcopy(config))


async def _admit_published_history(
    snapshot: AgentRequestSnapshot,
    payload: ChatRequest,
    incoming: list,
    *,
    source_message_ids: tuple[str | None, ...] | None = None,
    transient_current_user: bool = True,
    verified_source_ids: bool = False,
) -> tuple[list, tuple[str | None, ...] | None]:
    """Offer complete plain input to the host reader; never infer publication."""
    if (type(source_message_ids) is not tuple
            or len(source_message_ids) != len(incoming)):
        return incoming, None

    if not verified_source_ids:
        expected_source_message_ids = _build_compaction_source_message_ids(
            payload, incoming, transient_current_user=transient_current_user,
        )
        if expected_source_message_ids is None or source_message_ids != expected_source_message_ids:
            return incoming, None

    def unchanged() -> tuple[list, tuple[str | None, ...]]:
        return incoming, source_message_ids

    # Only durable IDs may reach the owner reader. The quote/current-user tail
    # is kept verbatim for the graph and never participates in admission.
    persisted_end = len(source_message_ids)
    while persisted_end > 0 and source_message_ids[persisted_end - 1] is None:
        persisted_end -= 1
    persisted_messages = incoming[:persisted_end]
    if not persisted_messages:
        return unchanged()

    try:
        from app.api.compaction_archive import get_compaction_scope
        from app.api.context_events import ContextEventScope
        from app.api.compaction_checkpoint import HostContextReplacement, load_published_context
        from src.agent.compact.checkpoint_publish import _run_host_record_transaction

        scope = get_compaction_scope()
        if (type(scope) is not ContextEventScope
                or scope is not snapshot.context_event_scope
                or scope.principal_hash != snapshot.principal_hash
                or scope.session_id != snapshot.business_session_id or scope.request_id != snapshot.request_id
                or scope.session_epoch != snapshot.session_delete_epoch
        ):
            return unchanged()
        replacement = await _run_host_record_transaction(
            load_published_context, scope, persisted_messages, skills_mode="off",
        )
        if type(replacement) is not HostContextReplacement:
            return unchanged()
        prefix_count = replacement.prefix_count
        if (type(prefix_count) is not int or not 0 < prefix_count <= len(persisted_messages)
                or any(message_id is None for message_id in source_message_ids[:prefix_count])
                or type(replacement.tail) is not tuple
                or len(replacement.tail) != len(persisted_messages) - prefix_count
                or any(message is not persisted_messages[prefix_count + index]
                       for index, message in enumerate(replacement.tail))):
            return unchanged()
        # Project only the verified host checkpoint; the suffix stays untouched.
        checkpoint = _langchain_messages_to_dicts([replacement.checkpoint])[0]
        if (checkpoint["role"] != "system" or checkpoint["content"] != replacement.checkpoint.content
                or checkpoint.get("additional_kwargs") != replacement.checkpoint.additional_kwargs):
            return unchanged()
        # Keep the locator sidecar separate from message/model fields. The
        # checkpoint inherits only a verified persisted prefix. All transient
        # quote/current-user messages stay outside the owner reader's inputs.
        transient_suffix = incoming[persisted_end:]
        return ([checkpoint, *replacement.tail, *transient_suffix],
                (None, *source_message_ids[prefix_count:]))
    except Exception:
        logger.warning("Published history admission unavailable; retaining complete input")
        return unchanged()


def _build_compaction_source_message_ids(
    payload: ChatRequest,
    agent_messages: list[dict],
    *,
    transient_current_user: bool,
) -> tuple[str | None, ...] | None:
    """Bind persisted turns and a position-checked transient quote/user suffix."""
    request_messages = tuple(payload.messages)
    if not request_messages or len(request_messages) != len(agent_messages):
        return None
    final_index = len(request_messages) - 1
    quote_start = final_index
    while quote_start > 0 and request_messages[quote_start - 1].role == "system":
        quote_start -= 1
    has_transient_quote = quote_start < final_index
    if ((transient_current_user or has_transient_quote)
            and request_messages[final_index].role != "user"):
        return None

    message_ids: list[str] = []
    sidecar: list[str | None] = []
    for index, (request_message, agent_message) in enumerate(zip(
        request_messages, agent_messages, strict=True,
    )):
        message_id = request_message.message_id
        if (
            type(agent_message) is not dict
            or agent_message.get("role") != request_message.role
            or type(request_message.content) is not str
            or type(agent_message.get("content")) is not str
            or agent_message.get("content") != request_message.content
        ):
            return None

        if request_message.role == "system":
            if not (quote_start <= index < final_index) or message_id is not None:
                return None
            sidecar.append(None)
            continue
        if (request_message.role not in {"user", "assistant"}
                or type(message_id) is not str or not message_id):
            return None
        message_ids.append(message_id)
        if index == final_index and (transient_current_user or has_transient_quote):
            sidecar.append(None)
        else:
            sidecar.append(message_id)
    if len(set(message_ids)) != len(message_ids):
        return None
    return tuple(sidecar)


def _session_context_window(session_id: Optional[str]) -> Optional[int]:
    """Read context_window from the session row; None when unknown."""
    from src.agent.sse_helpers import _load_session_context_window
    return _load_session_context_window(session_id or "default")


def _compact_event_payload(threshold: int, context_window: int, msg_count: int) -> dict:
    """Build the context_compressing SSE payload."""
    return {
        "reason": "history_exceeds_threshold",
        "threshold_tokens": threshold,
        "context_window": context_window,
        "message_count": msg_count,
    }


async def _run_autocompact(
    messages: list, creds: dict, context_window: int, *,
    source_message_ids: tuple[str | None, ...] | None = None,
    token_threshold: int | None = None,
) -> list:
    """Build an LLM client and run autocompact_messages."""
    from src.agent.compact.autocompact import autocompact_messages
    llm_client = make_client(
        api_key=creds.get("api_key"), base_url=creds.get("base_url"), model=creds.get("model"),
    )
    return await autocompact_messages(
        messages, llm_client, context_window,
        source_message_ids=source_message_ids,
        token_threshold=token_threshold,
    )


async def _check_and_compact_history(
    payload: ChatRequest, creds: dict, agent_input: dict,
    context_budget: Any = None,
    *, request_snapshot: AgentRequestSnapshot | None = None,
    allow_compaction: bool = False,
    source_message_ids: tuple[str | None, ...] | None = None,
    source_message_ids_out: list[tuple[str | None, ...] | None] | None = None,
    verified_history: bool = False,
) -> AsyncIterator[str]:
    """Pre-send: compact history when it reaches the resolved input budget."""
    from src.agent.compact.autocompact import should_autocompact
    if context_budget is None:
        from src.agent.context_guard import resolve_context_budget

        session_window = _session_context_window(payload.session_id)
        requested_tokens = getattr(payload, "max_tokens", None)
        if type(session_window) is int and session_window > 0 and (
            requested_tokens is None
            or (type(requested_tokens) is int and requested_tokens >= session_window)
        ):
            requested_tokens = max(1, session_window // 4)
        context_budget = resolve_context_budget(
            creds.get("model"), session_window, requested_tokens,
        )
    lc_messages = _dicts_to_langchain_messages(agent_input["messages"])
    threshold = context_budget.token_limit
    context_window = context_budget.effective_window
    if not should_autocompact(lc_messages, threshold):
        return
    if not allow_compaction:
        raise AgentRequestBudgetError(
            "Pre-send compaction was unavailable and the full history exceeds the context budget"
        )
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from app.api.compaction_checkpoint import checkpoint_metadata_for_transport

    if (
        source_message_ids is None
        or type(source_message_ids) is not tuple
        or len(source_message_ids) != len(lc_messages)
        or any(type(message_id) not in (str, type(None))
               for message_id in source_message_ids)
    ):
        raise AgentRequestBudgetError(
            "Pre-send V3 compaction requires complete plain-text message IDs"
        )

    yield sse_event(
        "context_compressing",
        _compact_event_payload(
            threshold, context_budget.effective_window, len(lc_messages),
        ),
    )
    if request_snapshot is not None:
        _require_request_snapshot_epochs_current(
            request_snapshot, payload.session_id,
        )
        from app.api.compaction_archive import get_compaction_scope
        if get_compaction_scope() is not request_snapshot.context_event_scope:
            raise AgentRequestBudgetError("Pre-send request scope changed")
    from src.agent.compact.autocompact import _is_midrun_summary_message

    messages = lc_messages
    current_source_ids = source_message_ids
    compacted = messages
    max_rounds = max(1, len(messages) + 1)
    for _round in range(max_rounds):
        if not should_autocompact(messages, threshold):
            break
        next_compacted = await _run_autocompact(
            messages, creds, context_window,
            source_message_ids=current_source_ids,
            token_threshold=threshold,
        )
        if next_compacted is messages:
            break
        retained_count = len(next_compacted) - 1
        retained_start = len(messages) - retained_count
        is_summary = (
            type(next_compacted[0]) is SystemMessage
            and (checkpoint_metadata_for_transport(next_compacted[0].additional_kwargs)
                 or _is_midrun_summary_message(next_compacted[0]))
        )
        if (not is_summary or retained_start < 0
                or any(new is not old for new, old in zip(
                    next_compacted[1:], messages[retained_start:], strict=True,
                ))):
            break
        current_source_ids = (None, *current_source_ids[retained_start:])
        messages = next_compacted
        compacted = messages
    if should_autocompact(compacted, threshold):
        raise AgentRequestBudgetError(
            "Pre-send compaction did not bring history within the context budget"
        )
    if compacted is not lc_messages:
        if source_message_ids_out is not None:
            source_message_ids_out[:] = [current_source_ids]
        agent_input["messages"] = _langchain_messages_to_dicts(compacted)


async def _run_agent_stream(
    payload: ChatRequest,
    creds: dict,
    long_term_memory: str | None = None,
    request_snapshot: AgentRequestSnapshot | None = None,
    *,
    authenticated_user: dict | None = None,
    assembled_history: Any = None,
) -> AsyncIterator[str]:
    """Build the agent and yield SSE events from its stream.

    Raises on construction failure so the caller can fall back to the LLM stream.
    """
    session_id = _session_memory_key(payload.session_id)
    from src.agent.context_guard import resolve_context_budget

    context_budget = resolve_context_budget(
        creds.get("model"),
        _session_context_window(payload.session_id),
        payload.max_tokens,
    )
    resolved_memory = long_term_memory
    if resolved_memory is None:
        resolved_memory = getattr(payload, "long_term_memory", None) or ""
    snapshot = request_snapshot or _request_snapshot_from_payload(payload, resolved_memory)
    resolved_memory = snapshot.long_term_memory
    _require_request_snapshot_epochs_current(snapshot, payload.session_id)
    snapshot = _ensure_skills_runtime(snapshot)
    request_payload = _payload_with_request_snapshot(payload, snapshot)
    snapshot = _ensure_external_tool_snapshot(snapshot, session_id, request_payload)
    snapshot, manual_events, manual_load_succeeded = await _preload_manual_skills(
        snapshot, session_id,
    )
    _require_request_snapshot_epochs_current(snapshot, payload.session_id)
    for event in manual_events:
        _require_request_snapshot_epochs_current(snapshot, payload.session_id)
        yield event
    if not manual_load_succeeded:
        raise RuntimeError("Selected skill instructions could not be loaded")
    effective_payload = _payload_with_request_snapshot(payload, snapshot)
    snapshot = _ensure_request_tool_context(snapshot, effective_payload, session_id)
    if snapshot.tool_context is not None:
        snapshot.tool_context.context_token_limit = context_budget.token_limit
    effective_payload = _payload_with_request_snapshot(payload, snapshot)
    agent = await _build_agent_for_payload(
        effective_payload, creds, resolved_memory, request_snapshot=snapshot,
        context_window_override=context_budget.effective_window,
    )
    _require_request_snapshot_epochs_current(snapshot, payload.session_id)
    config = build_agent_config(session_id)
    writer_binding = None
    writer_lease = None
    try:
        from src.agent.compact.writer_lifecycle import (
            WriterOwnerConflictError,
            WriterQuarantinedError,
        )
    except Exception as exc:
        # Do not start a graph writer without the checkpoint ownership guard.
        raise _WriterLifecycleUnavailableError(
            "Agent writer lifecycle is unavailable"
        ) from exc
    else:
        try:
            writer_binding = _build_request_writer_binding(
                agent, config, snapshot, payload.session_id,
            )
            writer_lease = _acquire_request_writer_lease(writer_binding)
        except _RequestSnapshotEpochError:
            raise
        except WriterOwnerConflictError:
            # Never allow another request to write the same checkpoint concurrently.
            raise
        except WriterQuarantinedError:
            # A quarantined writer has an unresolved native run or checkpoint write.
            raise
        except Exception as exc:
            # Never reset, stream, or write through an unbound graph.
            raise _WriterLifecycleUnavailableError(
                "Agent writer lifecycle is unavailable"
            ) from exc

    snapshot_pending = False
    try:
        if writer_binding is not None and writer_lease is not None:
            from src.agent.agent_factory import reset_thread_checkpoint_for_writer

            await reset_thread_checkpoint_for_writer(writer_binding, writer_lease)
            _require_request_snapshot_epochs_current(snapshot, payload.session_id)
        _clear_pending_request_snapshot(session_id)
        if getattr(effective_payload, "history_mode", "client") == "server":
            if (
                assembled_history is None
                or type(assembled_history.source_message_ids) is not tuple
                or len(assembled_history.source_message_ids) != len(assembled_history.messages)
                or any(type(message) is not dict for message in assembled_history.messages)
            ):
                yield sse_event("error", {
                    "code": ChatErrorCode.HISTORY_UNAVAILABLE,
                    "message": "无法确认服务端历史，已停止本次请求；原有历史未被替换。",
                })
                yield sse_event("done", {"success": False, "completed": False})
                return
            agent_messages = [dict(message) for message in assembled_history.messages]
            request_source_message_ids = assembled_history.source_message_ids
            verified_history = True
        else:
            agent_messages = _build_agent_messages(effective_payload)
            request_source_message_ids = _build_compaction_source_message_ids(
                effective_payload, agent_messages, transient_current_user=True,
            )
            verified_history = False
        agent_input = {"messages": agent_messages}
        compaction_source_message_ids = request_source_message_ids
        compaction_source_message_ids_out = [compaction_source_message_ids]
        # Pre-send history check uses the same output/framing-adjusted budget as the stream guard.
        with bind_compaction_archive(snapshot.context_event_scope):
            async for sse in _check_and_compact_history(
                effective_payload, creds, agent_input, context_budget,
                request_snapshot=snapshot,
                allow_compaction=snapshot.context_event_scope is not None,
                source_message_ids=compaction_source_message_ids,
                source_message_ids_out=compaction_source_message_ids_out,
                verified_history=verified_history,
            ):
                _require_request_snapshot_epochs_current(snapshot, payload.session_id)
                yield sse
                _require_request_snapshot_epochs_current(snapshot, payload.session_id)
        compaction_source_message_ids = compaction_source_message_ids_out[0]
        _require_request_snapshot_epochs_current(snapshot, payload.session_id)
        call_counter: Counter = Counter()
        _require_request_snapshot_epochs_current(snapshot, payload.session_id)
        async for sse in _stream_agent_in_context(
            agent, agent_input, config, call_counter,
            effective_payload.permission_mode, creds.get("model", ""),
            snapshot.tool_context, writer_binding, writer_lease,
            effective_payload.max_tokens, context_budget,
            archive_scope=snapshot.context_event_scope,
            tool_event_scope=snapshot.tool_event_scope,
            compaction_source_message_ids=compaction_source_message_ids,
            compaction_source_messages=tuple(agent_input["messages"]),
            request_snapshot=snapshot,
        ):
            _require_request_snapshot_epochs_current(snapshot, payload.session_id)
            event_type = _sse_event_type(sse)
            if event_type == "tool_confirm_required":
                _store_pending_request_snapshot(session_id, snapshot)
                snapshot_pending = True
            elif event_type == "error" and snapshot_pending:
                _clear_pending_request_snapshot(session_id)
                snapshot_pending = False
            yield sse
            _require_request_snapshot_epochs_current(snapshot, payload.session_id)
    except Exception:
        if snapshot_pending:
            _clear_pending_request_snapshot(session_id)
        raise
    finally:
        if writer_binding is not None and writer_lease is not None:
            _release_request_writer_lease(writer_binding, writer_lease)


async def _stream_agent_in_context(
    agent, agent_input, config, counter, permission_mode, model, tool_context,
    writer_binding=None, writer_lease=None, requested_max_tokens=None,
    context_budget=None, *, archive_scope=None, tool_event_scope=None,
    compaction_source_message_ids: tuple[str | None, ...] | None = None,
    compaction_source_messages: tuple[dict, ...] | None = None,
    request_snapshot: AgentRequestSnapshot | None = None,
):
    from contextlib import nullcontext
    from src.agent.request_context import activate_tool_context

    grant = getattr(
        getattr(request_snapshot, "skill_install_tool", None), "_install_grant", None,
    )
    if grant is not None:
        from src.agent.tools.groups.skills.install_tool import activate_install_binding

        install_scope = activate_install_binding(grant)
    else:
        install_scope = nullcontext()
    with activate_tool_context(tool_context), install_scope:
        with bind_compaction_archive(archive_scope), bind_tool_event_archive(tool_event_scope):
            with _request_writer_context(
                writer_binding, writer_lease, model, config, request_snapshot,
            ):
                async for event in stream_agent_to_sse(
                    agent, agent_input, config, counter, permission_mode, model=model,
                    requested_max_tokens=requested_max_tokens,
                    context_budget=context_budget,
                    compaction_source_message_ids=compaction_source_message_ids,
                    compaction_source_messages=compaction_source_messages,
                ):
                    yield event


async def _build_agent_for_payload(
    payload: ChatRequest,
    creds: dict,
    long_term_memory: str | None = None,
    request_snapshot: AgentRequestSnapshot | None = None,
    context_window_override: int | None = None,
):
    """Instantiate tools + the compiled ReAct agent for this request.

    enable_hitl is enabled for default/acceptEdits modes so the tools node
    pauses for permission gating; bypassPermissions runs tools directly.
    """
    snapshot = request_snapshot
    if snapshot is not None:
        snapshot = _ensure_skills_runtime(snapshot)
        payload = _payload_with_request_snapshot(payload, snapshot)
    else:
        snapshot = _ensure_skills_runtime(
            _request_snapshot_from_payload(
                payload,
                long_term_memory
                if long_term_memory is not None
                else getattr(payload, "long_term_memory", None) or "",
            )
        )
    session_id = _session_memory_key(payload.session_id)
    snapshot = _ensure_external_tool_snapshot(snapshot, session_id, payload)
    snapshot = _ensure_request_tool_context(snapshot, payload, session_id)
    _seed_skill_install_tool_context(
        snapshot.tool_context, snapshot.skill_install_tool,
    )
    tools = build_tools(
        payload,
        skills_runtime=snapshot.skills_runtime,
        mcp_tools=snapshot.mcp_tools,
        tool_context=snapshot.tool_context,
    )
    _attach_skill_install_tool(
        snapshot.tool_context, tools, snapshot.skill_install_tool,
    )
    plan_mode, tools = _apply_plan_mode_tools(snapshot, tools)
    # HITL must stay enabled even in bypass mode: PermissionClassifier still
    # asks for HIGH/ALWAYS tools, and disabling the interrupt would skip it.
    enable_hitl = True if plan_mode else bool(tools)
    resolved_memory = snapshot.long_term_memory
    skills_catalog = (
        snapshot.skills_runtime.catalog()
        if snapshot.skills_mode == "auto" and snapshot.skills_runtime is not None
        else []
    )
    kwargs: dict = {
        "model": creds["model"], "api_key": creds["api_key"],
        "base_url": creds["base_url"], "tools": tools,
        "enable_hitl": enable_hitl,
        "long_term_memory": resolved_memory,
        "skills_mode": snapshot.skills_mode,
        "skills_catalog": skills_catalog,
        "skills_context": list(snapshot.skills_context),
        "plan_mode": plan_mode,
        "approved_plan_text": snapshot.approved_plan_text,
    }
    last_user_query = ""
    for message in reversed(payload.messages):
        if message.role == "user":
            content = message.content
            last_user_query = content if isinstance(content, str) else str(content or "")
            break
    kwargs["user_query"] = last_user_query
    kwargs["session_id"] = _session_memory_key(payload.session_id)
    _optional_arg(kwargs, "temperature", payload.temperature)
    _optional_arg(kwargs, "max_tokens", payload.max_tokens)
    _optional_arg(kwargs, "context_window_override", context_window_override)
    return await create_hardware_agent(**kwargs)


def _optional_arg(kwargs: dict, key: str, value) -> None:
    """Add key=value to kwargs only when value is not None (let the callee use its default)."""
    if value is not None:
        kwargs[key] = value


def _filter_plan_mode_tools(tools: list) -> list:
    """Final plan-mode exposure: read-only allowlist plus submit_plan."""
    from src.agent.plan_policy import SUBMIT_PLAN_TOOL_NAME, filter_tools_for_plan_mode
    from src.agent.tools.groups.execution.submit_plan import SubmitPlanTool

    filtered = filter_tools_for_plan_mode(tools)
    if not any(tool.name == SUBMIT_PLAN_TOOL_NAME for tool in filtered):
        filtered.append(SubmitPlanTool())
    return filtered


def _apply_plan_mode_tools(snapshot: AgentRequestSnapshot, tools: list) -> tuple[bool, list]:
    """Filter the final tool list and rebuild request-local lookup in plan mode."""
    if snapshot.permission_mode != "plan":
        return False, tools
    filtered = _filter_plan_mode_tools(tools)
    # The appended SubmitPlanTool is created after build_tool_specs injected
    # _ctx, so bind the request context explicitly before the graph runs.
    for tool in filtered:
        setattr(tool, "_ctx", snapshot.tool_context)
    replace_request_tools(snapshot.tool_context, filtered)
    return True, filtered


# ═══════════════════════════════════════════
# Recovery R1 — run lifecycle
# ═══════════════════════════════════════════

def _resolve_run_store() -> Any | None:
    """Return the run store, or None when persistence is degraded."""
    try:
        return run_events.get_run_store()
    except Exception:
        logger.warning(
            "Run persistence store is unavailable; continuing without recovery",
            exc_info=True,
        )
        return None


def _sanitized_run_request(payload: ChatRequest) -> dict[str, Any]:
    """Persist only request fields needed to rebuild the HITL card."""
    return payload.model_dump(exclude={"tool_keys", "attachments"})


def _create_chat_run(
    store: Any | None,
    payload: ChatRequest,
    model: str,
    session_id: str,
    principal_id: str | None,
) -> str:
    run_id = run_events.new_run_id()
    if store is None:
        return run_id
    config = {
        "history_mode": payload.history_mode,
        "skills_mode": payload.skills_mode,
        "use_agent": payload.use_agent,
        "temperature": payload.temperature,
        "max_tokens": payload.max_tokens,
        "kb_ids": payload.kb_ids,
        "top_k": payload.top_k,
        "relevance_threshold": payload.relevance_threshold,
        "principal_id": principal_id,
    }
    try:
        store.create_run({
            "id": run_id,
            "session_id": session_id or "",
            "status": run_events.RUN_STATUS_RUNNING,
            "model": model,
            "permission_mode": payload.permission_mode,
            "config_json": json.dumps(config, ensure_ascii=False),
            "thread_id": session_id or "",
            "checkpoint_id": None,
            "started_at": run_events.utc_now_iso(),
            "finished_at": None,
            "last_event_seq": 0,
            "error_code": None,
            "error_message": None,
        })
    except Exception:
        logger.warning("Run record could not be created run=%s", run_id, exc_info=True)
    return run_id


def _require_resume_run(
    store: Any | None,
    run_id: str,
    session_id: str,
    decision: str,
    call_id: str | None,
) -> dict[str, Any]:
    """Validate that a HITL resume targets the live paused run of this session."""
    if store is None:
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": "RUN_STORE_UNAVAILABLE",
                    "message": "运行记录存储不可用，请稍后重试。",
                },
            },
        )
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "success": False,
                "error": {"code": "RUN_NOT_FOUND", "message": "运行记录不存在。"},
            },
        )
    if run.get("session_id") != session_id:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "RUN_SESSION_MISMATCH",
                    "message": "该运行不属于当前会话，无法恢复。",
                },
            },
        )
    if run.get("status") not in run_events.ACTIVE_RUN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "RUN_NOT_AWAITING_CONFIRMATION",
                    "message": "该运行当前不在等待确认状态。",
                    "status": run.get("status"),
                },
            },
        )
    if decision == "allow" and not call_id:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_CONFIRMATION_STALE",
                    "message": "确认已失效，请重新发送请求。",
                },
            },
        )
    return run


def _consume_resume_approvals(
    store: Any, run_id: str, decision: str, call_id: str | None,
) -> list[dict[str, Any]]:
    """Consume the persisted pending approval once; staleness is fatal.

    ``allow`` targets the submitted call; ``deny``/``stop`` apply to the whole
    pause, so every pending row of the run is decided.
    """
    try:
        consumed = store.consume_pending_approvals(
            run_id,
            call_id=call_id if decision == "allow" else None,
            decision=decision,
        )
    except Exception:
        logger.warning(
            "Pending approval consumption failed run=%s", run_id, exc_info=True,
        )
        consumed = []
    if not consumed:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_CONFIRMATION_STALE",
                    "message": "确认已失效或已被使用，请重新发送本轮请求。",
                },
            },
        )
    return consumed


# ═══════════════════════════════════════════
# POST /api/chat — SSE 流式聊天
# ═══════════════════════════════════════════

def _plan_error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


def _require_plan_mode_agent(payload: ChatRequest, model: str) -> None:
    if payload.permission_mode != "plan":
        return
    if not (_AGENT_PATH_AVAILABLE and _should_use_agent(payload, model)):
        raise _plan_error(
            "PLAN_REQUIRES_AGENT",
            "计划模式需要支持工具调用的 Agent 模型。",
            400,
        )


def _resolve_approved_plan(payload: ChatRequest, session_id: str, model: str) -> dict | None:
    plan_id = payload.approved_plan_id
    if not plan_id:
        return None
    if payload.permission_mode == "plan":
        raise _plan_error("PLAN_MODE_CONFLICT", "计划模式下不能同时执行已批准计划。", 409)
    if not (_AGENT_PATH_AVAILABLE and _should_use_agent(payload, model)):
        raise _plan_error("PLAN_REQUIRES_AGENT", "执行已批准计划需要 Agent 模型。", 400)
    from app.db import plan_store

    plan = plan_store.get(plan_id)
    if plan is None or plan.get("session_id") != session_id:
        raise _plan_error("PLAN_NOT_FOUND", "计划不存在或不属于当前会话。", 404)
    latest = plan_store.latest(session_id)
    if latest is None or latest.get("id") != plan_id:
        raise _plan_error("PLAN_SUPERSEDED", "计划已被更新版本替代，请重新批准。", 409)
    if plan.get("status") != "approved":
        raise _plan_error("PLAN_NOT_APPROVED", "计划尚未批准或已失效。", 409)
    return plan


def _seed_plan_todos(session_id: str, plan: dict) -> list[dict] | None:
    from app.db import todo_store

    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    todos = [
        {"content": str(step.get("title", "")).strip(), "status": "pending", "priority": "medium"}
        for step in steps
        if isinstance(step, dict) and str(step.get("title", "")).strip()
    ]
    if not todos:
        return None
    try:
        todo_store.replace_todos(session_id, todos)
    except Exception:
        logger.warning("plan todo seeding failed session=%s", session_id, exc_info=True)
        return None
    return todos


@router.post("/chat")
async def chat_sse(payload: ChatRequest, request: Request, user: dict = Depends(current_user)):
    """RAG 流式聊天，严格匹配前端 SSE 事件协议。"""
    creds = resolve_credentials(payload, request)
    api_key = creds["api_key"]
    base_url = creds["base_url"]
    model = creds["model"]
    provider = creds["provider"]
    _require_plan_mode_agent(payload, model)
    from src.agent.tools.groups.skills.install_tool import parse_explicit_install_intent

    explicit_install = (
        parse_explicit_install_intent(_last_plain_user_turn(payload)) is not None
    )
    if payload.skills_mode != "off" and not (
        _AGENT_PATH_AVAILABLE and _should_use_agent(payload, model)
    ) and not explicit_install:
        raise HTTPException(
            status_code=422,
            detail={
                "success": False,
                "error": {
                    "code": "SKILLS_AGENT_UNAVAILABLE",
                    "message": "当前请求不能使用 Skills；请选择支持工具调用的 Agent 模型后重试。",
                },
            },
        )
    try:
        session_epoch, memory_epoch = _capture_request_snapshot_epochs(payload.session_id)
    except _RequestSnapshotEpochError as exc:
        logger.warning("request snapshot epochs could not be pinned")
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": ChatErrorCode.INTERNAL_ERROR,
                    "message": "无法验证请求上下文，请稍后重试。",
                },
            },
        ) from exc
    try:
        long_term_memory = resolve_long_term_memory(payload)
    except ValueError:
        logger.error("long-term memory setting is invalid")
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": "LONG_TERM_MEMORY_UNAVAILABLE",
                    "message": "已保存的长期记忆无效，请检查设置后重试。",
                },
            },
        )
    except Exception:
        logger.error("long-term memory setting could not be read")
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": "LONG_TERM_MEMORY_UNAVAILABLE",
                    "message": "无法读取已保存的长期记忆，请稍后重试。",
                },
            },
        )
    skill_install_tool, skill_install_response = await _prepare_chat_skill_install(
        payload, user, model,
    )
    if payload.permission_mode == "plan" and (explicit_install or skill_install_response is not None):
        raise _plan_error(
            "PLAN_MODE_SKILLS_UNAVAILABLE",
            "计划模式只读，不能安装或导入技能。",
            400,
        )
    if payload.skills_mode != "off" and not (
        _AGENT_PATH_AVAILABLE and _should_use_agent(payload, model)
    ) and skill_install_response is None and not explicit_install:
        raise HTTPException(
            status_code=422,
            detail={
                "success": False,
                "error": {
                    "code": "SKILLS_AGENT_UNAVAILABLE",
                    "message": "当前请求不能使用 Skills；请选择支持工具调用的 Agent 模型后重试。",
                },
            },
        )
    tool_event_scope = make_compaction_scope(user, payload.session_id, session_epoch)
    context_event_scope = tool_event_scope
    request_snapshot = replace(
        _request_snapshot_from_payload(
            payload,
            long_term_memory,
            session_delete_epoch=session_epoch,
            memory_clear_epoch=memory_epoch,
        ),
        principal_id=_verified_principal_id(user),
        session_id=_session_memory_key(payload.session_id),
        business_session_id=_session_memory_key(payload.session_id),
        principal_hash=compaction_principal(user),
        context_event_scope=context_event_scope,
        tool_event_scope=tool_event_scope,
        skill_install_tool=skill_install_tool,
    )
    try:
        _require_request_snapshot_epochs_current(request_snapshot, payload.session_id)
    except _RequestSnapshotEpochError as exc:
        logger.warning("request snapshot changed while loading long-term memory")
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": ChatErrorCode.INTERNAL_ERROR,
                    "message": "请求上下文已变化，请重新发起本轮请求。",
                },
            },
        ) from exc
    try:
        request_snapshot = _ensure_skills_runtime(request_snapshot)
    except Exception as exc:
        from src.skills.errors import SkillError
        if isinstance(exc, SkillError):
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "success": False,
                    "error": {"code": exc.code, "message": str(exc)},
                },
            ) from exc
        logger.error("Skills request snapshot could not be created")
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": "SKILLS_UNAVAILABLE",
                    "message": "无法读取所选 Skills，请检查技能状态后重试。",
                },
            },
        ) from exc

    try:
        _require_request_snapshot_epochs_current(request_snapshot, payload.session_id)
    except _RequestSnapshotEpochError as exc:
        logger.warning("request snapshot changed before stream creation")
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": ChatErrorCode.INTERNAL_ERROR,
                    "message": "请求上下文已变化，请重新发起本轮请求。",
                },
            },
        ) from exc

    run_store = _resolve_run_store()
    run_session_id = _session_memory_key(payload.session_id)
    run_principal_id = _verified_principal_id(user)
    approved_plan = _resolve_approved_plan(payload, run_session_id, model)
    if approved_plan is not None:
        from src.agent.plan_prompt import build_approved_plan_prompt
        request_snapshot = replace(
            request_snapshot,
            approved_plan_id=approved_plan["id"],
            approved_plan_text=build_approved_plan_prompt(approved_plan),
        )
    run_id = _create_chat_run(
        run_store, payload, model, run_session_id, run_principal_id,
    )
    if request_snapshot.tool_context is not None:
        request_snapshot.tool_context.run_id = run_id
    seeded_todos = None
    if approved_plan is not None:
        from app.db import plan_store
        try:
            first_execution = plan_store.mark_executed(approved_plan["id"], run_id)
        except Exception:
            logger.warning("plan execution claim failed plan=%s", approved_plan["id"], exc_info=True)
            first_execution = False
        if first_execution:
            seeded_todos = _seed_plan_todos(run_session_id, approved_plan)
    run_request_snapshot = _sanitized_run_request(payload)

    async def event_generator():
        """SSE 流式生成器，带 CancelledError 处理 + idle timeout + finally 清理。"""
        import asyncio as _asyncio
        import time
        from src.agent.exceptions import ModelEmptyResponseError

        msgs = payload.messages

        try:
            if skill_install_response is not None:
                yield sse_event("text", {"content": skill_install_response})
                yield sse_event("done", {"success": True, "completed": True, "usage": None})
                return

            if approved_plan is not None:
                if seeded_todos:
                    yield sse_event("todo_update", {
                        "todos": seeded_todos, "session_id": run_session_id,
                    })
                from app.db import plan_store
                try:
                    current_plan = plan_store.get(approved_plan["id"]) or approved_plan
                except Exception:
                    current_plan = approved_plan
                yield sse_event("plan_update", {"plan": current_plan})

            # ── 附件 + 历史构建 ──
            attachment_texts, image_parts = await _process_attachments(payload)
            _require_request_snapshot_epochs_current(request_snapshot, payload.session_id)
            history, last_user_msg = _build_chat_history(msgs)
            if image_parts:
                text_part = last_user_msg if isinstance(last_user_msg, str) else str(last_user_msg)
                last_user_msg = [{"type": "text", "text": text_part}] + image_parts

            # ── system_prompt 构建（pre-RAG 已移除，Agent 自行决定是否检索）──
            system_prompt = _build_system_prompt(
                payload, attachment_texts, "", long_term_memory=long_term_memory,
            )

            # ── LLM client（fallback 路径使用）──
            client = make_client(
                api_key=api_key, base_url=base_url, model=model,
                temperature=payload.temperature, max_tokens=payload.max_tokens,
            )

            # === Agent 主路径（spec §2.2 / §10.1）===
            # 所有请求默认走 Agent；Agent 不可用时（langgraph 未装/模型不支持 tools）
            # 走下方纯 LLM 流式 fallback（不检索知识库）。
            if _AGENT_PATH_AVAILABLE and _should_use_agent(payload, model):
                from src.agent.exceptions import (
                    AgentStreamIdleTimeoutError,
                    AgentTimeoutError,
                    ContextLimitError,
                    ModelEmptyResponseError,
                )

                creds = {"api_key": api_key, "base_url": base_url, "model": model}
                agent_text_seen = False
                agent_saw_confirmation = False
                agent_saw_error = False
                agent_terminal: dict[str, Any] | None = None
                try:
                    assembled_history = None
                    if payload.history_mode == "server":
                        from app.api.context_history import assemble_server_history

                        assembled_history = assemble_server_history(
                            payload.session_id,
                            user,
                            payload.messages,
                            normalize_content=lambda content: _isolate_images_in_content(
                                content, "user",
                            ),
                        )
                        if assembled_history is None:
                            yield sse_event("error", {
                                "code": ChatErrorCode.HISTORY_UNAVAILABLE,
                                "message": "无法确认服务端历史，已停止本次请求；原有历史未被替换。",
                            })
                            yield sse_event("done", {"success": False, "completed": False})
                            return
                    async for sse in _run_agent_stream(
                        payload, creds, long_term_memory=long_term_memory,
                        request_snapshot=request_snapshot,
                        authenticated_user=user,
                        assembled_history=assembled_history,
                    ):
                        for frame in _sse_frames(sse):
                            event = _sse_frame_payload(frame)
                            event_type = event.get("type") if event else None
                            if event_type == "done":
                                agent_terminal = event
                                continue
                            if event_type == "text" and isinstance(event.get("content"), str):
                                agent_text_seen = agent_text_seen or bool(event["content"].strip())
                            elif event_type == "tool_confirm_required":
                                agent_saw_confirmation = True
                            elif event_type == "error":
                                agent_saw_error = True
                            yield f"{frame}\n\n"

                    if agent_saw_error or (agent_terminal and agent_terminal.get("success") is False):
                        yield sse_event("done", {"success": False, "completed": False})
                        return
                    if agent_saw_confirmation:
                        yield sse_event("done", {
                            "success": True,
                            "completed": False,
                            "awaiting_confirmation": True,
                            "usage": None,
                        })
                        return
                    if not agent_text_seen:
                        raise ModelEmptyResponseError(
                            "Agent stream ended without non-whitespace answer text"
                        )
                    yield sse_event("done", {
                        "success": True,
                        "completed": True,
                        "usage": (agent_terminal or {}).get("usage"),
                    })
                    return
                except Exception as agent_exc:
                    if isinstance(agent_exc, _RequestSnapshotEpochError):
                        raise
                    if isinstance(agent_exc, ContextLimitError):
                        logger.warning(
                            "context_limit_exceeded tokens=%s limit=%s; stopping request",
                            agent_exc.cumulative,
                            agent_exc.limit,
                        )
                        effective_window = None
                        try:
                            from src.agent.context_guard import resolve_context_budget

                            effective_window = resolve_context_budget(
                                model,
                                _session_context_window(payload.session_id),
                                payload.max_tokens,
                            ).effective_window
                        except Exception:
                            effective_window = None
                        yield sse_event("error", _context_limit_payload(
                            agent_exc, model=model, effective_window=effective_window,
                        ))
                        yield sse_event("done", {"success": False, "completed": False})
                        return
                    if isinstance(agent_exc, _WriterLifecycleUnavailableError):
                        cause = agent_exc.__cause__
                        cause_type = (
                            type(cause).__name__
                            if cause is not None
                            else type(agent_exc).__name__
                        )
                        logger.error(
                            "Agent writer lifecycle unavailable (%s); request stopped before graph execution",
                            cause_type,
                        )
                        error_code = ChatErrorCode.INTERNAL_ERROR
                        error_message = "Agent 安全写入保护不可用，本轮请求已停止"
                        error_detail = "无法建立会话写入保护；本轮未启动 Agent。"
                        yield sse_event("error", {
                            "code": error_code,
                            "message": error_message,
                            "detail": error_detail,
                        })
                        yield sse_event("done", {"success": False, "completed": False})
                        return
                    from src.agent.compact.writer_lifecycle import WriterQuarantinedError

                    if isinstance(agent_exc, WriterQuarantinedError):
                        logger.warning(
                            "agent writer quarantined; request stopped before graph execution"
                        )
                        yield sse_event("error", {
                            "code": "AGENT_WRITER_QUARANTINED",
                            "retryable": False,
                            "message": (
                                "该会话的写入保护已隔离，本轮请求未启动。"
                                "请开启新会话后重试。"
                            ),
                            "detail": (
                                "上一次 Agent 执行中断，写入状态无法确认；"
                                "服务重启也会清除隔离，其他会话不受影响。"
                            ),
                        })
                        yield sse_event("done", {"success": False, "completed": False})
                        return
                    known_empty_response = _is_known_upstream_zero_output(agent_exc)
                    if known_empty_response:
                        logger.warning("Agent upstream reported the known zero-output signature")
                    else:
                        logger.error("Agent path failed: %s", agent_exc)
                    if not agent_saw_error:
                        if isinstance(agent_exc, ModelEmptyResponseError) or known_empty_response:
                            error_code = ChatErrorCode.MODEL_EMPTY_RESPONSE
                            error_message = "模型未返回可见回答"
                            error_detail = (
                                "上游明确报告零输出 token；已保留已有正文，本轮未自动重试。"
                                if known_empty_response
                                else "流已结束，但没有非空回答文本。未自动重试。"
                            )
                        elif isinstance(agent_exc, AgentStreamIdleTimeoutError):
                            error_code = ChatErrorCode.TIMEOUT
                            error_message = "模型生成流长时间没有进展"
                            error_detail = sanitize_error(str(agent_exc))
                        elif isinstance(agent_exc, AgentTimeoutError):
                            error_code = ChatErrorCode.TIMEOUT
                            error_message = "工具调用超时"
                            error_detail = sanitize_error(str(agent_exc))
                        else:
                            error_code = ChatErrorCode.INTERNAL_ERROR
                            error_message = "Agent 调用失败"
                            error_detail = sanitize_error(str(agent_exc))
                        yield sse_event("error", {
                            "code": error_code,
                            "message": error_message,
                            "detail": error_detail,
                        })
                    yield sse_event("done", {"success": False, "completed": False})
                    return

            # === Fallback: 纯 LLM 流式（Agent 不可用，不检索知识库）===
            try:
                _require_request_snapshot_epochs_current(request_snapshot, payload.session_id)
                usage_data = None
                full_response_text = ""  # Accumulated response for fallback token estimation
                # 使用 queue 包装 LLM stream，支持 idle timeout + heartbeat
                IDLE_TIMEOUT = 300  # 累计 5 分钟无数据断开
                HEARTBEAT_INTERVAL = 15  # 每 15s 发心跳，防止代理/浏览器断连
                last_event_time = time.monotonic()

                queue = _asyncio.Queue()

                async def _llm_worker(q):
                    """后台任务：消费 LLM stream 并推入队列。"""
                    try:
                        _require_request_snapshot_epochs_current(request_snapshot, payload.session_id)
                        async for chunk in client.chat_stream(
                            user_message=last_user_msg, system_prompt=system_prompt,
                            history=history if len(history) > 0 else None,
                            api_key=api_key, base_url=base_url, model=model, provider=provider,
                        ):
                            await q.put(chunk)
                    except _asyncio.CancelledError:
                        logger.debug("SSE task cancelled")
                        pass  # 主协程取消，静默退出
                    except Exception as e:
                        logger.error(f"LLM stream error: {e}")
                        await q.put(e)
                    finally:
                        await q.put(None)  # 哨兵：标记结束

                worker_task = _asyncio.create_task(_llm_worker(queue))

                try:
                    while True:
                        try:
                            chunk = await _asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                        except _asyncio.TimeoutError:
                            _require_request_snapshot_epochs_current(request_snapshot, payload.session_id)
                            if time.monotonic() - last_event_time >= IDLE_TIMEOUT:
                                logger.error("LLM 响应超时 (5min idle)")
                                yield sse_event("error", {"code": ChatErrorCode.TIMEOUT, "message": "LLM 响应超时", "detail": "5 分钟内无新数据，连接已断开"})
                                yield sse_event("done", {"success": False, "completed": False})
                                return
                            yield sse_event("heartbeat", {})
                            continue

                        _require_request_snapshot_epochs_current(request_snapshot, payload.session_id)
                        last_event_time = time.monotonic()

                        if chunk is None:
                            # 流正常结束
                            break
                        if isinstance(chunk, Exception):
                            raise chunk

                        if chunk.type == "thinking":
                            yield sse_event("thinking", {"content": chunk.content, "source": "reasoning"})
                        elif chunk.type == "usage":
                            usage_data = chunk.usage
                            logger.info(f"LLM usage: {usage_data}")
                            # Record token usage to database
                            if usage_data:
                                _record_token_usage(
                                    model=model, provider=provider or "",
                                    session_id=payload.session_id, usage_data=usage_data,
                                )
                        else:
                            full_response_text += chunk.content or ""
                            yield sse_event("text", {"content": chunk.content})

                    if not full_response_text.strip():
                        raise ModelEmptyResponseError(
                            "LLM stream ended without non-whitespace answer text"
                        )

                    # Fallback: if provider didn't return usage in stream, estimate it
                    # (some providers like certain Ollama setups don't support stream_options)
                    if not usage_data:
                        try:
                            from src.llm.client import LLMClient as _LLMClient
                            # Estimate input tokens from messages
                            input_tokens = 0
                            for m in msgs:
                                input_tokens += _LLMClient._estimate_tokens(m.content)
                            if system_prompt:
                                input_tokens += _LLMClient._estimate_tokens(system_prompt)
                            # Estimate output tokens from accumulated response
                            output_tokens = _LLMClient._estimate_tokens(full_response_text)
                            usage_data = {
                                "prompt_tokens": input_tokens,
                                "completion_tokens": output_tokens,
                                "total_tokens": input_tokens + output_tokens,
                            }
                            logger.info(f"LLM usage (estimated): {usage_data}")
                            _record_token_usage(
                                model=model, provider=provider or "",
                                session_id=payload.session_id, usage_data=usage_data,
                            )
                        except Exception as est_err:
                            logger.warning(f"Token estimation failed: {est_err}")

                    # 正常结束，发送 done
                    _require_request_snapshot_epochs_current(request_snapshot, payload.session_id)
                    done_payload: dict = {"success": True, "completed": True}
                    if usage_data:
                        done_payload["usage"] = usage_data
                    yield sse_event("done", done_payload)
                    return

                finally:
                    worker_task.cancel()
                    try:
                        await worker_task
                    except _asyncio.CancelledError:
                        pass

            except Exception as e:
                if isinstance(e, _RequestSnapshotEpochError):
                    raise
                _emsg = str(e)
                _emsg_lower = _emsg.lower()
                known_empty_response = _is_known_upstream_zero_output(e)
                if isinstance(e, ModelEmptyResponseError) or known_empty_response:
                    _ecode = ChatErrorCode.MODEL_EMPTY_RESPONSE
                    _emsg_display = "模型未返回可见回答"
                    _edetail = (
                        "上游明确报告零输出 token；已保留已有正文，本轮未自动重试。"
                        if known_empty_response
                        else "流已结束，但没有非空回答文本。未自动重试。"
                    )
                elif (OpenAINotFoundError is not None and isinstance(e, OpenAINotFoundError)) or \
                   "model_not_found" in _emsg_lower or \
                   "modelerror" in _emsg_lower or \
                   ("model" in _emsg_lower and ("not found" in _emsg_lower or "does not exist" in _emsg_lower or "is not supported" in _emsg_lower)):
                    _ecode: ChatErrorCode = ChatErrorCode.MODEL_NOT_FOUND
                    _emsg_display = f"模型不存在：{model}"
                    _edetail = _emsg
                elif isinstance(e, LLMError):
                    if "API Key" in _emsg or "AuthenticationError" in _emsg:
                        _ecode = ChatErrorCode.AUTH_FAILED
                        _emsg_display = "API Key 无效"
                        _edetail = "请检查设置中的 API Key 是否正确"
                    elif "频率" in _emsg or "rate" in _emsg.lower():
                        _ecode = ChatErrorCode.RATE_LIMITED
                        _emsg_display = "请求频率超限"
                        _edetail = "请稍后再试，或降低并发请求"
                    elif "超时" in _emsg or "timeout" in _emsg.lower():
                        _ecode = ChatErrorCode.TIMEOUT
                        _emsg_display = "LLM 响应超时"
                        _edetail = "LLM 流在限定时间内未能继续，请稍后重试"
                    else:
                        _ecode = ChatErrorCode.INTERNAL_ERROR
                        _emsg_display = "LLM 调用失败"
                        _edetail = _emsg
                else:
                    _ecode = ChatErrorCode.INTERNAL_ERROR
                    _emsg_display = "LLM 调用失败"
                    _edetail = sanitize_error(_emsg)
                logger.error(f"LLM call failed: {_ecode} - {_emsg_display}")
                yield sse_event("error", {"code": _ecode, "message": _emsg_display, "detail": _edetail})
                yield sse_event("done", {"success": False, "completed": False})
                return

        except _RequestSnapshotEpochError:
            _clear_pending_request_snapshot(payload.session_id)
            yield sse_event("error", {
                "code": ChatErrorCode.INTERNAL_ERROR,
                "message": "请求上下文已变化或无法验证，本轮请求已停止，请重新发起。",
            })
            yield sse_event("done", {"success": False, "completed": False})
        except _asyncio.CancelledError:
            logger.info("SSE 流被客户端取消（CancelledError）")
            # 捕获后重新 raise，让 FastAPI 正常终止
            raise

        finally:
            logger.debug("SSE 流结束，资源已释放")
    async def persisted_event_generator():
        async for frame in run_events.stream_with_persistence(
            event_generator(),
            store=run_store,
            run_id=run_id,
            principal_id=run_principal_id,
            session_id=run_session_id,
            request_snapshot=run_request_snapshot,
        ):
            yield frame

    return StreamingResponse(
        persisted_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


# ═══════════════════════════════════════════
# POST /api/agent-sandbox/resume — HITL resume (v2-T3)
# ═══════════════════════════════════════════


@router.get("/sessions/{session_id}/context-events/{batch_id}/{sequence}")
def read_compacted_context_event(
    request: Request, session_id: str, batch_id: str, sequence: int, request_id: str, content_sha256: str,
    byte_budget: int = Query(default=4096, ge=1, le=32768),
    user: dict = Depends(current_user),
):
    """Recover one verified-scope historical record, never execute its content."""
    from app.api.context_events import ContextEventScope, EventReference, recover_context_event
    authorization = request.headers.get("authorization", "")
    if ("token" in request.query_params or not authorization.lower().startswith("bearer ")
            or not authorization.split(" ", 1)[1].strip()):
        raise HTTPException(status_code=403, detail={"success": False, "error": {
            "code": "CONTEXT_EVENT_HEADER_AUTH_REQUIRED", "message": "Use Authorization Bearer header, not URL tokens",
        }})
    principal = compaction_principal(user)
    if principal is None:
        raise HTTPException(status_code=403, detail={"success": False, "error": {
            "code": "CONTEXT_EVENT_ACCESS_DENIED", "message": "Context event access denied",
        }})
    epoch = read_session_delete_epoch(session_id)
    reference = EventReference(batch_id, session_id, request_id, sequence, 1, content_sha256)
    scope = ContextEventScope(session_id, request_id, principal, epoch)
    event = recover_context_event(scope, reference, authorized=True, byte_budget=byte_budget)
    if event is None:
        raise HTTPException(status_code=404, detail={"success": False, "error": {
            "code": "CONTEXT_EVENT_UNAVAILABLE", "message": "Context event unavailable",
        }})
    return {"success": True, "data": {"event": event, "untrusted_history": True}}


class ResumeRequest(BaseModel):
    """Carries the original ChatRequest + user's HITL decision."""
    payload: ChatRequest
    decision: Literal["allow", "deny", "stop"]
    call_id: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    run_id: StrictStr | None = Field(default=None, min_length=1, max_length=128)


@router.post("/agent-sandbox/resume")
async def resume_agent(req: ResumeRequest, request: Request, user: dict = Depends(current_user)):
    """Resume a paused Agent after user HITL decision."""
    if not _AGENT_PATH_AVAILABLE:
        return {"error": "Agent path unavailable"}
    session_id = _session_memory_key(req.payload.session_id)
    run_store = _resolve_run_store()
    if req.run_id is not None:
        _require_resume_run(
            run_store, req.run_id, session_id, req.decision, req.call_id,
        )
    try:
        pinned_session_epoch, pinned_memory_epoch = _capture_request_snapshot_epochs(
            req.payload.session_id,
        )
    except _RequestSnapshotEpochError as exc:
        _clear_pending_request_snapshot(session_id)
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_REQUEST_SNAPSHOT_UNAVAILABLE",
                    "message": "原请求快照无法验证，请重新发送本轮请求。",
                },
            },
        ) from exc
    request_snapshot = _get_pending_request_snapshot(session_id)
    if (
        request_snapshot is None
        or request_snapshot.session_delete_epoch != pinned_session_epoch
        or request_snapshot.memory_clear_epoch != pinned_memory_epoch
    ):
        _clear_pending_request_snapshot(session_id)
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_REQUEST_SNAPSHOT_UNAVAILABLE",
                    "message": "原请求快照已不可用，请重新发送本轮请求。",
                },
            },
        )
    current_principal_id = _verified_principal_id(user)
    if current_principal_id != request_snapshot.principal_id:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_REQUEST_PRINCIPAL_MISMATCH",
                    "message": "当前身份与暂停请求不匹配，无法继续恢复。",
                },
            },
        )
    _require_resume_snapshot_current(request_snapshot, req.payload.session_id)
    if (
        req.decision == "allow"
        and (type(req.call_id) is not str or not req.call_id)
        and request_snapshot.pending_call_ids
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "HITL_CONFIRMATION_STALE",
                    "message": "确认已失效，请重新发送请求。",
                },
            },
        )
    effective_payload = _payload_with_request_snapshot(req.payload, request_snapshot)
    effective_req = req.model_copy(update={"payload": effective_payload})
    creds = _resolve_creds(effective_payload, request)
    _require_resume_snapshot_current(request_snapshot, req.payload.session_id)
    from src.agent.context_guard import resolve_context_budget

    context_budget = resolve_context_budget(
        creds.get("model"),
        _session_context_window(req.payload.session_id),
        req.payload.max_tokens,
    )
    agent = await _build_agent_for_payload(
        effective_payload, creds, request_snapshot.long_term_memory,
        request_snapshot=request_snapshot,
        context_window_override=context_budget.effective_window,
    )
    _require_resume_snapshot_current(request_snapshot, req.payload.session_id)
    config = build_agent_config(session_id)
    from src.agent.compact.writer_lifecycle import WriterOwnerConflictError

    try:
        writer_binding = _build_request_writer_binding(
            agent, config, request_snapshot, req.payload.session_id,
        )
        writer_lease = _acquire_request_writer_lease(writer_binding)
    except _RequestSnapshotEpochError as exc:
        _clear_pending_request_snapshot(session_id)
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_REQUEST_SNAPSHOT_UNAVAILABLE",
                    "message": "原请求快照已失效，请重新发送本轮请求。",
                },
            },
        ) from exc
    except WriterOwnerConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_WRITER_BUSY",
                    "message": "该会话仍有请求在写入，请等待其结束后重试确认。",
                },
            },
        ) from exc
    except Exception as exc:
        logger.warning("HITL writer binding could not be established (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error": {
                    "code": "HITL_WRITER_UNAVAILABLE",
                    "message": "无法安全恢复此会话，请稍后重试。",
                },
            },
        ) from exc

    from app.api.hitl_pause import (
        has_recorded_pause,
        make_pause_receipt,
        pause_key,
        require_and_consume,
    )
    from src.agent.hitl_handler import _detect_tools_interrupt
    from src.agent.writer_bridge import WriterRequestIdentity

    pause_identity = WriterRequestIdentity(
        request_snapshot.business_session_id,
        request_snapshot.request_id,
        request_snapshot.session_delete_epoch or "",
    )
    pause_receipt_key = pause_key(
        pause_identity, pause_identity.session_delete_epoch,
    )
    if has_recorded_pause(pause_receipt_key):
        pause_scope = writer_binding.current_scope()
        pause_pending = await _detect_tools_interrupt(agent, config)
        pause_state = await agent.aget_state(config)
        try:
            require_and_consume(
                pause_receipt_key,
                make_pause_receipt(
                    pause_state, pause_pending, pause_scope.owner_key,
                ),
            )
        except Exception as exc:
            _release_request_writer_lease(writer_binding, writer_lease)
            raise HTTPException(
                status_code=409,
                detail={
                    "success": False,
                    "error": {
                        "code": "HITL_CONFIRMATION_STALE",
                        "message": "确认已失效或已被使用，请重新发送本轮请求。",
                    },
                },
            ) from exc

    lease_transferred = False
    lease_release_state = {"released": False}
    try:
        authorization = getattr(request_snapshot.tool_context, "mcp_authorization", None)
        if authorization is not None:
            if req.decision == "stop":
                authorization.revoke()
            else:
                from src.agent.hitl_handler import _detect_tools_interrupt
                pending = await _detect_tools_interrupt(agent, config)
                _require_resume_snapshot_current(request_snapshot, req.payload.session_id)
                writer_binding.registry.assert_scope(
                    writer_lease, writer_binding.current_scope(),
                )
                if not authorization.validate_confirmation(req.call_id, pending):
                    raise HTTPException(status_code=409, detail={"error": {"code": "MCP_CONFIRMATION_STALE", "message": "确认已失效，请重新发送请求。"}})
                try:
                    authorization.resolve_confirmation(req.call_id, req.decision)
                except ValueError:
                    raise HTTPException(status_code=409, detail={"error": {"code": "MCP_CONFIRMATION_STALE", "message": "确认已处理，请勿重复提交。"}}) from None
        elif req.decision == "allow":
            from src.agent.hitl_handler import (
                _detect_tools_interrupt,
                _require_single_pending_confirmation_call,
            )

            try:
                pending = await _detect_tools_interrupt(agent, config)
                if req.call_id is not None or pending:
                    _require_single_pending_confirmation_call(req.call_id, pending)
            except Exception as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "HITL_CONFIRMATION_STALE",
                            "message": "确认已失效，请重新发送请求。",
                        },
                    },
                ) from exc
            _require_resume_snapshot_current(request_snapshot, req.payload.session_id)
            writer_binding.registry.assert_scope(
                writer_lease, writer_binding.current_scope(),
            )
        call_counter: Counter = Counter()
        model = creds.get("model", "")
        _require_resume_snapshot_current(request_snapshot, req.payload.session_id)
        from starlette.background import BackgroundTask

        if req.run_id is not None:
            _consume_resume_approvals(
                run_store, req.run_id, req.decision, req.call_id,
            )
            try:
                run_store.update_run_status(
                    req.run_id, run_events.RUN_STATUS_RUNNING,
                    error_code=None, error_message=None,
                )
            except Exception:
                logger.warning(
                    "Run resume status update failed run=%s", req.run_id, exc_info=True,
                )

        resume_stream = _resume_event_generator(
            agent, config, effective_req, call_counter, model,
            request_snapshot.long_term_memory, request_snapshot,
            writer_binding, writer_lease, lease_release_state,
            authenticated_user=user,
        )
        if req.run_id is not None:
            resume_stream = run_events.stream_with_persistence(
                resume_stream,
                store=run_store,
                run_id=req.run_id,
                principal_id=request_snapshot.principal_id,
                session_id=session_id,
                emit_started=False,
            )
        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
        if req.run_id is not None:
            headers["X-Run-Id"] = req.run_id
        response = StreamingResponse(
            resume_stream,
            media_type="text/event-stream",
            headers=headers,
            background=BackgroundTask(
                _release_request_writer_lease,
                writer_binding,
                writer_lease,
                lease_release_state,
            ),
        )
        lease_transferred = True
        return response
    finally:
        if not lease_transferred:
            _release_request_writer_lease(writer_binding, writer_lease)


@dataclass
class ResumeContext:
    """Context for resuming an agent after HITL confirmation.

    Bundles the 5 _resume_event_generator params into a single object so the
    resume flow doesn't have to thread agent/config/req/counter/model through
    every helper signature (P2-I-1 parameter encapsulation).
    """
    agent: Any
    config: dict
    req: "ResumeRequest"
    call_counter: Counter
    model: str
    long_term_memory: str = ""
    request_snapshot: AgentRequestSnapshot | None = None
    writer_binding: Any = field(default=None, repr=False)
    writer_lease: Any = field(default=None, repr=False)
    lease_release_state: dict[str, bool] | None = field(default=None, repr=False)
    authenticated_user: dict | None = field(default=None, repr=False, compare=False)
    claim: Any = field(default=None, repr=False, compare=False)


async def _resume_event_generator_from_ctx(ctx: ResumeContext):
    """Yield SSE from the resume handler and ensure only one terminal event.

    Reads all inputs from the ResumeContext dataclass. New call sites should
    build a ResumeContext and call this directly.
    """
    saw_confirmation = False
    try:
        from src.agent.hitl_handler import resume_agent_after_user
        request_snapshot = ctx.request_snapshot
        if request_snapshot is None:
            raise _RequestSnapshotEpochError("resume request snapshot is missing")
        _require_request_snapshot_epochs_current(
            request_snapshot, ctx.req.payload.session_id,
        )
        terminal_sent = False
        async for sse in _resume_in_context(ctx, request_snapshot, resume_agent_after_user):
            _require_request_snapshot_epochs_current(
                request_snapshot, ctx.req.payload.session_id,
            )
            if ctx.writer_binding is not None and ctx.writer_lease is not None:
                ctx.writer_binding.registry.assert_scope(
                    ctx.writer_lease, ctx.writer_binding.current_scope(),
                )
            if _sse_event_type(sse) == "tool_confirm_required":
                _store_pending_request_snapshot(
                    ctx.req.payload.session_id, request_snapshot,
                )
                saw_confirmation = True
            yield sse
            _require_request_snapshot_epochs_current(
                request_snapshot, ctx.req.payload.session_id,
            )
            if ctx.writer_binding is not None and ctx.writer_lease is not None:
                ctx.writer_binding.registry.assert_scope(
                    ctx.writer_lease, ctx.writer_binding.current_scope(),
                )
            if _is_done_sse_event(sse):
                terminal_sent = True
                break
        if not terminal_sent:
            if saw_confirmation:
                yield sse_event("done", {
                    "success": True,
                    "completed": False,
                    "awaiting_confirmation": True,
                    "usage": None,
                })
            else:
                yield sse_event("done", {"success": True, "usage": None})
    except Exception as exc:
        _clear_pending_request_snapshot(ctx.req.payload.session_id)
        yield _resume_error_event(exc, model=ctx.model)
        yield sse_event("done", {"success": False, "completed": False})
    else:
        if not saw_confirmation:
            _clear_pending_request_snapshot(ctx.req.payload.session_id)
    finally:
        if ctx.writer_binding is not None and ctx.writer_lease is not None:
            _release_request_writer_lease(
                ctx.writer_binding, ctx.writer_lease, ctx.lease_release_state,
            )


async def _resume_in_context(ctx: ResumeContext, snapshot: AgentRequestSnapshot, handler):
    from src.agent.request_context import activate_tool_context
    _require_request_snapshot_epochs_current(snapshot, ctx.req.payload.session_id)
    if ctx.writer_binding is not None and ctx.writer_lease is not None:
        ctx.writer_binding.registry.assert_scope(
            ctx.writer_lease, ctx.writer_binding.current_scope(),
        )
    options = {
        "model": ctx.model,
        "requested_max_tokens": ctx.req.payload.max_tokens,
    }
    if getattr(snapshot.tool_context, "mcp_authorization", None) is not None:
        options.update({
            "confirmed_call_id": ctx.req.call_id,
            "confirmation_resolved": True,
        })
    elif ctx.req.decision == "allow":
        options["confirmed_call_id"] = ctx.req.call_id
    from src.agent.sse_adapter import bind_compaction_source_sidecar
    from app.api.context_history import build_graph_message_source_ids

    try:
        observed = await ctx.agent.aget_state(ctx.config)
    except Exception:
        observed = None
    graph_messages = tuple(
        getattr(observed, "values", {}).get("messages", ()) or ()
    )
    resume_source_message_ids = build_graph_message_source_ids(
        snapshot.business_session_id,
        ctx.authenticated_user,
        graph_messages,
    ) if graph_messages else None
    sidecar_scope = (
        bind_compaction_source_sidecar(resume_source_message_ids, graph_messages)
        if resume_source_message_ids is not None
        else nullcontext()
    )
    install_grant = getattr(
        getattr(snapshot, "skill_install_tool", None), "_install_grant", None,
    )
    install_scope = nullcontext()
    if install_grant is not None:
        from src.agent.tools.groups.skills.install_tool import activate_install_binding

        install_scope = activate_install_binding(install_grant)
    with activate_tool_context(snapshot.tool_context), install_scope, sidecar_scope:
        with _request_writer_context(ctx.writer_binding, ctx.writer_lease, ctx.model, ctx.config, snapshot):
            async for event in handler(
                ctx.agent, ctx.config, ctx.req.decision, ctx.call_counter,
                ctx.req.payload.permission_mode, **options,
            ):
                _require_request_snapshot_epochs_current(snapshot, ctx.req.payload.session_id)
                if ctx.writer_binding is not None and ctx.writer_lease is not None:
                    ctx.writer_binding.registry.assert_scope(
                        ctx.writer_lease, ctx.writer_binding.current_scope(),
                    )
                yield event
                _require_request_snapshot_epochs_current(snapshot, ctx.req.payload.session_id)
                if ctx.writer_binding is not None and ctx.writer_lease is not None:
                    ctx.writer_binding.registry.assert_scope(
                        ctx.writer_lease, ctx.writer_binding.current_scope(),
                    )


def _is_done_sse_event(sse: str) -> bool:
    """Return whether one serialized SSE frame is already a terminal event."""
    for line in sse.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and payload.get("type") == "done"
    return False


async def _resume_event_generator(
    agent, config, req, call_counter, model, long_term_memory: str = "",
    request_snapshot: AgentRequestSnapshot | None = None,
    writer_binding: Any = None, writer_lease: Any = None,
    lease_release_state: dict[str, bool] | None = None,
    *,
    authenticated_user: dict | None = None,
):
    """Backward-compat shim around _resume_event_generator_from_ctx (P2-I-1).

    Preserves the original 5-arg signature so resume_agent keeps working.
    New code should build a ResumeContext and call _resume_event_generator_from_ctx.
    """
    ctx = ResumeContext(
        agent=agent, config=config, req=req,
        call_counter=call_counter, model=model,
        long_term_memory=long_term_memory,
        request_snapshot=request_snapshot,
        writer_binding=writer_binding,
        writer_lease=writer_lease,
        lease_release_state=lease_release_state,
        authenticated_user=authenticated_user,
    )
    async for sse in _resume_event_generator_from_ctx(ctx):
        yield sse


def _context_limit_payload(
    exc: Any,
    *,
    model: str | None = None,
    effective_window: int | None = None,
) -> dict:
    """Build a specific, non-retryable CONTEXT_LIMIT error payload."""
    model_label = f"模型 {model} 的" if model else "当前模型的"
    window_detail = (
        f"，模型窗口 {effective_window} tokens" if effective_window else ""
    )
    return {
        "code": ChatErrorCode.CONTEXT_LIMIT,
        "retryable": False,
        "message": (
            f"上下文超过{model_label}可处理上限（{exc.limit} tokens），本轮已停止。"
            "直接重试会再次超限；请开启新会话，或缩小扫描/对话范围后重新提问。"
        ),
        "detail": (
            f"本轮累计估算 {exc.cumulative} tokens{window_detail}；"
            "已执行的工具输出也计入上下文。"
        ),
    }


def _resume_error_event(exc: Exception, *, model: str | None = None) -> str:
    """Build the error SSE event for a failed resume."""
    from src.agent.exceptions import ContextLimitError

    if isinstance(exc, _RequestSnapshotEpochError):
        return sse_event("error", {
            "code": "HITL_REQUEST_SNAPSHOT_UNAVAILABLE",
            "message": "原请求快照已失效，请重新发送本轮请求。",
        })
    if isinstance(exc, ContextLimitError):
        logger.warning(
            "context_limit_exceeded tokens=%s limit=%s during resume; stopping request",
            exc.cumulative,
            exc.limit,
        )
        return sse_event("error", _context_limit_payload(exc, model=model))
    logger.exception("Agent resume failed")
    return sse_event("error", {"code": "AGENT_RESUME_FAILED", "message": str(exc)[:200]})


def _resolve_creds(payload: ChatRequest, request: Request) -> dict:
    """Extract api_key/base_url/model from headers + payload + settings."""
    return resolve_credentials(payload, request)


# ═══════════════════════════════════════════
# POST /api/models — 拉取模型列表
# ═══════════════════════════════════════════

@router.post("/models")
async def list_models(payload: ModelsRequest, request: Request, user: dict = Depends(current_user_optional)):
    """根据用户填写的 provider 配置获取可用模型列表。

    使用可选鉴权：验证新 provider 时浏览器可能还没有 session_token（鸡生蛋问题），
    实际的 API Key 通过 X-API-Key 请求头由 resolve_credentials 解析，端点自身
    已有 `if not api_key: return AUTH_FAILED` 防御，无需强制鉴权。
    """
    creds = resolve_credentials(payload, request)
    api_key = creds["api_key"]
    base_url = creds["base_url"]
    provider = creds["provider"]

    if not api_key:
        return {
            "success": False,
            "error": {"code": "AUTH_FAILED", "message": "未提供 API Key", "details": None},
        }

    client = LLMClient(api_key=api_key, base_url=base_url)
    try:
        models = await client.list_models(api_key=api_key, base_url=base_url, provider=provider)
        return {"success": True, "data": {"models": models or []}}
    except LLMError as e:
        return {
            "success": False,
            "error": {"code": "MODEL_FETCH_FAILED", "message": str(e), "details": None},
        }
    except Exception as e:
        logger.exception("模型列表获取异常")
        return {
            "success": False,
            "error": {
                "code": "MODEL_FETCH_FAILED",
                "message": f"模型列表获取失败: {sanitize_error(str(e))}",
                "details": None,
            },
        }


# ═══════════════════════════════════════════
# GET /api/token-usage/stats — Token 用量统计
# ═══════════════════════════════════════════

@router.get("/token-usage/stats")
def token_usage_stats(
    days: int = 30,
    session_id: Optional[str] = None,
    user: dict = Depends(current_user_optional),
):
    """返回近 N 天的 Token 用量统计。

    返回:
        daily: 按天聚合 [{date, input, output, total}]
        by_model: 按模型分组 [{model, input, output, total, calls}]
        summary: {total_input, total_output, total_tokens, days}
    """
    days = max(1, min(days, 365))  # Clamp to valid range
    import datetime
    from sqlalchemy import func

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    try:
        with get_db_ctx() as db:
            # Daily aggregation
            daily_query = db.query(
                func.date(TokenUsage.created_at).label("date"),
                func.sum(TokenUsage.prompt_tokens).label("input"),
                func.sum(TokenUsage.completion_tokens).label("output"),
                func.sum(TokenUsage.total_tokens).label("total"),
            ).filter(TokenUsage.created_at >= cutoff)
            if session_id is not None:
                daily_query = daily_query.filter(TokenUsage.session_id == session_id)
            daily_rows = daily_query.group_by(
                func.date(TokenUsage.created_at)
            ).order_by(
                func.date(TokenUsage.created_at)
            ).all()

            daily = [
                {
                    "date": str(row.date),
                    "input": int(row.input or 0),
                    "output": int(row.output or 0),
                    "total": int(row.total or 0),
                }
                for row in daily_rows
            ]

            # By model aggregation
            model_query = db.query(
                TokenUsage.model,
                func.sum(TokenUsage.prompt_tokens).label("input"),
                func.sum(TokenUsage.completion_tokens).label("output"),
                func.sum(TokenUsage.total_tokens).label("total"),
                func.count(TokenUsage.id).label("calls"),
            ).filter(TokenUsage.created_at >= cutoff)
            if session_id is not None:
                model_query = model_query.filter(TokenUsage.session_id == session_id)
            model_rows = model_query.group_by(
                TokenUsage.model
            ).order_by(
                func.sum(TokenUsage.total_tokens).desc()
            ).all()

            by_model = [
                {
                    "model": row.model,
                    "input": int(row.input or 0),
                    "output": int(row.output or 0),
                    "total": int(row.total or 0),
                    "calls": int(row.calls or 0),
                }
                for row in model_rows
            ]

            # Summary
            total_input = sum(d["input"] for d in daily)
            total_output = sum(d["output"] for d in daily)
            total_tokens = sum(d["total"] for d in daily)

            return {
                "success": True,
                "data": {
                    "daily": daily,
                    "by_model": by_model,
                    "summary": {
                        "total_input": total_input,
                        "total_output": total_output,
                        "total_tokens": total_tokens,
                        "days": days,
                    },
                },
            }
    except Exception as e:
        logger.exception("Token 用量统计查询失败")
        return {
            "success": False,
            "error": {
                "code": "STATS_FAILED",
                "message": f"统计查询失败: {sanitize_error(str(e))}",
                "details": None,
            },
        }
