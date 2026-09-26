"""测试 /api/wiring 路由。"""
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


class TestWiring:
    """测试接线图生成接口。"""

    @pytest.mark.parametrize(
        "connection",
        [
            {
                "from": "MCU",
                "pin": "GPIO2",
                "to_component": "LED",
                "to_pin": "ANODE",
                "color": "#f00",
            },
            {
                "from_component": "MCU",
                "from_pin": "GPIO2",
                "to_component": "LED",
                "to_pin": "ANODE",
                "color": "#f00",
            },
        ],
        ids=["legacy-alias-fields", "legacy-model-fields"],
    )
    def test_wiring_returns_svg_and_bom(self, client, connection):
        """验证 /api/wiring 返回 SVG 和 BOM。"""
        response = client.post(
            "/api/wiring",
            json={
                "title": "Test",
                "connections": [connection],
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
        root = ElementTree.fromstring(data["data"]["svg"])
        svg_text = {
            element.text
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "text" and element.text
        }
        assert {"MCU", "GPIO2", "LED", "ANODE"}.issubset(svg_text)

    def test_extracted_connections_generate_svg_and_bom(self, client):
        """从提取接口取出的规范嵌套端点可直接送入生成接口。"""
        code = """
        #define STATUS_LED 2
        void setup() {
          pinMode(STATUS_LED, OUTPUT);
        }
        void loop() {
          digitalWrite(STATUS_LED, HIGH);
        }
        """
        extract_response = client.post("/api/wiring/extract", json={"code": code})
        assert extract_response.status_code == 200
        extracted = extract_response.json()["data"]
        assert extracted["connections"] == [
            {
                "from": {"component": "MCU", "pin": "GPIO2"},
                "to": {"component": "LED", "pin": "ANODE"},
                "line_type": "signal",
                "color": "#ff0000",
            }
        ]

        generate_response = client.post(
            "/api/wiring",
            json={
                "title": "Extracted wiring",
                "components": extracted["components"],
                "connections": extracted["connections"],
            },
        )
        assert generate_response.status_code == 200
        assert {component["name"] for component in extracted["components"]} == {"MCU", "LED"}
        generated = generate_response.json()["data"]
        root = ElementTree.fromstring(generated["svg"])
        svg_text = {
            element.text
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "text" and element.text
        }
        assert {"MCU", "GPIO2", "LED", "ANODE"}.issubset(svg_text)
        assert {entry["component"] for entry in generated["bom"]} == {"MCU", "LED"}

    def test_wiring_empty_components(self, client):
        """代码没有引脚时，空提取结果也能生成可解析的空 SVG。"""
        extract_response = client.post(
            "/api/wiring/extract",
            json={"code": "void setup() {} void loop() {}"},
        )
        assert extract_response.status_code == 200
        extracted = extract_response.json()["data"]
        assert extracted["connections"] == []

        response = client.post(
            "/api/wiring",
            json={
                "title": "Empty",
                "connections": extracted["connections"],
                "components": extracted["components"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "<svg" in data["data"]["svg"]
        assert ElementTree.fromstring(data["data"]["svg"]).tag.endswith("svg")
        assert data["data"]["bom"] == []

    def test_wiring_rejects_invalid_nested_endpoints(self, client):
        """端点字段缺失或引用不存在的器件引脚时返回校验错误。"""
        components = [
            {"name": "MCU", "type": "mcu", "pins": ["GPIO2"]},
            {"name": "LED", "type": "led", "pins": ["ANODE", "CATHODE"]},
        ]
        invalid_connections = [
            {
                "from": {"component": "MCU"},
                "to": {"component": "LED", "pin": "ANODE"},
            },
            {
                "from": {"component": "MCU", "pin": "GPIO99"},
                "to": {"component": "LED", "pin": "ANODE"},
            },
            {
                "from": {"component": "MCU", "pin": "GPIO2"},
                "to": {"component": "MISSING", "pin": "ANODE"},
            },
        ]
        for connection in invalid_connections:
            response = client.post(
                "/api/wiring",
                json={"components": components, "connections": [connection]},
            )
            assert response.status_code == 422
