"""Tool groups — 6 domain-organized subpackages.

Layout (spec: rag-to-agent-tool-trigger, Task 1):
  retrieval/  — search_docs, web_search, list_kb_docs, search_history,
                web_fetch, vision_analysis, view_image, image_generation
  hardware/   — audit_pins, wiring
  workbench/  — render_wiring, render_code, render_safety_report
  code/       — generate_code, build_firmware, flash_firmware
  file_ops/   — read_file, write_file, edit_file (+ shared _permission)
  execution/  — run_command

build_tools (agent_factory.py) instantiates ALL tools (全量注入, currently 27);
Agent decides which to call. This package only reorganizes code.
"""

from src.agent.tools.groups.code import (
    BuildCodeArgs, BuildTool, FlashFirmwareArgs, FlashTool,
)
from src.agent.tools.groups.execution import RunCommandTool
from src.agent.tools.groups.file_ops import EditFileTool, ReadFileTool, WriteFileTool
from src.agent.tools.groups.hardware import AuditPinsTool, WiringTool
from src.agent.tools.groups.retrieval import SearchDocsTool, WebSearchTool
from src.agent.tools.groups.workbench import (
    RenderCodeTool,
    RenderSafetyReportTool,
    RenderWiringTool,
    get_workbench_tools,
)

__all__ = [
    # retrieval
    "SearchDocsTool",
    "WebSearchTool",
    # hardware
    "AuditPinsTool",
    "WiringTool",
    # workbench
    "RenderWiringTool",
    "RenderCodeTool",
    "RenderSafetyReportTool",
    "get_workbench_tools",
    # code
    "BuildTool",
    "BuildCodeArgs",
    "FlashTool",
    "FlashFirmwareArgs",
    # file_ops
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    # execution
    "RunCommandTool",
]
