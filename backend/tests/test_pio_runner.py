"""Unit tests for src.hardware.pio_runner.

Covers validation helpers, platformio.ini generation, temp project creation,
the compile_firmware async generator (success / failure / pio-not-installed /
unsupported-board), and cleanup_old_builds.

All filesystem touches go to pytest's tmp_path (via monkeypatch.chdir).
Subprocess + serial port enumeration are mocked.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.hardware.pio_runner import (
    BUILD_TMP_ROOT,
    CompileRequest,
    DEFAULT_UPLOAD_SPEED,
    ERR_COMPILE_FAILED,
    ERR_PIO_NOT_INSTALLED,
    ERR_UNSUPPORTED_BOARD,
    SUPPORTED_PLATFORMS,
    UploadRequest,
    _create_temp_project,
    _generate_platformio_ini,
    _resolve_env_name,
    _validate_binary_path,
    _validate_board,
    _validate_port,
    cleanup_old_builds,
    compile_firmware,
)


# ═══════════════════════════════════════════
# _validate_board
# ═══════════════════════════════════════════


class TestValidateBoard:
    def test_validate_board_valid(self):
        """Non-empty board id is returned as-is (PlatformIO validates the actual id)."""
        assert _validate_board("esp32-s3-devkitc-1") == "esp32-s3-devkitc-1"
        assert _validate_board("black_f407vg") == "black_f407vg"

    def test_validate_board_empty(self):
        """Empty board raises ValueError."""
        with pytest.raises(ValueError, match="board cannot be empty"):
            _validate_board("")
        with pytest.raises(ValueError):
            _validate_board("   ")


# ═══════════════════════════════════════════
# _resolve_env_name
# ═══════════════════════════════════════════


class TestResolveEnvName:
    def test_resolve_env_name(self):
        """esp32-s3 -> esp32s3 (dashes stripped)."""
        assert _resolve_env_name("esp32-s3") == "esp32s3"
        assert _resolve_env_name("esp32-c3") == "esp32c3"
        assert _resolve_env_name("esp32-s2") == "esp32s2"
        assert _resolve_env_name("esp32") == "esp32"


# ═══════════════════════════════════════════
# _validate_binary_path
# ═══════════════════════════════════════════


class TestValidateBinaryPath:
    def test_validate_binary_path_valid(self):
        """Path under .build/tmp/ passes validation."""
        ok = _validate_binary_path(
            ".build/tmp/abc123/.pio/build/esp32s3/firmware.bin"
        )
        assert ok is True

    def test_validate_binary_path_traversal(self):
        """Path traversal attempts (relative/absolute) are rejected."""
        # Linux-style relative traversal
        assert _validate_binary_path("../etc/passwd") is False
        # Windows-style relative traversal
        assert _validate_binary_path("..\\..\\etc\\passwd") is False
        # Absolute paths (Linux + Windows drive)
        assert _validate_binary_path("/etc/passwd") is False
        assert _validate_binary_path("C:\\Windows\\System32\\evil.dll") is False
        # Sibling directory (not under .build/tmp/)
        assert _validate_binary_path(".build/other/x.bin") is False


# ═══════════════════════════════════════════
# _validate_port
# ═══════════════════════════════════════════


class TestValidatePort:
    def test_validate_port_valid(self):
        """Port present in comports() list is valid."""
        fake_port = MagicMock()
        fake_port.device = "COM3"
        with patch(
            "src.hardware.pio_runner.serial.tools.list_ports.comports",
            return_value=[fake_port],
        ):
            is_valid, available = _validate_port("COM3")
        assert is_valid is True
        assert "COM3" in available

    def test_validate_port_invalid(self):
        """Port not in comports() list returns (False, available_ports)."""
        fake_port = MagicMock()
        fake_port.device = "COM3"
        with patch(
            "src.hardware.pio_runner.serial.tools.list_ports.comports",
            return_value=[fake_port],
        ):
            is_valid, available = _validate_port("COM99")
        assert is_valid is False
        assert available == ["COM3"]


# ═══════════════════════════════════════════
# _generate_platformio_ini
# ═══════════════════════════════════════════


class TestGeneratePlatformioIni:
    def test_generate_platformio_ini_esp32(self):
        """ESP32 ini has espressif32 platform + arduino framework + upload_speed."""
        ini = _generate_platformio_ini("esp32-s3-devkitc-1", "espressif32", {})
        assert "[env:esp32s3devkitc1]" in ini
        assert "platform = espressif32" in ini
        assert "board = esp32-s3-devkitc-1" in ini
        assert "framework = arduino" in ini
        assert f"upload_speed = {DEFAULT_UPLOAD_SPEED}" in ini
        assert "upload_protocol" not in ini
        assert "upload_port" not in ini

    def test_generate_platformio_ini_stm32(self):
        """STM32 ini has ststm32 platform + arduino framework + upload_protocol=serial."""
        ini = _generate_platformio_ini("black_f407vg", "ststm32", {})
        assert "[env:black_f407vg]" in ini
        assert "platform = ststm32" in ini
        assert "board = black_f407vg" in ini
        assert "framework = arduino" in ini
        assert "upload_protocol = serial" in ini
        assert "upload_speed" not in ini

    def test_generate_platformio_ini_with_upload_port(self):
        """options.upload_port produces an upload_port line in the ini."""
        ini = _generate_platformio_ini("esp32-s3-devkitc-1", "espressif32", {"upload_port": "COM3"})
        assert "upload_port = COM3" in ini
        assert "[env:esp32s3devkitc1]" in ini


# ═══════════════════════════════════════════
# _create_temp_project
# ═══════════════════════════════════════════


class TestCreateTempProject:
    def test_create_temp_project(self, tmp_path, monkeypatch):
        """Creates src/main.cpp + platformio.ini under .build/tmp/{session}/."""
        monkeypatch.chdir(tmp_path)
        req = CompileRequest(
            code="void setup(){} void loop(){}",
            board="esp32-s3-devkitc-1",
            platform="espressif32",
            options={},
            session_id="test-session-123",
        )
        project_dir = _create_temp_project(req)

        # _create_temp_project returns a relative Path (BUILD_TMP_ROOT is
        # relative); compare via resolve() since we chdir'd into tmp_path.
        expected = tmp_path / ".build" / "tmp" / "test-session-123"
        assert project_dir.resolve() == expected.resolve()

        main_cpp = project_dir / "src" / "main.cpp"
        assert main_cpp.exists()
        assert main_cpp.read_text(encoding="utf-8") == "void setup(){} void loop(){}"

        ini = project_dir / "platformio.ini"
        assert ini.exists()
        content = ini.read_text(encoding="utf-8")
        assert "[env:esp32s3devkitc1]" in content
        assert "board = esp32-s3-devkitc-1" in content
        assert "platform = espressif32" in content


# ═══════════════════════════════════════════
# compile_firmware async generator
# ═══════════════════════════════════════════


def _make_fake_proc(lines: list[bytes], returncode: int) -> MagicMock:
    """Build a fake asyncio.subprocess.Process for streaming tests."""
    proc = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(side_effect=lines)
    proc.wait = AsyncMock(return_value=None)
    proc.kill = MagicMock()
    proc.returncode = returncode
    return proc


def _make_compile_request(board: str = "esp32-s3-devkitc-1", platform: str = "espressif32") -> CompileRequest:
    return CompileRequest(
        code="void setup(){} void loop(){}",
        board=board,
        platform=platform,
        options={},
        session_id="session-test-1",
    )


class TestCompileFirmware:
    @pytest.mark.asyncio
    async def test_compile_firmware_pio_not_installed(self, tmp_path, monkeypatch):
        """When _check_pio_installed returns False, yields PIO_NOT_INSTALLED done."""
        monkeypatch.chdir(tmp_path)
        req = _make_compile_request()
        with patch(
            "src.hardware.pio_runner._check_pio_installed",
            AsyncMock(return_value=False),
        ):
            events = [e async for e in compile_firmware(req)]

        assert events[0]["type"] == "thinking"
        done = events[-1]
        assert done["type"] == "done"
        assert done["success"] is False
        assert done["error"]["code"] == ERR_PIO_NOT_INSTALLED

    @pytest.mark.asyncio
    async def test_compile_firmware_empty_board(self, tmp_path, monkeypatch):
        """Empty board yields UNSUPPORTED_BOARD done event."""
        monkeypatch.chdir(tmp_path)
        req = _make_compile_request(board="")
        with patch(
            "src.hardware.pio_runner._check_pio_installed",
            AsyncMock(return_value=True),
        ):
            events = [e async for e in compile_firmware(req)]

        assert events[0]["type"] == "thinking"
        done = events[-1]
        assert done["type"] == "done"
        assert done["success"] is False
        assert done["error"]["code"] == ERR_UNSUPPORTED_BOARD

    @pytest.mark.asyncio
    async def test_compile_firmware_success(self, tmp_path, monkeypatch):
        """Successful compile yields thinking -> progress -> compile_log* -> done(success)."""
        monkeypatch.chdir(tmp_path)
        req = _make_compile_request()
        fake_proc = _make_fake_proc(
            [b"Processing esp32s3\n", b"Linking firmware\n", b""], returncode=0
        )
        with patch(
            "src.hardware.pio_runner._check_pio_installed",
            AsyncMock(return_value=True),
        ), patch(
            "src.hardware.pio_runner._spawn_pio",
            AsyncMock(return_value=fake_proc),
        ):
            events = [e async for e in compile_firmware(req)]

        types = [e["type"] for e in events]
        assert types[0] == "thinking"
        assert "progress" in types
        compile_logs = [e for e in events if e["type"] == "compile_log"]
        assert len(compile_logs) == 2
        assert types[-1] == "done"

        done = events[-1]
        assert done["success"] is True
        assert "binary_path" in done
        assert done["binary_path"].endswith("firmware.bin")
        assert "esp32s3devkitc1" in done["binary_path"]

    @pytest.mark.asyncio
    async def test_compile_firmware_failed(self, tmp_path, monkeypatch):
        """Failed compile (returncode != 0) yields done(success=False, error.code=COMPILE_FAILED)."""
        monkeypatch.chdir(tmp_path)
        req = _make_compile_request()
        fake_proc = _make_fake_proc(
            [b"main.cpp:5: error: 'foo' was not declared\n", b""], returncode=1
        )
        with patch(
            "src.hardware.pio_runner._check_pio_installed",
            AsyncMock(return_value=True),
        ), patch(
            "src.hardware.pio_runner._spawn_pio",
            AsyncMock(return_value=fake_proc),
        ):
            events = [e async for e in compile_firmware(req)]

        done = events[-1]
        assert done["type"] == "done"
        assert done["success"] is False
        assert done["error"]["code"] == ERR_COMPILE_FAILED
        assert "details" in done["error"]


# ═══════════════════════════════════════════
# cleanup_old_builds
# ═══════════════════════════════════════════


class TestCleanupOldBuilds:
    @pytest.mark.asyncio
    async def test_cleanup_old_builds(self, tmp_path, monkeypatch):
        """Deletes stale session dirs (mtime > max_age_hours) and keeps fresh ones."""
        monkeypatch.chdir(tmp_path)
        root = tmp_path / BUILD_TMP_ROOT
        old_dir = root / "old-session"
        fresh_dir = root / "fresh-session"
        old_dir.mkdir(parents=True)
        fresh_dir.mkdir(parents=True)

        # Mark old_dir as 48h old
        stale = time.time() - 48 * 3600
        os.utime(old_dir, (stale, stale))

        deleted = await cleanup_old_builds(max_age_hours=24)

        assert deleted == 1
        assert not old_dir.exists()
        assert fresh_dir.exists()
