"""测试 /api/wiring 路由。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


class TestWiring:
    """测试接线图生成接口。"""

    def test_wiring_returns_svg_and_bom(self, client):
        """验证 /api/wiring 返回 SVG 和 BOM。"""
        response = client.post(
            "/api/wiring",
            json={
                "title": "Test",
                "connections": [
                    {
                        "from": "MCU",
                        "pin": "GPIO2",
                        "to_component": "LED",
                        "to_pin": "ANODE",
                        "color": "#f00",
                    }
                ],
                "components": [
                    {"name": "MCU", "type": "mcu", "pins": ["GPIO2", "GND"]},
                    {"name": "LED", "type": "led", "pins": ["ANODE", "CATHODE"]},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "<svg" in data["data"]["svg"]
        assert len(data["data"]["bom"]) == 2

    def test_wiring_empty_components(self, client):
        """空器件列表也应正常返回。"""
        response = client.post(
            "/api/wiring",
            json={"title": "Empty", "connections": [], "components": []},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "<svg" in data["data"]["svg"]
