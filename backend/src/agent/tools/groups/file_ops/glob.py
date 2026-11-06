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
from src.agent.path_guard import resolve_agent_path, validate_path, is_path_allowed_for_tree
from src.agent.tools.groups.file_ops._scan_policy import (
    explicit_ignored_segments,
    is_default_ignored_dir,
)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

_DEFAULT_SEARCH_ROOT: str = "."
_DEFAULT_MAX_RESULTS: int = 100
_PLAN_MODE_MAX_RESULTS: int = 50
_MAX_OUTPUT_CHARS: int = 16_000
_APPROX_MATCH_CHARS: int = 40
_PLAN_PERMISSION_MODE: str = "plan"


# ═══════════════════════════════════════════
# GlobTool
# ═══════════════════════════════════════════

class GlobArgs(BaseModel):
    pattern: str = Field(description="glob 模式，如 '*.py' 或 '**/*.ts'")
    path: str = Field(default=_DEFAULT_SEARCH_ROOT, description="搜索根目录（建议传绝对路径）")
    max_results: int = Field(default=_DEFAULT_MAX_RESULTS, description="最多返回文件数")
    include_ignored: bool = Field(
        default=False,
        description="为 true 时同时匹配依赖/构建/缓存目录（node_modules、.venv、.git 等）",
    )


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
        "默认跳过依赖/构建/缓存目录（node_modules、.venv、venv、.git、.pio、"
        ".platformio、dist、build、__pycache__、.pytest_cache 等）；"
        "模式中显式写出这些目录（如 '**/node_modules/**'）时按显式请求处理，"
        "也可传 include_ignored=true。计划模式最多返回 50 条，普通模式 100 条、约 16k 字符。"
        "敏感路径（.git/.env/*.key/*.pem）会被拦截。"
    )
    args_schema: type = GlobArgs
    output_schema: type[BaseModel] | None = GlobOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 15
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Glob file paths under root dir."""
        path = resolve_agent_path(args.get("path", _DEFAULT_SEARCH_ROOT))
        if not path:
            return _error("path could not be resolved")
        err = _check_path(path)
        if err:
            return err
        pattern = args.get("pattern", "")
        if not pattern:
            return _error("pattern is required")
        max_results = int(args.get("max_results", _DEFAULT_MAX_RESULTS))
        if getattr(ctx, "permission_mode", "") == _PLAN_PERMISSION_MODE:
            max_results = min(max_results, _PLAN_MODE_MAX_RESULTS)
        return await _run_glob(
            path,
            pattern,
            max_results,
            include_ignored=bool(args.get("include_ignored", False)),
        )


async def _run_glob(
    root: str,
    pattern: str,
    max_results: int,
    *,
    include_ignored: bool = False,
) -> dict:
    """Run sync glob in a worker thread, return envelope dict."""
    matches, truncated = await asyncio.to_thread(
        _glob_sync, root, pattern, max_results, include_ignored,
    )
    return {"matches": matches, "truncated": truncated, "count": len(matches)}


def _glob_sync(
    root: str,
    pattern: str,
    max_results: int,
    include_ignored: bool = False,
) -> tuple[list[dict], bool]:
    """Match glob pattern under root, sort by mtime desc, truncate."""
    root_path = Path(root)
    if not root_path.exists():
        return [], False
    allowed_ignored = explicit_ignored_segments(pattern)
    raw = list(root_path.glob(pattern))
    files = [p for p in raw if not _is_dir(p)]
    files = [
        p for p in files
        if include_ignored or not _is_ignored(p, root_path, allowed_ignored)
    ]
    files = [p for p in files if _has_mtime(p)]
    files = [p for p in files if is_path_allowed_for_tree(str(p))]
    files.sort(key=_get_mtime, reverse=True)
    return _truncate_matches(files, max_results)


def _is_ignored(
    path: Path,
    root: Path,
    allowed_ignored: frozenset[str] = frozenset(),
) -> bool:
    """True when a relative component is a default-ignored directory.

    Components the pattern names literally (``node_modules``) are exempt.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(
        is_default_ignored_dir(part) and part not in allowed_ignored
        for part in parts
    )


def _truncate_matches(files: list[Path], max_results: int) -> tuple[list[dict], bool]:
    """Convert paths to dicts, truncate to max_results and the output budget."""
    matches: list[dict] = []
    chars = 0
    for p in files:
        if len(matches) >= max_results or chars >= _MAX_OUTPUT_CHARS:
            return matches, True
        match = _to_match(p)
        matches.append(match)
        chars += len(match["path"]) + _APPROX_MATCH_CHARS
    return matches, False


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
