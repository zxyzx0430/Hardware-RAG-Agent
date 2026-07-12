"""MCP Server 管理 API"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from src.mcp.manager import get_mcp_manager
from app.api.dependencies import current_user

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

class MCPServerConfig(BaseModel):
    id: str
    name: str = ""
    command: str
    args: list[str] = []
    env: dict = {}

@router.post("/servers")
async def add_server(config: MCPServerConfig, user: dict = Depends(current_user)):
    """添加 MCP Server 配置"""
    manager = get_mcp_manager()
    manager.register_config(config.id, config.dict())
    return {"success": True, "data": {"id": config.id}}

@router.get("/servers")
async def list_servers(user: dict = Depends(current_user)):
    """列出所有 MCP Server 及状态"""
    manager = get_mcp_manager()
    servers = manager.list_servers()
    return {"success": True, "data": {"servers": servers}}

@router.post("/servers/{server_id}/start")
async def start_server(server_id: str, user: dict = Depends(current_user)):
    """启动 MCP Server"""
    manager = get_mcp_manager()
    success = await manager.start(server_id)
    if not success:
        raise HTTPException(500, detail=f"MCP Server {server_id} 启动失败")
    return {"success": True}

@router.post("/servers/{server_id}/stop")
async def stop_server(server_id: str, user: dict = Depends(current_user)):
    """停止 MCP Server"""
    manager = get_mcp_manager()
    await manager.stop(server_id)
    return {"success": True}

@router.get("/servers/{server_id}/tools")
async def list_tools(server_id: str, user: dict = Depends(current_user)):
    """列出 MCP Server 提供的工具"""
    manager = get_mcp_manager()
    client = manager.get_client(server_id)
    if not client:
        return {"success": True, "data": {"tools": []}}
    return {"success": True, "data": {"tools": [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in client.tools
    ]}}

@router.delete("/servers/{server_id}")
async def delete_server(server_id: str, user: dict = Depends(current_user)):
    """删除 MCP Server 配置"""
    manager = get_mcp_manager()
    await manager.stop(server_id)
    manager.remove_config(server_id)
    return {"success": True}
