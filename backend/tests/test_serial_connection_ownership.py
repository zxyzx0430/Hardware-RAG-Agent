"""Regression tests for serial WebSocket takeover ownership."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api import tool_routes
from app.api.locks import (
    get_active_serial,
    get_port_lock,
    is_port_uploading,
    register_serial,
    release_port_after_upload,
    reserve_port_for_upload,
    unregister_serial,
    update_serial_obj,
)


class FakeWebSocket:
    def __init__(self, disconnect_on_receive: bool = False) -> None:
        self.disconnect_on_receive = disconnect_on_receive
        self.close_args = None
        self.messages: list[str] = []

    async def accept(self) -> None:
        return None

    async def close(self, **kwargs) -> None:
        self.close_args = kwargs

    async def send_text(self, message: str) -> None:
        self.messages.append(message)

    async def receive_text(self) -> str:
        if self.disconnect_on_receive:
            raise WebSocketDisconnect(code=1000)
        return '{"type":"start"}'


class FakeSerial:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_old_connection_cannot_unregister_or_replace_new_connection() -> None:
    port = "TEST-OWNERSHIP"
    old_ws, new_ws = FakeWebSocket(), FakeWebSocket()
    old_serial, new_serial = FakeSerial(), FakeSerial()
    register_serial(port, old_ws, old_serial)
    try:
        register_serial(port, new_ws)
        await asyncio.sleep(0)
        assert old_serial.closed
        update_serial_obj(port, old_serial, old_ws)
        update_serial_obj(port, new_serial, new_ws)
        unregister_serial(port, old_ws)
        assert get_active_serial(port) == {"ws": new_ws, "ser": new_serial}
    finally:
        unregister_serial(port, new_ws)


@pytest.mark.asyncio
async def test_monitor_waits_for_shared_port_lock_before_opening_serial(monkeypatch) -> None:
    import serial
    from serial.tools import list_ports

    port = "TEST-MONITOR-LOCK"
    lock = get_port_lock(port)
    await lock.acquire()
    ws = FakeWebSocket(disconnect_on_receive=True)
    opened: list[FakeSerial] = []

    class ConnectedSerial(FakeSerial):
        dtr = True
        rts = True
        in_waiting = 0

        def read(self, _size: int) -> bytes:
            return b""

    monkeypatch.setattr(tool_routes, "ws_auth", lambda _websocket: {})
    monkeypatch.setattr(serial, "Serial", lambda *_args, **_kwargs: opened.append(ConnectedSerial()) or opened[-1])
    monkeypatch.setattr(list_ports, "comports", lambda: [SimpleNamespace(device=port)])

    task = asyncio.create_task(tool_routes.serial_monitor(ws, port))
    try:
        await asyncio.sleep(0)
        assert opened == []
        assert get_active_serial(port) == {"ws": ws, "ser": None}
    finally:
        lock.release()

    await asyncio.wait_for(task, timeout=2)

    assert len(opened) == 1
    assert opened[0].closed is True
    assert get_active_serial(port) is None
    assert lock.locked() is False


@pytest.mark.asyncio
async def test_monitor_is_rejected_while_upload_owns_port(monkeypatch) -> None:
    port = "TEST-MONITOR-UPLOAD-BUSY"
    ws = FakeWebSocket()
    assert reserve_port_for_upload(port)
    monkeypatch.setattr(tool_routes, "ws_auth", lambda _websocket: {})

    try:
        await tool_routes.serial_monitor(ws, port)
    finally:
        release_port_after_upload(port)

    assert is_port_uploading(port) is False
    assert json.loads(ws.messages[0])["message"] == f"端口 {port} 正在烧录，请稍后重新连接"
    assert ws.close_args == {"code": 4007, "reason": "端口正在烧录"}
