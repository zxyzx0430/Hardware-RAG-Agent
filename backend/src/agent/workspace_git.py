"""Bounded local Git and GitHub CLI operations for the active workspace.

All commands use argv (never a shell). The module does not stash/reset/force;
push and draft PR creation are explicit caller actions.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

_GIT_TIMEOUT = 15
_GH_TIMEOUT = 30
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$")


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
    return re.sub(r"(https?://)[^/@]+@", r"***@", value)


def _inside_root(path: str, root: Path) -> bool:
    try:
        target = os.path.normcase(str(Path(path).resolve(strict=False)))
        base = os.path.normcase(str(root.resolve(strict=False)))
        return os.path.commonpath([target, base]) == base
    except (OSError, ValueError):
        return False


def _repo_root(root: str) -> Path | None:
    path = Path(root)
    result = _git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message}


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
    for line in _clean(status.stdout).splitlines():
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


def create_branch(root: str, name: str) -> dict[str, Any]:
    repo = _repo_root(root)
    if repo is None:
        return _error("NOT_A_GIT_REPO", "当前工作区不是 Git 仓库")
    branch = _clean(name)
    if not _BRANCH_RE.fullmatch(branch) or ".." in branch or branch.startswith("-"):
        return _error("INVALID_BRANCH", "分支名不合法")
    result = _git(repo, "switch", "-c", branch)
    if result.returncode != 0:
        return _error("BRANCH_FAILED", result.stderr.strip() or "无法创建分支")
    return {"ok": True, "branch": branch, "context": git_context(str(repo))}


def commit_paths(root: str, paths: list[str], message: str) -> dict[str, Any]:
    repo = _repo_root(root)
    if repo is None:
        return _error("NOT_A_GIT_REPO", "当前工作区不是 Git 仓库")
    commit_message = _clean(message)
    if not commit_message:
        return _error("INVALID_COMMIT_MESSAGE", "提交信息不能为空")
    if not paths:
        return _error("NO_PATHS", "没有选择要提交的文件")
    relative: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip() or any(ch in raw for ch in "*?["):
            continue
        if not _inside_root(raw, repo):
            return _error("PATH_OUTSIDE_WORKSPACE", "提交路径超出工作区")
        target = Path(raw).resolve(strict=False)
        relative.append(os.path.relpath(target, repo).replace(os.sep, "/"))
    if not relative:
        return _error("NO_PATHS", "没有选择要提交的文件")
    added = _git(repo, "add", "--", *relative)
    if added.returncode != 0:
        return _error("GIT_ADD_FAILED", added.stderr.strip() or "git add 失败")
    # Commit only the selected paths; never commit unrelated staged user edits.
    committed = _git(repo, "commit", "--only", "-m", commit_message, "--", *relative)
    if committed.returncode != 0:
        return _error("COMMIT_FAILED", committed.stderr.strip() or committed.stdout.strip() or "提交失败")
    return {
        "ok": True,
        "commit": _clean(_git(repo, "rev-parse", "HEAD").stdout),
        "context": git_context(str(repo)),
    }


def _github_remote(repo: Path) -> str | None:
    remote = _clean(_git(repo, "remote", "get-url", "origin").stdout)
    if not remote:
        return None
    normalized = remote.replace("\\", "/").lower()
    if "github.com/" not in normalized and not normalized.startswith("git@github.com:"):
        return None
    return remote


def _gh_auth(repo: Path) -> dict[str, Any]:
    result = _run(repo, "gh", "auth", "status", timeout=_GH_TIMEOUT)
    output = (result.stdout + "\n" + result.stderr).lower()
    auth_failed = (
        result.returncode != 0
        or "failed to log in" in output
        or "not logged in" in output
        or "no oauth token" in output
    )
    if auth_failed:
        return _error("GH_AUTH_REQUIRED", "GitHub CLI 未登录或 token 失效；请先运行 gh auth login")
    return {"ok": True}


def _gh_json(repo: Path, *args: str) -> Any:
    result = _run(repo, "gh", *args, timeout=_GH_TIMEOUT)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError):
        return None


def pr_status(root: str) -> dict[str, Any]:
    repo = _repo_root(root)
    if repo is None:
        return _error("NOT_A_GIT_REPO", "当前工作区不是 Git 仓库")
    if _github_remote(repo) is None:
        return _error("NO_GITHUB_REMOTE", "当前仓库没有 GitHub origin remote")
    auth = _gh_auth(repo)
    if not auth["ok"]:
        return auth
    pr = _gh_json(repo, "pr", "view", "--json", "number,url,isDraft,state,mergeable,headRefName,baseRefName")
    if pr is None:
        return {"ok": True, "pr": None, "checks": [], "message": "当前分支还没有关联的 PR"}
    checks = _gh_json(repo, "pr", "checks", "--json", "name,state,bucket,link")
    return {"ok": True, "pr": pr, "checks": checks or []}


def create_draft_pr(
    root: str, *, base: str, title: str, body: str, push: bool,
) -> dict[str, Any]:
    repo = _repo_root(root)
    if repo is None:
        return _error("NOT_A_GIT_REPO", "当前工作区不是 Git 仓库")
    if _github_remote(repo) is None:
        return _error("NO_GITHUB_REMOTE", "当前仓库没有 GitHub origin remote")
    auth = _gh_auth(repo)
    if not auth["ok"]:
        return auth
    branch = _clean(_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout)
    if not branch or branch == "HEAD":
        return _error("DETACHED_HEAD", "当前不在可交付分支上")
    if push:
        pushed = _git(repo, "push", "-u", "origin", branch)
        if pushed.returncode != 0:
            return _error("PUSH_FAILED", pushed.stderr.strip() or "推送失败")
    base = _clean(base) or "main"
    title = _clean(title) or f"Draft: {branch}"
    result = _run(
        repo, "gh", "pr", "create", "--draft",
        "--base", base, "--head", branch, "--title", title, "--body", body or "",
        timeout=_GH_TIMEOUT,
    )
    if result.returncode != 0:
        return _error("PR_CREATE_FAILED", result.stderr.strip() or result.stdout.strip() or "创建 PR 失败")
    return {"ok": True, "url": _clean(result.stdout), "status": pr_status(root)}
