"""
Hardware RAG Agent — ApplyPatchTool (file_ops group).

Apply a unified diff patch to a single file. Context lines are validated
against the file's current content; on any mismatch the file is left
untouched. Uses only the Python standard library (re).
Spec: add-grep-glob-todo-tools §Requirement: apply_patch.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import validate_path
from src.agent.tools.groups.file_ops._git_snapshot import _git_snapshot


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ═══════════════════════════════════════════
# ApplyPatchTool
# ═══════════════════════════════════════════

class ApplyPatchArgs(BaseModel):
    file_path: str = Field(description="要应用 patch 的文件路径")
    patch: str = Field(description="unified diff 格式的 patch 文本（含 @@ hunk 头）")


class ApplyPatchOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    file_path: str = Field("", description="编辑的文件路径")
    success: bool = Field(False, description="是否成功")
    changes: int = Field(0, description="变更行数（含新增+删除）")


class ApplyPatchTool(ToolSpec):
    """Apply a unified diff patch to a file, with context validation."""
    name: str = "apply_patch"
    description: str = (
        "应用 unified diff patch 到本地文件（context 行不匹配则不写盘）。"
        "适用于大段重构的紧凑表达。敏感文件如 .env/*.key 会被拦截。"
    )
    args_schema: type = ApplyPatchArgs
    output_schema: type[BaseModel] | None = ApplyPatchOutput

    risk_level: RiskLevel = RiskLevel.MEDIUM
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Validate path, then apply patch atomically."""
        path = args.get("file_path", "")
        ok, reason = validate_path(path, is_write=True)
        if not ok:
            return _fail(path, f"Error: {reason}")
        patch = args.get("patch", "")
        return await _do_apply(path, patch)


async def _do_apply(path: str, patch: str) -> dict:
    """Run patch in a worker thread; translate str error to failure."""
    result = await asyncio.to_thread(_apply_patch_sync, path, patch)
    if isinstance(result, str):
        return _fail(path, result)
    await asyncio.to_thread(_git_snapshot, path, "apply_patch")
    return _ok(path, result)


def _apply_patch_sync(path: str, patch: str) -> str | int:
    """Read file, parse patch, apply hunks in memory, write back if ok."""
    content, err = _read_content(path)
    if err:
        return err
    hunks, err = _parse_patch(patch)
    if err:
        return err
    new_content, changes, err = _apply_hunks(content, hunks)
    if err:
        return err
    return _write_back(path, new_content, changes)


def _read_content(path: str) -> tuple[str, str | None]:
    """Read file content. Returns (content, error)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), None
    except FileNotFoundError:
        return "", f"file not found: {path}"
    except OSError as exc:
        return "", str(exc)


# ── Patch parsing ──────────────────────────

def _parse_patch(patch: str) -> tuple[list[dict], str | None]:
    """Parse patch text into a list of hunk dicts."""
    lines = patch.splitlines()
    if not lines:
        return [], "invalid patch format: empty patch"
    return _collect_hunks(lines)


def _collect_hunks(lines: list[str]) -> tuple[list[dict], str | None]:
    """Skip header lines (--- / +++ / etc.), collect @@ hunks."""
    hunks: list[dict] = []
    idx = _skip_header(lines, 0)
    while idx < len(lines):
        if lines[idx].startswith("@@"):
            hunk, idx, err = _parse_hunk(lines, idx)
            if err:
                return [], err
            hunks.append(hunk)
        else:
            idx += 1
    return hunks, None


def _skip_header(lines: list[str], idx: int) -> int:
    """Skip lines before the first @@ hunk header."""
    while idx < len(lines) and not lines[idx].startswith("@@"):
        idx += 1
    return idx


def _parse_hunk(lines: list[str], start: int) -> tuple[dict, int, str | None]:
    """Parse one hunk header + body. Returns (hunk, next_idx, error)."""
    m = _HUNK_HEADER_RE.match(lines[start])
    if not m:
        return {}, start + 1, f"invalid patch format: bad hunk header at line {start + 1}"
    old_start = int(m.group(1))
    body, next_idx = _collect_hunk_body(lines, start + 1)
    return {"old_start": old_start, "body": body}, next_idx, None


def _collect_hunk_body(lines: list[str], idx: int) -> tuple[list[str], int]:
    """Collect body lines until next @@ or end of patch."""
    body: list[str] = []
    while idx < len(lines) and not lines[idx].startswith("@@"):
        body.append(lines[idx])
        idx += 1
    return body, idx


# ── Patch application ──────────────────────

def _apply_hunks(content: str, hunks: list[dict]) -> tuple[str, int, str | None]:
    """Apply all hunks in memory. Returns (new_content, changes, error)."""
    lines = content.splitlines(keepends=True)
    changes = 0
    for hunk in hunks:
        new_lines, n, err = _apply_one_hunk(lines, hunk)
        if err:
            return content, changes, err
        lines = new_lines
        changes += n
    return "".join(lines), changes, None


def _apply_one_hunk(lines: list[str], hunk: dict) -> tuple[list[str], int, str | None]:
    """Apply a single hunk. Returns (new_lines, changes, error)."""
    body = hunk["body"]
    pre_idx = hunk["old_start"] - 1
    consumed, produced, err = _walk_body(lines, pre_idx, body)
    if err:
        return lines, 0, err
    new_lines = lines[:pre_idx] + produced + lines[pre_idx + consumed:]
    return new_lines, _count_changes(body), None


def _walk_body(lines: list[str], start: int, body: list[str]) -> tuple[int, list[str], str | None]:
    """Walk body lines, producing the replacement region. Returns (consumed, produced, error)."""
    state = _new_walk_state()
    for body_line in body:
        if not _step_body(lines, start, body_line, state):
            return state["consumed"], state["produced"], state["err"]
    return state["consumed"], state["produced"], None


def _new_walk_state() -> dict[str, Any]:
    return {"consumed": 0, "produced": [], "err": None}


def _step_body(lines: list[str], start: int, body_line: str, state: dict[str, Any]) -> bool:
    """Process one body line. Returns False if an error was recorded."""
    tag = body_line[:1]
    text = body_line[1:]
    if tag == " ":
        return _step_context(lines, start, text, state)
    if tag == "-":
        return _step_remove(lines, start, text, state)
    if tag == "+":
        state["produced"].append(text + "\n")
        return True
    return _step_blank(body_line, state)


def _step_context(lines: list[str], start: int, text: str, state: dict[str, Any]) -> bool:
    """Handle a context line. Validates match against the file."""
    idx = start + state["consumed"]
    err = _check_context(lines, idx, text)
    if err:
        state["err"] = err
        return False
    state["produced"].append(lines[idx])
    state["consumed"] += 1
    return True


def _step_remove(lines: list[str], start: int, text: str, state: dict[str, Any]) -> bool:
    """Handle a '-' line. Validates match, consumes without producing."""
    idx = start + state["consumed"]
    err = _check_context(lines, idx, text)
    if err:
        state["err"] = err
        return False
    state["consumed"] += 1
    return True


def _step_blank(body_line: str, state: dict[str, Any]) -> bool:
    """Handle a blank body line as a literal empty line in output."""
    if body_line == "":
        state["produced"].append("\n")
    return True


def _check_context(lines: list[str], idx: int, expected: str) -> str | None:
    """Compare expected context line to file line at idx."""
    if idx >= len(lines):
        return f"patch context mismatch at line {idx + 1}: end of file"
    actual = lines[idx].rstrip("\r\n")
    if actual != expected:
        return f"patch context mismatch at line {idx + 1}"
    return None


def _count_changes(body: list[str]) -> int:
    """Count +/- lines in a hunk body."""
    return sum(1 for line in body if line[:1] in ("+", "-"))


def _write_back(path: str, content: str, changes: int) -> str | int:
    """Write content back to disk. Returns changes count or error string."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return changes
    except OSError as exc:
        return str(exc)


def _fail(path: str, msg: str) -> dict:
    return {"output": msg, "file_path": path, "success": False, "changes": 0}


def _ok(path: str, changes: int) -> dict:
    return {
        "output": f"已应用 patch，变更 {changes} 行",
        "file_path": path,
        "success": True,
        "changes": changes,
    }
