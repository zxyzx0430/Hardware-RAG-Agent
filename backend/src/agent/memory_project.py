"""Thin project-identity wrapper for memory context, API and tools.

All identity derivation lives in ``app.db.project_identity``; this module only
adds a small process-local cache so per-turn context building does not spawn
``git`` repeatedly. The cache is keyed by the resolved root path, so tests that
change ``HARDWARE_AGENT_PROJECT_PATH`` or the working directory stay correct.
"""

from __future__ import annotations

import os

from app.db.project_identity import (
    PROJECT_PATH_ENV,
    current_project_id as _current_project_id,
    normalize_path,
    project_info as _project_info,
)

_CACHE_LIMIT = 32
_info_cache: dict[str, dict[str, str]] = {}


def _resolved_root(root: str | None) -> str:
    if root is None:
        configured = os.environ.get(PROJECT_PATH_ENV, "").strip()
        root = configured or os.getcwd()
    return os.path.abspath(os.path.expanduser(str(root)))


def project_info(root: str | None = None) -> dict[str, str]:
    """Return ``project_id``/``root``/``remote`` for ``root`` (cached)."""
    resolved = _resolved_root(root)
    key = normalize_path(resolved)
    cached = _info_cache.get(key)
    if cached is not None:
        return dict(cached)
    info = _project_info(resolved)
    if len(_info_cache) >= _CACHE_LIMIT:
        _info_cache.clear()
    _info_cache[key] = dict(info)
    return dict(info)


def current_project_id(root: str | None = None) -> str:
    """Stable project id; never raises, even if git discovery fails."""
    try:
        return project_info(root)["project_id"]
    except Exception:
        return _current_project_id(root)


def clear_cache() -> None:
    """Drop cached identities (tests and long-lived process reconfiguration)."""
    _info_cache.clear()


__all__ = [
    "PROJECT_PATH_ENV",
    "clear_cache",
    "current_project_id",
    "project_info",
]
