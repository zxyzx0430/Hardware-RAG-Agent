"""
测试 /api/diagnose 路由。
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


class TestDiagnose:
    """测试代码诊断接口。"""

    def test_diagnose_returns_five_result_categories(self, client):
        """验证 /api/diagnose 返回 5 类诊断结果。"""
        code = """
#define LED_PIN 2
#define BUTTON_PIN 0

void setup() {
    pinMode(LED_PIN, OUTPUT);
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    digitalWrite(LED_PIN, HIGH);
    delay(100);
}

void loop() {}
"""
        response = client.post(
            "/api/diagnose",
            json={"code": code, "chip": "esp32-s3"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["success"] is True
        assert "data" in data
        assert "results" in data["data"]
        assert len(data["data"]["results"]) == 5

        names = {r["name"] for r in data["data"]["results"]}
        assert names == {
            "GPIO 安全检查",
            "编译预检",
            "引脚冲突检测",
            "内存估算",
            "Flash 兼容性",
        }

    def test_diagnose_strapping_pin_warning(self, client):
        """使用 Strapping 引脚 GPIO0 应返回 WARN。"""
        code = "void setup(){pinMode(0,OUTPUT);}"
        response = client.post(
            "/api/diagnose",
            json={"code": code, "chip": "esp32-s3"},
        )
        assert response.status_code == 200
        data = response.json()
        gpio_item = next(i for i in data["data"]["results"] if i["name"] == "GPIO 安全检查")
        assert gpio_item["status"] == "WARN"

    def test_diagnose_pin_conflict(self, client):
        """同一引脚同时配置为 INPUT 和 OUTPUT 应返回 FAIL。"""
        code = "void setup(){pinMode(2,OUTPUT);pinMode(2,INPUT);}"
        response = client.post(
            "/api/diagnose",
            json={"code": code, "chip": "esp32-s3"},
        )
        assert response.status_code == 200
        data = response.json()
        conflict_item = next(i for i in data["data"]["results"] if i["name"] == "引脚冲突检测")
        assert conflict_item["status"] == "FAIL"

    def test_diagnose_returns_standard_response_format(self, client):
        """验证返回标准响应格式。"""
        code = """
void setup() {
    pinMode(2, OUTPUT);
    digitalWrite(2, HIGH);
}
void loop() {}
"""
        response = client.post(
            "/api/diagnose",
            json={"code": code, "env": "esp32-s3", "chip": "esp32-s3"},
        )

        assert response.status_code == 200
        data = response.json()

        assert "success" in data
        assert "data" in data
        assert isinstance(data["data"]["results"], list)

        for item in data["data"]["results"]:
            assert "name" in item
            assert "status" in item
            assert "detail" in item
            assert item["status"] in ("PASS", "WARN", "FAIL")
