"""Stable project identity for memory scoping.

The identity is derived from the git common directory plus the normalized
``origin`` remote URL, so a repository and all of its worktrees/subdirectories
share one id without trusting the current working directory. Non-git folders
fall back to the normalized absolute root path. Every failure path returns a
deterministic fallback instead of raising into chat or startup.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from urllib.parse import urlsplit

PROJECT_PATH_ENV = "HARDWARE_AGENT_PROJECT_PATH"
GIT_TIMEOUT_SECONDS = 3

_SCP_REMOTE_RE = re.compile(r"^(?P<user>[^@/\\]+)@(?P<host>[^:/\\]+):(?P<path>.+)$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _resolve_root(root: str | None) -> str:
    if root is None:
        configured = os.environ.get(PROJECT_PATH_ENV, "").strip()
        root = configured or os.getcwd()
    return os.path.abspath(os.path.expanduser(str(root)))


def normalize_path(path: str) -> str:
    """Normalize a local path for hashing (separators and Windows case)."""
    absolute = os.path.abspath(os.path.expanduser(str(path)))
    normalized = os.path.normcase(os.path.normpath(absolute))
    return normalized.replace(os.sep, "/")


def is_path_like(value: object) -> bool:
    """True when a legacy scope_key looks like a filesystem path."""
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    return bool(
        _WINDOWS_ABSOLUTE_RE.match(candidate)
        or candidate.startswith("/")
        or candidate.startswith("\\\\")
    )


def normalize_remote(url: str | None) -> str:
    """Canonical ``host/path`` form; HTTPS and scp-style URLs compare equal."""
    if not url:
        return ""
    value = url.strip()
    if not value:
        return ""
    if (
        _WINDOWS_ABSOLUTE_RE.match(value)
        or value.startswith("/")
        or value.startswith("\\\\")
    ):
        return normalize_path(value)
    scp_match = _SCP_REMOTE_RE.match(value)
    if scp_match:
        host = scp_match.group("host")
        path = scp_match.group("path")
    else:
        parsed = urlsplit(value)
        if parsed.scheme == "file":
            local = parsed.path
            if re.match(r"^/[A-Za-z]:[\\/]", local):
                local = local[1:]
            return normalize_path(local)
        if parsed.scheme and parsed.netloc:
            host = parsed.hostname or ""
            path = parsed.path
        else:
            # Local-path remotes have no host; fall back to the path form.
            return normalize_path(value)
    host = host.strip().casefold()
    path = path.strip().strip("/")
    if path.casefold().endswith(".git"):
        path = path[: -len(".git")]
    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    if not path:
        return host
    return f"{host}/{path}"


def _run_git(root: str, *args: str) -> str | None:
    command = ["git", "-C", root, *args]
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "timeout": GIT_TIMEOUT_SECONDS,
        "check": False,
    }
    creation_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt" and creation_no_window:
        kwargs["creationflags"] = creation_no_window
    try:
        completed = subprocess.run(command, **kwargs)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value or None


def _git_common_dir(root: str) -> str | None:
    common = _run_git(root, "rev-parse", "--git-common-dir")
    if not common:
        return None
    if not os.path.isabs(common):
        common = os.path.join(root, common)
    return normalize_path(common)


def _git_remote(root: str) -> str:
    remote = _run_git(root, "config", "--get", "remote.origin.url")
    return normalize_remote(remote)


def project_info(root: str | None = None) -> dict[str, str]:
    """Return ``project_id``/``root``/``remote`` for the given root."""
    resolved_root = _resolve_root(root)
    common_dir = _git_common_dir(resolved_root)
    if common_dir is not None:
        # Identity is the repository's working-tree root, not ``.git`` and not
        # the remote URL. This keeps the same id after ``git init``/remote
        # changes and lets every linked worktree share the main checkout.
        repo_root = os.path.dirname(common_dir.rstrip("/\\")) or common_dir
        remote = _git_remote(resolved_root)
        identity = "\0".join(("root", normalize_path(repo_root)))
    else:
        remote = ""
        identity = "\0".join(("root", normalize_path(resolved_root)))
    digest = hashlib.sha256(identity.encode("utf-8", errors="strict")).hexdigest()
    return {
        "project_id": digest,
        "root": resolved_root,
        "remote": remote,
    }


def current_project_id(root: str | None = None) -> str:
    """Stable project id; never raises on git/subprocess failure."""
    try:
        return project_info(root)["project_id"]
    except Exception:
        # Last-resort fallback: the normalized path alone stays deterministic.
        fallback = normalize_path(_resolve_root(root))
        return hashlib.sha256(("\0".join(("path", fallback))).encode("utf-8")).hexdigest()


__all__ = [
    "PROJECT_PATH_ENV",
    "current_project_id",
    "is_path_like",
    "normalize_path",
    "normalize_remote",
    "project_info",
]
