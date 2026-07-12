"""PluginManager — registers plugins and chains hook execution.

pre_tool_call: runs all plugins by priority; if ANY returns None the call
is vetoed (manager returns None immediately). Otherwise returns the args
after all plugins have transformed them.

post_tool_result: runs all plugins by priority, threading the result
through each. A single plugin failure is logged and skipped (does not
break the chain) so one buggy plugin cannot poison the pipeline.

Spec: P1 task 8.
"""
from __future__ import annotations

import logging
from typing import Any

from src.agent.plugins.base import Plugin

logger = logging.getLogger(__name__)


class PluginManager:
    """Registry + dispatcher for plugins."""

    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    def register(self, plugin: Plugin) -> None:
        """Register a plugin, keeping the list sorted by priority."""
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)

    async def run_pre_tool_call(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Run pre_tool_call hooks. Returns None if any plugin vetoes."""
        current = args
        for plugin in self._plugins:
            current = await _safe_pre(plugin, tool, current)
            if current is None:
                logger.info("plugin_veto tool=%s plugin=%s", tool, plugin.name)
                return None
        return current

    async def run_post_tool_result(self, tool: str, result: dict[str, Any]) -> dict[str, Any]:
        """Run post_tool_result hooks, threading result through each."""
        current = result
        for plugin in self._plugins:
            current = await _safe_post(plugin, tool, current)
        return current


async def _safe_pre(plugin: Plugin, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Run one pre hook; on failure return args unchanged (no veto)."""
    try:
        return await plugin.pre_tool_call(tool, args)
    except Exception as exc:  # noqa: BLE001 — plugin isolation
        logger.warning("plugin_pre_failed tool=%s plugin=%s err=%s", tool, plugin.name, exc)
        return args


async def _safe_post(plugin: Plugin, tool: str, result: dict[str, Any]) -> dict[str, Any]:
    """Run one post hook; on failure return result unchanged."""
    try:
        return await plugin.post_tool_result(tool, result)
    except Exception as exc:  # noqa: BLE001 — plugin isolation
        logger.warning("plugin_post_failed tool=%s plugin=%s err=%s", tool, plugin.name, exc)
        return result


__all__ = ["PluginManager"]
