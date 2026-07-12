"""ToolRouter — unified dispatch entry point (NO permission checks).

Dispatch flow per call:
  1. find tool in registry           -> TOOL_NOT_FOUND envelope on miss
  2. validate input_schema           -> INVALID_ARGS envelope on failure
  3. asyncio.wait_for(execute)       -> TIMEOUT / EXEC_ERROR envelope on raise
                                        (business-level exceptions caught inside
                                        execute are NOT intercepted here)
  4. wrap into ToolResultEnvelope
  5. output_schema soft-validation   -> warning log only (never blocks)
  6. long-output truncation          -> output > 8000 chars truncated,
                                        full payload kept in data,
                                        metadata.compacted = True
  7. audit_recorder.record           -> silent on DB failure
  8. return envelope.model_dump()

Loop detection was removed (v2 suggestion 3: converged to the streaming
layer in sse_helpers). Permission is checked UPSTREAM by
PermissionClassifier at the pre-ToolNode stage.

Permission is checked UPSTREAM by PermissionClassifier at the pre-ToolNode
stage — this router does not gate. ToolSpec._arun delegates here; dispatch
calls spec.execute (NOT _arun) so there is no recursion.

Spec: industrial-tool-runtime §ADDED Requirements (ToolRouter).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from src.agent.core.toolkit.audit_recorder import AuditRecord, AuditRecorder
from src.agent.core.toolkit.tool_result_envelope import (
    ErrorDetail,
    ResultMetadata,
    ToolResultEnvelope,
)
from src.agent.core.toolkit.tool_spec import ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Error tags (flow into ErrorDetail.error_type)
# ═══════════════════════════════════════════

ERR_NOT_FOUND: str = "TOOL_NOT_FOUND"
ERR_INVALID_ARGS: str = "INVALID_ARGS"
ERR_TIMEOUT: str = "TIMEOUT"
ERR_EXEC: str = "EXEC_ERROR"
ERR_OUTPUT_SCHEMA: str = "OUTPUT_SCHEMA_VIOLATION"

SUGGEST_INVALID_ARGS: str = "请检查参数类型与必填字段。"
SUGGEST_TIMEOUT: str = "请缩小查询范围或拆分任务后重试。"
SUGGEST_EXEC: str = "工具执行异常，请联系管理员或重试。"
SUGGEST_NOT_FOUND: str = "工具未注册，请检查工具名拼写。"

# v2 suggestion 1: long-output truncation threshold (chars).
# envelope.output above this is truncated; full payload stays in data.
OUTPUT_TRUNCATE_THRESHOLD: int = 8000
_TRUNCATE_SUFFIX: str = "...(已截断，完整数据见 data)"

# ═══════════════════════════════════════════
# Module-level registry
# ═══════════════════════════════════════════

_TOOL_REGISTRY: dict[str, ToolSpec] = {}
_default_router: "ToolRouter | None" = None


def register(spec: ToolSpec) -> None:
    """Register a ToolSpec instance under its `name`."""
    _TOOL_REGISTRY[spec.name] = spec
    logger.info("tool_registered name=%s risk=%s", spec.name, spec.risk_level.value)


def unregister(name: str) -> bool:
    """Remove a tool from the registry. Returns True if it was present.

    Used by MCPServerManager.stop to drop a disconnected server's tools
    (spec v2 §4.3 dynamic unregister).
    """
    existed = name in _TOOL_REGISTRY
    _TOOL_REGISTRY.pop(name, None)
    if existed:
        logger.info("tool_unregistered name=%s", name)
    return existed


def list_registered_tools() -> list[ToolSpec]:
    """Return all currently registered ToolSpec instances (snapshot)."""
    return list(_TOOL_REGISTRY.values())


@dataclass
class _DispatchState:
    """Mutable per-dispatch context passed between internal steps.

    Encapsulating call_id/spec/args/ctx/start_ms keeps every helper signature
    within the project's max-params <= 3 rule (spec §Code 规范).
    """
    call_id: str
    spec: ToolSpec
    args: dict[str, Any]
    ctx: ToolContext
    start_ms: int


class ToolRouter:
    """Unified dispatch entry point. Does NOT check permissions."""

    def __init__(self, audit_recorder: AuditRecorder) -> None:
        self.audit_recorder = audit_recorder

    @classmethod
    def get_default(cls) -> "ToolRouter":
        """Return a process-wide default router with a fresh recorder.

        Used by ToolSpec._arun when the agent_factory has not injected a
        per-request router. Per-request routers are preferred for production flows.
        """
        global _default_router
        if _default_router is None:
            _default_router = cls(AuditRecorder())
        return _default_router

    async def dispatch(
        self,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolContext,
        decision: str = "allow",
        decision_source: str = "auto_allow",
    ) -> dict[str, Any]:
        """8-step flow. Returns envelope.model_dump() — always a dict."""
        start_ms = _now_ms()
        spec = _TOOL_REGISTRY.get(tool_name)
        if spec is None:
            return _not_found_envelope(tool_name, call_id, start_ms).model_dump()
        state = _DispatchState(call_id, spec, args, ctx, start_ms)
        envelope = await self._run_pipeline(state)
        self.audit_recorder.record(
            AuditRecord(call_id, spec, args, envelope, decision, decision_source, ctx)
        )
        return envelope.model_dump()

    async def _run_pipeline(self, state: _DispatchState) -> ToolResultEnvelope:
        """validate args -> exec -> wrap -> soft-validate output -> truncate."""
        invalid = _validate_args(state)
        if invalid is not None:
            return invalid
        result, error = await self._run_with_timeout(state)
        envelope = _build_envelope(state, result, error)
        self._validate_output_schema(state, envelope)
        return self._truncate_long_output(envelope)

    async def _run_with_timeout(
        self, state: _DispatchState
    ) -> tuple[dict[str, Any] | None, tuple[str, Exception] | None]:
        """Execute spec.execute under asyncio.wait_for with retry on timeout.

        Returns (result_dict, None) on success, (None, (error_tag, exc)) on
        non-business failure. Business-level exceptions caught inside execute
        never reach here — they are returned as part of result_dict.
        """
        timeout = state.spec.timeout_seconds
        last_exc: Exception | None = None
        for _ in range(state.spec.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    state.spec.execute(state.args, state.ctx),
                    timeout=timeout,
                )
                return (result, None)
            except asyncio.TimeoutError as exc:
                last_exc = exc
                continue
            except Exception as exc:  # noqa: BLE001 — non-business, surface to envelope
                return (None, (ERR_EXEC, exc))
        return (None, (ERR_TIMEOUT, last_exc or asyncio.TimeoutError()))

    def _validate_output_schema(
        self, state: _DispatchState, envelope: ToolResultEnvelope
    ) -> None:
        """Soft-validate envelope.data against spec.output_schema.

        Warning-only: logs a mismatch but NEVER blocks the call (spec v2
        suggestion 1). Skipped for error envelopes and tools without schema.
        """
        schema = getattr(state.spec, "output_schema", None)
        if schema is None or not envelope.success or envelope.data is None:
            return
        try:
            schema(**envelope.data)
        except Exception as exc:  # noqa: BLE001 — soft, never raise
            logger.warning(
                "output_schema_violation tool=%s err=%s", state.spec.name, exc
            )

    def _truncate_long_output(
        self, envelope: ToolResultEnvelope
    ) -> ToolResultEnvelope:
        """Truncate envelope.output above OUTPUT_TRUNCATE_THRESHOLD.

        Full payload is preserved in envelope.data; metadata.compacted is
        set so the frontend knows the LLM-facing text was trimmed.
        """
        if len(envelope.output) <= OUTPUT_TRUNCATE_THRESHOLD:
            return envelope
        envelope.output = envelope.output[:OUTPUT_TRUNCATE_THRESHOLD] + _TRUNCATE_SUFFIX
        envelope.metadata.compacted = True
        return envelope


# ═══════════════════════════════════════════
# Envelope builders (pure helpers)
# ═══════════════════════════════════════════


def _now_ms() -> int:
    """Current wall-clock time in milliseconds (int)."""
    return int(time.time() * 1000)


def _validate_args(state: _DispatchState) -> ToolResultEnvelope | None:
    """Validate args against spec.args_schema. Returns error envelope or None."""
    schema = getattr(state.spec, "args_schema", None)
    if schema is None:
        return None
    try:
        schema(**state.args)
        return None
    except Exception as exc:  # noqa: BLE001 — surface as INVALID_ARGS envelope
        logger.info("invalid_args tool=%s err=%s", state.spec.name, exc)
        return _error_envelope(state, ERR_INVALID_ARGS, str(exc), SUGGEST_INVALID_ARGS)


def _build_envelope(
    state: _DispatchState,
    result: dict[str, Any] | None,
    error: tuple[str, Exception] | None,
) -> ToolResultEnvelope:
    """Wrap execute result or error into a ToolResultEnvelope."""
    if error is not None:
        tag, exc = error
        return _error_envelope(state, tag, str(exc), _suggestion_for(tag))
    return _success_envelope(state, result or {})


def _success_envelope(
    state: _DispatchState, result: dict[str, Any]
) -> ToolResultEnvelope:
    """Build a success envelope from execute's result dict.

    Extracts `kb_coverage_hint` (populated by search_docs) from the result
    dict into envelope.metadata. All other non-`output` keys flow into `data`.
    """
    output = str(result.get("output", ""))
    hint = result.get("kb_coverage_hint")
    excluded = ("output", "kb_coverage_hint")
    data = {k: v for k, v in result.items() if k not in excluded} or None
    return ToolResultEnvelope(
        success=True,
        output=output,
        data=data,
        error=None,
        metadata=_metadata(state, hint),
    )


def _error_envelope(
    state: _DispatchState,
    error_type: str,
    message: str,
    suggestion: str,
) -> ToolResultEnvelope:
    """Build a failure envelope. output is empty so the LLM is not confused."""
    retryable = error_type in (ERR_TIMEOUT, ERR_EXEC)
    return ToolResultEnvelope(
        success=False,
        output="",
        data=None,
        error=ErrorDetail(
            error_type=error_type,
            error_message=message,
            suggestion=suggestion,
            retryable=retryable,
        ),
        metadata=_metadata(state),
    )


def _not_found_envelope(tool_name: str, call_id: str, start_ms: int) -> ToolResultEnvelope:
    """Build a TOOL_NOT_FOUND envelope (used before _DispatchState exists)."""
    return ToolResultEnvelope(
        success=False,
        output="",
        data=None,
        error=ErrorDetail(
            error_type=ERR_NOT_FOUND,
            error_message=f"tool '{tool_name}' is not registered",
            suggestion=SUGGEST_NOT_FOUND,
            retryable=False,
        ),
        metadata=ResultMetadata(
            tool_name=tool_name,
            duration_ms=_now_ms() - start_ms,
            call_id=call_id,
        ),
    )


def _metadata(state: _DispatchState, hint: str | None = None) -> ResultMetadata:
    """Build ResultMetadata with elapsed time + optional coverage hint."""
    return ResultMetadata(
        tool_name=state.spec.name,
        duration_ms=_now_ms() - state.start_ms,
        call_id=state.call_id,
        kb_coverage_hint=hint,
    )


def _suggestion_for(tag: str) -> str:
    """Map an error tag to a user-facing suggestion string."""
    mapping: dict[str, str] = {
        ERR_TIMEOUT: SUGGEST_TIMEOUT,
        ERR_EXEC: SUGGEST_EXEC,
        ERR_INVALID_ARGS: SUGGEST_INVALID_ARGS,
        ERR_NOT_FOUND: SUGGEST_NOT_FOUND,
    }
    return mapping.get(tag, SUGGEST_EXEC)
