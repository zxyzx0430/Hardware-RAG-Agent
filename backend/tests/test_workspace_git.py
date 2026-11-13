import subprocess
from pathlib import Path

from src.agent import workspace_git


def _git(path: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "base.txt")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def test_git_context_reports_branch_and_worktree_state(tmp_path):
    repo = _repo(tmp_path)
    context = workspace_git.git_context(str(repo))
    assert context["ok"] is True
    assert context["is_git"] is True
    assert context["branch"] in {"master", "main"}
    assert context["dirty"] == []
    assert context["untracked"] == []

    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")

    after = workspace_git.git_context(str(repo))
    assert after["dirty"] == ["base.txt"]
    assert after["untracked"] == ["new.txt"]


def test_git_context_outside_repository(tmp_path):
    context = workspace_git.git_context(str(tmp_path))
    assert context["ok"] is True
    assert context["is_git"] is False
    assert context["branch"] is None
    assert context["dirty"] == []
