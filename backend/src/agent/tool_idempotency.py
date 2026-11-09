"""Tool-level idempotency ledger for one Run.

Guarantees implemented here:

* one ``(run_id, call_id)`` executes at most once; a duplicate dispatch gets
  the recorded terminal result instead of a second execution;
* the dispatch hook writes ``started`` before the tool runs, so a crash leaves
  a durable unknown instead of a silently retried side effect;
* read-only calls may be replayed after an unknown outcome;
* local writes record a pre/post file digest, and are only replayed when the
  target is still at the recorded version;
* commands, deletes, firmware flashing and external/MCP calls are never
  retried automatically after an unknown outcome.

The ledger never stores raw arguments or raw results: arguments are reduced to
a SHA-256 digest plus a redacted target summary, and the replay envelope is
sanitized (credential-like keys and values removed) and bounded.

This module deliberately depends on the dict-row store protocol from
``src.agent.run_events``; persistence lives in ``app.db.run_store``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.agent.core.toolkit.tool_result_envelope import (
    ErrorDetail,
    ResultMetadata,
    ToolResultEnvelope,
)

logger = logging.getLogger(__name__)

# ── Side-effect categories ────────────────────────────────────────────────

SIDE_EFFECT_READ_ONLY = "read_only"
SIDE_EFFECT_LOCAL_WRITE = "local_write"
SIDE_EFFECT_COMMAND = "command"
SIDE_EFFECT_DELETE = "delete"
SIDE_EFFECT_HARDWARE = "hardware"
SIDE_EFFECT_EXTERNAL = "external"
SIDE_EFFECT_MCP = "mcp"
SIDE_EFFECT_OTHER = "other"

READ_ONLY_TOOLS = frozenset({
    "read_file",
    "list_files",
    "glob",
    "grep",
    "read_context_event",
    "read_tool_event",
    "read_tool_observation",
    "read_rolling_skill_history",
    "read_owner_rolling_history",
    "search_docs",
    "list_kb_docs",
    "search_history",
    "web_search",
    "webfetch",
    "view_image",
    "vision_analysis",
    "audit_pins",
    "hardware_profile",
    "memory_search",
    "load_skill",
    "read_skill_resource",
    "preview_skill_import",
    "render_code",
    "render_safety_report",
})

LOCAL_WRITE_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "multi_edit",
    "apply_patch",
    "undo_edit",
    "todo_write",
    "submit_plan",
    "enter_plan_mode",
    "memory_remember",
    "memory_forget",
    "install_github_skill",
    "import_skill",
})

COMMAND_TOOLS = frozenset({"run_command", "build_firmware"})
HARDWARE_TOOLS = frozenset({"flash_firmware"})
EXTERNAL_TOOLS = frozenset({"image_generation"})

# ── Ledger statuses / result codes ────────────────────────────────────────

STATUS_STARTED = "started"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

ACTION_EXECUTE = "execute"
ACTION_REPLAY = "replay"
ACTION_BLOCKED = "blocked"

TOOL_RESULT_UNKNOWN_CODE = "TOOL_RESULT_UNKNOWN"
TOOL_IN_PROGRESS_CODE = "TOOL_IN_PROGRESS"
IDEMPOTENCY_CONFLICT_CODE = "IDEMPOTENCY_CONFLICT"
IDEMPOTENCY_VERSION_CONFLICT_CODE = "IDEMPOTENCY_VERSION_CONFLICT"
IDEMPOTENCY_TARGET_CONFLICT_CODE = "IDEMPOTENCY_TARGET_CONFLICT"
IDEMPOTENCY_LEDGER_UNAVAILABLE_CODE = "IDEMPOTENCY_LEDGER_UNAVAILABLE"

RESULT_MAX_BYTES = 32 * 1024
TARGET_MAX_CHARS = 512
COMMAND_PREVIEW_CHARS = 300

_PATH_KEYS = ("path", "file_path")
_COMMAND_KEYS = ("command", "cmd")
_PORT_KEYS = ("port", "serial_port", "device")

_SENSITIVE_KEY_EXACT = frozenset({
    "auth",
    "authorization",
    "api_key",
    "apikey",
    "key",
    "keys",
    "token",
    "tokens",
    "secret",
    "secrets",
    "password",
    "passwd",
    "credential",
    "credentials",
    "private_key",
    "cookie",
    "cookies",
})
_SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_key",
    "_keys",
    "_token",
    "_secret",
    "_password",
    "_passwd",
    "_credential",
    "_credentials",
    "_private_key",
    "_cookie",
)
_KEY_SPLIT = re.compile(r"[^a-z0-9]+")
_KEY_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_REDACT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r'(--(?:token|key|password|secret|api[_-]?key)=)\S+', r'\1***REDACTED***'),
    (r'(--(?:token|key|password|secret|api[_-]?key)\s+)\S+', r'\1***REDACTED***'),
    (r'(Authorization:\s*Bearer\s+)\S+', r'\1***REDACTED***'),
    (r'((?:api[_-]?key|apikey|password|token|secret)\s*[:=]\s*)\S+', r'\1***REDACTED***'),
    (r'\bsk-[A-Za-z0-9_\-]{8,}\b', '***REDACTED***'),
)


def utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── Classification and redaction ──────────────────────────────────────────


def classify_side_effect(spec: Any, args: Mapping[str, Any] | None = None) -> str:
    """Return the conservative side-effect category for a tool call.

    Unknown tools fall through to ``other`` which is treated as non-replayable
    after an unknown result; the explicit read-only allowlist is the only path
    that permits a safe re-dispatch.
    """
    if getattr(spec, "mcp_info", None) is not None:
        return SIDE_EFFECT_MCP
    name = str(getattr(spec, "name", "") or "")
    if name in READ_ONLY_TOOLS:
        return SIDE_EFFECT_READ_ONLY
    if name in LOCAL_WRITE_TOOLS:
        return SIDE_EFFECT_LOCAL_WRITE
    if name in COMMAND_TOOLS:
        return SIDE_EFFECT_COMMAND
    if name in HARDWARE_TOOLS:
        return SIDE_EFFECT_HARDWARE
    if name in EXTERNAL_TOOLS:
        return SIDE_EFFECT_EXTERNAL
    lowered = name.lower()
    if "delete" in lowered or "remove" in lowered:
        return SIDE_EFFECT_DELETE
    if "flash" in lowered or "serial" in lowered or "upload" in lowered:
        return SIDE_EFFECT_HARDWARE
    _ = args
    return SIDE_EFFECT_OTHER


def redact_text(value: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value


def _normalize_key(key: Any) -> str:
    if not isinstance(key, str):
        return ""
    normalized = _KEY_CAMEL.sub("_", key)
    return _KEY_SPLIT.sub("_", normalized.lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    if not normalized:
        return False
    if normalized in _SENSITIVE_KEY_EXACT:
        return True
    return normalized.endswith(_SENSITIVE_KEY_SUFFIXES)


def sanitize_value(value: Any) -> Any:
    """Recursively remove credential-like keys and redact known value shapes."""
    if isinstance(value, Mapping):
        return {
            key: sanitize_value(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def args_hash(args: Mapping[str, Any] | None) -> str:
    """Digest raw arguments; the raw values themselves never reach storage."""
    try:
        payload = _canonical_json(dict(args or {}))
    except (TypeError, ValueError):
        payload = repr(args)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _first_arg(args: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _mcp_target(spec: Any) -> str | None:
    info = getattr(spec, "mcp_info", None)
    if not isinstance(info, Mapping):
        return None
    server = info.get("server_name") or info.get("server") or "mcp"
    tool = info.get("tool_name") or getattr(spec, "name", None) or "tool"
    return f"{server}/{tool}"[:TARGET_MAX_CHARS]


def extract_target(spec: Any, args: Mapping[str, Any], side_effect: str) -> str | None:
    """Build a redacted, bounded target summary (path / port / command).

    For local writes the resolved path is the canonical resource identity used
    by the started-target exclusion index; commands keep only a redacted
    preview and are never serialized by target.
    """
    if side_effect == SIDE_EFFECT_MCP:
        return _mcp_target(spec)
    if side_effect == SIDE_EFFECT_LOCAL_WRITE:
        resolved = _resolve_write_path(args)
        raw = _first_arg(args, _PATH_KEYS)
        if resolved:
            return resolved[:TARGET_MAX_CHARS]
        if raw:
            return redact_text(raw)[:TARGET_MAX_CHARS]
    parts: list[str] = []
    path = _first_arg(args, _PATH_KEYS)
    if path:
        parts.append(redact_text(path))
    port = _first_arg(args, _PORT_KEYS)
    if port:
        normalized_port = port.upper() if port.upper().startswith("COM") else port
        parts.append(f"port={redact_text(normalized_port)}")
    command = _first_arg(args, _COMMAND_KEYS)
    if command:
        parts.append(redact_text(command)[:COMMAND_PREVIEW_CHARS])
    if not parts:
        return None
    joined = " | ".join(dict.fromkeys(parts))
    return joined[:TARGET_MAX_CHARS]


# ── Local write version ───────────────────────────────────────────────────


def _resolve_write_path(args: Mapping[str, Any]) -> str | None:
    raw = _first_arg(args, _PATH_KEYS)
    if not raw:
        return None
    try:
        from src.agent.path_guard import resolve_agent_path, validate_path

        allowed, _reason = validate_path(raw, is_write=True)
        if not allowed:
            return None
        return resolve_agent_path(raw) or None
    except Exception:
        return None


def file_digest(path: str | None) -> str | None:
    if not path:
        return None
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return None
    return f"sha256:{hashlib.sha256(data).hexdigest()}:{len(data)}"


def initial_version(args: Mapping[str, Any]) -> str | None:
    path = _resolve_write_path(args)
    if not path:
        return None
    return _canonical_json({"path": path, "pre": file_digest(path), "post": None})


def updated_version(version_text: str | None, result: Mapping[str, Any]) -> str | None:
    if not version_text:
        return None
    try:
        version = json.loads(version_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(version, dict) or not version.get("path"):
        return None
    data = result.get("data")
    data_path = None
    if isinstance(data, Mapping):
        candidate = data.get("path") or data.get("file_path")
        if isinstance(candidate, str) and candidate:
            data_path = candidate
    path = data_path or version.get("path")
    version["path"] = path
    version["post"] = file_digest(path)
    return _canonical_json(version)


def version_allows_retry(record: Mapping[str, Any]) -> bool:
    """True when a local write provably did not change its target."""
    raw = record.get("version")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        version = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(version, dict):
        return False
    path = version.get("path")
    pre = version.get("pre")
    if not isinstance(path, str) or not path or not isinstance(pre, str) or not pre:
        return False
    current = file_digest(path)
    return current is not None and current == pre


def version_conflicts_on_replay(record: Mapping[str, Any]) -> bool:
    """True when the target changed after the recorded successful write."""
    raw = record.get("version")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        version = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(version, dict):
        return False
    path = version.get("path")
    post = version.get("post")
    if not isinstance(path, str) or not path or not isinstance(post, str) or not post:
        return False
    current = file_digest(path)
    return current is not None and current != post


def invocation_needs_review(record: Mapping[str, Any]) -> bool:
    """Whether a non-terminal invocation blocks automatic resume/replay."""
    if record.get("status") not in (STATUS_STARTED, STATUS_UNKNOWN):
        return False
    side_effect = record.get("side_effect")
    if side_effect == SIDE_EFFECT_READ_ONLY:
        return False
    if side_effect == SIDE_EFFECT_LOCAL_WRITE:
        return not version_allows_retry(record)
    return True


def public_invocation(record: Mapping[str, Any]) -> dict[str, Any]:
    """API-safe invocation summary (no args, no result body)."""
    return {
        "call_id": record.get("call_id"),
        "tool": record.get("tool_name"),
        "side_effect": record.get("side_effect"),
        "target": record.get("target"),
        "status": record.get("status"),
        "attempts": record.get("attempts"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "error_code": record.get("error_code"),
    }


# ── Result storage ────────────────────────────────────────────────────────


def sanitized_result(envelope: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    """Return (stored payload, canonical JSON, SHA-256) for the ledger."""
    sanitized = sanitize_value(dict(envelope))
    text = _dump_result(sanitized)
    if len(text.encode("utf-8")) > RESULT_MAX_BYTES:
        metadata = sanitized.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        metadata["result_truncated"] = True
        sanitized = {
            "success": bool(sanitized.get("success")),
            "output": str(sanitized.get("output") or "")[: RESULT_MAX_BYTES // 4],
            "data": None,
            "error": sanitized.get("error"),
            "metadata": metadata,
        }
        text = _dump_result(sanitized)
    return sanitized, text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump_result(value: Any) -> str:
    try:
        return _canonical_json(value)
    except (TypeError, ValueError):
        # Preserve the top-level outcome even when a nested value (NaN, a
        # custom object) cannot be serialized; the replay must not turn a
        # succeeded call into a failure.
        payload = {
            "success": bool(value.get("success")) if isinstance(value, Mapping) else False,
            "output": str(value.get("output") or "")[:4096]
            if isinstance(value, Mapping) else "",
            "data": None,
            "error": value.get("error") if isinstance(value, Mapping) else None,
        }
        try:
            return _canonical_json(payload)
        except (TypeError, ValueError):
            return '{"success":false,"output":"","data":null,"error":null}'


# ── Ledger coordinator ────────────────────────────────────────────────────


@dataclass
class LedgerDecision:
    """Outcome of inspecting the ledger before dispatch."""

    action: str
    record: Mapping[str, Any] | None = None
    result: dict[str, Any] | None = None
    audit_source: str = ""


class ToolInvocationLedger:
    """Coordinates the durable invocation row for one dispatch."""

    def __init__(
        self,
        *,
        store: Any,
        run_id: str,
        call_id: str,
        spec: Any,
        args: Mapping[str, Any],
        start_ms: int,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.call_id = call_id
        self.spec = spec
        self.args = dict(args or {})
        self.start_ms = start_ms
        self.side_effect = classify_side_effect(spec, self.args)
        self.args_hash = args_hash(self.args)
        self.target = extract_target(spec, self.args, self.side_effect)
        self._execute_retry = False
        self._version: str | None = None

    @classmethod
    def maybe(
        cls,
        *,
        run_id: str | None,
        call_id: str,
        spec: Any,
        args: Mapping[str, Any],
        start_ms: int,
    ) -> "ToolInvocationLedger | None":
        if not run_id:
            return None
        store: Any = None
        try:
            from src.agent import run_events

            store = run_events.get_run_store()
        except Exception:
            logger.warning("Tool idempotency store unavailable", exc_info=True)
        return cls(
            store=store,
            run_id=run_id,
            call_id=call_id,
            spec=spec,
            args=args,
            start_ms=start_ms,
        )

    # -- inspection -------------------------------------------------------

    def preflight(self) -> LedgerDecision:
        if self.store is None:
            if self.side_effect == SIDE_EFFECT_READ_ONLY:
                return LedgerDecision(ACTION_EXECUTE)
            return self._blocked(
                IDEMPOTENCY_LEDGER_UNAVAILABLE_CODE,
                "幂等账本不可用，已阻止有副作用的工具调用。",
                "请稍后重试；若工具可能已执行，请人工核对结果。",
                source="idempotency_guard",
            )
        try:
            record = self.store.get_tool_invocation(self.run_id, self.call_id)
        except Exception:
            logger.warning(
                "Tool invocation lookup failed run=%s call=%s",
                self.run_id, self.call_id, exc_info=True,
            )
            if self.side_effect == SIDE_EFFECT_READ_ONLY:
                return LedgerDecision(ACTION_EXECUTE)
            return self._blocked(
                IDEMPOTENCY_LEDGER_UNAVAILABLE_CODE,
                "幂等账本读取失败，已阻止有副作用的工具调用。",
                "请稍后重试；若工具可能已执行，请人工核对结果。",
                source="idempotency_guard",
            )
        if record is None:
            return LedgerDecision(ACTION_EXECUTE)
        return self._decide(record)

    def _decide(self, record: Mapping[str, Any]) -> LedgerDecision:
        if record.get("tool_name") != str(getattr(self.spec, "name", "") or ""):
            return self._blocked(
                IDEMPOTENCY_CONFLICT_CODE,
                "同一 call_id 被用于不同工具，已拒绝再次执行。",
                "请使用新的 call_id 发起新的工具调用。",
                source="idempotency_guard",
            )
        if record.get("args_hash") != self.args_hash:
            return self._blocked(
                IDEMPOTENCY_CONFLICT_CODE,
                "同一 call_id 使用了不同的参数，已拒绝再次执行。",
                "请使用新的 call_id 发起新的工具调用。",
                source="idempotency_guard",
            )
        status = record.get("status")
        if status in (STATUS_SUCCEEDED, STATUS_FAILED):
            if (
                status == STATUS_SUCCEEDED
                and self.side_effect == SIDE_EFFECT_LOCAL_WRITE
                and version_conflicts_on_replay(record)
            ):
                return self._blocked(
                    IDEMPOTENCY_VERSION_CONFLICT_CODE,
                    "记录结果写入后目标文件已被修改，不能重放旧结果。",
                    "请先核对文件当前内容，再决定是否重新编辑。",
                    source="idempotency_guard",
                )
            return LedgerDecision(
                ACTION_REPLAY,
                record=record,
                result=self._replay_result(record),
                audit_source="idempotent_replay",
            )
        # A live ``started`` row means another dispatcher is executing this
        # call right now. Read-only calls may safely repeat; every side effect
        # must wait for that call to finish instead of running twice.
        if status == STATUS_STARTED:
            if self.side_effect == SIDE_EFFECT_READ_ONLY:
                self._execute_retry = True
                return LedgerDecision(ACTION_EXECUTE, record=record)
            return self._blocked(
                TOOL_IN_PROGRESS_CODE,
                "该工具调用仍在执行，已阻止重复执行。",
                "请等待当前调用结束；若进程已重启，请先人工核对结果。",
                source="idempotency_guard",
            )
        # ``unknown`` after a crash: only provably safe cases may run again.
        if self.side_effect == SIDE_EFFECT_READ_ONLY:
            self._execute_retry = True
            return LedgerDecision(ACTION_EXECUTE, record=record)
        if (
            self.side_effect == SIDE_EFFECT_LOCAL_WRITE
            and version_allows_retry(record)
        ):
            self._execute_retry = True
            return LedgerDecision(ACTION_EXECUTE, record=record)
        try:
            self.store.mark_tool_invocation_unknown(
                self.run_id,
                self.call_id,
                error_code=TOOL_RESULT_UNKNOWN_CODE,
                error_message="tool started but produced no terminal result",
            )
        except Exception:
            logger.warning(
                "Marking tool invocation unknown failed run=%s call=%s",
                self.run_id, self.call_id, exc_info=True,
            )
        return self._blocked(
            TOOL_RESULT_UNKNOWN_CODE,
            "该工具调用已开始但结果未知，不能自动重试。",
            "请人工核对副作用后，使用新的 call_id 重新发起。",
            source="idempotency_guard",
        )

    # -- execution claim --------------------------------------------------

    def begin(self, decision: LedgerDecision) -> LedgerDecision | None:
        """Claim the execution slot. Returns a replay/blocked decision on race."""
        if decision.action != ACTION_EXECUTE:
            return decision
        if self.store is None:
            return None
        version = self._current_version()
        self._version = version
        if self._execute_retry:
            try:
                restarted = self.store.restart_tool_invocation(
                    self.run_id, self.call_id, version=version,
                )
            except Exception:
                logger.warning(
                    "Tool invocation restart failed run=%s call=%s",
                    self.run_id, self.call_id, exc_info=True,
                )
                return self._blocked(
                    IDEMPOTENCY_LEDGER_UNAVAILABLE_CODE,
                    "幂等账本不可用，已阻止有副作用的工具调用。",
                    "请稍后重试；若工具可能已执行，请人工核对结果。",
                    source="idempotency_guard",
                )
            if restarted is not None:
                return None
            return self._after_claim_failure()
        row = {
            "run_id": self.run_id,
            "call_id": self.call_id,
            "tool_name": str(getattr(self.spec, "name", "") or ""),
            "args_hash": self.args_hash,
            "side_effect": self.side_effect,
            "target": self.target,
            "status": STATUS_STARTED,
            "attempts": 1,
            "version": version,
            "started_at": utc_now_iso(),
        }
        try:
            inserted = self.store.begin_tool_invocation(row)
        except Exception:
            logger.warning(
                "Tool invocation insert failed run=%s call=%s",
                self.run_id, self.call_id, exc_info=True,
            )
            if self.side_effect == SIDE_EFFECT_READ_ONLY:
                return None
            return self._blocked(
                IDEMPOTENCY_LEDGER_UNAVAILABLE_CODE,
                "幂等账本写入失败，已阻止有副作用的工具调用。",
                "请稍后重试；若工具可能已执行，请人工核对结果。",
                source="idempotency_guard",
            )
        if inserted is not None:
            return None
        return self._after_claim_failure()

    def _after_claim_failure(self) -> LedgerDecision:
        """Never execute when this dispatch could not claim the ledger row.

        A lost insert/restart race means another dispatcher owns the call (or
        the resource target). Terminal records are replayed; everything else
        is blocked instead of running without a durable claim.
        """
        record = self._reread()
        if record is not None and record.get("status") in (
            STATUS_SUCCEEDED, STATUS_FAILED,
        ):
            return self._decide(record)
        conflict = self._find_target_conflict()
        if conflict is not None and conflict.get("call_id") != self.call_id:
            return self._blocked(
                IDEMPOTENCY_TARGET_CONFLICT_CODE,
                "同一资源上已有未完成的写操作，已阻止并发执行。",
                "请等待该操作完成后再试，或改用不同的目标。",
                source="idempotency_guard",
            )
        return self._blocked(
            TOOL_IN_PROGRESS_CODE,
            "该工具调用仍在执行或状态已被其他执行者抢占，已阻止重复执行。",
            "请等待当前调用结束；若进程已重启，请先人工核对结果。",
            source="idempotency_guard",
        )

    def _find_target_conflict(self) -> Mapping[str, Any] | None:
        if (
            not self.target
            or self.side_effect not in (SIDE_EFFECT_LOCAL_WRITE, SIDE_EFFECT_HARDWARE)
            or self.store is None
        ):
            return None
        finder = getattr(self.store, "find_started_tool_invocation", None)
        if finder is None:
            return None
        try:
            return finder(self.run_id, target=self.target)
        except Exception:
            logger.warning(
                "Tool target conflict lookup failed run=%s target=%s",
                self.run_id, self.target, exc_info=True,
            )
            return None

    def finish(
        self,
        envelope: ToolResultEnvelope,
        result: Mapping[str, Any],
        result_ref: str | None,
    ) -> None:
        if self.store is None:
            return
        status = STATUS_SUCCEEDED if envelope.success else STATUS_FAILED
        payload, text, digest = sanitized_result(result)
        version = (
            updated_version(self._version, payload)
            if self.side_effect == SIDE_EFFECT_LOCAL_WRITE else None
        )
        error = envelope.error
        try:
            self.store.finish_tool_invocation(
                self.run_id,
                self.call_id,
                status=status,
                result_hash=digest,
                result_ref=result_ref,
                result_json=text,
                error_code=error.error_type if error is not None else None,
                error_message=(
                    redact_text(error.error_message or "")
                    if error is not None else None
                ),
                version=version,
                finished_at=utc_now_iso(),
            )
        except Exception:
            # The row stays ``started``; recovery will surface it as unknown.
            logger.warning(
                "Tool invocation completion write failed run=%s call=%s",
                self.run_id, self.call_id, exc_info=True,
            )

    # -- helpers ----------------------------------------------------------

    def _current_version(self) -> str | None:
        if self.side_effect != SIDE_EFFECT_LOCAL_WRITE:
            return None
        return initial_version(self.args)

    def _reread(self) -> Mapping[str, Any] | None:
        try:
            return self.store.get_tool_invocation(self.run_id, self.call_id)
        except Exception:
            return None

    def _replay_result(self, record: Mapping[str, Any]) -> dict[str, Any]:
        raw = record.get("result_json")
        result: dict[str, Any] | None = None
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                result = parsed
        if result is None:
            error_code = (
                record.get("error_code")
                or (
                    TOOL_RESULT_UNKNOWN_CODE
                    if record.get("status") != STATUS_FAILED else "TOOL_FAILED"
                )
            )
            result = ToolResultEnvelope(
                success=record.get("status") == STATUS_SUCCEEDED,
                output="",
                data=None,
                error=ErrorDetail(
                    error_type=str(error_code),
                    error_message=str(record.get("error_message") or "已记录的工具结果不可用。"),
                    suggestion="请参考已记录的 call_id 与审计日志。",
                    retryable=False,
                ),
                metadata=ResultMetadata(
                    tool_name=str(record.get("tool_name") or ""),
                    duration_ms=0,
                    call_id=self.call_id,
                ),
            ).model_dump()
        metadata = result.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            result["metadata"] = metadata
        metadata["idempotent_replay"] = True
        metadata["idempotency_status"] = record.get("status")
        return result

    def _blocked(
        self,
        code: str,
        message: str,
        suggestion: str,
        *,
        source: str,
    ) -> LedgerDecision:
        envelope = ToolResultEnvelope(
            success=False,
            output="",
            data=None,
            error=ErrorDetail(
                error_type=code,
                error_message=message,
                suggestion=suggestion,
                retryable=False,
            ),
            metadata=ResultMetadata(
                tool_name=str(getattr(self.spec, "name", "") or ""),
                duration_ms=_now_ms() - self.start_ms,
                call_id=self.call_id,
            ),
        )
        result = envelope.model_dump()
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            metadata["idempotency_blocked"] = True
            metadata["side_effect"] = self.side_effect
        return LedgerDecision(ACTION_BLOCKED, result=result, audit_source=source)
