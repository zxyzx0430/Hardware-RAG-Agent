"""Tool 路由 — /api/tool + /api/tools + WS /api/monitor/{port}"""

import json
import logging
import asyncio
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.agent.core.toolkit.permission_classifier import (
    ALLOW,
    ASK,
    DENY,
    PermissionClassifier,
)
from src.agent.core.toolkit.tool_router import ToolRouter, list_registered_tools
from src.agent.core.toolkit.tool_result_envelope import (
    ErrorDetail,
    ResultMetadata,
    ToolResultEnvelope,
)
from src.agent.exceptions import ToolContext
from app.api.dependencies import current_user, current_user_optional, ws_auth
from app.api.errors import sanitize_error
from app.api.locks import (
    get_active_serial,
    get_port_lock,
    is_port_uploading,
    register_serial,
    update_serial_obj,
    unregister_serial,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ═══════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════

class ToolRequest(BaseModel):
    tool: str
    args: dict = {}


# ═══════════════════════════════════════════
# POST /api/tool — 工具调用
# ═══════════════════════════════════════════

@router.post("/tool")
async def call_tool(payload: ToolRequest, user: dict = Depends(current_user)):
    """调用工具。

    Tools requiring interactive confirmation are refused here because this
    endpoint has no HITL resume flow. Low-risk calls still use ToolRouter.

    Auto-registers all tools when registry is empty so the endpoint works
    standalone without a prior /api/chat Agent request.
    """
    _ensure_tools()
    ctx = ToolContext()
    call_id = uuid.uuid4().hex
    try:
        spec = next(
            (item for item in list_registered_tools() if item.name == payload.tool),
            None,
        )
        if spec is None:
            return _tool_not_found_rejection(
                payload.tool,
                call_id,
            )

        decision = PermissionClassifier().check(spec, payload.args, ctx)
        if decision in (ASK, DENY):
            return _permission_rejection(
                payload.tool,
                call_id,
                decision,
            )
        if decision != ALLOW:
            return _permission_rejection(payload.tool, call_id, DENY)

        return await ToolRouter.get_default().dispatch(
            call_id, payload.tool, payload.args, ctx,
            decision="allow", decision_source="auto_allow",
        )
    except Exception as e:
        logger.exception(f"工具调用失败: {payload.tool}")
        return {
            "success": False,
            "error": {"code": "TOOL_ERROR", "message": sanitize_error(str(e)), "details": sanitize_error(str(e))},
        }


def _permission_rejection(tool_name: str, call_id: str, decision: str) -> dict:
    """Return a stable envelope without dispatching tools that need HITL."""
    if decision == ASK:
        error_type = "PERMISSION_CONFIRMATION_REQUIRED"
        message = f"tool '{tool_name}' requires interactive confirmation"
        suggestion = "请在聊天流程中确认此操作；直接 API 调用不会执行需要确认的工具。"
    else:
        error_type = "PERMISSION_DENIED"
        message = f"tool '{tool_name}' was denied by the permission policy"
        suggestion = "请检查工具参数与路径权限后重试。"
    return ToolResultEnvelope(
        success=False,
        output="",
        data=None,
        error=ErrorDetail(
            error_type=error_type,
            error_message=message,
            suggestion=suggestion,
            retryable=False,
        ),
        metadata=ResultMetadata(
            tool_name=tool_name,
            duration_ms=0,
            call_id=call_id,
        ),
    ).model_dump()


def _tool_not_found_rejection(tool_name: str, call_id: str) -> dict:
    """Fail closed when no spec is available to classify for direct calls."""
    return ToolResultEnvelope(
        success=False,
        output="",
        data=None,
        error=ErrorDetail(
            error_type="TOOL_NOT_FOUND",
            error_message=f"tool '{tool_name}' is not registered",
            suggestion="请检查工具名和当前工具注册状态。",
            retryable=False,
        ),
        metadata=ResultMetadata(
            tool_name=tool_name,
            duration_ms=0,
            call_id=call_id,
        ),
    ).model_dump()


def _ensure_tools() -> None:
    """Lazy-register all tools when registry is empty (standalone mode)."""
    try:
        from src.agent.agent_factory import ensure_default_tools_registered
        ensure_default_tools_registered()
    except Exception as e:
        logger.warning("ensure_default_tools_registered failed: %s", e)


# ═══════════════════════════════════════════
# GET /api/tools — 工具元数据列表（26 个真实 LangChain 工具）
# ═══════════════════════════════════════════

@router.get("/tools")
async def list_tools(user: dict = Depends(current_user_optional)):
    """返回 26 个 Agent 工具的元数据（name/description/group/enabled）。

    工具元数据来源：agent_factory._build_tool_groups()，按 6 个 group 分组：
    retrieval(8) / hardware(2) / workbench(3) / code(2) / file_ops(9) / execution(2)。
    enabled 字段从 data/tool_config.json 读取（文件不存在则全部为 true）。
    """
    try:
        from src.agent.agent_factory import list_tool_metadata
        tools = list_tool_metadata()
        return {"success": True, "data": {"tools": tools, "total": len(tools)}}
    except Exception as e:
        logger.exception("list_tool_metadata failed")
        return {
            "success": False,
            "error": {"code": "TOOLS_LIST_ERROR", "message": sanitize_error(str(e))},
        }


# ═══════════════════════════════════════════
# PATCH /api/tools/{tool_name}/toggle — 切换工具启用状态
# ═══════════════════════════════════════════

@router.patch("/tools/{tool_name}/toggle")
async def toggle_tool(tool_name: str, user: dict = Depends(current_user)):
    """切换工具启用状态，持久化到 data/tool_config.json。

    文件结构：`{"disabled": ["tool_name_1", "tool_name_2"]}`（用 disabled 列表，默认空）。
    禁用的工具不会注入到 Agent（agent_factory._assemble_all_tools 会过滤）。
    """
    try:
        from src.agent.agent_factory import toggle_tool_enabled
        new_state = toggle_tool_enabled(tool_name)
        if new_state is None:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "error": {
                        "code": "TOOL_NOT_FOUND",
                        "message": f"Tool not found: {tool_name}",
                    },
                },
            )
        return {"success": True, "data": {"name": tool_name, "enabled": new_state}}
    except Exception as e:
        logger.exception("toggle tool failed: %s", tool_name)
        return {
            "success": False,
            "error": {"code": "TOOL_TOGGLE_ERROR", "message": sanitize_error(str(e))},
        }


# ═══════════════════════════════════════════
# WS /api/monitor/{port} — 串口监视器
# ═══════════════════════════════════════════


@router.websocket("/monitor/{port}")
async def serial_monitor(websocket: WebSocket, port: str, baud: int = 115200):
    """WebSocket 串口监视器（双向桥接）。"""
    # 鉴权：accept 前校验 token，失败直接关闭连接（ws_auth 返回 None 表示未授权）
    if ws_auth(websocket) is None:
        await websocket.close(code=4401, reason="未授权")
        return
    await websocket.accept()

    if is_port_uploading(port):
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"端口 {port} 正在烧录，请稍后重新连接",
        }))
        await websocket.close(code=4007, reason="端口正在烧录")
        return

    # 注册当前 WS，如果该端口已有旧连接，register_serial 会关闭旧 WS
    # 旧 WS 关闭后 → 旧 handler 的 receive_text() 抛 WebSocketDisconnect → 旧 finally 关闭 ser → 释放端口
    register_serial(port, websocket)
    try:
        async with get_port_lock(port):
            active = get_active_serial(port)
            if is_port_uploading(port):
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"端口 {port} 正在烧录，请稍后重新连接",
                }))
                await websocket.close(code=4007, reason="端口正在烧录")
                return
            if not active or active.get("ws") is not websocket:
                await websocket.close(code=4006, reason="连接已被新连接替换")
                return
            await _serial_monitor_session(websocket, port, baud)
    finally:
        unregister_serial(port, websocket)


async def _serial_monitor_session(websocket: WebSocket, port: str, baud: int) -> None:
    """Own the serial device while holding the per-port lock."""
    ser = None
    try:
        import serial
        from serial.tools import list_ports
        available_ports = [p.device for p in list_ports.comports()]
        if port not in available_ports:
            await websocket.send_text(json.dumps({
                "type": "sys",
                "payload": f"端口 {port} 不存在或不可用。可用端口: {', '.join(available_ports) or '无'}",
            }))
            await websocket.close(code=4004, reason="端口不可用")
            return

        # 重试打开串口：旧 WS 连接或残留 esptool 可能还在占用端口
        open_retries = 5
        for attempt in range(open_retries):
            try:
                ser = serial.Serial(port, baudrate=baud, timeout=1)
                break
            except (serial.SerialException, PermissionError) as open_err:
                err_str = str(open_err)
                is_permission = "PermissionError" in err_str or "拒绝访问" in err_str or isinstance(open_err, PermissionError)
                if attempt < open_retries - 1 and is_permission:
                    logger.warning("串口 %s 打开失败(第%d次)，等待端口释放后重试: %s", port, attempt + 1, err_str)
                    await asyncio.sleep(0.8)
                else:
                    raise

        update_serial_obj(port, ser, websocket)

        # 1) 打开后 DTR=True, RTS=True → EN LOW → 芯片保持在复位状态
        # 2) 设 DTR=False → IO0 HIGH（正常启动模式）
        # 3) 设 RTS=False → EN HIGH → 释放复位，芯片以正常模式启动
        ser.dtr = False
        await asyncio.sleep(0.1)
        ser.rts = False
        await websocket.send_text(json.dumps({
            "type": "sys",
            "payload": f"串口已连接: {port} @ {baud} baud",
        }))

        async def _read_serial():
            consecutive_errors = 0
            MAX_CONSECUTIVE_ERRORS = 10
            while True:
                try:
                    if ser.in_waiting:
                        data = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                        if data:
                            await websocket.send_text(json.dumps({"type": "data", "payload": data}))
                    await asyncio.sleep(0.05)
                    consecutive_errors = 0  # 成功读取，重置计数
                except Exception as e:
                    consecutive_errors += 1
                    try:
                        await websocket.send_text(json.dumps({"type": "error", "message": f"串口读取异常: {e}"}))
                    except Exception as exc:
                        logger.debug("send error event failed: %s", exc)
                    logger.warning("Serial read error on %s (consecutive=%d): %s", port, consecutive_errors, e)
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        logger.error("串口 %s 连续 %d 次读取失败，判定设备已断开，关闭连接", port, consecutive_errors)
                        try:
                            await websocket.close(code=4005, reason="串口设备持续异常")
                        except Exception:
                            pass
                        break
                    await asyncio.sleep(0.2)

        read_task = asyncio.create_task(_read_serial())
        try:
            while True:
                # 30s 超时：超时后发心跳检测连接是否存活，
                # 如果 WS 已断开，send 会抛异常退出循环
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    try:
                        await websocket.send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        break
                    continue
                msg = json.loads(data)
                if msg.get("type") == "write":
                    ser.write(msg.get("payload", "").encode("utf-8"))
                elif msg.get("type") == "start":
                    pass
                elif msg.get("type") == "set_dtr":
                    try:
                        ser.dtr = bool(msg.get("payload", False))
                        logger.info("DTR set to %s on port %s", msg.get("payload"), port)
                    except Exception as e:
                        logger.warning("set_dtr failed: %s", e)
                        await websocket.send_text(json.dumps({"type": "error", "message": f"DTR 设置失败: {e}"}))

                elif msg.get("type") == "set_rts":
                    try:
                        ser.rts = bool(msg.get("payload", False))
                        logger.info("RTS set to %s on port %s", msg.get("payload"), port)
                    except Exception as e:
                        logger.warning("set_rts failed: %s", e)
                        await websocket.send_text(json.dumps({"type": "error", "message": f"RTS 设置失败: {e}"}))

        except WebSocketDisconnect:
            logger.info(f"串口 WS 断开: {port}")
        finally:
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass

    except serial.SerialException as e:
        logger.error(f"串口打开失败 {port}: {e}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error", "message": f"串口打开失败: {sanitize_error(str(e))}",
            }))
        except Exception as exc:
            logger.debug("send serial open error failed: %s", exc)
        await websocket.close(code=4003, reason="串口打开失败")
    except Exception as e:
        logger.error(f"串口监视器异常: {e}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error", "message": f"串口异常: {sanitize_error(str(e))}",
            }))
        except Exception as exc:
            logger.debug("send serial monitor error failed: %s", exc)
        await websocket.close(code=4000, reason="串口监视器异常")
    finally:
        if ser:
            try:
                ser.close()
            except Exception as exc:
                logger.debug("close serial failed: %s", exc)
