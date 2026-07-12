"""Git auto-snapshot helper for file-editing tools.

After write_file/edit_file/multi_edit/apply_patch write to disk, they call
_git_snapshot() to commit the change with a fixed author so UndoEditTool can
safely `git reset --hard HEAD~1` to undo the most recent agent edit.

All git write operations are serialized via a global threading lock
(get_git_sync_lock) to prevent concurrent index.lock corruption. An async
entry point (git_snapshot_async) additionally acquires the asyncio lock for
call sites that dispatch via asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess

from src.agent.tools.groups.file_ops._git_lock import get_git_lock, get_git_sync_lock
from src.config.settings import ROOT_DIR

logger = logging.getLogger(__name__)

_AGENT_AUTHOR = "hardware-rag-agent <agent@local>"
_GIT_TIMEOUT_SECONDS = 10


def _git_snapshot(file_path: str, tool_name: str) -> None:
    """git add <abs file> && commit with agent author. Serialized by global lock.

    The file path is resolved to an absolute path so `git add` works regardless
    of the process cwd: snapshot runs from cwd=ROOT_DIR (=backend/), but the
    edited file may live anywhere in the project tree (frontend/, scripts/...).
    A relative path like `frontend/src/app.tsx` would otherwise be looked up
    under backend/ and silently missed.
    """
    abs_path = os.path.abspath(file_path)
    try:
        with get_git_sync_lock():
            _run_git(["add", "--", abs_path])
            _run_git(["commit", "-m", f"agent edit: {tool_name}", "--author", _AGENT_AUTHOR])
    except subprocess.SubprocessError as exc:
        logger.info("git_snapshot skipped for %s: %s", abs_path, exc)
    except OSError as exc:
        logger.info("git_snapshot skipped (no git): %s", exc)


async def git_snapshot_async(file_path: str, tool_name: str) -> None:
    """Async entry: serialize via the global asyncio lock, then thread off."""
    async with get_git_lock():
        await asyncio.to_thread(_git_snapshot, file_path, tool_name)


def _run_git(args: list[str]) -> str:
    """Run a git command in repo root; return stdout. Raise on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def _is_git_repo() -> bool:
    """Check if ROOT_DIR is inside a git repo."""
    try:
        _run_git(["rev-parse", "--is-inside-work-tree"])
        return True
    except (subprocess.SubprocessError, OSError):
        return False
