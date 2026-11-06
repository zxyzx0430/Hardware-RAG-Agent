"""Plan-mode tool policy.

Plan mode is a deliberate read-only research phase. The allowlist is
default-deny so a newly added tool is not exposed until it is reviewed. The
same predicate is used when exposing tools to the model and immediately before
tool execution, which keeps the two entry points aligned.
"""

from __future__ import annotations

from typing import Any, Iterable

PLAN_MODE = "plan"
SUBMIT_PLAN_TOOL_NAME = "submit_plan"

READ_ONLY_TOOL_NAMES = frozenset({
    "search_docs",
    "list_kb_docs",
    "read_context_event",
    "read_tool_event",
    "read_tool_observation",
    "read_rolling_skill_history",
    "read_owner_rolling_history",
    "search_history",
    "memory_search",
    "web_search",
    "webfetch",
    "view_image",
    "vision_analysis",
    "read_file",
    "list_files",
    "glob",
    "grep",
    "audit_pins",
    "load_skill",
    "read_skill_resource",
})

ACTION_ALLOWLIST = {
    "hardware_profile": frozenset({"search", "get"}),
}


def is_allowed_in_plan_mode(name: str, args: Any = None) -> bool:
    """Return True when the tool (and action, if applicable) is read-only."""
    if name == SUBMIT_PLAN_TOOL_NAME:
        return True
    if name in READ_ONLY_TOOL_NAMES:
        return True
    allowed_actions = ACTION_ALLOWLIST.get(name)
    if allowed_actions is None:
        return False
    action = args.get("action") if isinstance(args, dict) else None
    return isinstance(action, str) and action in allowed_actions


def filter_tools_for_plan_mode(tools: Iterable[Any]) -> list[Any]:
    """Keep only tools visible in plan mode (default-deny by name)."""
    return [tool for tool in tools if is_allowed_in_plan_mode(tool.name)]
