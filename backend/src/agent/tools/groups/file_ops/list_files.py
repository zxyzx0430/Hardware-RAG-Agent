"""
Hardware RAG Agent — ListFilesTool (file_ops group).

Browse a directory tree (recursive, depth-limited). Returns nodes with
name/type/size/mtime; directories carry children. Source: OpenCode /
OpenHands tool survey (spec add-grep-glob-todo-tools §list_files).

Distinguishes from glob: glob matches a KNOWN filename pattern; this
tool EXPLORES an UNKNOWN directory structure.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import resolve_agent_path, validate_path, is_path_allowed_for_tree
from src.agent.tools.groups.file_ops._scan_policy import is_default_ignored_dir

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

_DEFAULT_SEARCH_ROOT: str = "."
_DEFAULT_MAX_DEPTH: int = 3
_MAX_DEPTH_LIMIT: int = 6
_DEFAULT_MAX_NODES: int = 150
_MAX_OUTPUT_CHARS: int = 24_000
_PLAN_MODE_MAX_DEPTH: int = 2
_PLAN_MODE_MAX_NODES: int = 80
_PLAN_MODE_MAX_OUTPUT_CHARS: int = 12_000
_APPROX_NODE_CHARS: int = 80
_TRUNCATED_SUFFIX: str = "... (truncated)"
_PLAN_PERMISSION_MODE: str = "plan"


# ═══════════════════════════════════════════
# ListFilesTool
# ═══════════════════════════════════════════

class ListFilesArgs(BaseModel):
    path: str = Field(default=_DEFAULT_SEARCH_ROOT, description="要列出的目录路径（建议传绝对路径）")
    max_depth: int = Field(default=_DEFAULT_MAX_DEPTH, description="递归最大深度（默认 3，上限 6）")
    include_ignored: bool = Field(
        default=False,
        description="为 true 时同时列出依赖/构建/缓存目录（node_modules、.venv、.git 等）",
    )


class ListFilesOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    tree: dict | None = Field(None, description="目录树根节点")
    truncated: bool = Field(False, description="是否因超过节点上限被截断")


@dataclass
class _ListCfg:
    """Bundle traversal options; counters are 1-element mutable lists."""
    max_depth: int
    include_ignored: bool = False
    counter: list[int] = field(default_factory=lambda: [0])
    max_nodes: int = _DEFAULT_MAX_NODES
    chars: list[int] = field(default_factory=lambda: [0])
    max_chars: int = _MAX_OUTPUT_CHARS


class ListFilesTool(ToolSpec):
    """List directory tree with depth limit."""
    name: str = "list_files"
    description: str = (
        "列出指定目录的文件树（含文件大小/修改时间），递归深度默认 3 层（上限 6）。"
        "与 glob 区分：已知文件名模式用 glob，探索未知目录结构用 list_files。"
        "默认跳过依赖/构建/缓存目录（node_modules、.venv、venv、.git、.pio、"
        ".platformio、dist、build、__pycache__、.pytest_cache 等）；"
        "需要查看这些目录时传 include_ignored=true，或直接把 path 指向该目录。"
        "计划模式下降到 2 层并收紧到 80 节点；普通模式 150 节点、约 24k 字符。"
        "敏感路径（.git/.env/*.key/*.pem）会被拦截。"
    )
    args_schema: type = ListFilesArgs
    output_schema: type[BaseModel] | None = ListFilesOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 15
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """List files under root as a tree."""
        path = resolve_agent_path(args.get("path", _DEFAULT_SEARCH_ROOT))
        if not path:
            return _error("path could not be resolved")
        err = _check_path(path)
        if err:
            return err
        max_depth = min(int(args.get("max_depth", _DEFAULT_MAX_DEPTH)), _MAX_DEPTH_LIMIT)
        plan_mode = getattr(ctx, "permission_mode", "") == _PLAN_PERMISSION_MODE
        if plan_mode:
            max_depth = min(max_depth, _PLAN_MODE_MAX_DEPTH)
        cfg = _ListCfg(
            max_depth=max_depth,
            include_ignored=bool(args.get("include_ignored", False)),
            max_nodes=_PLAN_MODE_MAX_NODES if plan_mode else _DEFAULT_MAX_NODES,
            max_chars=_PLAN_MODE_MAX_OUTPUT_CHARS if plan_mode else _MAX_OUTPUT_CHARS,
        )
        return await _run_list(path, cfg)


async def _run_list(root: str, cfg: _ListCfg) -> dict:
    """Run sync list in a worker thread, return envelope dict."""
    tree, truncated = await asyncio.to_thread(_build_tree, root, cfg)
    return {"tree": tree, "truncated": truncated}


def _build_tree(root: str, cfg: _ListCfg) -> tuple[dict | None, bool]:
    """Build the entire tree from root, return (tree, truncated)."""
    root_path = Path(root)
    if not root_path.exists():
        return None, False
    node = _build_node(root_path, 1, cfg)
    truncated = cfg.counter[0] >= cfg.max_nodes or cfg.chars[0] >= cfg.max_chars
    return node, truncated


def _build_node(p: Path, depth: int, cfg: _ListCfg) -> dict | None:
    """Build a single tree node, return None if a cap is reached."""
    if cfg.counter[0] >= cfg.max_nodes or cfg.chars[0] >= cfg.max_chars:
        return None
    cfg.counter[0] += 1
    cfg.chars[0] += len(p.name) + _APPROX_NODE_CHARS
    if cfg.chars[0] > cfg.max_chars:
        return None
    if not _is_dir(p):
        return _node_dict(p, "file", None)
    return _node_dict(p, "dir", _collect_children(p, depth, cfg))


def _collect_children(p: Path, depth: int, cfg: _ListCfg) -> list[dict]:
    """Iterate dir children; append truncated marker when a cap is reached.

    depth is the parent node's depth (root=1). We expand children when
    parent depth <= max_depth, so max_depth=N yields N levels of children
    (max_depth=0 = root only, max_depth=1 = root + direct children).
    """
    if depth > cfg.max_depth:
        return []
    children: list[dict] = []
    for child in _iter_children(p):
        if not cfg.include_ignored and is_default_ignored_dir(child.name):
            continue
        node = _build_node(child, depth + 1, cfg)
        if node is None:
            children.append(_truncated_marker())
            break
        children.append(node)
    return children


def _iter_children(p: Path) -> Iterator[Path]:
    """Yield non-ignored children of a dir, sorted by name."""
    try:
        items = sorted(p.iterdir(), key=lambda x: x.name)
    except OSError:
        return
    for child in items:
        if not is_path_allowed_for_tree(str(child)):
            continue
        yield child


def _node_dict(p: Path, kind: str, children: list[dict] | None) -> dict:
    """Build the common node dict."""
    return {
        "name": p.name,
        "type": kind,
        "size_bytes": _get_size(p) if kind == "file" else 0,
        "modified_at": _get_mtime_iso(p),
        "children": children,
    }


def _truncated_marker() -> dict:
    """Build a marker node indicating the tree was truncated."""
    return {"name": _TRUNCATED_SUFFIX, "type": "marker",
            "size_bytes": 0, "modified_at": "", "children": None}


def _is_dir(p: Path) -> bool:
    """Return True if path is a directory (fail-closed on error)."""
    try:
        return p.is_dir()
    except OSError:
        return True


def _get_size(p: Path) -> int:
    """Return file size in bytes (0 on error)."""
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _get_mtime_iso(p: Path) -> str:
    """Return ISO-format mtime string ('' on error)."""
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat()
    except OSError:
        return ""


def _check_path(path: str) -> dict | None:
    """Return error envelope if path invalid, else None."""
    ok, reason = validate_path(path, is_write=False)
    if not ok:
        return _error(f"path not allowed: {reason}")
    return None


def _error(msg: str) -> dict:
    """Build an error response envelope."""
    return {"tree": None, "truncated": False, "error": msg}
