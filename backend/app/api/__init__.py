"""
API 路由聚合 — 按业务域拆分后的统一入口。

所有新路由文件在 __init__.py 层级聚合，供 main.py 导入。
"""

# 新拆分路由
from app.api.chat_routes import router as chat_router
from app.api.kb_routes import router as kb_router
from app.api.hardware_routes import router as hardware_router
from app.api.build_routes import router as build_router
from app.api.tool_routes import router as tool_router
from app.api.wiring_extract import router as wiring_extract_router

# 已有的独立路由文件
# 注：sandbox_routes 已于 2026-06-30 删除（Docker 沙箱废弃，改用 Agent run_command 工具）
from app.api.auth import router as auth_router
from app.api.crud import db_router
from app.api.mcp_routes import router as mcp_router
from app.api.feedback_routes import router as feedback_router
from app.api.search_routes import router as search_router

__all__ = [
    "chat_router", "kb_router", "hardware_router", "build_router", "tool_router",
    "wiring_extract_router",
    "auth_router", "db_router",
    "mcp_router", "feedback_router", "search_router",
]
