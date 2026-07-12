"""PermissionClassifier — risk grading + 3-state decision (allow/ask/deny).

Called from hitl_handler at the pre-ToolNode stage (i.e. after LangGraph's
`interrupt_before=["tools"]` fires and before ToolNode executes). It is NOT
called from ToolRouter.dispatch — permission is checked once, upstream of
the router.

Collaborators:
  * path_guard.validate_path  — MEDIUM branch (write_file/edit_file path).
  * risk_classifier.classify_risk — HIGH branch (run_command arg grading,
    v2 spec §2). Lets safe commands (ls/cat/grep) auto-allow while
    destructive ones (rm -rf/mkfs) stay HIGH and trigger HITL.

Spec: industrial-tool-runtime-v2 §2 (PermissionClassifier._decide_high).
"""
from __future__ import annotations

import logging
from typing import Any

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Decision vocabulary
# ═══════════════════════════════════════════

ALLOW: str = "allow"
ASK: str = "ask"
DENY: str = "deny"

# Permission modes (align with frontend useSettingsStore.permissionMode).
BYPASS_MODE: str = "bypassPermissions"
DEFAULT_MODE: str = "default"
ACCEPT_EDITS_MODE: str = "acceptEdits"

# Tools whose MEDIUM-risk branch needs path_guard validation on `path`.
_FILE_WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})
_PATH_ARG_FIELD: str = "path"

# run_command is graded by risk_classifier in the HIGH branch (v2 §2).
_COMMAND_TOOL: str = "run_command"

# Tools whose HIGH risk is auto-allowed (skipping HITL) because they are
# demo-critical operations with explicit user consent at a higher level.
# Empty since spec `optimize-workbench-ux-batch` Track D Task 8: flash_firmware
# reverted to HITL confirm (HIGH risk writes to device — user must approve
# via the confirm card before esptool runs).
_HITL_SKIP_TOOLS: frozenset[str] = frozenset()


class PermissionClassifier:
    """3-state permission gate evaluated per pending tool_call.

    Decision matrix (spec §PermissionClassifier + v2 §2):
      * bypassPermissions               -> allow (every tool)
      * risk_level == LOW               -> allow
      * risk_level == HIGH              -> _decide_high: run_command is graded
                                            by risk_classifier and may auto-
                                            allow safe commands (e.g. ls/cat);
                                            destructive commands and all other
                                            HIGH tools -> ask
      * risk_level == MEDIUM            -> path_guard.validate first;
                                            deny on path violation;
                                            acceptEdits -> allow;
                                            default     -> ask

    Note: HIGH risk does NOT always ask.  run_command can return ALLOW when
    risk_classifier classifies the concrete command as LOW risk.
    """

    def check(self, spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> str:
        """Return allow/ask/deny for a tool call. Pure, no side effects."""
        mode = ctx.permission_mode
        if mode == BYPASS_MODE:
            return ALLOW
        if spec.risk_level == RiskLevel.LOW:
            return ALLOW
        if spec.risk_level == RiskLevel.HIGH:
            return self._decide_high(spec, args, ctx)
        return self._decide_medium(spec, args, ctx)

    def _decide_high(self, spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> str:
        """HIGH risk: run_command graded by risk_classifier; whitelist skips HITL; others ask."""
        if spec.name == _COMMAND_TOOL:
            return self._grade_command(args)
        if spec.name in _HITL_SKIP_TOOLS:
            return ALLOW
        return ASK

    def _grade_command(self, args: dict[str, Any]) -> str:
        """Grade run_command via risk_classifier; LOW -> allow, else ask."""
        from src.agent.risk_classifier import LOW, classify_risk
        risk = classify_risk(_COMMAND_TOOL, args)
        if risk == LOW:
            return ALLOW
        return ASK

    def _decide_medium(self, spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> str:
        """MEDIUM risk: write_file/edit_file. Path-guard then route by mode."""
        if spec.name in _FILE_WRITE_TOOLS:
            deny = self._path_denied(args)
            if deny:
                return DENY
        if ctx.permission_mode == ACCEPT_EDITS_MODE:
            return ALLOW
        return ASK

    def _path_denied(self, args: dict[str, Any]) -> bool:
        """Return True if path_guard rejects the file path."""
        from src.agent.path_guard import validate_path
        path = str(args.get(_PATH_ARG_FIELD, ""))
        ok, reason = validate_path(path, is_write=True)
        if not ok:
            logger.info("permission_deny path tool=%s reason=%s", "write_file", reason)
            return True
        return False