"""
Hardware RAG Agent — RenderWiringTool (workbench group).

Generates wiring SVG + BOM and pushes to the WiringPane.
Migrated from tools/workbench_tools.py (Task 1, SubTask 1.7).

industrial-tool-runtime Task 2: refactored to ToolSpec.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

# ═══════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════

DEFAULT_WIRING_TITLE: str = "Agent Wiring"


# ═══════════════════════════════════════════
# RenderWiringTool
# ═══════════════════════════════════════════

class RenderWiringArgs(BaseModel):
    components: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "组件列表，每项含 name / type / pins，"
            "例如 {'name': 'ESP32-S3', 'type': 'mcu', 'pins': ['3V3','GND','GPIO4']}"
        ),
    )
    connections: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "连线列表，每项含 from_component / from_pin / "
            "to_component / to_pin / color / label。"
        ),
    )


class RenderWiringOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    target_pane: str = Field("wiring", description="目标展示面板")
    render_data: dict = Field(default_factory=dict, description="渲染数据（svg/bom）")


class RenderWiringTool(ToolSpec):
    """Generate wiring SVG + BOM and push to the WiringPane."""
    name: str = "render_wiring"
    description: str = (
        "根据组件列表和连线列表生成接线 SVG 图与 BOM 清单，"
        "结果推送到工作台 Wiring Pane 展示给用户。"
        "\n\n与 wiring 的区分："
        "\n- render_wiring：推送到前端面板展示给用户看（用户可见）"
        "\n- wiring：返回数据给 LLM 分析（用户不可见，LLM 内部用）"
        "\n通常先调 wiring 检查数据合理性，确认无误后再调本工具推给用户。"
    )
    args_schema: type = RenderWiringArgs
    output_schema: type[BaseModel] | None = RenderWiringOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Generate wiring SVG + BOM (sync generator wrapped in a thread)."""
        from src.hardware.svg_generator import generate_wiring_svg

        components = args.get("components") or []
        connections = args.get("connections") or []
        svg, bom = await asyncio.to_thread(
            generate_wiring_svg,
            DEFAULT_WIRING_TITLE, components, connections,
        )
        return _format_wiring_result(svg, bom, components, connections)


def _format_wiring_result(
    svg: str, bom: list, components: list, connections: list,
) -> dict:
    """Build render_wiring payload with target_pane + render_data."""
    summary = (
        f"接线图生成完成：{len(components)} 个组件，{len(connections)} 条连线。"
    )
    return {
        "output": summary,
        "target_pane": "wiring",
        "render_data": {"svg": svg, "bom": bom},
    }
