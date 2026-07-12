"""
Hardware RAG Agent — GlobTool (file_ops group).

Glob-pattern match against file paths. Returns matches sorted by mtime
descending (newest first). Source: Claude Code / OpenCode tool survey
(spec add-grep-glob-todo-tools §glob).

Distinguishes from list_files: glob matches a KNOWN filename pattern
(e.g. "*.py"); list_files explores an UNKNOWN directory structure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import validate_path

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

_DEFAULT_SEARCH_ROOT: str = "."
_DEFAULT_MAX_RESULTS: int = 100
_TRUNCATED_SUFFIX: str = "... (truncated)"


# ═══════════════════════════════════════════
# GlobTool
# ═══════════════════════════════════════════

class GlobArgs(BaseModel):
    pattern: str = Field(description="glob 模式，如 '*.py' 或 '**/*.ts'")
    path: str = Field(default=_DEFAULT_SEARCH_ROOT, description="搜索根目录（建议传绝对路径）")
    max_results: int = Field(default=_DEFAULT_MAX_RESULTS, description="最多返回文件数")


class GlobOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    matches: list[dict] = Field(default_factory=list, description="匹配列表 [{path,mtime}]")
    truncated: bool = Field(False, description="是否因超过 max_results 被截断")


class GlobTool(ToolSpec):
    """Glob-pattern match against file paths, sorted by mtime desc."""
    name: str = "glob"
    description: str = (
        "用 glob 模式匹配文件路径（如 '*.py'、'**/*.ts'），返回文件路径+修改时间，"
        "按修改时间倒序（最新在前）。"
        "已知文件名模式时用本工具；探索未知目录结构请用 list_files。"
        "敏感路径（.git/.env/*.key/*.pem）会被拦截。"
    )
    args_schema: type = GlobArgs
    output_schema: type[BaseModel] | None = GlobOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 15
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Glob file paths under root dir."""
        path = args.get("path", _DEFAULT_SEARCH_ROOT)
        err = _check_path(path)
        if err:
            return err
        pattern = args.get("pattern", "")
        if not pattern:
            return _error("pattern is required")
        max_results = int(args.get("max_results", _DEFAULT_MAX_RESULTS))
        return await _run_glob(path, pattern, max_results)


async def _run_glob(root: str, pattern: str, max_results: int) -> dict:
    """Run sync glob in a worker thread, return envelope dict."""
    matches, truncated = await asyncio.to_thread(_glob_sync, root, pattern, max_results)
    return {"matches": matches, "truncated": truncated, "count": len(matches)}


def _glob_sync(root: str, pattern: str, max_results: int) -> tuple[list[dict], bool]:
    """Match glob pattern under root, sort by mtime desc, truncate."""
    root_path = Path(root)
    if not root_path.exists():
        return [], False
    raw = list(root_path.glob(pattern))
    files = [p for p in raw if not _is_dir(p)]
    files = [p for p in files if _has_mtime(p)]
    files.sort(key=_get_mtime, reverse=True)
    return _truncate_matches(files, max_results)


def _truncate_matches(files: list[Path], max_results: int) -> tuple[list[dict], bool]:
    """Convert paths to dicts, truncate to max_results."""
    truncated = len(files) > max_results
    sliced = files[:max_results]
    return [_to_match(p) for p in sliced], truncated


def _to_match(p: Path) -> dict:
    """Convert a path to a {path, mtime} dict."""
    return {"path": str(p), "mtime": _get_mtime(p)}


def _get_mtime(p: Path) -> float:
    """Return mtime as float, 0.0 on error."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _has_mtime(p: Path) -> bool:
    """Return True if stat succeeds (used as a filter guard)."""
    try:
        p.stat()
        return True
    except OSError:
        return False


def _is_dir(p: Path) -> bool:
    """Return True if path is a directory (fail-closed on error)."""
    try:
        return p.is_dir()
    except OSError:
        return True


def _check_path(path: str) -> dict | None:
    """Return error envelope if path invalid, else None."""
    ok, reason = validate_path(path, is_write=False)
    if not ok:
        return _error(f"path not allowed: {reason}")
    return None


def _error(msg: str) -> dict:
    """Build an error response envelope."""
    return {"matches": [], "truncated": False, "count": 0, "error": msg}
