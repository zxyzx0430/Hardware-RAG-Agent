"""Tool 路由 — /api/tool + /api/tools + WS /api/monitor/{port}"""

import json
import logging
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel

from src.agent.tool_router import dispatch, ToolNotFoundError, _REGISTRY as TOOL_REGISTRY
from app.api.dependencies import current_user
from app.api.common import sanitize_error, get_port_lock

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
async def call_tool(payload: ToolRequest):
    """调用工具。"""
    try:
        result = await dispatch(payload.tool, payload.args)
        return {"success": True, "data": result}
    except ToolNotFoundError:
        return {
            "success": False,
            "error": {"code": "TOOL_NOT_FOUND", "message": f"工具不存在: {payload.tool}", "details": None},
        }
    except Exception as e:
        logger.exception(f"工具调用失败: {payload.tool}")
        return {
            "success": False,
            "error": {"code": "TOOL_ERROR", "message": sanitize_error(str(e)), "details": sanitize_error(str(e))},
        }


# ═══════════════════════════════════════════
# GET /api/tools — 工具列表
# ═══════════════════════════════════════════

@router.get("/tools")
async def list_tools():
    """返回当前已注册的工具列表。"""
    tools = []
    for name, func in TOOL_REGISTRY.items():
        doc = (func.__doc__ or "").strip()
        tools.append({"name": name, "description": doc})
    return {"success": True, "data": {"tools": tools}}


# ═══════════════════════════════════════════
# WS /api/monitor/{port} — 串口监视器
# ═══════════════════════════════════════════

@router.websocket("/monitor/{port}")
async def serial_monitor(websocket: WebSocket, port: str, baud: int = 115200):
    """WebSocket 串口监视器（双向桥接）。"""
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
            await websocket.send_text(json.dumps({
                "type": "sys",
                "payload": f"串口已连接: {port} @ {baud} baud",
            }))

            async def _read_serial():
                try:
                    while True:
                        if ser.in_waiting:
                            data = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                            if data:
                                await websocket.send_text(json.dumps({"type": "data", "payload": data}))
                        await asyncio.sleep(0.05)
                except Exception:
                    pass

            async def _heartbeat():
                try:
                    while True:
                        await asyncio.sleep(30)
                        await asyncio.wait_for(websocket.ping(), timeout=10.0)
                except Exception:
                    pass

            read_task = asyncio.create_task(_read_serial())
            heartbeat_task = asyncio.create_task(_heartbeat())
            try:
                while True:
                    data = await websocket.receive_text()
                    msg = json.loads(data)
                    if msg.get("type") == "write":
                        ser.write(msg.get("payload", "").encode("utf-8"))
                    elif msg.get("type") == "start":
                        pass
            except WebSocketDisconnect:
                logger.info(f"串口 WS 断开: {port}")
            finally:
                read_task.cancel()
                heartbeat_task.cancel()
                try:
                    await read_task
                except asyncio.CancelledError:
                    pass
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        except serial.SerialException as e:
            logger.error(f"串口打开失败 {port}: {e}")
            try:
                await websocket.send_text(json.dumps({
                    "type": "error", "message": f"串口打开失败: {sanitize_error(str(e))}",
                }))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"串口监视器异常: {e}")
            try:
                await websocket.send_text(json.dumps({
                    "type": "error", "message": f"串口异常: {sanitize_error(str(e))}",
                }))
            except Exception:
                pass
        finally:
            if ser:
                try:
                    ser.close()
                except Exception:
                    pass
