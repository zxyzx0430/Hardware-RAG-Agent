"""Tests for src.agent.tools.groups.code.build_tool.

Covers BuildTool / FlashTool execute() success and failure paths, risk levels,
PermissionClassifier behavior (BuildTool LOW auto-allow / FlashTool HIGH triggers
HITL via _decide_high ASK fallback), and AuditRecorder integration via
ToolRouter.dispatch.

pio_runner.compile_firmware / upload_firmware are mocked so no real PlatformIO
invocation happens.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.core.toolkit.audit_recorder import AuditRecorder
from src.agent.core.toolkit.permission_classifier import (
    ALLOW,
    ASK,
    PermissionClassifier,
)
from src.agent.core.toolkit.tool_router import (
    ToolRouter,
    register,
    unregister,
)
from src.agent.core.toolkit.tool_spec import RiskLevel
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.code.build_tool import BuildTool, FlashTool


# ═══════════════════════════════════════════
# Fake pio_runner async generators
# ═══════════════════════════════════════════


async def _fake_compile_success(req):
    yield {"type": "thinking", "content": "正在编译固件...", "source": "build"}
    yield {"type": "progress", "percent": 5, "message": "已创建临时项目"}
    yield {"type": "compile_log", "line": "Linking...", "stream": "stdout"}
    yield {
        "type": "done",
        "success": True,
        "binary_path": ".build/tmp/test/.pio/build/esp32s3/firmware.bin",
    }


async def _fake_compile_failed(req):
    yield {"type": "thinking", "content": "正在编译固件...", "source": "build"}
    yield {"type": "compile_log", "line": "error: undeclared 'foo'", "stream": "stdout"}
    yield {
        "type": "done",
        "success": False,
        "error": {"code": "COMPILE_FAILED", "message": "编译失败", "details": "..."},
    }


async def _fake_upload_success(req):
    yield {"type": "thinking", "content": "正在烧录固件...", "source": "flash"}
    yield {"type": "progress", "percent": 10, "message": "开始烧录"}
    yield {"type": "done", "success": True, "message": "烧录完成"}


async def _fake_upload_failed(req):
    yield {"type": "thinking", "content": "正在烧录固件...", "source": "flash"}
    yield {"type": "compile_log", "line": "esptool: failed to connect", "stream": "stdout"}
    yield {
        "type": "done",
        "success": False,
        "error": {"code": "UPLOAD_FAILED", "message": "烧录失败", "details": "..."},
    }


def _build_ctx() -> ToolContext:
    return ToolContext(session_id="test-session")


# ═══════════════════════════════════════════
# BuildTool.execute
# ═══════════════════════════════════════════


class TestBuildToolExecute:
    @pytest.mark.asyncio
    async def test_build_tool_success(self):
        """BuildTool.execute returns {output, binary_path, success=True}."""
        with patch(
            "src.agent.tools.groups.code.build_tool.compile_firmware",
            _fake_compile_success,
        ):
            tool = BuildTool()
            result = await tool.execute(
                {"code": "void setup(){} void loop(){}", "board": "esp32-s3"},
                _build_ctx(),
            )

        assert result["success"] is True
        assert result["binary_path"] == ".build/tmp/test/.pio/build/esp32s3/firmware.bin"
        assert isinstance(result["output"], str)
        # Output is "编译成功，固件路径: <binary_path>"; verify path is mentioned.
        assert result["binary_path"] in result["output"]

    @pytest.mark.asyncio
    async def test_build_tool_failed(self):
        """BuildTool.execute on compile failure returns {output, binary_path:'', success=False}."""
        with patch(
            "src.agent.tools.groups.code.build_tool.compile_firmware",
            _fake_compile_failed,
        ):
            tool = BuildTool()
            result = await tool.execute(
                {"code": "broken", "board": "esp32-s3"},
                _build_ctx(),
            )

        assert result["success"] is False
        assert result["binary_path"] == ""
        assert "COMPILE_FAILED" in result["output"]


# ═══════════════════════════════════════════
# FlashTool.execute
# ═══════════════════════════════════════════


class TestFlashToolExecute:
    @pytest.mark.asyncio
    async def test_flash_tool_success(self):
        """FlashTool.execute returns {output, success=True, port}."""
        with patch(
            "src.agent.tools.groups.code.build_tool.upload_firmware",
            _fake_upload_success,
        ):
            tool = FlashTool()
            result = await tool.execute(
                {
                    "binary_path": ".build/tmp/test/.pio/build/esp32s3/firmware.bin",
                    "board": "esp32-s3",
                    "port": "COM3",
                },
                _build_ctx(),
            )

        assert result["success"] is True
        assert result["port"] == "COM3"
        assert isinstance(result["output"], str)
        assert "COM3" in result["output"]

    @pytest.mark.asyncio
    async def test_flash_tool_failed(self):
        """FlashTool.execute on upload failure returns {output, success=False, port}."""
        with patch(
            "src.agent.tools.groups.code.build_tool.upload_firmware",
            _fake_upload_failed,
        ):
            tool = FlashTool()
            result = await tool.execute(
                {
                    "binary_path": ".build/tmp/test/.pio/build/esp32s3/firmware.bin",
                    "board": "esp32-s3",
                    "port": "COM3",
                },
                _build_ctx(),
            )

        assert result["success"] is False
        assert result["port"] == "COM3"
        assert "UPLOAD_FAILED" in result["output"]


# ═══════════════════════════════════════════
# Risk levels (class attributes)
# ═══════════════════════════════════════════


class TestRiskLevels:
    def test_build_tool_risk_level_low(self):
        """BuildTool.risk_level is LOW (compile has no side effects)."""
        assert BuildTool().risk_level == RiskLevel.LOW

    def test_flash_tool_risk_level_high(self):
        """FlashTool.risk_level is HIGH (writes to device)."""
        assert FlashTool().risk_level == RiskLevel.HIGH


# ═══════════════════════════════════════════
# PermissionClassifier
# ═══════════════════════════════════════════


class TestPermissionClassifier:
    def test_build_tool_auto_allow(self):
        """BuildTool (LOW risk) is auto-allowed by PermissionClassifier."""
        spec = BuildTool()
        ctx = _build_ctx()
        decision = PermissionClassifier().check(spec, {}, ctx)
        assert decision == ALLOW

    def test_flash_tool_triggers_hitl(self):
        """FlashTool (HIGH risk) triggers HITL via _decide_high ASK fallback.

        spec `optimize-workbench-ux-batch` Track D Task 8 reverted flash_firmware
        to HITL confirm — _HITL_SKIP_TOOLS no longer whitelists it.
        """
        spec = FlashTool()
        ctx = _build_ctx()
        decision = PermissionClassifier().check(
            spec,
            {
                "binary_path": ".build/tmp/test/.pio/build/esp32s3/firmware.bin",
                "port": "COM3",
            },
            ctx,
        )
        assert decision == ASK


# ═══════════════════════════════════════════
# Audit recording via ToolRouter.dispatch
# ═══════════════════════════════════════════


class TestAuditRecording:
    @pytest.mark.asyncio
    async def test_flash_tool_audit_recorded(self):
        """FlashTool dispatched via ToolRouter writes one audit row with risk_level=high."""
        with patch(
            "src.agent.tools.groups.code.build_tool.upload_firmware",
            _fake_upload_success,
        ):
            spec = FlashTool()
            register(spec)
            try:
                mock_recorder = MagicMock(spec=AuditRecorder)
                router = ToolRouter(audit_recorder=mock_recorder)
                args = {
                    "binary_path": ".build/tmp/test/.pio/build/esp32s3/firmware.bin",
                    "board": "esp32-s3",
                    "port": "COM3",
                }
                ctx = _build_ctx()
                await router.dispatch("call-1", "flash_firmware", args, ctx)
            finally:
                unregister("flash_firmware")

        # AuditRecorder.record called once with a single AuditRecord argument.
        assert mock_recorder.record.called
        call = mock_recorder.record.call_args
        record = call.args[0]
        assert record.spec.name == "flash_firmware"
        assert record.spec.risk_level == RiskLevel.HIGH
