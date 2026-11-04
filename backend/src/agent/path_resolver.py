"""Path resolution for the Agent path guard.

Resolves a path to its real location while rejecting escapes that the old
``os.path.realpath`` call missed: Win32 namespace prefixes, NTFS alternate
data streams, symlinks, and Windows junctions/reparse points.
"""
from __future__ import annotations

import logging
import os
import stat

logger = logging.getLogger(__name__)

_NT_PREFIXES: tuple[str, ...] = ("\\\\?\\", "\\\\.\\", "//?/", "//./")
_REPARSE_ATTRIBUTE: int = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def resolve_guard_path(path: str) -> str:
    """Resolve a path to its real location, rejecting link/reparse escapes.

    Resolves the longest existing prefix component by component with
    ``realpath(strict=True)``, rejects any symlink/junction component, NT
    namespace prefixes, and NTFS alternate data streams. The non-existent
    suffix (e.g. a file about to be created) is re-appended unresolved.
    """
    if "\x00" in path or has_nt_prefix(path):
        return ""
    if os.name == "nt" and has_ads(path):
        return ""
    try:
        return _resolve_components(os.path.abspath(path))
    except (OSError, ValueError) as exc:
        logger.warning("guard resolve failed for %r: %s", path, exc)
        return ""


def normalize_for_compare(path: str) -> str:
    """Normalize a path for case-insensitive containment checks."""
    return os.path.normcase(os.path.normpath(path))


def has_nt_prefix(path: str) -> bool:
    """True for Win32 namespace paths that bypass normal normalization."""
    return path.startswith(_NT_PREFIXES)


def has_ads(path: str) -> bool:
    """True if an NTFS alternate data stream separator is present."""
    _, tail = os.path.splitdrive(path)
    return ":" in tail


def _resolve_components(absolute: str) -> str:
    """Walk path components, resolving each existing one and checking links."""
    drive, tail = os.path.splitdrive(absolute)
    current = drive + os.sep if drive else os.sep
    pending = [part for part in tail.replace("\\", "/").split("/") if part and part != "."]
    for index, part in enumerate(pending):
        candidate = os.path.join(current, part)
        if not os.path.lexists(candidate):
            return os.path.normpath(os.path.join(current, *pending[index:]))
        if is_link_or_reparse(candidate):
            return ""
        current = os.path.realpath(candidate, strict=True)
    return os.path.normpath(current)


def is_link_or_reparse(path: str) -> bool:
    """True for symlinks and Windows junctions/reparse points."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)
