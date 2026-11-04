"""RAM-only management API for local MCP stdio servers."""

from __future__ import annotations

import os
import shutil
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from app.api.dependencies import require_session
from src.mcp.client import MCPConfigError
from src.mcp.manager import MCPManagerError, get_mcp_manager

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "python", "python3", "python.exe", "node", "node.exe", "npx", "npx.cmd",
    "uv", "uv.exe", "uvx", "uvx.exe", "pnpm", "pnpm.cmd", "deno", "deno.exe",
    "bun", "bun.exe",
})
_BLOCKED_ARGS: frozenset[str] = frozenset({
    "-c", "-e", "--eval", "--exec", "-command", "-encodedcommand",
    "--import", "-r", "--require", "--command",
})
_BLOCKED_ENV_KEYS: frozenset[str] = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONSTARTUP", "PYTHONHOME",
    "NODE_OPTIONS", "NODE_PATH", "COMSPEC", "PATHEXT", "BASH_ENV", "ENV",
    "PROMPT_COMMAND",
})


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: StrictStr = Field(min_length=1, max_length=64)
    name: StrictStr = Field(default="", max_length=128)
    command: StrictStr = Field(min_length=1, max_length=4_096)
    args: list[StrictStr] = Field(default_factory=list, max_length=128)
    env: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=64)

    @field_validator("command")
    @classmethod
    def _check_command(cls, value: str) -> str:
        return _validate_command(value)

    @field_validator("args")
    @classmethod
    def _check_args(cls, value: list[str]) -> list[str]:
        return _validate_args(value)

    @field_validator("env")
    @classmethod
    def _check_env(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_env(value)


def _validate_command(value: str) -> str:
    """Only known interpreter launchers, never a shell."""
    name = os.path.basename(value.replace("\\", "/")).lower()
    if name not in _ALLOWED_COMMANDS:
        raise ValueError("MCP command is not allowlisted")
    if os.path.isabs(value):
        if not os.path.isfile(value):
            raise ValueError("MCP command does not exist")
    elif shutil.which(value) is None:
        raise ValueError("MCP command is not on PATH")
    return value


def _validate_args(value: list[str]) -> list[str]:
    """Reject inline-code flags and control characters."""
    for arg in value:
        if arg.lower() in _BLOCKED_ARGS:
            raise ValueError("MCP arg is not allowlisted")
        if any(char in arg for char in "\x00\r\n"):
            raise ValueError("MCP arg contains control characters")
    return value


def _validate_env(value: dict[str, str]) -> dict[str, str]:
    """Block preload/interpreter startup variables."""
    for key, item in value.items():
        if key.upper() in _BLOCKED_ENV_KEYS:
            raise ValueError("MCP env key is not allowlisted")
        if any(char in item for char in "\x00\r\n"):
            raise ValueError("MCP env value contains control characters")
    return value


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": {"error": {"code": code, "message": message}}},
    )


@router.post("/servers")
async def add_server(payload: Any = Body(...), user: str = Depends(require_session)):
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
async def list_servers(user: str = Depends(require_session)):
    return {"success": True, "data": {"servers": get_mcp_manager().list_servers()}}


@router.post("/servers/{server_id}/start")
async def start_server(server_id: str, user: str = Depends(require_session)):
    manager = get_mcp_manager()
    if not manager.has_config(server_id):
        return _error(404, "server_not_found", "MCP server configuration was not found")
    if not await manager.start(server_id):
        return _error(502, "start_failed", "MCP server could not be started")
    return {"success": True, "data": {"id": server_id, "status": "running"}}


@router.post("/servers/{server_id}/stop")
async def stop_server(server_id: str, user: str = Depends(require_session)):
    manager = get_mcp_manager()
    if not manager.has_config(server_id):
        return _error(404, "server_not_found", "MCP server configuration was not found")
    await manager.stop(server_id)
    return {"success": True, "data": {"id": server_id, "status": "stopped"}}


@router.get("/servers/{server_id}/tools")
async def list_tools(server_id: str, user: str = Depends(require_session)):
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
async def delete_server(server_id: str, user: str = Depends(require_session)):
    removed = await get_mcp_manager().remove_config(server_id)
    if not removed:
        return _error(404, "server_not_found", "MCP server configuration was not found")
    return {"success": True, "data": {"id": server_id}}
