"""End-to-end test: write_file / edit_file / run_command → undo_edit rollback.

Run from project root:  python scripts/test_git_snapshot_rollback.py

SAFETY: Before testing, all uncommitted work is committed to a checkpoint
commit. After testing, HEAD is restored to the checkpoint and the checkpoint
is soft-reset back so the user's changes return as unstaged. This prevents
undo_edit's `git reset --hard` from nuking uncommitted work.

Verifies that:
  1. write_file creates a git snapshot → undo_edit rolls it back
  2. edit_file creates a git snapshot → undo_edit rolls it back
  3. run_command `echo > file` creates a git snapshot → undo_edit rolls it back
  4. path_guard allows non-blacklisted paths, blocks .git
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure backend/ is on sys.path so `from src.agent...` imports work.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Project root (one level above backend/). _git_snapshot commits here.
PROJECT_ROOT = BACKEND_DIR.parent
os.chdir(str(PROJECT_ROOT))

from src.agent.agent_factory import ensure_default_tools_registered  # noqa: E402
from src.agent.core.toolkit.tool_router import ToolRouter  # noqa: E402
from src.agent.exceptions import ToolContext  # noqa: E402


TEST_DIR = PROJECT_ROOT / "scripts" / "_git_snapshot_test"
COLOR = {
    "GREEN": "\033[92m",
    "RED": "\033[91m",
    "YELLOW": "\033[93m",
    "CYAN": "\033[96m",
    "RESET": "\033[0m",
}


def _c(name: str, msg: str) -> str:
    return f"{COLOR[name]}{msg}{COLOR['RESET']}"


def _git(*args: str) -> str:
    """Run git in PROJECT_ROOT, return stdout (stripped)."""
    r = subprocess.run(
        ["git", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return f"[git err] {r.stderr.strip()}"
    return r.stdout.strip()


def _git_head() -> str:
    return _git("rev-parse", "HEAD")


def _git_log_agent(n: int = 3) -> str:
    """Show last n commits authored by the agent."""
    out = _git(
        "log", f"-{n}", "--format=%h %an <%ae> %s",
        "--author", "hardware-rag-agent",
    )
    return out or "(no agent commits found)"


def _git_log_recent(n: int = 3) -> str:
    return _git("log", f"-{n}", "--format=%h %an %s")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _unpack(env: dict, label: str = "") -> tuple[bool, str, dict]:
    """Unify envelope shape. Returns (success, output_text, data)."""
    print(f"  [{label}] envelope keys = {list(env.keys()) if isinstance(env, dict) else type(env)}")
    env_success = bool(env.get("success", False)) if isinstance(env, dict) else False
    data = env.get("data", {}) if isinstance(env, dict) else {}
    if not isinstance(data, dict):
        data = {}
    # output is at top level (envelope.output), not data.output
    output = env.get("output", "") if isinstance(env, dict) else ""
    if not output:
        output = data.get("output", "")
    if not output:
        err = env.get("error") if isinstance(env, dict) else None
        if isinstance(err, dict):
            output = err.get("message", "")
    # tool-level success lives in data.success for file tools; fall back to env success
    tool_success = data.get("success", env_success)
    return tool_success, output, data


async def _dispatch(tool: str, args: dict) -> dict:
    """Call a tool via ToolRouter with auto-allow decision."""
    ensure_default_tools_registered()
    ctx = ToolContext()
    call_id = f"test-{tool}-{os.urandom(4).hex()}"
    envelope = await ToolRouter.get_default().dispatch(
        call_id, tool, args, ctx,
        decision="allow", decision_source="auto_allow",
    )
    return envelope


def _setup_test_dir() -> None:
    """Fresh test dir so previous-run leftovers don't interfere."""
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    TEST_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════
# Test 1: write_file → undo_edit
# ═══════════════════════════════════════════

async def test_write_file_rollback() -> bool:
    print(_c("CYAN", "\n=== Test 1: write_file → undo_edit ==="))
    target = TEST_DIR / "wf_test.txt"
    head_before = _git_head()
    print(f"  HEAD before: {head_before[:8]}")

    env = await _dispatch("write_file", {
        "path": str(target),
        "content": "version-1 from write_file\n",
    })
    success, out, _ = _unpack(env, "write_file")
    print(f"  write_file success={success}  out={out[:80]!r}")
    if not success:
        print(_c("RED", "  FAIL: write_file did not succeed"))
        return False

    if not target.exists() or _read(target) != "version-1 from write_file\n":
        print(_c("RED", f"  FAIL: file content mismatch: {_read(target)!r}"))
        return False
    print(_c("GREEN", "  ✓ file content matches"))

    head_after_write = _git_head()
    print(f"  HEAD after write: {head_after_write[:8]}")
    if head_after_write == head_before:
        print(_c("RED", "  FAIL: HEAD did not advance — no git snapshot was committed"))
        return False
    log = _git_log_agent(1)
    print(f"  agent commit: {log}")

    env = await _dispatch("undo_edit", {})
    success, out, _ = _unpack(env, "undo_edit")
    print(f"  undo_edit success={success}  out={out[:80]!r}")

    head_after_undo = _git_head()
    print(f"  HEAD after undo: {head_after_undo[:8]}")
    if head_after_undo != head_before:
        print(_c("RED", f"  FAIL: HEAD should be back to {head_before[:8]}, got {head_after_undo[:8]}"))
        return False
    if target.exists():
        print(_c("RED", f"  FAIL: file still exists after undo: {_read(target)!r}"))
        return False
    print(_c("GREEN", "  ✓ file removed, HEAD rolled back"))
    return True


# ═══════════════════════════════════════════
# Test 2: edit_file → undo_edit
# ═══════════════════════════════════════════

async def test_edit_file_rollback() -> bool:
    print(_c("CYAN", "\n=== Test 2: edit_file → undo_edit ==="))
    target = TEST_DIR / "ef_test.txt"
    _write(target, "original line\n")
    _git("add", "--", str(target))
    _git("commit", "-m", "test baseline for edit_file", "--author", "test <test@local>")
    head_before = _git_head()
    print(f"  HEAD before: {head_before[:8]}")
    print(f"  baseline content: {_read(target)!r}")

    env = await _dispatch("edit_file", {
        "path": str(target),
        "old_string": "original line",
        "new_string": "edited line by agent",
    })
    success, out, _ = _unpack(env, "edit_file")
    print(f"  edit_file success={success}  out={out[:80]!r}")
    if not success:
        print(_c("RED", "  FAIL: edit_file did not succeed"))
        return False

    content = _read(target)
    print(f"  edited content: {content!r}")
    if "edited line by agent" not in content:
        print(_c("RED", "  FAIL: edit did not apply"))
        return False
    print(_c("GREEN", "  ✓ edit applied"))

    head_after_edit = _git_head()
    print(f"  HEAD after edit: {head_after_edit[:8]}")
    if head_after_edit == head_before:
        print(_c("RED", "  FAIL: HEAD did not advance — no git snapshot"))
        return False
    log = _git_log_agent(1)
    print(f"  agent commit: {log}")

    env = await _dispatch("undo_edit", {})
    success, out, _ = _unpack(env, "undo_edit")
    print(f"  undo_edit success={success}  out={out[:80]!r}")

    head_after_undo = _git_head()
    print(f"  HEAD after undo: {head_after_undo[:8]}")
    if head_after_undo != head_before:
        print(_c("RED", f"  FAIL: HEAD should be back to {head_before[:8]}, got {head_after_undo[:8]}"))
        return False
    content = _read(target)
    print(f"  rolled-back content: {content!r}")
    if content != "original line\n":
        print(_c("RED", f"  FAIL: content not reverted to original: {content!r}"))
        return False
    print(_c("GREEN", "  ✓ content reverted, HEAD rolled back"))
    return True


# ═══════════════════════════════════════════
# Test 3: run_command `echo > file` → undo_edit
# ═══════════════════════════════════════════

async def test_run_command_rollback() -> bool:
    print(_c("CYAN", "\n=== Test 3: run_command echo > file → undo_edit ==="))
    target = TEST_DIR / "rc_test.txt"
    if target.exists():
        target.unlink()
    _git("add", "-A")
    _git("commit", "-m", "baseline before run_command test", "--allow-empty", "--author", "test <test@local>")
    head_before = _git_head()
    print(f"  HEAD before: {head_before[:8]}")

    # PowerShell echo writes UTF-16 BOM by default — use [IO.File]::WriteAllText for UTF-8.
    # But we want to test redirection detection, so use echo with Out-File -Encoding utf8.
    cmd = f'echo "hello from run_command" | Out-File -FilePath "{target}" -Encoding utf8'
    env = await _dispatch("run_command", {"command": cmd, "timeout_ms": 15000})
    success, out, data = _unpack(env, "run_command")
    exit_code = data.get("exit_code", "?") if isinstance(data, dict) else "?"
    print(f"  run_command success={success}  exit={exit_code}  out={out[:80]!r}")
    if not success or exit_code != 0:
        print(_c("RED", "  FAIL: run_command did not succeed"))
        return False

    if not target.exists():
        print(_c("RED", "  FAIL: file not created by run_command"))
        return False
    content = _read(target)
    print(f"  file content: {content!r}")
    if "hello from run_command" not in content:
        print(_c("RED", "  FAIL: content mismatch"))
        return False
    print(_c("GREEN", "  ✓ file created via redirection"))

    head_after = _git_head()
    print(f"  HEAD after run_command: {head_after[:8]}")
    if head_after == head_before:
        print(_c("RED", "  FAIL: HEAD did not advance — _maybe_git_snapshot did not fire"))
        return False
    log = _git_log_agent(1)
    print(f"  agent commit: {log}")
    if "run_command" not in log:
        print(_c("YELLOW", f"  WARN: latest agent commit not from run_command: {log}"))

    env = await _dispatch("undo_edit", {})
    success, out, _ = _unpack(env, "undo_edit")
    print(f"  undo_edit success={success}  out={out[:80]!r}")

    head_after_undo = _git_head()
    print(f"  HEAD after undo: {head_after_undo[:8]}")
    if head_after_undo != head_before:
        print(_c("RED", f"  FAIL: HEAD should be back to {head_before[:8]}, got {head_after_undo[:8]}"))
        return False
    if target.exists():
        print(_c("RED", f"  FAIL: file still exists after undo: {_read(target)!r}"))
        return False
    print(_c("GREEN", "  ✓ file removed, HEAD rolled back"))
    return True


# ═══════════════════════════════════════════
# Test 4: path_guard — write outside project (non-blacklisted)
# ═══════════════════════════════════════════

async def test_path_guard_open() -> bool:
    print(_c("CYAN", "\n=== Test 4: path_guard allows non-blacklisted outside-project path ==="))
    import tempfile
    outside = Path(tempfile.gettempdir()) / "agent_pg_test" / "outside.txt"
    if outside.exists():
        outside.unlink()
    blocked = PROJECT_ROOT / ".git" / "should_be_blocked.txt"

    # 4a: outside project, non-blacklisted → should succeed
    env = await _dispatch("write_file", {
        "path": str(outside),
        "content": "outside project\n",
    })
    success, out, _ = _unpack(env, "write_outside")
    print(f"  write outside (temp): success={success}  out={out[:80]!r}")
    if not success:
        print(_c("RED", "  FAIL: path_guard blocked a non-blacklisted outside-project path"))
        return False
    if not outside.exists() or _read(outside) != "outside project\n":
        print(_c("RED", "  FAIL: file not actually written"))
        return False
    print(_c("GREEN", "  ✓ outside-project non-blacklisted path allowed"))

    # 4b: .git path → must be blocked
    env = await _dispatch("write_file", {
        "path": str(blocked),
        "content": "should fail\n",
    })
    success, out, _ = _unpack(env, "write_git")
    print(f"  write .git: success={success}  out={out[:80]!r}")
    if success:
        print(_c("RED", "  FAIL: path_guard allowed a .git path!"))
        return False
    if blocked.exists():
        print(_c("RED", "  FAIL: .git file was actually written!"))
        return False
    print(_c("GREEN", "  ✓ .git blacklisted path still blocked"))

    # Cleanup
    try:
        outside.unlink()
        outside.parent.rmdir()
    except OSError:
        pass
    return True


# ═══════════════════════════════════════════
# Safety: checkpoint + restore
# ═══════════════════════════════════════════

def _checkpoint() -> str | None:
    """Commit all uncommitted work to a checkpoint commit so undo_edit's
    `git reset --hard` during tests can't destroy it. Returns checkpoint sha."""
    head_original = _git_head()
    _git("add", "-A")
    r = _git("commit", "-m", "TEST CHECKPOINT — do not push", "--author", "test <test@local>")
    if "nothing to commit" in r:
        print(_c("YELLOW", f"  no uncommitted work to checkpoint (clean tree)"))
        return head_original
    checkpoint = _git_head()
    print(_c("YELLOW", f"  checkpoint created: {checkpoint[:8]} (was {head_original[:8]})"))
    return checkpoint


def _restore(checkpoint: str, head_original: str) -> None:
    """Restore working tree to checkpoint state, then soft-reset to original HEAD.

    After restore: HEAD = head_original, all user changes are unstaged (same as
    before the test). Test commits and test files are discarded.
    """
    # Hard-reset to checkpoint (discards test commits AFTER checkpoint)
    _git("reset", "--hard", checkpoint)
    # Soft-reset to original HEAD (keeps user changes staged)
    _git("reset", "--soft", head_original)
    # Unstage user changes (use restore --staged, NOT `git reset HEAD` which
    # has surprising behavior in some shells)
    _git("restore", "--staged", ".")
    # Clean up test dir
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    # Drop the checkpoint commit if it exists (it's now redundant)
    # Only drop if checkpoint != head_original
    if checkpoint != head_original:
        # Check if checkpoint is still in history
        log = _git("log", "--format=%h", "-3")
        if checkpoint[:7] in log:
            # checkpoint is HEAD or ancestor; if HEAD==checkpoint, reset back
            current = _git_head()
            if current.startswith(checkpoint[:7]):
                _git("reset", "--hard", head_original)
    print(_c("YELLOW", f"  restored to {head_original[:8]}, user changes unstaged"))


# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

async def main() -> int:
    print(_c("CYAN", "=== Git Snapshot Rollback E2E Test ==="))
    print(f"PROJECT_ROOT = {PROJECT_ROOT}")
    head_original = _git_head()
    print(f"HEAD at start: {head_original[:8]}")
    print(f"recent log:\n  " + "\n  ".join(_git_log_recent(3).splitlines()))

    # SAFETY: checkpoint all uncommitted work before testing
    print(_c("CYAN", "\n--- Checkpointing uncommitted work ---"))
    checkpoint = _checkpoint()
    if checkpoint is None:
        print(_c("RED", "  FATAL: checkpoint failed, aborting test"))
        return 1

    _setup_test_dir()
    results: list[tuple[str, bool]] = []
    try:
        for name, fn in [
            ("write_file", test_write_file_rollback),
            ("edit_file", test_edit_file_rollback),
            ("run_command", test_run_command_rollback),
            ("path_guard", test_path_guard_open),
        ]:
            try:
                ok = await fn()
            except Exception:
                import traceback
                traceback.print_exc()
                ok = False
            results.append((name, ok))
    finally:
        # SAFETY: always restore, even on exception
        print(_c("CYAN", "\n--- Restoring working tree ---"))
        _restore(checkpoint, head_original)

    print(_c("CYAN", "\n=== Summary ==="))
    for name, ok in results:
        flag = _c("GREEN", "PASS") if ok else _c("RED", "FAIL")
        print(f"  {flag}  {name}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
