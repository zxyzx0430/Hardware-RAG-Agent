"""
测试 /api/kb/upload 路由。

注意：新版 kb_routes.py 的 upload 接口改为异步索引：
- 响应立即返回 status="indexing"，chunks=0
- 后台任务完成后再更新 KnowledgeDoc.status="indexed"
- 文件大小超限返回 200 + {"success": False, "error": {"code": "FILE_TOO_LARGE"}}
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import create_app
from app.db.database import SessionLocal, init_db
from app.db.models import KnowledgeBase


@pytest.fixture
def client():
    """Create test client with builtin KB ensured."""
    init_db()
    # Ensure builtin KB exists for upload tests
    db = SessionLocal()
    try:
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.is_builtin == True).first()
        if not kb:
            kb = KnowledgeBase(
                id="builtin-001",
                name="硬件手册库",
                description="test builtin KB",
                collection_name="hardware-docs-test",
                chunk_method="hybrid",
                embedding_model="text-embedding-3-small",
                enabled=True,
                is_builtin=True,
            )
            db.add(kb)
            db.commit()
    finally:
        db.close()

    return TestClient(create_app())


class TestKbUpload:
    """测试知识库文件上传。"""

    def test_upload_success_returns_doc_id_and_status(self, client):
        """上传合法 Markdown 文件应返回 success、doc_id、filename、status=indexing。

        新版 API 异步索引，响应中 chunks=0，status="indexing"。
        """
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
        assert data["data"]["status"] == "indexing"
        assert isinstance(data["data"]["chunks"], int)

    def test_upload_file_too_large_returns_file_too_large(self, client):
        """文件超过大小限制应返回 FILE_TOO_LARGE 错误。"""
        with patch("app.api.kb_routes.MAX_UPLOAD_SIZE", 10):
            response = client.post(
                "/api/kb/upload",
                files={"file": ("big.md", b"x" * 11, "text/markdown")},
            )

        # 新版 API 返回 200 + 错误体（不抛 HTTPException）
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "FILE_TOO_LARGE"
