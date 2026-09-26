"""Global locks for serializing private Agent Git snapshots and undo.

Prevents concurrent snapshot/ref updates from interleaving and corrupting the
private undo chain. Snapshot indexes are temporary and do not use the user's
index.lock.

Two primitives are exposed:

- get_git_lock(): asyncio.Lock held across each pre-edit/edit/post-edit snapshot
  transaction and across undo.

- get_git_sync_lock(): threading.Lock acquired inside synchronous snapshot/ref
  and undo helpers, so work dispatched through asyncio.to_thread shares the
  same serialization point.
"""
from __future__ import annotations

import asyncio
import threading

_git_lock: asyncio.Lock = asyncio.Lock()
_git_sync_lock: threading.Lock = threading.Lock()


def get_git_lock() -> asyncio.Lock:
    """Return the asyncio lock for async call sites wrapping asyncio.to_thread."""
    return _git_lock


def get_git_sync_lock() -> threading.Lock:
    """Return the threading lock acquired inside synchronous git functions."""
    return _git_sync_lock
