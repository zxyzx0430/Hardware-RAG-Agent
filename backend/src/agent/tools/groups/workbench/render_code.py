"""
Hardware RAG Agent — RenderCodeTool (workbench group).

Pushes generated code to the PreviewPane.
Migrated from tools/workbench_tools.py (Task 1, SubTask 1.8).

industrial-tool-runtime Task 2: refactored to ToolSpec.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

# ═══════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════

DEFAULT_LANGUAGE: str = "cpp"


# ═══════════════════════════════════════════
# RenderCodeTool
# ═══════════════════════════════════════════

class RenderCodeArgs(BaseModel):
    code: str = Field(description="要展示的代码内容（完整源码字符串）")
    language: str = Field(
        default=DEFAULT_LANGUAGE,
        description="代码语言，如 'cpp' / 'python' / 'javascript'",
    )


class RenderCodeOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    target_pane: str = Field("preview", description="目标展示面板")
    render_data: dict = Field(default_factory=dict, description="渲染数据（code/language）")


class RenderCodeTool(ToolSpec):
    """Push generated code to the PreviewPane."""
    name: str = "render_code"
    description: str = (
        "把代码字符串推送到工作台 Preview Pane 展示，"
        "支持 cpp / python / javascript 等语言。"
    )
    args_schema: type = RenderCodeArgs
    output_schema: type[BaseModel] | None = RenderCodeOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 10
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Build render_code payload (pure transform, no IO)."""
        code = args.get("code", "")
        language = args.get("language", DEFAULT_LANGUAGE)
        return _format_code_result(code, language)


def _format_code_result(code: str, language: str) -> dict:
    """Build render_code payload with target_pane + render_data."""
    line_count = code.count("\n") + 1 if code else 0
    return {
        "output": f"已生成代码（{line_count} 行）。",
        "target_pane": "preview",
        "render_data": {"code": code, "language": language},
    }
