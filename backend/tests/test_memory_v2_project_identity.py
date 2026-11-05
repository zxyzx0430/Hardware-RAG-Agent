"""Project identity tests: git, worktrees, subdirectories and fallbacks."""

import os
import subprocess

import pytest

from app.db import project_identity
from app.db.project_identity import (
    current_project_id,
    is_path_like,
    normalize_path,
    normalize_remote,
    project_info,
)


def _git(*args, cwd):
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Memory Test",
            "GIT_AUTHOR_EMAIL": "memory-test@example.invalid",
            "GIT_COMMITTER_NAME": "Memory Test",
            "GIT_COMMITTER_EMAIL": "memory-test@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    return repo


def test_repo_subdirectory_and_worktree_share_one_id(git_repo, tmp_path):
    (git_repo / "README.md").write_text("synthetic\n", encoding="utf-8")
    _git("add", "README.md", cwd=git_repo)
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init", cwd=git_repo)

    subdir = git_repo / "backend" / "app"
    subdir.mkdir(parents=True)
    worktree = tmp_path / "worktree"
    _git("worktree", "add", "-q", str(worktree), "-b", "synthetic-worktree", cwd=git_repo)

    repo_id = current_project_id(str(git_repo))
    assert current_project_id(str(subdir)) == repo_id
    assert current_project_id(str(worktree)) == repo_id


def test_https_and_scp_remotes_are_equivalent(git_repo):
    assert normalize_remote("https://github.com/Example/Repo.git") == (
        "github.com/Example/Repo"
    )
    assert normalize_remote("git@github.com:Example/Repo.git") == (
        "github.com/Example/Repo"
    )
    assert normalize_remote("ssh://git@github.com/Example/Repo.git") == (
        "github.com/Example/Repo"
    )
    assert normalize_remote("https://github.com/Example/Repo/") == (
        "github.com/Example/Repo"
    )

    _git("remote", "add", "origin", "https://github.com/example/repo.git", cwd=git_repo)
    https_id = current_project_id(str(git_repo))
    _git("remote", "set-url", "origin", "git@github.com:example/repo.git", cwd=git_repo)
    assert current_project_id(str(git_repo)) == https_id


def test_non_git_path_uses_normalized_root(tmp_path):
    project = tmp_path / "plain-folder"
    project.mkdir()
    info = project_info(str(project))
    assert info["root"] == str(project)
    assert info["remote"] == ""
    assert len(info["project_id"]) == 64
    assert current_project_id(str(project)) == info["project_id"]


@pytest.mark.skipif(os.name != "nt", reason="Windows path case folding")
def test_windows_path_case_and_separators_normalize(tmp_path):
    project = tmp_path / "CaseFolder"
    project.mkdir()
    value = str(project)
    assert normalize_path(value) == normalize_path(value.upper())
    assert normalize_path(value) == normalize_path(value.replace("\\", "/"))


def test_git_failure_falls_back_without_raising(tmp_path, monkeypatch):
    project = tmp_path / "git-timeout"
    project.mkdir()

    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(project_identity.subprocess, "run", _timeout)
    first = current_project_id(str(project))
    second = current_project_id(str(project))
    assert len(first) == 64
    assert first == second
    info = project_info(str(project))
    assert info["remote"] == ""
    assert info["project_id"] == first


def test_is_path_like_recognizes_absolute_paths_only():
    assert is_path_like("E:/work/project") is True
    assert is_path_like("E:\\work\\project") is True
    assert is_path_like("/home/user/project") is True
    assert is_path_like("\\\\server\\share") is True
    assert is_path_like("proj-a") is False
    assert is_path_like("") is False
    assert is_path_like(None) is False
