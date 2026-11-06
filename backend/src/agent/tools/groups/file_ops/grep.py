"""
Hardware RAG Agent — GrepTool (file_ops group).

Regex search across file contents. Returns matching line + line number +
file path. Source: Claude Code / OpenCode tool survey (spec
add-grep-glob-todo-tools §grep).

Supports two path modes:
  * path is a file  -> grep that single file directly (bypass include filter).
  * path is a dir   -> recursive walk with ignored-dir pruning + caps.

Caps that bound runtime on large trees:
  * _MAX_FILE_COUNT     — stop after scanning N files.
  * _SEARCH_BUDGET_SECONDS — internal monotonic-clock deadline.
  * _MAX_FILE_SIZE_BYTES   — skip files larger than 1 MB.
  * _MAX_OUTPUT_CHARS      — total match output budget.
  * default-ignored dirs   — shared policy prunes node_modules / .git / etc.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import resolve_agent_path, validate_path, is_path_allowed_for_tree, matches_deny_pattern
from src.agent.tools.groups.file_ops._scan_policy import (
    is_default_ignored_dir,
    is_link_or_junction,
)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

_DEFAULT_SEARCH_ROOT: str = "."
_DEFAULT_INCLUDE: str = "*"
_DEFAULT_MAX_RESULTS: int = 100
_PLAN_MODE_MAX_RESULTS: int = 50
_MAX_FILE_SIZE_BYTES: int = 1024 * 1024  # 1 MB — large file protection.
_MAX_FILE_COUNT: int = 500  # recursion file-count cap to bound runtime.
_SEARCH_BUDGET_SECONDS: float = 8.0  # internal budget; router caps at 10s.
_MAX_MATCH_CONTENT_CHARS: int = 400  # single-line output cap (minified files).
_MAX_OUTPUT_CHARS: int = 20_000  # total output budget across matches.
_APPROX_MATCH_CHARS: int = 40
_PLAN_PERMISSION_MODE: str = "plan"


# ═══════════════════════════════════════════
# GrepTool
# ═══════════════════════════════════════════

class GrepArgs(BaseModel):
    pattern: str = Field(description="正则表达式（Python re 语法）")
    path: str = Field(
        default=_DEFAULT_SEARCH_ROOT,
        description="搜索根目录或单文件路径（建议传绝对路径）",
    )
    include: str = Field(default=_DEFAULT_INCLUDE, description="文件名 glob 过滤，如 '*.py'")
    max_results: int = Field(default=_DEFAULT_MAX_RESULTS, description="最多返回匹配行数")
    include_ignored: bool = Field(
        default=False,
        description="为 true 时同时搜索依赖/构建/缓存目录（node_modules、.venv、.git 等）",
    )


class GrepOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    matches: list[dict] = Field(default_factory=list, description="匹配列表 [{file,line,content}]")
    truncated: bool = Field(
        False,
        description="是否因超过 max_results / 文件数上限 / 时间预算被截断",
    )


@dataclass(frozen=True)
class _GrepCfg:
    """Bundle of search options to keep function arity ≤ 3."""
    regex: re.Pattern
    include: str
    max_results: int
    include_ignored: bool = False


@dataclass
class _GrepState:
    """Mutable search state shared across the directory walk."""
    matches: list[dict] = field(default_factory=list)
    file_count: int = 0
    chars: int = 0
    deadline: float = 0.0


class GrepTool(ToolSpec):
    """Regex search across file contents under a dir or in a single file."""
    name: str = "grep"
    description: str = (
        "用正则表达式搜索指定目录或单文件内容，返回匹配行+行号+文件路径。"
        "支持 include 过滤文件类型（如 '*.py'）。"
        "path 可以是目录（递归搜索）或单文件（直接搜索该文件）。"
        "大文件（>1MB）自动跳过；递归上限 500 个文件，搜索预算 8s；"
        "默认跳过依赖/构建/缓存目录（node_modules、.venv、venv、.git、.pio、"
        ".platformio、dist、build、__pycache__、.pytest_cache 等），"
        "需要时传 include_ignored=true 或把 path 直接指向该目录。"
        "单行最多返回 400 字符，总输出约 20k 字符；计划模式最多 50 条匹配。"
        "敏感路径（.git/.env/*.key/*.pem）会被拦截。"
    )
    args_schema: type = GrepArgs
    output_schema: type[BaseModel] | None = GrepOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Grep file contents under root dir or in a single file."""
        path = resolve_agent_path(args.get("path", _DEFAULT_SEARCH_ROOT))
        if not path:
            return _error("path could not be resolved")
        err = _check_path(path)
        if err:
            return err
        if not Path(path).exists():
            return _error(f"path not found: {path}")
        regex = _compile_pattern(args.get("pattern", ""))
        if regex is None:
            return _error("invalid regex pattern")
        max_results = int(args.get("max_results", _DEFAULT_MAX_RESULTS))
        if getattr(ctx, "permission_mode", "") == _PLAN_PERMISSION_MODE:
            max_results = min(max_results, _PLAN_MODE_MAX_RESULTS)
        cfg = _GrepCfg(
            regex,
            args.get("include", _DEFAULT_INCLUDE),
            max_results,
            include_ignored=bool(args.get("include_ignored", False)),
        )
        return await _run_grep(path, cfg)


async def _run_grep(root: str, cfg: _GrepCfg) -> dict:
    """Run sync grep in a worker thread, return envelope dict."""
    matches, truncated = await asyncio.to_thread(_grep_sync, root, cfg)
    return {"matches": matches, "truncated": truncated, "count": len(matches)}


def _check_path(path: str) -> dict | None:
    """Return error envelope if path invalid, else None."""
    ok, reason = validate_path(path, is_write=False)
    if not ok:
        return _error(f"path not allowed: {reason}")
    return None


def _grep_sync(root: str, cfg: _GrepCfg) -> tuple[list[dict], bool]:
    """Dispatch to single-file or directory grep based on path type."""
    root_path = Path(root)
    if not root_path.exists():
        return [], False
    if root_path.is_file():
        return _grep_single_file(root_path, cfg)
    return _grep_dir(root_path, cfg)


def _grep_single_file(p: Path, cfg: _GrepCfg) -> tuple[list[dict], bool]:
    """Grep a single file directly (include filter is bypassed)."""
    state = _GrepState(deadline=time.monotonic() + _SEARCH_BUDGET_SECONDS)
    _scan_file(p, cfg, state)
    truncated = (
        len(state.matches) >= cfg.max_results
        or state.chars >= _MAX_OUTPUT_CHARS
    )
    return state.matches[:cfg.max_results], truncated


def _grep_dir(root_path: Path, cfg: _GrepCfg) -> tuple[list[dict], bool]:
    """Walk dir with os.walk; prune ignored dirs; enforce caps."""
    state = _GrepState(deadline=time.monotonic() + _SEARCH_BUDGET_SECONDS)
    truncated = _walk_and_collect(root_path, cfg, state)
    return state.matches[:cfg.max_results], truncated


def _walk_and_collect(root_path: Path, cfg: _GrepCfg, state: _GrepState) -> bool:
    """Walk tree, return True if truncated by any cap (files/time/matches)."""
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [
            d for d in dirnames
            if (cfg.include_ignored or not is_default_ignored_dir(d))
            and not is_link_or_junction(os.path.join(dirpath, d))
            and matches_deny_pattern(os.path.join(dirpath, d)) is None
        ]
        for name in filenames:
            state.file_count += 1
            if _should_stop(state, cfg):
                return True
            if _matches_include(name, cfg.include):
                _scan_file(Path(dirpath) / name, cfg, state)
    return False


def _should_stop(state: _GrepState, cfg: _GrepCfg) -> bool:
    """Return True if any cap (matches / output / file count / time) is exceeded."""
    return (
        len(state.matches) >= cfg.max_results
        or state.chars >= _MAX_OUTPUT_CHARS
        or state.file_count > _MAX_FILE_COUNT
        or time.monotonic() > state.deadline
    )


def _matches_include(name: str, include: str) -> bool:
    """Match filename against include glob (default '*' matches all)."""
    return fnmatch.fnmatch(name, include)


def _scan_file(p: Path, cfg: _GrepCfg, state: _GrepState) -> None:
    """Scan one file for pattern matches, append to state."""
    if not is_path_allowed_for_tree(str(p)):
        return
    if _is_too_large(p):
        return
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for idx, line in enumerate(text.splitlines(), start=1):
        if cfg.regex.search(line):
            content = line if len(line) <= _MAX_MATCH_CONTENT_CHARS else (
                line[:_MAX_MATCH_CONTENT_CHARS] + "…"
            )
            state.matches.append({"file": str(p), "line": idx, "content": content})
            state.chars += len(p.name) + len(content) + _APPROX_MATCH_CHARS
            if len(state.matches) >= cfg.max_results or state.chars >= _MAX_OUTPUT_CHARS:
                return


def _is_too_large(p: Path) -> bool:
    """Return True if file exceeds size limit (fail-closed on error)."""
    try:
        return p.stat().st_size > _MAX_FILE_SIZE_BYTES
    except OSError:
        return True


def _compile_pattern(pattern: str) -> re.Pattern | None:
    """Compile regex, return None on error."""
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _error(msg: str) -> dict:
    """Build an error response envelope."""
    return {"matches": [], "truncated": False, "count": 0, "error": msg}
