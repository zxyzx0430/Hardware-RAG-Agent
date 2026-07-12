"""Deprecated shim — tools migrated to tools/groups/file_ops/ (Task 1, SubTask 1.13).

The 3 file tools now live at:
    src.agent.tools.groups.file_ops.read_file
    src.agent.tools.groups.file_ops.write_file
    src.agent.tools.groups.file_ops.edit_file

The shared permission helper (enforce_permission) lives at:
    src.agent.tools.groups.file_ops._permission

This file re-exports them for backward compatibility. Note that
run_command.py (legacy) imports enforce_permission from here; the migrated
version at groups/execution/run_command.py imports it directly from
groups.file_ops.
"""

from __future__ import annotations

from src.agent.tools.groups.file_ops import enforce_permission
from src.agent.tools.groups.file_ops.edit_file import (
    EditFileArgs,
    EditFileTool,
    _apply_replace,
    _do_edit,
    _edit_file_sync,
    _read_content,
    _write_back,
)
from src.agent.tools.groups.file_ops.read_file import (
    DEFAULT_READ_LIMIT,
    DEFAULT_READ_OFFSET,
    MAX_OUTPUT_CHARS,
    ReadFileArgs,
    ReadFileTool,
    TRUNCATE_SUFFIX,
    _do_read,
    _read_file_sync,
    _truncate,
)
from src.agent.tools.groups.file_ops.write_file import (
    WriteFileArgs,
    WriteFileTool,
    _do_write,
    _write_file_sync,
)

__all__ = [
    "ReadFileTool",
    "ReadFileArgs",
    "WriteFileTool",
    "WriteFileArgs",
    "EditFileTool",
    "EditFileArgs",
    "enforce_permission",
    # constants
    "DEFAULT_READ_LIMIT",
    "DEFAULT_READ_OFFSET",
    "MAX_OUTPUT_CHARS",
    "TRUNCATE_SUFFIX",
    # helpers
    "_do_read",
    "_read_file_sync",
    "_do_write",
    "_write_file_sync",
    "_do_edit",
    "_edit_file_sync",
    "_read_content",
    "_apply_replace",
    "_write_back",
    "_truncate",
]
