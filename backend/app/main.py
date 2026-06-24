"""
Hardware RAG Agent — FastAPI 后端入口

用法：
  python -m app.main
  python app/main.py
"""

import sys
import os
import json
import logging
import time
import uuid
from pathlib import Path

# 确保 src 可导入（与 backend/main.py 同理）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# v1: routes.py 保留不动，新代码走拆分路由
from app.api.chat_routes import router as chat_router
from app.api.kb_routes import router as kb_router
from app.api.hardware_routes import router as hardware_router
from app.api.build_routes import router as build_router
from app.api.tool_routes import router as tool_router
from app.api.auth import router as auth_router
from app.api.crud import db_router
from app.api.sandbox_routes import router as sandbox_router
from app.api.mcp_routes import router as mcp_router
from app.api.feedback_routes import router as feedback_router
from app.api.search_routes import router as search_router
from src.config.settings import settings
from app.db.database import init_db

# 请求体大小限制（默认 20MB，可通过环境变量 MAX_BODY_SIZE 覆盖）
MAX_REQUEST_BODY_SIZE = int(os.getenv("MAX_BODY_SIZE", 20 * 1024 * 1024))

# 日志配置
_LOGGER = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 纯 ASGI 中间件 — 不缓冲 SSE 流式响应
# （替换原 BaseHTTPMiddleware，后者会消费整个响应体再转发，
#   导致 /api/chat 的 SSE 事件无法逐个推送到前端）
# ═══════════════════════════════════════════════════════════

class _RequestBodyLimitMiddleware:
    """纯 ASGI 中间件：拒绝超大体请求，不缓冲流式响应。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/api/chat":
            headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
            cl = headers.get("content-length")
            if cl and int(cl) > MAX_REQUEST_BODY_SIZE:
                body = json.dumps({
                    "success": False,
                    "error": {"code": "PAYLOAD_TOO_LARGE", "message": "请求体超过 20MB 限制"},
                }).encode()
                await send({
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)


class _RequestLogMiddleware:
    """纯 ASGI 中间件：注入 X-Request-Id + 访问日志，不缓冲流式响应。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        start_ns = time.time_ns()
        status_code = 0

        # 注入 request_id 到 scope.state，路由中可通过 request.state.request_id 访问
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        async def _send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send)

        elapsed_ms = (time.time_ns() - start_ns) / 1_000_000
        _LOGGER.info(
            "%s %s %s %.0fms %s",
            scope.get("method", "?"),
            scope.get("path", "?"),
            status_code,
            elapsed_ms,
            request_id,
        )


def configure_logging():
    """统一配置日志格式和级别（从 settings 读取）。"""
    level = getattr(settings, "log_level", "INFO").upper()
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 抑制 uvicorn 访问日志（由本中间件替代）
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    # 在 App 创建前完成日志配置
    configure_logging()

    app = FastAPI(title="Hardware RAG Agent API", version="0.2.0")

    # 初始化数据库表（CREATE TABLE IF NOT EXISTS）
    init_db()

    # CORS — 通过环境变量 CORS_ORIGINS 切换（逗号分隔），默认仅允许本地前端
    cors_env = os.getenv("CORS_ORIGINS", "")
    if cors_env:
        allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
    else:
        allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 请求体大小限制中间件（纯 ASGI，不缓冲 SSE）
    app.add_middleware(_RequestBodyLimitMiddleware)

    # 请求追踪与访问日志中间件（纯 ASGI，不缓冲 SSE）
    app.add_middleware(_RequestLogMiddleware)

    # 全局异常处理器：捕获未处理异常，避免堆栈泄露给客户端
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        _LOGGER.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务器内部错误",
                    "details": None,
                },
            },
        )

    app.include_router(chat_router)
    app.include_router(kb_router)
    app.include_router(hardware_router)
    app.include_router(build_router)
    app.include_router(tool_router)
    app.include_router(auth_router)
    app.include_router(db_router)
    app.include_router(sandbox_router)
    app.include_router(mcp_router)
    app.include_router(feedback_router)
    app.include_router(search_router)

    env = os.getenv("ENVIRONMENT", "development")
    _LOGGER.info("App created: env=%s cors=%s", env, allowed_origins)

    @app.on_event("startup")
    async def _ensure_builtin_kb():
        """启动时确保内置 KB 存在（若 builtin_kb 路径存在）。"""
        try:
            import sys
            print("STARTUP: importing kb_manager...", flush=True)
            from src.rag.kb_manager import get_kb_manager
            print("STARTUP: getting kb_manager...", flush=True)
            kb_manager = get_kb_manager()
            print("STARTUP: ensuring builtin kb...", flush=True)
            kb_manager.ensure_builtin_kb()
            print("STARTUP: done!", flush=True)
        except Exception:
            _LOGGER.warning("初始化内置 KB 失败（非致命）", exc_info=True)
            print("STARTUP: FAILED!", flush=True)

    @app.get("/")
    async def root():
        return {"status": "ok", "message": "Hardware RAG Agent API", "version": "0.2.0"}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


app = create_app()


def main():
    host = settings.host
    port = settings.port
    _LOGGER.info("启动 Hardware RAG Agent API: http://%s:%s", host, port)
    _LOGGER.info("API 文档: http://%s:%s/docs", host, port)
    uvicorn.run(app, host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
