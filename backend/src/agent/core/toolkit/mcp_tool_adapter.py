"""Create request-local ToolSpecs bound to a specific MCP client process."""

from __future__ import annotations

from typing import Any
import hashlib
import json
from copy import deepcopy

from pydantic import PrivateAttr

from src.agent.core.toolkit.tool_spec import ConfirmationRule, RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.mcp.client import MCPCallError, MCPClient, MCPTool, REQUEST_TIMEOUT_SECONDS


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Return the stable tool name used by Agent permissions and audit records."""
    name = f"mcp__{server_name}__{tool_name}"
    if len(name) <= 64 and "__" not in server_name and "__" not in tool_name:
        return name
    digest = hashlib.sha256(json.dumps([server_name, tool_name]).encode("utf-8")).hexdigest()[:16]
    return f"mcp__{server_name[:20]}__{tool_name[:18]}__{digest}"


class MCPToolSpec(ToolSpec):
    """A high-risk ToolSpec that cannot outlive its bound child process."""

    _client: MCPClient | None = PrivateAttr(default=None)
    _remote_tool_name: str = PrivateAttr(default="")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        client = self._client
        if client is None or not client.connected:
            raise MCPCallError("disconnected")
        return await client.call_tool(self._remote_tool_name, args)


def adapt_mcp_tools(
    server_name: str,
    client: MCPClient,
    mcp_tools: list[MCPTool],
) -> list[MCPToolSpec]:
    """Build fresh raw-schema ToolSpecs bound to this exact client instance."""
    specs = []
    for tool in mcp_tools:
        spec = MCPToolSpec(
            name=mcp_tool_name(server_name, tool.name),
            description=tool.description or f"MCP tool {tool.name}",
            args_schema=deepcopy(tool.input_schema),
            risk_level=RiskLevel.HIGH,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS + 5,
            max_retries=0,
            requires_confirmation=ConfirmationRule.ALWAYS,
            mcp_info={"server_name": server_name, "tool_name": tool.name},
        )
        spec._client = client
        spec._remote_tool_name = tool.name
        specs.append(spec)
    return specs
