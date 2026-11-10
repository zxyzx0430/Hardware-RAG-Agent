"""Explicit, bounded validation runs for a ChangeSet.

Commands are user-triggered from the Changes UI, restricted to a small
allowlist, and executed through the existing RunCommandTool so timeout,
process-tree cleanup and output truncation stay consistent with Agent tools.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from app.db import change_store
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.execution.run_command import RunCommandTool

ALLOWED_PREFIXES: tuple[str, ...] = (
    "pytest",
    "python -m pytest",
    "python -m unittest",
    "npm test",
    "npm run",
    "npx tsc",
    "tsc",
    "pio",
    "platformio",
)
MAX_COMMAND_CHARS = 2000
# Commands go through PowerShell/Bash; a prefix match alone cannot prevent
# `pytest; Remove-Item ...`, redirection, or command substitution.
_SHELL_METACHARS = re.compile(r"[;&|`$<>\r\n\x00]")


def _inside_root(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        target = os.path.normcase(str(Path(path).resolve(strict=False)))
        workspace = os.path.normcase(str(Path(root).resolve(strict=False)))
        return os.path.commonpath([target, workspace]) == workspace
    except (OSError, ValueError):
        return False


def _allowed(command: str) -> bool:
    stripped = command.strip()
    if not stripped or _SHELL_METACHARS.search(stripped):
        return False
    return any(
        stripped == prefix or stripped.startswith(f"{prefix} ")
        for prefix in ALLOWED_PREFIXES
    )


async def run_validation(
    *,
    change_set: dict[str, Any],
    command: str,
    cwd: str = "",
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    command = command.strip()
    if not command or len(command) > MAX_COMMAND_CHARS:
        return {"ok": False, "error": "validation command is empty or too long"}
    if not _allowed(command):
        return {"ok": False, "error": "validation command is not in the allowlist"}
    workspace_root = str(change_set.get("workspace_root") or "")
    workdir = cwd.strip() or workspace_root
    if not _inside_root(workdir, workspace_root):
        return {"ok": False, "error": "validation cwd is outside the workspace root"}
    bounded_timeout = max(1000, min(int(timeout_ms), 300000))
    run = change_store.add_validation_run(
        change_set_id=change_set["id"],
        run_id=change_set.get("run_id"),
        command=command,
        cwd=workdir,
        status="running",
        timed_out=False,
    )
    _emit(change_set, "validation_started", {"validation_id": run["id"]})
    tool = RunCommandTool()
    ctx = ToolContext(
        session_id=str(change_set.get("session_id") or "default"),
        run_id=change_set.get("run_id"),
        permission_mode="yolo",
    )
    try:
        result = await tool.execute(
            {"command": command, "cwd": workdir, "timeout_ms": bounded_timeout},
            ctx,
        )
        timed_out = bool(result.get("timed_out"))
        exit_code = int(result.get("exit_code", -1))
        status = "timeout" if timed_out else ("passed" if exit_code == 0 else "failed")
        finished = change_store.finish_validation_run(
            run["id"],
            status=status,
            exit_code=exit_code,
            duration_ms=int(result.get("duration", 0)),
            timed_out=timed_out,
            stdout=str(result.get("output", ""))[:20000],
            stderr=str(result.get("stderr", ""))[:20000],
        )
        _emit(change_set, "validation_finished", {"validation": finished})
        return {"ok": status == "passed", "validation": finished}
    except Exception as exc:
        finished = change_store.finish_validation_run(
            run["id"],
            status="failed",
            exit_code=-1,
            duration_ms=0,
            timed_out=False,
            stderr=str(exc)[:20000],
        )
        _emit(change_set, "validation_finished", {"validation": finished})
        return {"ok": False, "error": str(exc), "validation": finished}


def _emit(change_set: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
    try:
        from src.agent.change_capture import emit_change_event
        run_id = change_set.get("run_id")
        if run_id:
            emit_change_event(
                run_id, event_type,
                {"change_set_id": change_set["id"], **payload},
            )
    except Exception:
        pass
