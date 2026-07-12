"""MCPToolAdapter — wraps MCP server tools as ToolSpec instances.

Converts MCPClient.tools (MCPTool dataclasses) into MCPToolSpec instances
registered under the new ToolRouter, so MCP tools enjoy the same timeout /
audit / permission-gating as built-in tools (spec v2 §4).

Design:
  * name format: mcp__{server_name}__{tool_name}
  * risk_level: HIGH by default (MCP = external untrusted code, spec §4.3)
  * args_schema: JSON Schema -> Pydantic via create_model; permissive model
    when the server provides no schema; oneOf/anyOf -> Optional[Union[...]]
  * execute: delegates to MCPClient.call_tool; returns a clear error string
    when the server is disconnected (never crashes the Agent)
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.mcp.client import MCPTool

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════

# MCP tools are external untrusted code -> always HIGH risk (spec §4.3).
MCP_DEFAULT_RISK: RiskLevel = RiskLevel.HIGH
# Registered name format: mcp__<server>__<tool> (double-underscore separated).
MCP_NAME_PREFIX: str = "mcp"
MCP_NAME_SEPARATOR: str = "__"
# Returned in execute.output when the server is gone (spec §4.3).
MCP_DISCONNECTED_MSG: str = "MCP server disconnected"

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


# ═══════════════════════════════════════════
# JSON Schema -> Pydantic conversion
# ═══════════════════════════════════════════


class _PermissiveArgs(BaseModel):
    """Fallback args model: accepts any dict (extra="allow")."""

    model_config = ConfigDict(extra="allow")


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build the registered tool name: mcp__{server}__{tool}."""
    return (
        f"{MCP_NAME_PREFIX}{MCP_NAME_SEPARATOR}"
        f"{server_name}{MCP_NAME_SEPARATOR}{tool_name}"
    )


def _json_schema_to_pydantic(model_name: str, schema: dict) -> type[BaseModel]:
    """Convert a JSON Schema dict to a Pydantic model via create_model."""
    if not schema or not isinstance(schema, dict) or not schema.get("properties"):
        return _PermissiveArgs
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    return create_model(model_name, **_build_field_defs(props, required))


def _build_field_defs(props: dict, required: set[str]) -> dict:
    """Build pydantic field definitions from JSON Schema properties."""
    fields: dict = {}
    for name, prop in props.items():
        fields[name] = _build_one_field(name, prop or {}, required)
    return fields


def _build_one_field(name: str, prop: dict, required: set[str]) -> tuple:
    """Build a single (type, FieldInfo) tuple for create_model."""
    py_type = _json_type_to_python(prop)
    desc = prop.get("description", "") if isinstance(prop, dict) else ""
    if name in required:
        return (py_type, Field(..., description=desc))
    return (Optional[py_type], Field(default=None, description=desc))


def _json_type_to_python(schema: dict) -> Any:
    """Map a JSON Schema type to a Python type; union for oneOf/anyOf."""
    if "oneOf" in schema or "anyOf" in schema:
        return _union_type(schema.get("oneOf") or schema.get("anyOf") or [])
    return _JSON_TYPE_MAP.get(schema.get("type", ""), Any)


def _union_type(subschemas: list) -> Any:
    """Expand oneOf/anyOf sub-schemas into Optional[Union[...]]."""
    types = [_json_type_to_python(s) for s in subschemas if isinstance(s, dict)]
    if not types:
        return Any
    return Optional[Union[tuple(types)]]


# ═══════════════════════════════════════════
# MCPToolSpec — ToolSpec backed by an MCP server tool
# ═══════════════════════════════════════════


class MCPToolSpec(ToolSpec):
    """ToolSpec wrapper around an MCP server tool.

    `mcp_info` (inherited from ToolSpec) carries the origin:
    {"server_name": <id>, "tool_name": <mcp tool name>}. execute looks up the
    live MCPClient via MCPServerManager at call time so reconnections are
    transparent.
    """

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Delegate to MCPClient.call_tool; surface a clear error if down."""
        from src.mcp.manager import get_mcp_manager

        info = self.mcp_info or {}
        server_id = info.get("server_name", "")
        tool_name = info.get("tool_name", "")
        client = get_mcp_manager().get_client(server_id)
        if client is None or not client.connected:
            return {"output": MCP_DISCONNECTED_MSG}
        return await client.call_tool(tool_name, args)


# ═══════════════════════════════════════════
# Public adapter entry point
# ═══════════════════════════════════════════


def adapt_mcp_tools(server_name: str, mcp_tools: list[MCPTool]) -> list[MCPToolSpec]:
    """Convert MCPClient.tools into MCPToolSpec instances."""
    return [_adapt_one(server_name, tool) for tool in mcp_tools]


def _adapt_one(server_name: str, tool: MCPTool) -> MCPToolSpec:
    """Build a single MCPToolSpec from an MCPTool."""
    model_name = f"{server_name}_{tool.name}_args"
    return MCPToolSpec(
        name=mcp_tool_name(server_name, tool.name),
        description=tool.description or f"MCP tool {tool.name}",
        args_schema=_json_schema_to_pydantic(model_name, tool.input_schema),
        risk_level=MCP_DEFAULT_RISK,
        mcp_info={"server_name": server_name, "tool_name": tool.name},
    )
