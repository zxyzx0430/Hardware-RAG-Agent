"""Negative security-boundary tests for the W2 hardening work.

Each class covers one batch:
  * path guard allowlist / secret deny / reparse escape
Later batches append command, build, auth, MCP and webfetch cases.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.agent import path_guard
from src.agent.path_guard import (
    is_path_allowed_for_tree,
    resolve_guard_path,
    validate_path,
)

SECRET_NAMES = [
    ".env",
    ".env.local",
    ".env.production",
    ".enc_key",
    ".enc_key.bak",
    "keys_store.json",
    "keys_store.json.tmp",
]


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_secret_files_are_denied(name: str) -> None:
    ok, reason = validate_path(name, is_write=False)
    assert ok is False
    assert "deny pattern" in reason


def test_path_outside_allowlist_is_denied(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setattr(path_guard, "ALLOWED_DIRS", (allowed,))

    assert validate_path(str(outside / "note.txt"), is_write=False)[0] is False
    assert validate_path(str(allowed / "note.txt"), is_write=False)[0] is True


def test_traversal_is_denied() -> None:
    ok, reason = validate_path("..\\..\\Windows\\win.ini", is_write=False)
    assert ok is False
    assert "traversal" in reason


def test_windows_namespace_prefix_is_denied() -> None:
    assert resolve_guard_path("\\\\?\\C:\\Windows\\win.ini") == ""


def test_ads_is_denied() -> None:
    assert resolve_guard_path(str(Path("note.txt")) + ":secret") == ""


def test_symlink_escape_is_denied(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("SECRET", encoding="utf-8")
    link = allowed / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is not permitted on this host")
    monkeypatch.setattr(path_guard, "ALLOWED_DIRS", (allowed,))

    ok, reason = validate_path(str(link), is_write=False)
    assert ok is False
    assert "could not be resolved" in reason
    assert is_path_allowed_for_tree(str(link)) is False


def test_grep_walk_skips_secret_files(monkeypatch, tmp_path: Path) -> None:
    from src.agent.tools.groups.file_ops.grep import _GrepCfg, _grep_sync

    import re

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "notes.txt").write_text("SAFE_MARKER", encoding="utf-8")
    (allowed / ".env.local").write_text("SECRET_MARKER", encoding="utf-8")
    (allowed / "keys_store.json").write_text("SECRET_MARKER", encoding="utf-8")
    monkeypatch.setattr(path_guard, "ALLOWED_DIRS", (allowed,))

    matches, _ = _grep_sync(str(allowed), _GrepCfg(re.compile("MARKER"), "*", 100))
    contents = [item["content"] for item in matches]
    assert contents == ["SAFE_MARKER"]
