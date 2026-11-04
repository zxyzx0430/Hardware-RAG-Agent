"""Content search across explorer-authorized directories."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Iterator

from src.explorer.files import _is_text_file
from src.explorer.security import ExplorerSecurityError, _assert_not_system, is_denied_component

MAX_SEARCH_RESULTS = 100
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB


def search_content(
    root: Path,
    query: str,
    max_results: int = MAX_SEARCH_RESULTS,
    include_pattern: str = "*",
) -> list[dict[str, Any]]:
    """Search text file contents under *root* for case-insensitive *query*."""
    if not query:
        return []
    needle = query.lower()
    results: list[dict[str, Any]] = []
    for file_path in _walk_text_files(root, include_pattern):
        _append_matches(file_path, needle, results)
        if len(results) >= max_results:
            return results[:max_results]
    return results


def _walk_text_files(root: Path, include_pattern: str) -> Iterator[Path]:
    """Yield searchable text files under *root* matching *include_pattern*."""
    try:
        real_root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return

    stack = [root]
    visited_directories = {real_root}
    while stack:
        for entry in _safe_iterdir(stack.pop()):
            try:
                if entry.is_symlink():
                    continue
                real_entry = entry.resolve(strict=True)
                relative_entry = real_entry.relative_to(real_root)
                is_directory = entry.is_dir()
            except (OSError, RuntimeError, ValueError):
                # resolve() follows Windows junctions. A target outside the
                # explicitly opened root is rejected before it can be read.
                continue

            try:
                _assert_not_system(real_entry)
            except ExplorerSecurityError:
                continue

            if any(_is_denied_name(part) for part in relative_entry.parts):
                continue
            if is_directory:
                _maybe_descend(stack, entry, real_entry, visited_directories)
            elif _is_searchable(entry, include_pattern):
                yield entry


def _safe_iterdir(path: Path) -> list[Path]:
    """Return directory entries, or an empty list on permission/OS errors."""
    try:
        return list(path.iterdir())
    except (PermissionError, OSError):
        return []


def _maybe_descend(
    stack: list[Path],
    entry: Path,
    real_entry: Path,
    visited_directories: set[Path],
) -> None:
    """Push a safe directory once, avoiding loops through in-root junctions."""
    if _is_denied_name(entry.name) or real_entry in visited_directories:
        return
    visited_directories.add(real_entry)
    stack.append(entry)


def _is_denied_name(name: str) -> bool:
    """Return True if *name* matches any deny pattern."""
    return is_denied_component(name)


def _is_searchable(entry: Path, include_pattern: str) -> bool:
    """Return True if *entry* is a small text file matching *include_pattern*."""
    if entry.is_symlink() or is_denied_component(entry.name) or entry.stat().st_size > MAX_FILE_SIZE:
        return False
    if not fnmatch.fnmatch(entry.name, include_pattern):
        return False
    return _is_text_file(entry)


def _append_matches(path: Path, needle: str, results: list[dict[str, Any]]) -> None:
    """Append case-insensitive line matches from *path* into *results*."""
    for line_number, line_text in _read_lines(path):
        count = line_text.lower().count(needle)
        if not count:
            continue
        results.append({"path": str(path), "name": path.name, "line_number": line_number, "line_text": line_text, "match_count": count})


def _read_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield (line_number, line_text) tuples; skip files that cannot be read."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for idx, line in enumerate(handle, start=1):
                yield idx, line.rstrip("\n")
    except (PermissionError, OSError):
        return
