"""
Hardware RAG Agent — 可扩展工具路由器

提供 ToolHandler 协议、@register 装饰器 / register() 函数、
内置 stub 工具以及 dispatch() 统一调度入口。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Protocol, Type, Union, runtime_checkable


logger = logging.getLogger(__name__)


def _sanitize_error(msg: str) -> str:
    """Sanitize error message: mask sk-xxx API keys and key/secret URL params."""
    msg = re.sub(r"sk-[a-zA-Z0-9]{8,}", "sk-***", msg)
    msg = re.sub(
        r"([?&](?:api[_-]?key|key|secret|token)=)[^&\s]+",
        r"\1***",
        msg,
        flags=re.IGNORECASE,
    )
    return msg


class ToolNotFoundError(Exception):
    """请求了未注册的工具时抛出。"""

    def __init__(self, tool: str):
        self.tool = tool
        super().__init__(f"工具 '{tool}' 不存在")


@runtime_checkable
class ToolHandler(Protocol):
    """工具处理器协议。"""

    name: str

    async def run(self, args: dict) -> dict:
        """执行工具并返回 {output: str, duration_ms: int}。"""
        ...


# 工具名 -> 处理器元信息 {"fn": handler, "param_schema": ..., "timeout_ms": ...}
_REGISTRY: dict[str, dict[str, Any]] = {}


HandlerType = Union[ToolHandler, Type[ToolHandler]]


def _instantiate(handler: HandlerType) -> ToolHandler:
    """如果是类则实例化，否则直接返回实例。"""
    if isinstance(handler, type):
        return handler()
    return handler


def _get_name(handler: HandlerType) -> str:
    """从处理器或其类中获取默认工具名。"""
    if isinstance(handler, type):
        return getattr(handler, "name", handler.__name__)
    return getattr(handler, "name", handler.__class__.__name__)


def register(
    name_or_handler: Union[str, HandlerType, None] = None,
    handler: HandlerType | None = None,
    *,
    param_schema: dict | None = None,
    timeout_ms: int = 30000,
) -> Any:
    """
    注册工具处理器。

    支持三种用法：
        1. @register                 # 以类/实例默认 name 注册
        2. @register("custom_name")  # 指定名称注册类
        3. register("name", handler) # 函数式注册

    额外关键字参数：
        param_schema: JSON Schema 格式的参数校验定义
        timeout_ms: 工具执行超时时间（毫秒），默认 30000
    """
    def _make_entry(h: ToolHandler) -> dict[str, Any]:
        schema = param_schema or getattr(h, "param_schema", None)
        timeout = timeout_ms or getattr(h, "timeout_ms", 30000)
        return {
            "fn": h,
            "param_schema": schema,
            "timeout_ms": timeout,
        }

    # 用法 3：register("name", handler)
    if isinstance(name_or_handler, str) and handler is not None:
        h = _instantiate(handler)
        _REGISTRY[name_or_handler] = _make_entry(h)
        logger.info("工具注册: %s", name_or_handler)
        return handler

    # 用法 1：@register（直接装饰类或实例）
    if callable(name_or_handler) and not isinstance(name_or_handler, str):
        h = _instantiate(name_or_handler)
        _REGISTRY[_get_name(h)] = _make_entry(h)
        logger.info("工具注册: %s", _get_name(h))
        return name_or_handler

    # 用法 2：@register("custom_name")
    if isinstance(name_or_handler, str):
        name = name_or_handler

        def decorator(h: HandlerType) -> HandlerType:
            inst = _instantiate(h)
            _REGISTRY[name] = _make_entry(inst)
            return h

        return decorator

    raise ValueError("register 参数不合法")


async def dispatch(tool: str, args: dict) -> dict:
    """
    调度指定工具执行，包含参数 Schema 校验和超时控制。

    Returns:
        {"output": str, "duration_ms": int} 或含 success/error 的错误响应

    Raises:
        ToolNotFoundError: 工具未注册时抛出。
    """
    entry = _REGISTRY.get(tool)
    if entry is None:
        logger.warning("工具调度失败: %s 未注册", tool)
        raise ToolNotFoundError(tool)

    handler = entry["fn"]

    # 参数 Schema 校验
    schema = entry.get("param_schema")
    if schema:
        required = schema.get("required", [])
        for field in required:
            if field not in args:
                return {
                    "success": False,
                    "error": {"code": "INVALID_ARGS", "message": f"缺少必填字段: {field}"},
                }

    # 超时控制
    timeout_ms = entry.get("timeout_ms", 30000)
    start = time.perf_counter()
    try:
        logger.info("工具调度: %s args=%s timeout=%dms", tool, args, timeout_ms)
        # 兼容两种 handler 形式：
        #   1. ToolHandler 实例（有 .run 方法）— @register 装饰的类
        #   2. 可调用对象（async function）— register_mcp_tools 注册的闭包
        if hasattr(handler, "run"):
            result = await asyncio.wait_for(handler.run(args), timeout=timeout_ms / 1000)
        elif asyncio.iscoroutinefunction(handler):
            result = await asyncio.wait_for(handler(args), timeout=timeout_ms / 1000)
        else:
            return {
                "success": False,
                "error": {"code": "INVALID_HANDLER", "message": f"工具 {tool} handler 类型不合法"},
            }
    except asyncio.TimeoutError:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "success": False,
            "error": {"code": "TIMEOUT", "message": f"工具 {tool} 执行超时 ({timeout_ms}ms)"},
            "duration_ms": duration_ms,
        }
    except Exception as e:
        # P1: single tool failure should not crash the whole agent
        logger.exception("工具 %s 执行异常", tool)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "success": False,
            "error": {"code": "TOOL_ERROR", "message": _sanitize_error(str(e))},
            "duration_ms": duration_ms,
        }

    duration_ms = int((time.perf_counter() - start) * 1000)
    if not isinstance(result, dict):
        result = {"output": str(result), "duration_ms": duration_ms}
    else:
        result.setdefault("duration_ms", duration_ms)
    return result


# ═══════════════════════════════════════════
# 内置 stub 工具
# ═══════════════════════════════════════════


@register
class AuditPinsTool:
    name = "audit_pins"

    async def run(self, args: dict) -> dict:
        return {
            "output": "引脚冲突审计完成：未发现冲突（stub）。",
            "duration_ms": 0,
        }


@register
class WiringTool:
    name = "wiring"

    async def run(self, args: dict) -> dict:
        return {
            "output": "接线图生成完成（stub）。",
            "duration_ms": 0,
        }


@register
class BuildTool:
    name = "build"

    async def run(self, args: dict) -> dict:
        return {
            "output": "固件编译完成（stub）。",
            "duration_ms": 0,
        }


@register
class UploadTool:
    name = "upload"

    async def run(self, args: dict) -> dict:
        return {
            "output": "固件烧录完成（stub）。",
            "duration_ms": 0,
        }


@register
class SearchDocsTool:
    name = "search_docs"

    async def run(self, args: dict) -> dict:
        query = args.get("query", "")
        top_k = args.get("top_k", 5)
        return {
            "output": f"文档检索完成：query={query}, top_k={top_k}（stub）。",
            "duration_ms": 0,
        }


@register("code_executor", param_schema={
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "要执行的代码"},
        "language": {"type": "string", "description": "编程语言", "enum": ["python", "c", "cpp", "javascript"]},
    },
    "required": ["code", "language"],
}, timeout_ms=15000)
class CodeExecutorTool:
    """在隔离沙箱中执行代码并返回结果"""
    name = "code_executor"
    description = "在隔离沙箱中执行代码并返回结果"

    async def run(self, args: dict) -> dict:
        from src.sandbox import execute_code
        code = args.get("code", "")
        language = args.get("language", "python")
        result = await execute_code(code, language)
        return {
            "output": result.stdout if result.exit_code == 0 else result.stderr,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
        }


# ═══════════════════════════════════════════
# MCP 工具动态注册/注销
# ═══════════════════════════════════════════


def register_mcp_tools(server_id: str, tools: list):
    logger.info("注册 MCP 工具: server=%s count=%d", server_id, len(tools))
    """将 MCP Server 发现的工具注册到 _REGISTRY"""
    for tool in tools:
        tool_name = f"mcp_{server_id}_{tool.name}"
        # 创建闭包捕获 server_id 和 tool.name
        def make_handler(sid, tname):
            async def handler(args: dict) -> dict:
                from src.mcp.manager import get_mcp_manager
                client = get_mcp_manager().get_client(sid)
                if not client:
                    return {"error": f"MCP Server {sid} 未连接"}
                result = await client.call_tool(tname, args)
                return result.get("output", "")
            return handler

        _REGISTRY[tool_name] = {
            "fn": make_handler(server_id, tool.name),
            "description": tool.description,
            "param_schema": tool.input_schema,
            "timeout_ms": 30000,
            "source": "mcp",
            "server_id": server_id,
        }


def unregister_mcp_tools(server_id: str):
    logger.info("注销 MCP 工具: server=%s", server_id)
    """从 _REGISTRY 注销 MCP Server 的工具"""
    to_remove = [k for k, v in _REGISTRY.items() if isinstance(v, dict) and v.get("server_id") == server_id]
    for k in to_remove:
        _REGISTRY.pop(k, None)
