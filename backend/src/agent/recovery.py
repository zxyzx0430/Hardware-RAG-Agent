"""Recovery R1 policy: which tools may be replayed after a restart.

Read-only retrieval/file tools are safe to replay. Any tool that mutates the
filesystem, runs a process, or talks to hardware must never be replayed
automatically; its recorded outcome is reused or the run is left interrupted.
"""
from __future__ import annotations

READ_ONLY_REPLAY_TOOLS = frozenset({
    "search_docs",
    "list_kb_docs",
    "read_file",
    "grep",
    "glob",
    "list_files",
})

SIDE_EFFECT_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "multi_edit",
    "apply_patch",
    "undo_edit",
    "run_command",
    "build_firmware",
    "flash_firmware",
})


def can_replay_tool(tool_name: str) -> bool:
    """Return True only for explicitly read-only tools."""
    return tool_name in READ_ONLY_REPLAY_TOOLS


def is_replay_safe(tool_name: str) -> bool:
    """Alias used by recovery callers/tests."""
    return can_replay_tool(tool_name)
