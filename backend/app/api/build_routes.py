"""Build 路由 — /api/build SSE + /api/upload SSE

真实调用 PlatformIO 编译/烧录（via pio_runner 共享核心）。
事件流: thinking → progress → compile_log → done

pio_runner 不自动触发编译：若 /api/upload 只传 code 而无 binary_path，
本路由层先调 compile_firmware 拿 binary_path 再调 upload_firmware。
"""

import logging
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.sse import sse_event
from app.api.locks import get_port_lock
from src.hardware.pio_runner import (
    CompileRequest as PioCompileRequest,
    UploadRequest as PioUploadRequest,
    cleanup_old_builds,
    compile_firmware as pio_compile_firmware,
    upload_firmware as pio_upload_firmware,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Per-port locks: 同一串口不能并发烧录（lock.locked() 预检 + async with 非原子，
# 但 demo 场景够用，避免两次 esptool 抢端口）。复用 app.api.locks.get_port_lock，
# 与 hardware_routes 等模块共享同一组锁，防止跨模块并发抢端口。
# ═══════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════


class BuildRequest(BaseModel):
    code: str
    board: str = "esp32-s3-devkitc-1"
    platform: str = "espressif32"
    framework: str = "arduino"
    lib_deps: list[str] = []
    options: dict = {}


class UploadRequest(BaseModel):
    code: str = ""
    binary_path: str = ""
    board: str = "esp32-s3-devkitc-1"
    platform: str = "espressif32"
    framework: str = "arduino"
    lib_deps: list[str] = []
    port: str = ""
    options: dict = {}


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════


def _generate_session_id() -> str:
    """生成唯一 session id（用于临时项目目录名）。"""
    return uuid.uuid4().hex


def _sse_from_event(event: dict[str, Any]) -> str:
    """把 pio_runner event dict 转为 sse_event 字符串。"""
    event_type = event.get("type", "unknown")
    data = {k: v for k, v in event.items() if k != "type"}
    return sse_event(event_type, data)


def _error_done(code: str, message: str) -> str:
    """构造错误 done SSE 事件。"""
    return sse_event("done", {"success": False, "error": {"code": code, "message": message}})


def _extract_binary_path(event: dict[str, Any]) -> str:
    """从 done 事件提取 binary_path；失败/缺失返回 ""。"""
    if not event.get("success"):
        return ""
    return event.get("binary_path") or ""


def _make_compile_request(payload: BuildRequest | UploadRequest) -> PioCompileRequest:
    """从 HTTP payload 构造 pio_runner CompileRequest（Build/Upload 均有 code/board/platform/framework/lib_deps/options）。"""
    return PioCompileRequest(
        code=payload.code,
        board=payload.board,
        platform=payload.platform,
        framework=payload.framework,
        lib_deps=tuple(payload.lib_deps or []),
        options=payload.options,
        session_id=_generate_session_id(),
    )


def _check_upload_input(payload: UploadRequest) -> str | None:
    """Return error SSE if input invalid/port busy, else None."""
    if not payload.binary_path and not payload.code:
        return _error_done("INVALID_ARGS", "需要 binary_path 或 code")
    if get_port_lock(payload.port).locked():
        return _error_done("PORT_BUSY", f"端口 {payload.port} 正在使用")
    return None


# ═══════════════════════════════════════════
# POST /api/build — 编译 SSE
# ═══════════════════════════════════════════


@router.post("/build")
async def build_firmware(payload: BuildRequest):
    """编译固件（SSE 流式返回编译进度 + PlatformIO 原始日志）。"""

    async def event_generator() -> AsyncIterator[str]:
        req = _make_compile_request(payload)
        async for event in pio_compile_firmware(req):
            yield _sse_from_event(event)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ═══════════════════════════════════════════
# POST /api/upload — 烧录 SSE
# ═══════════════════════════════════════════


@router.post("/upload")
async def upload_firmware(payload: UploadRequest):
    """烧录固件到设备（SSE 流式返回烧录进度）。

    支持两种入参：
    - binary_path 非空：直接烧录已编译固件
    - 仅 code：先编译再烧录（透传 compile 事件后接 upload 事件）
    """

    async def event_generator() -> AsyncIterator[str]:
        async for ev in _stream_upload(payload):
            yield ev

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _stream_upload(payload: UploadRequest) -> AsyncIterator[str]:
    """烧录主流程：校验 → 端口锁 → 编译(可选) → 烧录。"""
    err = _check_upload_input(payload)
    if err:
        yield err
        return
    async with get_port_lock(payload.port):
        async for ev in _run_upload_steps(payload):
            yield ev


async def _run_upload_steps(payload: UploadRequest) -> AsyncIterator[str]:
    """编译(若需) → 烧录，透传所有 SSE 事件。"""
    binary_path = payload.binary_path
    if not binary_path:
        holder: list[str] = [""]
        async for ev in _stream_compile(payload, holder):
            yield ev
        if not holder[0]:
            return
        binary_path = holder[0]
    async for ev in _run_pio_upload(payload, binary_path):
        yield ev


async def _stream_compile(payload: UploadRequest, holder: list[str]) -> AsyncIterator[str]:
    """编译代码并透传事件，成功时 holder[0]=binary_path。"""
    req = _make_compile_request(payload)
    async for event in pio_compile_firmware(req):
        if event.get("type") != "done":
            yield _sse_from_event(event)
            continue
        holder[0] = _extract_binary_path(event)
        yield _sse_from_event(event)
        return


async def _run_pio_upload(
    payload: UploadRequest, binary_path: str
) -> AsyncIterator[str]:
    """调用 pio upload_firmware 并透传事件。"""
    req = PioUploadRequest(
        binary_path=binary_path,
        board=payload.board,
        platform=payload.platform,
        port=payload.port,
        options=payload.options,
        session_id=_generate_session_id(),
        framework=payload.framework,
        lib_deps=tuple(payload.lib_deps or []),
    )
    async for event in pio_upload_firmware(req):
        yield _sse_from_event(event)


# ═══════════════════════════════════════════
# 启动清理
# ═══════════════════════════════════════════


@router.on_event("startup")
async def _cleanup_on_startup():
    """启动时清理旧的 build 临时目录（>24h）。"""
    try:
        deleted = await cleanup_old_builds()
        if deleted > 0:
            logger.info("startup_cleanup_old_builds deleted=%s", deleted)
    except Exception as e:
        logger.warning("startup_cleanup_failed err=%s", e)
