"""Tool 路由 — /api/tool + /api/tools + WS /api/monitor/{port}"""

import json
import logging
import asyncio
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.agent.core.toolkit.tool_router import ToolRouter, list_registered_tools
from src.agent.exceptions import ToolContext
from app.api.dependencies import current_user, ws_auth
from app.api.errors import sanitize_error
from app.api.locks import get_port_lock

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

    Delegates to the new ToolRouter.dispatch, which returns a
    ToolResultEnvelope dict (always a dict — tool-not-found is an envelope
    with success=False, not an exception). A default ToolContext is used
    since this REST path is outside the Agent SSE flow.

    Auto-registers all tools when registry is empty so the endpoint works
    standalone without a prior /api/chat Agent request.
    """
    _ensure_tools()
    ctx = ToolContext()
    call_id = uuid.uuid4().hex
    try:
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
async def list_tools(user: dict = Depends(current_user)):
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
    lock = get_port_lock(port)
    async with lock:
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

            ser = serial.Serial(port, baudrate=baud, timeout=1)
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
                while True:
                    try:
                        if ser.in_waiting:
                            data = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                            if data:
                                await websocket.send_text(json.dumps({"type": "data", "payload": data}))
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": f"串口读取异常: {e}"}))
                        except Exception as exc:
                            logger.debug("send error event failed: %s", exc)
                        logger.warning("Serial read error on %s: %s", port, e)
                        # 不关闭 WS — 芯片复位期间串口报错是正常的
                        continue

            read_task = asyncio.create_task(_read_serial())
            try:
                while True:
                    data = await websocket.receive_text()
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
