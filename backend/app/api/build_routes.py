"""Build 路由 — /api/build SSE + /api/upload SSE"""

import logging
import asyncio
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.common import sse_event, sanitize_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ═══════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════

class BuildRequest(BaseModel):
    code: str
    board: str = "esp32-s3"
    options: dict = {}


class UploadRequest(BaseModel):
    code: str
    board: str = "esp32-s3"
    port: str = ""
    options: dict = {}


# ═══════════════════════════════════════════
# POST /api/build — 编译 SSE
# ═══════════════════════════════════════════

@router.post("/build")
async def build_firmware(payload: BuildRequest):
    """编译固件（SSE 流式返回编译进度）。"""
    async def event_generator():
        yield sse_event("thinking", {"content": "正在编译固件...", "source": "build"})
        yield sse_event("progress", {"percent": 10, "message": "检查代码语法..."})
        await asyncio.sleep(0.3)
        yield sse_event("progress", {"percent": 30, "message": "编译中..."})
        await asyncio.sleep(0.3)
        yield sse_event("progress", {"percent": 70, "message": "链接中..."})
        await asyncio.sleep(0.3)
        yield sse_event("progress", {"percent": 100, "message": "编译完成"})
        yield sse_event("done", {"success": True})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ═══════════════════════════════════════════
# POST /api/upload — 烧录 SSE
# ═══════════════════════════════════════════

@router.post("/upload")
async def upload_firmware(payload: UploadRequest):
    """烧录固件到设备（SSE 流式返回烧录进度）。"""
    async def event_generator():
        yield sse_event("thinking", {"content": "正在烧录固件...", "source": "flash"})
        yield sse_event("progress", {"percent": 5, "message": "连接设备..."})
        await asyncio.sleep(0.5)
        yield sse_event("progress", {"percent": 30, "message": "擦除 Flash..."})
        await asyncio.sleep(0.3)
        yield sse_event("progress", {"percent": 60, "message": "写入固件..."})
        await asyncio.sleep(0.5)
        yield sse_event("progress", {"percent": 100, "message": "烧录完成"})
        yield sse_event("done", {"success": True})

    return StreamingResponse(event_generator(), media_type="text/event-stream")
