"""Private Git snapshots for Agent file edits without touching user Git state."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import AsyncIterator

from src.agent.tools.groups.file_ops._git_lock import get_git_lock, get_git_sync_lock
from src.config.settings import ROOT_DIR

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 10
_LATEST_REF = "refs/hardware-rag-agent/snapshots/latest"
_BASE_REF = "refs/hardware-rag-agent/snapshots/base"
_AGENT_NAME = "hardware-rag-agent"
_AGENT_EMAIL = "agent@local"


@dataclass(frozen=True)
class _SnapshotToken:
    repo: Path
    paths: tuple[str, ...]
    tool_name: str
    before_commit: str
    previous_after: str | None
    base_commit: str
    owns_base_ref: bool


@asynccontextmanager
async def git_snapshot_context(
    file_paths: list[str], tool_name: str
) -> AsyncIterator[None]:
    """Capture the exact pre-edit and post-edit worktree states for file paths.

    Git writes go through a temporary index and private refs. The user's index,
    branch refs, HEAD, and stash are never used as snapshot storage.
    """
    async with get_git_lock():
        try:
            token = await asyncio.to_thread(_begin_snapshot, file_paths, tool_name)
        except Exception as exc:  # Git snapshots must not block the file tool.
            logger.info("git snapshot begin skipped: %s", exc)
            token = None
        try:
            yield
        finally:
            if token is not None:
                try:
                    await asyncio.to_thread(_finish_snapshot, token)
                except Exception as exc:  # Git snapshots must not mask tool results.
                    logger.info("git snapshot finish skipped: %s", exc)


def undo_latest_snapshot() -> str | tuple[list[str], str | None]:
    """Restore only the latest Agent-edited paths, leaving user Git state intact."""
    with get_git_sync_lock():
        repo = _repo_root()
        if repo is None:
            return "undo not available: not a git repo"

        latest = _read_ref(repo, _LATEST_REF)
        base = _read_ref(repo, _BASE_REF)
        if latest is None or base is None:
            return "nothing to undo: no agent snapshot"

        try:
            before = _git_text(["rev-parse", "--verify", f"{latest}^"], repo)
            previous = _git_text(["rev-parse", "--verify", f"{before}^"], repo)
            paths = _snapshot_paths(latest, repo)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"undo snapshot could not be read: {exc}"
        if not paths:
            return "nothing to undo: latest snapshot has no changed files"

        try:
            if not _worktree_matches_commit(repo, latest, paths):
                return "cannot undo: target files changed since the agent snapshot"
        except (OSError, subprocess.SubprocessError) as exc:
            return f"undo snapshot comparison failed: {exc}"

        restore_paths: list[str] = []
        remove_paths: list[Path] = []
        for path in paths:
            target, error = _safe_repo_path(repo, path)
            if error:
                return error
            try:
                exists_before = _tree_has_path(before, path, repo)
            except (OSError, subprocess.SubprocessError) as exc:
                return f"undo snapshot path check failed: {exc}"
            if exists_before:
                restore_paths.append(path)
            elif target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    return f"cannot undo: target became a directory: {path}"
                remove_paths.append(target)

        try:
            if restore_paths:
                _git_text(
                    [
                        "--literal-pathspecs",
                        "restore",
                        "--source",
                        before,
                        "--worktree",
                        "--",
                        *restore_paths,
                    ],
                    repo,
                )
            for target in remove_paths:
                target.unlink()
        except (OSError, subprocess.SubprocessError) as exc:
            return f"undo restore failed: {exc}"

        try:
            if previous == base:
                _delete_ref(repo, _LATEST_REF, latest)
                _delete_ref(repo, _BASE_REF, base)
            else:
                _update_ref(repo, _LATEST_REF, previous, latest)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"files were restored but snapshot history could not advance: {exc}"
        return paths, None


def _begin_snapshot(file_paths: list[str], tool_name: str) -> _SnapshotToken | None:
    """Record the worktree content before an edit using an isolated index."""
    owns_base_ref = False
    base: str | None = None
    repo: Path | None = None
    try:
        with get_git_sync_lock():
            repo = _repo_root()
            if repo is None:
                return None
            paths = _repo_pathspecs(repo, file_paths)
            if not paths:
                return None

            latest = _read_ref(repo, _LATEST_REF)
            base = _read_ref(repo, _BASE_REF)
            head = _git_text(["rev-parse", "--verify", "HEAD"], repo)
            zero_oid = _zero_oid(repo)
            if latest is None:
                if base is None:
                    _update_ref(repo, _BASE_REF, head, zero_oid)
                    base = head
                    owns_base_ref = True
                parent = base
            else:
                if base is None:
                    logger.warning("agent snapshot latest ref exists without base ref")
                    return None
                parent = latest

            before_tree = _private_index_tree(repo, parent, paths)
            before_commit = _commit_tree(
                repo, before_tree, parent, f"agent snapshot before: {_safe_tool_name(tool_name)}"
            )
            return _SnapshotToken(
                repo=repo,
                paths=tuple(paths),
                tool_name=_safe_tool_name(tool_name),
                before_commit=before_commit,
                previous_after=latest,
                base_commit=base,
                owns_base_ref=owns_base_ref,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        if owns_base_ref and base is not None and repo is not None:
            _delete_ref_quietly(repo, _BASE_REF, base)
        logger.info("git snapshot preimage skipped: %s", exc)
        return None


def _finish_snapshot(token: _SnapshotToken) -> None:
    """Record post-edit state and atomically publish the private latest ref."""
    with get_git_sync_lock():
        try:
            after_tree = _private_index_tree(
                token.repo, token.before_commit, list(token.paths)
            )
            before_tree = _git_text(
                ["rev-parse", "--verify", f"{token.before_commit}^{{tree}}"], token.repo
            )
            if after_tree == before_tree:
                if token.owns_base_ref:
                    _delete_ref_quietly(token.repo, _BASE_REF, token.base_commit)
                return

            after_commit = _commit_tree(
                token.repo,
                after_tree,
                token.before_commit,
                f"agent snapshot after: {token.tool_name}",
            )
            expected = token.previous_after or _zero_oid(token.repo)
            _update_ref(token.repo, _LATEST_REF, after_commit, expected)
            logger.info("agent git snapshot recorded paths=%d tool=%s", len(token.paths), token.tool_name)
        except (OSError, subprocess.SubprocessError) as exc:
            if token.owns_base_ref:
                _delete_ref_quietly(token.repo, _BASE_REF, token.base_commit)
            logger.info("git snapshot postimage skipped: %s", exc)


def _repo_root() -> Path | None:
    """Return the enclosing Git worktree root, if ROOT_DIR is in one."""
    try:
        return Path(_git_text(["rev-parse", "--show-toplevel"], Path(ROOT_DIR))).resolve()
    except (OSError, subprocess.SubprocessError):
        return None


def _repo_pathspecs(repo: Path, file_paths: list[str]) -> list[str]:
    """Convert exact file paths to literal, repository-relative pathspecs."""
    result: list[str] = []
    for file_path in file_paths:
        absolute = Path(os.path.abspath(file_path))
        try:
            if os.path.commonpath((str(repo), str(absolute))) != str(repo):
                continue
            relative = os.path.relpath(absolute, repo).replace(os.sep, "/")
        except (OSError, ValueError):
            continue
        if relative not in ("", ".") and not relative.startswith("../"):
            result.append(relative)
    return list(dict.fromkeys(result))


def _private_index_tree(repo: Path, parent: str, paths: list[str]) -> str:
    """Write a temporary index based on parent, staging only the given paths."""
    fd, index_path = tempfile.mkstemp(prefix="hardware-rag-agent-index-")
    os.close(fd)
    os.unlink(index_path)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_path
    try:
        _git_text(["read-tree", parent], repo, env=env)
        existing_paths = [
            path
            for path in paths
            if (repo / Path(path)).exists()
            or (repo / Path(path)).is_symlink()
            or _tree_has_path(parent, path, repo)
        ]
        if existing_paths:
            _git_text(
                ["--literal-pathspecs", "add", "-f", "-A", "--", *existing_paths],
                repo,
                env=env,
            )
        return _git_text(["write-tree"], repo, env=env)
    finally:
        for suffix in ("", ".lock"):
            try:
                os.unlink(index_path + suffix)
            except FileNotFoundError:
                pass


def _commit_tree(repo: Path, tree: str, parent: str, message: str) -> str:
    """Create a private snapshot commit without moving a branch or HEAD."""
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": _AGENT_NAME,
            "GIT_AUTHOR_EMAIL": _AGENT_EMAIL,
            "GIT_COMMITTER_NAME": _AGENT_NAME,
            "GIT_COMMITTER_EMAIL": _AGENT_EMAIL,
        }
    )
    return _git_text(
        ["commit-tree", tree, "-p", parent, "-m", message], repo, env=env
    )


def _snapshot_paths(commit: str, repo: Path) -> list[str]:
    """List exact path names changed by the latest private snapshot commit."""
    result = _git_result(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit],
        repo,
        text=False,
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError(_stderr(result))
    return [os.fsdecode(part) for part in result.stdout.split(b"\0") if part]


def _tree_has_path(commit: str, path: str, repo: Path) -> bool:
    result = _git_result(
        ["--literal-pathspecs", "ls-tree", "-z", commit, "--", path],
        repo,
        text=False,
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError(_stderr(result))
    return bool(result.stdout)


def _worktree_matches_commit(repo: Path, commit: str, paths: list[str]) -> bool:
    """Compare worktree paths to a commit through a private, temporary index."""
    worktree_tree = _private_index_tree(repo, commit, paths)
    expected_tree = _git_text(["rev-parse", "--verify", f"{commit}^{{tree}}"], repo)
    return worktree_tree == expected_tree


def _safe_repo_path(repo: Path, relative_path: str) -> tuple[Path, str | None]:
    """Refuse snapshot paths that escape through traversal or a symlink parent."""
    parsed = PurePosixPath(relative_path)
    if parsed.is_absolute() or any(part in ("", ".", "..") for part in parsed.parts):
        return repo, f"cannot undo unsafe snapshot path: {relative_path}"
    target = repo.joinpath(*parsed.parts)
    try:
        parent = target.parent.resolve()
        if os.path.normcase(os.path.commonpath((str(repo), str(parent)))) != os.path.normcase(str(repo)):
            return target, f"cannot undo path outside the repository: {relative_path}"
    except (OSError, ValueError):
        return target, f"cannot resolve snapshot path: {relative_path}"
    return target, None


def _read_ref(repo: Path, ref: str) -> str | None:
    result = _git_result(["rev-parse", "--verify", "--quiet", ref], repo)
    return result.stdout.strip() if result.returncode == 0 else None


def _zero_oid(repo: Path) -> str:
    object_format = _git_text(["rev-parse", "--show-object-format"], repo)
    return "0" * (64 if object_format == "sha256" else 40)


def _update_ref(repo: Path, ref: str, new_oid: str, old_oid: str) -> None:
    _git_text(
        ["update-ref", "--create-reflog", "-m", "Agent private snapshot", ref, new_oid, old_oid],
        repo,
    )


def _delete_ref(repo: Path, ref: str, old_oid: str) -> None:
    _git_text(["update-ref", "-d", ref, old_oid], repo)


def _delete_ref_quietly(repo: Path, ref: str, old_oid: str) -> None:
    try:
        _delete_ref(repo, ref, old_oid)
    except (OSError, subprocess.SubprocessError):
        pass


def _safe_tool_name(tool_name: str) -> str:
    return " ".join(tool_name.split())[:80]


def _git_text(
    args: list[str], repo: Path, *, env: dict[str, str] | None = None
) -> str:
    result = _git_result(args, repo, env=env)
    if result.returncode != 0:
        raise subprocess.SubprocessError(_stderr(result) or f"git {args[0]} failed")
    return result.stdout.strip()


def _git_result(
    args: list[str],
    repo: Path,
    *,
    env: dict[str, str] | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=text,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _stderr(result: subprocess.CompletedProcess) -> str:
    if isinstance(result.stderr, bytes):
        return os.fsdecode(result.stderr).strip()
    return (result.stderr or "").strip()
