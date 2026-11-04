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

from app.api import build_registry, build_routes
from app.api.dependencies import require_session
from app.main import create_app

TEST_TOKEN = "test-session-token"


def _register_build(binary_path: str = ".build/tmp/abc/.pio/build/esp32s3/firmware.bin") -> str:
    """Create a server-side build record for upload tests."""
    return build_registry.register_build(
        binary_path,
        TEST_TOKEN,
        board="esp32-s3",
        platform="esp32",
        framework="arduino",
        lib_deps=(),
    )


# ═══════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with cwd pinned to tmp_path so startup cleanup is a no-op."""
    monkeypatch.chdir(tmp_path)
    with patch.object(build_routes, "cleanup_old_builds", AsyncMock(return_value=0)):
        c = TestClient(create_app())
        c.app.dependency_overrides[require_session] = lambda: TEST_TOKEN
        yield c


@pytest.fixture(autouse=True)
def _clear_upload_locks():
    """Ensure _port_locks dict is clean before and after every test."""
    from app.api import locks
    locks._port_locks.clear()
    locks._uploading_ports.clear()
    locks._active_serial.clear()
    build_registry.clear_builds()
    yield
    locks._port_locks.clear()
    locks._uploading_ports.clear()
    locks._active_serial.clear()
    build_registry.clear_builds()


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
                json={"code": "void setup(){} void loop(){}", "board": "esp32-s3", "confirm": True},
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
        assert done["build_id"]

    def test_build_endpoint_failed(self, client):
        """POST /api/build with failing compile yields done(success=False, COMPILE_FAILED)."""
        with patch.object(build_routes, "pio_compile_firmware", _fake_compile_failed):
            response = client.post(
                "/api/build",
                json={"code": "broken code", "board": "esp32-s3", "confirm": True},
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
        """POST /api/upload with a server build id calls upload only."""
        calls = {"compile": 0, "upload": 0}
        build_id = _register_build()

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
                json={"build_id": build_id, "port": "COM3", "confirm": True},
            )

        assert response.status_code == 200
        assert calls["compile"] == 0
        assert calls["upload"] == 1

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert types[0] == "thinking"
        assert types[-1] == "done"
        assert events[-1]["success"] is True

    def test_upload_rejects_client_binary_path_and_code(self, client):
        """Upload no longer accepts client-supplied paths or source code."""
        response = client.post(
            "/api/upload",
            json={
                "binary_path": ".build/tmp/abc/.pio/build/esp32s3/firmware.bin",
                "code": "void setup(){} void loop(){}",
                "port": "COM3",
                "confirm": True,
            },
        )

        assert response.status_code == 422

    def test_upload_rejects_missing_build_id(self, client):
        """A client cannot start an upload without a server-issued build id."""
        response = client.post(
            "/api/upload",
            json={"port": "COM3", "confirm": True},
        )

        assert response.status_code == 422

    def test_upload_unknown_build_id_returns_error_sse(self, client):
        """An unknown build id yields a BUILD_NOT_FOUND done event."""
        response = client.post(
            "/api/upload",
            json={"build_id": "unknown", "port": "COM3", "confirm": True},
        )

        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert events[-1]["error"]["code"] == "BUILD_NOT_FOUND"

    def test_upload_port_busy(self, client):
        """A concurrent upload reservation returns PORT_BUSY."""
        from app.api import locks
        build_id = _register_build()
        assert locks.reserve_port_for_upload("COM3")

        try:
            response = client.post(
                "/api/upload",
                json={"build_id": build_id, "port": "COM3", "confirm": True},
            )
        finally:
            locks.release_port_after_upload("COM3")

        assert response.status_code == 200
        events = _parse_sse(response.text)
        done = events[-1]
        assert done["type"] == "done"
        assert done["success"] is False
        assert done["error"]["code"] == "PORT_BUSY"
