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
        assert "output" in data["data"]

    def test_unknown_tool_returns_tool_not_found(self, client):
        """调用未知工具应返回 TOOL_NOT_FOUND。"""
        response = client.post(
            "/api/tool",
            json={"tool": "nonexistent_tool", "args": {}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "TOOL_NOT_FOUND"
