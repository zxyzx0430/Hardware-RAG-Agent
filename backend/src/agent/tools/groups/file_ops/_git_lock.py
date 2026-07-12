"""Global lock for serializing all git write operations (snapshot + reset).

Prevents concurrent git add/commit/reset from fighting over index.lock,
which silently drops snapshots and corrupts undo state.

Two primitives are exposed:

- get_git_lock(): asyncio.Lock for async call sites that dispatch git work via
  asyncio.to_thread (run_command snapshot, undo_edit reset, git_snapshot_async).
  Acquired with `async with get_git_lock():`.

- get_git_sync_lock(): threading.Lock acquired inside the synchronous git
  functions (_git_snapshot, _git_snapshot_files, _run_reset). This is the real
  cross-thread serializer: file_ops tool callers (write_file/edit_file/
  multi_edit/apply_patch) run _git_snapshot through their OWN asyncio.to_thread
  and bypass any async wrapper, so an asyncio.Lock alone would not serialize
  them. The threading.Lock covers every code path regardless of how it was
  dispatched.
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
