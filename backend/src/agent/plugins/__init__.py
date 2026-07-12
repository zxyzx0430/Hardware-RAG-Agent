"""Agent plugins — hook-based extensibility for the tool execution pipeline.

Public API:
    Plugin          — abstract base, override pre_tool_call / post_tool_result
    PluginManager   — registry + dispatcher (chains hooks by priority)
    RedactPlugin    — built-in plugin that masks API keys in tool results

Usage:
    from src.agent.plugins import PluginManager, RedactPlugin
    pm = PluginManager()
    pm.register(RedactPlugin())
    args = await pm.run_pre_tool_call(tool, args)        # None = vetoed
    result = await pm.run_post_tool_result(tool, result) # transformed

Spec: P1 task 8 (插件钩子系统).
"""
from src.agent.plugins.base import Plugin
from src.agent.plugins.manager import PluginManager
from src.agent.plugins.redact_plugin import RedactPlugin

__all__ = ["Plugin", "PluginManager", "RedactPlugin"]
