"""
Path guard — validates file paths for Agent local tools.

Two layers (spec §7.2):
  1. Allowed directories: project root + dedicated sandbox temp dir.
  2. Deny patterns: sensitive files/dirs blocked in every permission mode.

Uses os.path.realpath() to resolve symlinks and ../ traversals before checks.
PLUR constraint: sandbox tools must use absolute paths; forced deny paths.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import tempfile
from pathlib import Path

from src.config.settings import ROOT_DIR

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

# ROOT_DIR points to backend/; the project root is one level up.
PROJECT_ROOT: Path = ROOT_DIR.parent
SANDBOX_DIR_NAME: str = "agent-sandbox"
SANDBOX_TEMP_DIR: Path = Path(tempfile.gettempdir()) / SANDBOX_DIR_NAME

# Directories where Agent file tools may operate.
ALLOWED_DIRS: tuple[Path, ...] = (PROJECT_ROOT, SANDBOX_TEMP_DIR)

# Path components blocked unconditionally in every permission mode.
# Protects version control, IDE config, secrets, and credentials.
# Simple patterns (no "/") are matched per path component via fnmatch.
# Multi-component patterns (containing "/") are matched as case-insensitive
# substrings against the separator-normalized full path (Windows OS paths),
# and trailing-slash single-component patterns also match the component name
# so the directory itself (no trailing separator) is blocked too.
DENY_PATTERNS: tuple[str, ...] = (
    # 版本控制 / IDE
    ".git", ".vscode", ".idea", ".claude",
    # 配置文件
    "settings.json", ".env",
    # 证书/密钥（有扩展名）
    "*.key", "*.pem", "*.pfx", "*.p12", "*.keystore", "*.jks", "*.kdbx",
    # 凭证文件名（无扩展名）
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    # 凭证目录
    ".ssh/", ".aws/", ".gcloud/", ".azure/",
    # dotfile 凭证
    ".gitconfig", ".npmrc", ".pypirc", ".netrc",
    ".bash_history", ".zsh_history", "*credentials*",
    # Windows 系统目录
    "Windows/", "System32/", "SysWOW64/", "Program Files/",
    "Program Files (x86)/", "ProgramData/", "$Recycle.Bin/",
    "Boot/bootmgr", "bootmgr", "BOOTSECT.BAK",
    # Linux 系统目录（跨平台）
    "/etc/", "/root/",
)

# 8.3 short-name component guard (e.g. WINDOWS~1, SYSTEM32~1).
SHORT_NAME_PATTERN: re.Pattern[str] = re.compile(r"~\d+$")

# Pre-split deny patterns for the two matching strategies.
_DENY_SIMPLE: tuple[str, ...] = tuple(p for p in DENY_PATTERNS if "/" not in p)
_DENY_SLASHED: tuple[str, ...] = tuple(p for p in DENY_PATTERNS if "/" in p)

# Path traversal sequence — blocked before realpath resolution too.
TRAVERSAL_SEQ: str = ".."


# ═══════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════

def validate_path(path: str, is_write: bool) -> tuple[bool, str]:
    """Validate a path for Agent tool access. Returns (ok, reason).

    is_write is accepted for future read/write differentiation; v2 applies
    the same deny + allowed-dir checks to both.
    """
    _ = is_write  # reserved for future write-specific restrictions
    if not path:
        return False, "path is empty"
    if _has_traversal_component(path):
        return False, "path traversal (..) is not allowed"
    real = _realpath(path)
    if not real:
        return False, "path could not be resolved"
    return _check_access(real)


def is_in_allowed_dir(path: str) -> bool:
    """Check whether a realpath is inside one of ALLOWED_DIRS."""
    p = Path(path)
    for allowed in ALLOWED_DIRS:
        try:
            p.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


def matches_deny_pattern(path: str) -> str | None:
    """Return the matched deny pattern if any, else None."""
    parts = Path(path).parts
    return (
        _detect_short_name(parts)
        or _match_simple_patterns(parts)
        or _match_slash_patterns(path, parts)
    )


def _detect_short_name(parts: tuple[str, ...]) -> str | None:
    """Return marker if any component looks like an 8.3 short name (~N)."""
    for part in parts:
        if SHORT_NAME_PATTERN.search(part):
            return "8.3 short name detected"
    return None


def _match_simple_patterns(parts: tuple[str, ...]) -> str | None:
    """Match slash-free deny patterns against each component via fnmatch."""
    for part in parts:
        for pat in _DENY_SIMPLE:
            if fnmatch.fnmatch(part, pat) or part == pat:
                return pat
    return None


def _match_slash_patterns(path: str, parts: tuple[str, ...]) -> str | None:
    """Match slash patterns by substring and by trailing-slash component."""
    path_lower = path.replace("\\", "/").lower()
    for pat in _DENY_SLASHED:
        hit = _match_one_slash_pattern(pat, path_lower, parts)
        if hit:
            return hit
    return None


def _match_one_slash_pattern(
    pat: str, path_lower: str, parts: tuple[str, ...]
) -> str | None:
    """Slash pattern: substring match, plus trailing-slash component name."""
    if pat.lower() in path_lower:
        return pat
    name = pat.rstrip("/")
    if name and "/" not in name and _part_matches(parts, name):
        return pat
    return None


def _part_matches(parts: tuple[str, ...], name: str) -> bool:
    """True if any component equals or fnmatches name (case-normalized)."""
    return any(fnmatch.fnmatch(p, name) or p == name for p in parts)


def ensure_sandbox_dir() -> Path:
    """Create the sandbox temp dir if missing, return its path."""
    SANDBOX_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return SANDBOX_TEMP_DIR


# ═══════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════

def _has_traversal_component(path: str) -> bool:
    """True if any path component is exactly '..' (precise, not substring).

    A '..' inside a filename (e.g. 'notes..draft.txt') is a single component
    and must NOT trigger the traversal check. Separators are normalized so the
    check works for both POSIX and Windows paths.
    """
    normalized = path.replace("\\", "/")
    return any(part == TRAVERSAL_SEQ for part in normalized.split("/"))


def _check_access(real: str) -> tuple[bool, str]:
    """Check deny patterns only. Allowed-dir restriction removed per user request.

    System black-list (.git, .env, *.key, Windows/System32, etc.) still applies
    in every permission mode. All other paths are allowed for both read/write.
    """
    denied = matches_deny_pattern(real)
    if denied:
        return False, f"path matches deny pattern: {denied}"
    return True, "ok"


def _realpath(path: str) -> str:
    """Resolve to absolute realpath, returning empty string on failure."""
    try:
        return os.path.realpath(path)
    except (OSError, ValueError) as exc:
        logger.warning("realpath failed for %r: %s", path, exc)
        return ""
