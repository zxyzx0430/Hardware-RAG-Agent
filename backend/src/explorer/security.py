"""Explorer path security — authorized roots + traversal/symlink/system checks."""

from __future__ import annotations

import fnmatch
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_AUTHORIZED_ROOTS: set[Path] = set()
_LOCK = threading.Lock()

TRAVERSAL_SEQ: str = ".."

# Simple component patterns blocked anywhere; multi-component patterns matched
# case-insensitively against the separator-normalized full path.
DENY_PATTERNS: tuple[str, ...] = (
    ".git", ".vscode", ".idea", ".claude", ".env",
    "settings.json", "*.key", "*.pem", "*credentials*",
)

MULTI_COMPONENT_DENY_PATTERNS: tuple[str, ...] = (
    "Windows/", "System32/", "SysWOW64/",
    "$Recycle.Bin/", "Boot/bootmgr", "Program Files/",
    "Program Files (x86)/", "ProgramData/",
)


class ExplorerSecurityError(ValueError):
    """Raised when a path fails explorer security validation."""


def authorize_root(path: str) -> Path:
    """Resolve and register an allowed root directory."""
    root = _resolve_directory(path)
    _add_root(root)
    logger.info("authorized explorer root: %s", root)
    return root


def is_authorized(path: Path) -> bool:
    """Check whether a resolved path lies inside any authorized root."""
    real = _safe_resolve(path)
    if real is None:
        return False
    return _is_under_any_root(real)


# Markers that identify a project root; used for auto-reauthorization after
# backend restarts clear the in-memory authorized-roots set.
_PROJECT_MARKERS: tuple[str, ...] = (
    ".git", "package.json", "pyproject.toml", "Cargo.toml",
    "go.mod", "AGENTS.md", ".gitnexus", "platformio.ini",
)


def _find_project_root(path: Path) -> Path | None:
    """Walk up from *path* to find the nearest directory containing a project marker."""
    candidate = path if path.is_dir() else path.parent
    for _ in range(20):
        for marker in _PROJECT_MARKERS:
            if (candidate / marker).exists():
                return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None


def validate_path(
    path: str,
    must_exist: bool = True,
    allow_file: bool = True,
    allow_dir: bool = True,
) -> Path:
    """Validate *path* for explorer operations."""
    _assert_non_empty(path)
    _assert_no_traversal(path)
    real = _safe_resolve(Path(path))
    if real is None:
        raise ExplorerSecurityError("path could not be resolved")
    _assert_not_system(real)
    _assert_no_deny_pattern(real)
    try:
        _assert_within_root(real)
    except ExplorerSecurityError:
        # Backend may have restarted, clearing in-memory roots.
        # Try to auto-reauthorize the project root.
        root = _find_project_root(real)
        if root is None:
            raise
        _add_root(root)
        _assert_within_root(real)
    if must_exist and not real.exists():
        raise ExplorerSecurityError("path does not exist")
    if not allow_file and real.is_file():
        raise ExplorerSecurityError("expected directory, got file")
    if not allow_dir and real.is_dir():
        raise ExplorerSecurityError("expected file, got directory")
    return real


def _resolve_directory(path: str) -> Path:
    """Resolve path and require it to be an existing directory."""
    real = _safe_resolve(Path(path))
    if real is None:
        raise ExplorerSecurityError("root path could not be resolved")
    if not real.is_dir():
        raise ExplorerSecurityError("root path is not a directory")
    return real


def _add_root(root: Path) -> None:
    with _LOCK:
        _AUTHORIZED_ROOTS.add(root)


def _is_under_any_root(child: Path) -> bool:
    with _LOCK:
        roots = list(_AUTHORIZED_ROOTS)
    return any(_is_relative(child, root) for root in roots)


def _is_relative(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False)
    except (OSError, ValueError) as exc:
        logger.warning("path resolve failed for %r: %s", path, exc)
        return None


def _assert_non_empty(path: str) -> None:
    if not path:
        raise ExplorerSecurityError("path is empty")


def _assert_no_traversal(path: str) -> None:
    if any(part == TRAVERSAL_SEQ for part in path.replace("\\", "/").split("/")):
        raise ExplorerSecurityError("path traversal (..) is not allowed")


def _assert_within_root(real: Path) -> None:
    if not _is_under_any_root(real):
        raise ExplorerSecurityError("path is outside authorized directories")


def _assert_not_system(real: Path) -> None:
    norm = str(real).replace("\\", "/")
    lower = norm.lower()
    for pat in MULTI_COMPONENT_DENY_PATTERNS:
        if pat.lower() in lower:
            raise ExplorerSecurityError(f"system path is blocked: {pat}")
    home_prefix = _home_prefix()
    if home_prefix and lower == home_prefix:
        raise ExplorerSecurityError("user home directory is blocked")


def _home_prefix() -> str | None:
    try:
        return str(Path.home()).replace("\\", "/").lower()
    except RuntimeError:
        return None


def _assert_no_deny_pattern(real: Path) -> None:
    matched = _matches_deny_pattern(real)
    if matched:
        raise ExplorerSecurityError(f"path matches deny pattern: {matched}")


def _matches_deny_pattern(real: Path) -> str | None:
    for part in real.parts:
        for pat in DENY_PATTERNS:
            if fnmatch.fnmatch(part, pat):
                return pat
    return None
