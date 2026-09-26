"""Git snapshot/undo behavior tests use only isolated temporary repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_repo(repo: Path) -> str:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "target.txt").write_text("base target\n", encoding="utf-8")
    (repo / "staged.txt").write_text("base staged\n", encoding="utf-8")
    (repo / "unstaged.txt").write_text("base unstaged\n", encoding="utf-8")
    _git(repo, "add", "--", "target.txt", "staged.txt", "unstaged.txt")
    _git(repo, "commit", "-qm", "baseline")
    return _git(repo, "rev-parse", "HEAD")


def _index_bytes(repo: Path) -> bytes:
    index_path = Path(_git(repo, "rev-parse", "--git-path", "index"))
    if not index_path.is_absolute():
        index_path = repo / index_path
    return index_path.read_bytes()


@pytest.fixture
def isolated_git_repo(tmp_path, monkeypatch):
    from src.agent.tools.groups.file_ops import _git_snapshot

    repo = tmp_path / "isolated-repo"
    head = _make_repo(repo)
    monkeypatch.setattr(_git_snapshot, "ROOT_DIR", repo)
    return repo, head


@pytest.mark.asyncio
async def test_snapshot_and_undo_preserve_user_index_head_and_other_worktree_changes(
    isolated_git_repo,
):
    from src.agent.tools.groups.file_ops import write_file, undo_edit

    repo, head = isolated_git_repo
    target = repo / "target.txt"
    staged = repo / "staged.txt"
    unstaged = repo / "unstaged.txt"

    staged.write_text("user staged change\n", encoding="utf-8")
    _git(repo, "add", "--", "staged.txt")
    target.write_text("pre-agent target with unstaged change\n", encoding="utf-8")
    _git(repo, "add", "--", "target.txt")
    target.write_text("actual pre-agent worktree content\n", encoding="utf-8")
    unstaged.write_text("user unstaged change\n", encoding="utf-8")
    index_before = _index_bytes(repo)

    result = await write_file._do_write(str(target), "agent result\n")
    assert result["success"] is True
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _index_bytes(repo) == index_before

    undone = undo_edit._undo_sync()

    assert isinstance(undone, tuple)
    assert target.read_text(encoding="utf-8") == "actual pre-agent worktree content\n"
    assert staged.read_text(encoding="utf-8") == "user staged change\n"
    assert unstaged.read_text(encoding="utf-8") == "user unstaged change\n"
    assert _index_bytes(repo) == index_before
    assert _git(repo, "rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_repeated_agent_edits_undo_one_at_a_time_without_moving_head(isolated_git_repo):
    from src.agent.tools.groups.file_ops import write_file, undo_edit

    repo, head = isolated_git_repo
    target = repo / "target.txt"
    target.write_text("user pre-agent state\n", encoding="utf-8")
    index_before = _index_bytes(repo)

    await write_file._do_write(str(target), "agent edit one\n")
    await write_file._do_write(str(target), "agent edit two\n")

    assert isinstance(undo_edit._undo_sync(), tuple)
    assert target.read_text(encoding="utf-8") == "agent edit one\n"
    assert isinstance(undo_edit._undo_sync(), tuple)
    assert target.read_text(encoding="utf-8") == "user pre-agent state\n"
    assert _index_bytes(repo) == index_before
    assert _git(repo, "rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_undo_refuses_to_overwrite_user_edit_made_after_agent_snapshot(isolated_git_repo):
    from src.agent.tools.groups.file_ops import write_file, undo_edit

    repo, head = isolated_git_repo
    target = repo / "target.txt"
    await write_file._do_write(str(target), "agent result\n")
    index_before = _index_bytes(repo)
    target.write_text("later user edit\n", encoding="utf-8")

    result = undo_edit._undo_sync()

    assert isinstance(result, str)
    assert "changed since" in result
    assert target.read_text(encoding="utf-8") == "later user edit\n"
    assert _index_bytes(repo) == index_before
    assert _git(repo, "rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_undo_removes_file_created_by_agent_without_moving_head(isolated_git_repo):
    from src.agent.tools.groups.file_ops import write_file, undo_edit

    repo, head = isolated_git_repo
    new_file = repo / "new-file.txt"
    index_before = _index_bytes(repo)
    await write_file._do_write(str(new_file), "agent-created\n")

    result = undo_edit._undo_sync()

    assert isinstance(result, tuple)
    assert not new_file.exists()
    assert _index_bytes(repo) == index_before
    assert _git(repo, "rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_run_command_snapshots_only_explicit_target_before_execution(
    isolated_git_repo, monkeypatch
):
    from src.agent.exceptions import ToolContext
    from src.agent.tools.groups.execution import run_command

    repo, head = isolated_git_repo
    target = repo / "target.txt"
    target.write_text("run command preimage\n", encoding="utf-8")
    index_before = _index_bytes(repo)

    async def fake_run(command, timeout_ms, cwd):
        target.write_text("run command postimage\n", encoding="utf-8")
        return {
            "output": "written",
            "stderr": "",
            "exit_code": 0,
            "duration": 1,
            "timed_out": False,
        }

    monkeypatch.setattr(run_command, "validate_path", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(run_command, "_do_run", fake_run)
    result = await run_command.RunCommandTool().execute(
        {
            "command": "Set-Content -Path target.txt -Value updated",
            "cwd": str(repo),
        },
        ToolContext(),
    )

    assert result["exit_code"] == 0
    assert target.read_text(encoding="utf-8") == "run command postimage\n"
    assert _index_bytes(repo) == index_before
    assert _git(repo, "rev-parse", "HEAD") == head

    from src.agent.tools.groups.file_ops import undo_edit

    assert isinstance(undo_edit._undo_sync(), tuple)
    assert target.read_text(encoding="utf-8") == "run command preimage\n"


@pytest.mark.asyncio
async def test_multi_file_snapshot_restores_all_targets_and_no_other_git_state(
    isolated_git_repo,
):
    from src.agent.tools.groups.file_ops import _git_snapshot, undo_edit

    repo, head = isolated_git_repo
    first = repo / "target.txt"
    second = repo / "second.txt"
    first.write_text("first preimage\n", encoding="utf-8")
    second.write_text("second preimage\n", encoding="utf-8")
    index_before = _index_bytes(repo)

    async with _git_snapshot.git_snapshot_context([str(first), str(second)], "test_multi"):
        first.write_text("first after\n", encoding="utf-8")
        second.write_text("second after\n", encoding="utf-8")

    result = undo_edit._undo_sync()

    assert isinstance(result, tuple)
    assert set(result[0]) == {"target.txt", "second.txt"}
    assert first.read_text(encoding="utf-8") == "first preimage\n"
    assert second.read_text(encoding="utf-8") == "second preimage\n"
    assert _index_bytes(repo) == index_before
    assert _git(repo, "rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_snapshot_finalizes_partial_write_when_operation_raises(isolated_git_repo):
    from src.agent.tools.groups.file_ops import _git_snapshot, undo_edit

    repo, head = isolated_git_repo
    target = repo / "target.txt"
    target.write_text("exception preimage\n", encoding="utf-8")
    index_before = _index_bytes(repo)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        async with _git_snapshot.git_snapshot_context([str(target)], "test_exception"):
            target.write_text("partial result\n", encoding="utf-8")
            raise RuntimeError("simulated interruption")

    assert isinstance(undo_edit._undo_sync(), tuple)
    assert target.read_text(encoding="utf-8") == "exception preimage\n"
    assert _index_bytes(repo) == index_before
    assert _git(repo, "rev-parse", "HEAD") == head
