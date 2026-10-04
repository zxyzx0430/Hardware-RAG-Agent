"""In-memory lifecycle manager for local MCP stdio servers."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .client import MCPClient, MCPConfigError, normalize_server_config

if TYPE_CHECKING:
    from src.agent.core.toolkit.mcp_tool_adapter import MCPToolSpec

logger = logging.getLogger(__name__)
CONNECTION_DEADLINE_SECONDS = 60.0


class MCPManagerError(MCPConfigError):
    """Safe manager error identified by a stable machine-readable code."""


class MCPServerManager:
    """Manage configured child processes without global tool registration."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPClient] = {}
        self._configs: dict[str, dict[str, Any]] = {}
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}

    def register_config(self, server_id: str, config: dict[str, Any]) -> None:
        normalized = normalize_server_config(config)
        if server_id != normalized["id"]:
            raise MCPConfigError()
        if server_id in self._configs:
            raise MCPManagerError("duplicate_config")
        self._configs[server_id] = normalized

    async def remove_config(self, server_id: str) -> bool:
        if server_id not in self._configs and server_id not in self._servers:
            return False
        async with self._lock_for(server_id):
            client = self._servers.pop(server_id, None)
            if client is not None:
                await client.disconnect()
            return self._configs.pop(server_id, None) is not None

    def has_config(self, server_id: str) -> bool:
        return server_id in self._configs

    async def start(self, server_id: str) -> bool:
        """Start one configured server; only explicit calls can create a process."""
        if server_id not in self._configs:
            return False
        async with self._lock_for(server_id):
            config = self._configs.get(server_id)
            if config is None:
                return False
            existing = self._servers.get(server_id)
            if existing is not None and existing.connected:
                return True
            if existing is not None:
                self._servers.pop(server_id, None)
                await existing.disconnect()

            client = MCPClient(config)
            try:
                started = await asyncio.wait_for(client.connect(), timeout=CONNECTION_DEADLINE_SECONDS)
            except asyncio.CancelledError:
                await client.disconnect()
                raise
            except Exception:
                started = False
            if not started or not client.connected:
                await client.disconnect()
                logger.warning("MCP server start failed id=%s", server_id)
                return False
            self._servers[server_id] = client
            return True

    async def stop(self, server_id: str) -> None:
        if server_id not in self._configs and server_id not in self._servers:
            return
        async with self._lock_for(server_id):
            client = self._servers.pop(server_id, None)
            if client is not None:
                await client.disconnect()

    async def shutdown(self) -> None:
        """Stop every managed child for application lifespan shutdown."""
        for server_id in tuple(self._servers):
            await self.stop(server_id)

    def connected_tool_specs_snapshot(self) -> tuple[MCPToolSpec, ...]:
        """Return fresh request specs bound to each currently connected client."""
        from src.agent.core.toolkit.mcp_tool_adapter import adapt_mcp_tools

        specs = []
        for server_id, client in tuple(self._servers.items()):
            if client.connected:
                specs.extend(adapt_mcp_tools(server_id, client, client.tools))
        return tuple(specs)

    async def health_check(self) -> dict[str, str]:
        return {
            server_id: "running" if client.connected else "stopped"
            for server_id, client in self._servers.items()
        } | {
            server_id: "stopped"
            for server_id in self._configs
            if server_id not in self._servers
        }

    def get_client(self, server_id: str) -> MCPClient | None:
        return self._servers.get(server_id)

    def list_servers(self) -> list[dict[str, Any]]:
        result = []
        for server_id, config in self._configs.items():
            client = self._servers.get(server_id)
            result.append({
                "id": server_id,
                "name": config["name"],
                "command": config["command"],
                "status": "running" if client is not None and client.connected else "stopped",
                "tools_count": len(client.tools) if client is not None and client.connected else 0,
            })
        return result

    def _lock_for(self, server_id: str) -> asyncio.Lock:
        lock = self._lifecycle_locks.get(server_id)
        if lock is None:
            lock = asyncio.Lock()
            self._lifecycle_locks[server_id] = lock
        return lock


_manager: MCPServerManager | None = None


def get_mcp_manager() -> MCPServerManager:
    global _manager
    if _manager is None:
        _manager = MCPServerManager()
    return _manager
