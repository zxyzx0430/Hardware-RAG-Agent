"""Read-only local Git context for the active workspace.

All commands use argv (never a shell). The module only inspects the
repository; branch, commit, push and PR actions belong to the Agent's own
command-line tools.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_GIT_TIMEOUT = 15


def _run(cwd: Path, *args: str, timeout: int = _GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            list(args), 127, "", f"{type(exc).__name__}: {exc}",
        )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(cwd, "git", *args)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _redact_remote(value: str) -> str:
    """Do not expose credentials embedded in a remote URL."""
    return re.sub(r"(https?://)[^/@]+@", r"***@", value)


def _repo_root(root: str) -> Path | None:
    path = Path(root)
    result = _git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def git_context(root: str) -> dict[str, Any]:
    path = Path(root)
    repo = _repo_root(root)
    if repo is None:
        return {
            "ok": True,
            "root": str(path),
            "is_git": False,
            "branch": None,
            "head": None,
            "dirty": [],
            "untracked": [],
            "remote_url": None,
            "upstream": None,
        }
    branch = _clean(_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout)
    head_result = _git(repo, "rev-parse", "HEAD")
    head = _clean(head_result.stdout) if head_result.returncode == 0 else None
    status = _git(repo, "status", "--porcelain")
    dirty: list[str] = []
    untracked: list[str] = []
    for line in status.stdout.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:])
        elif line.strip():
            dirty.append(line[3:])
    remote = _clean(_git(repo, "remote", "get-url", "origin").stdout) or None
    if remote:
        remote = _redact_remote(remote)
    upstream = _clean(_git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").stdout) or None
    return {
        "ok": True,
        "root": str(repo),
        "is_git": True,
        "branch": branch or None,
        "head": head,
        "dirty": dirty,
        "untracked": untracked,
        "remote_url": remote,
        "upstream": upstream,
    }
