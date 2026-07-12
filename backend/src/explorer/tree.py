from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from src.explorer.security import DENY_PATTERNS


def build_tree(path: Path) -> dict[str, Any]:
    """Build the root node for *path*.

    Only immediate children are populated. Sub-directories are marked with
    ``lazy: true`` so the frontend can load them on demand via
    :func:`build_tree_for_dir`. This keeps the initial payload small even
    when the project contains huge generated directories such as
    ``.platformio`` or ``node_modules``.
    """
    node = _tree_node(path)
    if path.is_dir():
        node["children"] = _build_children(path)
    return node


def build_tree_for_dir(path: Path) -> list[dict[str, Any]]:
    """Return the immediate children of *path* for on-demand loading."""
    return _build_children(path)


def _build_children(path: Path) -> list[dict[str, Any]]:
    """Build nodes for the direct children of *path*.

    Directory children are marked ``lazy: true`` so the frontend can fetch
    their contents when the user expands them.
    """
    children: list[dict[str, Any]] = []
    for child in _sorted_children(path):
        try:
            if child.is_symlink():
                continue
        except OSError:
            continue
        child_node = _tree_node(child)
        if child.is_dir():
            child_node["lazy"] = True
        children.append(child_node)
    return children


def _tree_node(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "type": "directory" if path.is_dir() else "file",
        "path": str(path),
    }


def _sorted_children(path: Path) -> list[Path]:
    try:
        children = [child for child in path.iterdir() if _is_visible_name(child)]
    except PermissionError:
        return []
    children.sort(key=lambda c: (not c.is_dir(), c.name.lower()))
    return children


def _is_visible_name(path: Path) -> bool:
    """快速名字过滤，不调用 resolve（避免文件系统访问）。

    安全性保证：
    1. 根目录已通过 validate_path 验证
    2. 符号链接在 _build_children 中被跳过
    3. DENY_PATTERNS 的文件/目录被跳过
    4. iterdir 只返回直接子项，都在根目录内
    """
    name = path.name
    for pat in DENY_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return False
    return True
