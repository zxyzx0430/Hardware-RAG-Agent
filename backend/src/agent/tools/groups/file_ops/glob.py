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
import fnmatch
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import resolve_agent_path, validate_path, is_path_allowed_for_tree
from src.agent.tools.groups.file_ops._scan_policy import (
    explicit_ignored_segments,
    iter_scan_files,
)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

_DEFAULT_SEARCH_ROOT: str = "."
_DEFAULT_MAX_RESULTS: int = 100
_PLAN_MODE_MAX_RESULTS: int = 50
_MAX_OUTPUT_CHARS: int = 16_000
_MAX_SCAN_FILES: int = 50_000
_SEARCH_BUDGET_SECONDS: float = 8.0
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
        if not Path(path).exists():
            return _error(f"path not found: {path}")
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
    """Match glob pattern under root, sort by mtime desc, truncate.

    Uses one pruned walk instead of ``Path.glob`` so ignored directories and
    symlink/junction loops are never traversed (a junction loop previously
    duplicated matches exponentially before truncation).
    """
    root_path = Path(root)
    if not root_path.exists():
        return [], False
    pattern_parts = _pattern_parts(pattern)
    if not pattern_parts:
        return [], False
    allowed_ignored = explicit_ignored_segments(pattern)
    matches: list[Path] = []
    scanned = 0
    truncated = False
    deadline = time.monotonic() + _SEARCH_BUDGET_SECONDS
    for file_path in iter_scan_files(
        root_path,
        include_ignored=include_ignored,
        allowed_ignored=allowed_ignored,
        max_depth=None if "**" in pattern_parts else len(pattern_parts) - 1,
    ):
        scanned += 1
        if scanned > _MAX_SCAN_FILES or time.monotonic() > deadline:
            truncated = True
            break
        try:
            rel_parts = file_path.relative_to(root_path).parts
        except ValueError:
            continue
        if not _matches_glob(rel_parts, pattern_parts):
            continue
        if not _has_mtime(file_path):
            continue
        if not is_path_allowed_for_tree(str(file_path)):
            continue
        matches.append(file_path)
    matches.sort(key=_get_mtime, reverse=True)
    listed, budget_truncated = _truncate_matches(matches, max_results)
    return listed, truncated or budget_truncated


def _pattern_parts(pattern: str) -> list[str]:
    """Normalize a relative glob pattern; reject absolute/drive patterns."""
    normalized = str(pattern or "").replace("\\", "/")
    if normalized.startswith("/") or (
        len(normalized) > 1 and normalized[1] == ":"
    ):
        raise ValueError("glob pattern must be relative (no drive or leading slash)")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    return parts


def _matches_glob(rel_parts: tuple[str, ...], pattern_parts: list[str]) -> bool:
    """Match path segments against pattern segments with ``**`` support.

    Dynamic programming keeps a pathological pattern with many ``**``
    segments linear in segments x pattern length instead of exponential.
    """
    pattern_count = len(pattern_parts)
    part_count = len(rel_parts)
    matches = [[False] * (part_count + 1) for _ in range(pattern_count + 1)]
    matches[pattern_count][part_count] = True
    for pattern_index in range(pattern_count - 1, -1, -1):
        part = pattern_parts[pattern_index]
        next_row = matches[pattern_index + 1]
        row = matches[pattern_index]
        if part == "**":
            row[part_count] = next_row[part_count]
            for part_index in range(part_count - 1, -1, -1):
                row[part_index] = next_row[part_index] or row[part_index + 1]
        else:
            for part_index in range(part_count - 1, -1, -1):
                row[part_index] = (
                    fnmatch.fnmatch(rel_parts[part_index], part)
                    and next_row[part_index + 1]
                )
    return matches[0][0]


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


def _check_path(path: str) -> dict | None:
    """Return error envelope if path invalid, else None."""
    ok, reason = validate_path(path, is_write=False)
    if not ok:
        return _error(f"path not allowed: {reason}")
    return None


def _error(msg: str) -> dict:
    """Build an error response envelope."""
    return {"matches": [], "truncated": False, "count": 0, "error": msg}
