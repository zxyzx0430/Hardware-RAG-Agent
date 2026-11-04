"""One-time setup code for the first provider configuration.

The code is printed to the server console and stored in ``backend/data``;
it is removed once the first provider is stored.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)

SETUP_CODE_PATH: Path = Path(__file__).parent.parent.parent / "data" / ".setup_code"
_LOCK = threading.RLock()
_cache: str = ""


def require_setup_code(supplied: str) -> None:
    """Reject first-run setup unless the console code matches."""
    expected = get_setup_code()
    if not supplied or not secrets.compare_digest(supplied.strip(), expected):
        raise HTTPException(
            403,
            detail={"success": False, "error": {
                "code": "SETUP_CODE_REQUIRED",
                "message": "首次配置需要服务端控制台显示的一次性确认码",
            }},
        )


def get_setup_code() -> str:
    """Load or create the one-time setup code (owner-only on POSIX)."""
    global _cache
    with _LOCK:
        if _cache:
            return _cache
        code = _read_code()
        _cache = code or _create_code()
        logger.warning("首次配置确认码: %s (文件: %s)", _cache, SETUP_CODE_PATH)
        return _cache


def clear_setup_code() -> None:
    """Invalidate the setup code once the first provider is stored."""
    global _cache
    with _LOCK:
        _cache = ""
        try:
            SETUP_CODE_PATH.unlink()
        except OSError:
            pass


def _read_code() -> str:
    try:
        return SETUP_CODE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _create_code() -> str:
    code = secrets.token_hex(8)
    SETUP_CODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETUP_CODE_PATH.write_text(code, encoding="utf-8")
    try:
        os.chmod(SETUP_CODE_PATH, 0o600)
    except OSError:
        pass
    return code
