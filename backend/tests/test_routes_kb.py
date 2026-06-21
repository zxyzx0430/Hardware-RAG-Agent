"""
测试 /api/kb/upload 路由。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


class TestKbUpload:
    """测试知识库文件上传。"""

    def test_upload_success_returns_doc_id_and_chunks(self, client):
        """上传合法 Markdown 文件应返回 success、doc_id、filename、chunks。"""
        content = b"# ESP32\n\nThis is a test document.\n\n" + b"word " * 200
        response = client.post(
            "/api/kb/upload",
            files={"file": ("esp32_guide.md", content, "text/markdown")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "doc_id" in data["data"]
        assert data["data"]["filename"] == "esp32_guide.md"
        assert isinstance(data["data"]["chunks"], int)
        assert data["data"]["chunks"] > 0

    def test_upload_file_too_large_returns_file_too_large(self, client):
        """文件超过大小限制应返回 FILE_TOO_LARGE。"""
        with patch("app.api.routes.MAX_UPLOAD_SIZE", 10):
            response = client.post(
                "/api/kb/upload",
                files={"file": ("big.md", b"x" * 11, "text/markdown")},
            )

        assert response.status_code == 400
        data = response.json()
        # FastAPI HTTPException 的 detail 会被包装在 "detail" 字段下
        assert data["detail"]["success"] is False
        assert data["detail"]["error"]["code"] == "FILE_TOO_LARGE"
