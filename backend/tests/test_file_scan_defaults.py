"""Broad file scans skip generated dirs and stay within bounded output."""

from __future__ import annotations

import pytest

from src.agent.exceptions import ToolContext
from src.agent.tools.groups.file_ops import glob as glob_tool
from src.agent.tools.groups.file_ops import grep as grep_tool
from src.agent.tools.groups.file_ops import list_files as list_files_tool

IGNORED_DIRS = [
    "node_modules", ".venv", "venv", ".pio", ".platformio", "dist",
    "build", "__pycache__", ".pytest_cache",
]


@pytest.fixture(autouse=True)
def _allow_tmp_paths(monkeypatch):
    for module in (list_files_tool, glob_tool, grep_tool):
        monkeypatch.setattr(module, "validate_path", lambda path, is_write: (True, ""))
        monkeypatch.setattr(module, "is_path_allowed_for_tree", lambda path: True)


def _make_project(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.ino").write_text("void setup() {}\n", encoding="utf-8")
    (project / "README.md").write_text("# demo\n", encoding="utf-8")
    for name in IGNORED_DIRS:
        inner = project / name / "inner"
        inner.mkdir(parents=True)
        (inner / "ignored.py").write_text("needle\n", encoding="utf-8")
    return project


def _tree_names(tree: dict | None) -> set[str]:
    names: set[str] = set()
    stack = [tree]
    while stack:
        node = stack.pop()
        if not node:
            continue
        names.add(node["name"])
        stack.extend(node.get("children") or [])
    return names


def _tree_size(tree: dict | None) -> int:
    count = 0
    stack = [tree]
    while stack:
        node = stack.pop()
        if not node or node.get("type") == "marker":
            continue
        count += 1
        stack.extend(node.get("children") or [])
    return count


async def test_list_files_skips_generated_dirs(tmp_path):
    project = _make_project(tmp_path)

    result = await list_files_tool.ListFilesTool().execute(
        {"path": str(project)}, ToolContext(),
    )

    names = _tree_names(result["tree"])
    assert {"src", "main.ino", "README.md"} <= names
    assert names.isdisjoint(IGNORED_DIRS)
    assert "inner" not in names
    assert result["truncated"] is False


async def test_list_files_explicit_ignored_root_is_allowed(tmp_path):
    project = _make_project(tmp_path)

    result = await list_files_tool.ListFilesTool().execute(
        {"path": str(project / "node_modules")}, ToolContext(),
    )

    names = _tree_names(result["tree"])
    assert {"inner", "ignored.py"} <= names


async def test_list_files_include_ignored_flag(tmp_path):
    project = _make_project(tmp_path)

    result = await list_files_tool.ListFilesTool().execute(
        {"path": str(project), "include_ignored": True}, ToolContext(),
    )

    assert "node_modules" in _tree_names(result["tree"])


async def test_list_files_plan_mode_caps_depth(tmp_path):
    project = _make_project(tmp_path)
    deep = project / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep.txt").write_text("deep\n", encoding="utf-8")

    plan = await list_files_tool.ListFilesTool().execute(
        {"path": str(project)}, ToolContext(permission_mode="plan"),
    )
    normal = await list_files_tool.ListFilesTool().execute(
        {"path": str(project), "max_depth": 6}, ToolContext(),
    )

    assert "c" not in _tree_names(plan["tree"])
    assert "deep.txt" in _tree_names(normal["tree"])


async def test_list_files_plan_mode_caps_nodes(tmp_path):
    project = tmp_path / "flat"
    project.mkdir()
    for index in range(150):
        (project / f"f{index:03}.txt").write_text("x", encoding="utf-8")

    result = await list_files_tool.ListFilesTool().execute(
        {"path": str(project)}, ToolContext(permission_mode="plan"),
    )

    assert result["truncated"] is True
    assert _tree_size(result["tree"]) <= list_files_tool._PLAN_MODE_MAX_NODES


async def test_glob_skips_generated_dirs_by_default(tmp_path):
    project = _make_project(tmp_path)

    result = await glob_tool.GlobTool().execute(
        {"pattern": "**/*.py", "path": str(project)}, ToolContext(),
    )

    assert result["matches"] == []


async def test_glob_explicit_pattern_reaches_ignored_dir(tmp_path):
    project = _make_project(tmp_path)

    result = await glob_tool.GlobTool().execute(
        {"pattern": "node_modules/**/*.py", "path": str(project)}, ToolContext(),
    )

    assert result["count"] == 1
    assert "node_modules" in result["matches"][0]["path"]


async def test_glob_include_ignored_flag(tmp_path):
    project = _make_project(tmp_path)

    result = await glob_tool.GlobTool().execute(
        {"pattern": "**/*.py", "path": str(project), "include_ignored": True},
        ToolContext(),
    )

    assert result["count"] == len(IGNORED_DIRS)


async def test_glob_handles_repeated_double_star_pattern(tmp_path):
    project = _make_project(tmp_path)

    result = await glob_tool.GlobTool().execute(
        {"pattern": "**/**/**/**/**/**/*.ino", "path": str(project)},
        ToolContext(),
    )

    assert result["count"] == 1
    assert result["matches"][0]["path"].endswith("main.ino")


async def test_grep_skips_generated_dirs_by_default(tmp_path):
    project = _make_project(tmp_path)

    result = await grep_tool.GrepTool().execute(
        {"pattern": "needle", "path": str(project)}, ToolContext(),
    )

    assert result["matches"] == []


async def test_grep_explicit_root_and_include_ignored(tmp_path):
    project = _make_project(tmp_path)

    explicit = await grep_tool.GrepTool().execute(
        {"pattern": "needle", "path": str(project / "node_modules")}, ToolContext(),
    )
    broad = await grep_tool.GrepTool().execute(
        {"pattern": "needle", "path": str(project), "include_ignored": True},
        ToolContext(),
    )

    assert explicit["count"] == 1
    assert broad["count"] == len(IGNORED_DIRS)


async def test_grep_truncates_long_match_lines(tmp_path):
    project = _make_project(tmp_path)
    long_line = "needle " + "x" * 1000
    (project / "long.txt").write_text(long_line, encoding="utf-8")

    result = await grep_tool.GrepTool().execute(
        {"pattern": "needle", "path": str(project / "long.txt")}, ToolContext(),
    )

    content = result["matches"][0]["content"]
    assert len(content) == grep_tool._MAX_MATCH_CONTENT_CHARS + 1
    assert content.endswith("…")


async def test_grep_output_budget_stops_matches(tmp_path):
    project = _make_project(tmp_path)
    lines = "\n".join("needle " + "y" * 500 for _ in range(120))
    (project / "many.txt").write_text(lines, encoding="utf-8")

    result = await grep_tool.GrepTool().execute(
        {"pattern": "needle", "path": str(project / "many.txt")}, ToolContext(),
    )

    assert result["truncated"] is True
    assert len(result["matches"]) < grep_tool._DEFAULT_MAX_RESULTS
    total_chars = sum(len(match["content"]) for match in result["matches"])
    assert total_chars <= (
        grep_tool._MAX_OUTPUT_CHARS + grep_tool._MAX_MATCH_CONTENT_CHARS
    )


async def test_grep_plan_mode_caps_matches(tmp_path):
    project = _make_project(tmp_path)
    lines = "\n".join("needle" for _ in range(120))
    (project / "many.txt").write_text(lines, encoding="utf-8")

    result = await grep_tool.GrepTool().execute(
        {"pattern": "needle", "path": str(project / "many.txt")},
        ToolContext(permission_mode="plan"),
    )

    assert len(result["matches"]) == grep_tool._PLAN_MODE_MAX_RESULTS
    assert result["truncated"] is True
