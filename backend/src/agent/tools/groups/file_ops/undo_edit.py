"""
Hardware RAG Agent — UndoEditTool (file_ops group).

Undo the most recent agent edit by git-resetting one commit back. Only
commits authored by `hardware-rag-agent <agent@local>` are eligible, so
user commits are never silently rolled back.

Stash protection: before `git reset --hard HEAD~1`, unstaged working-tree
changes are stashed (--keep-index) and popped after the reset. If the pop
conflicts, the stash is preserved and the user is told how to recover,
instead of silently destroying the user's uncommitted work.
Spec: add-grep-glob-todo-tools §Requirement: undo_edit.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.file_ops._git_lock import get_git_lock, get_git_sync_lock
from src.config.settings import ROOT_DIR


_GIT_TIMEOUT_SECONDS: int = 10
_AGENT_AUTHOR: str = "hardware-rag-agent <agent@local>"
_STASH_MSG: str = "undo_edit: pre-reset stash"
_STASH_RECOVER_HINT: str = "工作区修改已保存到 stash@{0}，请手动恢复"
_NOTHING_TO_STASH_MARKER: str = "No local changes to save"


# ═══════════════════════════════════════════
# UndoEditTool
# ═══════════════════════════════════════════

class UndoEditArgs(BaseModel):
    """Undo takes no arguments — it always targets the latest commit."""


class UndoEditOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    success: bool = Field(False, description="是否撤销成功")
    reverted_files: list[str] = Field(default_factory=list, description="回滚的文件列表")


class UndoEditTool(ToolSpec):
    """Undo the most recent agent edit via git reset --hard HEAD~1."""
    name: str = "undo_edit"
    description: str = (
        "撤销最近一次 Agent 对文件系统的编辑（基于 git 快照，仅当最近一次提交 author 为 "
        "hardware-rag-agent 时才撤销）。非 git 仓库或最近提交非 agent 时返回提示。"
    )
    args_schema: type = UndoEditArgs
    output_schema: type[BaseModel] | None = UndoEditOutput

    risk_level: RiskLevel = RiskLevel.HIGH
    timeout_seconds: int = _GIT_TIMEOUT_SECONDS + 2
    max_retries: int = 0

    async def execute(self: "UndoEditTool", args: dict[str, Any], ctx: ToolContext) -> dict:
        """Run git inspection + reset under the global git lock."""
        return await _do_undo()


async def _do_undo() -> dict:
    """Serialize the undo via the global asyncio lock; thread off sync git work."""
    async with get_git_lock():
        result = await asyncio.to_thread(_undo_sync)
    if isinstance(result, str):
        return _fail(result)
    files, warning = result
    return _ok(files, warning)


def _undo_sync() -> str | tuple[list[str], str | None]:
    """Top-level sync routine. Returns error str or (files, warning)."""
    if not _is_git_repo():
        return "undo not available: not a git repo"
    author, err = _get_last_commit_author()
    if err:
        return err
    if author != _AGENT_AUTHOR:
        return "nothing to undo: last commit is not by agent"
    return _reset_head()


def _is_git_repo() -> bool:
    """Return True if ROOT_DIR is inside a git work tree."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(ROOT_DIR), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (subprocess.TimeoutExpired, OSError):
        return False


def _get_last_commit_author() -> tuple[str, str | None]:
    """Fetch 'Name <email>' of HEAD commit. Returns (author, error)."""
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%an <%ae>"],
            cwd=str(ROOT_DIR), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if r.returncode != 0:
            return "", "nothing to undo: no commits in repo"
        return r.stdout.strip(), None
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "", f"git log failed: {exc}"


def _reset_head() -> str | tuple[list[str], str | None]:
    """Capture HEAD files, reset --hard HEAD~1 (with stash protection)."""
    head_sha, err = _get_head_sha()
    if err:
        return err
    files, err = _get_commit_files(head_sha)
    if err:
        return err
    err, warning = _run_reset()
    if err:
        return err
    return files, warning


def _get_head_sha() -> tuple[str, str | None]:
    """Fetch HEAD commit hash. Returns (sha, error)."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT_DIR), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if r.returncode != 0:
            return "", f"git rev-parse failed: {r.stderr.strip()}"
        return r.stdout.strip(), None
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "", f"git rev-parse failed: {exc}"


def _get_commit_files(sha: str) -> tuple[list[str], str | None]:
    """List files changed in the given commit (by sha, not moving HEAD ref)."""
    try:
        r = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
            cwd=str(ROOT_DIR), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if r.returncode != 0:
            return [], f"git diff-tree failed: {r.stderr.strip()}"
        return [line for line in r.stdout.splitlines() if line], None
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"git diff-tree failed: {exc}"


def _run_reset() -> tuple[str | None, str | None]:
    """Stash unstaged changes, reset --hard HEAD~1, then pop stash.

    Returns (error, warning). error is set only if the reset itself fails.
    warning is set when a stash was created but could not be popped cleanly
    (the stash is preserved so the user can recover it manually).
    Serialized by the global threading lock so it never races with snapshots.
    """
    with get_git_sync_lock():
        stashed = _stash_unstaged()
        err = _do_reset_hard()
        if err:
            return err, None
        if not stashed:
            return None, None
        return _pop_stash()


def _stash_unstaged() -> bool:
    """Stash unstaged working-tree changes (--keep-index). Returns True if created.

    --keep-index leaves staged changes in the working tree (they are already
    captured in the agent's snapshot commit, so reset --hard HEAD~1 will
    restore their pre-commit state). Only the unstaged changes get stashed
    away and are popped back after the reset.
    """
    try:
        r = subprocess.run(
            ["git", "stash", "push", "--keep-index", "--message", _STASH_MSG],
            cwd=str(ROOT_DIR), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        return r.returncode == 0 and _NOTHING_TO_STASH_MARKER not in r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False


def _do_reset_hard() -> str | None:
    """Execute `git reset --hard HEAD~1`. Returns error string or None."""
    try:
        r = subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=str(ROOT_DIR), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if r.returncode != 0:
            return f"git reset failed: {r.stderr.strip()}"
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"git reset failed: {exc}"


def _pop_stash() -> tuple[str | None, str | None]:
    """Pop the pre-reset stash. On conflict, keep stash and return a warning.

    git stash pop leaves the stash in place when it hits conflicts, so the
    user's changes are never lost — they can `git stash pop` / `git stash drop`
    manually after resolving.
    """
    try:
        r = subprocess.run(
            ["git", "stash", "pop"],
            cwd=str(ROOT_DIR), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if r.returncode != 0:
            return None, _STASH_RECOVER_HINT
        return None, None
    except (subprocess.TimeoutExpired, OSError):
        return None, _STASH_RECOVER_HINT


def _fail(msg: str) -> dict:
    return {"output": msg, "success": False, "reverted_files": []}


def _ok(files: list[str], warning: str | None = None) -> dict:
    listing = ", ".join(files) if files else "(no files)"
    msg = f"reverted: {listing}"
    if warning:
        msg += f" | {warning}"
    return {
        "output": msg,
        "success": True,
        "reverted_files": files,
    }
