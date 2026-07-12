"""Arduino 代码器件/连线提取 — 正则匹配 + 启发式猜测。

从 Arduino 代码识别 pinMode/digitalWrite/digitalRead/analogRead/Wire/SPI/Serial，
用启发式规则猜测器件类型（LED/按钮/传感器/OLED 等），
返回匹配前端 WiringComponent/WiringConnection 类型的结构。
"""

from __future__ import annotations

import re

from app.hardware.gpio import resolve_gpio


# 命名常量（替代魔法数字）
ESP32_ADC_PINS = set(range(32, 40))  # GPIO 32-39 为 ESP32 ADC 引脚
LED_COLOR = "#ff0000"
SIGNAL_LINE = "signal"
NO_PIN_MESSAGE = "未在代码中检测到引脚使用"
MCU_NAME = "MCU"
LED_TYPE = "led"
OLED_TYPE = "oled"
SENSOR_TYPE = "sensor"
BUTTON_TYPE = "button"
SPI_DEVICE_TYPE = "spi_device"
UART_DEVICE_TYPE = "uart_device"

# 预编译正则
DEFINE_RE = re.compile(r"#define\s+(\w+)\s+(\d+)")
PINMODE_RE = re.compile(r"pinMode\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)")
DIGITAL_RE = re.compile(r"digital(Read|Write)\s*\(\s*(\w+)")
ANALOG_RE = re.compile(r"analog(Read|Write)\s*\(\s*(\w+)")
WIRE_BEGIN_RE = re.compile(r"Wire\.begin(?:Transmission)?\s*\(")
SPI_BEGIN_RE = re.compile(r"SPI\.begin\s*\(")
SERIAL_BEGIN_RE = re.compile(r"Serial\.begin\s*\(")

# 总线默认引脚（ESP32-S3）
I2C_PINS = ["SDA", "SCL"]
SPI_PINS = ["MOSI", "MISO", "SCK"]
UART_PINS = ["RX", "TX"]


def extract_wiring_from_code(code: str) -> dict:
    """从 Arduino 代码提取器件和连线，返回 {components, connections, message?}。"""
    defines = _extract_defines(code)
    pin_uses = _extract_pin_uses(code, defines)
    buses = _extract_buses(code)
    components, connections = _build_wiring(pin_uses, buses)
    return _format_result(components, connections)


def _extract_defines(code: str) -> dict[str, int]:
    """从 #define 提取宏定义引脚映射。"""
    defines: dict[str, int] = {}
    for name, value in DEFINE_RE.findall(code):
        defines[name] = int(value)
    return defines


def _extract_pin_uses(code: str, defines: dict[str, int]) -> list[dict]:
    """提取所有引脚使用记录（pinMode/digital/analog）。"""
    uses: list[dict] = []
    uses.extend(_extract_pin_modes(code, defines))
    uses.extend(_extract_digital_uses(code, defines))
    uses.extend(_extract_analog_uses(code, defines))
    return uses


def _extract_pin_modes(code: str, defines: dict[str, int]) -> list[dict]:
    """提取 pinMode(pin, MODE) 记录。"""
    uses: list[dict] = []
    for pin, mode in PINMODE_RE.findall(code):
        gpio = resolve_gpio(pin, defines)
        if gpio is not None:
            uses.append({"pin": pin, "gpio": gpio, "kind": "mode", "mode": mode.upper()})
    return uses


def _extract_digital_uses(code: str, defines: dict[str, int]) -> list[dict]:
    """提取 digitalRead/digitalWrite 记录，按操作推断 mode。"""
    uses: list[dict] = []
    for op, pin in DIGITAL_RE.findall(code):
        gpio = resolve_gpio(pin, defines)
        if gpio is not None:
            mode = "OUTPUT" if op == "Write" else "INPUT"
            uses.append({"pin": pin, "gpio": gpio, "kind": "digital", "mode": mode})
    return uses


def _extract_analog_uses(code: str, defines: dict[str, int]) -> list[dict]:
    """提取 analogRead/analogWrite 记录。"""
    uses: list[dict] = []
    for op, pin in ANALOG_RE.findall(code):
        gpio = resolve_gpio(pin, defines)
        if gpio is not None:
            uses.append({"pin": pin, "gpio": gpio, "kind": "analog", "op": op})
    return uses


def _extract_buses(code: str) -> dict[str, bool]:
    """提取 I2C/SPI/Serial 总线使用情况。"""
    return {
        "i2c": bool(WIRE_BEGIN_RE.search(code)),
        "spi": bool(SPI_BEGIN_RE.search(code)),
        "uart": bool(SERIAL_BEGIN_RE.search(code)),
    }


def _build_wiring(pin_uses: list[dict], buses: dict[str, bool]) -> tuple[list, list]:
    """构建 components + connections。"""
    components: list[dict] = []
    connections: list[dict] = []
    _add_bus_components(buses, components, connections)
    _add_pin_components(pin_uses, components, connections)
    return components, connections


def _add_bus_components(buses: dict[str, bool], components: list, connections: list) -> None:
    """根据总线使用添加器件和连线。"""
    if buses["i2c"]:
        _add_bus_component(components, connections, "OLED SSD1306", OLED_TYPE, I2C_PINS)
    if buses["spi"]:
        _add_bus_component(components, connections, "SPI Device", SPI_DEVICE_TYPE, SPI_PINS)
    if buses["uart"]:
        _add_bus_component(components, connections, "UART Module", UART_DEVICE_TYPE, UART_PINS)


def _add_bus_component(components: list, connections: list, name: str, ctype: str, pins: list[str]) -> None:
    """添加单个总线器件及其连线。"""
    components.append({"name": name, "type": ctype, "pins": list(pins)})
    for pin in pins:
        connections.append(_make_connection(pin, name, pin))


def _add_pin_components(pin_uses: list[dict], components: list, connections: list) -> None:
    """根据引脚使用添加器件和连线（按 GPIO 去重）。"""
    pin_info = _dedupe_pin_uses(pin_uses)
    for use in pin_info.values():
        _add_one_pin_component(use, components, connections)


def _dedupe_pin_uses(pin_uses: list[dict]) -> dict[int, dict]:
    """按 GPIO 去重，pinMode 的 mode 信息优先于 digital 推断。"""
    pin_info: dict[int, dict] = {}
    for use in pin_uses:
        gpio = use["gpio"]
        if gpio not in pin_info or use.get("kind") == "mode":
            pin_info[gpio] = use
    return pin_info


def _add_one_pin_component(use: dict, components: list, connections: list) -> None:
    """为单个引脚使用添加器件和连线。"""
    comp = _guess_component_type(use)
    if comp is None:
        return
    components.append(comp)
    connections.append(_build_pin_connection(comp, use))


def _guess_component_type(use: dict) -> dict | None:
    """根据引脚使用启发式猜测器件类型。"""
    pin_label = f"GPIO{use['gpio']}"
    if use.get("kind") == "analog" or use["gpio"] in ESP32_ADC_PINS:
        return {"name": "Analog Sensor", "type": SENSOR_TYPE, "pins": [pin_label]}
    mode = use.get("mode")
    if mode == "OUTPUT":
        return {"name": "LED", "type": LED_TYPE, "pins": [pin_label]}
    if mode in ("INPUT", "INPUT_PULLUP"):
        return {"name": "Button", "type": BUTTON_TYPE, "pins": [pin_label]}
    return None


def _build_pin_connection(comp: dict, use: dict) -> dict:
    """构建 MCU 到引脚器件的连线，LED 用 ANODE + 红色。"""
    pin_label = f"GPIO{use['gpio']}"
    is_led = comp["type"] == LED_TYPE
    comp_pin = "ANODE" if is_led else pin_label
    conn = _make_connection(pin_label, comp["name"], comp_pin)
    if is_led:
        conn["color"] = LED_COLOR
    return conn


def _make_connection(mcu_pin: str, comp_name: str, comp_pin: str) -> dict:
    """构建单条连线字典（MCU → 器件，signal 线型）。"""
    return {
        "from": {"component": MCU_NAME, "pin": mcu_pin},
        "to": {"component": comp_name, "pin": comp_pin},
        "line_type": SIGNAL_LINE,
    }


def _format_result(components: list, connections: list) -> dict:
    """格式化最终返回值，无器件时返回提示消息。"""
    if not components and not connections:
        return {"components": [], "connections": [], "message": NO_PIN_MESSAGE}
    return {"components": components, "connections": connections}
