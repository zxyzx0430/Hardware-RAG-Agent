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
import inspect
import json
import logging
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain_core.tools import BaseTool

from src.agent.prompts import (
    FUNCTION_CALLING_MODELS,
    MAX_RECURSION,
    append_long_term_memory,
    append_skills_context,
    build_system_prompt,
)
from src.llm.client import LLMClient
from src.agent.telemetry import (
    AGENT_INSTANCE_ID_ATTR,
    emit_agent_timing,
    new_agent_instance_id,
    utc_timestamp,
)
from src.agent.compact.writer_lifecycle import (
    CheckpointResetOutcome,
    QuarantineReason,
    WriterLease,
    WriterLifecycleError,
    WriterLifecycleRegistry,
    WriterScope,
)

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

    Bundles the create_hardware_agent inputs so call sites don't have to
    thread credentials, token limits, context overrides, and Agent settings
    through every function signature (P2-I-1 parameter encapsulation).
    """
    model: str
    api_key: str
    base_url: str
    tools: list[BaseTool]
    temperature: float = 0.7
    max_tokens: int = 4096
    enable_hitl: bool = False
    long_term_memory: str = ""
    skills_mode: str = "off"
    skills_catalog: list[dict] = field(default_factory=list)
    skills_context: list[dict] = field(default_factory=list)
    context_window_override: int | None = None


@dataclass(frozen=True, slots=True)
class AgentWriterBinding:
    """Factory-proven graph/saver scope for one caller-owned request epoch.

    Callers acquire a lease with ``registry.acquire(current_scope())`` and
    retain it through the native run. ``current_scope`` is synchronous and
    rebuilds the same graph/saver/request binding from the caller's epoch
    authority after awaits. Call it before acquisition and after awaits. This
    binding does not fence legacy writers or direct reset calls.
    """

    registry: WriterLifecycleRegistry = field(repr=False)
    scope: WriterScope
    current_scope: Callable[[], WriterScope] = field(repr=False)
    _factory_token: object = field(repr=False, compare=False)


# ═══════════════════════════════════════════
# Agent construction
# ═══════════════════════════════════════════

async def create_hardware_agent_from_config(config: AgentConfig) -> Any:
    """Build a compiled agent from an AgentConfig.

    Wraps the LLM with langchain ChatOpenAI (required by create_agent —
    project LLMClient is not a BaseChatModel). The prebuilt agent binds tools
    internally, so we pass the raw LLM (not a bind_tools result).

        Args:
            config: AgentConfig holding model, credentials, tools, and limits.

    Returns:
        CompiledStateGraph (langgraph). Call `.astream(events, config=...)` on it.
    """
    logger.info(
        "create_hardware_agent model=%s enable_hitl=%s tools_count=%s",
        config.model, config.enable_hitl, len(config.tools) if config.tools else 0,
    )
    agent_instance_id = new_agent_instance_id()
    build_started_ns = time.perf_counter_ns()
    build_started_at = utc_timestamp()
    build_status = "error"
    instance_id_attached: bool | None = None
    emit_agent_timing(
        "agent_build_start",
        agent_instance_id=agent_instance_id,
        started_at=build_started_at,
    )
    try:
        llm = _build_llm(
            config.model, config.api_key, config.base_url,
            config.temperature, config.max_tokens,
            config.context_window_override,
        )
        interrupt_before = ["tools"] if config.enable_hitl else []
        try:
            from langchain.agents import create_agent
            from src.agent.exceptions import ToolContext
            checkpointer = await _get_checkpointer()
            system_prompt = append_skills_context(
                append_long_term_memory(build_system_prompt(), config.long_term_memory),
                config.skills_mode,
                catalog=config.skills_catalog,
                manual_skills=config.skills_context,
            )
            agent = create_agent(
                model=llm,
                tools=config.tools,
                system_prompt=system_prompt,
                middleware=(_TOKEN_MIDDLEWARE,),
                context_schema=ToolContext,
                checkpointer=checkpointer,
                interrupt_before=interrupt_before,
            )
            try:
                from src.agent.skill_proof import (
                    record_factory_system_prompt_after_create_agent_from_context,
                )

                record_factory_system_prompt_after_create_agent_from_context(
                    system_prompt
                )
            except Exception:
                # Prompt proof is observational and cannot change construction.
                pass
        except ImportError as exc:
            logger.error("langchain.agents import failed: %s", exc)
            raise
        except Exception as exc:
            # bind_tools errors (model lacks function calling) surface here.
            logger.error("create_agent failed (model may not support tools): %s", exc)
            raise
        try:
            _register_factory_graph(agent, checkpointer)
        except Exception as exc:
            # Lifecycle tracking is opt-in. Preserve legacy Agent creation, but
            # binding construction will refuse an unregistered graph.
            logger.warning("agent writer lifecycle provenance unavailable: %s", exc)
        try:
            setattr(agent, AGENT_INSTANCE_ID_ATTR, agent_instance_id)
            instance_id_attached = True
        except Exception:
            instance_id_attached = False
        try:
            from src.agent.compact.checkpoint_publish import _mint_initial_publication

            if not _mint_initial_publication(agent, create_agent, _TOKEN_MIDDLEWARE):
                logger.debug("Private initial publication unavailable for this graph shape")
        except Exception:
            # Provenance is optional and host-only; never change the tools,
            # prompts, permissions or ordinary graph execution on mint failure.
            logger.debug("Private initial publication provenance unavailable")
        try:
            from src.agent.writer_lifecycle import mint_graph_provenance

            if not mint_graph_provenance(agent):
                logger.warning("Host writer lifecycle unavailable for this graph shape")
        except Exception:
            logger.warning("Host writer lifecycle provenance could not be minted")
        logger.info(
            "hardware_agent_created model=%s hitl=%s recursion_limit=%s",
            config.model, config.enable_hitl, MAX_RECURSION,
        )
        build_status = "complete"
        return agent
    except asyncio.CancelledError:
        build_status = "cancelled"
        raise
    finally:
        emit_agent_timing(
            "agent_build_end",
            agent_instance_id=agent_instance_id,
            started_at=build_started_at,
            ended_at=utc_timestamp(),
            duration_ms=round(max(time.perf_counter_ns() - build_started_ns, 0) / 1_000_000, 3),
            status=build_status,
            instance_id_attached=instance_id_attached,
        )


async def create_hardware_agent(
    model: str,
    api_key: str,
    base_url: str,
    tools: list[BaseTool],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    enable_hitl: bool = False,
    long_term_memory: str = "",
    skills_mode: str = "off",
    skills_catalog: list[dict] | None = None,
    skills_context: list[dict] | None = None,
    context_window_override: int | None = None,
) -> Any:
    """Backward-compat shim around create_hardware_agent_from_config (P2-I-1).

    Keeps existing call sites (chat_routes /api/agent-sandbox/resume) working;
    the optional context override is appended for backward compatibility. New
    code should construct an AgentConfig and call
    create_hardware_agent_from_config.
    """
    config = AgentConfig(
        model=model, api_key=api_key, base_url=base_url, tools=tools,
        temperature=temperature, max_tokens=max_tokens, enable_hitl=enable_hitl,
        long_term_memory=long_term_memory,
        skills_mode=skills_mode,
        skills_catalog=skills_catalog or [],
        skills_context=skills_context or [],
        context_window_override=context_window_override,
    )
    return await create_hardware_agent_from_config(config)


# Module-level checkpointer singleton — shared across all agent instances so the
# resume API (which rebuilds the agent) can recover state via thread_id checkpoint.
_global_checkpointer: Any = None
# AsyncSqliteSaver.from_conn_string is an async context manager; we must keep the
# context-manager object alive for the process lifetime so the aiosqlite connection
# is not torn down after the first __aenter__ call.
_global_checkpointer_cm: Any = None
# Cooperative initialization only, not a graph writer fence or a deployment
# assumption. Existing shared savers are never initialized/repaired here.
_checkpointer_creation_lock = asyncio.Lock()

# The async initializer lock is process-wide and independent of an asyncio
# event loop. Contending loops yield while another caller initializes the
# saver, so only one actual checkpointer can be published.
_checkpointer_init_lock = threading.Lock()

# The writer registry is bound once to the exact process checkpointer object.
_writer_lifecycle_lock = threading.RLock()
_writer_lifecycle_saver: Any = None
_writer_lifecycle_registry: WriterLifecycleRegistry | None = None

# id(graph) avoids graph equality/hash semantics; weakrefs prove identity while
# avoiding a process-lifetime strong reference to every compiled graph.
_factory_graph_lock = threading.RLock()
_factory_graph_savers: dict[int, tuple[Any, Any]] = {}
_agent_writer_binding_token = object()

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
    while not _checkpointer_init_lock.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
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
    finally:
        _checkpointer_init_lock.release()


def _writer_registry_for_saver(saver: object) -> WriterLifecycleRegistry:
    """Return the sole process registry, bound to the exact global saver."""
    global _writer_lifecycle_saver, _writer_lifecycle_registry
    with _writer_lifecycle_lock:
        if saver is None or saver is not _global_checkpointer:
            raise WriterLifecycleError(
                "writer registry saver differs from the process checkpointer"
            )
        if _writer_lifecycle_registry is None:
            if _writer_lifecycle_saver is not None:
                raise WriterLifecycleError("writer registry binding is inconsistent")
            registry = WriterLifecycleRegistry(known_savers=(saver,))
            _writer_lifecycle_saver = saver
            _writer_lifecycle_registry = registry
        elif _writer_lifecycle_saver is not saver:
            raise WriterLifecycleError(
                "process checkpointer identity changed after writer registry binding"
            )
        return _writer_lifecycle_registry


def _register_factory_graph(agent: object, saver: object) -> None:
    """Record exact factory graph/saver provenance using a weak identity key."""
    _writer_registry_for_saver(saver)
    graph_id = id(agent)

    def forget_graph(graph_ref: Any) -> None:
        with _factory_graph_lock:
            prior = _factory_graph_savers.get(graph_id)
            if prior is not None and prior[0] is graph_ref:
                _factory_graph_savers.pop(graph_id, None)

    try:
        graph_ref = weakref.ref(agent, forget_graph)
    except TypeError as exc:
        raise WriterLifecycleError(
            "factory graph cannot be weakly identity-tracked"
        ) from exc

    with _factory_graph_lock:
        prior = _factory_graph_savers.get(graph_id)
        if prior is not None:
            prior_graph = prior[0]()
            if prior_graph is agent:
                if prior[1] is not saver:
                    raise WriterLifecycleError(
                        "factory graph was returned with a different saver"
                    )
                return
            if prior_graph is not None:
                raise WriterLifecycleError("factory graph identity tracking conflicted")
        _factory_graph_savers[graph_id] = (graph_ref, saver)


def _factory_saver_for_graph(agent: object) -> object:
    """Require a live graph identity registered by this factory."""
    with _factory_graph_lock:
        registered = _factory_graph_savers.get(id(agent))
        if registered is None or registered[0]() is not agent:
            raise WriterLifecycleError(
                "writer binding requires a live graph returned by this factory"
            )
        return registered[1]


def _build_writer_scope(
    agent: object,
    saver: object,
    owner_key: str,
    request_scope_token: object,
    epoch_provider: Callable[[], object],
) -> WriterScope:
    """Build a scope using only caller-owned request and epoch authority."""
    epoch = epoch_provider()
    if inspect.isawaitable(epoch):
        close = getattr(epoch, "close", None)
        if callable(close):
            close()
        raise WriterLifecycleError("epoch_provider must be synchronous")
    return WriterScope(
        owner_key=owner_key,
        graph=agent,
        saver=saver,
        request_scope=request_scope_token,
        epoch=epoch,
        epoch_generation=None,
    )


def build_agent_writer_binding(
    agent: object,
    *,
    owner_key: str,
    request_scope_token: object,
    epoch_provider: Callable[[], object],
) -> AgentWriterBinding:
    """Build a lifecycle binding for a factory graph and host-owned epoch.

    ``request_scope_token`` must be a stable, opaque identity object owned by
    the caller. ``epoch_provider`` must synchronously return the authoritative
    epoch token on every call (an opaque object or the persistent empty/UUID
    hex token); no counter is invented here. The returned ``current_scope``
    reconstructs that exact graph/saver/request binding after awaits.
    """
    if not callable(epoch_provider):
        raise WriterLifecycleError("epoch_provider must be callable")
    saver = _factory_saver_for_graph(agent)
    registry = _writer_registry_for_saver(saver)

    def current_scope() -> WriterScope:
        if _factory_saver_for_graph(agent) is not saver:
            raise WriterLifecycleError("factory graph/saver identity changed")
        if _writer_registry_for_saver(saver) is not registry:
            raise WriterLifecycleError("process writer registry identity changed")
        observed = _build_writer_scope(
            agent, saver, owner_key, request_scope_token, epoch_provider,
        )
        if _factory_saver_for_graph(agent) is not saver:
            raise WriterLifecycleError("factory graph/saver identity changed")
        if _writer_registry_for_saver(saver) is not registry:
            raise WriterLifecycleError("process writer registry identity changed")
        return observed

    scope = current_scope()
    return AgentWriterBinding(
        registry=registry,
        scope=scope,
        current_scope=current_scope,
        _factory_token=_agent_writer_binding_token,
    )


def _read_checkpointer_type() -> str:
    """Read agent_checkpointer_type from settings (fail-open to 'sqlite')."""
    try:
        from src.config.settings import settings
        return getattr(settings, "agent_checkpointer_type", "sqlite") or "sqlite"
    except Exception as exc:
        logger.warning("read agent_checkpointer_type failed: %s — defaulting to sqlite", exc)
        return "sqlite"


async def _build_sqlite_checkpointer() -> Any:
    """Publish one ready saver/CM pair, or the existing ordinary fallback.

    Only the exact native newly-created, unpublished CM can be set up/closed.
    Cancellation joins owned setup and teardown before releasing the creation
    lock. A previously shared saver (even unready) is returned unchanged.
    """
    global _global_checkpointer, _global_checkpointer_cm
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from src.agent.compact.checkpoint_publish import _join_initial_task

    async with _checkpointer_creation_lock:
        if _global_checkpointer is not None:
            return _global_checkpointer
        cm = None
        task = None
        try:
            _SQLITE_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
            cm = AsyncSqliteSaver.from_conn_string(str(_SQLITE_CHECKPOINT_PATH))
            task = asyncio.create_task(_initialize_owned_sqlite(cm))
            checkpointer = await _join_initial_task(task)
            # No await between publication of the ready saver and its owner CM.
            _global_checkpointer, _global_checkpointer_cm = checkpointer, cm
            logger.info("checkpointer=AsyncSqliteSaver path=%s", _SQLITE_CHECKPOINT_PATH)
            return checkpointer
        except asyncio.CancelledError:
            # A joined initializer may have succeeded after parent cancellation.
            # Its still-unpublished native CM is ours, never the shared pair.
            if task is not None and task.done() and not task.cancelled() and task.exception() is None:
                await _close_owned_sqlite(cm)
            raise
        except Exception as exc:
            logger.error("AsyncSqliteSaver init failed: %s — falling back to InMemorySaver", exc)
            from langgraph.checkpoint.memory import InMemorySaver
            _global_checkpointer, _global_checkpointer_cm = InMemorySaver(), None
            return _global_checkpointer


async def _close_owned_sqlite(cm) -> None:
    """Join teardown of a proven factory-owned, unpublished native CM only."""
    from src.agent.compact.checkpoint_publish import _join_initial_task

    await _join_initial_task(asyncio.create_task(cm.__aexit__(None, None, None)))


async def _initialize_owned_sqlite(cm) -> Any:
    """Finish setup before exposing the exact native generator's new connection."""
    from contextlib import _AsyncGeneratorContextManager
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    generator = AsyncSqliteSaver.from_conn_string.__func__.__wrapped__
    if (type(cm) is not _AsyncGeneratorContextManager or cm.func is not generator
            or cm.args != (AsyncSqliteSaver, str(_SQLITE_CHECKPOINT_PATH)) or cm.kwds
            or cm.gen.ag_code is not generator.__code__):
        raise RuntimeError("Cannot prove fresh native SQLite ownership")
    entered = False
    try:
        checkpointer = await cm.__aenter__()
        entered = True
        frame = cm.gen.ag_frame
        if (type(checkpointer) is not AsyncSqliteSaver or frame is None
                or frame.f_locals.get("conn") is not checkpointer.conn
                or checkpointer.is_setup is not False or checkpointer.conn.in_transaction):
            raise RuntimeError("Unsupported new SQLite connection")
        # Only this private new native connection is eligible. Never call setup
        # on the cache value or on a saver supplied by another graph/caller.
        await checkpointer.setup()
        if checkpointer.is_setup is not True or checkpointer.conn.in_transaction:
            raise RuntimeError("SQLite initialization did not finish")
        return checkpointer
    except BaseException:
        if entered:
            await _close_owned_sqlite(cm)
        raise


async def reset_thread_checkpoint(thread_id: str) -> None:
    """Legacy thread-id-only reset API; fail closed without touching a saver.

    A thread ID alone cannot prove writer ownership, run quiescence, or a
    conditional reset acknowledgement. Use
    ``reset_thread_checkpoint_for_writer`` with its factory binding and active
    lease for a protected reset.
    """
    raise WriterLifecycleError(
        "unguarded checkpoint reset is disabled; use "
        "reset_thread_checkpoint_for_writer with a factory binding and active lease"
    )


# Bounded join for the owned checkpoint-reset task. The reset must finish (or be
# quarantined) before the lease can be released, but waiting forever would swallow
# cancellation from asyncio.wait_for/timeout and pytest-timeout.
_RESET_JOIN_TIMEOUT_S: float = 30.0
_RESET_CANCEL_GRACE_S: float = 5.0
_RESET_ABANDON_WAIT_S: float = 1.0


async def reset_thread_checkpoint_for_writer(
    binding: AgentWriterBinding,
    lease: WriterLease,
) -> None:
    """Reset one idle bound thread through an owned SQLite transaction.

    A shared lifecycle reservation excludes runs and competing host resets for
    this lease. ACK requires a committed transaction and exact empty readback
    of both checkpoint and pending-write tables. InMemorySaver and unknown
    saver implementations are refused before invoking any mutation API.
    """
    saver = _assert_agent_writer_binding(binding, lease)
    scope = binding.scope
    token = binding.registry.begin_checkpoint_reset(lease, scope)
    reservation_open = True

    try:
        saver = _assert_agent_writer_binding(binding, lease)
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        if type(saver) is InMemorySaver:
            binding.registry.finish_checkpoint_reset(
                lease,
                scope,
                token,
                outcome=CheckpointResetOutcome.NOT_ATTEMPTED,
            )
            reservation_open = False
            raise WriterLifecycleError(
                "checkpoint reset is unsupported for InMemorySaver: it has no "
                "owned lock and atomic complete-state readback"
            )
        if type(saver) is not AsyncSqliteSaver:
            raise WriterLifecycleError(
                "checkpoint reset requires the exact installed AsyncSqliteSaver"
            )
        _validate_bound_async_sqlite_saver(saver)
        _assert_agent_writer_binding(binding, lease)
        binding.registry.mark_checkpoint_reset_started(lease, scope, token)
        task = asyncio.create_task(
            _reset_bound_thread_checkpoint(saver, binding, lease),
            name="agent-checkpoint-reset",
        )
    except BaseException:
        if reservation_open:
            _quarantine_checkpoint_reset(binding, lease, scope, token)
        raise

    cancellation: asyncio.CancelledError | None = None
    task_error: BaseException | None = None
    deadline = time.monotonic() + _RESET_JOIN_TIMEOUT_S
    join_timed_out = False
    while not task.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            join_timed_out = True
            break
        try:
            # asyncio.wait does not cancel the awaited task, unlike wait_for, so
            # the owned reset keeps its join and cleanup window.
            await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
                # First cancellation keeps the join, but bounds it to a grace
                # window so the caller can still observe its own cancel.
                deadline = min(
                    deadline, time.monotonic() + _RESET_CANCEL_GRACE_S,
                )
    if task.done() and task_error is None and not task.cancelled():
        task_error = task.exception()

    if join_timed_out:
        logger.error(
            "checkpoint reset join timed out after %.1fs (cancel_requested=%s)",
            _RESET_JOIN_TIMEOUT_S,
            cancellation is not None,
        )
        _quarantine_checkpoint_reset(binding, lease, scope, token)
        reservation_open = False
        await _abandon_checkpoint_reset_task(task)
        if cancellation is not None:
            raise cancellation
        raise WriterLifecycleError(
            "checkpoint reset did not join within its deadline"
        )

    try:
        if task_error is not None:
            raise task_error
        task.result()
        _assert_agent_writer_binding(binding, lease)
        binding.registry.finish_checkpoint_reset(
            lease,
            scope,
            token,
            outcome=CheckpointResetOutcome.ACKNOWLEDGED,
        )
        reservation_open = False
    except BaseException:
        if reservation_open:
            _quarantine_checkpoint_reset(binding, lease, scope, token)
            reservation_open = False
        if cancellation is not None:
            raise cancellation
        raise

    if cancellation is not None:
        raise cancellation


async def _abandon_checkpoint_reset_task(task: asyncio.Task) -> None:
    """Cancel a reset that outlived its join deadline and wait briefly.

    The writer is already quarantined when this runs, so a late commit or a
    failed rollback cannot be acknowledged or reused. Cancellation here reaches
    the reset's own rollback path; any failure is swallowed by design.
    """
    if task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=_RESET_ABANDON_WAIT_S)
    except BaseException:
        pass


def _assert_agent_writer_binding(
    binding: AgentWriterBinding,
    lease: WriterLease,
) -> object:
    if (
        type(binding) is not AgentWriterBinding
        or binding._factory_token is not _agent_writer_binding_token
        or binding.registry is not _writer_lifecycle_registry
        or not isinstance(binding.scope, WriterScope)
    ):
        raise WriterLifecycleError("reset requires a binding created by this factory")
    saver = _factory_saver_for_graph(binding.scope.graph)
    if saver is not binding.scope.saver:
        raise WriterLifecycleError("bound graph/saver identity changed")
    _writer_registry_for_saver(saver)
    observed_scope = binding.current_scope()
    binding.registry.assert_scope(lease, observed_scope)
    return saver


def _quarantine_agent_writer_binding(
    binding: AgentWriterBinding,
    lease: WriterLease,
) -> None:
    if (
        type(binding) is not AgentWriterBinding
        or binding._factory_token is not _agent_writer_binding_token
        or not isinstance(binding.registry, WriterLifecycleRegistry)
        or not isinstance(binding.scope, WriterScope)
    ):
        return
    try:
        binding.registry.quarantine(
            lease,
            binding.scope,
            reason=QuarantineReason.WRITE_OUTCOME_UNKNOWN,
        )
    except WriterLifecycleError:
        # A stale/foreign lease or existing quarantine is already fail-closed.
        pass


def _validate_bound_async_sqlite_saver(saver: object) -> None:
    """Reject saver/connection substitutions before any reset mutation."""
    import aiosqlite

    if (
        type(getattr(saver, "conn", None)) is not aiosqlite.Connection
        or type(getattr(saver, "lock", None)) is not asyncio.Lock
        or getattr(saver, "loop", None) is not asyncio.get_running_loop()
        or getattr(saver, "is_setup", None) is not True
    ):
        raise WriterLifecycleError(
            "AsyncSqliteSaver connection, lock, loop, or initialized schema is "
            "not the exact supported binding"
        )


def _quarantine_checkpoint_reset(
    binding: AgentWriterBinding,
    lease: WriterLease,
    scope: WriterScope,
    token: object,
) -> None:
    try:
        binding.registry.finish_checkpoint_reset(
            lease,
            scope,
            token,
            outcome=CheckpointResetOutcome.UNKNOWN,
        )
    except BaseException:
        pass
    _quarantine_agent_writer_binding(binding, lease)


async def _reset_bound_thread_checkpoint(
    saver: object,
    binding: AgentWriterBinding,
    lease: WriterLease,
) -> None:
    """Delete and prove one thread empty under the exact saver-owned lock."""
    _validate_bound_async_sqlite_saver(saver)
    connection = saver.conn  # type: ignore[attr-defined]
    thread_id = binding.scope.owner_key
    owns_transaction = False

    async with saver.lock:  # type: ignore[attr-defined]
        _assert_agent_writer_binding(binding, lease)
        _validate_bound_async_sqlite_saver(saver)
        if type(connection.in_transaction) is not bool or connection.in_transaction is not False:
            raise WriterLifecycleError(
                "checkpoint reset refused because the saver connection already "
                "has a transaction"
            )

        try:
            _assert_agent_writer_binding(binding, lease)
            begin_cursor = await connection.execute("BEGIN IMMEDIATE")
            if type(connection.in_transaction) is bool and connection.in_transaction is True:
                owns_transaction = True
            try:
                _assert_agent_writer_binding(binding, lease)
                if not owns_transaction:
                    raise WriterLifecycleError(
                        "SQLite did not confirm the reset-owned BEGIN IMMEDIATE"
                    )
            finally:
                await begin_cursor.close()
                _assert_agent_writer_binding(binding, lease)

            _require_owned_reset_transaction(connection)
            for table, expected_columns in _RESET_TABLE_SCHEMAS.items():
                rows = await _checkpoint_reset_fetchall(
                    connection,
                    binding,
                    lease,
                    f"PRAGMA table_info({table})",
                )
                if not _checkpoint_reset_schema_matches(rows, expected_columns):
                    raise WriterLifecycleError(
                        f"installed AsyncSqliteSaver {table} schema is not the "
                        "exact supported layout"
                    )
                _require_owned_reset_transaction(connection)

            _assert_agent_writer_binding(binding, lease)
            _require_owned_reset_transaction(connection)
            await _checkpoint_reset_execute(
                connection,
                binding,
                lease,
                "DELETE FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
            _require_owned_reset_transaction(connection)

            _assert_agent_writer_binding(binding, lease)
            _require_owned_reset_transaction(connection)
            await _checkpoint_reset_execute(
                connection,
                binding,
                lease,
                "DELETE FROM writes WHERE thread_id = ?",
                (thread_id,),
            )
            _require_owned_reset_transaction(connection)

            if not await _checkpoint_reset_thread_is_empty(
                connection, binding, lease, thread_id,
            ):
                raise WriterLifecycleError(
                    "checkpoint reset transaction readback found remaining rows"
                )
            _require_owned_reset_transaction(connection)

            _assert_agent_writer_binding(binding, lease)
            _require_owned_reset_transaction(connection)
            await connection.commit()
            _assert_agent_writer_binding(binding, lease)
            if type(connection.in_transaction) is not bool or connection.in_transaction is not False:
                raise WriterLifecycleError(
                    "SQLite commit did not confirm completion of the owned reset"
                )
            owns_transaction = False

            if not await _checkpoint_reset_thread_is_empty(
                connection, binding, lease, thread_id,
            ):
                raise WriterLifecycleError(
                    "checkpoint reset post-commit readback found remaining rows"
                )
            _assert_agent_writer_binding(binding, lease)
        except BaseException:
            if owns_transaction:
                try:
                    if type(connection.in_transaction) is bool and connection.in_transaction is True:
                        await connection.rollback()
                        owns_transaction = False
                        _assert_agent_writer_binding(binding, lease)
                        if type(connection.in_transaction) is not bool or connection.in_transaction is not False:
                            raise WriterLifecycleError(
                                "SQLite rollback did not close the owned reset transaction"
                            )
                    elif type(connection.in_transaction) is bool and connection.in_transaction is False:
                        owns_transaction = False
                except BaseException:
                    pass
            raise

    _assert_agent_writer_binding(binding, lease)


_RESET_TABLE_SCHEMAS = {
    "checkpoints": (
        ("thread_id", "TEXT", 1, None, 1),
        ("checkpoint_ns", "TEXT", 1, "''", 2),
        ("checkpoint_id", "TEXT", 1, None, 3),
        ("parent_checkpoint_id", "TEXT", 0, None, 0),
        ("type", "TEXT", 0, None, 0),
        ("checkpoint", "BLOB", 0, None, 0),
        ("metadata", "BLOB", 0, None, 0),
    ),
    "writes": (
        ("thread_id", "TEXT", 1, None, 1),
        ("checkpoint_ns", "TEXT", 1, "''", 2),
        ("checkpoint_id", "TEXT", 1, None, 3),
        ("task_id", "TEXT", 1, None, 4),
        ("idx", "INTEGER", 1, None, 5),
        ("channel", "TEXT", 1, None, 0),
        ("type", "TEXT", 0, None, 0),
        ("value", "BLOB", 0, None, 0),
    ),
}


def _require_owned_reset_transaction(connection: object) -> None:
    if type(getattr(connection, "in_transaction", None)) is not bool or connection.in_transaction is not True:
        raise WriterLifecycleError(
            "checkpoint reset no longer owns an active SQLite transaction"
        )


def _checkpoint_reset_schema_matches(rows: object, expected: tuple) -> bool:
    if not isinstance(rows, (list, tuple)):
        return False
    try:
        observed = tuple(
            (
                row[1],
                str(row[2]).upper(),
                row[3],
                row[4],
                row[5],
            )
            for row in rows
        )
    except (IndexError, TypeError):
        return False
    return observed == expected


async def _checkpoint_reset_execute(
    connection: object,
    binding: AgentWriterBinding,
    lease: WriterLease,
    statement: str,
    parameters: tuple = (),
) -> None:
    _assert_agent_writer_binding(binding, lease)
    cursor = await connection.execute(statement, parameters)  # type: ignore[attr-defined]
    try:
        _assert_agent_writer_binding(binding, lease)
    finally:
        await cursor.close()
        _assert_agent_writer_binding(binding, lease)


async def _checkpoint_reset_fetchall(
    connection: object,
    binding: AgentWriterBinding,
    lease: WriterLease,
    statement: str,
    parameters: tuple = (),
) -> list:
    _assert_agent_writer_binding(binding, lease)
    cursor = await connection.execute(statement, parameters)  # type: ignore[attr-defined]
    try:
        _assert_agent_writer_binding(binding, lease)
        rows = await cursor.fetchall()
        _assert_agent_writer_binding(binding, lease)
        return rows
    finally:
        await cursor.close()
        _assert_agent_writer_binding(binding, lease)


async def _checkpoint_reset_thread_is_empty(
    connection: object,
    binding: AgentWriterBinding,
    lease: WriterLease,
    thread_id: str,
) -> bool:
    for table in ("checkpoints", "writes"):
        rows = await _checkpoint_reset_fetchall(
            connection,
            binding,
            lease,
            f"SELECT 1 FROM {table} WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        )
        if rows:
            return False
    return True


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


async def delete_thread_checkpoint(thread_id: str) -> bool:
    """Best-effort checkpoint removal for a deleted session."""
    if not isinstance(thread_id, str) or not thread_id:
        return False
    try:
        cp = await _get_checkpointer()
        return await _try_delete_thread(cp, thread_id)
    except Exception as exc:
        logger.warning("checkpoint delete failed thread_id=%s: %s", thread_id, exc)
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
    context_window_override: int | None = None,
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
        context_window_override=context_window_override,
    )


# ═══════════════════════════════════════════
# Tool list assembly (per-request)
# ═══════════════════════════════════════════

class _BuiltRequestToolMap(dict[str, BaseTool]):
    """Complete request tool lookup plus the extension specs to reuse on resume."""

    def __init__(
        self,
        tools: list[BaseTool],
        injected_tools: tuple[BaseTool, ...],
    ) -> None:
        super().__init__((tool.name, tool) for tool in tools)
        self.injected_tools = injected_tools


def _preconfigured_request_tools(ctx: ToolContext) -> tuple[BaseTool, ...]:
    """Read and validate request-local tools supplied before this build."""
    request_tools = ctx.request_tools
    if isinstance(request_tools, _BuiltRequestToolMap):
        candidates = tuple(
            (tool.name, tool) for tool in request_tools.injected_tools
        )
    elif isinstance(request_tools, dict):
        candidates = tuple(request_tools.items())
    else:
        raise ValueError("Invalid request tool snapshot")

    from src.agent.core.toolkit.tool_spec import ToolSpec

    tools: list[BaseTool] = []
    seen_names: set[str] = set()
    for name, tool in candidates:
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(tool, ToolSpec)
            or tool.name != name
            or tool.mcp_info is not None
            or tool.name in seen_names
        ):
            raise ValueError("Invalid request tool snapshot")
        seen_names.add(tool.name)
        tools.append(tool)
    return tuple(tools)


def build_tool_specs(
    payload: Any,
    *,
    skills_runtime: Any = None,
    mcp_tools: tuple = (),
    tool_context: ToolContext | None = None,
    skill_importer: Any = None,
) -> list[BaseTool]:
    """Instantiate request-scoped tools, inject ctx, and keep them out of the registry.

    Creates fresh tool instances every call so concurrent requests never share
    ctx or per-request config (top_k / kb_ids / credentials). Per-request
    instances are NOT written to the global _TOOL_REGISTRY — that registry is
    populated with default instances for permission/HITL metadata and legacy
    direct-tool lookup. Agent dispatch passes the request's ToolSpec instance
    to ToolRouter, so the configured instance is executed and audited without
    global registration. The per-request ToolContext is injected via
    ToolSpec._ctx PrivateAttr.

    Args:
        payload: ChatRequest (or compatible). Reads top_k, kb_ids,
            relevance_threshold, tool_keys.tavily, permission_mode, session_id.
    """
    ensure_default_tools_registered()
    skills_mode = getattr(payload, "skills_mode", "off") or "off"
    if skills_runtime is None and tool_context is not None:
        skills_runtime = getattr(tool_context, "skills_runtime", None)
    if skills_mode == "off":
        if skills_runtime is not None and getattr(skills_runtime, "mode", "off") != "off":
            raise ValueError("Skills runtime does not match request mode")
        if skills_runtime is None:
            from src.agent.skill_runtime import build_skills_runtime
            skills_runtime = build_skills_runtime("off")
    elif skills_runtime is None or getattr(skills_runtime, "mode", None) != skills_mode:
        raise ValueError("Skills mode requires its matching request runtime")
    ctx = tool_context or _build_tool_ctx(payload, skills_runtime)
    ctx.skills_mode = skills_mode
    ctx.skills_runtime = skills_runtime
    request_tools = _preconfigured_request_tools(ctx)
    tool_list = _assemble_all_tools(payload)
    existing_names = {tool.name for tool in tool_list}
    if any(tool.name in existing_names or tool.mcp_info is None for tool in mcp_tools):
        raise ValueError("Invalid external tool snapshot")
    tool_list.extend(mcp_tools)
    if getattr(ctx, "tool_observation_scope", None) is not None:
        from src.agent.tools.groups.retrieval.read_tool_observation import (
            ReadToolObservationTool,
        )

        if any(tool.name == "read_tool_observation" for tool in tool_list):
            raise ValueError(
                "Tool observation reader name conflicts with a registered tool"
            )
        tool_list.append(ReadToolObservationTool())

    from src.agent.skill_runtime import (
        ImportSkillTool,
        LoadSkillTool,
        PreviewSkillImportTool,
        ReadSkillResourceTool,
    )
    skill_tools = [
        LoadSkillTool(skills_runtime),
        ReadSkillResourceTool(skills_runtime),
        PreviewSkillImportTool(skills_runtime, skill_importer),
        ImportSkillTool(skills_runtime, skill_importer),
    ]
    existing_names = {tool.name for tool in tool_list}
    if any(tool.name in existing_names for tool in skill_tools):
        raise ValueError("Skills tool name conflicts with a registered tool")
    tool_list.extend(skill_tools)
    tool_list = _filter_disabled_tools(tool_list)
    if skills_mode != "off":
        required_skill_tools = {"load_skill", "read_skill_resource"}
        missing_skill_tools = required_skill_tools - {tool.name for tool in tool_list}
        if missing_skill_tools:
            raise ValueError("Required Skills tools are disabled")
    if request_tools:
        existing_names = {tool.name for tool in tool_list}
        if any(tool.name in existing_names for tool in request_tools):
            raise ValueError("request tool name conflict")
        tool_list.extend(_filter_disabled_tools(list(request_tools)))
    # Recoverable tool observations are request/session scoped opaque refs and
    # are useful outside Skills mode too. The tool itself revalidates the owner,
    # session, request and deletion epoch; registration does not grant content.
    seen_tool_names: set[str] = set()
    duplicate_tool_names: set[str] = set()
    for tool in tool_list:
        if tool.name in seen_tool_names:
            duplicate_tool_names.add(tool.name)
        else:
            seen_tool_names.add(tool.name)
    if duplicate_tool_names:
        duplicate_names = ", ".join(sorted(duplicate_tool_names))
        raise ValueError(
            f"Duplicate tool names in final request tool list: {duplicate_names}"
        )
    request_tool_ids = {id(tool) for tool in request_tools}
    included_request_tools = tuple(
        tool for tool in tool_list if id(tool) in request_tool_ids
    )
    ctx.request_tools = _BuiltRequestToolMap(tool_list, included_request_tools)
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
    """Instantiate all 27 tools grouped by their subdirectory.

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
    from src.agent.tools.groups.retrieval.read_context_event import ReadContextEventTool
    from src.agent.tools.groups.retrieval.read_tool_event import ReadToolEventTool
    from src.agent.tools.groups.retrieval.read_tool_observation import ReadToolObservationTool
    from src.agent.tools.groups.retrieval.read_rolling_history import (
        ReadRollingHistoryTool,
    )
    from src.agent.tools.groups.retrieval.read_owner_rolling_history import (
        ReadOwnerRollingHistoryTool,
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
            ReadContextEventTool(),
            ReadToolEventTool(),
            ReadToolObservationTool(),
            ReadRollingHistoryTool(),
            ReadOwnerRollingHistoryTool(),
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
    """Return metadata for all 27 Agent tools (name/description/group/enabled).

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
    with default instances for permission/HITL metadata and legacy direct-tool
    lookup; Agent dispatch uses the request-local tool instance instead.
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


def _build_tool_ctx(payload: Any, skills_runtime: Any = None) -> ToolContext:
    """Build ToolContext from payload's permission_mode + session_id."""
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.retrieval.source_registry import RagSourceRegistry
    mode = getattr(payload, "permission_mode", "default") or "default"
    session_id = getattr(payload, "session_id", "default") or "default"
    kb_ids = getattr(payload, "kb_ids", None)
    kb_scope = tuple(kb_ids) if kb_ids else None
    skills_mode = getattr(payload, "skills_mode", "off") or "off"
    return ToolContext(
        permission_mode=mode,
        session_id=session_id,
        skills_mode=skills_mode,
        skills_runtime=skills_runtime,
        kb_scope=kb_scope,
        rag_source_registry=RagSourceRegistry(),
    )


def _build_local_tools() -> list[BaseTool]:
    """Backward-compat: file_ops + execution flat list (ctx injected by build_tool_specs)."""
    return _build_local_file_ops_tools() + _build_local_execution_tools()


def _build_local_file_ops_tools() -> list[BaseTool]:
    """Instantiate all 8 file_ops tools (apply_patch disabled)."""
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

def build_agent_config(session_id: str, *, request_id: str | None = None) -> dict:
    """Build the config dict passed to agent.astream().

    - recursion_limit: 20-iteration hard cap (see prompts.MAX_RECURSION).
    - configurable.thread_id: legacy per-session key or optional request graph key.
    - request_id: optional host-minted ID for an isolated graph thread; it does
      not grant authentication or permission.
    """
    if request_id is not None:
        from src.agent.request_context import (
            REQUEST_GRAPH_BINDING_KEY,
            new_request_graph_binding,
        )

        binding = new_request_graph_binding(session_id, request_id)
        return {
            "recursion_limit": MAX_RECURSION,
            "configurable": {
                "thread_id": binding.graph_thread_id,
                REQUEST_GRAPH_BINDING_KEY: binding.as_config_value(),
            },
        }
    return {
        "recursion_limit": MAX_RECURSION,
        "configurable": {"thread_id": session_id or "default"},
    }
