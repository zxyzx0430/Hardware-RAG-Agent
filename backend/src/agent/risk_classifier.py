"""
Risk classifier — V2 regex-based risk grading for Agent tool calls.

V2 changes (spec §2, industrial-tool-runtime-v2-design):
  - HIGH_RISK_KEYWORDS -> HIGH_RISK_PATTERNS (re.compile'd regexes)
  - LOW_RISK_KEYWORDS  -> LOW_RISK_PREFIXES (prefix match, safer than `in`)
  - reads settings.risk_extra_patterns (comma-separated) at call time

Used by PermissionClassifier._decide_high to grade run_command args:
safe commands (ls/cat/grep...) auto-allow, destructive ones (rm -rf/mkfs...)
stay HIGH and trigger HITL. file ops default to LOW (path_guard screens).

Spec §2.5. PLUR constraint: V2 regex blacklist + prefix whitelist.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from src.config.settings import settings

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Risk levels
# ═══════════════════════════════════════════

HIGH: str = "high"
MEDIUM: str = "medium"
LOW: str = "low"
NA: str = "n/a"

# ═══════════════════════════════════════════
# High-risk patterns (pre-compiled regex, spec §2.5)
# ═══════════════════════════════════════════

# Destructive / irreversible operations — always ask even in acceptEdits.
# Pre-compiled for performance; case-insensitive where casing varies.
HIGH_RISK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(-[a-z]*r[a-z]*\s+)+"),        # rm -r, rm -rf, rm -r -f
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"curl.*\|\s*(sh|bash)", re.IGNORECASE),
    re.compile(r">\s*/etc/|>\s*/sys/"),
    re.compile(r"\bkillall\b|\bkill\s+-9\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{.*\|.*&"),                    # fork bomb :(){...|...&}
)

# ═══════════════════════════════════════════
# Medium-risk keywords (kept as substring match, backward compat)
# ═══════════════════════════════════════════

# State-changing network/system operations — ask in default mode.
MEDIUM_RISK_KEYWORDS: tuple[str, ...] = (
    "pip install", "pip uninstall", "npm install", "npm uninstall",
    "git push", "git reset --hard", "git clean -f",
    "curl", "wget", "scp", "rsync",
    "docker", "systemctl", "taskkill",
    # 破坏性 git（新增）
    "git checkout -- ", "git restore ", "git stash drop",
    "git branch -D", "git branch -d", "git rebase",
    "git filter-branch", "git reflog expire",
    "git push --force", "git push -f",
)

# ═══════════════════════════════════════════
# Low-risk prefixes (whitelist, spec §2.5)
# ═══════════════════════════════════════════

# Read-only / local execution — auto-allow in acceptEdits.
# Prefix match (safer than substring: `rm -rf /tmp/ls` won't match `ls`).
# Note: cat/type removed — file reads must go through read_file (path_guard).
# Note: git branch removed — destructive flags (-D/-d) bypass LOW; route to MEDIUM.
LOW_RISK_PREFIXES: tuple[str, ...] = (
    "ls", "echo", "grep", "pwd", "wc",
    "git status", "git diff", "git log", "git show",
    "dir", "where",
)

# Tools whose risk is graded by pattern matching on their args.
COMMAND_TOOL: str = "run_command"
COMMAND_ARG_FIELD: str = "command"

# Tools that operate on files — path_guard handles the dangerous cases.
_FILE_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "edit_file",
})


# ═══════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════

def classify_risk(tool: str, args: dict[str, Any]) -> str:
    """Classify a tool call's risk level. Returns HIGH/MEDIUM/LOW/NA."""
    if tool == COMMAND_TOOL:
        return _classify_command(args.get(COMMAND_ARG_FIELD, ""))
    if tool in _FILE_TOOLS:
        return LOW
    return NA


def _classify_command(command: str) -> str:
    """Grade a shell command by regex/keyword matching (case-insensitive)."""
    cmd_lower = command.lower().strip()
    if not cmd_lower:
        return MEDIUM
    if _matches_high_risk(command):
        return HIGH
    if any(kw in cmd_lower for kw in MEDIUM_RISK_KEYWORDS):
        return MEDIUM
    if _matches_low_prefix(cmd_lower):
        return LOW
    return MEDIUM  # unknown commands default to medium (ask)


def _matches_high_risk(command: str) -> bool:
    """Return True if command matches any high-risk regex pattern."""
    for pattern in _all_high_patterns():
        if pattern.search(command):
            return True
    return False


def _matches_low_prefix(cmd_lower: str) -> bool:
    """Return True if command equals or starts with a known-safe prefix."""
    for prefix in LOW_RISK_PREFIXES:
        if cmd_lower == prefix or cmd_lower.startswith(prefix + " "):
            return True
    return False


def _all_high_patterns() -> tuple[re.Pattern[str], ...]:
    """Return hardcoded HIGH_RISK_PATTERNS plus settings-configured extras."""
    extra = _compile_extra_patterns()
    if extra:
        return HIGH_RISK_PATTERNS + extra
    return HIGH_RISK_PATTERNS


def _compile_extra_patterns() -> tuple[re.Pattern[str], ...]:
    """Compile settings.risk_extra_patterns (comma-separated) into regexes."""
    raw = getattr(settings, "risk_extra_patterns", "") or ""
    if not raw.strip():
        return ()
    return _compile_pattern_list(raw.split(","))


def _compile_pattern_list(pieces: list[str]) -> tuple[re.Pattern[str], ...]:
    """Compile each non-empty regex string, skipping invalid ones."""
    compiled: list[re.Pattern[str]] = []
    for piece in pieces:
        expr = piece.strip()
        if not expr:
            continue
        one = _compile_one_pattern(expr)
        if one is not None:
            compiled.append(one)
    return tuple(compiled)


def _compile_one_pattern(expr: str) -> re.Pattern[str] | None:
    """Compile one regex; return None and log on re.error."""
    try:
        return re.compile(expr)
    except re.error as exc:
        logger.warning("risk_extra_patterns invalid regex %r: %s", expr, exc)
        return None