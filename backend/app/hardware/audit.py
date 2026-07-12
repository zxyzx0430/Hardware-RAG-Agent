"""
Hardware RAG Agent — 引脚审计核心逻辑。

从 app/api/hardware_routes.py 的 audit_pins 路由抽出，
供路由层和 agent 工具层复用，消除 stub 工具与真实实现的割裂。
"""

from __future__ import annotations

from typing import Any

from app.hardware.gpio import resolve_gpio, STRAPPING_PINS


def _parse_pin_gpio(pin_name: str) -> int | None:
    """解析引脚名到 GPIO 编号，失败返回 None。"""
    gpio = resolve_gpio(pin_name, {})
    if gpio is not None:
        return gpio
    try:
        return int(pin_name.replace("GPIO", "").replace("gpio", ""))
    except (ValueError, AttributeError):
        return None


def _build_warning(pin_name: str, message: str, suggestion: str) -> dict[str, Any]:
    """构建单个警告项。"""
    return {
        "pin": pin_name,
        "severity": "warning",
        "message": message,
        "suggestion": suggestion,
    }


def _check_conflict(
    pin_name: str, function: str, gpio: int, used_gpios: dict[int, str]
) -> dict[str, Any] | None:
    """检测单个引脚的冲突，返回冲突项或 None。"""
    if gpio in used_gpios:
        return {
            "pin": pin_name,
            "severity": "critical",
            "message": f"GPIO{gpio} 冲突: '{used_gpios[gpio]}' 和 '{function}'",
            "suggestion": f"请将 {pin_name} 重新分配到未使用的 GPIO",
        }
    return None


def _check_strapping(
    pin_name: str, function: str, gpio: int, strapping: set[int]
) -> dict[str, Any] | None:
    """检测 Strapping 引脚警告，返回警告项或 None。"""
    if gpio in strapping:
        return _build_warning(
            pin_name,
            f"GPIO{gpio}({function}) 是 Strapping 引脚，启动时影响芯片模式",
            f"避免使用 GPIO{gpio}，改用其他可用引脚",
        )
    return None


def audit_pins_core(chip: str, pin_assignments: dict[str, Any]) -> dict[str, Any]:
    """
    审计引脚分配，检测冲突和 Strapping 引脚。

    Args:
        chip: 芯片型号（如 "esp32-s3"）
        pin_assignments: {pin_name: {"function": str, "config": str}}

    Returns:
        {"safe": bool, "conflicts": list, "warnings": list, "pin_map": dict}
    """
    chip_lower = chip.lower()
    # 未知芯片不再 fallback 到 esp32-s3，返回空集避免误报
    strapping = STRAPPING_PINS.get(chip_lower, set())
    used_gpios: dict[int, str] = {}
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    pin_map: dict[str, str] = {}

    for pin_name, info in pin_assignments.items():
        function = info.get("function", "") if isinstance(info, dict) else str(info)
        gpio = _parse_pin_gpio(pin_name)
        if gpio is None:
            warnings.append(_build_warning(
                pin_name,
                f"无法解析引脚 {pin_name} 的 GPIO 编号",
                "请使用 GPIOx 或纯数字格式",
            ))
            continue
        conflict = _check_conflict(pin_name, function, gpio, used_gpios)
        if conflict:
            conflicts.append(conflict)
        else:
            used_gpios[gpio] = function
        strapping_warn = _check_strapping(pin_name, function, gpio, strapping)
        if strapping_warn:
            warnings.append(strapping_warn)
        pin_map[pin_name] = f"GPIO{gpio}"

    return {
        "safe": len(conflicts) == 0,
        "conflicts": conflicts,
        "warnings": warnings,
        "pin_map": pin_map,
    }
