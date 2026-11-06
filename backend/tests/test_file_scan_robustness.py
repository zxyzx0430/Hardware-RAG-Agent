"""Robustness regressions: junction loops, binary files, permission errors."""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import subprocess

import pytest

from src.agent.exceptions import ToolContext
from src.agent.tools.groups.file_ops import glob as glob_tool
from src.agent.tools.groups.file_ops import grep as grep_tool
from src.agent.tools.groups.file_ops import list_files as list_files_tool
from src.agent.tools.groups.file_ops import read_file as read_file_tool


@pytest.fixture(autouse=True)
def _allow_tmp_paths(monkeypatch):
    for module in (list_files_tool, glob_tool, grep_tool):
        monkeypatch.setattr(module, "validate_path", lambda path, is_write: (True, ""))
        monkeypatch.setattr(module, "is_path_allowed_for_tree", lambda path: True)
    monkeypatch.setattr(
        read_file_tool, "validate_path", lambda path, is_write: (True, ""),
    )
    monkeypatch.setattr(read_file_tool, "resolve_agent_path", lambda path: path)


def _make_project(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "file.txt").write_text("hello\n", encoding="utf-8")
    return project


def _make_junction(link, target):
    if os.name != "nt":
        pytest.skip("junction test requires Windows")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"could not create a temporary junction: {result.stderr}")
    return link


def _remove_junction(link):
    if os.path.isjunction(link):
        os.rmdir(link)


def _count_tree_name(tree, name):
    count = 0
    stack = [tree]
    while stack:
        node = stack.pop()
        if not node:
            continue
        if node.get("name") == name:
            count += 1
        stack.extend(node.get("children") or [])
    return count


async def test_list_files_does_not_follow_junction_loop(tmp_path):
    project = _make_project(tmp_path)
    link = _make_junction(project / "src" / "back", project)
    try:
        result = await list_files_tool.ListFilesTool().execute(
            {"path": str(project), "max_depth": 6}, ToolContext(),
        )
    finally:
        _remove_junction(link)

    assert result["tree"] is not None
    assert _count_tree_name(result["tree"], "back") == 0
    assert _count_tree_name(result["tree"], "file.txt") == 1


async def test_glob_does_not_follow_junction_loop(tmp_path):
    project = _make_project(tmp_path)
    link = _make_junction(project / "src" / "back", project)
    try:
        result = await glob_tool.GlobTool().execute(
            {"pattern": "**/*.txt", "path": str(project)}, ToolContext(),
        )
    finally:
        _remove_junction(link)

    paths = [match["path"] for match in result["matches"]]
    assert len(paths) == len(set(paths)) == 1


async def test_grep_does_not_follow_junction_loop(tmp_path):
    project = _make_project(tmp_path)
    link = _make_junction(project / "src" / "back", project)
    try:
        result = await grep_tool.GrepTool().execute(
            {"pattern": "hello", "path": str(project)}, ToolContext(),
        )
    finally:
        _remove_junction(link)

    files = [match["file"] for match in result["matches"]]
    assert len(files) == len(set(files)) == 1


async def test_glob_rejects_absolute_pattern(tmp_path):
    project = _make_project(tmp_path)

    with pytest.raises(ValueError, match="relative"):
        await glob_tool.GlobTool().execute(
            {"pattern": str(tmp_path / "*.txt"), "path": str(project)},
            ToolContext(),
        )


async def test_read_file_rejects_binary(tmp_path):
    target = tmp_path / "binary.bin"
    target.write_bytes(b"\x00\x01\x02binary\x00data" * 100)

    result = await read_file_tool.ReadFileTool().execute(
        {"path": str(target)}, ToolContext(),
    )

    assert "binary file" in result["output"]
    assert len(result["output"]) < 300


async def test_read_file_reports_missing_file(tmp_path):
    result = await read_file_tool.ReadFileTool().execute(
        {"path": str(tmp_path / "missing.txt")}, ToolContext(),
    )

    assert result["output"].startswith("Error: file not found")


async def test_read_file_reports_permission_denied(tmp_path, monkeypatch):
    target = tmp_path / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    real_open = builtins.open

    def denying_open(path, *args, **kwargs):
        if str(path) == str(target):
            raise PermissionError(13, "permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denying_open)

    result = await read_file_tool.ReadFileTool().execute(
        {"path": str(target)}, ToolContext(),
    )

    assert "permission denied" in result["output"]
    assert len(result["output"]) < 300


async def test_read_file_long_line_is_bounded(tmp_path):
    target = tmp_path / "long.txt"
    target.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")

    result = await read_file_tool.ReadFileTool().execute(
        {"path": str(target)}, ToolContext(permission_mode="plan"),
    )

    assert len(result["output"]) == (
        read_file_tool.PLAN_MAX_OUTPUT_CHARS
        + len(read_file_tool.TRUNCATE_SUFFIX)
    )
    assert result["output"].endswith(read_file_tool.TRUNCATE_SUFFIX)


async def test_read_file_offset_skips_an_oversized_line(tmp_path):
    target = tmp_path / "long_then_short.txt"
    target.write_text("y" * (1024 * 1024) + "\nsecond\n", encoding="utf-8")

    result = await read_file_tool.ReadFileTool().execute(
        {"path": str(target), "offset": 1, "limit": 1}, ToolContext(),
    )

    assert result["output"] == "second\n"


async def test_missing_scan_roots_return_clear_errors(tmp_path):
    missing = tmp_path / "missing-dir"

    listed = await list_files_tool.ListFilesTool().execute(
        {"path": str(missing)}, ToolContext(),
    )
    globbed = await glob_tool.GlobTool().execute(
        {"pattern": "*.txt", "path": str(missing)}, ToolContext(),
    )
    grepped = await grep_tool.GrepTool().execute(
        {"pattern": "needle", "path": str(missing)}, ToolContext(),
    )

    for result in (listed, globbed, grepped):
        assert "path not found" in result["error"]


async def test_list_files_rejects_a_file_path(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")

    result = await list_files_tool.ListFilesTool().execute(
        {"path": str(target)}, ToolContext(),
    )

    assert "not a directory" in result["error"]


async def test_concurrent_tool_calls_are_bounded(tmp_path):
    project = _make_project(tmp_path)
    docs = project / "docs"
    docs.mkdir()
    for index in range(60):
        (docs / f"doc-{index:03d}.txt").write_text(
            "needle line\n" * 20, encoding="utf-8",
        )

    async def one(index: int) -> dict:
        ctx = ToolContext(permission_mode="plan", context_token_limit=32_768)
        kind = index % 4
        if kind == 0:
            return await list_files_tool.ListFilesTool().execute(
                {"path": str(project), "max_depth": 4}, ctx,
            )
        if kind == 1:
            return await glob_tool.GlobTool().execute(
                {"pattern": "**/*.txt", "path": str(project)}, ctx,
            )
        if kind == 2:
            return await grep_tool.GrepTool().execute(
                {"pattern": "needle", "path": str(project)}, ctx,
            )
        return await read_file_tool.ReadFileTool().execute(
            {"path": str(project / "src" / "file.txt")}, ctx,
        )

    results = await asyncio.gather(*(one(index) for index in range(16)))

    for result in results:
        assert len(json.dumps(result, ensure_ascii=False)) < 200_000
