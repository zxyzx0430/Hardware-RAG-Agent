"""Explorer path security — authorized roots + traversal/symlink/system checks."""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_AUTHORIZED_ROOTS: dict[str, set[Path]] = {}
_LOCK = threading.Lock()

TRAVERSAL_SEQ: str = ".."

# Simple component patterns blocked anywhere; multi-component patterns matched
# case-insensitively against the separator-normalized full path.
DENY_PATTERNS: tuple[str, ...] = (
    ".git", ".vscode", ".idea", ".claude", ".env*",
    "settings.json", "*.key", "*.pem", "*.p12", "*.pfx",
    "id_rsa*", "id_ed25519*", "*credential*", "*secret*",
)

MULTI_COMPONENT_DENY_PATTERNS: tuple[str, ...] = (
    "Windows/", "System32/", "SysWOW64/",
    "$Recycle.Bin/", "Boot/bootmgr", "Program Files/",
    "Program Files (x86)/", "ProgramData/",
)


class ExplorerSecurityError(ValueError):
    """Raised when a path fails explorer security validation."""


def session_scope(session_id: str) -> str:
    """Return a non-reversible in-memory scope key for a validated session token."""
    if not session_id:
        raise ExplorerSecurityError("Explorer session is required")
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def authorize_root(path: str, session_id: str) -> Path:
    """Resolve and register an allowed root directory for one Explorer session."""
    root = _resolve_directory(path)
    _assert_not_system(root)
    _assert_no_deny_pattern(root)
    _add_root(root, session_id)
    logger.info("authorized explorer root: %s", root)
    return root


def is_authorized(path: Path, session_id: str) -> bool:
    """Check whether a resolved path lies inside this session's authorized roots."""
    real = _safe_resolve(path)
    if real is None:
        return False
    return _is_under_any_root(real, session_id)


def require_authorized_root(path: str, session_id: str) -> Path:
    """Return an explicitly opened root directory; never authorize implicitly."""
    _assert_non_empty(path)
    _assert_no_traversal(path)
    requested_path = Path(path)
    _assert_no_symlink_components(requested_path)
    requested = _safe_resolve(requested_path)
    if requested is None:
        raise ExplorerSecurityError("root path could not be resolved")
    _assert_not_system(requested)
    _assert_no_deny_pattern(requested)
    if not requested.is_dir():
        raise ExplorerSecurityError("root path is not a directory")
    with _LOCK:
        if requested not in _AUTHORIZED_ROOTS.get(session_scope(session_id), set()):
            raise ExplorerSecurityError("directory has not been explicitly opened")
    return requested


def authorized_root_for(path: Path, session_id: str) -> Path:
    """Return the most specific explicitly opened root for this session containing *path*."""
    real = _safe_resolve(path)
    if real is None:
        raise ExplorerSecurityError("path could not be resolved")
    with _LOCK:
        roots = sorted(
            _AUTHORIZED_ROOTS.get(session_scope(session_id), set()),
            key=lambda root: len(root.parts),
            reverse=True,
        )
    for root in roots:
        if _is_relative(real, root):
            return root
    raise ExplorerSecurityError("path is outside authorized directories")


def validate_path(
    path: str,
    must_exist: bool = True,
    allow_file: bool = True,
    allow_dir: bool = True,
    *,
    session_id: str,
) -> Path:
    """Validate *path* for explorer operations."""
    _assert_non_empty(path)
    _assert_no_traversal(path)
    requested = Path(path)
    _assert_no_symlink_components(requested)
    real = _safe_resolve(requested)
    if real is None:
        raise ExplorerSecurityError("path could not be resolved")
    _assert_not_system(real)
    _assert_no_deny_pattern(real)
    _assert_within_root(real, session_id)
    if must_exist and not real.exists():
        raise ExplorerSecurityError("path does not exist")
    if not allow_file and real.is_file():
        raise ExplorerSecurityError("expected directory, got file")
    if not allow_dir and real.is_dir():
        raise ExplorerSecurityError("expected file, got directory")
    return real


def _resolve_directory(path: str) -> Path:
    """Resolve path and require it to be an existing directory."""
    requested = Path(path)
    _assert_no_symlink_components(requested)
    real = _safe_resolve(requested)
    if real is None:
        raise ExplorerSecurityError("root path could not be resolved")
    if not real.is_dir():
        raise ExplorerSecurityError("root path is not a directory")
    return real


def _add_root(root: Path, session_id: str) -> None:
    key = session_scope(session_id)
    with _LOCK:
        _AUTHORIZED_ROOTS.setdefault(key, set()).add(root)


def _is_under_any_root(child: Path, session_id: str) -> bool:
    key = session_scope(session_id)
    with _LOCK:
        roots = list(_AUTHORIZED_ROOTS.get(key, set()))
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


def _assert_no_symlink_components(path: Path) -> None:
    """Reject symbolic links instead of resolving Explorer requests through them."""
    candidate = path if path.is_absolute() else Path.cwd() / path
    current = Path(candidate.anchor)
    parts = candidate.parts[1:] if candidate.anchor else candidate.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                raise ExplorerSecurityError("symbolic links are not allowed")
        except OSError as exc:
            raise ExplorerSecurityError("path could not be inspected") from exc


def _assert_no_traversal(path: str) -> None:
    if any(part == TRAVERSAL_SEQ for part in path.replace("\\", "/").split("/")):
        raise ExplorerSecurityError("path traversal (..) is not allowed")


def _assert_within_root(real: Path, session_id: str) -> None:
    if not _is_under_any_root(real, session_id):
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
        if is_denied_component(part):
            for pat in DENY_PATTERNS:
                if fnmatch.fnmatch(part.casefold(), pat.casefold()):
                    return pat
    return None


def is_denied_component(name: str) -> bool:
    """Whether a path component is excluded from Explorer access/search."""
    return any(fnmatch.fnmatch(name.casefold(), pattern.casefold()) for pattern in DENY_PATTERNS)
