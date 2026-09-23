"""
测试 /api/tool 路由。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


class TestTool:
    """测试 Agent 工具调用。"""

    def test_known_tool_returns_success_and_data(self, client):
        """调用已知工具应返回 {success: true, data: {...}}。"""
        response = client.post(
            "/api/tool",
            json={"tool": "audit_pins", "args": {}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "output" in data

    def test_unknown_tool_returns_tool_not_found(self, client):
        """调用未知工具应返回 TOOL_NOT_FOUND。"""
        response = client.post(
            "/api/tool",
            json={"tool": "nonexistent_tool", "args": {}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"]["error_type"] == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_command_process_uses_an_isolated_posix_session(monkeypatch):
    import asyncio
    import os

    from src.agent.tools.groups.execution import run_command

    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await run_command._create_process("echo test", "")

    assert captured["start_new_session"] is (os.name != "nt")


@pytest.mark.asyncio
async def test_run_command_blocks_pio_device_monitor_case_insensitively():
    from src.agent.tools.groups.execution.run_command import RunCommandTool

    result = await RunCommandTool().execute(
        {"command": "PIO DEVICE MONITOR --port COM3"},
        None,
    )

    assert result["exit_code"] == -1
    assert "禁止使用 pio device monitor" in result["output"]
