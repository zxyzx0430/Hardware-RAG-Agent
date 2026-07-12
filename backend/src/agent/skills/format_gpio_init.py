"""生成 ESP32 GPIO 初始化代码。

按参数生成 Arduino 或 ESP-IDF 风格的 GPIO 初始化代码片段。默认 Arduino。

输入:
  args["pin"]    引脚号 (int 或 str)，例如 4
  args["mode"]   模式："input" / "output"
  args["pull"]   上下拉："pullup" / "pulldown" / "none"（默认 none）
  args["style"]  代码风格："arduino" / "espidf"（默认 arduino）

输出: {"code": "...", "style": "arduino"}

典型用法：
  from src.agent.skills.format_gpio_init import main
  main({"pin": 4, "mode": "output", "pull": "pullup"})
"""
from __future__ import annotations

from typing import Any

# Named constants
_MODE_INPUT: str = "input"
_MODE_OUTPUT: str = "output"
_PULL_PULLUP: str = "pullup"
_PULL_PULLDOWN: str = "pulldown"
_PULL_NONE: str = "none"
_STYLE_ARDUINO: str = "arduino"
_STYLE_ESPIDF: str = "espidf"
_VALID_MODES: frozenset[str] = frozenset({_MODE_INPUT, _MODE_OUTPUT})
_VALID_PULLS: frozenset[str] = frozenset({_PULL_PULLUP, _PULL_PULLDOWN, _PULL_NONE})
_VALID_STYLES: frozenset[str] = frozenset({_STYLE_ARDUINO, _STYLE_ESPIDF})
_DEFAULT_PULL: str = _PULL_NONE
_DEFAULT_STYLE: str = _STYLE_ARDUINO


def main(args: dict[str, Any]) -> dict:
    """Generate GPIO init code for ESP32 (Arduino default)."""
    pin = args.get("pin")
    if pin is None:
        return _err("missing pin")
    mode = str(args.get("mode", "")).lower()
    pull = str(args.get("pull", _DEFAULT_PULL)).lower()
    style = str(args.get("style", _DEFAULT_STYLE)).lower()
    err = _validate(mode, pull, style)
    if err:
        return _err(err)
    code = _generate(pin, mode, pull, style)
    return {"code": code, "style": style, "pin": pin, "mode": mode, "pull": pull}


def _validate(mode: str, pull: str, style: str) -> str | None:
    """Return error string if invalid, else None."""
    if mode not in _VALID_MODES:
        return f"invalid mode '{mode}': use 'input' or 'output'"
    if pull not in _VALID_PULLS:
        return f"invalid pull '{pull}': use 'pullup' / 'pulldown' / 'none'"
    if style not in _VALID_STYLES:
        return f"invalid style '{style}': use 'arduino' or 'espidf'"
    return None


def _generate(pin: Any, mode: str, pull: str, style: str) -> str:
    """Dispatch to the right generator by style."""
    if style == _STYLE_ARDUINO:
        return _gen_arduino(pin, mode, pull)
    return _gen_espidf(pin, mode, pull)


def _gen_arduino(pin: Any, mode: str, pull: str) -> str:
    """Arduino pinMode + digitalWrite snippet."""
    lines = [f"// ESP32 Arduino GPIO{pin} init"]
    if mode == _MODE_OUTPUT:
        lines.append(f"pinMode({pin}, OUTPUT);")
        lines.append(f"digitalWrite({pin}, LOW);  // initial state")
    else:
        lines.append(f"pinMode({pin}, INPUT{_arduino_pull_suffix(pull)});")
    return "\n".join(lines)


def _arduino_pull_suffix(pull: str) -> str:
    """Return Arduino pinMode pull suffix."""
    if pull == _PULL_PULLUP:
        return "_PULLUP"
    if pull == _PULL_PULLDOWN:
        return "_PULLDOWN"
    return ""


def _gen_espidf(pin: Any, mode: str, pull: str) -> str:
    """ESP-IDF gpio_config snippet."""
    cfg = _espidf_cfg_dict(pin, mode, pull)
    lines = [
        f"// ESP32 ESP-IDF GPIO{pin} init",
        "gpio_config_t io_conf = {",
    ]
    lines.extend(f"    .{k} = {v}," for k, v in cfg)
    lines.append("};")
    lines.append("gpio_config(&io_conf);")
    return "\n".join(lines)


def _espidf_cfg_dict(pin: Any, mode: str, pull: str) -> list[tuple[str, str]]:
    """Build ESP-IDF gpio_config_t field list (order preserved)."""
    pull_up_en, pull_mode = _espidf_pull(pull)
    pull_down_en = "1" if pull_mode == "down" else "0"
    mode_name = "GPIO_MODE_OUTPUT" if mode == _MODE_OUTPUT else "GPIO_MODE_INPUT"
    return [
        ("pin_bit_mask", f"(1ULL << {pin})"),
        ("mode", mode_name),
        ("pull_up_en", pull_up_en),
        ("pull_down_en", pull_down_en),
        ("intr_type", "GPIO_INTR_DISABLE"),
    ]


def _espidf_pull(pull: str) -> tuple[str, str]:
    """Return (pull_up_en, pull_mode) for ESP-IDF."""
    if pull == _PULL_PULLUP:
        return "1", "up"
    if pull == _PULL_PULLDOWN:
        return "0", "down"
    return "0", "none"


def _err(msg: str) -> dict[str, Any]:
    """Build error result dict."""
    return {"error": msg, "code": ""}
