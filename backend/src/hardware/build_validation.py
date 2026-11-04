"""Validation for PlatformIO build/upload inputs.

``options`` and ``lib_deps`` are rendered into platformio.ini, so an
unvalidated value can inject a new section/key (``extra_scripts``) or point
PlatformIO at a remote package whose build scripts run arbitrary Python.
"""
from __future__ import annotations

import re

MAX_OPTIONS: int = 10
MAX_LIB_DEPS: int = 20
MAX_VALUE_CHARS: int = 512
MAX_SESSION_ID_CHARS: int = 64

_ALLOWED_OPTION_KEYS: frozenset[str] = frozenset({
    "upload_port", "upload_protocol", "upload_speed", "monitor_speed",
    "board_build.filesystem", "board_build.partitions", "build_flags",
    "build_unflags",
})

_UPLOAD_PROTOCOLS: frozenset[str] = frozenset({
    "serial", "esptool", "stlink", "dfu", "jlink", "cmsis-dap",
})
_PORT_RE: re.Pattern[str] = re.compile(r"^(?:COM\d{1,3}|/dev/tty[A-Za-z0-9._-]{1,64})$")
_TOKEN_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_./-]{1,128}$")
_SESSION_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LIB_DEP_RE: re.Pattern[str] = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_ .+-]+(?:@[A-Za-z0-9_.+-]+|#[A-Za-z0-9_.-]+)?$"
)
_INI_UNSAFE: frozenset[str] = frozenset("\r\n\x00")


class BuildInputError(ValueError):
    """Raised when a build/upload input fails validation."""


def validate_options(options: dict | None) -> dict[str, str]:
    """Return only whitelisted, single-line option values."""
    if not options:
        return {}
    if len(options) > MAX_OPTIONS:
        raise BuildInputError("too many build options")
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in options.items():
        key = str(raw_key)
        if key not in _ALLOWED_OPTION_KEYS:
            raise BuildInputError(f"build option not allowed: {key}")
        value = _clean_value(raw_value, key)
        cleaned[key] = _validated_value(key, value)
    return cleaned


def validate_lib_deps(lib_deps: tuple[str, ...] | list[str]) -> list[str]:
    """Only ``owner/name[@version|#tag]`` entries; no URL/path packages."""
    if len(lib_deps) > MAX_LIB_DEPS:
        raise BuildInputError("too many lib_deps entries")
    cleaned: list[str] = []
    for dep in lib_deps:
        value = str(dep)
        if len(value) > MAX_VALUE_CHARS or not _LIB_DEP_RE.fullmatch(value):
            raise BuildInputError(f"lib_deps entry not allowed: {value}")
        cleaned.append(value)
    return cleaned


def sanitize_session_id(value: str) -> str:
    """Session id is used as a directory name; reject anything else."""
    if not value or not _SESSION_RE.fullmatch(value):
        raise BuildInputError("invalid session id")
    return value


def _clean_value(raw_value: object, key: str) -> str:
    """Reject control characters and overlong values before key checks."""
    value = str(raw_value)
    if len(value) > MAX_VALUE_CHARS or any(char in _INI_UNSAFE for char in value):
        raise BuildInputError(f"invalid value for {key}")
    return value.strip()


def _validated_value(key: str, value: str) -> str:
    """Apply the per-key grammar."""
    if key == "upload_port":
        return _match(_PORT_RE, value, key)
    if key == "upload_protocol":
        if value not in _UPLOAD_PROTOCOLS:
            raise BuildInputError("unsupported upload_protocol")
        return value
    if key in ("upload_speed", "monitor_speed"):
        return _positive_int(value, key)
    if key == "board_build.filesystem":
        if value not in ("default", "spiffs", "littlefs"):
            raise BuildInputError("unsupported filesystem")
        return value
    if key == "board_build.partitions":
        return _match(_TOKEN_RE, value, key)
    return _flag_value(value, key)


def _positive_int(value: str, key: str) -> str:
    if not value.isdigit() or not 9600 <= int(value) <= 4_000_000:
        raise BuildInputError(f"invalid {key}")
    return value


def _flag_value(value: str, key: str) -> str:
    """build_flags/build_unflags: single line, no shell/ini separators."""
    if not value or any(char in value for char in ";`"):
        raise BuildInputError(f"invalid {key}")
    return value


def _match(pattern: re.Pattern[str], value: str, key: str) -> str:
    if not pattern.fullmatch(value):
        raise BuildInputError(f"invalid {key}")
    return value
