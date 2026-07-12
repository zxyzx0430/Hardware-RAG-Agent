"""
Hardware RAG Agent — FastAPI 后端入口

用法：
  python -m app.main
  python app/main.py
"""

import asyncio
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
from app.api.wiring_extract import router as wiring_extract_router
from app.api.auth import router as auth_router
from app.api.crud import db_router
from app.api.mcp_routes import router as mcp_router
from app.api.feedback_routes import router as feedback_router
from app.api.search_routes import router as search_router
from app.api.agent_sandbox_routes import router as agent_sandbox_router
from app.api.skill_routes import router as skill_router
from app.api.explorer_routes import router as explorer_router
from prometheus_client import Counter, Histogram, make_asgi_app
from src.config.settings import settings
from app.db.database import init_db

# 请求体大小限制（默认 20MB，可通过环境变量 MAX_BODY_SIZE 覆盖）
MAX_REQUEST_BODY_SIZE = int(os.getenv("MAX_BODY_SIZE", 20 * 1024 * 1024))

# 日志配置
_LOGGER = logging.getLogger(__name__)

# Temp file cleanup — runs every 1h, deletes files older than 24h
_CLEANUP_INTERVAL_SECONDS: int = 3600


async def _periodic_cleanup() -> None:
    """Background loop: clean build tmp + agent sandbox every hour."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        await _run_temp_cleanup()


async def _run_temp_cleanup() -> None:
    """Clean stale build artifacts and sandbox files (>24h)."""
    from src.hardware.pio_runner import cleanup_old_builds
    from src.agent.path_guard import cleanup_sandbox
    builds = await cleanup_old_builds()
    sandbox = await cleanup_sandbox()
    if builds or sandbox:
        _LOGGER.info("periodic cleanup: builds=%d sandbox=%d", builds, sandbox)

# HTTP 请求耗时指标（模块级单例，由 create_app() 在 metrics_enabled 时初始化）
# 在 _RequestLogMiddleware 中 observe；metrics 关闭时为 None，observe 被跳过。
_HTTP_REQUEST_HISTOGRAM = None

# Prometheus 指标（模块级单例，避免 create_app() 多次调用时重复注册）
_RAG_REQUESTS_TOTAL = Counter("rag_requests_total", "Total RAG requests", ["kb_id", "status"])
_RAG_RETRIEVAL_SECONDS = Histogram(
    "rag_retrieval_seconds", "RAG retrieval latency",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
)
_LLM_TOKENS_TOTAL = Counter("llm_tokens_total", "LLM tokens used", ["type", "model"])
_RAG_RERANKER_SECONDS = Histogram(
    "rag_reranker_seconds", "Reranker latency",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
)

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
            if cl:
                try:
                    cl_int = int(cl)
                except (TypeError, ValueError):
                    cl_int = 0
                if cl_int > MAX_REQUEST_BODY_SIZE:
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
        # B9: observe HTTP request latency (skip when metrics disabled)
        if _HTTP_REQUEST_HISTOGRAM is not None:
            try:
                _HTTP_REQUEST_HISTOGRAM.labels(
                    method=scope.get("method", "?"),
                    status=str(status_code or 0),
                ).observe(elapsed_ms / 1000.0)
            except Exception as e:
                _LOGGER.debug("metrics observe failed: %s", e)


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


def _warmup_rag_models_background() -> None:
    """后台线程预热 RAG 模型，避免首次查询延迟。

    预热内容：
      B: Reranker (bge-reranker-base, ~280MB, 加载 ~19s)
      D: BM25 索引（每个 enabled KB ~0.5-1s）
      C: Embedding 客户端（OpenAIEmbeddings 初始化 ~2-3s）

    用 daemon 线程执行，不阻塞 FastAPI 启动。模型加载失败只 warning，
    不影响启动——首次查询时会再次尝试加载（有 _RERANKER_PREDICT_FAILED 等兜底）。
    """
    import threading

    def _warmup():
        import time
        t0 = time.perf_counter()

        # B: Reranker 预加载
        try:
            from src.rag.reranker import get_reranker
            get_reranker()
            _LOGGER.info("[Warmup] Reranker loaded (%.1fs)", time.perf_counter() - t0)
        except Exception as e:
            _LOGGER.warning("[Warmup] Reranker preload failed (non-fatal): %s", e)

        # D: BM25 索引预加载（遍历所有 enabled KB）
        try:
            from src.rag.kb_manager import get_kb_manager, BM25_DIR
            kb_manager = get_kb_manager()
            kbs = kb_manager.list_kbs()
            bm25_count = 0
            for kb in kbs:
                if not kb.get("enabled", False):
                    continue
                kb_id = kb["id"]
                collection_name = kb.get("collection_name")
                if not collection_name:
                    continue
                bm25_path = BM25_DIR / f"{collection_name}.pkl"
                if not bm25_path.exists():
                    continue
                try:
                    from src.rag.kb_manager import BM25Index
                    kb_manager._bm25_indices[kb_id] = BM25Index.load(bm25_path)
                    bm25_count += 1
                except Exception:
                    _LOGGER.debug("[Warmup] BM25 load failed for KB %s", kb_id)
            _LOGGER.info(
                "[Warmup] BM25 indices loaded: %d/%d KBs (%.1fs total)",
                bm25_count, len(kbs), time.perf_counter() - t0,
            )
        except Exception as e:
            _LOGGER.warning("[Warmup] BM25 preload failed (non-fatal): %s", e)

        # C: Embedding 客户端预加载（触发 OpenAIEmbeddings 初始化）
        try:
            from src.config.settings import settings
            if settings.embedding_api_key:
                from src.rag.kb_manager import get_kb_manager
                kb_manager = get_kb_manager()
                kbs = kb_manager.list_kbs()
                # 找第一个 enabled KB 触发 store 初始化（store 内含 embeddings）
                for kb_dict in kbs:
                    if not kb_dict.get("enabled", False):
                        continue
                    kb_obj = kb_manager.get_kb(kb_dict["id"])
                    if kb_obj:
                        kb_manager._get_store(kb_obj)
                        _LOGGER.info(
                            "[Warmup] Embedding client initialized (%.1fs total)",
                            time.perf_counter() - t0,
                        )
                        break
        except Exception as e:
            _LOGGER.warning("[Warmup] Embedding preload failed (non-fatal): %s", e)

        _LOGGER.info("[Warmup] RAG models warmup done (%.1fs total)", time.perf_counter() - t0)

    thread = threading.Thread(target=_warmup, daemon=True, name="rag-warmup")
    thread.start()


def create_app() -> FastAPI:
    # 在 App 创建前完成日志配置
    configure_logging()

    app = FastAPI(title="Hardware RAG Agent API", version="0.2.0")

    # 初始化数据库表（CREATE TABLE IF NOT EXISTS）
    init_db()

    # v3-T1: 清理 30 天前的工具审计日志
    try:
        from src.agent.audit_logger import cleanup_old_logs
        deleted = cleanup_old_logs()
        if deleted:
            _LOGGER.info("cleaned %d old audit logs", deleted)
    except Exception as exc:
        _LOGGER.warning("audit cleanup on startup failed: %s", exc)

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
    app.include_router(wiring_extract_router)
    app.include_router(auth_router)
    app.include_router(db_router)
    app.include_router(mcp_router)
    app.include_router(feedback_router)
    app.include_router(search_router)
    app.include_router(agent_sandbox_router)
    app.include_router(skill_router)
    app.include_router(explorer_router)

    # Prometheus 指标（B9 优化）— 引用模块级单例，避免重复注册
    if settings.metrics_enabled:
        global _HTTP_REQUEST_HISTOGRAM
        if _HTTP_REQUEST_HISTOGRAM is None:
            _HTTP_REQUEST_HISTOGRAM = Histogram(
                "http_request_seconds", "HTTP request latency",
                ["method", "status"],
                buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
            )
        # 暴露到 app state 供 routes 使用
        app.state.metrics = {
            "rag_requests_total": _RAG_REQUESTS_TOTAL,
            "rag_retrieval_seconds": _RAG_RETRIEVAL_SECONDS,
            "llm_tokens_total": _LLM_TOKENS_TOTAL,
            "rag_reranker_seconds": _RAG_RERANKER_SECONDS,
        }
        # mount /metrics 端点
        app.mount("/metrics", make_asgi_app())
        _LOGGER.info("[Metrics] Prometheus /metrics endpoint enabled")

    env = os.getenv("ENVIRONMENT", "development")
    _LOGGER.info("App created: env=%s cors=%s", env, allowed_origins)

    @app.on_event("startup")
    async def _startup_tasks():
        """启动时执行初始化任务。"""
        # 1. Ensure builtin KB exists
        try:
            from src.rag.kb_manager import get_kb_manager
            kb_manager = get_kb_manager()
            kb_manager.ensure_builtin_kb()
        except Exception:
            _LOGGER.warning("初始化内置 KB 失败（非致命）", exc_info=True)

        # 2. Reset stuck indexing documents (crash recovery)
        # Any record still in "indexing" at startup is an orphan: the
        # background asyncio task that was processing it died with the
        # previous process. Reset all of them — no time threshold, because
        # even a 5-second-old "indexing" record is stale after a restart.
        try:
            from app.db.database import SessionLocal
            from app.db.models import KnowledgeDoc
            db = SessionLocal()
            stuck = db.query(KnowledgeDoc).filter(
                KnowledgeDoc.status == "indexing"
            ).all()
            for doc in stuck:
                doc.status = "error"
                doc.error_message = "服务重启时索引中断，请重新上传"
                _LOGGER.warning(f"Reset stuck indexing doc: {doc.doc_id}")
            db.commit()
            db.close()
        except Exception:
            _LOGGER.warning("清理卡住的索引任务失败（非致命）", exc_info=True)

        # 3. 预热 RAG 模型（后台线程，不阻塞启动）
        # B: Reranker 预加载（省首次查询 ~19s 模型加载）
        # D: BM25 索引预加载（省首次查询 5-10s，每个 KB ~0.5-1s）
        # C: Embedding 客户端预加载（省首次向量检索 2-3s 初始化）
        _warmup_rag_models_background()

        # 4. Start hourly temp file cleanup (>24h build tmp + sandbox)
        asyncio.create_task(_periodic_cleanup())

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
