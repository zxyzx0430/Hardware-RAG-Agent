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


def test_git_context_and_selected_commit(tmp_path):
    repo = _repo(tmp_path)
    context = workspace_git.git_context(str(repo))
    assert context["is_git"] is True
    assert context["branch"] in {"master", "main"}

    (repo / "agent.txt").write_text("agent\n", encoding="utf-8")
    (repo / "user.txt").write_text("user\n", encoding="utf-8")
    result = workspace_git.commit_paths(
        str(repo), [str(repo / "agent.txt")], "agent change",
    )

    assert result["ok"] is True
    after = workspace_git.git_context(str(repo))
    assert "user.txt" in after["untracked"]
    assert "agent.txt" not in after["dirty"]
    assert "agent.txt" not in after["untracked"]


def test_commit_does_not_include_unselected_staged_user_file(tmp_path):
    repo = _repo(tmp_path)
    (repo / "user.txt").write_text("user\n", encoding="utf-8")
    _git(repo, "add", "user.txt")
    (repo / "agent.txt").write_text("agent\n", encoding="utf-8")

    result = workspace_git.commit_paths(
        str(repo), [str(repo / "agent.txt")], "agent only",
    )
    assert result["ok"] is True

    committed = subprocess.run(
        ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    assert committed.stdout.strip() == "agent.txt"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
        check=False,
    )
    assert "A  user.txt" in status.stdout


def test_branch_creation_and_no_github_remote(tmp_path):
    repo = _repo(tmp_path)
    branch = workspace_git.create_branch(str(repo), "feature/acceptance")
    assert branch["ok"] is True
    assert branch["context"]["branch"] == "feature/acceptance"

    status = workspace_git.pr_status(str(repo))
    assert status["ok"] is False
    assert status["code"] == "NO_GITHUB_REMOTE"


def test_commit_rejects_path_outside_repo(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")

    result = workspace_git.commit_paths(str(repo), [str(outside)], "bad")

    assert result["ok"] is False
    assert result["code"] == "PATH_OUTSIDE_WORKSPACE"
