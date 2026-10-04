"""Request-local, one-shot approvals bound to exact MCP calls and instances.

This object is never accepted from an API payload or saved in a checkpoint.
Every external call still requires an explicit user decision in bypass mode.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Sequence

MAX_PENDING_CALLS = 128
MAX_ARGUMENT_BYTES = 65536


def _signature(call_id: str, name: str, args: dict) -> tuple[str, str, str]:
    if not isinstance(call_id, str) or not call_id or len(call_id) > 256:
        raise ValueError("Invalid confirmation call ID")
    if not isinstance(name, str) or not name or not isinstance(args, dict):
        raise ValueError("Invalid confirmation call")
    payload = json.dumps(args, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ValueError("Confirmation arguments exceed the limit")
    return call_id, name, payload


class MCPRequestAuthorizations:
    """Serialize confirmation decisions; dispatch consumes a matching grant once."""

    def __init__(self, specs: Sequence[Any]):
        self._bound_specs = {spec.name: spec for spec in specs}
        if len(self._bound_specs) != len(specs):
            raise ValueError("Duplicate external tool name")
        self._decisions: dict[str, tuple[tuple[str, str, str], str, str]] = {}
        self._consumed: set[str] = set()
        self._pending: tuple[str, str, str] | None = None
        self._revoked = False
        self._lock = threading.RLock()

    @property
    def pending_call_id(self) -> str | None:
        with self._lock:
            return self._pending[0] if self._pending else None

    def next_confirmation(self, calls: Sequence[dict], tools: dict, classifier: Any, ctx: Any) -> dict | None:
        """Return exactly one unreviewed call; no batch-wide user approval."""
        with self._lock:
            if self._revoked or not 0 < len(calls) <= MAX_PENDING_CALLS:
                raise ValueError("External request is no longer confirmable")
            signatures = [_signature(call.get("call_id"), call.get("name"), call.get("args")) for call in calls]
            if len({item[0] for item in signatures}) != len(signatures):
                raise ValueError("Duplicate pending call ID")
            for call, signature in zip(calls, signatures):
                call_id, name, _ = signature
                previous = self._decisions.get(call_id)
                if previous is not None:
                    if previous[0] != signature or call_id in self._consumed:
                        raise ValueError("Pending confirmation changed or was already executed")
                    continue
                if len(self._decisions) >= MAX_PENDING_CALLS:
                    raise ValueError("Confirmation budget exceeded")
                spec = tools.get(name)
                decision = "deny" if spec is None else classifier.check(spec, call["args"], ctx)
                if decision == "ask":
                    self._pending = signature
                    return call
                source = "path_deny" if decision == "deny" else "auto_allow"
                self._decisions[call_id] = (signature, decision, source)
            self._pending = None
            return None

    def validate_confirmation(self, call_id: str | None, calls: Sequence[dict]) -> bool:
        with self._lock:
            if self._revoked or self._pending is None or call_id != self._pending[0]:
                return False
            for call in calls:
                if call.get("call_id") == call_id:
                    try:
                        return _signature(call_id, call.get("name"), call.get("args")) == self._pending
                    except (ValueError, TypeError):
                        return False
            return False

    def resolve_confirmation(self, call_id: str, decision: str) -> None:
        with self._lock:
            if decision not in {"allow", "deny"} or self._pending is None or call_id != self._pending[0] or self._revoked:
                raise ValueError("Confirmation no longer matches the pending call")
            if call_id in self._decisions or call_id in self._consumed:
                raise ValueError("Confirmation was already resolved")
            self._decisions[call_id] = (self._pending, decision, "user_allow" if decision == "allow" else "user_deny")
            self._pending = None

    def dispatch_decision(self, call_id: str, spec: Any, args: dict) -> tuple[bool, str]:
        with self._lock:
            external = getattr(spec, "mcp_info", None) is not None
            if self._revoked:
                return False, "user_stop"
            if external and self._bound_specs.get(spec.name) is not spec:
                return False, "mcp_snapshot_mismatch"
            try:
                signature = _signature(call_id, spec.name, args)
            except (ValueError, TypeError):
                return False, "mcp_confirmation_required"
            previous = self._decisions.get(call_id)
            if previous is None:
                return (False, "mcp_confirmation_required") if external else (True, "auto_allow")
            if previous[0] != signature:
                return False, "mcp_confirmation_mismatch"
            if call_id in self._consumed:
                return False, "mcp_replay_blocked"
            self._consumed.add(call_id)
            return previous[1] == "allow", previous[2]

    def revoke(self) -> None:
        with self._lock:
            self._revoked = True
            self._pending = None
            self._decisions.clear()
