"""测试 /api/audit_pins 路由。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


class TestAuditPins:
    """测试引脚冲突审计接口。"""

    def test_audit_pins_returns_standard_format(self, client):
        """验证 /api/audit_pins 返回标准格式。"""
        response = client.post(
            "/api/audit_pins",
            json={
                "chip": "esp32-s3",
                "pin_assignments": {
                    "GPIO2": {"function": "LED", "config": "OUTPUT"}
                },
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "conflicts" in data["data"]
        assert "warnings" in data["data"]
