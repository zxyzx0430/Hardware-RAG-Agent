"""
Hardware RAG Agent — WiringTool (hardware group).

Generates an SVG wiring diagram and a BOM list from components + connections.
Migrated from tools/wrappers.py (Task 1, SubTask 1.5).

industrial-tool-runtime Task 2: refactored to ToolSpec.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext


# ═══════════════════════════════════════════
# WiringTool
# ═══════════════════════════════════════════

class WiringArgs(BaseModel):
    title: str = Field(default="Wiring Diagram", description="接线图标题")
    components: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "组件列表，每个组件含 name / type / pins 字段，"
            "例如 {'name': 'ESP32-S3', 'type': 'mcu', 'pins': ['3V3', 'GND', 'GPIO4']}"
        ),
    )
    connections: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "连线列表，每条连线含 from_component / from_pin / "
            "to_component / to_pin / color / label 字段。"
        ),
    )


class WiringOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    svg: str = Field("", description="接线 SVG 图")
    bom: list = Field(default_factory=list, description="BOM 物料清单")


class WiringTool(ToolSpec):
    """Generate an SVG wiring diagram and a BOM list from components + connections."""
    name: str = "wiring"
    description: str = (
        "根据组件列表和连线列表生成接线 SVG 图与 BOM 清单（返回数据给 LLM 分析）。"
        "组件含 name/type/pins，连线含 from_component/from_pin/to_component/to_pin。"
        "\n\n与 render_wiring 的区分："
        "\n- wiring：生成 SVG+BOM 数据返回给 LLM，LLM 可基于数据做分析（如检查连线是否合理）"
        "\n- render_wiring：生成 SVG+BOM 并推送到前端工作台面板展示给用户看"
        "\n通常先调 wiring 检查数据合理性，确认无误后再调 render_wiring 推给用户。"
    )
    args_schema: type = WiringArgs
    output_schema: type[BaseModel] | None = WiringOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 30
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Generate wiring SVG + BOM (sync generator wrapped in a thread)."""
        from src.hardware.svg_generator import generate_wiring_svg

        title = args.get("title", "Wiring Diagram")
        components = args.get("components") or []
        connections = args.get("connections") or []
        svg, bom = await asyncio.to_thread(
            generate_wiring_svg, title, components, connections,
        )
        return _format_wiring_output(svg, bom, components, connections)


def _format_wiring_output(svg: str, bom: list, components: list, connections: list) -> dict:
    """Build the dict returned by WiringTool."""
    summary = (
        f"接线图生成完成：{len(components)} 个组件，{len(connections)} 条连线，"
        f"BOM {len(bom)} 项。"
    )
    return {"output": summary, "svg": svg, "bom": bom}
