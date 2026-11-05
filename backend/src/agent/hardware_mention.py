"""Hardware mention hint: name an alias, never inject profile content.

The hint is advisory only. It tells the agent that the user mentioned a known
board alias so it can load the ``hardware-profile`` Skill; profile fields and
values stay out of the prompt until the agent explicitly calls the tool.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from app.db.hardware_profile_store import HardwareProfileStore

logger = logging.getLogger(__name__)

MAX_HINT_ALIASES = 3
MAX_ALIAS_DISPLAY_CHARS = 64


def match_hardware_aliases(message: str, aliases: Iterable[str]) -> list[str]:
    """Return aliases mentioned as whole tokens, longest first, deduplicated."""
    if not isinstance(message, str) or not message.strip():
        return []
    matched: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if not isinstance(alias, str) or not alias.strip():
            continue
        text = alias.strip()
        if not text or any(ord(char) < 32 for char in text):
            continue
        text = text[:MAX_ALIAS_DISPLAY_CHARS]
        key = text.casefold()
        if key in seen:
            continue
        if not _matches_whole_token(message, text):
            continue
        seen.add(key)
        matched.append(text)
    matched.sort(key=lambda item: (-len(item), item.casefold()))
    return matched


def _matches_whole_token(message: str, alias: str) -> bool:
    # ASCII letters/digits/underscore count as token characters; CJK and
    # punctuation act as boundaries, so "我的ESP32-S3开发板" still matches.
    pattern = re.compile(
        r"(?<![0-9A-Za-z_])" + re.escape(alias) + r"(?![0-9A-Za-z_])",
        re.IGNORECASE,
    )
    return pattern.search(message) is not None


def build_hardware_mention_hint(
    message: str,
    *,
    store: HardwareProfileStore | None = None,
) -> str:
    """Return a single-line hint when the latest user message names an alias.

    Returns an empty string when no alias matches or the profile store is
    unavailable. The hint never contains profile fields.
    """
    if not isinstance(message, str) or not message.strip():
        return ""
    try:
        pairs = (store or HardwareProfileStore()).list_aliases()
    except Exception:  # noqa: BLE001 — a hint must never break the chat turn
        logger.warning("hardware mention lookup failed", exc_info=True)
        return ""
    matched = match_hardware_aliases(message, (alias for _, alias in pairs))
    if not matched:
        return ""
    shown = "、".join(matched[:MAX_HINT_ALIASES])
    return (
        f"用户提到 {shown}；可考虑 load_skill('hardware-profile') "
        "并按需查询该硬件档案。"
    )
