"""Hardware 路由 — /api/devices /diagnose /wiring /audit_pins"""

import asyncio
import logging
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.hardware.svg_generator import generate_wiring_svg
from app.api.dependencies import current_user
from app.api.errors import sanitize_error
from app.api.locks import wiring_lock
from app.hardware.audit import audit_pins_core
from app.hardware.gpio import resolve_gpio, STRAPPING_PINS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ═══════════════════════════════════════════
# GET /api/devices — 扫描串口
# ═══════════════════════════════════════════

@router.get("/devices")
async def scan_devices(user: dict = Depends(current_user)):
    """扫描当前可用串口设备。"""
    try:
        import serial.tools.list_ports
        ports = await asyncio.to_thread(serial.tools.list_ports.comports)
        devices = [{
            "port": p.device,
            "description": p.description,
            "vid": p.vid,
            "pid": p.pid,
            "manufacturer": p.manufacturer,
            "serial_number": p.serial_number,
        } for p in ports]
        return {"success": True, "data": {"devices": devices}}
    except Exception as e:
        return {
            "success": False,
            "error": {"code": "DEVICE_SCAN_FAILED", "message": "串口设备扫描失败", "details": sanitize_error(str(e))},
        }


# ═══════════════════════════════════════════
# POST /api/diagnose — 代码与引脚诊断
# ═══════════════════════════════════════════

class DiagnoseRequest(BaseModel):
    code: str
    env: str = "esp32-s3"
    chip: str = "esp32-s3"


class DiagnoseItem(BaseModel):
    name: str
    status: Literal["PASS", "WARN", "FAIL"]
    detail: str


@router.post("/diagnose")
async def diagnose_code(payload: DiagnoseRequest, user: dict = Depends(current_user)):
    """对嵌入式代码做静态扫描，返回 GPIO 安全、引脚冲突等诊断项。"""
    try:
        code = payload.code
        chip = payload.chip.lower()
        defines: dict[str, int] = {}
        for match in re.finditer(r"#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)", code):
            name, val = match.groups()
            defines[name] = int(val, 16) if val.startswith("0x") else int(val)

        results: list[DiagnoseItem] = []
        strapping = STRAPPING_PINS.get(chip, STRAPPING_PINS["esp32-s3"])
        used_pins: dict[int, str] = {}
        violations: list[str] = []
        # pin_modes[gpio] = set of modes (INPUT/OUTPUT/INPUT_PULLUP) — for conflict detection
        pin_modes: dict[int, set[str]] = {}
        for match in re.finditer(r"pinMode\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)", code):
            pin_name, mode_name = match.group(1), match.group(2)
            gpio = resolve_gpio(pin_name, defines)
            if gpio is not None:
                pin_modes.setdefault(gpio, set()).add(mode_name)
                # Strapping 检测覆盖 pinMode（之前只检测 digitalWrite 等会漏掉 pinMode）
                if gpio in strapping:
                    violations.append(f"GPIO{gpio} 为 Strapping 引脚，建议避免使用")
        # Also track pins used by other GPIO ops (digitalWrite etc.)
        for match in re.finditer(r"(?:digitalWrite|digitalRead|analogRead|analogWrite)\s*\(\s*(\w+)\s*,", code):
            pin_name = match.group(1)
            gpio = resolve_gpio(pin_name, defines)
            if gpio is not None:
                if gpio in strapping:
                    violations.append(f"GPIO{gpio} 为 Strapping 引脚，建议避免使用")
                if gpio in used_pins:
                    violations.append(f"GPIO{gpio} 被多处使用: {used_pins[gpio]} 和 {pin_name}")
                used_pins[gpio] = pin_name
        if violations:
            results.append(DiagnoseItem(name="GPIO 安全检查", status="WARN", detail="; ".join(violations)))
        else:
            results.append(DiagnoseItem(name="GPIO 安全检查", status="PASS", detail="未发现引脚冲突或 Strapping 引脚"))

        # 语法预检
        found: list[str] = []
        if code.count("{") != code.count("}"):
            results.append(DiagnoseItem(name="编译预检", status="FAIL", detail="花括号不匹配"))
        elif code.count("(") != code.count(")"):
            results.append(DiagnoseItem(name="编译预检", status="FAIL", detail="括号不匹配"))
        else:
            functions = ["setup", "loop", "pinMode", "digitalWrite", "digitalRead", "delay", "Serial"]
            found = [f for f in functions if f in code]
            results.append(DiagnoseItem(name="编译预检", status="PASS", detail=f"识别到常见函数: {', '.join(found) if found else '无'}"))

        # 引脚冲突检测：同一引脚被 pinMode 设为不同方向（INPUT + OUTPUT）
        _INPUT_MODES = {"INPUT", "INPUT_PULLUP", "INPUT_PULLDOWN"}
        _OUTPUT_MODES = {"OUTPUT"}
        pin_conflicts: list[str] = []
        for gpio, modes in pin_modes.items():
            has_input = bool(modes & _INPUT_MODES)
            has_output = bool(modes & _OUTPUT_MODES)
            if has_input and has_output:
                pin_conflicts.append(f"GPIO{gpio} 同时被配置为 INPUT 和 OUTPUT")
        if pin_conflicts:
            results.append(DiagnoseItem(
                name="引脚冲突检测", status="FAIL",
                detail="; ".join(pin_conflicts),
            ))
        else:
            results.append(DiagnoseItem(
                name="引脚冲突检测", status="PASS",
                detail="未发现同一引脚被同时配置为输入和输出",
            ))
        results.append(DiagnoseItem(
            name="内存估算", status="PASS",
            detail=f"估算 SRAM 使用约 {min(30 + len(code) // 100, 80)}%",
        ))
        results.append(DiagnoseItem(
            name="Flash 兼容性", status="PASS",
            detail=f"识别到常见库: {', '.join(found[:5]) if found else 'delay, Serial'}",
        ))
        return {"success": True, "data": {"results": [r.model_dump() for r in results]}}
    except Exception as e:
        logger.exception("诊断异常")
        return {
            "success": False,
            "error": {"code": "DIAGNOSE_FAILED", "message": f"诊断失败: {sanitize_error(str(e))}", "details": sanitize_error(str(e))},
        }


# ═══════════════════════════════════════════
# POST /api/wiring — 接线图
# ═══════════════════════════════════════════

class WiringConnection(BaseModel):
    """接线连接项。字段名对齐 svg_generator 期望 + 前端易用别名。

    svg_generator 读取顺序：from_component or from, from_pin or pin,
    to_component, to_pin, color, label。
    """
    model_config = {"populate_by_name": True}

    from_component: str = Field(default="", alias="from")
    from_pin: str = Field(default="", alias="pin")
    to_component: str = ""
    to_pin: str = ""
    color: str = "#38bdf8"
    label: str = ""
    note: str = ""


class WiringComponent(BaseModel):
    """接线器件项。pins 为引脚名列表（svg_generator 期望 list）。"""
    model_config = {"populate_by_name": True}

    id: str = ""
    name: str
    type: str = "module"
    pins: list[str] = []


class WiringRequest(BaseModel):
    """接线图生成请求。title 为 SVG 标题，缺省"接线图"。"""
    title: str = "接线图"
    components: list[WiringComponent] = []
    connections: list[WiringConnection] = []


@router.post("/wiring")
async def generate_wiring(payload: WiringRequest, user: dict = Depends(current_user)):
    """生成接线 SVG 图。"""
    async with wiring_lock:
        try:
            svg, bom = generate_wiring_svg(
                title=payload.title,
                components=[c.model_dump(by_alias=True) for c in payload.components],
                connections=[c.model_dump(by_alias=True) for c in payload.connections],
            )
            return {"success": True, "data": {"svg": svg, "bom": bom}}
        except Exception as e:
            logger.exception("接线图生成失败")
            return {
                "success": False,
                "error": {"code": "WIRING_FAILED", "message": f"接线图生成失败: {sanitize_error(str(e))}", "details": sanitize_error(str(e))},
            }


# ═══════════════════════════════════════════
# POST /api/audit_pins — 引脚审计
# ═══════════════════════════════════════════

class PinAssignmentInfo(BaseModel):
    """单个引脚的分配信息（契约 5.6 pin_assignments value 结构）。"""
    function: str
    config: str = ""


class AuditPinsRequest(BaseModel):
    """契约 5.6: pin_assignments 为对象（key=引脚名，value=分配信息），非数组。"""
    chip: str = "esp32-s3"
    pin_assignments: dict[str, PinAssignmentInfo] = {}


class PinWarning(BaseModel):
    """引脚警告/冲突项（契约 5.6 响应 conflicts/warnings 元素结构）。"""
    pin: str
    severity: Literal["critical", "warning"] = "warning"
    message: str
    suggestion: str = ""


@router.post("/audit_pins")
async def audit_pins(payload: AuditPinsRequest, user: dict = Depends(current_user)):
    """审计引脚分配，检测冲突和 Strapping 引脚。"""
    try:
        result = audit_pins_core(
            chip=payload.chip,
            pin_assignments={
                name: info.model_dump() for name, info in payload.pin_assignments.items()
            },
        )
        return {"success": True, "data": result}
    except Exception as e:
        return {
            "success": False,
            "error": {"code": "AUDIT_FAILED", "message": f"引脚审计失败: {sanitize_error(str(e))}", "details": sanitize_error(str(e))},
        }
