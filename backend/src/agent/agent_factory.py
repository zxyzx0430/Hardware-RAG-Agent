"""
Hardware RAG Agent — LangChain 1.x create_agent factory.

Builds a `create_agent` instance (langchain 1.3.4+ new API) with full tool
injection, HITL interrupt_before, and TokenCounterMiddleware. Keeps
chat_routes.py free of langgraph specifics.

Stage 3 migration: create_react_agent → create_agent (system_prompt= replaces
prompt=, middleware= param enabled). HITL interrupt_before=["tools"] retained.

Spec: docs/superpowers/specs/2026-06-30-agent-react-design.md §2 (architecture),
§6 (truncation), §10 (fallback path).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from src.agent.prompts import (
    FUNCTION_CALLING_MODELS,
    MAX_RECURSION,
    build_system_prompt,
)
from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

# Stage 3 (Task 13): TokenCounterMiddleware now registered to create_agent
# via middleware=(_TOKEN_MIDDLEWARE,). The active token counting path
# remains context_guard.accumulate_tokens (called from sse_adapter
# _handle_message_chunk); middleware after_model is a parallel path.
# Stage 3 Task 12 keeps astream as active path and prepares stream_events
# v3 as future fallback (_consume_custom_events raises NotImplementedError).
from src.agent.middleware.token_counter_middleware import token_counter_middleware as _TOKEN_MIDDLEWARE

# Default top_k when payload omits it (mirrors ChatRequest default).
_DEFAULT_TOP_K: int = 5
_DEFAULT_THRESHOLD: float = 0.0

# Path to the tool enable/disable config: <backend>/data/tool_config.json
# Structure: {"disabled": ["tool_name_1", "tool_name_2"]}
_TOOL_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "tool_config.json"
)

# Tool group name constants (mirror tools/ subdirectory layout).
_TOOL_GROUP_RETRIEVAL = "retrieval"
_TOOL_GROUP_HARDWARE = "hardware"
_TOOL_GROUP_WORKBENCH = "workbench"
_TOOL_GROUP_CODE = "code"
_TOOL_GROUP_FILE_OPS = "file_ops"
_TOOL_GROUP_EXECUTION = "execution"


@dataclass
class AgentConfig:
    """Configuration for building a hardware RAG agent.

    Bundles the 7 create_hardware_agent params into a single object so call
    sites don't have to thread credentials/temperature/max_tokens/HITL flag
    through every function signature (P2-I-1 parameter encapsulation).
    """
    model: str
    api_key: str
    base_url: str
    tools: list[BaseTool]
    temperature: float = 0.7
    max_tokens: int = 4096
    enable_hitl: bool = False


# ═══════════════════════════════════════════
# Agent construction
# ═══════════════════════════════════════════

async def create_hardware_agent_from_config(config: AgentConfig) -> Any:
    """Build a compiled agent from an AgentConfig.

    Wraps the LLM with langchain ChatOpenAI (required by create_agent —
    project LLMClient is not a BaseChatModel). The prebuilt agent binds tools
    internally, so we pass the raw LLM (not a bind_tools result).

    Args:
        config: AgentConfig holding model/credentials/tools/HITL flag.

    Returns:
        CompiledStateGraph (langgraph). Call `.astream(events, config=...)` on it.
    """
    logger.info(
        "create_hardware_agent model=%s enable_hitl=%s tools_count=%s",
        config.model, config.enable_hitl, len(config.tools) if config.tools else 0,
    )
    llm = _build_llm(
        config.model, config.api_key, config.base_url,
        config.temperature, config.max_tokens,
    )
    interrupt_before = ["tools"] if config.enable_hitl else []
    try:
        from langchain.agents import create_agent
        from src.agent.exceptions import ToolContext
        agent = create_agent(
            model=llm,
            tools=config.tools,
            system_prompt=build_system_prompt(),
            middleware=(_TOKEN_MIDDLEWARE,),
            context_schema=ToolContext,
            checkpointer=await _get_checkpointer(),
            interrupt_before=interrupt_before,
        )
        logger.info(
            "hardware_agent_created model=%s hitl=%s recursion_limit=%s",
            config.model, config.enable_hitl, MAX_RECURSION,
        )
        return agent
    except ImportError as exc:
        logger.error("langchain.agents import failed: %s", exc)
        raise
    except Exception as exc:
        # bind_tools errors (model lacks function calling) surface here.
        logger.error("create_agent failed (model may not support tools): %s", exc)
        raise


async def create_hardware_agent(
    model: str,
    api_key: str,
    base_url: str,
    tools: list[BaseTool],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    enable_hitl: bool = False,
) -> Any:
    """Backward-compat shim around create_hardware_agent_from_config (P2-I-1).

    Preserves the original 7-arg signature so existing call sites
    (chat_routes /api/agent-sandbox/resume) keep working. New code should
    construct an AgentConfig and call create_hardware_agent_from_config.
    """
    config = AgentConfig(
        model=model, api_key=api_key, base_url=base_url, tools=tools,
        temperature=temperature, max_tokens=max_tokens, enable_hitl=enable_hitl,
    )
    return await create_hardware_agent_from_config(config)


# Module-level checkpointer singleton — shared across all agent instances so the
# resume API (which rebuilds the agent) can recover state via thread_id checkpoint.
_global_checkpointer: Any = None
# AsyncSqliteSaver.from_conn_string is an async context manager; we must keep the
# context-manager object alive for the process lifetime so the aiosqlite connection
# is not torn down after the first __aenter__ call.
_global_checkpointer_cm: Any = None

# Path to the SQLite checkpoint database (SqliteSaver mode).
_SQLITE_CHECKPOINT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "agent_checkpoints.sqlite"
)


async def _get_checkpointer() -> Any:
    """Return the process-wide checkpointer (InMemorySaver or AsyncSqliteSaver).

    create_agent uses astream(), which calls checkpointer.aget_tuple(). The sync
    SqliteSaver does not implement async methods, so we must use AsyncSqliteSaver
    for the astream path. Falls back to InMemorySaver on failure.
    """
    global _global_checkpointer
    if _global_checkpointer is None:
        cp_type = _read_checkpointer_type()
        if cp_type == "sqlite":
            _global_checkpointer = await _build_sqlite_checkpointer()
            saver_type = type(_global_checkpointer).__name__
            logger.info("checkpointer=%s path=%s", saver_type, _SQLITE_CHECKPOINT_PATH)
        else:
            from langgraph.checkpoint.memory import InMemorySaver
            _global_checkpointer = InMemorySaver()
            logger.info("checkpointer=InMemorySaver (agent_checkpointer_type=%s)", cp_type)
    return _global_checkpointer


def _read_checkpointer_type() -> str:
    """Read agent_checkpointer_type from settings (fail-open to 'sqlite')."""
    try:
        from src.config.settings import settings
        return getattr(settings, "agent_checkpointer_type", "sqlite") or "sqlite"
    except Exception as exc:
        logger.warning("read agent_checkpointer_type failed: %s — defaulting to sqlite", exc)
        return "sqlite"


async def _build_sqlite_checkpointer() -> Any:
    """Build an AsyncSqliteSaver for the astream path.

    create_agent's astream() calls aget_tuple(), which the sync SqliteSaver
    does not implement. AsyncSqliteSaver.from_conn_string is an async context
    manager; we enter it once and keep both the context manager and the saver
    alive for the process lifetime so the aiosqlite connection stays open.
    Falls back to InMemorySaver on failure.
    """
    global _global_checkpointer_cm
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    try:
        _SQLITE_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _global_checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(_SQLITE_CHECKPOINT_PATH))
        checkpointer = await _global_checkpointer_cm.__aenter__()
        return checkpointer
    except Exception as exc:
        logger.error("AsyncSqliteSaver init failed: %s — falling back to InMemorySaver", exc)
        _global_checkpointer_cm = None
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()


async def reset_thread_checkpoint(thread_id: str) -> None:
    """Clear stale checkpointer state for a thread to prevent INVALID_CHAT_HISTORY.

    If a prior request died mid-tool-execution (SSE disconnect / exception),
    the checkpoint holds an AIMessage(tool_calls) without a matching ToolMessage.
    LangGraph rejects this on the next request. Frontend sends full history
    each turn, so clearing stale state is safe; HITL resume is unaffected
    because it operates within the same request lifecycle.

    langgraph 1.x: AsyncSqliteSaver has an async delete_thread API; InMemorySaver
    still uses the sync storage/writes filtering fallback below.
    """
    cp = await _get_checkpointer()
    if await _try_delete_thread(cp, thread_id):
        return
    _clear_storage(cp, thread_id)
    _clear_writes(cp, thread_id)


async def _try_delete_thread(cp: Any, thread_id: str) -> bool:
    """Use AsyncSqliteSaver.adelete_thread public API when available. Returns True if used."""
    adelete = getattr(cp, "adelete_thread", None)
    if callable(adelete) and asyncio.iscoroutinefunction(adelete):
        try:
            await adelete(thread_id)
            logger.info("cleared_checkpoint_via_adelete_thread thread_id=%s", thread_id)
            return True
        except Exception as exc:
            logger.warning("adelete_thread failed: %s — falling back to storage/writes filter", exc)
            return False
    # Fallback to sync delete_thread for InMemorySaver / older savers.
    delete = getattr(cp, "delete_thread", None)
    if not callable(delete):
        return False
    try:
        delete(thread_id)
        logger.info("cleared_checkpoint_via_delete_thread thread_id=%s", thread_id)
        return True
    except Exception as exc:
        logger.warning("delete_thread failed: %s — falling back to storage/writes filter", exc)
        return False


def _clear_storage(cp: Any, thread_id: str) -> None:
    """Remove the thread's checkpoint entries from checkpointer.storage.

    langgraph 1.x: storage keys are 3-tuples (thread_id, checkpoint_ns,
    checkpoint_id). Filter by k[0] == thread_id instead of direct lookup.
    """
    storage = getattr(cp, "storage", None)
    if not storage:
        return
    stale_keys = [k for k in storage if _key_thread_id(k) == thread_id]
    for k in stale_keys:
        storage.pop(k, None)
    if stale_keys:
        logger.info("cleared_checkpoint_storage thread_id=%s count=%s", thread_id, len(stale_keys))


def _clear_writes(cp: Any, thread_id: str) -> None:
    """Remove the thread's pending writes from checkpointer.writes."""
    writes = getattr(cp, "writes", None)
    if not writes:
        return
    stale = [k for k in writes if _key_thread_id(k) == thread_id]
    for k in stale:
        writes.pop(k, None)
    if stale:
        logger.info("cleared_checkpoint_writes thread_id=%s count=%s", thread_id, len(stale))


def _key_thread_id(key: Any) -> str:
    """Extract thread_id from a 1.x checkpoint key (tuple or scalar).

    1.x keys are (thread_id, checkpoint_ns, checkpoint_id) tuples; 0.x keys
    were plain thread_id strings. Handle both for forward/back compat.
    """
    if isinstance(key, tuple) and key:
        return str(key[0])
    return str(key)


def _build_llm(
    model: str, api_key: str, base_url: str,
    temperature: float, max_tokens: int,
) -> Any:
    """Wrap OpenAI-compatible endpoint as ReasoningChatOpenAI.

    Uses the ReasoningChatOpenAI subclass so DeepSeek/QwQ reasoning_content is
    captured into additional_kwargs (langchain-openai drops it by default).
    """
    from src.agent.reasoning_chat import ReasoningChatOpenAI
    return ReasoningChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_completion_tokens=max_tokens,
    )


# ═══════════════════════════════════════════
# Tool list assembly (per-request)
# ═══════════════════════════════════════════

def build_tool_specs(payload: Any) -> list[BaseTool]:
    """Instantiate per-request ToolSpec tools and inject ctx (NOT registered globally).

    Creates fresh tool instances every call so concurrent requests never share
    ctx or per-request config (top_k / kb_ids / credentials). Per-request
    instances are NOT written to the global _TOOL_REGISTRY — that registry is
    populated once with default instances by ensure_default_tools_registered
    for dispatch lookup, HITL metadata, and standalone endpoints. Writing
    per-request instances to the global registry caused ctx 串号 under
    concurrency (last writer's ctx/config won, so session A used session B's
    ctx). The per-request ToolContext is injected via ToolSpec._ctx PrivateAttr.

    Args:
        payload: ChatRequest (or compatible). Reads top_k, kb_ids,
            relevance_threshold, tool_keys.tavily, permission_mode, session_id.
    """
    ensure_default_tools_registered()
    ctx = _build_tool_ctx(payload)
    tool_list = _assemble_all_tools(payload)
    _inject_ctx(tool_list, ctx)
    logger.info(
        "build_tool_specs done count=%s names=%s",
        len(tool_list), [t.name for t in tool_list],
    )
    return tool_list


# Backward-compat alias — external callers may still import build_tools.
build_tools = build_tool_specs


def ensure_default_tools_registered() -> None:
    """Register all tools with default config when registry is empty.

    Enables the standalone /api/tool and /api/tools endpoints to work without
    requiring a prior /api/chat Agent request (which is what normally registers
    tools via build_tool_specs). Idempotent — no-op when registry is populated.
    """
    from src.agent.core.toolkit.tool_router import (
        _TOOL_REGISTRY, register,
    )
    from src.agent.exceptions import ToolContext
    if _TOOL_REGISTRY:
        return
    ctx = ToolContext(permission_mode="default", session_id="standalone")
    tool_list = _assemble_all_tools(_DefaultPayload())
    _inject_ctx_and_register(tool_list, ctx, register)
    logger.info(
        "ensure_default_tools_registered count=%s names=%s",
        len(tool_list), [t.name for t in tool_list],
    )


class _DefaultPayload:
    """Minimal payload stub for standalone tool registration (no KB context)."""

    top_k = _DEFAULT_TOP_K
    kb_ids = None
    relevance_threshold = _DEFAULT_THRESHOLD
    tool_keys: dict = {}
    permission_mode = "default"
    session_id = "standalone"


def _assemble_all_tools(payload: Any) -> list[BaseTool]:
    """Instantiate all tools and filter out disabled ones (data/tool_config.json).

    Disabled tools are removed before Agent injection so the LLM never sees
    them. Tool order follows the group dict order (retrieval → hardware →
    workbench → code → file_ops → execution); ToolRouter dispatch is by-name
    so order does not affect behavior.
    """
    groups = _build_tool_groups(payload)
    flat = [t for tools in groups.values() for t in tools]
    return _filter_disabled_tools(flat)


def _build_tool_groups(payload: Any = None) -> dict[str, list[BaseTool]]:
    """Instantiate all 26 tools grouped by their subdirectory.

    Returns a dict mapping group name → list of BaseTool. Tools that need
    per-request credentials (Tavily / vision / image) read them from payload.
    When payload is None, _DefaultPayload is used (standalone metadata path).
    """
    if payload is None:
        payload = _DefaultPayload()
    from src.agent.tools.groups.retrieval import (
        ImageGenerationTool, ListKbDocsTool, SearchDocsTool, SearchHistoryTool,
        ViewImageTool, VisionAnalysisTool, WebFetchTool, WebSearchTool,
    )
    from src.agent.tools.groups.hardware import AuditPinsTool, WiringTool
    from src.agent.tools.groups.workbench import get_workbench_tools
    from src.agent.tools.groups.code import BuildTool, FlashTool

    top_k = getattr(payload, "top_k", None) or _DEFAULT_TOP_K
    kb_ids = getattr(payload, "kb_ids", None)
    threshold = getattr(payload, "relevance_threshold", None) or _DEFAULT_THRESHOLD
    tavily_key = _read_tavily_key(payload)
    tavily_base_url = _read_tavily_base_url(payload)
    vision_model, vision_base_url, vision_api_key = _read_vision_creds(payload)
    image_model, image_base_url, image_api_key = _read_image_creds(payload)

    return {
        _TOOL_GROUP_RETRIEVAL: [
            SearchDocsTool(top_k=top_k, kb_ids=kb_ids, threshold=threshold),
            ListKbDocsTool(kb_ids=kb_ids),
            WebSearchTool(tavily_api_key=tavily_key, tavily_base_url=tavily_base_url),
            WebFetchTool(),
            SearchHistoryTool(),
            VisionAnalysisTool(vision_model=vision_model, base_url=vision_base_url, api_key=vision_api_key),
            ViewImageTool(vision_model=vision_model, base_url=vision_base_url, api_key=vision_api_key),
            ImageGenerationTool(image_model=image_model, base_url=image_base_url, api_key=image_api_key),
        ],
        _TOOL_GROUP_HARDWARE: [AuditPinsTool(), WiringTool()],
        _TOOL_GROUP_WORKBENCH: list(get_workbench_tools()),
        _TOOL_GROUP_CODE: [BuildTool(), FlashTool()],
        _TOOL_GROUP_FILE_OPS: _build_local_file_ops_tools(),
        _TOOL_GROUP_EXECUTION: _build_local_execution_tools(),
    }


def list_tool_metadata() -> list[dict]:
    """Return metadata for all 26 Agent tools (name/description/group/enabled).

    Used by GET /api/tools. `enabled` is read from data/tool_config.json
    (absent file → all enabled). Disabled tools are still listed (with
    enabled=False) so the frontend can render the toggle UI.
    """
    disabled = _load_disabled_tools()
    groups = _build_tool_groups()
    metadata: list[dict] = []
    for group, tools in groups.items():
        for tool in tools:
            metadata.append({
                "name": tool.name,
                "description": tool.description or "",
                "group": group,
                "enabled": tool.name not in disabled,
            })
    return metadata


def toggle_tool_enabled(name: str) -> bool | None:
    """Flip the enabled state of a tool; persist to data/tool_config.json.

    Returns the new enabled state, or None when the tool name is unknown.
    """
    valid_names = {t.name for tools in _build_tool_groups().values() for t in tools}
    if name not in valid_names:
        return None
    disabled = _load_disabled_tools()
    if name in disabled:
        disabled.discard(name)
        enabled = True
    else:
        disabled.add(name)
        enabled = False
    _save_disabled_tools(disabled)
    logger.info("tool toggled name=%s enabled=%s", name, enabled)
    return enabled


def _filter_disabled_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Remove tools whose name appears in the disabled list."""
    disabled = _load_disabled_tools()
    if not disabled:
        return tools
    filtered = [t for t in tools if t.name not in disabled]
    if len(filtered) != len(tools):
        logger.info(
            "filtered disabled tools: before=%s after=%s disabled=%s",
            len(tools), len(filtered), sorted(disabled),
        )
    return filtered


def _load_disabled_tools() -> set[str]:
    """Read disabled tool names from data/tool_config.json. Absent → empty set."""
    try:
        if not _TOOL_CONFIG_PATH.exists():
            return set()
        data = json.loads(_TOOL_CONFIG_PATH.read_text(encoding="utf-8"))
        raw = data.get("disabled", []) if isinstance(data, dict) else []
        return {str(n) for n in raw}
    except Exception as e:
        logger.warning("load disabled tools failed: %s", e)
        return set()


def _save_disabled_tools(disabled: set[str]) -> None:
    """Persist disabled tool names to data/tool_config.json (sorted for stability)."""
    try:
        _TOOL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOOL_CONFIG_PATH.write_text(
            json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("save disabled tools failed: %s", e)


def _inject_ctx(tools: list[BaseTool], ctx: Any) -> None:
    """Inject per-request ToolContext into each tool (NO global registration).

    Used by build_tool_specs for the per-request Agent path so concurrent
    requests keep isolated ctx. The global _TOOL_REGISTRY stays populated
    with default instances only (via ensure_default_tools_registered), which
    avoids the last-writer-wins ctx 串号 under concurrency.
    """
    for tool in tools:
        tool._ctx = ctx


def _inject_ctx_and_register(tools: list[BaseTool], ctx: Any, register: Any) -> None:
    """Inject per-request ToolContext into each tool and register to ToolRouter.

    Used only by ensure_default_tools_registered (standalone default path) —
    NOT by build_tool_specs, which keeps per-request instances out of the
    global registry to prevent concurrent ctx 串号.
    """
    for tool in tools:
        tool._ctx = ctx
        register(tool)


def _build_tool_ctx(payload: Any) -> ToolContext:
    """Build ToolContext from payload's permission_mode + session_id."""
    from src.agent.exceptions import ToolContext
    mode = getattr(payload, "permission_mode", "default") or "default"
    session_id = getattr(payload, "session_id", "default") or "default"
    return ToolContext(permission_mode=mode, session_id=session_id)


def _build_local_tools() -> list[BaseTool]:
    """Backward-compat: file_ops + execution flat list (ctx injected by build_tool_specs)."""
    return _build_local_file_ops_tools() + _build_local_execution_tools()


def _build_local_file_ops_tools() -> list[BaseTool]:
    """Instantiate all 8 file_ops tools (apply_patch disabled — see docs/pitfalls.md)."""
    from src.agent.tools.groups.file_ops import (
        EditFileTool, GlobTool, GrepTool,
        ListFilesTool, MultiEditTool, ReadFileTool, UndoEditTool, WriteFileTool,
    )
    return [
        ReadFileTool(), WriteFileTool(), EditFileTool(),
        GrepTool(), ListFilesTool(), GlobTool(),
        UndoEditTool(), MultiEditTool(),
    ]


def _build_local_execution_tools() -> list[BaseTool]:
    """Instantiate all 2 execution tools."""
    from src.agent.tools.groups.execution import RunCommandTool, TodoWriteTool
    return [RunCommandTool(), TodoWriteTool()]


def _read_tavily_key(payload: Any) -> str:
    """Read Tavily key from payload.tool_keys (if present) or env."""
    tool_keys = getattr(payload, "tool_keys", None) or {}
    if isinstance(tool_keys, dict) and tool_keys.get("tavily"):
        return str(tool_keys["tavily"])
    try:
        from src.config.settings import settings
        return getattr(settings, "tavily_api_key", "") or ""
    except Exception:
        return ""


def _read_tavily_base_url(payload: Any) -> str:
    """Read Tavily-compatible base URL from payload.tool_keys (if present)."""
    tool_keys = getattr(payload, "tool_keys", None) or {}
    if isinstance(tool_keys, dict) and tool_keys.get("tavily_base_url"):
        return str(tool_keys["tavily_base_url"])
    return ""


def _read_vision_creds(payload: Any) -> tuple[str, str, str]:
    """Read vision model/base_url/api_key from payload.tool_keys."""
    tool_keys = getattr(payload, "tool_keys", None) or {}
    if not isinstance(tool_keys, dict):
        logger.warning("_read_vision_creds: tool_keys is not a dict (%s)", type(tool_keys).__name__)
        return "", "", ""
    model = str(tool_keys.get("vision_model", ""))
    base_url = str(tool_keys.get("vision_base_url", ""))
    api_key = str(tool_keys.get("vision_api_key", ""))
    logger.info("_read_vision_creds: model=%s base_url=%s has_key=%s", model, base_url, bool(api_key))
    return model, base_url, api_key


def _read_image_creds(payload: Any) -> tuple[str, str, str]:
    """Read image model/base_url/api_key from payload.tool_keys."""
    tool_keys = getattr(payload, "tool_keys", None) or {}
    if not isinstance(tool_keys, dict):
        logger.warning("image_creds tool_keys not dict: %r", type(tool_keys))
        return "", "", ""
    image_model = str(tool_keys.get("image_model", ""))
    image_base_url = str(tool_keys.get("image_base_url", ""))
    image_api_key = str(tool_keys.get("image_api_key", ""))
    logger.info("image_creds received model=%s base_url=%s key_len=%d", image_model, image_base_url, len(image_api_key))
    return image_model, image_base_url, image_api_key


def _make_llm_client() -> LLMClient:
    """Build a default LLMClient (kept for future tools that need direct LLM)."""
    return LLMClient()


# ═══════════════════════════════════════════
# Agent path gating (see spec §10.1)
# ═══════════════════════════════════════════

def _should_use_agent(payload: Any, model: str = "") -> bool:
    """Decide whether the Agent path is allowed for this request.

    Three conditions (short-circuit AND):
      1. payload.use_agent is True (frontend opted in).
      2. effective model is in FUNCTION_CALLING_MODELS (or whitelist empty → allow).
         `model` arg is the resolved model (chat_routes falls back to settings.llm_model
         when payload.model is None) — pass it here so default-config requests still work.
      3. langgraph is importable.
    """
    if not getattr(payload, "use_agent", False):
        return False
    effective_model = model or getattr(payload, "model", None) or ""
    if not _model_supports_tools(effective_model):
        return False
    try:
        import langgraph  # noqa: F401
        return True
    except ImportError:
        logger.warning("langgraph not installed — Agent path disabled")
        return False


def _model_supports_tools(model: str) -> bool:
    """Substring match against FUNCTION_CALLING_MODELS. Empty whitelist → allow."""
    if not FUNCTION_CALLING_MODELS:
        return True
    model_lower = (model or "").lower()
    return any(white.lower() in model_lower for white in FUNCTION_CALLING_MODELS)


# ═══════════════════════════════════════════
# Stream config (see spec §2.1, §8.2)
# ═══════════════════════════════════════════

def build_agent_config(session_id: str) -> dict:
    """Build the config dict passed to agent.astream().

    - recursion_limit: 20-iteration hard cap (see prompts.MAX_RECURSION).
    - configurable.thread_id: per-session memory key (MemorySaver checkpointing).
    """
    return {
        "recursion_limit": MAX_RECURSION,
        "configurable": {"thread_id": session_id or "default"},
    }
