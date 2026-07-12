"""Workbench group — render_wiring + render_code + render_safety_report tools.

Also exposes get_workbench_tools() helper for Agent path registration
(migrated from tools/workbench_tools.py).
"""

from langchain_core.tools import BaseTool

from .render_code import RenderCodeArgs, RenderCodeTool
from .render_safety_report import RenderSafetyReportArgs, RenderSafetyReportTool
from .render_wiring import RenderWiringArgs, RenderWiringTool

__all__ = [
    "RenderWiringTool",
    "RenderWiringArgs",
    "RenderCodeTool",
    "RenderCodeArgs",
    "RenderSafetyReportTool",
    "RenderSafetyReportArgs",
    "get_workbench_tools",
]


def get_workbench_tools() -> list[BaseTool]:
    """Return the 3 workbench display tools for Agent path registration."""
    return [RenderWiringTool(), RenderSafetyReportTool(), RenderCodeTool()]
