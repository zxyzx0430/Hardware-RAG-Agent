"""并发锁原语 — 串口与接线图访问互斥。"""

import asyncio
from typing import Optional


# ─── 串口锁 ────────────────────────────────────
_port_locks: dict[str, asyncio.Lock] = {}
_uploading_ports: set[str] = set()

def get_port_lock(port: str) -> asyncio.Lock:
    """获取或创建串口锁。"""
    if port not in _port_locks:
        _port_locks[port] = asyncio.Lock()
    return _port_locks[port]


def reserve_port_for_upload(port: str) -> bool:
    """Reserve a port for one upload and prevent new monitor connections."""
    if port in _uploading_ports:
        return False
    _uploading_ports.add(port)
    return True


def release_port_after_upload(port: str) -> None:
    """Release the upload reservation so the serial monitor can reconnect."""
    _uploading_ports.discard(port)


def is_port_uploading(port: str) -> bool:
    """Return whether firmware upload currently owns the port."""
    return port in _uploading_ports


# ─── 串口连接跟踪 ────────────────────────────────
# 跟踪每个端口的活跃 WebSocket + serial 对象，新连接时可强制关闭旧的
_active_serial: dict[str, dict] = {}  # port -> {"ws": WebSocket, "ser": serial.Serial}


def register_serial(port: str, ws, ser=None) -> None:
    """注册串口连接。如果该端口已有旧连接，直接关闭旧 ser 和旧 WS。

    直接 close 旧 ser 而非依赖旧 handler 的 finally，避免 WS 断开检测延迟导致端口被占。
    """
    old = _active_serial.get(port)
    if old:
        old_ser = old.get("ser")
        if old_ser:
            try:
                old_ser.close()
            except Exception:
                pass
        old_ws = old.get("ws")
        if old_ws:
            try:
                asyncio.ensure_future(old_ws.close(code=4006, reason="被新连接抢占"))
            except Exception:
                pass
    _active_serial[port] = {"ws": ws, "ser": ser}


def update_serial_obj(port: str, ser, ws) -> None:
    """更新端口对应的 serial 对象（打开串口后调用）。"""
    if _active_serial.get(port, {}).get("ws") is ws:
        _active_serial[port]["ser"] = ser


def unregister_serial(port: str, ws) -> None:
    """注销串口连接。"""
    if _active_serial.get(port, {}).get("ws") is ws:
        _active_serial.pop(port, None)


def get_active_serial(port: str) -> Optional[dict]:
    """获取端口的活跃连接信息。"""
    return _active_serial.get(port)


async def disconnect_active_serial(port: str, reason: str) -> bool:
    """Close the active serial monitor before an upload takes the port."""
    active = _active_serial.get(port)
    if not active:
        return False

    ser = active.get("ser")
    if ser:
        try:
            ser.close()
        except Exception:
            pass

    ws = active.get("ws")
    if ws:
        try:
            await ws.close(code=4007, reason=reason)
        except Exception:
            pass
        unregister_serial(port, ws)
    return True


# ─── 接线图锁 ───────────────────────────────────
wiring_lock = asyncio.Lock()
