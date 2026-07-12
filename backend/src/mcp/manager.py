"""MCP Server 进程管理器"""
import asyncio
import logging
from typing import Optional
from .client import MCPClient

logger = logging.getLogger(__name__)

class MCPServerManager:
    """管理多个 MCP Server 的生命周期"""

    def __init__(self):
        self._servers: dict[str, MCPClient] = {}
        self._configs: dict[str, dict] = {}

    def register_config(self, server_id: str, config: dict):
        """注册 MCP Server 配置"""
        self._configs[server_id] = config

    def remove_config(self, server_id: str):
        """移除 MCP Server 配置"""
        self._configs.pop(server_id, None)

    async def start(self, server_id: str) -> bool:
        """启动 MCP Server 并注册工具到 _REGISTRY"""
        if server_id in self._servers and self._servers[server_id].connected:
            return True

        config = self._configs.get(server_id)
        if not config:
            logger.error(f"MCP Server 配置不存在: {server_id}")
            return False

        client = MCPClient(config)
        success = await client.connect()

        if success:
            self._servers[server_id] = client
            # Adapt MCP tools to ToolSpec and register to new ToolRouter
            # (spec v2 §4.3 — MCP tools join the unified runtime).
            self._register_tools(server_id, client.tools)
            return True

        return False

    async def stop(self, server_id: str):
        """停止 MCP Server 并从 ToolRouter 注销工具"""
        client = self._servers.pop(server_id, None)
        if client:
            # Unregister from new ToolRouter before tearing down the client
            # (client.tools is cleared on disconnect, so unregister first).
            self._unregister_tools(server_id, client.tools)
            await client.disconnect()

    def _register_tools(self, server_id: str, tools: list) -> None:
        """Adapt MCP tools to ToolSpec and register to new ToolRouter."""
        from src.agent.core.toolkit.mcp_tool_adapter import adapt_mcp_tools
        from src.agent.core.toolkit.tool_router import register

        for spec in adapt_mcp_tools(server_id, tools):
            register(spec)

    def _unregister_tools(self, server_id: str, tools: list) -> None:
        """Unregister a server's MCP tools from new ToolRouter."""
        from src.agent.core.toolkit.mcp_tool_adapter import mcp_tool_name
        from src.agent.core.toolkit.tool_router import unregister

        for tool in tools:
            unregister(mcp_tool_name(server_id, tool.name))

    async def health_check(self) -> dict[str, str]:
        """检查所有 MCP Server 的状态"""
        status = {}
        for sid, client in self._servers.items():
            status[sid] = "running" if client.connected else "stopped"
        for sid in self._configs:
            if sid not in status:
                status[sid] = "stopped"
        return status

    def get_client(self, server_id: str) -> Optional[MCPClient]:
        return self._servers.get(server_id)

    def list_servers(self) -> list[dict]:
        """列出所有 MCP Server 及状态"""
        result = []
        for sid, config in self._configs.items():
            client = self._servers.get(sid)
            result.append({
                "id": sid,
                "name": config.get("name", sid),
                "command": config.get("command", ""),
                "status": "running" if client and client.connected else "stopped",
                "tools_count": len(client.tools) if client else 0,
            })
        return result

# 全局单例
_manager: Optional[MCPServerManager] = None

def get_mcp_manager() -> MCPServerManager:
    global _manager
    if _manager is None:
        _manager = MCPServerManager()
    return _manager
