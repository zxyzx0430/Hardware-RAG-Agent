"""Workbench group — render_code + render_safety_report tools.

Also exposes get_workbench_tools() helper for Agent path registration
(migrated from tools/workbench_tools.py).
"""

from langchain_core.tools import BaseTool

from .render_code import RenderCodeArgs, RenderCodeTool
from .render_safety_report import RenderSafetyReportArgs, RenderSafetyReportTool

__all__ = [
    "RenderCodeTool",
    "RenderCodeArgs",
    "RenderSafetyReportTool",
    "RenderSafetyReportArgs",
    "get_workbench_tools",
]


def get_workbench_tools() -> list[BaseTool]:
    """Return the workbench display tools for Agent path registration."""
    return [RenderSafetyReportTool(), RenderCodeTool()]
