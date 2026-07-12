"""PermissionMatcher — fnmatch-based fine-grained command permission.

Complements risk_classifier (which returns HIGH/MEDIUM/LOW) by returning
explicit allow/ask/deny decisions per command pattern. Used by hitl_handler
to short-circuit allow/deny before falling back to PermissionClassifier.

Security: splits commands on ;/&&/||/| and takes the STRICTEST decision
(deny > ask > allow) so injected payloads like `ls; rm -rf /` cannot sneak
through on the safe prefix of the first segment.

Spec: P1 task 7 (细粒度权限模式匹配).
"""
from __future__ import annotations

import fnmatch
import logging
from typing import Literal

logger = logging.getLogger(__name__)

PermissionDecision = Literal["allow", "ask", "deny"]

# ═══════════════════════════════════════════
# Decision ranking (strictness) — higher = stricter
# ═══════════════════════════════════════════

_DECISION_RANK: dict[str, int] = {"allow": 0, "ask": 1, "deny": 2}
_RANK_TO_DECISION: tuple[str, ...] = ("allow", "ask", "deny")

# ═══════════════════════════════════════════
# Default command rules (fnmatch patterns)
# ═══════════════════════════════════════════

DEFAULT_COMMAND_RULES: dict[str, str] = {
    "ls *": "allow",
    "pwd": "allow",
    "cat *": "allow",
    "git status": "allow",
    "grep *": "allow",
    "find *": "allow",
    "echo *": "allow",
    "rm *": "ask",
    "del *": "ask",
    "format *": "deny",
    "shutdown *": "deny",
    "regedit *": "deny",
}

# ═══════════════════════════════════════════
# Command splitters (prevent injection)
# ═══════════════════════════════════════════

_COMMAND_SEPARATORS: tuple[str, ...] = (";", "&&", "||", "|")

# Tool name that carries a shell command
_COMMAND_TOOL: str = "run_command"
_COMMAND_ARG_FIELD: str = "command"

# Default decision when no rule matches
_DEFAULT_DECISION: PermissionDecision = "ask"


class PermissionMatcher:
    """fnmatch-based permission matcher for run_command tool calls.

    Non-command tools return ``ask`` so the caller (hitl_handler) falls
    through to the risk-based PermissionClassifier.
    """

    def __init__(self, rules: dict[str, str] | None = None) -> None:
        self._rules: dict[str, str] = dict(rules) if rules is not None else dict(DEFAULT_COMMAND_RULES)

    def check(self, tool_name: str, tool_input: dict) -> PermissionDecision:
        """Return allow/ask/deny for a tool call. Non-command tools -> ask."""
        if tool_name != _COMMAND_TOOL:
            return _DEFAULT_DECISION
        command = str(tool_input.get(_COMMAND_ARG_FIELD, ""))
        return self._match_command(command)

    def _match_command(self, command: str) -> PermissionDecision:
        """Match command against rules. Splits on separators, strictest wins."""
        segments = _split_command(command)
        if not segments:
            return _DEFAULT_DECISION
        decisions = [self._match_single(seg) for seg in segments]
        return _strictest(decisions)

    def _match_single(self, segment: str) -> PermissionDecision:
        """Match one segment. Last matching rule wins; default ask."""
        seg = segment.strip()
        if not seg:
            return "allow"
        decision: PermissionDecision = _DEFAULT_DECISION
        for pattern, verdict in self._rules.items():
            if _pattern_matches(seg, pattern):
                decision = verdict  # type: ignore[assignment]
        return decision


# ═══════════════════════════════════════════
# Pure helpers
# ═══════════════════════════════════════════


def _pattern_matches(seg: str, pattern: str) -> bool:
    """Case-insensitive fnmatch (Windows commands are case-insensitive)."""
    return fnmatch.fnmatchcase(seg.lower(), pattern.lower())


def _split_command(command: str) -> list[str]:
    """Split a command string on shell separators (;/&&/||/|)."""
    pieces: list[str] = [command]
    for sep in _COMMAND_SEPARATORS:
        pieces = _flatten_split(pieces, sep)
    return [p for p in pieces if p.strip()]


def _flatten_split(pieces: list[str], sep: str) -> list[str]:
    """Split every piece on ``sep`` and flatten into a single list."""
    out: list[str] = []
    for piece in pieces:
        out.extend(piece.split(sep))
    return out


def _strictest(decisions: list[PermissionDecision]) -> PermissionDecision:
    """Return the strictest decision (deny > ask > allow)."""
    if not decisions:
        return _DEFAULT_DECISION
    best_rank = -1
    best: PermissionDecision = "allow"
    for d in decisions:
        rank = _DECISION_RANK.get(d, 1)
        if rank > best_rank:
            best_rank = rank
            best = d
    return best


__all__ = ["PermissionMatcher", "PermissionDecision", "DEFAULT_COMMAND_RULES"]
