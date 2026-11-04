"""Restricted argv policy for commands that may auto-allow.

The legacy risk classifier treats any command starting with ``ls``/``grep``
as LOW and skips confirmation, even though ``run_command`` executes a full
PowerShell line. This module replaces that decision for auto-allow: only a
small set of read-only commands with a fixed argument grammar qualifies, and
every path-like argument must pass path_guard.
"""
from __future__ import annotations

import re
import shlex

from src.agent.path_guard import validate_path

MAX_COMMAND_CHARS: int = 512
MAX_TOKENS: int = 12

_SHELL_METACHARS: frozenset[str] = frozenset(";|&><`(){}$\r\n")
_REVISION_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_./~^@{}-]+$")
_LOG_COUNT_RE: re.Pattern[str] = re.compile(r"^-n\d+$")

# command key -> allowed flags. Anything else forces a confirmation.
_ALLOWED_FLAGS: dict[tuple[str, ...], frozenset[str]] = {
    ("pwd",): frozenset(),
    ("git", "status"): frozenset({
        "-s", "--short", "-b", "--branch", "--porcelain", "--porcelain=v1",
        "--untracked-files=no",
    }),
    ("git", "diff"): frozenset({
        "-s", "--stat", "--name-only", "--name-status", "--no-color",
        "--color=never", "--cached", "--staged", "--quiet",
    }),
    ("git", "log"): frozenset({
        "--oneline", "--stat", "--name-only", "--no-color", "--decorate",
        "--graph", "--all", "--reverse",
    }),
    ("git", "show"): frozenset({
        "--stat", "--oneline", "--name-only", "--no-color", "--quiet",
    }),
}
_GIT_SUBCOMMANDS: frozenset[str] = frozenset({"status", "diff", "log", "show"})
_PATH_COMMANDS: frozenset[tuple[str, ...]] = frozenset({("git", "status")})


def is_auto_allowable(command: str) -> bool:
    """True only for a single safe command with a restricted argv."""
    if not command or len(command) > MAX_COMMAND_CHARS:
        return False
    if any(char in _SHELL_METACHARS for char in command):
        return False
    tokens = _split(command)
    if not tokens or len(tokens) > MAX_TOKENS:
        return False
    key = _command_key(tokens)
    if key is None:
        return False
    return _tokens_allowed(key, tokens[len(key):])


def _split(command: str) -> list[str]:
    """Tokenize without shell expansion; strip surrounding quotes."""
    try:
        raw = shlex.split(command, posix=False)
    except ValueError:
        return []
    return [token.strip("\"'") for token in raw]


def _command_key(tokens: list[str]) -> tuple[str, ...] | None:
    """Return the matched command key, or None when not allowlisted."""
    name = tokens[0].lower()
    if name == "pwd":
        return ("pwd",)
    if name != "git" or len(tokens) < 2:
        return None
    subcommand = tokens[1].lower()
    if subcommand not in _GIT_SUBCOMMANDS:
        return None
    return ("git", subcommand)


def _tokens_allowed(key: tuple[str, ...], rest: list[str]) -> bool:
    """Validate every flag and path/revision token against the policy."""
    flags = _ALLOWED_FLAGS[key]
    paths_mode = False
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--":
            paths_mode = True
            index += 1
            continue
        if token.startswith("-"):
            if token == "-n":
                if index + 1 >= len(rest) or not rest[index + 1].isdigit():
                    return False
                index += 2
                continue
            if not _flag_allowed(token, flags):
                return False
            index += 1
            continue
        if not _operand_allowed(key, token, paths_mode):
            return False
        index += 1
    return True


def _flag_allowed(token: str, flags: frozenset[str]) -> bool:
    """Only exact allowlisted flags; -n<count> is the sole parameterized form."""
    return token in flags or bool(_LOG_COUNT_RE.fullmatch(token))


def _operand_allowed(key: tuple[str, ...], token: str, paths_mode: bool) -> bool:
    """Path operands go through path_guard; git revisions stay read-only."""
    if key == ("pwd",):
        return False
    if paths_mode or key in _PATH_COMMANDS:
        return validate_path(token, is_write=False)[0]
    if ".." in token or not _REVISION_RE.fullmatch(token):
        return False
    return True
