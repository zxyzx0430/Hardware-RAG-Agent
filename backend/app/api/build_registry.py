"""Server-side registry for compiled firmware.

An upload may only reference a build id issued here for the same session
token; the client never supplies a binary path.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass

BUILD_ID_TTL_SECONDS: int = 3600
MAX_REGISTERED_BUILDS: int = 32


@dataclass(frozen=True)
class BuildRecord:
    """A compiled firmware path owned by the token that built it."""

    binary_path: str
    owner: str
    board: str
    platform: str
    framework: str
    lib_deps: tuple[str, ...]
    created_at: float


_BUILDS: dict[str, BuildRecord] = {}


def register_build(
    binary_path: str,
    token: str,
    *,
    board: str,
    platform: str,
    framework: str,
    lib_deps: tuple[str, ...],
) -> str:
    """Remember a successful build and return its server-issued id."""
    now = time.time()
    _prune(now)
    build_id = uuid.uuid4().hex
    _BUILDS[build_id] = BuildRecord(
        binary_path=os.path.abspath(binary_path),
        owner=_owner_digest(token),
        board=board,
        platform=platform,
        framework=framework,
        lib_deps=tuple(lib_deps),
        created_at=now,
    )
    return build_id


def lookup_build(build_id: str, token: str) -> BuildRecord | None:
    """Return the caller's own unexpired build record, or None."""
    _prune(time.time())
    record = _BUILDS.get(build_id)
    if record is None or record.owner != _owner_digest(token):
        return None
    return record


def clear_builds() -> None:
    """Test helper: drop all registered builds."""
    _BUILDS.clear()


def _owner_digest(token: str) -> str:
    """Hash the session token so the registry never stores it raw."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _prune(now: float) -> None:
    """Drop expired records, then keep the registry bounded."""
    expired = [
        key for key, record in _BUILDS.items()
        if now - record.created_at > BUILD_ID_TTL_SECONDS
    ]
    for key in expired:
        _BUILDS.pop(key, None)
    while len(_BUILDS) >= MAX_REGISTERED_BUILDS:
        oldest = min(_BUILDS, key=lambda key: _BUILDS[key].created_at)
        _BUILDS.pop(oldest, None)
