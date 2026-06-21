"""
Agent 模块 — LangChain ReAct Agent、工具调度、记忆系统。

核心组件：
- ToolRouter：register/dispatch 系统
- HardwareTools：5 个硬件工具
"""

__all__ = [
    "ToolHandler",
    "register",
    "dispatch",
    "ToolNotFoundError",
    "_REGISTRY",
    "register_mcp_tools",
    "unregister_mcp_tools",
]

_module_map = {
    "ToolHandler": "src.agent.tool_router",
    "register": "src.agent.tool_router",
    "dispatch": "src.agent.tool_router",
    "ToolNotFoundError": "src.agent.tool_router",
    "_REGISTRY": "src.agent.tool_router",
    "register_mcp_tools": "src.agent.tool_router",
    "unregister_mcp_tools": "src.agent.tool_router",
}

def __getattr__(name):
    import importlib
    if name in _module_map:
        module = importlib.import_module(_module_map[name])
        return getattr(module, name)
    raise AttributeError(f"module {name!r} has no attribute {name!r}")
