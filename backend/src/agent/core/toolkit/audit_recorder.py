"""AuditRecorder — unified audit logging at the ToolRouter layer.

Called once at the end of ToolRouter.dispatch. Tool implementations no longer
self-audit in finally blocks (spec §REMOVED Requirements). This module reuses
the existing ToolAudit table via audit_logger.log_tool_call.

The legacy log_tool_call signature does not yet accept call_id / success /
error_type (Task 6 adds an alembic migration for those columns). We try the
extended call first and fall back to the legacy signature on TypeError so
this module is safe to land before the DB schema change.

Spec: industrial-tool-runtime §ADDED Requirements (AuditRecorder).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.agent.core.toolkit.tool_result_envelope import ToolResultEnvelope
from src.agent.core.toolkit.tool_spec import ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

# Substring patterns for sensitive arg field redaction. "key" was removed to
# avoid false positives on non-sensitive fields (keyboard / hotkey / primary_key);
# all secret-bearing fields in the project use api_key / tavily_api_key (matched
# by "api_key") or token / password / secret / credential directly.
_SENSITIVE_KEY_PATTERNS: set[str] = {"api_key", "apikey", "token", "password", "secret", "credential"}

# Arg field names whose value is a shell command — these may embed API keys
# inline (e.g. curl -H "Authorization: Bearer sk-...") and need value-level
# regex redaction in addition to the key-level redaction above.
_COMMAND_ARG_KEYS: set[str] = {"command", "cmd"}

# Regex patterns for redacting API keys / tokens embedded inside command strings.
# Order matters: more specific patterns (with prefix capture) run first.
# Replacement uses ***REDACTED*** to match _SENSITIVE_KEY_PATTERNS style.
_SENSITIVE_VALUE_PATTERNS: list[tuple[str, str]] = [
    # --token=xxx / --key=xxx / --password=xxx / --secret=xxx / --api-key=xxx
    (r'(--(?:token|key|password|secret|api[_-]?key)=)\S+', r'\1***REDACTED***'),
    # --token xxx / --key xxx / --password xxx / --secret xxx / --api-key xxx (space-separated)
    (r'(--(?:token|key|password|secret|api[_-]?key)\s+)\S+', r'\1***REDACTED***'),
    # Authorization: Bearer xxx
    (r'(Authorization:\s*Bearer\s+)\S+', r'\1***REDACTED***'),
    # api_key: xxx / apikey: xxx / password: xxx / token: xxx / secret: xxx (header-style with colon)
    (r'((?:api[_-]?key|password|token|secret):\s*)\S+', r'\1***REDACTED***'),
    # OpenAI-style API keys: sk- followed by 8+ word chars (avoids short false positives)
    (r'\bsk-[A-Za-z0-9_\-]{8,}\b', '***REDACTED***'),
]


def _redact_command_value(value: str) -> str:
    """Redact API keys / tokens embedded inside a command string."""
    for pattern, replacement in _SENSITIVE_VALUE_PATTERNS:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value


@dataclass
class AuditRecord:
    """Inputs for one ToolAudit row, passed from ToolRouter to AuditRecorder.

    Bundles the 7 dispatch-time fields so the recorder signature stays under
    the max-params limit (spec §code-quality). Constructed once in
    ToolRouter.dispatch, consumed once by AuditRecorder.record.
    """

    call_id: str
    spec: ToolSpec
    args: dict[str, Any]
    envelope: ToolResultEnvelope
    decision: str
    decision_source: str
    ctx: ToolContext


def _redact_args(args: dict) -> dict:
    """Redact sensitive values in tool args before logging.

    Two layers: key-name match → whole value redacted; command/cmd value →
    regex redact embedded API keys / tokens (sk- / Bearer / --token / ...).
    """
    if not isinstance(args, dict):
        return args
    redacted = {}
    for k, v in args.items():
        if any(p in k.lower() for p in _SENSITIVE_KEY_PATTERNS):
            redacted[k] = "***REDACTED***"
        elif k.lower() in _COMMAND_ARG_KEYS and isinstance(v, str):
            redacted[k] = _redact_command_value(v)
        else:
            redacted[k] = v
    return redacted


class AuditRecorder:
    """Records one ToolAudit row per ToolRouter.dispatch.

    DB write failures are swallowed (warning log only) so a flaky DB never
    breaks the Agent stream.
    """

    def record(self, record: AuditRecord) -> None:
        """Persist one audit row. Swallows all errors."""
        try:
            self._write(record)
        except Exception as exc:  # noqa: BLE001 — DB must not break the stream
            logger.warning(
                "audit_record_failed tool=%s call_id=%s err=%s",
                record.spec.name, record.call_id, exc,
            )

    def _write(self, record: AuditRecord) -> None:
        """Call log_tool_call with the extended fields; fall back to legacy."""
        from src.agent.audit_logger import log_tool_call

        common_kwargs: dict[str, Any] = {
            "session_id": record.ctx.session_id,
            "tool_name": record.spec.name,
            "args": _redact_args(record.args),
            "decision": record.decision,
            "decision_source": record.decision_source,
            "risk_level": record.spec.risk_level.value,
            "exit_code": _extract_exit_code(record.envelope),
            "duration_ms": record.envelope.metadata.duration_ms,
            "error": _extract_error_message(record.envelope),
        }
        try:
            log_tool_call(
                **common_kwargs,
                call_id=record.call_id,
                success=record.envelope.success,
                error_type=_extract_error_type(record.envelope),
            )
        except TypeError:
            # Legacy log_tool_call (pre-migration) — retry without new fields.
            log_tool_call(**common_kwargs)


def _extract_exit_code(envelope: ToolResultEnvelope) -> int | None:
    """Pull exit_code from envelope.data if present (run_command writes it)."""
    data = envelope.data or {}
    return data.get("exit_code")


def _extract_error_message(envelope: ToolResultEnvelope) -> str | None:
    """Pull error_message from envelope.error if present."""
    if envelope.error:
        return envelope.error.error_message or None
    return None


def _extract_error_type(envelope: ToolResultEnvelope) -> str | None:
    """Pull error_type from envelope.error if present."""
    if envelope.error:
        return envelope.error.error_type
    return None
