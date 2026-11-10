"""Observe file changes around mutating tool calls.

This module deliberately records after-the-fact facts. It does not stage or
block writes; staging is a later P2 extension. Capture failures must never
change the tool result.
"""
from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable

from app.db import change_store, database
from src.agent.core.toolkit.tool_result_envelope import ToolResultEnvelope
from src.agent.exceptions import ToolContext
from src.agent.path_guard import PROJECT_ROOT, active_workspace_root, resolve_agent_path, validate_path

logger = logging.getLogger(__name__)

FILE_WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "multi_edit", "apply_patch", "undo_edit",
})
UNOBSERVED_TOOLS = frozenset({"undo_edit"})
MAX_CAPTURE_BYTES = 1024 * 1024
_GIT_TIMEOUT_SECONDS = 5


@dataclasses.dataclass
class _FileState:
    path: str
    sha256: str | None
    blob_ref: str | None
    size: int
    text: str | None
    binary: bool


@dataclasses.dataclass
class _Pending:
    set_id: str | None
    call_id: str
    session_id: str
    run_id: str
    tool_name: str
    risk: str
    targets: tuple[str, ...]
    before: dict[str, _FileState]
    plan_step_id: str | None = None
    plan_step_index: int | None = None
    unobserved_reason: str | None = None
    skip_reason: str | None = None


def _blob_dir() -> Path:
    path = Path(database.DB_PATH).parent / "change_sets" / "blobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_blob(data: bytes, digest: str) -> str:
    target = _blob_dir() / digest
    if target.exists():
        return digest
    temp = target.with_suffix(".tmp")
    temp.write_bytes(data)
    os.replace(temp, target)
    return digest


def read_blob(ref: str | None) -> str | None:
    """Read a captured text blob; binary/large items have no blob ref."""
    if not ref:
        return None
    try:
        data = (_blob_dir() / ref).read_bytes()
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def file_digest(path: str) -> str | None:
    """Return the current SHA-256 for a file, or None when unavailable."""
    try:
        digest, _ = _hash_file(Path(path))
        return digest
    except (FileNotFoundError, OSError):
        return None


def _command_looks_like_write(command: str) -> bool:
    try:
        from src.agent.tools.groups.execution.run_command import _FILE_WRITE_RE
        return bool(_FILE_WRITE_RE.search(command))
    except Exception:
        return any(marker in command for marker in (">", "tee ", "Set-Content", "Remove-Item"))


def _file_state(path: str) -> _FileState:
    target = Path(path)
    try:
        stat = target.stat()
    except FileNotFoundError:
        return _FileState(path, None, None, 0, None, False)
    except OSError:
        return _FileState(path, None, None, 0, None, False)
    if stat.st_size > MAX_CAPTURE_BYTES:
        digest, size = _hash_file(target)
        return _FileState(path, digest, None, size, None, False)
    try:
        data = target.read_bytes()
    except OSError:
        return _FileState(path, None, None, 0, None, False)
    digest = hashlib.sha256(data).hexdigest()
    text: str | None = None
    binary = False
    if b"\x00" not in data:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            binary = True
    else:
        binary = True
    blob_ref = None if binary else _write_blob(data, digest)
    return _FileState(path, digest, blob_ref, len(data), text, binary)


def workspace_context(root: Path | None = None) -> dict[str, Any]:
    """Read-only workspace baseline; never changes Git state."""
    base = Path(root) if root else active_workspace_root()
    result: dict[str, Any] = {
        "root": str(base),
        "is_git": False,
        "branch": None,
        "head": None,
        "dirty": [],
        "untracked": [],
    }
    try:
        top = _git(base, "rev-parse", "--show-toplevel")
        if not top:
            return result
        result["is_git"] = True
        result["branch"] = _git(base, "rev-parse", "--abbrev-ref", "HEAD")
        result["head"] = _git(base, "rev-parse", "HEAD")
        status = _git(base, "status", "--porcelain")
        for line in (status or "").splitlines():
            if line.startswith("?? "):
                result["untracked"].append(line[3:].lstrip())
            elif line.strip():
                result["dirty"].append(line[3:].lstrip())
    except Exception:
        logger.debug("workspace context unavailable", exc_info=True)
    return result


def _git(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _path_from_args(args: dict[str, Any]) -> str | None:
    raw = args.get("path") or args.get("file_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    ok, _ = validate_path(raw, is_write=True)
    if not ok:
        return None
    return resolve_agent_path(raw)


def _command_targets(args: dict[str, Any]) -> list[str]:
    command = args.get("command") or args.get("cmd")
    if not isinstance(command, str) or not command.strip():
        return []
    if any(char in command for char in ";|&`$\r\n{}()"):
        return []
    try:
        from src.agent.tools.groups.execution.run_command import _extract_write_targets
        targets = _extract_write_targets(command, str(args.get("cwd") or active_workspace_root()))
    except Exception:
        return []
    return targets if len(targets) == 1 else []


def _targets_for(spec_name: str, args: dict[str, Any]) -> tuple[list[str], str | None]:
    if spec_name in UNOBSERVED_TOOLS:
        return [], "tool has no explicit file target"
    if spec_name in FILE_WRITE_TOOLS:
        path = _path_from_args(args)
        return ([path], None) if path else ([], "file target could not be resolved")
    if spec_name == "run_command":
        command = str(args.get("command") or args.get("cmd") or "")
        targets = _command_targets(args)
        if targets:
            return targets, None
        if _command_looks_like_write(command):
            return [], "compound or unparsed command target"
        return [], None
    return [], "tool is not a file mutation"


def _change_set(
    *, session_id: str, run_id: str, workspace: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        existing = change_store.get_change_set_by_run(run_id)
        plan = change_store.resolve_run_plan(run_id)
        row = change_store.get_or_create_change_set(
            session_id=session_id,
            run_id=run_id,
            workspace_root=workspace["root"],
            base_ref=workspace.get("branch"),
            base_sha=workspace.get("head"),
            plan_id=plan[0] if plan else None,
            plan_version=plan[1] if plan else None,
        )
        if existing is None:
            _emit(run_id, "change_set_updated", {
                "change_set_id": row["id"],
                "session_id": session_id,
                "workspace_root": row["workspace_root"],
            })
        return row
    except Exception:
        logger.warning("change set creation failed run=%s", run_id, exc_info=True)
        return None


def begin_capture(
    call_id: str,
    spec: Any,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    target_paths: Iterable[str] | None = None,
) -> _Pending | None:
    run_id = getattr(ctx, "run_id", None)
    if not run_id:
        return None
    tool_name = str(getattr(spec, "name", "") or "")
    if tool_name not in FILE_WRITE_TOOLS and tool_name != "run_command":
        return None
    if target_paths is not None:
        targets = list(target_paths)
        reason = None if targets else "no file target"
    else:
        targets, reason = _targets_for(tool_name, args)
        if not targets and reason is None:
            return None
    before = {path: _file_state(path) for path in targets}
    workspace = workspace_context()
    row = _change_set(
        session_id=str(getattr(ctx, "session_id", "default")),
        run_id=str(run_id), workspace=workspace,
    )
    summary = row.get("summary", {}) if row else {}
    step_index = summary.get("current_plan_step_index") if isinstance(summary, dict) else None
    if not isinstance(step_index, int) or step_index < 0:
        step_index = None
    plan_id = row.get("plan_id") if row else None
    plan_version = row.get("plan_version") if row else None
    plan_step_id = (
        f"{plan_id}:v{plan_version}:{step_index}"
        if plan_id and plan_version is not None and step_index is not None
        else None
    )
    return _Pending(
        set_id=row["id"] if row else None,
        call_id=call_id,
        session_id=str(getattr(ctx, "session_id", "default")),
        run_id=str(run_id),
        tool_name=tool_name,
        risk=str(getattr(getattr(spec, "risk_level", None), "value", "unknown")),
        targets=tuple(targets),
        before=before,
        plan_step_id=plan_step_id,
        plan_step_index=step_index,
        unobserved_reason=reason,
    )


def set_plan_step_cursor(
    *, session_id: str, run_id: str, step_index: int,
) -> bool:
    """Record the current plan step for subsequent file changes in this run."""
    if not isinstance(step_index, int) or step_index < 0:
        return False
    if change_store.resolve_run_plan(run_id) is None:
        return False
    step_count = change_store.resolve_run_plan_step_count(run_id)
    if step_count is None or step_index >= step_count:
        return False
    row = _change_set(
        session_id=session_id,
        run_id=run_id,
        workspace=workspace_context(),
    )
    if row is None:
        return False
    change_store.set_plan_step_cursor(row["id"], step_index)
    try:
        _emit(run_id, "change_set_updated", {
            "change_set_id": row["id"],
            "session_id": session_id,
            "workspace_root": row["workspace_root"],
        })
    except Exception:
        logger.debug("change plan cursor event failed", exc_info=True)
    return True


def _diff_stats(before: _FileState, after: _FileState) -> dict[str, int]:
    if before.text is None or after.text is None:
        return {"additions": 0, "deletions": 0, "changed": before.sha256 != after.sha256}
    additions = deletions = 0
    diff = difflib.unified_diff(
        before.text.splitlines(), after.text.splitlines(), lineterm="",
    )
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"additions": additions, "deletions": deletions, "changed": before.sha256 != after.sha256}


def _change_type(before: _FileState, after: _FileState) -> str:
    if before.sha256 is None:
        return "created"
    if after.sha256 is None:
        return "deleted"
    return "modified" if before.sha256 != after.sha256 else "unchanged"


def _add_item(pending: _Pending, path: str, before: _FileState, after: _FileState) -> dict[str, Any] | None:
    if not pending.set_id:
        return None
    try:
        item = change_store.add_change_item(
            change_set_id=pending.set_id,
            call_id=pending.call_id,
            tool_name=pending.tool_name,
            operation=pending.tool_name.removesuffix("_tool"),
            path=path,
            before_sha256=before.sha256,
            after_sha256=after.sha256,
            before_blob_ref=before.blob_ref,
            after_blob_ref=after.blob_ref,
            diff_stats_json=json.dumps(_diff_stats(before, after), sort_keys=True),
            risk=pending.risk,
            status="observed",
            plan_step_id=pending.plan_step_id,
            plan_step_index=pending.plan_step_index,
        )
    except Exception:
        logger.warning("change item persistence failed run=%s", pending.run_id, exc_info=True)
        return None
    try:
        change_store.record_event(
            change_set_id=pending.set_id, change_item_id=item["id"],
            event_type="created", payload={"item": item},
        )
    except Exception:
        logger.debug("change event persistence failed", exc_info=True)
    _emit(pending.run_id, "change_item_updated", {"change_set_id": pending.set_id, "item": item})
    return item


def _add_unobserved(pending: _Pending, envelope: ToolResultEnvelope) -> dict[str, Any] | None:
    if not pending.set_id:
        return None
    try:
        item = change_store.add_change_item(
            change_set_id=pending.set_id,
            call_id=pending.call_id,
            tool_name=pending.tool_name,
            operation=pending.tool_name.removesuffix("_tool"),
            path="",
            diff_stats_json="{}",
            risk=pending.risk,
            status="unobserved",
            plan_step_id=pending.plan_step_id,
            plan_step_index=pending.plan_step_index,
        )
    except Exception:
        logger.warning("unobserved change persistence failed run=%s", pending.run_id, exc_info=True)
        return None
    try:
        change_store.record_event(
            change_set_id=pending.set_id, change_item_id=item["id"],
            event_type="created",
            payload={"item": item, "reason": pending.unobserved_reason or "unobserved"},
        )
    except Exception:
        logger.debug("change event persistence failed", exc_info=True)
    _emit(pending.run_id, "change_item_updated", {"change_set_id": pending.set_id, "item": item})
    return item


def finish_capture(pending: _Pending | None, envelope: ToolResultEnvelope) -> None:
    if pending is None:
        return
    if not pending.targets:
        _add_unobserved(pending, envelope)
        return
    first: dict[str, Any] | None = None
    for path in pending.targets:
        before = pending.before.get(path, _FileState(path, None, None, 0, None, False))
        after = _file_state(path)
        item = _add_item(pending, path, before, after)
        first = first or item
    if first is not None:
        try:
            envelope.metadata.change_item_id = first["id"]
        except Exception:
            logger.debug("change item metadata attach failed", exc_info=True)


def emit_change_event(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Public event hook used by review/revert/validation routes."""
    _emit(run_id, event_type, payload)


def _emit(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
    try:
        from src.agent import run_events
        store = run_events.get_run_store()
        writer = run_events.RunEventWriter(
            store, run_id, bus=run_events.get_run_bus(),
        )
        writer.append(event_type, {"type": event_type, "run_id": run_id, **payload})
    except Exception:
        logger.debug("change run event append failed run=%s", run_id, exc_info=True)
