"""
Chat 路由 — /api/chat SSE + /api/models

迁移自 routes.py，共享工具见 common.py。
"""

import logging
import json
import threading
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
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
from app.api.common import get_db_ctx, make_client
from app.api.sse import sse_event
from app.api.errors import sanitize_error
from app.api.chat_helpers import (
    MAX_LONG_TERM_MEMORY_CHARS, resolve_long_term_memory,
    _record_token_usage,
    _process_attachments, _build_chat_history, _build_system_prompt,
)
from app.db.models import TokenUsage
from src.agent.prompts import MAX_TOKEN_RATIO

logger = logging.getLogger(__name__)


async def stream_agent_to_sse(
    agent: Any,
    events: Any,
    config: dict,
    call_counter: Counter,
    permission_mode: str = "bypassPermissions",
    model: str = "",
) -> AsyncIterator[str]:
    """Lazily forward to the Agent SSE adapter while keeping a patchable API symbol."""
    from src.agent.sse_adapter import stream_agent_to_sse as adapter_stream

    adapter_iterator = adapter_stream(
        agent, events, config, call_counter, permission_mode, model=model,
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
    )
    _AGENT_PATH_AVAILABLE = True
except ImportError as _agent_import_err:
    _should_use_agent = None  # type: ignore[assignment]
    _AGENT_PATH_AVAILABLE = False
    logger.debug("Agent path unavailable, will use fallback LLM stream: %s", _agent_import_err)

router = APIRouter(prefix="/api")

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


def _ensure_external_tool_snapshot(
    snapshot: AgentRequestSnapshot,
    session_id: str,
    payload: Any = None,
) -> AgentRequestSnapshot:
    """Freeze connected tool instances and approvals for this request only."""
    if snapshot.skills_mode != "off" or snapshot.mcp_snapshot_bound or snapshot.tool_context is not None:
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


def _request_snapshot_from_payload(payload: Any, memory: str) -> AgentRequestSnapshot:
    kb_ids = getattr(payload, "kb_ids", None)
    return AgentRequestSnapshot(
        long_term_memory=memory,
        permission_mode=getattr(payload, "permission_mode", "default") or "default",
        kb_ids=tuple(kb_ids) if kb_ids is not None else None,
        top_k=getattr(payload, "top_k", None),
        relevance_threshold=getattr(payload, "relevance_threshold", None),
        skills_mode=getattr(payload, "skills_mode", "off") or "off",
        skill_ids=tuple(getattr(payload, "skill_ids", None) or ()),
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
    return replace(snapshot, tool_context=context)


def _ensure_skills_runtime(snapshot: AgentRequestSnapshot) -> AgentRequestSnapshot:
    if snapshot.skills_mode == "off":
        if snapshot.skills_runtime is not None:
            raise ValueError("Skills runtime must be absent when Skills mode is off")
        return snapshot
    if snapshot.skills_runtime is not None:
        if getattr(snapshot.skills_runtime, "mode", None) != snapshot.skills_mode:
            raise ValueError("Skills runtime does not match the saved request mode")
        expected_ids = tuple(item.id for item in snapshot.skills_runtime.snapshots)
        if snapshot.skill_snapshots and tuple(item.id for item in snapshot.skill_snapshots) != expected_ids:
            raise ValueError("Skills runtime does not match the saved request snapshot")
        return replace(snapshot, skill_snapshots=tuple(snapshot.skills_runtime.snapshots))

    from src.agent.skill_runtime import build_skills_runtime
    runtime = build_skills_runtime(
        snapshot.skills_mode,
        snapshot.skill_ids if snapshot.skills_mode == "manual" else None,
    )
    return replace(
        snapshot,
        skill_snapshots=tuple(runtime.snapshots),
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
    from src.agent.skill_runtime import LoadSkillTool, READ_ONLY_SKILL_TOOLS
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
        skills_allowed_tools=frozenset(READ_ONLY_SKILL_TOOLS - disabled_tools),
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


def _store_pending_request_snapshot(
    session_id: str | None, snapshot: AgentRequestSnapshot,
) -> None:
    context = snapshot.tool_context
    if context is None:
        logger.warning("cannot store resumable Agent request without ToolContext")
        _clear_pending_request_snapshot(session_id)
        return
    try:
        from src.agent.tools.groups.retrieval.source_registry import get_context_source_registry

        registry_snapshot = get_context_source_registry(context).to_snapshot()
    except Exception:
        logger.warning("cannot store resumable Agent request with invalid source registry", exc_info=True)
        _clear_pending_request_snapshot(session_id)
        return
    snapshot = replace(snapshot, rag_source_registry_snapshot=registry_snapshot)
    now = time.monotonic()
    with _PENDING_AGENT_SNAPSHOT_LOCK:
        _prune_pending_agent_snapshots(now)
        _PENDING_AGENT_REQUESTS[_session_memory_key(session_id)] = (snapshot, now)
        _prune_pending_agent_snapshots(now)


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


def _clear_pending_request_snapshot(session_id: str | None) -> None:
    with _PENDING_AGENT_SNAPSHOT_LOCK:
        pending = _PENDING_AGENT_REQUESTS.pop(_session_memory_key(session_id), None)
        if pending is not None:
            authorization = getattr(pending[0].tool_context, "mcp_authorization", None)
            if authorization is not None:
                authorization.revoke()


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
    AUTH_FAILED = "AUTH_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_EMPTY_RESPONSE = "MODEL_EMPTY_RESPONSE"
    TIMEOUT = "TIMEOUT"
    RAG_FAILED = "RAG_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"


# ═══════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════

class ChatMessageSchema(BaseModel):
    role: str
    content: Optional[str | list[dict]] = None  # str=纯文本, list=[{type,text|image_url},...]


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
    permission_mode: str = "default"  # bypassPermissions / default / acceptEdits (spec §7.1)
    tool_keys: dict[str, str] = {}  # per-request tool credentials, e.g. {"tavily": "..."}
    skills_mode: Literal["off", "auto", "manual"] = "off"
    skill_ids: list[str] = Field(default_factory=list, max_length=3)

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

def _dicts_to_langchain_messages(dicts: list[dict]) -> list:
    """Convert agent_input message dicts to langchain Message objects."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    cls_by_role = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
    messages: list = []
    for d in dicts:
        cls = cls_by_role.get(d.get("role", "user"), HumanMessage)
        messages.append(cls(content=d.get("content", "")))
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
        dicts.append({"role": role, "content": content})
    return dicts


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


async def _run_autocompact(messages: list, creds: dict, context_window: int) -> list:
    """Build an LLM client and run autocompact_messages."""
    from src.agent.compact.autocompact import autocompact_messages
    llm_client = make_client(
        api_key=creds.get("api_key"), base_url=creds.get("base_url"), model=creds.get("model"),
    )
    return await autocompact_messages(messages, llm_client, context_window)


async def _check_and_compact_history(
    payload: ChatRequest, creds: dict, agent_input: dict,
) -> AsyncIterator[str]:
    """Pre-send: compact history when over MAX_TOKEN_RATIO of context window.

    Yields a context_compressing SSE event when compaction runs; mutates
    agent_input['messages'] in place with the compacted list. No-op (and no
    SSE) when history is within budget or context_window is unknown.
    """
    from src.agent.compact.autocompact import should_autocompact
    context_window = _session_context_window(payload.session_id)
    if not context_window:
        return
    lc_messages = _dicts_to_langchain_messages(agent_input["messages"])
    threshold = int(context_window * MAX_TOKEN_RATIO)
    if not should_autocompact(lc_messages, threshold):
        return
    yield sse_event(
        "context_compressing",
        _compact_event_payload(threshold, context_window, len(lc_messages)),
    )
    compacted = await _run_autocompact(lc_messages, creds, context_window)
    # Use identity check: autocompact returns the same list when no compaction
    # happened. A new list means compaction ran (even if count is unchanged,
    # e.g., 11 msgs → 1 summary + 10 recent — tokens decreased).
    if compacted is not lc_messages:
        agent_input["messages"] = _langchain_messages_to_dicts(compacted)


async def _run_agent_stream(
    payload: ChatRequest,
    creds: dict,
    long_term_memory: str | None = None,
    request_snapshot: AgentRequestSnapshot | None = None,
) -> AsyncIterator[str]:
    """Build the agent and yield SSE events from its stream.

    Raises on construction failure so the caller can fall back to the LLM stream.
    """
    from src.agent.agent_factory import reset_thread_checkpoint
    session_id = _session_memory_key(payload.session_id)
    resolved_memory = long_term_memory
    if resolved_memory is None:
        resolved_memory = getattr(payload, "long_term_memory", None) or ""
    snapshot = request_snapshot or _request_snapshot_from_payload(payload, resolved_memory)
    resolved_memory = snapshot.long_term_memory
    snapshot = _ensure_skills_runtime(snapshot)
    request_payload = _payload_with_request_snapshot(payload, snapshot)
    snapshot = _ensure_external_tool_snapshot(snapshot, session_id, request_payload)
    await reset_thread_checkpoint(session_id)
    _clear_pending_request_snapshot(session_id)
    snapshot, manual_events, manual_load_succeeded = await _preload_manual_skills(
        snapshot, session_id,
    )
    for event in manual_events:
        yield event
    if not manual_load_succeeded:
        raise RuntimeError("Selected skill instructions could not be loaded")
    effective_payload = _payload_with_request_snapshot(payload, snapshot)
    snapshot = _ensure_request_tool_context(snapshot, effective_payload, session_id)
    effective_payload = _payload_with_request_snapshot(payload, snapshot)
    agent = await _build_agent_for_payload(
        effective_payload, creds, resolved_memory, request_snapshot=snapshot,
    )
    config = build_agent_config(payload.session_id or "default")
    agent_input = {"messages": _build_agent_messages(effective_payload)}
    # Pre-send history check — compact if over MAX_TOKEN_RATIO of context window.
    async for sse in _check_and_compact_history(effective_payload, creds, agent_input):
        yield sse
    call_counter: Counter = Counter()
    snapshot_pending = False
    try:
        async for sse in _stream_agent_in_context(agent, agent_input, config, call_counter, effective_payload.permission_mode, creds.get("model", ""), snapshot.tool_context):
            event_type = _sse_event_type(sse)
            if event_type == "tool_confirm_required":
                _store_pending_request_snapshot(session_id, snapshot)
                snapshot_pending = True
            elif event_type == "error" and snapshot_pending:
                _clear_pending_request_snapshot(session_id)
                snapshot_pending = False
            yield sse
    except Exception:
        if snapshot_pending:
            _clear_pending_request_snapshot(session_id)
        raise


async def _stream_agent_in_context(agent, agent_input, config, counter, permission_mode, model, tool_context):
    from src.agent.request_context import activate_tool_context
    with activate_tool_context(tool_context):
        async for event in stream_agent_to_sse(agent, agent_input, config, counter, permission_mode, model=model):
            yield event


async def _build_agent_for_payload(
    payload: ChatRequest,
    creds: dict,
    long_term_memory: str | None = None,
    request_snapshot: AgentRequestSnapshot | None = None,
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
    if snapshot.skills_mode != "off":
        tools = build_tools(
            payload,
            skills_runtime=snapshot.skills_runtime,
            mcp_tools=snapshot.mcp_tools,
            tool_context=snapshot.tool_context,
        )
    elif snapshot.tool_context is not None:
        tools = build_tools(payload, mcp_tools=snapshot.mcp_tools, tool_context=snapshot.tool_context)
    else:
        tools = build_tools(payload)
    enable_hitl = (
        payload.permission_mode != "bypassPermissions"
        and snapshot.skills_mode == "off"
    ) or bool(snapshot.mcp_tools)
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
    }
    _optional_arg(kwargs, "temperature", payload.temperature)
    _optional_arg(kwargs, "max_tokens", payload.max_tokens)
    return await create_hardware_agent(**kwargs)


def _optional_arg(kwargs: dict, key: str, value) -> None:
    """Add key=value to kwargs only when value is not None (let the callee use its default)."""
    if value is not None:
        kwargs[key] = value


# ═══════════════════════════════════════════
# POST /api/chat — SSE 流式聊天
# ═══════════════════════════════════════════

@router.post("/chat")
async def chat_sse(payload: ChatRequest, request: Request, user: dict = Depends(current_user)):
    """RAG 流式聊天，严格匹配前端 SSE 事件协议。"""
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
    creds = resolve_credentials(payload, request)
    api_key = creds["api_key"]
    base_url = creds["base_url"]
    model = creds["model"]
    provider = creds["provider"]
    if payload.skills_mode != "off" and not (
        _AGENT_PATH_AVAILABLE and _should_use_agent(payload, model)
    ):
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
    request_snapshot = _request_snapshot_from_payload(payload, long_term_memory)
    if request_snapshot.skills_mode != "off":
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

    async def event_generator():
        """SSE 流式生成器，带 CancelledError 处理 + idle timeout + finally 清理。"""
        import asyncio as _asyncio
        import time
        from src.agent.exceptions import ModelEmptyResponseError

        msgs = payload.messages

        try:
            # ── 附件 + 历史构建 ──
            attachment_texts, image_parts = await _process_attachments(payload)
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
                    ModelEmptyResponseError,
                )

                creds = {"api_key": api_key, "base_url": base_url, "model": model}
                agent_text_seen = False
                agent_saw_confirmation = False
                agent_saw_error = False
                agent_terminal: dict[str, Any] | None = None
                try:
                    async for sse in _run_agent_stream(
                        payload, creds, long_term_memory=long_term_memory,
                        request_snapshot=request_snapshot,
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
                            if time.monotonic() - last_event_time >= IDLE_TIMEOUT:
                                logger.error("LLM 响应超时 (5min idle)")
                                yield sse_event("error", {"code": ChatErrorCode.TIMEOUT, "message": "LLM 响应超时", "detail": "5 分钟内无新数据，连接已断开"})
                                yield sse_event("done", {"success": False, "completed": False})
                                return
                            yield sse_event("heartbeat", {})
                            continue

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

        except _asyncio.CancelledError:
            logger.info("SSE 流被客户端取消（CancelledError）")
            # 捕获后重新 raise，让 FastAPI 正常终止
            raise

        finally:
            logger.debug("SSE 流结束，资源已释放")
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════
# POST /api/agent-sandbox/resume — HITL resume (v2-T3)
# ═══════════════════════════════════════════


class ResumeRequest(BaseModel):
    """Carries the original ChatRequest + user's HITL decision."""
    payload: ChatRequest
    decision: Literal["allow", "deny", "stop"]
    call_id: StrictStr | None = Field(default=None, min_length=1, max_length=256)


@router.post("/agent-sandbox/resume")
async def resume_agent(req: ResumeRequest, request: Request, user: dict = Depends(current_user)):
    """Resume a paused Agent after user HITL decision."""
    if not _AGENT_PATH_AVAILABLE:
        return {"error": "Agent path unavailable"}
    session_id = _session_memory_key(req.payload.session_id)
    request_snapshot = _get_pending_request_snapshot(session_id)
    if request_snapshot is None:
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
    effective_payload = _payload_with_request_snapshot(req.payload, request_snapshot)
    effective_req = req.model_copy(update={"payload": effective_payload})
    creds = _resolve_creds(effective_payload, request)
    agent = await _build_agent_for_payload(
        effective_payload, creds, request_snapshot.long_term_memory,
        request_snapshot=request_snapshot,
    )
    config = build_agent_config(session_id)
    authorization = getattr(request_snapshot.tool_context, "mcp_authorization", None)
    if authorization is not None:
        if req.decision == "stop":
            authorization.revoke()
        else:
            from src.agent.hitl_handler import _detect_tools_interrupt
            pending = await _detect_tools_interrupt(agent, config)
            if not authorization.validate_confirmation(req.call_id, pending):
                raise HTTPException(status_code=409, detail={"error": {"code": "MCP_CONFIRMATION_STALE", "message": "确认已失效，请重新发送请求。"}})
            try:
                authorization.resolve_confirmation(req.call_id, req.decision)
            except ValueError:
                raise HTTPException(status_code=409, detail={"error": {"code": "MCP_CONFIRMATION_STALE", "message": "确认已处理，请勿重复提交。"}}) from None
    call_counter: Counter = Counter()
    model = creds.get("model", "")
    return StreamingResponse(
        _resume_event_generator(
            agent, config, effective_req, call_counter, model,
            request_snapshot.long_term_memory, request_snapshot,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


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


async def _resume_event_generator_from_ctx(ctx: ResumeContext):
    """Yield SSE from the resume handler and ensure only one terminal event.

    Reads all inputs from the ResumeContext dataclass. New call sites should
    build a ResumeContext and call this directly.
    """
    saw_confirmation = False
    try:
        from src.agent.hitl_handler import resume_agent_after_user
        request_snapshot = ctx.request_snapshot or _request_snapshot_from_payload(
            ctx.req.payload, ctx.long_term_memory,
        )
        terminal_sent = False
        async for sse in _resume_in_context(ctx, request_snapshot, resume_agent_after_user):
            if _sse_event_type(sse) == "tool_confirm_required":
                _store_pending_request_snapshot(
                    ctx.req.payload.session_id, request_snapshot,
                )
                saw_confirmation = True
            yield sse
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
        yield _resume_error_event(exc)
        yield sse_event("done", {"success": False, "completed": False})
    else:
        if not saw_confirmation:
            _clear_pending_request_snapshot(ctx.req.payload.session_id)


async def _resume_in_context(ctx: ResumeContext, snapshot: AgentRequestSnapshot, handler):
    from src.agent.request_context import activate_tool_context
    options = {}
    if getattr(snapshot.tool_context, "mcp_authorization", None) is not None:
        options = {"confirmed_call_id": ctx.req.call_id, "confirmation_resolved": True}
    with activate_tool_context(snapshot.tool_context):
        async for event in handler(ctx.agent, ctx.config, ctx.req.decision, ctx.call_counter, ctx.req.payload.permission_mode, **options):
            yield event


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
    )
    async for sse in _resume_event_generator_from_ctx(ctx):
        yield sse


def _resume_error_event(exc: Exception) -> str:
    """Build the error SSE event for a failed resume."""
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
