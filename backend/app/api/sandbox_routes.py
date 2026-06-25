"""沙箱执行 API 端点"""
import asyncio
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from src.sandbox import execute_code, check_docker_available
from app.api.dependencies import current_user

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])

# Limit concurrent container execution to prevent resource exhaustion
_sandbox_semaphore = asyncio.Semaphore(4)

# 允许在沙箱中执行的语言白名单
ALLOWED_LANGUAGES = {"python", "c", "cpp", "javascript", "arduino"}

class ExecuteRequest(BaseModel):
    code: str
    language: str = "python"

@router.post("/execute")
async def execute(req: ExecuteRequest, user: dict = Depends(current_user)):
    """在沙箱中执行代码"""
    # 契约 5.21: 错误响应使用标准 {success: False, error: {code, message}} 格式
    if not req.code.strip():
        return {
            "success": False,
            "error": {"code": "EMPTY_CODE", "message": "代码不能为空"},
        }
    if len(req.code) > 50000:
        return {
            "success": False,
            "error": {"code": "CODE_TOO_LONG", "message": "代码长度超过限制（50000 字符）"},
        }

    # 语言白名单校验：防止传入 bash/sh 等执行任意命令
    if req.language.lower() not in ALLOWED_LANGUAGES:
        return {
            "success": False,
            "error": {
                "code": "UNSUPPORTED_LANGUAGE",
                "message": f"不支持的语言: {req.language}，支持: {', '.join(sorted(ALLOWED_LANGUAGES))}",
            },
        }

    async with _sandbox_semaphore:
        result = await execute_code(req.code, req.language)

    # 契约 5.21: 沙箱不可用/超时/内部错误返回标准错误格式
    if result.timed_out:
        return {
            "success": False,
            "error": {"code": "EXECUTION_TIMEOUT", "message": f"执行超时（{result.duration_ms}ms）"},
        }

    if result.exit_code < 0 and "Docker" in result.stderr:
        return {
            "success": False,
            "error": {"code": "SANDBOX_UNAVAILABLE", "message": result.stderr},
        }

    return {
        "success": True,
        "data": {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
        }
    }

@router.get("/status")
async def status():
    """检查 Docker 是否可用"""
    available = await check_docker_available()
    return {
        "success": True,
        "data": {
            "docker_available": available,
            "supported_languages": sorted(ALLOWED_LANGUAGES),
        }
    }
