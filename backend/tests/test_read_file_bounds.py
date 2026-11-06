"""read_file byte/line/output bounds."""

from __future__ import annotations

import pytest

from src.agent.exceptions import ToolContext
from src.agent.tools.groups.file_ops import read_file as read_file_module
from src.agent.tools.groups.file_ops.read_file import ReadFileTool


@pytest.fixture(autouse=True)
def _allow_tmp_paths(monkeypatch):
    monkeypatch.setattr(read_file_module, "validate_path", lambda path, is_write: (True, ""))
    monkeypatch.setattr(read_file_module, "resolve_agent_path", lambda path: path)


async def _read(tmp_path, *, offset=0, limit=2000, mode="default"):
    target = tmp_path / "sample.txt"
    return await ReadFileTool().execute(
        {"path": str(target), "offset": offset, "limit": limit},
        ToolContext(permission_mode=mode),
    )


def _write(tmp_path, lines):
    target = tmp_path / "sample.txt"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


async def test_offset_and_limit_select_exact_window(tmp_path):
    _write(tmp_path, [f"line-{index}" for index in range(20)])

    result = await _read(tmp_path, offset=10, limit=3)

    assert result["output"] == "line-10\nline-11\nline-12\n"


async def test_plan_mode_output_cap(tmp_path):
    _write(tmp_path, ["a" * 2500, "b" * 2500])

    result = await _read(tmp_path, mode="plan")

    assert len(result["output"]) == read_file_module.PLAN_MAX_OUTPUT_CHARS + len(
        read_file_module.TRUNCATE_SUFFIX
    )
    assert result["output"].endswith(read_file_module.TRUNCATE_SUFFIX)


async def test_default_mode_output_cap(tmp_path):
    _write(tmp_path, ["a" * 2500, "b" * 2500])

    result = await _read(tmp_path, mode="default")

    assert len(result["output"]) == read_file_module.MAX_OUTPUT_CHARS + len(
        read_file_module.TRUNCATE_SUFFIX
    )


async def test_line_cap_is_absolute(tmp_path, monkeypatch):
    monkeypatch.setattr(read_file_module, "MAX_READ_LINES", 3)
    _write(tmp_path, [f"line-{index}" for index in range(10)])

    result = await _read(tmp_path, limit=100)

    assert result["output"] == "line-0\nline-1\nline-2\n"


async def test_byte_cap_stops_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(read_file_module, "MAX_READ_BYTES", 100)
    _write(tmp_path, ["x" * 60 for _ in range(5)])

    result = await _read(tmp_path, limit=100)

    assert result["output"].count("x") == 60
    assert result["output"].endswith(read_file_module.TRUNCATE_SUFFIX)
