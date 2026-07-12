"""解析芯片手册引脚表，从文本中提取引脚编号、名称、功能。

支持两种常见格式：
  1. 空格/Tab 分隔：`1 VCC 电源`
  2. Markdown 表格：`| 1 | VCC | 电源 |`

输入: args["text"] 引脚表文本（多行字符串）
输出: {"pins": [{"pin": "1", "name": "VCC", "function": "电源"}]}

典型用法：
  from src.agent.skills.parse_pin_table import main
  main({"text": "1 VCC 电源\\n2 GND 地"})
"""
from __future__ import annotations

import re
from typing import Any

# Named constants
_TABLE_ROW_RE = re.compile(
    r"^\s*\|\s*(?P<pin>\d+)\s*\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<func>[^|]+?)\s*\|"
)
_SPACE_ROW_RE = re.compile(
    r"^\s*(?P<pin>\d+)[\s\t]+(?P<name>\S+)[\s\t]+(?P<func>.+?)\s*$"
)
_MIN_PIN_LINES: int = 1


def main(args: dict[str, Any]) -> dict:
    """Parse pin table text into structured pin list.

    Returns {"pins": [...]} on success, {"error": "..."} on bad input.
    Lines that match neither pattern are silently skipped.
    """
    text = args.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return _err("text is empty or not a string")
    pins = _extract_pins(text)
    if not pins:
        return _err("no pin rows matched (expected '1 VCC 电源' or '| 1 | VCC | 电源 |')")
    return {"pins": pins, "count": len(pins)}


def _extract_pins(text: str) -> list[dict[str, str]]:
    """Walk lines, return matched pin dicts in order."""
    out: list[dict[str, str]] = []
    for line in text.splitlines():
        rec = _match_line(line)
        if rec is not None:
            out.append(rec)
    return out


def _match_line(line: str) -> dict[str, str] | None:
    """Try table pattern first, then space pattern."""
    m = _TABLE_ROW_RE.match(line)
    if m:
        return _build_rec(m)
    m = _SPACE_ROW_RE.match(line)
    if m:
        return _build_rec(m)
    return None


def _build_rec(m: re.Match[str]) -> dict[str, str]:
    """Build a pin dict from a regex match."""
    return {
        "pin": m.group("pin").strip(),
        "name": m.group("name").strip(),
        "function": m.group("func").strip(),
    }


def _err(msg: str) -> dict[str, Any]:
    """Build error result dict."""
    return {"error": msg, "pins": []}
