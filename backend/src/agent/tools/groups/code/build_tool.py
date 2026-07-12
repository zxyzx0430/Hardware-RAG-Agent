"""
Hardware RAG Agent — BuildTool + FlashTool (code group).

BuildTool: 调 pio_runner.compile_firmware 编译 Arduino 代码到 ESP32 固件。
FlashTool: 调 pio_runner.upload_firmware 烧录固件到 ESP32 设备。

Spec: .trae/specs/agent-build-flash-esp32/spec.md (Requirement: Agent 编译烧录工具).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import (
    ConfirmationRule,
    RiskLevel,
    ToolSpec,
)
from src.agent.exceptions import ToolContext
from src.agent.streaming_event_bus import (
    emit_build_log_via_stream_writer,
    emit_tool_event,
)
from src.hardware.pio_runner import (
    CompileRequest,
    UploadRequest,
    compile_firmware,
    upload_firmware,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════

# 工具执行超时（秒）— pio 内部编译超时 600s（首次编译需下载依赖），工具层 650s 兜底。
# 首次编译会下载平台包 + 库依赖，网络慢时需要几分钟，拉长超时让 pio 自己下载完。
BUILD_TOOL_TIMEOUT_S: int = 650
FLASH_TOOL_TIMEOUT_S: int = 150
# 失败时附给 LLM 的最近 N 行 pio 日志
LOG_TAIL_LINES: int = 20
# 默认板型
DEFAULT_BOARD: str = "esp32-s3-devkitc-1"
DEFAULT_PLATFORM: str = "espressif32"
# 错误默认值
_UNKNOWN_ERROR: str = "未知错误"
_UNKNOWN_CODE: str = "UNKNOWN"
# 首次编译下载库的提示（超时时附给 LLM，让 LLM 告知用户）
_FIRST_BUILD_HINT: str = (
    "提示：首次编译会下载平台包和库依赖（如 DHT/WiFi 等），"
    "网络慢时可能需要 5-10 分钟。已设置 10 分钟超时让 pio 自己下载完，"
    "后续编译会使用缓存，速度快很多。如果反复超时，请检查网络或手动 "
    "pio lib install 安装依赖。"
)
# 缺失库提示（编译失败 + No such file or directory 时附给 LLM）
_MISSING_LIB_HINT: str = (
    "提示：编译失败可能是因为代码引用了第三方库。"
    "可在 lib_deps 参数中显式声明依赖库名（格式 'owner/repo'，如 'adafruit/Adafruit NeoPixel'），"
    "工具也会自动扫描 #include 推断常见库。"
)

# ═══════════════════════════════════════════
# Library dependency auto-resolution
# ═══════════════════════════════════════════

# ESP32 Arduino core / STM32 Arduino core 自带头文件，不需要装库
BUILTIN_HEADERS: frozenset[str] = frozenset({
    "Arduino.h", "Wire.h", "SPI.h", "WiFi.h", "WiFiClient.h", "WiFiServer.h",
    "WiFiAP.h", "HTTPClient.h", "WebServer.h", "EEPROM.h", "Preferences.h",
    "FS.h", "SD.h", "SPIFFS.h", "LittleFS.h", "Update.h", "BLEDevice.h",
    "BLEServer.h", "BLEUtils.h", "BLEScan.h", "BLEAdvertisedDevice.h",
    "HardwareSerial.h", "Serial.h", "Print.h", "Stream.h", "String.h",
    "Esp.h", "esp_system.h", "esp_sleep.h", "esp_timer.h", "esp_log.h",
    "freertos/FreeRTOS.h", "freertos/task.h", "freertos/queue.h",
    "freertos/semphr.h", "freertos/event_groups.h",
    "driver/gpio.h", "driver/uart.h", "driver/i2c.h", "driver/spi.h",
    "driver/ledc.h", "driver/adc.h", "driver/pwm.h", "driver/gptimer.h",
    "hal/gpio_hal.h", "hal/uart_hal.h",
    "stm32f1xx_hal.h", "stm32f4xx_hal.h", "stm32f7xx_hal.h",
    "Adafruit_TinyUSB.h",
})

# 常用第三方库头文件 → PlatformIO 库名映射
INCLUDE_TO_LIBDEPS: dict[str, str] = {
    # Adafruit 系列
    "Adafruit_NeoPixel.h": "adafruit/Adafruit NeoPixel",
    "DHT.h": "adafruit/DHT sensor library",
    "DHT_U.h": "adafruit/DHT sensor library",
    "Adafruit_Sensor.h": "adafruit/Adafruit Unified Sensor",
    "Adafruit_SSD1306.h": "adafruit/Adafruit SSD1306",
    "Adafruit_GFX.h": "adafruit/Adafruit GFX Library",
    "Adafruit_BMP280.h": "adafruit/Adafruit BMP280 Library",
    "Adafruit_BME280.h": "adafruit/Adafruit BME280 Library",
    "Adafruit_MPU6050.h": "adafruit/Adafruit MPU6050",
    "Adafruit_AHTX0.h": "adafruit/Adafruit AHTX0",
    "Adafruit_SHT31.h": "adafruit/Adafruit SHT31 Library",
    "Adafruit_VEML6070.h": "adafruit/Adafruit VEML6070",
    "Adafruit_TCS34725.h": "adafruit/Adafruit TCS34725",
    "Adafruit_INA219.h": "adafruit/Adafruit INA219",
    "Adafruit_ADS1X15.h": "adafruit/Adafruit ADS1X15",
    # 显示屏
    "U8g2lib.h": "olikraus/U8g2",
    "LiquidCrystal_I2C.h": "marcoschwartz/LiquidCrystal_I2C",
    "TFT_eSPI.h": "bodmer/TFT_eSPI",
    "LovyanGFX.hpp": "lovyan03/LovyanGFX",
    # 网络 / MQTT
    "PubSubClient.h": "knolleary/PubSubClient",
    "ESPAsyncWebServer.h": "me-no-dev/ESPAsyncWebServer",
    "AsyncTCP.h": "me-no-dev/AsyncTCP",
    "ArduinoJson.h": "bblanchon/ArduinoJson",
    # 传感器
    "OneWire.h": "paulstoffregen/OneWire",
    "DallasTemperature.h": "milesburton/DallasTemperature",
    "SHT31.h": "robtillaart/SHT31",
    "MAX6675.h": "adafruit/MAX6675 library",
    # 红外
    "IRremoteESP8266.h": "crankyoldgit/IRremoteESP8266",
    "IRremote.h": "z3t0/IRremote",
    # 电机 / 舵机
    "Servo.h": "arduino-libraries/Servo",
    "Stepper.h": "arduino-libraries/Stepper",
    "ESP32Servo.h": "madhephaestus/ESP32Servo",
    "AccelStepper.h": "waspinator/AccelStepper",
    # 无线
    "RF24.h": "nRF24/RF24",
    "LoRa.h": "sandeepmistry/LoRa",
    # RFID
    "MFRC522.h": "miguelbalboa/MFRC522",
    # IO 扩展
    "PCF8574.h": "xreef/PCF8574 library",
    "Adafruit_PCF8574.h": "adafruit/Adafruit PCF8574",
    # LED
    "FastLED.h": "fastled/FastLED",
    "WS2812FX.h": "kitesurfer1404/WS2812FX",
}

# 匹配 #include <xxx.h> 或 #include "xxx.h" 的正则
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)


def scan_lib_deps_from_code(code: str) -> list[str]:
    """Scan #include directives in code and return matched PlatformIO lib_deps.

    Returns list of library names (e.g. ["adafruit/Adafruit NeoPixel"]).
    Builtin headers (WiFi.h, Wire.h, etc.) are skipped.
    Unknown headers are logged but not errors.
    """
    if not code:
        return []
    matched: list[str] = []
    unknown: list[str] = []
    for header in _INCLUDE_RE.findall(code):
        # Extract basename for matching (e.g. "Adafruit/NeoPixel.h" → "NeoPixel.h")
        basename = header.rsplit("/", 1)[-1]
        if basename in BUILTIN_HEADERS:
            continue
        lib_name = INCLUDE_TO_LIBDEPS.get(basename)
        if lib_name:
            if lib_name not in matched:
                matched.append(lib_name)
        else:
            if basename not in unknown:
                unknown.append(basename)
    if unknown:
        logger.info("scan_lib_deps_from_code: unknown headers (not in map): %s", unknown)
    if matched:
        logger.info("scan_lib_deps_from_code: matched lib_deps: %s", matched)
    return matched


# ═══════════════════════════════════════════
# BuildTool
# ═══════════════════════════════════════════

class BuildCodeArgs(BaseModel):
    code: str = Field(description="要编译的 Arduino/C++ 代码（含 setup/loop）")
    board: str = Field(
        default=DEFAULT_BOARD,
        description="PlatformIO 板 ID，如 esp32-s3-devkitc-1 / esp32-devkitc-v4 / black_f407vg / nucleo_f407re",
    )
    platform: str = Field(
        default=DEFAULT_PLATFORM,
        description="平台：espressif32（ESP32 系列）或 ststm32（STM32 系列）",
    )
    framework: str = Field(
        default="arduino",
        description=(
            "开发框架：arduino（默认）/ espidf（ESP-IDF）/ stm32cube（STM32 HAL）。"
            "ESP32 系列支持 arduino + espidf；STM32 系列支持 arduino + stm32cube。"
            "用户指定用 ESP-IDF 或 Cube HAL 时传对应值。"
        ),
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="额外 PlatformIO 选项（如 upload_speed / upload_protocol 等）",
    )
    lib_deps: list[str] = Field(
        default_factory=list,
        description=(
            "代码依赖的 PlatformIO 库（格式 'owner/repo' 或 'name@version'）。"
            "常见库（如 Adafruit_NeoPixel、DHT、FastLED 等）可留空，"
            "工具会自动扫描 #include 推断并下载。"
        ),
    )


class BuildCodeOutput(BaseModel):
    binary_path: str = Field("", description="编译产物 firmware.bin 路径")
    success: bool = Field(False, description="是否编译成功")


class BuildTool(ToolSpec):
    """编译 Arduino 代码到 ESP32 固件（PlatformIO 真实编译）。

    LLM 决策调用时机：当用户要求编译代码、生成固件、检查代码能否编译时调用。
    编译成功返回 binary_path，后续可调 FlashTool 烧录到设备。
    """
    name: str = "build_firmware"
    description: str = (
        "编译 Arduino / ESP-IDF / STM32 Cube 代码到固件（基于 PlatformIO 真实编译）。"
        "当用户要求编译代码、生成固件、检查代码能否编译时调用。"
        "编译成功返回 binary_path，后续可调 flash_firmware 工具烧录到设备。"
        "\n\n支持平台 + 框架："
        "\n- espressif32 + arduino（默认，ESP32 全系列）"
        "\n- espressif32 + espidf（ESP-IDF 框架，用户指定时传 framework='espidf'）"
        "\n- ststm32 + arduino（STM32 全系列，Arduino 框架）"
        "\n- ststm32 + stm32cube（STM32 HAL 框架，用户指定时传 framework='stm32cube'）"
        "\n\nboard 传 PlatformIO 板 ID（如 esp32-s3-devkitc-1 / black_f407vg / nucleo_f407re）。"
        "\n如果代码用了第三方库（如 Adafruit_NeoPixel、DHT、FastLED 等），可传 lib_deps 参数显式声明（格式 'owner/repo'）；"
        "不传时工具会自动扫描 #include 推断常见库并自动下载。"
    )
    args_schema: type = BuildCodeArgs
    output_schema: type[BaseModel] | None = BuildCodeOutput

    risk_level: RiskLevel = RiskLevel.LOW  # 编译无副作用，LOW
    timeout_seconds: int = BUILD_TOOL_TIMEOUT_S
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Construct CompileRequest with merged lib_deps + framework and stream compile."""
        code = args.get("code", "")
        if not code or not code.strip():
            return _invalid_args_output("code 不能为空")
        explicit_deps = list(args.get("lib_deps", []) or [])
        scanned_deps = scan_lib_deps_from_code(code)
        merged_deps = list(dict.fromkeys(explicit_deps + scanned_deps))
        if merged_deps:
            logger.info("BuildTool lib_deps: explicit=%s scanned=%s merged=%s",
                        explicit_deps, scanned_deps, merged_deps)
        session_id = _make_session_id(ctx)
        req = CompileRequest(
            code=code,
            board=args.get("board", DEFAULT_BOARD),
            platform=args.get("platform", DEFAULT_PLATFORM),
            framework=args.get("framework", "arduino"),
            options=args.get("options", {}),
            session_id=session_id,
            lib_deps=tuple(merged_deps),
        )
        return await _run_compile(req, session_id)


async def _run_compile(req: CompileRequest, session_id: str = "") -> dict:
    """调 compile_firmware，收集事件流返回最终结果。"""
    done, log_lines = await _drain_stream(compile_firmware(req), session_id)
    binary_path = _extract_binary_path(done)
    error = _extract_error_dict(done)
    return _format_build_output(binary_path, error, log_lines)


def _format_build_output(
    binary_path: str, error: dict | None, log_lines: list[str],
) -> dict:
    """构造 LLM 看到的工具结果。"""
    if binary_path:
        return _build_success_output(binary_path)
    err_msg, err_code = _split_error(error)
    hint = _FIRST_BUILD_HINT if err_code in ("COMPILE_TIMEOUT", "UNKNOWN") else ""
    # 缺失库提示：编译失败 + 日志含 "No such file or directory"
    if not hint and _has_missing_header(log_lines):
        hint = _MISSING_LIB_HINT
    return {
        "output": (
            f"编译失败 [{err_code}]: {err_msg}\n"
            f"最近编译输出:\n{_tail_logs(log_lines)}\n"
            f"{hint}"
        ).rstrip(),
        "binary_path": "",
        "success": False,
        "target_pane": "flash",
        "render_data": {
            "stage": "compile",
            "success": False,
            "error": {"code": err_code, "message": err_msg},
        },
    }


def _has_missing_header(log_lines: list[str]) -> bool:
    """True if compile logs indicate a missing header file."""
    if not log_lines:
        return False
    tail = "\n".join(log_lines[-LOG_TAIL_LINES:])
    return "No such file or directory" in tail or "fatal error" in tail.lower()


def _build_success_output(binary_path: str) -> dict:
    """编译成功的工具结果。"""
    return {
        "output": f"编译成功，固件路径: {binary_path}",
        "binary_path": binary_path,
        "success": True,
        "target_pane": "flash",
        "render_data": {
            "stage": "compile",
            "binary_path": binary_path,
            "success": True,
        },
    }


# ═══════════════════════════════════════════
# FlashTool
# ═══════════════════════════════════════════

class FlashFirmwareArgs(BaseModel):
    binary_path: str = Field(
        description=(
            "要烧录的 firmware.bin 路径（来自 BuildTool 的返回值）。"
        ),
    )
    board: str = Field(
        default=DEFAULT_BOARD,
        description="PlatformIO 板 ID，如 esp32-s3-devkitc-1 / black_f407vg",
    )
    platform: str = Field(
        default=DEFAULT_PLATFORM,
        description="平台：espressif32（ESP32 系列）或 ststm32（STM32 系列）",
    )
    port: str = Field(
        description=(
            "烧录目标串口端口，如 COM3 / /dev/ttyUSB0。"
            "需先用 audit_pins 或串口扫描确认。"
        ),
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="额外 PlatformIO 选项",
    )


class FlashFirmwareOutput(BaseModel):
    success: bool = Field(False, description="是否烧录成功")
    port: str = Field("", description="实际烧录使用的端口")


class FlashTool(ToolSpec):
    """烧录固件到 ESP32 设备（基于 PlatformIO pio run --target upload）。

    LLM 决策调用时机：在 BuildTool 编译成功后，用户要求烧录到设备时调用。
    需要传入 binary_path（来自 BuildTool 返回）+ port（来自串口扫描）。

    risk_level=HIGH + requires_confirmation=CONDITIONAL：HIGH risk 走 HITL
    确认卡片，用户允许后执行（PermissionClassifier._decide_high 返回 ASK）。
    审计日志记录 HIGH risk_level。
    """
    name: str = "flash_firmware"
    description: str = (
        "烧录固件到设备（基于 PlatformIO pio run --target upload）。"
        "在 build_firmware 工具编译成功后调用，需要 binary_path 和串口端口。"
        "如不知端口，可让用户先扫描串口或调用 list_ports。"
        "支持 ESP32 全系列 + STM32 全系列。"
    )
    args_schema: type = FlashFirmwareArgs
    output_schema: type[BaseModel] | None = FlashFirmwareOutput

    risk_level: RiskLevel = RiskLevel.HIGH  # 烧录会修改设备状态，HIGH
    timeout_seconds: int = FLASH_TOOL_TIMEOUT_S
    max_retries: int = 0
    # HIGH risk triggers HITL confirm card via PermissionClassifier._decide_high
    requires_confirmation: ConfirmationRule = ConfirmationRule.CONDITIONAL

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """构造 UploadRequest 并流式烧录。"""
        binary_path = args.get("binary_path", "")
        port = args.get("port", "")
        if not binary_path or not binary_path.strip():
            return _invalid_args_output("binary_path 不能为空")
        if not port or not port.strip():
            return _invalid_args_output("port 不能为空")
        session_id = _make_session_id(ctx)
        req = UploadRequest(
            binary_path=binary_path,
            board=args.get("board", DEFAULT_BOARD),
            platform=args.get("platform", DEFAULT_PLATFORM),
            port=port,
            options=args.get("options", {}),
            session_id=session_id,
        )
        return await _run_flash(req, session_id)


async def _run_flash(req: UploadRequest, session_id: str = "") -> dict:
    """调 upload_firmware，收集事件流返回最终结果。"""
    done, log_lines = await _drain_stream(upload_firmware(req), session_id)
    success = _extract_success(done)
    error = _extract_error_dict(done)
    return _format_flash_output(success, req.port, error, log_lines)


def _format_flash_output(
    success: bool, port: str, error: dict | None, log_lines: list[str],
) -> dict:
    """构造 LLM 看到的工具结果。"""
    if success:
        return _flash_success_output(port)
    err_msg, err_code = _split_error(error)
    return {
        "output": (
            f"烧录失败 [{err_code}]: {err_msg}\n"
            f"最近烧录输出:\n{_tail_logs(log_lines)}"
        ),
        "success": False,
        "port": port,
        "target_pane": "flash",
        "render_data": {
            "stage": "flash",
            "port": port,
            "success": False,
            "error": {"code": err_code, "message": err_msg},
        },
    }


def _flash_success_output(port: str) -> dict:
    """烧录成功的工具结果。"""
    return {
        "output": f"烧录成功，固件已写入 {port} 并复位设备。",
        "success": True,
        "port": port,
        "target_pane": "flash",
        "render_data": {
            "stage": "flash",
            "port": port,
            "success": True,
        },
    }


# ═══════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════

def _invalid_args_output(message: str) -> dict:
    """Return an INVALID_ARGS-style tool result for missing required parameters."""
    return {
        "output": f"INVALID_ARGS: {message}",
        "binary_path": "",
        "success": False,
        "target_pane": "flash",
        "render_data": {
            "stage": "compile",
            "success": False,
            "error": {"code": "INVALID_ARGS", "message": message},
        },
    }


async def _drain_stream(
    stream: AsyncIterator[dict[str, Any]],
    session_id: str = "",
    writer: Any = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Consume pio event stream; return (done_event, log_lines).

    Forwards compile_log / progress / thinking / heartbeat events in real
    time so sse_adapter can yield them as SSE. Two emit paths:
      - writer (StreamWriter): stage 3 stream_events v3 path. Preferred when
        available (LangGraph injects writer when stream_mode includes "custom").
      - emit_tool_event (queue): stage 1/2 fallback. Used when writer is None
        (current create_react_agent path without stream_mode="custom").
    """
    done_event: dict[str, Any] | None = None
    log_lines: list[str] = []
    async for event in stream:
        etype = event.get("type")
        if etype == "compile_log":
            log_lines.append(event.get("line", ""))
            _emit_event(session_id, writer, event)
        elif etype in ("progress", "heartbeat"):
            _emit_event(session_id, writer, event)
        elif etype == "done":
            done_event = event
    return done_event, log_lines


def _emit_event(session_id: str, writer: Any, event: dict[str, Any]) -> None:
    """Emit a tool event via StreamWriter (preferred) or queue (fallback)."""
    # Lazy import to avoid module-level circular dependency (streaming_event_bus
    # imports from agent core which imports tools). The import is repeated here
    # because _drain_stream's inner import is function-scoped and not visible
    # to _emit_event.
    from src.agent.streaming_event_bus import emit_tool_event, emit_build_log_via_stream_writer
    if writer is not None:
        emit_build_log_via_stream_writer(writer, event)
    elif session_id:
        emit_tool_event(session_id, event)


def _extract_binary_path(done: dict[str, Any] | None) -> str:
    """从 done 事件提取 binary_path（仅 success 时存在）。"""
    if done and done.get("success"):
        return str(done.get("binary_path", ""))
    return ""


def _extract_success(done: dict[str, Any] | None) -> bool:
    """从 done 事件提取 success 标志。"""
    return bool(done and done.get("success"))


def _extract_error_dict(done: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 done 事件提取 error dict（仅 fail 时存在）。"""
    if done and not done.get("success"):
        return done.get("error") or None
    return None


def _split_error(error: dict | None) -> tuple[str, str]:
    """从 error dict 提取 (message, code)，缺失给默认值。"""
    err = error or {}
    return (
        str(err.get("message", _UNKNOWN_ERROR)),
        str(err.get("code", _UNKNOWN_CODE)),
    )


def _tail_logs(log_lines: list[str]) -> str:
    """返回最近 LOG_TAIL_LINES 行日志的拼接字符串。"""
    if not log_lines:
        return ""
    return "\n".join(log_lines[-LOG_TAIL_LINES:])


def _make_session_id(ctx: ToolContext) -> str:
    """从 ToolContext 取 session_id 或生成新 uuid。"""
    sid = getattr(ctx, "session_id", "") or ""
    return sid or uuid.uuid4().hex
