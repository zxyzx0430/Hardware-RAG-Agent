"""
Hardware RAG Agent — AuditPinsTool (hardware group).

Checks GPIO assignments for conflicts and strapping-pin misuse.
Migrated from tools/wrappers.py (Task 1, SubTask 1.4).

industrial-tool-runtime Task 2: refactored to ToolSpec.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext


# ═══════════════════════════════════════════
# AuditPinsTool
# ═══════════════════════════════════════════

class PinAssignment(BaseModel):
    """单个引脚分配项。"""
    pin: str = Field(description="引脚名，如 'GPIO8'")
    function: str = Field(description="功能，如 'I2C_SDA' / 'UART_TX'")
    component: str = Field(default="", description="元件名，如 'SHT30'（可选）")


class AuditPinsArgs(BaseModel):
    chip: str = Field(
        default="esp32-s3",
        description="芯片型号，例如 'esp32-s3' / 'esp32' / 'stm32f103'",
    )
    pin_assignments: list[PinAssignment] = Field(
        default_factory=list,
        description=(
            "引脚分配列表，每项为 "
            "{'pin': 'GPIO8', 'function': 'I2C_SDA', 'component': 'SHT30'}（component 可选）。"
            "支持同一引脚出现多个功能项以触发冲突检测。"
        ),
    )


class AuditPinsOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter).
    The tool's result dict nests audit detail under a 'data' key, which
    flows through as envelope.data['data']."""
    data: dict = Field(default_factory=dict, description="引脚审计结果（safe/conflicts/warnings/pin_map）")


class AuditPinsTool(ToolSpec):
    """Check GPIO assignments for conflicts and strapping-pin misuse."""
    name: str = "audit_pins"
    description: str = (
        "检查引脚分配是否有 GPIO 冲突或踩到 Strapping 引脚（返回数据给 LLM 分析）。"
        "传入芯片型号和引脚分配列表，返回冲突列表和警告列表。"
        "支持检测同一引脚分配多个功能的冲突。"
        "支持 ESP32 全系列（S3/C3/C6/S2/H2）+ STM32 系列（F4/F7/H7 调试引脚）。"
        "\n\n与 render_safety_report 的区分："
        "\n- audit_pins：返回审计数据给 LLM，LLM 可基于数据做分析（如建议改用哪个引脚）"
        "\n- render_safety_report：审计并推送到前端工作台面板展示给用户看"
        "\n通常先调 audit_pins 检查问题，确认有/无问题后再调 render_safety_report 推给用户。"
    )
    args_schema: type = AuditPinsArgs
    output_schema: type[BaseModel] | None = AuditPinsOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 30
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Run pin audit (sync core wrapped in a thread)."""
        from app.hardware.audit import audit_pins_core

        chip = args.get("chip", "esp32-s3")
        pin_list = args.get("pin_assignments") or []
        pin_dict, dup_conflicts = _process_pin_list(pin_list)
        result = await asyncio.to_thread(
            audit_pins_core, chip=chip, pin_assignments=pin_dict,
        )
        if dup_conflicts:
            result["conflicts"].extend(dup_conflicts)
            result["safe"] = False
        return _format_audit_output(result)


def _process_pin_list(
    pin_list: list[Any],
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    """Convert list format to dict for audit_pins_core and detect duplicate-pin conflicts.

    audit_pins_core expects {pin: {"function": str}}; duplicate pins collapse to
    first occurrence. Same-pin multi-function conflicts are detected here since
    dict keys cannot represent duplicates.
    """
    pin_dict: dict[str, dict[str, str]] = {}
    pin_functions: dict[str, list[str]] = {}

    for item in pin_list:
        pin = _get_field(item, "pin", "")
        if not pin:
            continue
        function = _get_field(item, "function", "")
        if pin not in pin_dict:
            pin_dict[pin] = {"function": function, "config": _get_field(item, "config", "")}
        pin_functions.setdefault(pin, []).append(function)

    conflicts = _build_duplicate_conflicts(pin_functions)
    return pin_dict, conflicts


def _build_duplicate_conflicts(pin_functions: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Build conflict entries for pins assigned more than one function."""
    conflicts: list[dict[str, Any]] = []
    for pin, functions in pin_functions.items():
        if len(functions) > 1:
            conflicts.append({
                "pin": pin,
                "severity": "critical",
                "message": f"{pin} 冲突: 同一引脚分配多个功能 {functions}",
                "suggestion": f"请将 {pin} 的多余功能重新分配到未使用的 GPIO",
            })
    return conflicts


def _get_field(item: Any, name: str, default: str = "") -> str:
    """Get field from dict or pydantic model (handles both shapes)."""
    if isinstance(item, dict):
        value = item.get(name, default)
    else:
        value = getattr(item, name, default)
    return str(value) if value is not None else default


def _format_audit_output(result: dict) -> dict:
    """Build the dict returned by AuditPinsTool."""
    safe = result.get("safe", False)
    n_conflicts = len(result.get("conflicts", []))
    n_warnings = len(result.get("warnings", []))
    summary = (
        f"引脚审计完成：{'安全' if safe else '有冲突'}，"
        f"冲突 {n_conflicts} 项，警告 {n_warnings} 项。"
    )
    return {"output": summary, "data": result}
