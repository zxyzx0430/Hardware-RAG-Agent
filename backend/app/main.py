"""
Hardware RAG Agent — FastAPI 后端入口

用法：
  python -m app.main
  python app/main.py
"""

import sys
import os
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

from app.api.routes import router
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

async def limit_request_body_middleware(request: Request, call_next):
    """限制 /api/chat 请求体不超过 20MB。"""
    if request.url.path == "/api/chat":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY_SIZE:
            return JSONResponse(
                status_code=413,
                content={"success": False, "error": {"code": "PAYLOAD_TOO_LARGE", "message": "请求体超过 20MB 限制"}},
            )
    return await call_next(request)


async def request_log_middleware(request: Request, call_next):
    """为每个请求分配 request-id，记录访问日志，返回 X-Request-Id 响应头。

    对应 api-contract.md §2.15 请求追踪 ID 约定。
    """
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    start_ns = time.time_ns()
    response = await call_next(request)
    elapsed_ms = (time.time_ns() - start_ns) / 1_000_000

    response.headers["X-Request-Id"] = request_id

    _LOGGER.info(
        "%s %s %s %.0fms %s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    return response


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

    # 请求体大小限制中间件
    app.middleware("http")(limit_request_body_middleware)

    # 请求追踪与访问日志中间件（在 CORS 之后注册，确保能被遍历）
    app.middleware("http")(request_log_middleware)

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

    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(db_router)
    app.include_router(sandbox_router)
    app.include_router(mcp_router)
    app.include_router(feedback_router)
    app.include_router(search_router)

    env = os.getenv("ENVIRONMENT", "development")
    _LOGGER.info("App created: env=%s cors=%s", env, allowed_origins)

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
