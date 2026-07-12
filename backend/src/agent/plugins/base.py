"""Plugin base class — hooks for the tool execution pipeline.

Plugins extend Agent behavior without modifying ToolRouter. Each plugin
can implement ``pre_tool_call`` (veto or modify args) and
``post_tool_result`` (transform result). PluginManager chains plugins by
priority (lower priority value runs first).

Default hook implementations are no-ops so subclasses only override what
they need.

Spec: P1 task 8 (插件钩子系统).
"""
from __future__ import annotations

from abc import ABC
from typing import Any


class Plugin(ABC):
    """Base class for Agent plugins. Subclass and override hooks as needed.

    Attributes:
        name: plugin identifier (for logging / audit).
        priority: execution order; lower runs first (default 100).
    """

    name: str = "plugin"
    priority: int = 100

    async def pre_tool_call(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Hook before tool execution. Return modified args, or None to veto.

        Default: return args unchanged (no-op).
        """
        return args

    async def post_tool_result(self, tool: str, result: dict[str, Any]) -> dict[str, Any]:
        """Hook after tool execution. Return (possibly modified) result.

        Default: return result unchanged (no-op).
        """
        return result


__all__ = ["Plugin"]
