"""代码提取路由 — POST /api/wiring/extract

从 Arduino 代码用正则提取器件/连线，调 app.hardware.code_extractor。
返回 {success: True, data: {components, connections, message?}} 或
     {success: False, error: {code, message, details?}}。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import current_user
from app.api.errors import sanitize_error
from app.hardware.code_extractor import extract_wiring_from_code

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ═══════════════════════════════════════════
# POST /api/wiring/extract — 从代码提取接线图
# ═══════════════════════════════════════════

class ExtractRequest(BaseModel):
    """代码提取请求。code 为 Arduino/C 代码字符串。"""
    code: str = Field(..., description="待提取的 Arduino 代码")


class ExtractResponse(BaseModel):
    """代码提取响应数据。"""
    components: list[dict] = []
    connections: list[dict] = []
    message: Optional[str] = None


@router.post("/wiring/extract")
async def extract_wiring(payload: ExtractRequest, user: dict = Depends(current_user)):
    """从 Arduino 代码提取器件和连线，返回 components/connections。"""
    if not payload.code or not payload.code.strip():
        return _empty_code_error()
    try:
        result = extract_wiring_from_code(payload.code)
        return {"success": True, "data": result}
    except Exception as e:
        logger.exception("代码提取失败")
        return _build_extract_error(e)


def _empty_code_error() -> dict:
    """构造 code 为空时的错误响应。"""
    return {
        "success": False,
        "error": {"code": "EMPTY_CODE", "message": "代码不能为空"},
    }


def _build_extract_error(e: Exception) -> dict:
    """构造提取失败的错误响应。"""
    return {
        "success": False,
        "error": {
            "code": "EXTRACT_FAILED",
            "message": f"代码提取失败: {sanitize_error(str(e))}",
            "details": sanitize_error(str(e)),
        },
    }
