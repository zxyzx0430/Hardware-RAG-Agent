"""Integration tests for app.api.build_routes.

Uses FastAPI TestClient + mocked pio_runner functions to verify the SSE event
flow for /api/build and /api/upload without invoking real PlatformIO.

Covers:
- build_endpoint_success / build_endpoint_failed
- upload_with_binary_path / upload_with_code_only
- upload_invalid_args / upload_port_busy
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import build_routes
from app.main import create_app


# ═══════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with cwd pinned to tmp_path so startup cleanup is a no-op."""
    monkeypatch.chdir(tmp_path)
    with patch.object(build_routes, "cleanup_old_builds", AsyncMock(return_value=0)):
        c = TestClient(create_app())
        yield c


@pytest.fixture(autouse=True)
def _clear_upload_locks():
    """Ensure _port_locks dict is clean before and after every test."""
    from app.api import locks
    locks._port_locks.clear()
    locks._uploading_ports.clear()
    locks._active_serial.clear()
    yield
    locks._port_locks.clear()
    locks._uploading_ports.clear()
    locks._active_serial.clear()


def _parse_sse(response_text: str) -> list[dict]:
    """Parse SSE response text into a list of event dicts."""
    events = []
    for block in response_text.strip().split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: "):
                payload = line[len("data: "):]
                events.append(json.loads(payload))
    return events


# ═══════════════════════════════════════════
# Fake pio_runner async generators
# ═══════════════════════════════════════════


async def _fake_compile_success(req):
    yield {"type": "thinking", "content": "正在编译固件...", "source": "build"}
    yield {"type": "progress", "percent": 5, "message": "已创建临时项目"}
    yield {"type": "compile_log", "line": "Compiling...", "stream": "stdout"}
    yield {
        "type": "done",
        "success": True,
        "binary_path": ".build/tmp/abc/.pio/build/esp32s3/firmware.bin",
    }


async def _fake_compile_failed(req):
    yield {"type": "thinking", "content": "正在编译固件...", "source": "build"}
    yield {"type": "compile_log", "line": "error: 'foo' undeclared", "stream": "stdout"}
    yield {
        "type": "done",
        "success": False,
        "error": {"code": "COMPILE_FAILED", "message": "编译失败", "details": "..."},
    }


async def _fake_upload_success(req):
    yield {"type": "thinking", "content": "正在烧录固件...", "source": "flash"}
    yield {"type": "progress", "percent": 10, "message": "开始烧录"}
    yield {"type": "done", "success": True, "message": "烧录完成"}


# ═══════════════════════════════════════════
# /api/build
# ═══════════════════════════════════════════


class TestBuildEndpoint:
    def test_build_endpoint_success(self, client):
        """POST /api/build returns SSE stream ending in done(success=True)."""
        with patch.object(build_routes, "pio_compile_firmware", _fake_compile_success):
            response = client.post(
                "/api/build",
                json={"code": "void setup(){} void loop(){}", "board": "esp32-s3"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert types[0] == "thinking"
        assert "progress" in types
        assert "compile_log" in types
        assert types[-1] == "done"

        done = events[-1]
        assert done["success"] is True
        assert done["binary_path"].endswith("firmware.bin")

    def test_build_endpoint_failed(self, client):
        """POST /api/build with failing compile yields done(success=False, COMPILE_FAILED)."""
        with patch.object(build_routes, "pio_compile_firmware", _fake_compile_failed):
            response = client.post(
                "/api/build",
                json={"code": "broken code", "board": "esp32-s3"},
            )

        assert response.status_code == 200
        events = _parse_sse(response.text)
        done = events[-1]
        assert done["type"] == "done"
        assert done["success"] is False
        assert done["error"]["code"] == "COMPILE_FAILED"


# ═══════════════════════════════════════════
# /api/upload
# ═══════════════════════════════════════════


class TestUploadEndpoint:
    @pytest.mark.asyncio
    async def test_upload_disconnects_monitor_before_using_port(self, monkeypatch):
        """An upload closes the monitor and owns the shared port lock."""
        from app.api import locks

        port = "TEST-UPLOAD-HANDOFF"

        class FakeWebSocket:
            close_args = None

            async def close(self, **kwargs):
                self.close_args = kwargs

        class FakeSerial:
            closed = False

            def close(self):
                self.closed = True

        ws = FakeWebSocket()
        ser = FakeSerial()
        locks.register_serial(port, ws, ser)
        observed = {}

        async def fake_upload_steps(_payload):
            observed["lock_held"] = locks.get_port_lock(port).locked()
            observed["upload_reserved"] = locks.is_port_uploading(port)
            yield "data: upload started\n\n"

        monkeypatch.setattr(build_routes, "_run_upload_steps", fake_upload_steps)
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        payload = build_routes.UploadRequest(binary_path="firmware.bin", port=port)

        events = [event async for event in build_routes._stream_upload(payload, request)]

        assert events == ["data: upload started\n\n"]
        assert ser.closed is True
        assert ws.close_args == {"code": 4007, "reason": "端口即将用于固件烧录"}
        assert observed == {"lock_held": True, "upload_reserved": True}
        assert locks.get_active_serial(port) is None
        assert locks.is_port_uploading(port) is False

    @pytest.mark.asyncio
    async def test_disconnected_upload_releases_port_and_reservation(self, monkeypatch):
        """An upload SSE disconnect cannot leave the port stuck as busy."""
        from app.api import locks

        port = "TEST-UPLOAD-DISCONNECT"

        async def fake_upload_steps(_payload):
            yield "data: upload started\n\n"

        monkeypatch.setattr(build_routes, "_run_upload_steps", fake_upload_steps)
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
        payload = build_routes.UploadRequest(binary_path="firmware.bin", port=port)

        events = [event async for event in build_routes._stream_upload(payload, request)]

        assert events == []
        assert locks.get_port_lock(port).locked() is False
        assert locks.is_port_uploading(port) is False

    @pytest.mark.asyncio
    async def test_upload_done_is_sent_after_releasing_port(self, monkeypatch):
        """The terminal SSE event must not race the monitor's reconnect."""
        from app.api import locks

        port = "TEST-UPLOAD-COMPLETE"

        async def fake_upload_steps(_payload):
            yield build_routes._sse_from_event({"type": "progress", "percent": 100})
            yield build_routes._sse_from_event({"type": "done", "success": True})

        monkeypatch.setattr(build_routes, "_run_upload_steps", fake_upload_steps)
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        payload = build_routes.UploadRequest(binary_path="firmware.bin", port=port)
        terminal_state = None

        async for event in build_routes._stream_upload(payload, request):
            if build_routes._is_done_sse_event(event):
                terminal_state = {
                    "lock_held": locks.get_port_lock(port).locked(),
                    "upload_reserved": locks.is_port_uploading(port),
                }

        assert terminal_state == {"lock_held": False, "upload_reserved": False}

    def test_upload_with_binary_path(self, client):
        """POST /api/upload with binary_path calls upload_firmware only (no compile)."""
        calls = {"compile": 0, "upload": 0}

        async def fake_compile(req):
            calls["compile"] += 1
            yield {}  # pragma: no cover — should not be called

        async def fake_upload(req):
            calls["upload"] += 1
            async for ev in _fake_upload_success(req):
                yield ev

        with patch.object(build_routes, "pio_compile_firmware", fake_compile), \
             patch.object(build_routes, "pio_upload_firmware", fake_upload):
            response = client.post(
                "/api/upload",
                json={
                    "binary_path": ".build/tmp/abc/.pio/build/esp32s3/firmware.bin",
                    "board": "esp32-s3",
                    "port": "COM3",
                },
            )

        assert response.status_code == 200
        assert calls["compile"] == 0
        assert calls["upload"] == 1

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert types[0] == "thinking"
        assert types[-1] == "done"
        assert events[-1]["success"] is True

    def test_upload_with_code_only(self, client):
        """POST /api/upload with only code first compiles then uploads."""
        calls = {"compile": 0, "upload": 0}

        async def fake_compile(req):
            calls["compile"] += 1
            async for ev in _fake_compile_success(req):
                yield ev

        async def fake_upload(req):
            calls["upload"] += 1
            async for ev in _fake_upload_success(req):
                yield ev

        with patch.object(build_routes, "pio_compile_firmware", fake_compile), \
             patch.object(build_routes, "pio_upload_firmware", fake_upload):
            response = client.post(
                "/api/upload",
                json={
                    "code": "void setup(){} void loop(){}",
                    "board": "esp32-s3",
                    "port": "COM3",
                },
            )

        assert response.status_code == 200
        assert calls["compile"] == 1
        assert calls["upload"] == 1

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        # Should have at least 2 thinking events (compile + upload) and end with done
        assert types.count("thinking") >= 2
        assert types.count("done") == 1
        assert any(e.get("content") == "编译成功，准备开始烧录..." for e in events)
        assert types[-1] == "done"
        assert events[-1]["success"] is True

    def test_upload_compile_failure_has_one_terminal_event(self, client):
        """A compile failure ends upload; successful compile itself is not terminal."""
        calls = {"upload": 0}

        async def fake_compile(req):
            async for event in _fake_compile_failed(req):
                yield event

        async def fake_upload(req):
            calls["upload"] += 1
            async for event in _fake_upload_success(req):
                yield event

        with patch.object(build_routes, "pio_compile_firmware", fake_compile), \
             patch.object(build_routes, "pio_upload_firmware", fake_upload):
            response = client.post(
                "/api/upload",
                json={"code": "broken code", "port": "COM3"},
            )

        events = _parse_sse(response.text)
        done_events = [event for event in events if event["type"] == "done"]
        assert response.status_code == 200
        assert calls["upload"] == 0
        assert len(done_events) == 1
        assert done_events[0]["success"] is False
        assert done_events[0]["error"]["code"] == "COMPILE_FAILED"

    def test_upload_invalid_args(self, client):
        """POST /api/upload with neither code nor binary_path returns INVALID_ARGS."""
        response = client.post(
            "/api/upload",
            json={"port": "COM3"},  # no code, no binary_path
        )

        assert response.status_code == 200
        events = _parse_sse(response.text)
        done = events[-1]
        assert done["type"] == "done"
        assert done["success"] is False
        assert done["error"]["code"] == "INVALID_ARGS"

    def test_upload_port_busy(self, client):
        """A concurrent upload reservation returns PORT_BUSY."""
        from app.api import locks
        assert locks.reserve_port_for_upload("COM3")

        try:
            response = client.post(
                "/api/upload",
                json={
                    "binary_path": ".build/tmp/abc/.pio/build/esp32s3/firmware.bin",
                    "board": "esp32-s3",
                    "port": "COM3",
                },
            )
        finally:
            locks.release_port_after_upload("COM3")

        assert response.status_code == 200
        events = _parse_sse(response.text)
        done = events[-1]
        assert done["type"] == "done"
        assert done["success"] is False
        assert done["error"]["code"] == "PORT_BUSY"
