"""
API 路由聚合 — 按业务域拆分后的统一入口。

所有新路由文件在 __init__.py 层级聚合，供 main.py 导入。

v1 兼容：routes.py 保留不动，新功能逐步迁移到新文件。
"""

# 新拆分路由
from app.api.chat_routes import router as chat_router
from app.api.kb_routes import router as kb_router
from app.api.hardware_routes import router as hardware_router
from app.api.build_routes import router as build_router
from app.api.tool_routes import router as tool_router

# 已有的独立路由文件
from app.api.auth import router as auth_router
from app.api.crud import db_router
from app.api.sandbox_routes import router as sandbox_router
from app.api.mcp_routes import router as mcp_router
from app.api.feedback_routes import router as feedback_router
from app.api.search_routes import router as search_router

# v1 兼容：保留旧 routes.py 引用，但不自动注册
# from app.api.routes import router as legacy_router

__all__ = [
    "chat_router", "kb_router", "hardware_router", "build_router", "tool_router",
    "auth_router", "db_router", "sandbox_router",
    "mcp_router", "feedback_router", "search_router",
]
