"""RAM-only management API for local MCP stdio servers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from app.api.dependencies import current_user, current_user_optional
from src.mcp.client import MCPConfigError
from src.mcp.manager import MCPManagerError, get_mcp_manager

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: StrictStr = Field(min_length=1, max_length=64)
    name: StrictStr = Field(default="", max_length=128)
    command: StrictStr = Field(min_length=1, max_length=4_096)
    args: list[StrictStr] = Field(default_factory=list, max_length=128)
    env: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=64)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": {"error": {"code": code, "message": message}}},
    )


@router.post("/servers")
async def add_server(payload: Any = Body(...), user: dict = Depends(current_user)):
    if type(payload) is not dict:
        return _error(422, "invalid_config", "MCP server configuration is invalid")
    try:
        config = MCPServerConfig.model_validate(payload, strict=True)
    except ValidationError:
        return _error(422, "invalid_config", "MCP server configuration is invalid")
    try:
        get_mcp_manager().register_config(config.id, config.model_dump())
    except MCPManagerError as exc:
        if exc.code == "duplicate_config":
            return _error(409, "duplicate_config", "MCP server is already configured")
        return _error(422, "invalid_config", "MCP server configuration is invalid")
    except MCPConfigError:
        return _error(422, "invalid_config", "MCP server configuration is invalid")
    return {"success": True, "data": {"id": config.id}}


@router.get("/servers")
async def list_servers(user: dict = Depends(current_user_optional)):
    return {"success": True, "data": {"servers": get_mcp_manager().list_servers()}}


@router.post("/servers/{server_id}/start")
async def start_server(server_id: str, user: dict = Depends(current_user)):
    manager = get_mcp_manager()
    if not manager.has_config(server_id):
        return _error(404, "server_not_found", "MCP server configuration was not found")
    if not await manager.start(server_id):
        return _error(502, "start_failed", "MCP server could not be started")
    return {"success": True, "data": {"id": server_id, "status": "running"}}


@router.post("/servers/{server_id}/stop")
async def stop_server(server_id: str, user: dict = Depends(current_user)):
    manager = get_mcp_manager()
    if not manager.has_config(server_id):
        return _error(404, "server_not_found", "MCP server configuration was not found")
    await manager.stop(server_id)
    return {"success": True, "data": {"id": server_id, "status": "stopped"}}


@router.get("/servers/{server_id}/tools")
async def list_tools(server_id: str, user: dict = Depends(current_user_optional)):
    manager = get_mcp_manager()
    if not manager.has_config(server_id):
        return _error(404, "server_not_found", "MCP server configuration was not found")
    client = manager.get_client(server_id)
    tools = client.tools if client is not None and client.connected else []
    return {
        "success": True,
        "data": {
            "tools": [
                {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                for tool in tools
            ]
        },
    }


@router.delete("/servers/{server_id}")
async def delete_server(server_id: str, user: dict = Depends(current_user)):
    removed = await get_mcp_manager().remove_config(server_id)
    if not removed:
        return _error(404, "server_not_found", "MCP server configuration was not found")
    return {"success": True, "data": {"id": server_id}}
